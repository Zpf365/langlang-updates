from __future__ import annotations

import inspect
import json
import os
import queue
import sys
import threading
import time
import tkinter as tk
import webbrowser
from datetime import UTC, datetime
from pathlib import Path
from tkinter import font as tkfont
from tkinter import messagebox
from typing import Callable

from . import __version__
from .runtime_logging import log_event
from .ui.update_progress import UpdateProgressPanel
from .update_health import update_root_for_install
from .update_notes import build_update_summary
from .update_policy import (
    DEFAULT_TEST_INSTALL_DIR,
    TestUpdatePolicy,
    load_stable_update_policy,
    load_test_update_policy,
)
from .update_protocol import load_trust_config
from .updater import (
    StagedUpdate,
    UpdateInfo,
    check_for_update,
    download_and_stage_update,
    launch_update_installer,
)

STARTUP_COLORS = {
    "window": "#eef2f7",
    "panel": "#ffffff",
    "text": "#18314B",
    "muted": "#687b90",
    "accent": "#168bea",
}
AUTO_UPDATE_STATUSES = {
    "detected",
    "downloading",
    "verified",
    "pending_install",
    "installed",
    "rolled_back",
    "failed",
}


def _installed_version(target: Path) -> str:
    for relative in (
        Path("_internal") / "update_manifest.json",
        Path("update_manifest.json"),
    ):
        path = target / relative
        try:
            payload = json.loads(
                path.read_text(encoding="utf-8-sig")
            )
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        version = str(payload.get("version") or "").strip()
        if version:
            return version
    return ""


def _auto_state_path(target: Path) -> Path:
    return update_root_for_install(target) / "auto" / "state.json"


