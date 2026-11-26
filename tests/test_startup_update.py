from __future__ import annotations

import threading
import tkinter as tk
from pathlib import Path

import pytest

from weimi_manager import startup_update
from weimi_manager.startup_update import run_startup_update
from weimi_manager.updater import StagedUpdate, UpdateInfo


def test_startup_update_declined_continues_current_version(tmp_path: Path) -> None:
    info = UpdateInfo(
        current_version="1.8.56",
        latest_version="1.8.57",
        available=True,
        notes="测试更新",
        mandatory=True,
    )
    prompts: list[str] = []

    should_exit = run_startup_update(
        project_dir=tmp_path,
        force=True,
        check=lambda: info,
        prompt=lambda candidate: prompts.append(candidate.latest_version) or False,
        stage=lambda candidate, project: (_ for _ in ()).throw(
            AssertionError("declined update must not download")
        ),
        launch=lambda staged, project: (_ for _ in ()).throw(
            AssertionError("declined update must not install")
        ),
    )

    assert should_exit is False
    assert prompts == ["1.8.57"]


def test_startup_update_accepts_and_exits_before_main_program(tmp_path: Path) -> None:
    info = UpdateInfo(
        current_version="1.8.56",
        latest_version="1.8.57",
        available=True,
        notes="测试更新",
        mandatory=True,
    )
    staged = StagedUpdate(
        version="1.8.57",
        archive_path=tmp_path / "update.zip",
        staging_root=tmp_path / "stage",
        package_root=tmp_path / "stage" / "LangLang",
    )
    calls: list[str] = []

    should_exit = run_startup_update(
        project_dir=tmp_path,
        force=True,
        check=lambda: info,
        prompt=lambda _candidate: True,
        stage=lambda candidate, project: calls.append("stage") or staged,
        launch=lambda candidate, project: calls.append("launch") or tmp_path / "update.log",
    )

    assert should_exit is True
    assert calls == ["stage", "launch"]


def test_no_update_does_not_show_prompt(tmp_path: Path) -> None:
    info = UpdateInfo(
        current_version="1.8.56",
        latest_version="1.8.56",
        available=False,
    )

    should_exit = run_startup_update(
        project_dir=tmp_path,
        force=True,
        check=lambda: info,
        prompt=lambda _candidate: (_ for _ in ()).throw(
            AssertionError("no-update state must not prompt")
        ),
        stage=lambda candidate, project: None,
        launch=lambda staged, project: None,
    )

    assert should_exit is False


def test_optional_update_is_deferred_to_in_app_badge(
    tmp_path: Path,
) -> None:
    info = UpdateInfo(
        current_version="1.8.80",
        latest_version="1.8.81",
        available=True,
        mandatory=False,
    )

    should_exit = run_startup_update(
        project_dir=tmp_path,
        force=True,
        check=lambda: info,
        prompt=lambda _candidate: (_ for _ in ()).throw(
            AssertionError("optional update must not open startup dialog")
        ),
    )

    assert should_exit is False


def test_startup_update_failure_returns_to_current_version(
    tmp_path: Path,
) -> None:
    info = UpdateInfo(
        current_version="1.8.79",
        latest_version="1.8.80",
        available=True,
        notes="测试更新",
        mandatory=True,
    )

    should_exit = run_startup_update(
        project_dir=tmp_path,
        force=True,
        check=lambda: info,
        prompt=lambda _candidate: True,
        stage=lambda _candidate, _project: (_ for _ in ()).throw(
            RuntimeError("下载失败")
        ),
        launch=lambda _staged, _project: (_ for _ in ()).throw(
            AssertionError("failed staging must not launch")
        ),
    )

    assert should_exit is False


def test_startup_update_stage_receives_real_progress_callback(
    tmp_path: Path,
) -> None:
    info = UpdateInfo(
        current_version="1.8.79",
        latest_version="1.8.80",
        available=True,
        notes="测试更新",
        mandatory=True,
    )
    staged = StagedUpdate(
        version="1.8.80",
        archive_path=tmp_path / "update.zip",
        staging_root=tmp_path / "stage",
        package_root=tmp_path / "stage" / "LangLang",
    )
    progress_events: list[tuple[str, int, int]] = []
    launches: list[str] = []

    def stage(candidate, project, *, progress):
        assert candidate is info
        assert project == tmp_path.resolve()
        progress("正在下载更新包", 512, 1024)
        progress_events.append(
            ("正在下载更新包", 512, 1024)
        )
        return staged

    should_exit = run_startup_update(
        project_dir=tmp_path,
        force=True,
        check=lambda: info,
        prompt=lambda _candidate: True,
        stage=stage,
        launch=lambda candidate, _project: (
            launches.append(candidate.version) or tmp_path / "update.log"
        ),
    )

    assert should_exit is True
    assert progress_events == [("正在下载更新包", 512, 1024)]
    assert launches == ["1.8.80"]


def _require_tk() -> None:
    try:
        root = tk.Tk()
        root.withdraw()
        root.destroy()
    except tk.TclError as exc:
        pytest.skip(str(exc))


def _find_button(root: tk.Misc, text: str) -> tk.Button | None:
    for child in root.winfo_children():
        if isinstance(child, tk.Button) and child.cget("text") == text:
            return child
        found = _find_button(child, text)
        if found is not None:
            return found
    return None


def _visible_text_values(root: tk.Misc) -> list[str]:
    values: list[str] = []
    for child in root.winfo_children():
        try:
            variable = str(child.cget("textvariable"))
        except tk.TclError:
            variable = ""
        if variable:
            try:
                value = str(child.getvar(variable))
            except tk.TclError:
                value = ""
            if value:
                values.append(value)
        values.extend(_visible_text_values(child))
    return values


def test_startup_update_ui_failure_returns_to_current_version(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _require_tk()
    info = UpdateInfo(
        current_version="1.8.79",
        latest_version="1.8.80",
        available=True,
        notes="测试更新",
        mandatory=True,
    )
    release_worker = threading.Event()
    observations: dict[str, object] = {}

    def stage(_info, _target, *, progress):
        progress("正在下载更新包", 256, 1024)
        release_worker.wait(timeout=5)
        raise RuntimeError("磁盘空间不足")

    OriginalTk = tk.Tk

    class AutoFailTk(OriginalTk):
        def mainloop(self, n: int = 0):
            def begin() -> None:
                button = _find_button(self, "立即更新")
                assert button is not None
                button.invoke()

            def release_failure() -> None:
                release_worker.set()

            def inspect_failure() -> None:
                observations["texts"] = _visible_text_values(self)
                button = _find_button(self, "继续使用当前版本")
                assert button is not None
                button.invoke()

            self.after(30, begin)
            self.after(180, release_failure)
            self.after(420, inspect_failure)
            return super().mainloop(n)

    monkeypatch.setattr(startup_update.tk, "Tk", AutoFailTk)
    try:
        should_exit = startup_update._run_update_flow(
            info,
            tmp_path,
            stage=stage,
            launch=lambda _candidate, _target: pytest.fail(
                "failed update must not launch"
            ),
        )
    except tk.TclError as exc:
        release_worker.set()
        pytest.skip(f"Tcl/Tk interpreter unavailable: {exc}")

    assert should_exit is False
    assert any("更新失败" in text for text in observations["texts"])
    assert any("磁盘空间不足" in text for text in observations["texts"])
