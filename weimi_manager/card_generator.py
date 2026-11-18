from __future__ import annotations

import csv
import json
import os
import sys
import threading
import tkinter as tk
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any, Callable

from .auth_client import AuthError, SupabaseAuthClient
from .auth_config import AuthConfig

APP_TITLE = "澜琅卡密生成器"
APP_VERSION = "1.0.1"
BG = "#f3f6f8"
PANEL = "#ffffff"
TEXT = "#17272b"
MUTED = "#62767b"
ACCENT = "#1769aa"
SUCCESS = "#147a5a"
DANGER = "#b42318"
BORDER = "#d8e0e4"


def _candidate_config_paths() -> list[Path]:
    bundle_root = Path(
        getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent)
    )
    executable_dir = (
        Path(sys.executable).resolve().parent
        if getattr(sys, "frozen", False)
        else Path(__file__).resolve().parent.parent
    )
    return [
        executable_dir / "_internal" / "assets" / "auth_config.json",
        executable_dir / "assets" / "auth_config.json",
        bundle_root / "assets" / "auth_config.json",
        Path.cwd() / "assets" / "auth_config.json",
    ]


def load_auth_config() -> AuthConfig:
    url = str(os.environ.get("LANGLANG_SUPABASE_URL") or "").strip()
    key = str(
        os.environ.get("LANGLANG_SUPABASE_PUBLISHABLE_KEY")
        or os.environ.get("LANGLANG_SUPABASE_ANON_KEY")
        or ""
    ).strip()
    if not url or not key:
        for path in _candidate_config_paths():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(payload, dict):
                continue
            url = str(
                payload.get("supabase_url")
                or payload.get("url")
                or ""
            ).strip()
            key = str(
                payload.get("publishable_key")
                or payload.get("anon_key")
                or ""
            ).strip()
            if url and key:
                break
    config = AuthConfig(url, key)
    config.require_configured()
    return config


@dataclass(frozen=True)
class CardBatch:
    batch_id: str
    card_type: str
    duration_days: int | None
    codes: list[str]


class AdminCardService:
    def __init__(self, client: Any) -> None:
        self.client = client

    def login(self, username: str, password: str) -> Any:
        normalized = str(username or "").strip()
        if not normalized:
            raise AuthError("请输入管理员账户名")
        if not password:
            raise AuthError("请输入管理员密码")
        return self.client.login_by_username(normalized, password)

    def generate(
        self,
        tokens: Any,
        *,
        count: int,
        card_type: str,
        duration_days: int,
        max_devices: int,
        rebind_penalty_hours: int,
        notes: str = "",
        available_days: int = 0,
    ) -> CardBatch:
        normalized_type = (
            "permanent" if card_type == "permanent" else "temporary"
        )
        normalized_count = max(1, min(500, int(count)))
        normalized_days = max(1, min(3650, int(duration_days)))
        normalized_devices = max(1, min(999, int(max_devices)))
        normalized_penalty = max(
            0,
            min(720, int(rebind_penalty_hours)),
        )
        normalized_available = max(
            0,
            min(3650, int(available_days)),
        )
        data = self.client.generate_license_cards(
            str(tokens.access_token),
            count=normalized_count,
            card_type=normalized_type,
            duration_days=normalized_days,
            max_devices=normalized_devices,
            rebind_penalty_hours=normalized_penalty,
            notes=str(notes or "").strip()[:500],
            available_days=normalized_available,
        )
        codes = [
            str(code).strip()
            for code in data.get("codes") or []
            if str(code).strip()
        ]
        if not codes:
            raise AuthError("服务端没有返回可用卡密")
        duration = (
            None
            if normalized_type == "permanent"
            else int(data.get("duration_days") or normalized_days)
        )
        return CardBatch(
            batch_id=str(data.get("batch_id") or ""),
            card_type=normalized_type,
            duration_days=duration,
            codes=codes,
        )


