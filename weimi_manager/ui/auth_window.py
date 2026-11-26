from __future__ import annotations

import os
import queue
import re
import sys
import threading
import time
import tkinter as tk
from collections.abc import Callable
from datetime import UTC, datetime
from tkinter import messagebox
from typing import Any

from .. import __display_version__
from ..auth_client import AuthError
from ..auth_service import AuthContext, AuthService
from ..auth_session import SavedAccount
from ..config import AppPaths
from ..runtime_logging import (
    get_runtime_log_manager,
    log_error,
    log_event,
)
from ..updater import (
    check_for_update,
    download_and_stage_update,
    launch_update_installer,
)
from .update_progress import UpdateProgressPanel
from .update_notes_dialog import UpdateNotesDialog

COLORS = {
    "window": "#e9e4d9",
    "left": "#123f37",
    "left_deep": "#0e312c",
    "left_soft": "#1b5147",
    "panel": "#fffdf9",
    "panel_soft": "#f5f1e8",
    "text": "#18332d",
    "muted": "#77827d",
    "accent": "#167b67",
    "accent_hover": "#116b59",
    "line": "#dfd8ca",
    "success": "#198562",
    "danger": "#b55449",
    "white": "#f7fbf8",
}
INPUT_BACKGROUND = "#FFFFFF"
INPUT_TEXT = "#14253D"
INPUT_DISABLED_TEXT = "#77827D"
EMAIL_PATTERN = re.compile(
    r"^[A-Z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?"
    r"(?:\.[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?)+$",
    re.IGNORECASE,
)


def license_allows_entry(status: Any) -> bool:
    if bool(getattr(status, "permanent", False)):
        return True
    if bool(getattr(status, "active", False)):
        return True
    expires_at = str(getattr(status, "expires_at", "") or "")
    if not expires_at:
        return False
    try:
        expiry = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    except ValueError:
        return False
    server_time = str(getattr(status, "server_time", "") or "")
    try:
        current = datetime.fromisoformat(server_time.replace("Z", "+00:00"))
    except ValueError:
        current = datetime.now(UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=UTC)
    return expiry > current


