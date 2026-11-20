from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any

from ..dongxiao.buyer_flow.wechat_desktop import (
    WechatDesktopProbe,
    WechatDesktopState,
)
from .theme import MUTED, button

STATE_LABELS = {
    WechatDesktopState.NOT_INSTALLED: "未检测到 Windows 官方微信",
    WechatDesktopState.NOT_RUNNING: "Windows 官方微信未运行",
    WechatDesktopState.LOGIN_REQUIRED: "微信等待用户扫码登录",
    WechatDesktopState.IDENTITY_UNCERTAIN: (
        "已检测到微信客户端，但尚未确认当前登录账号"
    ),
    WechatDesktopState.LOGGED_IN: "微信客户端已运行，请核对当前账号",
    WechatDesktopState.UNSUPPORTED_VERSION: "当前微信客户端版本不支持所需页面",
    WechatDesktopState.ERROR: "微信客户端检测异常",
}

STATE_RECOVERY_HINTS = {
    WechatDesktopState.NOT_INSTALLED: "请先通过官方渠道安装 Windows 微信。",
    WechatDesktopState.NOT_RUNNING: "点击“打开官方微信”，由本人完成登录。",
    WechatDesktopState.LOGIN_REQUIRED: "请在官方微信中由本人完成扫码登录。",
    WechatDesktopState.IDENTITY_UNCERTAIN: (
        "无法从窗口可靠读取身份；请核对当前账号后填写昵称和身份标识。"
    ),
    WechatDesktopState.LOGGED_IN: "请核对官方微信当前账号后完成登记。",
    WechatDesktopState.UNSUPPORTED_VERSION: "请升级官方微信后重新检查。",
    WechatDesktopState.ERROR: "请查看诊断详情并按提示处理后重新检查。",
}


def wechat_state_label(state: WechatDesktopState | str) -> str:
    """Return the user-facing Chinese label for a WeChat probe state."""

    try:
        normalized = WechatDesktopState(state)
    except ValueError:
        return "微信状态未知"
    return STATE_LABELS.get(normalized, "微信状态未知")