def _read_auto_state(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    if payload.get("status") not in AUTO_UPDATE_STATUSES:
        return {}
    return payload


def _write_auto_state(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)


def _update_auto_state(
    path: Path,
    status: str,
    now: Callable[[], datetime] | None = None,
    **changes: object,
) -> dict[str, object]:
    if status not in AUTO_UPDATE_STATUSES:
        raise ValueError(f"非法自动更新状态：{status}")
    payload = _read_auto_state(path)
    payload.update(changes)
    payload["status"] = status
    payload["updated_at"] = (now() if now else datetime.now(UTC)).isoformat(
        timespec="seconds"
    )
    _write_auto_state(path, payload)
    return payload


def _show_auto_update_notice(title: str, message: str) -> None:
    if os.name != "nt":
        return
    try:
        root = tk.Tk()
        root.title(title)
        root.attributes("-topmost", True)
        root.configure(bg=STARTUP_COLORS["panel"])
        root.resizable(False, False)
        width, height = 520, 170
        root.geometry(
            f"{width}x{height}+"
            f"{max(0, (root.winfo_screenwidth() - width) // 2)}+"
            f"{max(0, (root.winfo_screenheight() - height) // 2)}"
        )
        tk.Label(
            root,
            text=title,
            bg="#ffffff",
            fg=STARTUP_COLORS["text"],
            font=("Microsoft YaHei UI", 15, "bold"),
        ).pack(pady=(22, 8))
        tk.Label(
            root,
            text=message,
            bg="#ffffff",
            fg=STARTUP_COLORS["muted"],
            font=("Microsoft YaHei UI", 10),
            wraplength=470,
        ).pack(padx=25, pady=(0, 18))
        root.after(2200, root.destroy)
        root.mainloop()
    except tk.TclError:
        return


def _reconcile_auto_update_state(
    target: Path,
    state_path: Path,
    notice: Callable[[str, str], None],
    now: Callable[[], datetime] | None = None,
) -> dict[str, object]:
    state = _read_auto_state(state_path)
    if not state:
        return state
    previous = str(state.get("status") or "")
    target_version = str(state.get("target_version") or "")
    current = _installed_version(target)
    if (
        previous in {"verified", "pending_install"}
        and target_version
        and current
        and current == target_version
    ):
        state = _update_auto_state(
            state_path,
            "installed",
            now=now,
            current_version=current,
            next_startup=False,
            last_error="",
            notice=f"当前版本已更新到{current}",
        )
        log_event(
            "update.auto.installed",
            current_version=current,
            target_version=target_version,
        )
        notice("更新完成", f"当前版本已更新到{current}")
        return state
    if previous == "pending_install" and target_version and current != target_version:
        installer_log = Path(str(state.get("installer_log") or ""))
        log_text = ""
        try:
            log_text = installer_log.read_text(
                encoding="utf-8-sig",
                errors="replace",
            )[-20000:]
        except OSError:
            pass
        if (
            "Previous version restored" in log_text
            or "恢复" in log_text
        ):
            reason = "更新失败，已恢复原版本"
            state = _update_auto_state(
                state_path,
                "rolled_back",
                now=now,
                current_version=current,
                next_startup=False,
                rollback_reason=reason,
                notice=reason,
            )
            log_event(
                "update.auto.rolled_back",
                current_version=current,
                target_version=target_version,
                reason=reason,
            )
            notice("更新已回滚", reason)
        elif "Update failed" in log_text:
            reason = "更新失败，未完成安装"
            state = _update_auto_state(
                state_path,
                "failed",
                now=now,
                current_version=current,
                next_startup=False,
                last_error=reason,
                notice=reason,
            )
            log_event(
                "update.auto.failed",
                current_version=current,
                target_version=target_version,
                error=reason,
            )
            notice("自动更新失败", reason)
        return state
    return state


def _stage_with_progress(
    stage: Callable[..., StagedUpdate],
    info: UpdateInfo,
    target: Path,
    progress: Callable[[str, int, int], None],
) -> StagedUpdate:
    try:
        parameters = inspect.signature(stage).parameters
    except (TypeError, ValueError):
        parameters = {}
    if "progress" in parameters:
        return stage(info, target, progress=progress)
    return stage(info, target)


def _run_update_flow(
    info: UpdateInfo,
    target: Path,
    *,
    stage: Callable[..., StagedUpdate],
    launch: Callable[[StagedUpdate, Path], Path],
) -> bool:
    if os.name != "nt":
        return False
    root = tk.Tk()
    root.title("发现新版本")
    root.configure(bg=STARTUP_COLORS["panel"])
    root.resizable(True, True)
    screen_width = root.winfo_screenwidth()
    screen_height = root.winfo_screenheight()
    width = min(940, max(680, screen_width - 80))
    height = min(640, max(420, int(screen_height * 0.70)))
    root.minsize(680, 420)
    root.geometry(
        f"{width}x{height}+"
        f"{max(0, (screen_width - width) // 2)}+"
        f"{max(0, (screen_height - height) // 2)}"
    )
    result = {"should_exit": False, "started": False}
    progress_queue: queue.Queue[
        tuple[str, str, int, int]
    ] = queue.Queue()
    interactive: list[tk.Misc] = []
    staged_holder: dict[str, StagedUpdate] = {}
    title_font = tkfont.Font(
        family="Microsoft YaHei UI",
        size=16,
        weight="bold",
    )
    body_font = tkfont.Font(
        family="Microsoft YaHei UI",
        size=10,
    )
    summary_groups = list(
        getattr(info, "summary", None)
        or build_update_summary(str(info.notes or ""))
    )

    def close_current_version() -> None:
        result["should_exit"] = False
        try:
            root.destroy()
        except tk.TclError:
            pass

    def close_prompt() -> None:
        if result["started"]:
            messagebox.showwarning(
                "正在更新",
                "更新正在下载、校验或安装，请等待完成。",
                parent=root,
            )
            return
        close_current_version()

    def begin_update() -> None:
        if result["started"]:
            return
        result["started"] = True
        log_event(
            "update.startup.clicked",
            current_version=info.current_version,
            latest_version=info.latest_version,
        )
        for widget in interactive:
            try:
                widget.configure(state="disabled")
            except tk.TclError:
                pass
        root.protocol("WM_DELETE_WINDOW", close_prompt)
        root.title("正在更新（不可关闭）")
        for child in root.winfo_children():
            child.destroy()
        panel = UpdateProgressPanel(
            root,
            current_version=str(info.current_version),
            latest_version=str(info.latest_version),
            colors=STARTUP_COLORS,
        )
        panel.pack(fill="both", expand=True)
        root.update_idletasks()
        panel_width = max(620, root.winfo_reqwidth())
        panel_height = max(300, root.winfo_reqheight())
        root.geometry(f"{panel_width}x{panel_height}")
        last_progress = {"at": 0.0, "message": ""}

        def progress(message: str, current: int, total: int) -> None:
            now = time.monotonic()
            immediate = (
                message != last_progress["message"]
                or current <= 0
                or total <= 0
                or (total > 0 and current >= total)
            )
            if not immediate and now - last_progress["at"] < 0.12:
                return
            last_progress["at"] = now
            last_progress["message"] = message
            progress_queue.put(("progress", message, current, total))

        def show_failure(message: str) -> None:
            panel.mark_error(message)
            log_event(
                "update.startup.failed",
                latest_version=info.latest_version,
                error=message,
            )
            root.protocol("WM_DELETE_WINDOW", close_current_version)
            continue_button = tk.Button(
                root,
                text="继续使用当前版本",
                command=close_current_version,
                bg="#ffffff",
                fg="#111111",
                activebackground="#f3f3f3",
                relief="solid",
                bd=1,
                font=body_font,
            )
            continue_button.pack(pady=(0, 20))

        def worker() -> None:
            try:
                staged = _stage_with_progress(
                    stage,
                    info,
                    target,
                    progress,
                )
                staged_holder["value"] = staged
                progress("更新包已通过校验，正在准备安装", 1, 1)
            except Exception as exc:
                progress_queue.put(("error", str(exc), 0, 0))
                return
            progress_queue.put(("ready", staged.version, 1, 1))

        threading.Thread(
            target=worker,
            daemon=True,
            name="langlang-startup-update",
        ).start()

        def poll_progress() -> None:
            try:
                while True:
                    kind, message, current, total = progress_queue.get_nowait()
                    if kind == "progress":
                        panel.set_progress(message, current, total)
                        continue
                    if kind == "ready":
                        panel.mark_installing()
                        log_event(
                            "update.startup.ready",
                            latest_version=info.latest_version,
                        )
                        try:
                            launch(staged_holder["value"], target)
                        except Exception as exc:
                            show_failure(str(exc))
                            return
                        result["should_exit"] = True
                        root.protocol("WM_DELETE_WINDOW", close_prompt)
                        root.after(1200, root.destroy)
                        return
                    show_failure(message)
                    return
            except queue.Empty:
                pass
            try:
                root.after(70, poll_progress)
            except tk.TclError:
                return

        poll_progress()

    root.columnconfigure(0, weight=1)
    root.rowconfigure(1, weight=1)
    header = tk.Frame(root, bg="#ffffff", padx=20, pady=14)
    header.grid(row=0, column=0, sticky="ew")
    header.columnconfigure(1, weight=1)
    icon = tk.Canvas(header, width=36, height=36, bg="#ffffff", highlightthickness=0)
    icon.grid(row=0, column=0, rowspan=2, padx=(0, 12))
    icon.create_oval(2, 2, 34, 34, fill="#168bea", outline="")
    icon.create_text(18, 18, text="i", fill="white", font=("Segoe UI", 22, "bold"))
    tk.Label(
        header,
        text=f"发现新版本 v{info.latest_version}",
        bg="#ffffff",
        fg="#222222",
        font=title_font,
        anchor="w",
    ).grid(row=0, column=1, sticky="ew")
    tk.Label(
        header,
        text=f"当前版本 v{info.current_version}",
        bg="#ffffff",
        fg="#687b90",
        font=body_font,
        anchor="w",
    ).grid(row=1, column=1, sticky="ew", pady=(5, 0))

    summary_frame = tk.Frame(root, bg="#ffffff", padx=20, pady=0)
    summary_frame.grid(row=1, column=0, sticky="nsew")
    summary_frame.columnconfigure(0, weight=1)
    summary_frame.rowconfigure(0, weight=1)
    summary_text = tk.Text(
        summary_frame,
        wrap="word",
        borderwidth=1,
        relief="solid",
        bg="#f8fafb",
        fg="#33495c",
        padx=14,
        pady=12,
        font=body_font,
    )
    summary_text.grid(row=0, column=0, sticky="nsew")
    summary_scroll = tk.Scrollbar(
        summary_frame,
        orient="vertical",
        command=summary_text.yview,
    )
    summary_scroll.grid(row=0, column=1, sticky="ns")
    summary_text.configure(yscrollcommand=summary_scroll.set)
    summary_text.tag_configure(
        "group",
        foreground="#263f52",
        font=("Microsoft YaHei UI", 11, "bold"),
        spacing1=7,
        spacing3=3,
    )
    summary_text.tag_configure("item", lmargin1=14, lmargin2=28, spacing3=3)
    for group in summary_groups:
        summary_text.insert("end", f"{group.get('title') or '主要更新'}\n", "group")
        for item in group.get("items") or []:
            summary_text.insert("end", f"• {item}\n", "item")
        summary_text.insert("end", "\n")
    summary_text.insert(
        "end",
        "点击立即更新后将锁定界面，自动下载、校验、安装并重启。\n",
        "item",
    )
    summary_text.configure(state="disabled")

    footer = tk.Frame(root, bg="#f7f9fa", padx=18, pady=12)
    footer.grid(row=2, column=0, sticky="ew")
    release_notes_url = str(getattr(info, "release_notes_url", "") or "")
    if release_notes_url:
        notes_button = tk.Button(
            footer,
            text="查看完整更新说明",
            command=lambda: webbrowser.open(release_notes_url),
            bg="#f7f9fa",
            fg="#176bb1",
            activebackground="#edf5fb",
            relief="flat",
            bd=0,
            font=body_font,
            cursor="hand2",
        )
        notes_button.pack(side="left")
    update_button = tk.Button(
        footer,
        text="立即更新",
        command=begin_update,
        bg="#ffffff",
        fg="#111111",
        activebackground="#edf5ff",
        relief="solid",
        bd=1,
        highlightbackground="#168bea",
        font=body_font,
        padx=18,
        pady=7,
    )
    update_button.pack(side="right", padx=(8, 0))
    later_button = tk.Button(
        footer,
        text="稍后",
        command=close_current_version,
        bg="#ffffff",
        fg="#111111",
        activebackground="#f3f3f3",
        relief="solid",
        bd=1,
        font=body_font,
        padx=16,
        pady=6,
    )
    later_button.pack(side="right")
    interactive.extend((update_button, later_button))
    root.protocol("WM_DELETE_WINDOW", close_prompt)
    root.attributes("-topmost", True)
    root.mainloop()
    return result["should_exit"]


def _eligible_for_test_auto_update(
    info: UpdateInfo,
    policy: TestUpdatePolicy,
) -> bool:
    def version_tuple(value: str) -> tuple[int, ...]:
        return tuple(
            int("".join(char for char in part if char.isdigit()) or 0)
            for part in value.replace("-", ".").split(".")
        )

    return bool(
        info.available
        and version_tuple(info.latest_version)
        > version_tuple(info.current_version)
        and str(info.channel or "").casefold() == policy.channel
        and policy.allow_prerelease is False
        and info.prerelease is False
    )


def _run_test_auto_update(
    info: UpdateInfo,
    target: Path,
    *,
    policy: TestUpdatePolicy,
    stage: Callable[..., StagedUpdate],
    launch: Callable[[StagedUpdate, Path], Path],
    critical_tasks: Callable[[], bool],
    notice: Callable[[str, str], None],
    now: Callable[[], datetime] | None,
) -> bool:
    state_path = _auto_state_path(target)
    state = _reconcile_auto_update_state(
        target,
        state_path,
        notice,
        now,
    )
    if not _eligible_for_test_auto_update(info, policy):
        log_event(
            "update.auto.skipped",
            latest_version=info.latest_version,
            channel=info.channel,
            prerelease=info.prerelease,
            reason="not_stable_newer_release",
        )
        return False
    if critical_tasks():
        current = _installed_version(target)
        _update_auto_state(
            state_path,
            "pending_install",
            now=now,
            install_path=str(target),
            current_version=current,
            target_version=info.latest_version,
            channel=info.channel,
            prerelease=info.prerelease,
            next_startup=True,
            last_error="",
            notice="当前存在关键后台任务，将在下次启动安装",
        )
        log_event(
            "update.auto.deferred_active_tasks",
            latest_version=info.latest_version,
        )
        return False
    _update_auto_state(
        state_path,
        "detected",
        now=now,
        install_path=str(target),
        current_version=str(info.current_version),
        target_version=info.latest_version,
        channel=info.channel,
        prerelease=info.prerelease,
        next_startup=False,
        last_error="",
    )
    reusable = None
    previous_status = str(state.get("status") or "")
    if (
        previous_status in {"verified", "pending_install"}
        and str(state.get("target_version") or "") == info.latest_version
    ):
        package_root = Path(str(state.get("package_root") or ""))
        staging_root = Path(str(state.get("staging_root") or ""))
        archive_path = Path(str(state.get("archive_path") or ""))
        if (
            package_root.is_dir()
            and staging_root.is_dir()
            and (package_root / "LangLang.exe").is_file()
        ):
            reusable = StagedUpdate(
                version=info.latest_version,
                archive_path=archive_path,
                staging_root=staging_root,
                package_root=package_root,
            )
    if reusable is None:
        _update_auto_state(
            state_path,
            "downloading",
            now=now,
            target_version=info.latest_version,
        )
        try:
            reusable = _stage_with_progress(
                stage,
                info,
                target,
                lambda _message, _current, _total: None,
            )
        except Exception as exc:
            message = str(exc)[:2000]
            _update_auto_state(
                state_path,
                "failed",
                now=now,
                next_startup=False,
                last_error=message,
                notice=f"自动更新失败：{message}",
            )
            log_event(
                "update.auto.failed",
                latest_version=info.latest_version,
                error=message,
            )
            return False
    _update_auto_state(
        state_path,
        "verified",
        now=now,
        archive_path=str(reusable.archive_path),
        staging_root=str(reusable.staging_root),
        package_root=str(reusable.package_root),
        target_version=info.latest_version,
    )
    if critical_tasks():
        _update_auto_state(
            state_path,
            "pending_install",
            now=now,
            next_startup=True,
            notice="更新包已校验，将在下次启动安装",
        )
        log_event(
            "update.auto.verified_next_startup",
            latest_version=info.latest_version,
        )
        return False
    try:
        installer_log = launch(reusable, target)
    except Exception as exc:
        message = str(exc)[:2000]
        _update_auto_state(
            state_path,
            "failed",
            now=now,
            next_startup=False,
            last_error=message,
            notice=f"更新安装准备失败：{message}",
        )
        log_event(
            "update.auto.failed",
            latest_version=info.latest_version,
            error=message,
        )
        return False
    _update_auto_state(
        state_path,
        "pending_install",
        now=now,
        installer_log=str(installer_log),
        next_startup=False,
        last_error="",
    )
    log_event(
        "update.auto.pending_install",
        latest_version=info.latest_version,
        installer_log=str(installer_log),
    )
    return True


def run_startup_update(
    *,
    project_dir: Path | None = None,
    force: bool = False,
    check: Callable[[], UpdateInfo] | None = None,
    prompt: Callable[[UpdateInfo], bool] | None = None,
    stage: Callable[..., StagedUpdate] = download_and_stage_update,
    launch: Callable[[StagedUpdate, Path], Path] = launch_update_installer,
    allowed_test_install_dir: Path = DEFAULT_TEST_INSTALL_DIR,
    critical_tasks: Callable[[], bool] | None = None,
    notice: Callable[[str, str], None] | None = None,
    now_provider: Callable[[], datetime] | None = None,
) -> bool:
    """Return True when the launcher must exit while the updater takes over."""
    if not force and not getattr(sys, "frozen", False):
        return False
    if os.environ.get("LANGLANG_SKIP_STARTUP_UPDATE") == "1":
        return False
    target = (
        project_dir or Path(sys.executable).resolve().parent
    ).resolve()
    internal_resource_dir = target / "_internal"
    resource_dir = (
        internal_resource_dir
        if (internal_resource_dir / "assets").is_dir()
        else target
    )
    trust = load_trust_config(resource_dir)
    policy = load_stable_update_policy(
        target,
        trust_enabled=trust.enabled,
    )
    test_policy = load_test_update_policy(
        target,
        allowed_install_dir=allowed_test_install_dir,
    )
    if test_policy is not None:
        policy = test_policy
    current_version = (
        _installed_version(target)
        or __version__
    )
    try:
        info = (
            check()
            if check is not None
            else check_for_update(
                current_version=current_version,
                public_keys=trust.public_keys if trust.enabled else None,
            )
        )
    except Exception as exc:
        log_event("update.startup.check_failed", error=str(exc))
        if policy is not None:
            _update_auto_state(
                _auto_state_path(target),
                "failed",
                now=now_provider,
                install_path=str(target),
                current_version=current_version,
                last_error=str(exc)[:2000],
                notice="自动更新检查失败",
            )
        return False
    log_event(
        "update.startup.checked",
        current_version=info.current_version,
        latest_version=info.latest_version,
        available=info.available,
        mandatory=info.mandatory,
        channel=info.channel,
        prerelease=info.prerelease,
    )
    if policy is not None:
        return _run_test_auto_update(
            info,
            target,
            policy=policy,
            stage=stage,
            launch=launch,
            critical_tasks=critical_tasks or (lambda: False),
            notice=notice or _show_auto_update_notice,
            now=now_provider,
        )
    if not info.available:
        return False
    if not info.mandatory:
        log_event(
            "update.startup.optional_deferred_to_badge",
            latest_version=info.latest_version,
        )
        return False
    if prompt is None:
        return _run_update_flow(
            info,
            target,
            stage=stage,
            launch=launch,
        )
    if not prompt(info):
        return False
    try:
        staged = _stage_with_progress(
            stage,
            info,
            target,
            lambda _message, _current, _total: None,
        )
        launch(staged, target)
    except Exception:
        return False
    return True