class CardGeneratorWindow(tk.Tk):
    def __init__(self, service: AdminCardService) -> None:
        super().__init__()
        self.service = service
        self.tokens: Any = None
        self.last_batch: CardBatch | None = None
        self.title(f"{APP_TITLE} · {APP_VERSION}")
        self.geometry("920x760")
        self.minsize(820, 680)
        self.configure(bg=BG)
        self._configure_style()
        self._build()

    def _configure_style(self) -> None:
        self.option_add("*Font", ("Microsoft YaHei UI", 10))
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TFrame", background=BG)
        style.configure("Panel.TFrame", background=PANEL)
        style.configure("TLabel", background=PANEL, foreground=TEXT)
        style.configure(
            "Title.TLabel",
            font=("Microsoft YaHei UI", 20, "bold"),
            foreground=TEXT,
        )
        style.configure(
            "Subtitle.TLabel",
            foreground=MUTED,
        )
        style.configure("TEntry", padding=8)
        style.configure("TCombobox", padding=7)
        style.configure("TSpinbox", padding=7)
        style.configure(
            "Primary.TButton",
            background=ACCENT,
            foreground="#ffffff",
            padding=(18, 10),
        )
        style.map(
            "Primary.TButton",
            background=[("active", "#12568b"), ("disabled", "#9aafbd")],
        )
        style.configure("TButton", padding=(12, 8))

    def _build(self) -> None:
        shell = ttk.Frame(self, padding=22)
        shell.pack(fill="both", expand=True)
        shell.columnconfigure(0, weight=1)
        shell.rowconfigure(1, weight=1)

        header = ttk.Frame(shell)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 16))
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text=APP_TITLE, style="Title.TLabel").grid(
            row=0,
            column=0,
            sticky="w",
        )
        ttk.Label(
            header,
            text="管理员专用 · 密钥仅保存在服务端",
            style="Subtitle.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(4, 0))
        self.session_label = ttk.Label(
            header,
            text="未登录",
            foreground=DANGER,
            style="Subtitle.TLabel",
        )
        self.session_label.grid(row=0, column=1, rowspan=2, sticky="e")

        self.content = ttk.Frame(shell, style="Panel.TFrame", padding=22)
        self.content.grid(row=1, column=0, sticky="nsew")
        self.content.columnconfigure(0, weight=1)
        self.content.rowconfigure(0, weight=1)
        self._build_login()
        self._build_generator()
        self._show_login()

    def _build_login(self) -> None:
        self.login_frame = ttk.Frame(self.content, style="Panel.TFrame")
        self.login_frame.columnconfigure(0, weight=1)
        ttk.Label(
            self.login_frame,
            text="管理员登录",
            font=("Microsoft YaHei UI", 16, "bold"),
        ).grid(row=0, column=0, sticky="w", pady=(0, 16))
        ttk.Label(
            self.login_frame,
            text="使用已设置为 admin 的澜琅账户。密码不会保存。",
            style="Subtitle.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(0, 18))

        self.username_var = tk.StringVar()
        self.password_var = tk.StringVar()
        ttk.Label(self.login_frame, text="管理员账户名").grid(
            row=2,
            column=0,
            sticky="w",
        )
        self.login_focus = ttk.Entry(
            self.login_frame,
            textvariable=self.username_var,
        )
        self.login_focus.grid(
            row=3,
            column=0,
            sticky="ew",
            pady=(5, 14),
        )
        ttk.Label(self.login_frame, text="管理员密码").grid(
            row=4,
            column=0,
            sticky="w",
        )
        self.password_focus = ttk.Entry(
            self.login_frame,
            textvariable=self.password_var,
            show="●",
        )
        self.password_focus.grid(
            row=5,
            column=0,
            sticky="ew",
            pady=(5, 18),
        )

        self.login_button = ttk.Button(
            self.login_frame,
            text="登录管理端",
            style="Primary.TButton",
            command=self._login,
        )
        self.login_button.grid(row=6, column=0, sticky="ew")
        self.login_frame.bind("<Return>", lambda _event: self._login())

    def _build_generator(self) -> None:
        self.generator_frame = ttk.Frame(
            self.content,
            style="Panel.TFrame",
        )
        self.generator_frame.columnconfigure(0, weight=1)
        self.generator_frame.rowconfigure(8, weight=1)

        title = ttk.Frame(
            self.generator_frame,
            style="Panel.TFrame",
        )
        title.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        title.columnconfigure(0, weight=1)
        ttk.Label(
            title,
            text="生成卡密",
            font=("Microsoft YaHei UI", 16, "bold"),
        ).grid(row=0, column=0, sticky="w")
        self.batch_label = ttk.Label(
            title,
            text="尚未生成",
            style="Subtitle.TLabel",
        )
        self.batch_label.grid(row=0, column=1, sticky="e")

        form = ttk.Frame(
            self.generator_frame,
            style="Panel.TFrame",
        )
        form.grid(row=1, column=0, sticky="ew")
        for column in range(4):
            form.columnconfigure(column, weight=1)
        self.count_var = tk.StringVar(value="10")
        self.type_var = tk.StringVar(value="临时卡")
        self.days_var = tk.StringVar(value="30")
        self.devices_var = tk.StringVar(value="1")
        self.penalty_var = tk.StringVar(value="24")
        self.available_var = tk.StringVar(value="0")
        self.notes_var = tk.StringVar()
        controls = (
            ("数量", self.count_var, "spin", 1, 500),
            ("类型", self.type_var, "combo", 0, 0),
            ("有效天数", self.days_var, "spin", 1, 3650),
            ("最大设备", self.devices_var, "spin", 1, 999),
            ("换绑扣时", self.penalty_var, "spin", 0, 720),
            ("兑换期限", self.available_var, "spin", 0, 3650),
        )
        for index, (label, variable, kind, low, high) in enumerate(controls):
            row = (index // 3) * 2
            column = index % 3
            ttk.Label(form, text=label).grid(
                row=row,
                column=column,
                sticky="w",
                padx=(0, 10),
                pady=(0, 4),
            )
            if kind == "combo":
                widget = ttk.Combobox(
                    form,
                    textvariable=variable,
                    values=("临时卡", "永久卡"),
                    state="readonly",
                )
            else:
                widget = ttk.Spinbox(
                    form,
                    from_=low,
                    to=high,
                    textvariable=variable,
                )
            widget.grid(
                row=row + 1,
                column=column,
                sticky="ew",
                padx=(0, 10),
                pady=(0, 12),
            )
        ttk.Label(form, text="备注").grid(
            row=4,
            column=0,
            sticky="w",
            pady=(0, 4),
        )
        ttk.Entry(form, textvariable=self.notes_var).grid(
            row=5,
            column=0,
            columnspan=3,
            sticky="ew",
            pady=(0, 14),
        )

        actions = ttk.Frame(
            self.generator_frame,
            style="Panel.TFrame",
        )
        actions.grid(row=2, column=0, sticky="ew", pady=(0, 12))
        self.generate_button = ttk.Button(
            actions,
            text="生成卡密",
            style="Primary.TButton",
            command=self._generate,
        )
        self.generate_button.pack(side="left")
        self.export_button = ttk.Button(
            actions,
            text="导出 CSV",
            command=self._export,
            state="disabled",
        )
        self.export_button.pack(side="left", padx=(8, 0))
        self.copy_button = ttk.Button(
            actions,
            text="复制全部",
            command=self._copy,
            state="disabled",
        )
        self.copy_button.pack(side="left", padx=(8, 0))
        ttk.Button(
            actions,
            text="退出登录",
            command=self._logout,
        ).pack(side="right")

        self.status_var = tk.StringVar(value="")
        ttk.Label(
            self.generator_frame,
            textvariable=self.status_var,
            foreground=MUTED,
        ).grid(row=3, column=0, sticky="w", pady=(0, 8))

        result_box = ttk.Frame(
            self.generator_frame,
            style="Panel.TFrame",
        )
        result_box.grid(row=8, column=0, sticky="nsew")
        result_box.columnconfigure(0, weight=1)
        result_box.rowconfigure(0, weight=1)
        self.result_text = tk.Text(
            result_box,
            wrap="none",
            height=12,
            bg="#f8fafb",
            fg=TEXT,
            relief="solid",
            borderwidth=1,
            padx=12,
            pady=10,
            font=("Consolas", 10),
        )
        self.result_text.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(
            result_box,
            orient="vertical",
            command=self.result_text.yview,
        )
        scroll.grid(row=0, column=1, sticky="ns")
        self.result_text.configure(yscrollcommand=scroll.set)

    def _show_login(self) -> None:
        self.generator_frame.grid_forget()
        self.login_frame.grid(row=0, column=0, sticky="nsew")
        self.after(80, self.login_focus.focus_set)

    def _show_generator(self) -> None:
        self.login_frame.grid_forget()
        self.generator_frame.grid(row=0, column=0, sticky="nsew")

    def _set_busy(self, busy: bool, message: str = "") -> None:
        state = "disabled" if busy else "normal"
        self.login_button.configure(state=state)
        self.generate_button.configure(state=state)
        if message:
            self.status_var.set(message)

    def _login(self) -> None:
        username = self.username_var.get().strip()
        password = self.password_var.get()
        self._set_busy(True, "正在验证管理员账户…")

        def work() -> Any:
            return self.service.login(username, password)

        def done(tokens: Any) -> None:
            self.tokens = tokens
            self.password_var.set("")
            self.session_label.configure(
                text=f"管理员：{username}",
                foreground=SUCCESS,
            )
            self._set_busy(False, "登录成功，可以生成卡密。")
            self._show_generator()

        self._run_worker(work, done)

    def _generate(self) -> None:
        if self.tokens is None:
            messagebox.showwarning(APP_TITLE, "请先登录管理员账户。")
            return
        try:
            count = int(self.count_var.get())
            days = int(self.days_var.get())
            devices = int(self.devices_var.get())
            penalty = int(self.penalty_var.get())
            available = int(self.available_var.get())
        except ValueError:
            messagebox.showwarning(APP_TITLE, "数量、天数和设备数必须是整数。")
            return
        card_type = (
            "permanent" if self.type_var.get() == "永久卡" else "temporary"
        )
        self._set_busy(True, "正在由云端生成并写入卡密…")

        def work() -> CardBatch:
            return self.service.generate(
                self.tokens,
                count=count,
                card_type=card_type,
                duration_days=days,
                max_devices=devices,
                rebind_penalty_hours=penalty,
                notes=self.notes_var.get().strip(),
                available_days=available,
            )

        def done(batch: CardBatch) -> None:
            self.last_batch = batch
            self.batch_label.configure(text=f"批次：{batch.batch_id}")
            self.result_text.delete("1.0", "end")
            self.result_text.insert("1.0", "\n".join(batch.codes))
            self.export_button.configure(state="normal")
            self.copy_button.configure(state="normal")
            self._set_busy(False, f"已生成 {len(batch.codes)} 张卡密。")

        self._run_worker(work, done)

    def _run_worker(
        self,
        work: Callable[[], Any],
        on_success: Callable[[Any], None],
    ) -> None:
        def runner() -> None:
            try:
                result = work()
            except Exception as exc:
                message = str(exc)
                self.after(0, lambda: self._failed(message))
            else:
                self.after(0, lambda: on_success(result))

        threading.Thread(
            target=runner,
            daemon=True,
            name="langlang-card-admin",
        ).start()

    def _failed(self, message: str) -> None:
        self._set_busy(False)
        self.status_var.set(message)
        messagebox.showerror(APP_TITLE, message)

    def _export(self) -> None:
        batch = self.last_batch
        if batch is None:
            return
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        batch_name = batch.batch_id or f"batch_{timestamp}"
        default_name = (
            f"澜琅卡密_{batch_name}.csv"
        )
        path = filedialog.asksaveasfilename(
            parent=self,
            title="导出卡密",
            defaultextension=".csv",
            initialfile=default_name,
            filetypes=[("CSV 文件", "*.csv")],
        )
        if not path:
            return
        with Path(path).open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(
                ["卡密", "类型", "天数", "最大设备", "换绑扣时", "批次"]
            )
            for code in batch.codes:
                writer.writerow(
                    [
                        code,
                        "永久" if batch.card_type == "permanent" else "临时",
                        "" if batch.duration_days is None
                        else batch.duration_days,
                        self.devices_var.get(),
                        self.penalty_var.get(),
                        batch.batch_id,
                    ]
                )
        self.status_var.set(f"已导出：{Path(path).name}")

    def _copy(self) -> None:
        if self.last_batch is None:
            return
        self.clipboard_clear()
        self.clipboard_append("\n".join(self.last_batch.codes))
        self.status_var.set(
            f"已复制 {len(self.last_batch.codes)} 张卡密到剪贴板。"
        )

    def _logout(self) -> None:
        self.tokens = None
        self.last_batch = None
        self.result_text.delete("1.0", "end")
        self.export_button.configure(state="disabled")
        self.copy_button.configure(state="disabled")
        self.session_label.configure(
            text="未登录",
            foreground=DANGER,
        )
        self._set_busy(False, "")
        self._show_login()


def main() -> int:
    try:
        config = load_auth_config()
    except Exception as exc:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(APP_TITLE, str(exc), parent=root)
        root.destroy()
        return 2
    service = AdminCardService(SupabaseAuthClient(config))
    window = CardGeneratorWindow(service)
    window.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
