from __future__ import annotations

import threading
import time
import tkinter as tk
from datetime import UTC, datetime
from unittest import mock

from weimi_manager.dongxiao.buyer_flow.domain import (
    BuyerAccount,
    BuyerAccountStatus,
    BuyerIdentityStatus,
    BuyerProvider,
    BuyerSwitchState,
)
from weimi_manager.dongxiao.buyer_flow.wechat_desktop import (
    WechatDesktopProbe,
    WechatDesktopState,
)
from weimi_manager.ui.buyer_account_wizard import (
    BuyerAccountWizard,
    wechat_state_label,
)


def _root() -> tuple[tk.Tk, tk.Frame]:
    for attempt in range(3):
        try:
            root = tk.Tk()
            break
        except tk.TclError:
            if attempt == 2:
                raise
            time.sleep(0.2)
    root.withdraw()
    parent = tk.Frame(root)
    parent.pack()
    parent.window = root
    root.run_task = _ThreadedWindow().run_task
    parent._permission_checker = lambda _permission: True
    parent._actor_id = lambda: "tester"
    return root, parent


class _ImmediateWindow:
    def run_task(
        self,
        work,
        done,
        error,
        **_kwargs,
    ) -> bool:
        try:
            done(work(lambda *_args: None))
        except Exception as exc:
            error(exc)
        return True


class _ThreadedWindow(_ImmediateWindow):
    def run_task(
        self,
        work,
        done,
        error,
        **_kwargs,
    ) -> bool:
        result = {}
        failure = {}

        def worker() -> None:
            try:
                result["value"] = work(lambda *_args: None)
            except Exception as exc:
                failure["error"] = exc

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join(timeout=5)
        assert not thread.is_alive()
        if "error" in failure:
            error(failure["error"])
        else:
            done(result.get("value"))
        return True


class _FakeService:
    def __init__(self) -> None:
        self.open_thread_name = ""

    @staticmethod
    def inspect_wechat_client(**_kwargs) -> WechatDesktopProbe:
        return WechatDesktopProbe(
            state=WechatDesktopState.IDENTITY_UNCERTAIN,
            installed=True,
            running=True,
            checked_at=datetime.now(UTC),
            executable_name="Weixin.exe",
            client_version="4.2.0.53",
            window_title="微信",
            session_id="weixin.exe:100:200",
            instructions="请核对当前微信账号。",
        )

    def open_official_wechat_client(self) -> bool:
        self.open_thread_name = threading.current_thread().name
        return True

    @staticmethod
    def register_current_official_account(**kwargs):
        now = datetime.now(UTC)
        return (
            BuyerAccount(
                buyer_account_id="wizard-account",
                display_name=kwargs["display_name"],
                account_ref_hash="a" * 64,
                provider=BuyerProvider.MANUAL_HANDOFF,
                identity_status=BuyerIdentityStatus.USER_ASSERTED,
                status=BuyerAccountStatus.ACTIVE,
                authorization_at=now,
                last_verified_at=now,
                created_at=now,
                updated_at=now,
                is_current=True,
                switch_state=BuyerSwitchState.CONFIRMED,
                confirmed_client_session="weixin.exe:100:200",
                wechat_nickname=kwargs["wechat_nickname"],
                identity_label=kwargs["identity_label"],
            ),
            True,
        )


def test_wechat_states_have_clear_chinese_user_labels() -> None:
    assert (
        wechat_state_label(WechatDesktopState.IDENTITY_UNCERTAIN)
        == "已检测到微信客户端，但尚未确认当前登录账号"
    )
    assert (
        wechat_state_label("login_required")
        == "微信等待用户扫码登录"
    )
    assert wechat_state_label("unexpected") == "微信状态未知"


def test_wizard_constructs_with_its_own_title_and_single_window_flow() -> None:
    root, parent = _root()
    try:
        wizard = BuyerAccountWizard(
            parent,
            _FakeService(),
            on_completed=lambda _account: None,
        )
        root.update_idletasks()
        root.update()
        assert wizard.title() == "添加微信账号"
        assert len(wizard.winfo_children()) >= 5
        wizard.destroy()
    finally:
        root.destroy()


def test_wizard_checks_wechat_and_registers_current_session() -> None:
    root, parent = _root()
    completed = []
    try:
        wizard = BuyerAccountWizard(
            parent,
            _FakeService(),
            on_completed=completed.append,
        )
        root.update_idletasks()
        root.update()
        wizard._inspect_wechat()
        wizard.display_name_var.set("向导测试买家")
        wizard.nickname_var.set("向导测试微信")
        wizard.identity_label_var.set("用户核对")
        wizard.remark_var.set("本地测试")
        with (
            mock.patch(
                "weimi_manager.ui.buyer_account_wizard.messagebox.askyesno",
                return_value=True,
            ),
            mock.patch(
                "weimi_manager.ui.buyer_account_wizard.messagebox.showinfo"
            ),
        ):
            wizard._submit_binding()
        assert completed
        assert completed[0].buyer_account_id == "wizard-account"
        assert completed[0].confirmed_client_session == "weixin.exe:100:200"
    finally:
        root.destroy()


def test_wizard_opens_wechat_outside_the_tk_main_thread() -> None:
    root, parent = _root()
    service = _FakeService()
    try:
        wizard = BuyerAccountWizard(
            parent,
            service,
            on_completed=lambda _account: None,
        )
        root.update_idletasks()
        root.update()
        wizard._open_wechat()
        assert service.open_thread_name
        assert service.open_thread_name != threading.main_thread().name
        assert "已请求打开官方微信" in wizard.status_var.get()
    finally:
        root.destroy()
