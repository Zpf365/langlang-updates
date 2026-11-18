from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any


class BrowserRuntimeError(RuntimeError):
    pass


_cached_executable: Path | None | bool = False


def _search_roots() -> list[Path]:
    roots: list[Path] = []
    configured = os.environ.get("LANGLANG_BROWSER_DIR", "").strip()
    if configured:
        roots.append(Path(configured).expanduser())
    meipass = getattr(sys, "_MEIPASS", "")
    if meipass:
        roots.extend(
            (
                Path(meipass) / "browser_runtime",
                Path(meipass) / "_internal" / "browser_runtime",
            )
        )
    if getattr(sys, "frozen", False):
        executable_dir = Path(sys.executable).resolve().parent
        roots.extend(
            (
                executable_dir / "browser_runtime",
                executable_dir / "_internal" / "browser_runtime",
            )
        )
    project_root = Path(__file__).resolve().parent.parent
    roots.append(project_root / "browser_runtime")
    return roots


def find_browser_executable() -> Path | None:
    global _cached_executable
    if _cached_executable is not False:
        return _cached_executable
    seen: set[str] = set()
    for root in _search_roots():
        try:
            resolved_root = str(root.resolve())
        except OSError:
            continue
        if resolved_root in seen or not root.is_dir():
            continue
        seen.add(resolved_root)
        direct = root / "chrome-win" / "chrome.exe"
        if direct.is_file():
            _cached_executable = direct.resolve()
            return _cached_executable
        direct_64 = root / "chrome-win64" / "chrome.exe"
        if direct_64.is_file():
            _cached_executable = direct_64.resolve()
            return _cached_executable
        try:
            candidate = next(
                (
                    path.resolve()
                    for pattern in (
                        "chromium-*/chrome-win/chrome.exe",
                        "chromium-*/chrome-win64/chrome.exe",
                    )
                    for path in sorted(root.glob(pattern))
                    if path.is_file()
                ),
                None,
            )
        except OSError:
            candidate = None
        if candidate is not None:
            _cached_executable = candidate
            return candidate
    _cached_executable = None
    return None


def browser_launch_options(
    *,
    headless: bool,
    no_viewport: bool = False,
    viewport: dict[str, int] | None = None,
    args: list[str] | None = None,
) -> dict[str, Any]:
    executable = find_browser_executable()
    options: dict[str, Any] = {
        "headless": bool(headless),
        "args": list(args or ()),
    }
    if no_viewport:
        options["no_viewport"] = True
    elif viewport:
        options["viewport"] = viewport
    if executable is not None:
        options["executable_path"] = str(executable)
        return options
    if not getattr(sys, "frozen", False):
        options["channel"] = os.environ.get(
            "LANGLANG_BROWSER_CHANNEL",
            "msedge",
        )
        return options
    raise BrowserRuntimeError(
        "澜琅自带浏览器组件缺失。请重新安装完整版本，"
        "或设置 LANGLANG_BROWSER_DIR 指向 Chromium 目录。"
    )


def reset_browser_runtime_cache() -> None:
    global _cached_executable
    _cached_executable = False


__all__ = [
    "BrowserRuntimeError",
    "browser_launch_options",
    "find_browser_executable",
    "reset_browser_runtime_cache",
]
