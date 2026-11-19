from __future__ import annotations

import ctypes
import os
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlparse


class WechatDesktopState(StrEnum):
    NOT_INSTALLED = "not_installed"
    NOT_RUNNING = "not_running"
    LOGIN_REQUIRED = "login_required"
    IDENTITY_UNCERTAIN = "identity_uncertain"
    LOGGED_IN = "logged_in"
    UNSUPPORTED_VERSION = "unsupported_version"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class DesktopWindow:
    hwnd: int
    process_id: int
    title: str
    class_name: str
    executable_name: str

    def __post_init__(self) -> None:
        if int(self.hwnd) <= 0:
            raise ValueError("微信窗口句柄无效")
        if int(self.process_id) <= 0:
            raise ValueError("微信进程 ID 无效")


@dataclass(frozen=True, slots=True)
class WindowSnapshot:
    login_page: bool = False
    main_window: bool = False
    visible_identity: str = ""
    visible_texts: tuple[str, ...] = ()
    error_texts: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class WechatDesktopProbe:
    state: WechatDesktopState
    installed: bool
    running: bool
    checked_at: datetime
    executable_path: str = ""
    hwnd: int = 0
    process_id: int = 0
    window_title: str = ""
    executable_name: str = ""
    visible_identity: str = ""
    identity_confirmed: bool = False
    error: str = ""
    screenshot_path: str = ""
    instructions: str = ""
    details: dict[str, Any] = field(default_factory=dict)


class WechatDesktopBackend(Protocol):
    def installation_paths(self) -> tuple[Path, ...]: ...

    def running_windows(self) -> tuple[DesktopWindow, ...]: ...

    def snapshot(self, window: DesktopWindow) -> WindowSnapshot: ...

    def capture(self, window: DesktopWindow, target: Path) -> bool: ...


class WindowsWechatBackend:
    """Read-only Windows adapter for the official WeChat desktop client."""

    _PROCESS_NAMES = {"wechat.exe", "weixin.exe"}
    _WINDOW_CLASS_MARKERS = ("wechat", "weixin")

    def installation_paths(self) -> tuple[Path, ...]:
        candidates: list[Path] = []
        for env_name in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
            root = os.environ.get(env_name, "")
            if not root:
                continue
            base = Path(root)
            candidates.extend(
                (
                    base / "Tencent" / "WeChat" / "WeChat.exe",
                    base / "Tencent" / "Weixin" / "Weixin.exe",
                    base / "Tencent" / "WeChat" / "Weixin.exe",
                )
            )
        candidates.extend(
            path
            for path in self._registry_install_paths()
            if path not in candidates
        )
        return tuple(path for path in candidates if path.is_file())

    @staticmethod
    def _registry_install_paths() -> list[Path]:
        if os.name != "nt":
            return []
        try:
            import winreg
        except ImportError:
            return []
        result: list[Path] = []
        locations = (
            (
                winreg.HKEY_CURRENT_USER,
                r"Software\Tencent\WeChat",
                "InstallPath",
            ),
            (
                winreg.HKEY_CURRENT_USER,
                r"Software\Tencent\Weixin",
                "InstallPath",
            ),
            (
                winreg.HKEY_LOCAL_MACHINE,
                r"Software\Tencent\WeChat",
                "InstallPath",
            ),
            (
                winreg.HKEY_LOCAL_MACHINE,
                r"Software\Tencent\Weixin",
                "InstallPath",
            ),
        )
        for hive, key_name, value_name in locations:
            try:
                with winreg.OpenKey(hive, key_name) as key:
                    value, _kind = winreg.QueryValueEx(key, value_name)
            except OSError:
                continue
            install_dir = Path(str(value))
            result.extend(
                (
                    install_dir / "WeChat.exe",
                    install_dir / "Weixin.exe",
                )
            )
        return result

    def running_windows(self) -> tuple[DesktopWindow, ...]:
        if os.name != "nt":
            return ()
        user32 = ctypes.windll.user32
        windows: list[DesktopWindow] = []
        callback_type = ctypes.WINFUNCTYPE(
            ctypes.c_bool,
            ctypes.c_void_p,
            ctypes.c_void_p,
        )

        def callback(hwnd: int, _lparam: int) -> bool:
            if not user32.IsWindowVisible(hwnd):
                return True
            title_length = user32.GetWindowTextLengthW(hwnd)
            if title_length <= 0:
                return True
            title_buffer = ctypes.create_unicode_buffer(title_length + 1)
            user32.GetWindowTextW(hwnd, title_buffer, title_length + 1)
            class_buffer = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, class_buffer, 256)
            process_id = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(
                hwnd,
                ctypes.byref(process_id),
            )
            executable_name = ""
            try:
                import psutil

                executable_name = psutil.Process(
                    int(process_id.value)
                ).name()
            except Exception:
                executable_name = ""
            class_name = str(class_buffer.value or "")
            if (
                executable_name.casefold() not in self._PROCESS_NAMES
                and not any(
                    marker in class_name.casefold()
                    for marker in self._WINDOW_CLASS_MARKERS
                )
            ):
                return True
            windows.append(
                DesktopWindow(
                    hwnd=int(hwnd),
                    process_id=int(process_id.value),
                    title=str(title_buffer.value or ""),
                    class_name=class_name,
                    executable_name=executable_name,
                )
            )
            return True

        user32.EnumWindows(callback_type(callback), 0)
        return tuple(windows)

    def snapshot(self, window: DesktopWindow) -> WindowSnapshot:
        texts: list[str] = [window.title]
        try:
            from pywinauto import Desktop

            wrapper = Desktop(backend="uia").window(
                handle=window.hwnd
            )
            texts.extend(
                str(control.window_text() or "")
                for control in wrapper.descendants()
                if str(control.window_text() or "").strip()
            )
        except Exception as exc:
            return WindowSnapshot(
                visible_texts=tuple(texts),
                error_texts=(f"UIA 读取失败：{type(exc).__name__}",),
            )
        unique = tuple(dict.fromkeys(texts))
        joined = "\n".join(unique)
        login_page = any(
            marker in joined
            for marker in ("进入微信", "扫码登录", "二维码已失效")
        )
        main_window = (
            any(
                marker in window.class_name.casefold()
                for marker in self._WINDOW_CLASS_MARKERS
            )
            and not login_page
        )
        identity_match = re.search(
            r"(?:微信|WeChat|Weixin)\s*[-—–]\s*(.+)$",
            window.title,
            re.IGNORECASE,
        )
        visible_identity = (
            identity_match.group(1).strip() if identity_match else ""
        )
        error_texts = tuple(
            text
            for text in unique
            if any(
                marker in text
                for marker in ("版本过低", "版本不支持", "不支持该页面", "请升级微信")
            )
        )
        return WindowSnapshot(
            login_page=login_page,
            main_window=main_window,
            visible_identity=visible_identity,
            visible_texts=unique,
            error_texts=error_texts,
        )

    def capture(self, window: DesktopWindow, target: Path) -> bool:
        try:
            from pywinauto import Desktop

            image = Desktop(backend="uia").window(
                handle=window.hwnd
            ).capture_as_image()
            target.parent.mkdir(parents=True, exist_ok=True)
            image.save(str(target))
            return True
        except Exception:
            return False


