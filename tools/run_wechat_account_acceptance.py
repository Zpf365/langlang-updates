"""Run the isolated Windows account-wizard acceptance flow."""

# ruff: noqa: E402, I001

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from PIL import ImageGrab

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weimi_manager.config import AppPaths
from weimi_manager.dongxiao.buyer_flow.repository import BuyerFlowRepository
from weimi_manager.dongxiao.buyer_flow.wechat_desktop import (
    WechatDesktopClient,
    WechatDesktopProbe,
    WechatDesktopState,
)
from weimi_manager.ui.app import MainWindow


OUTPUT = ROOT / "artifacts" / "release_1_8_52" / "ui-acceptance"


class AcceptanceWechatClient:
    def __init__(self) -> None:
        self.session_id = "acceptance-session-a"
        self.nickname = "LOCAL-UI-TEST-A"

    def probe(self) -> WechatDesktopProbe:
        return WechatDesktopProbe(
            state=WechatDesktopState.LOGGED_IN,
            installed=True,
            running=True,
            checked_at=datetime.now(UTC),
            executable_name="Weixin.exe",
            client_version="4.2.0.53",
            window_title="微信",
            visible_identity=self.nickname,
            identity_confirmed=False,
            session_id=self.session_id,
            instructions="隔离 UI 测试客户端，仅用于验证向导。",
        )

    @staticmethod
    def switch_account_instructions() -> str:
        return "隔离测试：模拟在官方微信中退出并登录目标账号。"

    @staticmethod
    def open_official_url(_url: str) -> bool:
        return True


def _owner_context() -> SimpleNamespace:
    tokens = SimpleNamespace(
        access_token="",
        refresh_token="",
        account_id="ui-owner",
    )
    license_info = SimpleNamespace(
        status_text="隔离 UI 验收授权",
        remaining_days=999,
    )
    return SimpleNamespace(
        username="ui-owner",
        account_id="ui-owner",
        device_hash="ui-test-device",
        member_role="owner",
        permissions={},
        organization_id="ui-org",
        vault_key="",
        offline=True,
        license=license_info,
        tokens=tokens,
    )