class BuyerAccountWizard(tk.Toplevel):
    """One-window flow for adding and binding the current official WeChat account."""

    def __init__(
        self,
        parent,
        service,
        *,
        on_completed,
    ) -> None:
        super().__init__(parent)
        self.parent_page = parent
        self.service = service
        self.on_completed = on_completed
        self._probe: WechatDesktopProbe | None = None
        self._busy = False
        self.title("添加微信账号")
        self.geometry("760x840")
        self.minsize(680, 720)
        self.transient(parent.window)
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.columnconfigure(0, weight=1)

        ttk.Label(
            self,
            text="添加当前 Windows 微信账号",
            font=("Microsoft YaHei UI", 16, "bold"),
        ).grid(row=0, column=0, sticky="w", padx=18, pady=(14, 2))
        ttk.Label(
            self,
            text=(
                "澜琅只登记当前官方微信客户端可见的身份，不读取密码、"
                "Cookie、Token 或微信私有数据库。"
            ),
            wraplength=620,
            foreground=MUTED,
            justify="left",
        ).grid(row=1, column=0, sticky="w", padx=18, pady=(0, 10))

        status_box = ttk.LabelFrame(
            self,
            text="第 1 步：检查官方微信",
            padding=8,
        )
        status_box.grid(row=2, column=0, sticky="ew", padx=18)
        status_box.columnconfigure(0, weight=1)
        self.status_var = tk.StringVar(value="正在检查微信客户端…")
        self.status_label = ttk.Label(
            status_box,
            textvariable=self.status_var,
            wraplength=550,
            justify="left",
        )
        self.status_label.grid(row=0, column=0, columnspan=3, sticky="w")
        self.detail_var = tk.StringVar(value="")
        ttk.Label(
            status_box,
            textvariable=self.detail_var,
            foreground=MUTED,
            wraplength=550,
            justify="left",
        ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(6, 10))
        self.open_button = button(
            status_box,
            "打开官方微信",
            self._open_wechat,
        )
        self.open_button.grid(row=2, column=0, sticky="w")
        self.check_button = button(
            status_box,
            "重新检查微信",
            self._inspect_wechat,
            "Primary",
        )
        self.check_button.grid(row=2, column=1, sticky="w", padx=(8, 0))

        form = ttk.LabelFrame(
            self,
            text="第 2 步：核对并登记当前账号",
            padding=8,
        )
        form.grid(row=3, column=0, sticky="ew", padx=18, pady=(10, 0))
        form.columnconfigure(1, weight=1)
        self.display_name_var = tk.StringVar(value="当前微信买家")
        self.nickname_var = tk.StringVar()
        self.identity_label_var = tk.StringVar()
        self.remark_var = tk.StringVar()
        for row, (label, variable, hint) in enumerate(
            (
                (
                    "买家显示名",
                    self.display_name_var,
                    "澜琅内部显示，可自行填写",
                ),
                (
                    "当前微信昵称",
                    self.nickname_var,
                    "请核对官方微信窗口后填写",
                ),
                (
                    "身份标识",
                    self.identity_label_var,
                    "填写本人可核对的简短标识，不填手机号或凭据",
                ),
                (
                    "账号备注",
                    self.remark_var,
                    "可选",
                ),
            ),
            start=0,
        ):
            ttk.Label(form, text=label).grid(
                row=row * 2,
                column=0,
                sticky="w",
                pady=(4, 0),
            )
            ttk.Entry(form, textvariable=variable).grid(
                row=row * 2,
                column=1,
                sticky="ew",
                padx=(12, 0),
                pady=(4, 0),
            )
            ttk.Label(
                form,
                text=hint,
                foreground=MUTED,
            ).grid(
                row=row * 2 + 1,
                column=1,
                sticky="w",
                padx=(12, 0),
            )

        ttk.Label(
            self,
            text=(
                "身份确认来源将标记为“用户人工确认”，"
                "不会冒充微信官方接口认证。"
            ),
            foreground=MUTED,
            wraplength=620,
            justify="left",
        ).grid(row=4, column=0, sticky="w", padx=18, pady=(8, 6))
        actions = ttk.Frame(self)
        actions.grid(row=5, column=0, sticky="e", padx=18, pady=(2, 10))
        self.bind_button = button(
            actions,
            "确认并绑定当前账号",
            self._submit_binding,
            "Primary",
        )
        self.bind_button.pack(side="left")
        button(actions, "取消", self._cancel).pack(
            side="left",
            padx=(8, 0),
        )
        self.after(50, self._inspect_wechat)

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        state = "disabled" if busy else "normal"
        self.check_button.configure(state=state)
        self.open_button.configure(state=state)
        self.bind_button.configure(state=state)

    def _inspect_wechat(self) -> None:
        if self._busy:
            return
        self._set_busy(True)
        self.status_var.set("正在检查 Windows 官方微信…")
        self.detail_var.set("")

        def done(probe: WechatDesktopProbe) -> None:
            self._probe = probe
            self._set_busy(False)
            self.status_var.set(wechat_state_label(probe.state))
            details = []
            if probe.client_version:
                details.append(f"客户端版本 {probe.client_version}")
            if probe.visible_identity:
                details.append(f"窗口可见身份 {probe.visible_identity}")
            if probe.session_id:
                details.append(f"运行会话 {probe.session_id[-28:]}")
            details.append(
                STATE_RECOVERY_HINTS.get(
                    probe.state,
                    probe.instructions,
                )
            )
            self.detail_var.set("；".join(item for item in details if item))
            if probe.visible_identity and not self.nickname_var.get().strip():
                self.nickname_var.set(probe.visible_identity)

        self.parent_page.window.run_task(
            lambda _progress: self.service.inspect_wechat_client(
                permission_checker=self.parent_page._permission_checker,
                actor_id=self.parent_page._actor_id(),
            ),
            done,
            self._inspect_failed,
            log_title="添加微信账号向导检查客户端",
            task_scope="dongxiao:add-wechat-account:inspect",
        )

    def _inspect_failed(self, error: Any) -> None:
        self._set_busy(False)
        self.status_var.set(f"微信检查失败：{error}")
        self.detail_var.set("可尝试打开官方微信后重新检查。")

    def _open_wechat(self) -> None:
        if self._busy:
            return
        self._set_busy(True)
        self.status_var.set("正在打开 Windows 官方微信…")

        def done(_result) -> None:
            self._set_busy(False)
            self.status_var.set(
                "已请求打开官方微信，请由用户本人完成扫码或登录"
            )
            self.after(800, self._inspect_wechat)

        self.parent_page.window.run_task(
            lambda _progress: self.service.open_official_wechat_client(),
            done,
            self._open_failed,
            log_title="添加微信账号向导打开官方微信",
            task_scope="dongxiao:add-wechat-account:open",
        )

    def _open_failed(self, error: Any) -> None:
        self._set_busy(False)
        messagebox.showerror(
            "打开官方微信失败",
            str(error),
            parent=self,
        )

    def _submit_binding(self) -> None:
        if self._busy:
            return
        probe = self._probe
        if probe is None:
            messagebox.showwarning(
                "添加微信账号",
                "请先检查 Windows 官方微信。",
                parent=self,
            )
            return
        if not probe.installed:
            messagebox.showwarning("添加微信账号", "请先安装官方微信。", parent=self)
            return
        if not probe.running:
            messagebox.showwarning("添加微信账号", "请先启动官方微信。", parent=self)
            return
        if probe.state is WechatDesktopState.LOGIN_REQUIRED:
            messagebox.showwarning(
                "添加微信账号",
                "官方微信尚未登录，请先由本人完成扫码登录。",
                parent=self,
            )
            return
        if probe.state in {
            WechatDesktopState.ERROR,
            WechatDesktopState.UNSUPPORTED_VERSION,
        }:
            messagebox.showerror(
                "添加微信账号",
                probe.error or probe.instructions,
                parent=self,
            )
            return
        display_name = self.display_name_var.get().strip()
        nickname = self.nickname_var.get().strip()
        identity_label = self.identity_label_var.get().strip()
        remark = self.remark_var.get().strip()
        if not display_name or not nickname or not identity_label:
            messagebox.showwarning(
                "添加微信账号",
                "请填写买家显示名、当前微信昵称和身份标识。",
                parent=self,
            )
            return
        if not messagebox.askyesno(
            "确认当前微信身份",
            (
                "请最后核对官方微信当前登录身份。\n\n"
                f"微信昵称：{nickname}\n"
                f"身份标识：{identity_label}\n\n"
                "该确认将标记为用户人工确认，不是微信官方认证。\n"
                "是否绑定为当前买家账号？"
            ),
            parent=self,
        ):
            return
        self._set_busy(True)
        self.status_var.set("正在保存账号并绑定当前微信会话…")
        self.parent_page.window.run_task(
            lambda _progress: self.service.register_current_official_account(
                display_name=display_name,
                wechat_nickname=nickname,
                identity_label=identity_label,
                remark=remark,
                permission_checker=self.parent_page._permission_checker,
                actor_id=self.parent_page._actor_id(),
            ),
            self._registered,
            self._register_failed,
            log_title="添加微信账号向导完成绑定",
            task_scope="dongxiao:add-wechat-account:register",
        )

    def _registered(self, result) -> None:
        account, created = result
        self._set_busy(False)
        self.status_var.set(
            f"已{'添加' if created else '关联'}当前微信账号并设为当前买家。"
        )
        self.on_completed(account)
        messagebox.showinfo(
            "微信账号添加完成",
            (
                f"买家显示名：{account.display_name}\n"
                f"微信昵称：{account.wechat_nickname}\n"
                f"身份标识：{account.identity_label}\n"
                "状态：已人工确认并绑定当前微信运行会话"
            ),
            parent=self,
        )
        self.destroy()

    def _register_failed(self, error: Any) -> None:
        self._set_busy(False)
        self.status_var.set(f"添加失败：{error}")
        self.detail_var.set(
            "未生成成功绑定。请重新检查微信、核对身份，"
            "或在官方微信完成登录后重试。"
        )

    def _cancel(self) -> None:
        if self._busy:
            messagebox.showinfo(
                "正在处理",
                "当前操作尚未完成，请稍候。",
                parent=self,
            )
            return
        self.destroy()
