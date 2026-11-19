from __future__ import annotations

from pathlib import Path

from weimi_manager.dongxiao.buyer_flow.wechat_desktop import (
    DesktopWindow,
    WechatDesktopClient,
    WechatDesktopState,
    WindowSnapshot,
)


class FakeBackend:
    def __init__(
        self,
        *,
        installs: tuple[Path, ...] = (),
        windows: tuple[DesktopWindow, ...] = (),
        snapshots: dict[int, WindowSnapshot] | None = None,
    ) -> None:
        self.installs = installs
        self.windows = windows
        self.snapshots = snapshots or {}
        self.captures: list[Path] = []

    def installation_paths(self) -> tuple[Path, ...]:
        return self.installs

    def running_windows(self) -> tuple[DesktopWindow, ...]:
        return self.windows

    def snapshot(self, _window: DesktopWindow) -> WindowSnapshot:
        return self.snapshots.get(_window.hwnd, WindowSnapshot())

    def capture(self, _window: DesktopWindow, target: Path) -> bool:
        target.write_bytes(b"png")
        self.captures.append(target)
        return True


def _window() -> DesktopWindow:
    return DesktopWindow(
        hwnd=123,
        process_id=456,
        title="微信",
        class_name="WeChatMainWndForPC",
        executable_name="WeChat.exe",
    )


def test_wechat_probe_reports_install_and_running_states(
    tmp_path: Path,
) -> None:
    missing = WechatDesktopClient(
        backend=FakeBackend(),
        screenshot_dir=tmp_path,
    ).probe()
    assert missing.state is WechatDesktopState.NOT_INSTALLED
    assert missing.installed is False

    stopped = WechatDesktopClient(
        backend=FakeBackend(installs=(tmp_path / "WeChat.exe",)),
        screenshot_dir=tmp_path,
    ).probe()
    assert stopped.state is WechatDesktopState.NOT_RUNNING
    assert stopped.installed is True
    assert stopped.running is False


def test_wechat_probe_requires_manual_identity_confirmation(
    tmp_path: Path,
) -> None:
    window = _window()
    backend = FakeBackend(
        installs=(tmp_path / "WeChat.exe",),
        windows=(window,),
        snapshots={
            window.hwnd: WindowSnapshot(
                main_window=True,
                visible_texts=("微信", "通讯录", "聊天"),
            )
        },
    )

    probe = WechatDesktopClient(
        backend=backend,
        screenshot_dir=tmp_path,
    ).probe()

    assert probe.state is WechatDesktopState.IDENTITY_UNCERTAIN
    assert probe.running is True
    assert probe.identity_confirmed is False
    assert "人工确认" in probe.instructions


def test_wechat_probe_detects_login_and_visible_identity(
    tmp_path: Path,
) -> None:
    login_window = _window()
    login = WechatDesktopClient(
        backend=FakeBackend(
            installs=(tmp_path / "WeChat.exe",),
            windows=(login_window,),
            snapshots={
                login_window.hwnd: WindowSnapshot(
                    login_page=True,
                    visible_texts=("进入微信", "二维码已失效"),
                )
            },
        ),
        screenshot_dir=tmp_path,
    ).probe()
    assert login.state is WechatDesktopState.LOGIN_REQUIRED

    signed_window = DesktopWindow(
        hwnd=789,
        process_id=456,
        title="微信",
        class_name="WeChatMainWndForPC",
        executable_name="Weixin.exe",
    )
    signed = WechatDesktopClient(
        backend=FakeBackend(
            installs=(tmp_path / "Weixin.exe",),
            windows=(signed_window,),
            snapshots={
                signed_window.hwnd: WindowSnapshot(
                    main_window=True,
                    visible_identity="微信买家甲",
                    visible_texts=("微信买家甲", "通讯录", "收藏"),
                )
            },
        ),
        screenshot_dir=tmp_path,
    ).probe()
    assert signed.state is WechatDesktopState.LOGGED_IN
    assert signed.visible_identity == "微信买家甲"
    assert signed.identity_confirmed is True


def test_wechat_probe_captures_unexpected_client_errors(
    tmp_path: Path,
) -> None:
    window = _window()
    backend = FakeBackend(
        installs=(tmp_path / "WeChat.exe",),
        windows=(window,),
        snapshots={
            window.hwnd: WindowSnapshot(
                main_window=False,
                error_texts=("当前微信版本不支持该页面",),
            )
        },
    )

    probe = WechatDesktopClient(
        backend=backend,
        screenshot_dir=tmp_path,
    ).probe()

    assert probe.state is WechatDesktopState.UNSUPPORTED_VERSION
    assert probe.screenshot_path.endswith(".png")
    assert backend.captures


def test_official_product_entry_only_accepts_weixin_https_links() -> None:
    validate = WechatDesktopClient.validate_official_url

    assert validate(
        "https://store.weixin.qq.com/shop/product/123"
    ) == "https://store.weixin.qq.com/shop/product/123"
    assert validate(
        "https://mp.weixin.qq.com/s/example"
    ) == "https://mp.weixin.qq.com/s/example"

    for value in (
        "http://store.weixin.qq.com/shop/product/123",
        "https://evil.example.com/weixin.qq.com",
        "weixin://private",
    ):
        try:
            validate(value)
        except ValueError:
            pass
        else:
            raise AssertionError(value)