def _pump(root, predicate, timeout: float = 8.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        root.update()
        if predicate():
            return True
        time.sleep(0.02)
    return False


def _capture(window: MainWindow, target: Path) -> None:
    time.sleep(0.25)
    window.update_idletasks()
    window.update()
    x = window.winfo_rootx()
    y = window.winfo_rooty()
    width = window.winfo_width()
    height = window.winfo_height()
    ImageGrab.grab(
        bbox=(x, y, x + width, y + height),
        all_screens=True,
    ).save(target)


def _capture_widget(widget, target: Path) -> None:
    widget.update_idletasks()
    widget.update()
    x = widget.winfo_rootx()
    y = widget.winfo_rooty()
    width = widget.winfo_width()
    height = widget.winfo_height()
    ImageGrab.grab(
        bbox=(x, y, x + width, y + height),
        all_screens=True,
    ).save(target)


def _close_window(window) -> None:
    """Use the production close path so runtime and diagnostics stop cleanly."""

    try:
        for callback_id in window.tk.splitlist(
            window.tk.call("after", "info")
        ):
            window.tk.call("after", "cancel", callback_id)
    except Exception:
        pass
    close = getattr(window, "_on_close", None)
    if callable(close):
        close()
    elif window.winfo_exists():
        window.destroy()


def _open_accounts(window: MainWindow):
    window.show_page("sales_related")
    window.update()
    page = window.sales_related_page
    page.section_buttons["accounts"].invoke()
    window.update()
    return page


def _run_wizard(
    root,
    page,
    *,
    display_name: str,
    nickname: str,
    identity_label: str,
    remark: str,
    screenshot_path: Path | None = None,
) -> str:
    page._add_official_buyer_account()
    wizard = page._account_wizard
    if wizard is None:
        raise RuntimeError("账号向导未创建")
    root.update()
    _pump(root, lambda: not wizard._busy)
    wizard._inspect_wechat()
    if not _pump(
        root,
        lambda: wizard._probe is not None and not wizard._busy,
    ):
        raise TimeoutError("账号向导检查微信超时")
    wizard.display_name_var.set(display_name)
    wizard.nickname_var.set(nickname)
    wizard.identity_label_var.set(identity_label)
    wizard.remark_var.set(remark)
    if screenshot_path is not None:
        _capture_widget(wizard, screenshot_path)
    with (
        mock.patch(
            "weimi_manager.ui.buyer_account_wizard.messagebox.askyesno",
            return_value=True,
        ),
        mock.patch(
            "weimi_manager.ui.buyer_account_wizard.messagebox.showinfo"
        ),
    ):
        print(
            "[acceptance] submit",
            {
                "busy": wizard._busy,
                "probe": wizard._probe.state.value if wizard._probe else "",
                "display_name": wizard.display_name_var.get(),
            },
            flush=True,
        )
        wizard._submit_binding()
        print(
            "[acceptance] submitted",
            {
                "busy": wizard._busy,
                "status": wizard.status_var.get(),
            },
            flush=True,
        )
        if not _pump(
            root,
            lambda: page._account_wizard is None
            or not page._account_wizard.winfo_exists(),
            timeout=12.0,
        ):
            status = (
                wizard.status_var.get()
                if wizard.winfo_exists()
                else ""
            )
            detail = (
                wizard.detail_var.get()
                if wizard.winfo_exists()
                else ""
            )
            raise TimeoutError(
                f"账号向导绑定超时；状态={status}；详情={detail}"
            )
    page._load_buyer_accounts()
    _pump(root, lambda: bool(page._buyer_accounts))
    return display_name


def run() -> dict[str, object]:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    real_probe = WechatDesktopClient(
        screenshot_dir=OUTPUT / "wechat-real-probe"
    ).probe()
    real_payload = {
        "state": real_probe.state.value,
        "installed": real_probe.installed,
        "running": real_probe.running,
        "executable_name": real_probe.executable_name,
        "client_version": real_probe.client_version,
        "window_title": real_probe.window_title,
        "visible_identity": real_probe.visible_identity,
        "session_id": real_probe.session_id,
        "identity_confirmed": real_probe.identity_confirmed,
        "error": real_probe.error,
    }
    (OUTPUT / "real-wechat-probe.json").write_text(
        json.dumps(real_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    with tempfile.TemporaryDirectory(
        prefix="langlang-account-wizard-",
        dir=OUTPUT,
    ) as temporary:
        data_dir = Path(temporary) / "data"
        os.environ["WEIMI_DATA_DIR"] = str(data_dir)
        os.environ["LANGLANG_DISABLE_AUTO_UPDATE"] = "1"
        os.environ["LANGLANG_DISABLE_AUTO_BACKUP"] = "1"
        window = MainWindow(
            AppPaths.discover(),
            auth_context=_owner_context(),
        )
        window.geometry("980x720+40+30")
        window.update()
        page = _open_accounts(window)
        page._add_official_buyer_account()
        real_wizard = page._account_wizard
        if real_wizard is None:
            raise RuntimeError("真实微信状态向导未创建")
        window.update()
        real_wizard._inspect_wechat()
        if not _pump(
            window,
            lambda: real_wizard._probe is not None
            and not real_wizard._busy,
        ):
            raise TimeoutError("真实微信状态检查超时")
        _capture_widget(
            real_wizard,
            OUTPUT / "wizard-real-client.png",
        )
        real_wizard_status = real_wizard.status_var.get()
        real_wizard.destroy()
        window.update()
        client = AcceptanceWechatClient()
        page.buyer_flow_service.wechat_client = client
        _run_wizard(
            window,
            page,
            display_name="LOCAL-UI-TEST-A",
            nickname="LOCAL-UI-TEST-NOT-REAL-A",
            identity_label="UI-TEST-IDENTITY-A",
            remark="隔离测试账号，可删除",
            screenshot_path=OUTPUT / "wizard-step-a.png",
        )
        _capture(window, OUTPUT / "wizard-account-a-bound.png")
        repository = BuyerFlowRepository(AppPaths.discover().database)
        first = repository.current_account()
        if first is None:
            raise RuntimeError("向导未创建当前账号")
        client.session_id = "acceptance-session-b"
        client.nickname = "LOCAL-UI-TEST-B"
        _run_wizard(
            window,
            page,
            display_name="LOCAL-UI-TEST-B",
            nickname="LOCAL-UI-TEST-NOT-REAL-B",
            identity_label="UI-TEST-IDENTITY-B",
            remark="第二隔离测试账号",
            screenshot_path=OUTPUT / "wizard-step-b.png",
        )
        _pump(window, lambda: len(page._buyer_accounts) >= 2)
        _capture(window, OUTPUT / "wizard-account-b-bound.png")
        accounts = repository.list_accounts()
        switch_events = repository.list_account_switch_events(limit=20)
        first_after = next(
            item
            for item in accounts
            if item.buyer_account_id == first.buyer_account_id
        )
        current_after = repository.current_account()
        _close_window(window)

        reopened = MainWindow(
            AppPaths.discover(),
            auth_context=_owner_context(),
        )
        reopened.geometry("980x720+40+30")
        reopened.update()
        reopened_page = _open_accounts(reopened)
        _pump(
            reopened,
            lambda: bool(reopened_page._buyer_accounts),
        )
        _capture(reopened, OUTPUT / "wizard-after-restart.png")
        persisted = BuyerFlowRepository(
            AppPaths.discover().database
        ).current_account()
        _close_window(reopened)
        if persisted is None:
            raise RuntimeError("重启后当前买家丢失")
        result = {
            "real_wechat_probe": real_payload,
            "real_wizard_status": real_wizard_status,
            "test_mode": "隔离 Fake 客户端，仅验证 UI 和数据流程",
            "accounts": [
                {
                    "buyer_account_id": item.buyer_account_id,
                    "display_name": item.display_name,
                    "wechat_nickname": item.wechat_nickname,
                    "identity_status": item.identity_status.value,
                    "switch_state": item.switch_state.value,
                    "confirmed_client_session": item.confirmed_client_session,
                    "is_current": item.is_current,
                }
                for item in accounts
            ],
            "first_identity_invalidated": (
                first_after.switch_state.value == "login_invalid"
                and first_after.confirmed_client_session == ""
            ),
            "current_after_second": (
                current_after.display_name if current_after else ""
            ),
            "switch_event_count": len(switch_events),
            "restart_current": persisted.display_name,
            "screenshots": [
                "wizard-step-a.png",
                "wizard-real-client.png",
                "wizard-account-a-bound.png",
                "wizard-step-b.png",
                "wizard-account-b-bound.png",
                "wizard-after-restart.png",
            ],
        }
        (OUTPUT / "account-wizard-acceptance.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return result


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2))