class WechatDesktopClient:
    def __init__(
        self,
        backend: WechatDesktopBackend | None = None,
        *,
        screenshot_dir: Path,
    ) -> None:
        self.backend = backend or WindowsWechatBackend()
        self.screenshot_dir = Path(screenshot_dir)

    def probe(self) -> WechatDesktopProbe:
        checked_at = datetime.now(UTC)
        installs = self.backend.installation_paths()
        windows = sorted(
            self.backend.running_windows(),
            key=lambda item: (
                item.executable_name.casefold() not in {"wechat.exe", "weixin.exe"},
                -int(item.hwnd),
            ),
        )
        installed = bool(installs)
        if not windows:
            return WechatDesktopProbe(
                state=(
                    WechatDesktopState.NOT_RUNNING
                    if installed
                    else WechatDesktopState.NOT_INSTALLED
                ),
                installed=installed,
                running=False,
                checked_at=checked_at,
                executable_path=str(installs[0]) if installs else "",
                instructions=(
                    "请用户本人启动 Windows 官方微信客户端。"
                    if installed
                    else "未检测到 Windows 官方微信客户端，请通过官方渠道安装。"
                ),
            )
        window = windows[0]
        snapshot = self.backend.snapshot(window)
        screenshot_path = ""
        if snapshot.error_texts:
            target = self.screenshot_dir / (
                f"wechat-client-{checked_at.strftime('%Y%m%d-%H%M%S')}.png"
            )
            if self.backend.capture(window, target):
                screenshot_path = str(target)
        base = {
            "installed": True,
            "running": True,
            "checked_at": checked_at,
            "executable_path": str(installs[0]) if installs else "",
            "hwnd": window.hwnd,
            "process_id": window.process_id,
            "window_title": window.title,
            "executable_name": window.executable_name,
            "screenshot_path": screenshot_path,
            "details": {
                "class_name": window.class_name,
                "visible_texts": list(snapshot.visible_texts[:80]),
            },
        }
        if snapshot.error_texts:
            error = "；".join(snapshot.error_texts)
            state = (
                WechatDesktopState.UNSUPPORTED_VERSION
                if "版本" in error or "升级" in error
                else WechatDesktopState.ERROR
            )
            return WechatDesktopProbe(
                state=state,
                error=error,
                instructions="已保留截图和诊断信息，请按提示人工处理。",
                **base,
            )
        if snapshot.login_page:
            return WechatDesktopProbe(
                state=WechatDesktopState.LOGIN_REQUIRED,
                instructions="请用户本人在官方微信窗口完成扫码或账号登录。",
                **base,
            )
        if snapshot.visible_identity:
            return WechatDesktopProbe(
                state=WechatDesktopState.LOGGED_IN,
                visible_identity=snapshot.visible_identity,
                identity_confirmed=True,
                instructions="已在窗口标题中看到当前微信身份。",
                **base,
            )
        return WechatDesktopProbe(
            state=WechatDesktopState.IDENTITY_UNCERTAIN,
            identity_confirmed=False,
            instructions=(
                "官方微信已经运行，但无法从可见窗口可靠确认当前微信身份。"
                "必须由用户本人在软件中人工确认昵称和身份标识。"
            ),
            **base,
        )

    @staticmethod
    def switch_account_instructions() -> str:
        return (
            "请在官方微信客户端中由用户本人退出当前账号并重新扫码登录。"
            "澜琅不会退出、注入、多开或读取微信私有数据；"
            "切换完成后回到软件检查并人工确认身份。"
        )

    @staticmethod
    def validate_official_url(url: str) -> str:
        text = str(url or "").strip()
        parsed = urlparse(text)
        host = (parsed.hostname or "").lower()
        if parsed.scheme != "https" or not (
            host == "weixin.qq.com"
            or host.endswith(".weixin.qq.com")
        ):
            raise ValueError("只允许交接官方 weixin.qq.com 商品链接")
        return text

    @classmethod
    def open_official_url(cls, url: str) -> bool:
        text = cls.validate_official_url(url)
        if os.name != "nt":
            raise RuntimeError("官方商品入口交接仅支持 Windows")
        os.startfile(text)  # type: ignore[attr-defined]
        return True
