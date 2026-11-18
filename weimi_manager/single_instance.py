from __future__ import annotations

import ctypes
import hashlib
import os
from pathlib import Path


class SingleInstanceGuard:
    """Prevent two copies from the same installation directory."""

    _ERROR_ALREADY_EXISTS = 183

    def __init__(self, project_dir: Path) -> None:
        path_key = str(Path(project_dir).resolve()).casefold().encode(
            "utf-8"
        )
        digest = hashlib.sha256(path_key).hexdigest()[:20]
        self.name = f"Local\\LangLang-{digest}"
        self._handle: int | None = None

    def acquire(self) -> bool:
        if os.name != "nt":
            return True
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.SetLastError(0)
        handle = kernel32.CreateMutexW(None, False, self.name)
        if not handle:
            raise OSError(
                ctypes.get_last_error(),
                "无法创建程序单实例锁",
            )
        if ctypes.get_last_error() == self._ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(handle)
            return False
        self._handle = handle
        return True

    def close(self) -> None:
        if self._handle is None:
            return
        ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle(
            self._handle
        )
        self._handle = None