class AuthWindow(tk.Tk):
    """First-run and startup account gateway using the selected 云岚 design."""

    def __init__(
        self,
        paths: AppPaths,
        service: AuthService,
    ) -> None:
        super().__init__()
        self.paths = paths
        self.service = service
        from .v2.theme import THEMES, load_preferences

        self.ui_prefs = load_preferences()
        palette = THEMES[self.ui_prefs.theme]
        auth_left = {
            "cloud_blue": ("#17324F", "#10253D", "#284B6D"),
            "jade_mist": ("#163D38", "#102D29", "#28554E"),
            "midnight_slate": ("#172033", "#111827", "#253C60"),
        }.get(self.ui_prefs.theme, ("#17324F", "#10253D", "#284B6D"))
        COLORS.update(
            {
                "window": palette.background,
                "left": auth_left[0],
                "left_deep": auth_left[1],
                "left_soft": auth_left[2],
                "panel": palette.surface,
                "panel_soft": palette.table_header,
                "text": palette.text,
                "muted": palette.text_muted,
                "accent": palette.primary,
                "accent_hover": palette.primary_hover,
                "line": palette.border,
                "success": palette.success,
                "danger": palette.danger,
                "white": "#FFFFFF",
            }
        )
        self.result: AuthContext | None = None
        self._context: AuthContext | None = None
        self._screen = ""
        self._busy = False
        self._saved_accounts: list[SavedAccount] = []
        self._register_email = ""
        self._register_countdown = 0
        self._recovery_email = ""
        self._update_check_running = False
        self._available_update: Any | None = None
        self._update_in_progress = False
        self._update_worker_active = False
        self._update_install_started = False
        self._update_lock_states: dict[tk.Misc, str] = {}
        self._login_update_dialog: tk.Toplevel | None = None
        self._login_update_panel: UpdateProgressPanel | None = None
        self._update_details_dialog: UpdateNotesDialog | None = None
        self._after_ids: set[str] = set()
        self._queue: queue.Queue[tuple[Callable[..., Any], tuple[Any, ...]]] = queue.Queue()

        self.title(f"澜琅 · 登录 {__display_version__}")
        self.configure(bg=COLORS["window"])
        self.geometry("1120x820")
        self.minsize(1020, 740)
        self.resizable(True, True)
        self.protocol("WM_DELETE_WINDOW", self._close)
        self._load_icon()
        self._build()
        self._center_window()
        self._schedule(70, self._drain_queue)
        self._load_saved_accounts()
        self._show_screen("login")
        log_event(
            "auth.window_ready",
            version=__display_version__,
            resource_dir=str(self.paths.resource_dir),
        )
        if (
            getattr(sys, "frozen", False)
            and os.environ.get("WEIMI_DISABLE_AUTO_UPDATE") != "1"
        ):
            self._schedule(
                1200,
                lambda: self._check_for_updates(manual=False),
            )
            self._schedule(
                30 * 60 * 1000,
                self._periodic_update_check,
            )

    def _periodic_update_check(self) -> None:
        if not self._update_in_progress and not self._update_worker_active:
            self._check_for_updates(manual=False)
        try:
            if self.winfo_exists():
                self._schedule(
                    30 * 60 * 1000,
                    self._periodic_update_check,
                )
        except tk.TclError:
            return

    def _schedule(self, delay_ms: int, callback: Callable[[], None]) -> None:
        callback_id = ""

        def run() -> None:
            self._after_ids.discard(callback_id)
            callback()

        callback_id = self.after(delay_ms, run)
        self._after_ids.add(callback_id)

    def report_callback_exception(
        self,
        exception_type: type[BaseException],
        value: BaseException,
        tb: Any,
    ) -> None:
        value.__traceback__ = tb
        log_error("auth.tk_callback_exception", value)
        super().report_callback_exception(exception_type, value, tb)

    def _cancel_scheduled(self) -> None:
        for callback_id in tuple(self._after_ids):
            try:
                self.after_cancel(callback_id)
            except tk.TclError:
                pass
        self._after_ids.clear()

    def _load_icon(self) -> None:
        icon_path = self.paths.resource_dir / "assets" / "icon.ico"
        if icon_path.is_file():
            try:
                self.iconbitmap(str(icon_path))
            except tk.TclError:
                pass

    def _center_window(self) -> None:
        self.update_idletasks()
        width = min(1120, max(1020, self.winfo_screenwidth() - 80))
        height = min(820, max(740, self.winfo_screenheight() - 80))
        x = max(0, (self.winfo_screenwidth() - width) // 2)
        y = max(0, (self.winfo_screenheight() - height) // 2 - 20)
        self.geometry(f"{width}x{height}+{x}+{y}")

    def _build(self) -> None:
        shell = tk.Frame(self, bg=COLORS["window"])
        shell.pack(fill="both", expand=True, padx=24, pady=24)
        shell.columnconfigure(0, weight=4, minsize=360)
        shell.columnconfigure(1, weight=6, minsize=520)
        shell.rowconfigure(0, weight=1)

        left = tk.Frame(shell, bg=COLORS["left"], width=410)
        left.grid(row=0, column=0, sticky="nsew")
        left.grid_propagate(False)
        self._build_left(left)

        right = tk.Frame(shell, bg=COLORS["panel"])
        right.grid(row=0, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)
        right.rowconfigure(0, weight=1)
        self.card_host = tk.Frame(right, bg=COLORS["panel"])
        self.card_host.grid(row=0, column=0, sticky="nsew", padx=44)
        self.card_host.columnconfigure(0, weight=1)
        self.card_host.rowconfigure(0, weight=1)
        tk.Label(
            right,
            text=(
                "继续使用即表示同意《服务条款》与《隐私政策》。"
                "为提供经营分析，会同步去标识化的店铺、商品、订单和任务统计；"
                "不上传账号密钥、Cookie、支付凭据或买家联系方式。"
            ),
            bg=COLORS["panel"],
            fg="#a6aaa5",
            font=("Microsoft YaHei UI", 8),
            wraplength=520,
            justify="center",
        ).grid(row=1, column=0, pady=(0, 18))

        self.card = tk.Frame(
            self.card_host,
            bg=COLORS["panel"],
            highlightbackground=COLORS["line"],
            highlightthickness=1,
            padx=34,
            pady=30,
        )
        self.card.grid(row=0, column=0)
        self.card.columnconfigure(0, weight=1)

        self._build_login_screen()
        self._build_register_screen()
        self._build_forgot_screen()
        self._build_license_screen()

        self.status_var = tk.StringVar(value="")
        self.status_label = tk.Label(
            self.card,
            textvariable=self.status_var,
            bg=COLORS["panel"],
            fg=COLORS["muted"],
            font=("Microsoft YaHei UI", 9),
            anchor="w",
            justify="left",
            wraplength=470,
        )
        self.status_label.grid(row=20, column=0, sticky="ew", pady=(12, 0))

    def _build_left(self, parent: tk.Frame) -> None:
        canvas = tk.Canvas(
            parent,
            bg=COLORS["left"],
            highlightthickness=0,
        )
        canvas.place(relwidth=1, relheight=1)
        canvas.create_oval(
            255,
            -140,
            590,
            195,
            fill="#275f54",
            outline="",
        )
        canvas.create_oval(
            -170,
            390,
            180,
            720,
            fill="#24594f",
            outline="",
        )

        content = tk.Frame(parent, bg=COLORS["left"])
        content.pack(fill="both", expand=True, padx=34, pady=32)
        brand = tk.Frame(content, bg=COLORS["left"])
        brand.pack(anchor="w")
        logo_box = tk.Frame(
            brand,
            bg=COLORS["accent"],
            width=58,
            height=58,
        )
        logo_box.pack(side="left", padx=(0, 14))
        logo_box.pack_propagate(False)
        tk.Label(
            logo_box,
            text="澜",
            bg=COLORS["accent"],
            fg="#FFFFFF",
            font=("Microsoft YaHei UI", 24, "bold"),
        ).place(relx=0.5, rely=0.5, anchor="center")
        name_box = tk.Frame(brand, bg=COLORS["left"])
        name_box.pack(side="left")
        tk.Label(
            name_box,
            text="澜琅",
            bg=COLORS["left"],
            fg=COLORS["white"],
            font=("Microsoft YaHei UI", 20, "bold"),
        ).pack(anchor="w")
        tk.Label(
            name_box,
            text="店 铺 管 理",
            bg=COLORS["left"],
            fg="#a9c4bc",
            font=("Microsoft YaHei UI", 8),
        ).pack(anchor="w", pady=(2, 0))

        self.update_badge = tk.Button(
            parent,
            text="",
            command=self._show_update_details,
            bg="#f4c95d",
            fg="#17324a",
            activebackground="#ffd978",
            activeforeground="#17324a",
            justify="left",
            anchor="w",
            relief="flat",
            bd=0,
            padx=12,
            pady=7,
            cursor="hand2",
            font=("Microsoft YaHei UI", 9, "bold"),
        )
        self.update_badge.place(relx=0.5, y=108, anchor="n", width=330)
        self.update_badge.place_forget()

        copy = tk.Frame(content, bg=COLORS["left"])
        copy.pack(side="bottom", anchor="w", fill="x")
        tk.Label(
            copy,
            text="多店经营，从容有序",
            bg=COLORS["left"],
            fg="#9bbab1",
            font=("Microsoft YaHei UI", 9),
        ).pack(anchor="w")
        tk.Label(
            copy,
            text="把复杂的店铺，\n放进清晰的日常。",
            bg=COLORS["left"],
            fg=COLORS["white"],
            justify="left",
            font=("Microsoft YaHei UI", 25, "bold"),
            pady=12,
        ).pack(anchor="w")
        tk.Label(
            copy,
            text="集中管理微信小店、订单与商品；\n登录状态加密迁移，换电脑也能继续工作。",
            bg=COLORS["left"],
            fg="#b9cdc7",
            justify="left",
            font=("Microsoft YaHei UI", 9),
            pady=0,
        ).pack(anchor="w")

        chips = tk.Frame(copy, bg=COLORS["left"])
        chips.pack(anchor="w", pady=(24, 20))
        for text in ("本地加密", "账号隔离", "一键迁移"):
            tk.Label(
                chips,
                text=text,
                bg=COLORS["left_soft"],
                fg="#d9e8e3",
                padx=11,
                pady=6,
                font=("Microsoft YaHei UI", 8),
            ).pack(side="left", padx=(0, 7))

        steps = tk.Frame(copy, bg=COLORS["left"])
        steps.pack(anchor="w", fill="x", pady=(4, 0))
        for index, (title, subtitle) in enumerate(
            (
                ("账户验证", "登录与找回密码"),
                ("卡密授权", "验证使用期限"),
                ("店铺工作台", "开始多店经营"),
            ),
            1,
        ):
            item = tk.Frame(steps, bg=COLORS["left"])
            item.pack(side="left", padx=(0, 18))
            tk.Label(
                item,
                text=f"0{index}",
                bg=COLORS["left"],
                fg="#82afa2",
                font=("Microsoft YaHei UI", 8),
            ).pack(anchor="w")
            tk.Label(
                item,
                text=title,
                bg=COLORS["left"],
                fg="#e1ede9",
                font=("Microsoft YaHei UI", 8, "bold"),
            ).pack(anchor="w")
            tk.Label(
                item,
                text=subtitle,
                bg=COLORS["left"],
                fg="#7fa197",
                font=("Microsoft YaHei UI", 7),
            ).pack(anchor="w")

    def _screen_frame(self, name: str) -> tk.Frame:
        frame = tk.Frame(self.card, bg=COLORS["panel"])
        frame.grid(row=0, column=0, sticky="ew")
        frame._screen_name = name  # type: ignore[attr-defined]
        return frame

    def _heading(
        self,
        parent: tk.Frame,
        overline: str,
        title: str,
        subtitle: str = "",
    ) -> tk.Frame:
        box = tk.Frame(parent, bg=COLORS["panel"])
        box.pack(fill="x")
        tk.Label(
            box,
            text=overline,
            bg=COLORS["panel"],
            fg=COLORS["muted"],
            font=("Microsoft YaHei UI", 8),
        ).pack(anchor="w")
        tk.Label(
            box,
            text=title,
            bg=COLORS["panel"],
            fg=COLORS["text"],
            font=("Microsoft YaHei UI", 22, "bold"),
        ).pack(anchor="w", pady=(6, 0))
        if subtitle:
            tk.Label(
                box,
                text=subtitle,
                bg=COLORS["panel"],
                fg=COLORS["muted"],
                font=("Microsoft YaHei UI", 9),
                wraplength=455,
                justify="left",
            ).pack(anchor="w", pady=(7, 0))
        return box

    def _field(
        self,
        parent: tk.Frame,
        label: str,
        *,
        show: str = "",
    ) -> tk.Entry:
        wrapper = tk.Frame(parent, bg=COLORS["panel"])
        wrapper.pack(fill="x", pady=(0, 10))
        tk.Label(
            wrapper,
            text=label,
            bg=COLORS["panel"],
            fg=COLORS["text"],
            font=("Microsoft YaHei UI", 10, "bold"),
        ).pack(anchor="w", pady=(0, 5))
        border = tk.Frame(
            wrapper,
            bg=INPUT_BACKGROUND,
            highlightbackground=COLORS["line"],
            highlightthickness=1,
        )
        border.pack(fill="x")
        entry = tk.Entry(
            border,
            bd=0,
            relief="flat",
            bg=INPUT_BACKGROUND,
            fg=INPUT_TEXT,
            disabledforeground=INPUT_DISABLED_TEXT,
            insertbackground=INPUT_TEXT,
            selectbackground=COLORS["accent"],
            selectforeground="#FFFFFF",
            font=("Microsoft YaHei UI", 10),
            show=show,
        )
        entry.pack(fill="x", padx=12, pady=12)
        entry.bind(
            "<FocusIn>",
            lambda _event: border.configure(
                highlightbackground=COLORS["accent"],
            ),
        )
        entry.bind(
            "<FocusOut>",
            lambda _event: border.configure(
                highlightbackground=COLORS["line"],
            ),
        )
        return entry

    def _button(
        self,
        parent: tk.Frame,
        text: str,
        command: Callable[[], None],
        *,
        primary: bool = False,
    ) -> tk.Button:
        return tk.Button(
            parent,
            text=text,
            command=command,
            bg=COLORS["accent"] if primary else COLORS["panel_soft"],
            fg=COLORS["white"] if primary else COLORS["text"],
            activebackground=(COLORS["accent_hover"] if primary else "#eae4d8"),
            activeforeground=COLORS["white"] if primary else COLORS["text"],
            relief="flat",
            bd=0,
            padx=14,
            pady=10,
            cursor="hand2",
            font=("Microsoft YaHei UI", 9, "bold" if primary else "normal"),
        )

    def _build_login_screen(self) -> None:
        frame = self._screen_frame("login")
        self.login_screen = frame
        self._heading(frame, "欢迎回来", "登录澜琅")
        tabs = tk.Frame(frame, bg=COLORS["panel_soft"], padx=4, pady=4)
        tabs.pack(fill="x", pady=(20, 16))
        login_tab = tk.Button(
            tabs,
            text="登录",
            command=lambda: self._show_screen("login"),
            bg=COLORS["panel"],
            fg=COLORS["text"],
            relief="flat",
            bd=0,
            pady=8,
            font=("Microsoft YaHei UI", 9, "bold"),
        )
        login_tab.pack(side="left", fill="x", expand=True)
        register_tab = tk.Button(
            tabs,
            text="注册",
            command=lambda: self._show_screen("register"),
            bg=COLORS["panel_soft"],
            fg=COLORS["muted"],
            activebackground=COLORS["panel_soft"],
            relief="flat",
            bd=0,
            pady=8,
            font=("Microsoft YaHei UI", 9),
        )
        register_tab.pack(side="left", fill="x", expand=True)

        self.login_account = self._field(frame, "账户名")
        self.login_password = self._field(frame, "密码", show="●")

        meta = tk.Frame(frame, bg=COLORS["panel"])
        meta.pack(fill="x", pady=(0, 8))
        self.remember_var = tk.BooleanVar(value=True)
        tk.Checkbutton(
            meta,
            text="保存密码",
            variable=self.remember_var,
            bg=COLORS["panel"],
            fg=COLORS["text"],
            activebackground=COLORS["panel"],
            selectcolor=COLORS["panel"],
            bd=0,
            font=("Microsoft YaHei UI", 8),
        ).pack(side="left")
        tk.Button(
            meta,
            text="忘记密码？",
            command=lambda: self._show_screen("forgot"),
            bg=COLORS["panel"],
            fg=COLORS["accent"],
            activebackground=COLORS["panel"],
            relief="flat",
            bd=0,
            font=("Microsoft YaHei UI", 8),
        ).pack(side="right")
        tk.Button(
            meta,
            text="修改密码",
            command=self._change_password,
            bg=COLORS["panel"],
            fg=COLORS["accent"],
            activebackground=COLORS["panel"],
            relief="flat",
            bd=0,
            font=("Microsoft YaHei UI", 8),
        ).pack(side="right", padx=(0, 12))
        self.update_button = tk.Button(
            meta,
            text="检查更新",
            command=lambda: self._check_for_updates(manual=True),
            bg=COLORS["panel"],
            fg=COLORS["accent"],
            activebackground=COLORS["panel"],
            relief="flat",
            bd=0,
            font=("Microsoft YaHei UI", 8),
        )
        self.update_button.pack(side="right", padx=(0, 12))
        tk.Label(
            frame,
            text="密码输入框始终隐藏；保存的是 Windows 加密登录令牌。",
            bg=COLORS["panel"],
            fg="#9a9f9a",
            font=("Microsoft YaHei UI", 7),
            anchor="w",
        ).pack(fill="x", pady=(0, 9))
        self.login_button = self._button(
            frame,
            "登录",
            self._login,
            primary=True,
        )
        self.login_button.pack(fill="x")

        self._saved_box = tk.Frame(frame, bg=COLORS["panel"])
        self._saved_box.pack(fill="x", pady=(14, 0))

    def _build_register_screen(self) -> None:
        frame = self._screen_frame("register")
        self.register_screen = frame
        tk.Button(
            frame,
            text="← 返回登录",
            command=lambda: self._show_screen("login"),
            bg=COLORS["panel"],
            fg=COLORS["muted"],
            relief="flat",
            bd=0,
            font=("Microsoft YaHei UI", 8),
        ).pack(anchor="w", pady=(0, 9))
        self._heading(frame, "创建账户", "开始使用澜琅")

        email_wrapper = tk.Frame(frame, bg=COLORS["panel"])
        email_wrapper.pack(fill="x", pady=(0, 9))
        tk.Label(
            email_wrapper,
            text="邮箱",
            bg=COLORS["panel"],
            fg=COLORS["text"],
            font=("Microsoft YaHei UI", 10, "bold"),
        ).pack(anchor="w", pady=(0, 5))
        email_row = tk.Frame(email_wrapper, bg=COLORS["panel"])
        email_row.pack(fill="x")
        email_border = tk.Frame(
            email_row,
            bg=INPUT_BACKGROUND,
            highlightbackground=COLORS["line"],
            highlightthickness=1,
        )
        email_border.pack(side="left", fill="x", expand=True)
        self.register_email = tk.Entry(
            email_border,
            bd=0,
            relief="flat",
            bg=INPUT_BACKGROUND,
            fg=INPUT_TEXT,
            disabledforeground=INPUT_DISABLED_TEXT,
            insertbackground=INPUT_TEXT,
            selectbackground=COLORS["accent"],
            selectforeground="#FFFFFF",
            font=("Microsoft YaHei UI", 10),
        )
        self.register_email.pack(fill="x", padx=12, pady=12)
        self.register_email.bind(
            "<FocusIn>",
            lambda _event: email_border.configure(
                highlightbackground=COLORS["accent"],
            ),
        )
        self.register_email.bind(
            "<FocusOut>",
            lambda _event: email_border.configure(
                highlightbackground=COLORS["line"],
            ),
        )
        self.register_email.bind(
            "<KeyRelease>",
            lambda _event: self._update_register_send_state(),
        )
        self.register_email.bind(
            "<Return>",
            lambda _event: self._begin_registration(),
        )
        self.register_send_button = self._button(
            email_row,
            "发送邮箱验证码",
            self._begin_registration,
            primary=True,
        )
        self.register_send_button.pack(side="left", padx=(8, 0), fill="y")

        self.register_code = self._field(frame, "邮箱验证码")
        self.register_code.configure(state="disabled")
        self.register_username = self._field(frame, "账户名")
        self.register_password = self._field(frame, "密码", show="●")
        self.register_confirm = self._field(frame, "确认密码", show="●")
        self.register_verify_button = self._button(
            frame,
            "验证邮箱并完成注册",
            self._verify_registration,
            primary=True,
        )
        self.register_verify_button.configure(state="disabled")
        self.register_verify_button.pack(fill="x", pady=(7, 0))
        self.register_link_button = self._button(
            frame,
            "我已点击邮件中的确认链接",
            self._verify_registration_link,
        )
        self.register_link_button.configure(state="disabled")
        self.register_link_button.pack_forget()
        self._update_register_send_state()

    def _build_forgot_screen(self) -> None:
        frame = self._screen_frame("forgot")
        self.forgot_screen = frame
        tk.Button(
            frame,
            text="← 返回登录",
            command=lambda: self._show_screen("login"),
            bg=COLORS["panel"],
            fg=COLORS["muted"],
            relief="flat",
            bd=0,
            font=("Microsoft YaHei UI", 8),
        ).pack(anchor="w", pady=(0, 9))
        self._heading(
            frame,
            "找回密码",
            "验证绑定邮箱",
            "验证码发送到账户绑定的邮箱，不会公开完整邮箱地址。",
        )
        self.forgot_identifier = self._field(frame, "账户名或邮箱")
        self.forgot_send_button = self._button(
            frame,
            "发送找回验证码",
            self._begin_recovery,
        )
        self.forgot_send_button.pack(fill="x", pady=(2, 8))
        self.forgot_code = self._field(frame, "邮箱验证码")
        self.forgot_password = self._field(frame, "新密码", show="●")
        self.forgot_confirm = self._field(frame, "确认新密码", show="●")
        self.forgot_reset_button = self._button(
            frame,
            "验证并重置密码",
            self._reset_password,
            primary=True,
        )
        self.forgot_reset_button.pack(fill="x")

    def _build_license_screen(self) -> None:
        frame = self._screen_frame("license")
        self.license_screen = frame
        self._heading(
            frame,
            "授权已到期",
            "输入卡密继续使用",
            "当前账户授权已到期，请验证新卡密后继续使用澜琅。",
        )
        summary = tk.Frame(frame, bg=COLORS["panel_soft"], padx=14, pady=12)
        summary.pack(fill="x", pady=(17, 12))
        tk.Label(
            summary,
            text="当前账户",
            bg=COLORS["panel_soft"],
            fg=COLORS["muted"],
            font=("Microsoft YaHei UI", 8),
        ).grid(row=0, column=0, sticky="w")
        self.license_account = tk.Label(
            summary,
            text="",
            bg=COLORS["panel_soft"],
            fg=COLORS["text"],
            font=("Microsoft YaHei UI", 11, "bold"),
        )
        self.license_account.grid(row=1, column=0, sticky="w", pady=(3, 0))
        tk.Label(
            summary,
            text="授权状态",
            bg=COLORS["panel_soft"],
            fg=COLORS["muted"],
            font=("Microsoft YaHei UI", 8),
        ).grid(row=0, column=1, sticky="w", padx=(35, 0))
        self.license_status = tk.Label(
            summary,
            text="",
            bg=COLORS["panel_soft"],
            fg=COLORS["accent"],
            font=("Microsoft YaHei UI", 11, "bold"),
        )
        self.license_status.grid(
            row=1,
            column=1,
            sticky="w",
            padx=(35, 0),
            pady=(3, 0),
        )
        self.license_code = self._field(frame, "卡密")
        self.license_redeem_button = self._button(
            frame,
            "验证卡密并延长授权",
            self._redeem_card,
            primary=True,
        )
        self.license_redeem_button.pack(fill="x")
        self.license_enter_button = self._button(
            frame,
            "进入澜琅工作台",
            self._finish,
        )
        self.license_enter_button.configure(state="disabled")

    def _show_screen(self, name: str) -> None:
        if self._update_in_progress:
            self._set_status("正在更新，请等待完成。", error=True)
            return
        screens = {
            "login": self.login_screen,
            "register": self.register_screen,
            "forgot": self.forgot_screen,
            "license": self.license_screen,
        }
        for screen in screens.values():
            screen.grid_remove()
        screens[name].grid()
        log_event("auth.screen_changed", screen=name)
        self._screen = name
        self._set_status("")
        if name == "login":
            self.protocol("WM_DELETE_WINDOW", self._close)
            self._schedule(80, self.login_account.focus_set)
        elif name == "register":
            self._schedule(80, self.register_email.focus_set)
        elif name == "forgot":
            self.protocol("WM_DELETE_WINDOW", self._close)
            self._schedule(80, self.forgot_identifier.focus_set)
        elif name == "license":
            self.protocol("WM_DELETE_WINDOW", lambda: None)
            self._schedule(80, self.license_code.focus_set)

    def _set_status(self, message: str, error: bool = False) -> None:
        self.status_var.set(message)
        self.status_label.configure(fg=COLORS["danger"] if error else COLORS["muted"])

    def _set_update_lock(self, locked: bool) -> None:
        if locked:
            if self._update_in_progress:
                return
            self._update_lock_states.clear()
            for widget in self._interactive_descendants(self):
                try:
                    state = str(widget.cget("state"))
                except tk.TclError:
                    continue
                if not state:
                    continue
                self._update_lock_states[widget] = state
                widget.configure(state="disabled")
            self._update_in_progress = True
            self.protocol("WM_DELETE_WINDOW", self._guard_close_during_update)
            try:
                self.configure(cursor="watch")
            except tk.TclError:
                pass
            return

        self._update_in_progress = False
        for widget, state in tuple(self._update_lock_states.items()):
            try:
                if widget.winfo_exists():
                    widget.configure(state=state)
            except tk.TclError:
                pass
        self._update_lock_states.clear()
        try:
            self._restore_window_close_handler()
            self.configure(cursor="")
        except tk.TclError:
            pass

    def _restore_window_close_handler(self) -> None:
        if self._screen == "license":
            self.protocol("WM_DELETE_WINDOW", lambda: None)
        else:
            self.protocol("WM_DELETE_WINDOW", self._close)

    @staticmethod
    def _interactive_descendants(widget: tk.Misc) -> list[tk.Misc]:
        interactive: list[tk.Misc] = []
        interactive_types = (
            tk.Button,
            tk.Checkbutton,
            tk.Entry,
            tk.Listbox,
            tk.Radiobutton,
            tk.Spinbox,
            tk.Text,
        )
        for child in widget.winfo_children():
            if isinstance(child, interactive_types):
                interactive.append(child)
            interactive.extend(AuthWindow._interactive_descendants(child))
        return interactive

    def _guard_close_during_update(self) -> None:
        message = (
            "更新正在安装，请等待软件自动重启。"
            if self._update_install_started
            else "正在更新，请等待完成。"
        )
        self._set_status(message, error=True)
        messagebox.showwarning("正在更新", message, parent=self)

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        if self._update_in_progress:
            return
        state = "disabled" if busy else "normal"
        self.login_button.configure(state=state)
        self._update_register_send_state()
        self.register_verify_button.configure(
            state="normal" if not busy and self.register_code["state"] != "disabled" else "disabled"
        )
        self.register_link_button.configure(
            state="normal" if not busy and self.register_code["state"] != "disabled" else "disabled"
        )
        self.forgot_send_button.configure(state=state)
        self.forgot_reset_button.configure(state=state)
        self.license_redeem_button.configure(state=state)
        if busy:
            self._set_status("正在连接账号服务器…")

    def _email_is_valid(self) -> bool:
        return bool(EMAIL_PATTERN.fullmatch(self.register_email.get().strip()))

    def _update_register_send_state(self) -> None:
        if not hasattr(self, "register_send_button"):
            return
        if self._update_in_progress:
            self.register_send_button.configure(
                state="disabled",
                bg=COLORS["muted"],
                activebackground=COLORS["muted"],
            )
            return
        if self._busy or self._register_countdown > 0:
            self.register_send_button.configure(
                state="disabled",
                bg=COLORS["muted"],
                activebackground=COLORS["muted"],
            )
            if self._register_countdown > 0:
                self.register_send_button.configure(text=f"重新发送 ({self._register_countdown}s)")
            return
        valid = self._email_is_valid()
        self.register_send_button.configure(
            state="normal",
            text="发送邮箱验证码",
            bg=COLORS["accent"] if valid else COLORS["muted"],
            activebackground=(COLORS["accent_hover"] if valid else COLORS["muted"]),
        )

    def _start_register_countdown(self) -> None:
        self._register_countdown = 60
        self._update_register_send_state()

        def tick() -> None:
            if self._register_countdown <= 0:
                self._update_register_send_state()
                return
            self._register_countdown -= 1
            self._update_register_send_state()
            if self._register_countdown > 0:
                self._schedule(1000, tick)

        self._schedule(1000, tick)

    def _run_async(
        self,
        work: Callable[[], Any],
        done: Callable[[Any], None],
        failed: Callable[[Exception], None] | None = None,
    ) -> None:
        if self._busy:
            return
        self._set_busy(True)

        def worker() -> None:
            try:
                result = work()
            except Exception as exc:
                self._queue.put(
                    (failed or self._operation_failed, (exc,))
                )
                self._queue.put((self._set_busy, (False,)))
            else:
                self._queue.put((self._set_busy, (False,)))
                self._queue.put((done, (result,)))

        threading.Thread(
            target=worker,
            daemon=True,
            name="langlang-auth",
        ).start()

    def _drain_queue(self) -> None:
        try:
            while True:
                callback, args = self._queue.get_nowait()
                callback(*args)
        except queue.Empty:
            pass
        except tk.TclError:
            return
        try:
            if self.winfo_exists():
                self._schedule(70, self._drain_queue)
        except tk.TclError:
            return

    def _operation_failed(self, error: Exception) -> None:
        if isinstance(error, AuthError):
            message = str(error)
        else:
            message = f"操作失败：{error}"
        log_event(
            "auth.operation_failed",
            error=message,
        )
        self._set_status(
            message + self._log_directory_hint(),
            error=True,
        )

    @staticmethod
    def _log_directory_hint() -> str:
        manager = get_runtime_log_manager()
        if manager is None:
            return ""
        return f"\n运行日志：{manager.day_dir}"

    def _check_for_updates(self, manual: bool = True) -> None:
        if self._update_in_progress or self._update_worker_active:
            if manual:
                self._set_status("正在更新，请等待完成。", error=True)
            return
        if self._update_check_running:
            if manual:
                self._set_status("正在检查更新，请稍候。")
            return
        self._update_check_running = True
        log_event(
            "update.login.check_started",
            manual=manual,
        )
        if manual:
            self._set_status("正在检查更新…")

        def worker() -> None:
            try:
                info = check_for_update(
                    "",
                    self.paths.resource_dir,
                )
            except Exception as exc:
                self._queue.put(
                    (self._update_check_failed, (str(exc), manual))
                )
            else:
                self._queue.put(
                    (self._update_checked, (info, manual))
                )

        threading.Thread(
            target=worker,
            daemon=True,
            name="langlang-login-update-check",
        ).start()

    def _update_checked(self, info: Any, manual: bool) -> None:
        self._update_check_running = False
        if not info.available:
            self._available_update = None
            self._hide_update_badge()
            self._close_update_details()
            log_event(
                "update.login.no_update",
                manual=manual,
                current_version=info.current_version,
            )
            if manual:
                self._set_status("当前已经是最新版本。")
            return
        if not info.download_url:
            self._available_update = None
            self._hide_update_badge()
            self._close_update_details()
            log_event(
                "update.login.missing_download_url",
                manual=manual,
                latest_version=info.latest_version,
            )
            if manual:
                self._set_status("更新清单没有下载地址。", error=True)
            return
        self._available_update = info
        self._show_update_badge(info)
        log_event(
            "update.login.available",
            manual=manual,
            current_version=info.current_version,
            latest_version=info.latest_version,
        )
        if manual:
            self._set_status(
                f"发现新版本 v{info.latest_version}，请点击左上角更新提示查看。"
            )

    def _show_update_badge(self, info: Any) -> None:
        button = getattr(self, "update_badge", None)
        if button is None:
            return
        button.configure(
            text=(
                f"发现新版本 v{info.latest_version}\n"
                f"当前 v{info.current_version} · 点击查看"
            )
        )
        try:
            button.place(relx=0.5, y=108, anchor="n", width=330)
            button.lift()
        except tk.TclError:
            pass

    def _hide_update_badge(self) -> None:
        button = getattr(self, "update_badge", None)
        if button is None:
            return
        try:
            button.place_forget()
        except tk.TclError:
            pass

    def _show_update_details(self) -> None:
        if self._update_in_progress:
            self._set_status("正在更新，请等待完成。", error=True)
            return
        info = self._available_update
        if info is None or not getattr(info, "available", False):
            self._hide_update_badge()
            return
        dialog = self._update_details_dialog
        if dialog is not None and dialog.winfo_exists():
            dialog.lift()
            dialog.focus_force()
            return
        log_event(
            "update.login.details_opened",
            current_version=info.current_version,
            latest_version=info.latest_version,
        )
        self._update_details_dialog = UpdateNotesDialog(
            self,
            info,
            on_update=lambda: self._accept_update_details(info),
            on_close=self._clear_update_details,
        )

    def _accept_update_details(self, info: Any) -> None:
        log_event(
            "update.login.accepted",
            current_version=info.current_version,
            latest_version=info.latest_version,
        )
        self._download_login_update(info)

    def _clear_update_details(self) -> None:
        if (
            self._update_details_dialog is not None
            and not self._update_details_dialog.winfo_exists()
        ):
            self._update_details_dialog = None

    def _close_update_details(self) -> None:
        dialog = self._update_details_dialog
        self._update_details_dialog = None
        if dialog is not None and dialog.winfo_exists():
            dialog.close()

    def _update_check_failed(self, error: str, manual: bool) -> None:
        self._update_check_running = False
        log_event(
            "update.login.check_failed",
            manual=manual,
            error=error,
        )
        if manual:
            self._set_status(
                f"检查更新失败：{error}{self._log_directory_hint()}",
                error=True,
            )

    def _download_login_update(self, info: Any) -> None:
        if self._update_in_progress or self._update_worker_active:
            self._set_status("更新任务已经开始，请等待完成。", error=True)
            return
        self._update_worker_active = True
        self._update_install_started = False
        self._set_update_lock(True)
        self._create_login_update_dialog(info)
        self._set_status(f"正在更新到 {info.latest_version}…")
        last_progress = {"at": 0.0, "message": ""}

        def progress(message: str, current: int, total: int) -> None:
            now = time.monotonic()
            if message != last_progress["message"]:
                log_event(
                    "update.login.stage",
                    latest_version=info.latest_version,
                    stage=message,
                    current=current,
                    total=total,
                )
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
            self._queue.put(
                (
                    self._login_update_progress,
                    (message, current, total),
                )
            )

        def worker() -> None:
            try:
                staged = download_and_stage_update(
                    info,
                    self.paths.project_dir,
                    progress=progress,
                )
            except Exception as exc:
                self._queue.put(
                    (
                        self._login_update_failed,
                        (str(exc),),
                    )
                )
            else:
                self._queue.put(
                    (self._login_update_ready, (staged,))
                )

        threading.Thread(
            target=worker,
            daemon=True,
            name="langlang-login-update-download",
        ).start()

    def _create_login_update_dialog(self, info: Any) -> None:
        if self._login_update_dialog is not None:
            try:
                if self._login_update_dialog.winfo_exists():
                    return
            except tk.TclError:
                pass
        dialog = tk.Toplevel(self)
        dialog.title("正在更新（不可关闭）")
        dialog.transient(self)
        dialog.resizable(False, False)
        dialog.configure(bg=COLORS["panel"])
        dialog.protocol("WM_DELETE_WINDOW", self._guard_close_during_update)
        dialog.attributes("-topmost", True)
        panel = UpdateProgressPanel(
            dialog,
            current_version=str(info.current_version),
            latest_version=str(info.latest_version),
            colors=COLORS,
        )
        panel.pack(fill="both", expand=True)
        dialog.update_idletasks()
        width = max(540, dialog.winfo_reqwidth())
        height = max(250, dialog.winfo_reqheight())
        x = self.winfo_rootx() + max(0, (self.winfo_width() - width) // 2)
        y = self.winfo_rooty() + max(0, (self.winfo_height() - height) // 2)
        dialog.geometry(f"{width}x{height}+{x}+{y}")
        dialog.grab_set()
        dialog.lift()
        self._login_update_dialog = dialog
        self._login_update_panel = panel

    def _login_update_progress(
        self,
        message: str,
        current: int,
        total: int,
    ) -> None:
        panel = self._login_update_panel
        if panel is None:
            return
        try:
            if panel.winfo_exists():
                panel.set_progress(message, current, total)
        except tk.TclError:
            return

    def _login_update_failed(self, error: str) -> None:
        self._update_worker_active = False
        self._update_install_started = False
        log_event(
            "update.login.failed",
            error=error,
        )
        panel = self._login_update_panel
        if panel is not None:
            try:
                if panel.winfo_exists():
                    panel.mark_error(error)
            except tk.TclError:
                pass
        self._destroy_login_update_dialog()
        self._set_update_lock(False)
        self._set_status(
            f"自动更新失败：{error}{self._log_directory_hint()}",
            error=True,
        )
        messagebox.showerror("自动更新失败", error, parent=self)

    def _login_update_ready(self, staged: Any) -> None:
        self._update_worker_active = False
        log_event(
            "update.login.ready",
            latest_version=staged.version,
        )
        panel = self._login_update_panel
        if panel is not None:
            try:
                if panel.winfo_exists():
                    panel.mark_installing()
            except tk.TclError:
                pass
        try:
            launch_update_installer(
                staged,
                self.paths.project_dir,
            )
        except Exception as exc:
            self._login_update_failed(str(exc))
            return
        self._update_install_started = True
        log_event(
            "update.login.install_started",
            latest_version=staged.version,
        )
        self._set_status(
            f"{staged.version} 更新包已通过校验，正在自动安装并重启。"
        )
        if self._login_update_dialog is not None:
            try:
                self._login_update_dialog.title("正在安装更新（不可关闭）")
            except tk.TclError:
                pass
        self._schedule(1200, self._exit_after_update)

    def _destroy_login_update_dialog(self) -> None:
        dialog = self._login_update_dialog
        self._login_update_dialog = None
        self._login_update_panel = None
        if dialog is None:
            return
        try:
            if dialog.winfo_exists():
                try:
                    dialog.grab_release()
                except tk.TclError:
                    pass
                dialog.destroy()
        except tk.TclError:
            pass

    def _exit_after_update(self) -> None:
        log_event("update.login.restarting")
        self.result = None
        self._cancel_scheduled()
        try:
            self.destroy()
        except tk.TclError:
            pass

    def _load_saved_accounts(self) -> None:
        for child in self._saved_box.winfo_children():
            child.destroy()
        self._saved_accounts = self.service.store.list_accounts()
        if not self._saved_accounts:
            return
        tk.Label(
            self._saved_box,
            text="最近账户",
            bg=COLORS["panel"],
            fg=COLORS["muted"],
            font=("Microsoft YaHei UI", 8),
        ).pack(anchor="w")
        row = tk.Frame(self._saved_box, bg=COLORS["panel"])
        row.pack(fill="x", pady=(5, 0))
        for account in self._saved_accounts[:3]:
            tk.Button(
                row,
                text=f"{account.username}",
                command=lambda item=account: self._choose_saved(item),
                bg=COLORS["panel_soft"],
                fg=COLORS["text"],
                activebackground="#eae4d8",
                relief="flat",
                bd=0,
                padx=10,
                pady=6,
                font=("Microsoft YaHei UI", 8),
            ).pack(side="left", padx=(0, 6))
        if self._saved_accounts:
            self._apply_saved_account(self._saved_accounts[0])

    def _apply_saved_account(self, account: SavedAccount) -> None:
        self.login_account.delete(0, "end")
        self.login_account.insert(0, account.username)
        self.login_password.delete(0, "end")
        if account.password:
            self.login_password.insert(0, account.password)
            self.remember_var.set(True)
            self.login_button.focus_set()
        else:
            self.login_password.focus_set()

    def _choose_saved(self, account: SavedAccount) -> None:
        self._apply_saved_account(account)
        self._set_status(
            f"已选择 {account.username}，已保存密码可直接登录。"
            if account.password
            else f"已选择 {account.username}；输入密码登录，或留空使用加密登录状态。"
        )

    def _login(self) -> None:
        if self._update_in_progress:
            self._set_status("正在更新，请等待完成。", error=True)
            return
        username = self.login_account.get().strip()
        password = self.login_password.get()
        if not username:
            self._set_status("请输入账户名。", error=True)
            return
        saved = self.service.store.find(username)
        if not password and saved is None:
            self._set_status("请输入密码。", error=True)
            return
        remember = bool(self.remember_var.get())
        log_event(
            "auth.login_started",
            account=username,
        )

        def work() -> AuthContext:
            if password:
                return self.service.login(username, password, remember)
            return self.service.restore_account(saved)

        self._run_async(work, self._authorized)

    def _change_password(self) -> None:
        dialog = tk.Toplevel(self)
        dialog.title("修改主账号密码")
        dialog.geometry("520x430")
        dialog.transient(self)
        dialog.resizable(False, False)
        dialog.configure(bg=COLORS["panel"])
        body = tk.Frame(dialog, bg=COLORS["panel"], padx=26, pady=22)
        body.pack(fill="both", expand=True)
        tk.Label(
            body,
            text="修改主账号密码",
            bg=COLORS["panel"],
            fg=COLORS["text"],
            font=("Microsoft YaHei UI", 17, "bold"),
        ).pack(anchor="w")
        tk.Label(
            body,
            text="修改后会同步重新加密云端店铺保险箱。",
            bg=COLORS["panel"],
            fg=COLORS["muted"],
            font=("Microsoft YaHei UI", 9),
        ).pack(anchor="w", pady=(5, 14))
        username_entry = self._field(body, "账户名")
        username_entry.insert(0, self.login_account.get().strip())
        current_entry = self._field(body, "当前密码", show="●")
        new_entry = self._field(body, "新密码", show="●")
        confirm_entry = self._field(body, "确认新密码", show="●")
        status = tk.Label(
            body,
            text="",
            bg=COLORS["panel"],
            fg=COLORS["danger"],
            font=("Microsoft YaHei UI", 8),
            anchor="w",
        )
        status.pack(fill="x", pady=(0, 8))

        def submit() -> None:
            username = username_entry.get().strip()
            current = current_entry.get()
            new_password = new_entry.get()
            confirm = confirm_entry.get()
            if not username:
                status.configure(text="请输入账户名。")
                return
            if len(new_password) < 8:
                status.configure(text="新密码至少需要 8 位。")
                return
            if new_password != confirm:
                status.configure(text="两次输入的新密码不一致。")
                return
            submit_button.configure(state="disabled")
            status.configure(text="正在修改密码…", fg=COLORS["muted"])

            def done(_result: Any) -> None:
                dialog.destroy()
                self.login_account.delete(0, "end")
                self.login_account.insert(0, username)
                self.login_password.delete(0, "end")
                self._set_status("密码已修改，请使用新密码登录。")
                messagebox.showinfo(
                    "密码已修改",
                    "主账号密码已更新，云端店铺保险箱也已重新加密。",
                    parent=self,
                )

            def failed(exc: Exception) -> None:
                submit_button.configure(state="normal")
                status.configure(
                    text=str(exc),
                    fg=COLORS["danger"],
                )

            self._run_async(
                lambda: self.service.change_password(
                    username,
                    current,
                    new_password,
                ),
                done,
                failed,
            )

        submit_button = self._button(
            body,
            "确认修改",
            submit,
            primary=True,
        )
        submit_button.pack(fill="x")
        dialog.bind("<Escape>", lambda _event: dialog.destroy())
        dialog.grab_set()

    def _authorized(self, context: AuthContext) -> None:
        if self._update_in_progress:
            return
        self._context = context
        log_event(
            "auth.login_succeeded",
            account=context.username,
            account_id=context.account_id,
            license_active=bool(context.license.active),
        )
        if license_allows_entry(context.license):
            self.result = context
            self._cancel_scheduled()
            self.destroy()
            return
        self.license_account.configure(text=context.username)
        license_info = context.license
        if license_info.permanent:
            text = "永久授权"
        elif license_info.active:
            text = f"剩余约 {license_info.remaining_days} 天"
        else:
            text = license_info.status_text or "未激活"
        self.license_status.configure(
            text=text,
            fg=COLORS["success"] if license_info.active else COLORS["danger"],
        )
        self.license_enter_button.configure(state="disabled")
        self.license_enter_button.pack_forget()
        self._show_screen("license")
        if context.offline:
            self._set_status("当前网络不可用，正在使用最近一次联网验证后的 1 小时离线宽限。")
        else:
            self._set_status(
                "授权有效，可直接进入；也可以输入卡密继续延长。"
                if license_info.active
                else "当前没有有效授权，请输入卡密激活。"
            )

    def _begin_registration(self) -> None:
        email = self.register_email.get().strip()
        if not email:
            self._set_status("请输入邮箱。", error=True)
            messagebox.showwarning(
                "请输入邮箱",
                "请输入要绑定的邮箱地址。",
                parent=self,
            )
            self.register_email.focus_set()
            return
        if not EMAIL_PATTERN.fullmatch(email):
            self._set_status("邮箱格式不正确。", error=True)
            messagebox.showwarning(
                "邮箱格式不正确",
                "请检查邮箱地址格式，例如 name@example.com。",
                parent=self,
            )
            self.register_email.focus_set()
            return

        def done(_result: Any) -> None:
            self._register_email = email
            self.register_code.configure(state="normal")
            self.register_verify_button.configure(state="normal")
            self._start_register_countdown()
            self._set_status("验证码已发送，请填写邮箱验证码、账户名和密码。")
            self.register_code.focus_set()

        self._run_async(
            lambda: self.service.send_registration_code(email),
            done,
        )

    def _verify_registration(self) -> None:
        code = self.register_code.get().strip()
        if not self._register_email:
            self._set_status("请先发送邮箱验证码。", error=True)
            return
        if len(code) < 6:
            self._set_status("请输入邮箱中的验证码。", error=True)
            return
        username = self.register_username.get().strip()
        password = self.register_password.get()
        confirm = self.register_confirm.get()
        if not username:
            self._set_status("请输入账户名。", error=True)
            return
        if len(password) < 8:
            self._set_status("密码至少需要 8 位。", error=True)
            return
        if password != confirm:
            self._set_status("两次输入的密码不一致。", error=True)
            return
        self._run_async(
            lambda: self.service.complete_registration_with_code(
                self._register_email,
                code,
                username,
                password,
                True,
            ),
            lambda context: (
                self.login_account.delete(0, "end"),
                self.login_account.insert(0, username),
                self._load_saved_accounts(),
                self._authorized(context),
            ),
        )

    def _verify_registration_link(self) -> None:
        if not self._register_email:
            self._set_status("请先发送注册邮件。", error=True)
            return
        password = self.register_password.get()
        if not password:
            self._set_status("请重新输入注册密码。", error=True)
            return
        username = self.register_username.get().strip()
        self._run_async(
            lambda: self.service.complete_registration_after_link(
                self._register_email,
                password,
                True,
            ),
            lambda context: (
                self.login_account.delete(0, "end"),
                self.login_account.insert(0, username),
                self._load_saved_accounts(),
                self._authorized(context),
            ),
        )

    def _begin_recovery(self) -> None:
        identifier = self.forgot_identifier.get().strip()
        if not identifier:
            self._set_status("请输入账户名或邮箱。", error=True)
            return

        def done(email: str) -> None:
            self._recovery_email = email
            self._set_status("验证码已发送至账户绑定邮箱。")

        self._run_async(
            lambda: self.service.send_recovery_code(identifier),
            done,
        )

    def _reset_password(self) -> None:
        if not self._recovery_email:
            self._set_status("请先发送找回验证码。", error=True)
            return
        code = self.forgot_code.get().strip()
        password = self.forgot_password.get()
        confirm = self.forgot_confirm.get()
        if len(code) < 6:
            self._set_status("请输入邮箱验证码。", error=True)
            return
        if len(password) < 8:
            self._set_status("新密码至少需要 8 位。", error=True)
            return
        if password != confirm:
            self._set_status("两次输入的新密码不一致。", error=True)
            return
        identifier = self.forgot_identifier.get().strip()

        def done(_result: Any) -> None:
            self.login_account.delete(0, "end")
            self.login_account.insert(0, identifier)
            self._load_saved_accounts()
            self._show_screen("login")
            self._set_status("密码已重置，请使用新密码登录。")

        self._run_async(
            lambda: self.service.reset_password(
                self._recovery_email,
                code,
                password,
            ),
            done,
        )

    def _redeem_card(self) -> None:
        if self._context is None:
            self._set_status("登录状态已失效，请重新登录。", error=True)
            return
        code = self.license_code.get().strip()
        if not code:
            self._set_status("请输入卡密。", error=True)
            return
        self._run_async(
            lambda: self.service.redeem(self._context, code),
            self._card_redeemed,
        )

    def _card_redeemed(self, context: AuthContext) -> None:
        self._context = context
        license_info = context.license
        if license_info.permanent:
            text = "永久授权"
        else:
            text = f"剩余约 {license_info.remaining_days} 天"
        self.license_status.configure(text=text, fg=COLORS["success"])
        self.license_enter_button.configure(state="normal")
        self.license_code.delete(0, "end")
        self._set_status("卡密验证成功。")
        self._finish()

    def _finish(self) -> None:
        if self._update_in_progress:
            self._set_status("正在更新，请等待完成。", error=True)
            return
        if self._context is None or not license_allows_entry(self._context.license):
            self._set_status("请先完成卡密授权。", error=True)
            return
        self.result = self._context
        self._cancel_scheduled()
        self.destroy()

    def _close(self) -> None:
        if self._update_in_progress:
            self._guard_close_during_update()
            return
        self.result = None
        log_event("auth.window_closed")
        self._cancel_scheduled()
        try:
            if not self.winfo_exists():
                return
        except tk.TclError:
            return
        self.destroy()
