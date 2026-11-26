from __future__ import annotations

import ctypes
import time


CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002


def _open_clipboard(user32: ctypes.WinDLL) -> None:
    for _attempt in range(20):
        if user32.OpenClipboard(None):
            return
        time.sleep(0.025)
    raise RuntimeError("Windows 剪贴板正被其他程序占用")


def write_clipboard_text(value: str) -> dict[str, object]:
    if not hasattr(ctypes, "windll"):
        raise RuntimeError("当前系统不支持 Windows 原生剪贴板")
    text = str(value or "")
    if len(text) > 1_000_000:
        raise ValueError("剪贴板文本过长")
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    user32.OpenClipboard.argtypes = [ctypes.c_void_p]
    user32.OpenClipboard.restype = ctypes.c_bool
    user32.EmptyClipboard.argtypes = []
    user32.EmptyClipboard.restype = ctypes.c_bool
    user32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
    user32.SetClipboardData.restype = ctypes.c_void_p
    user32.CloseClipboard.argtypes = []
    user32.CloseClipboard.restype = ctypes.c_bool
    kernel32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalFree.argtypes = [ctypes.c_void_p]
    _open_clipboard(user32)
    handle = None
    try:
        if not user32.EmptyClipboard():
            raise RuntimeError("无法清空 Windows 剪贴板")
        encoded = (text + "\x00").encode("utf-16-le")
        handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(encoded))
        if not handle:
            raise RuntimeError("无法为剪贴板分配内存")
        locked = kernel32.GlobalLock(handle)
        if not locked:
            raise RuntimeError("无法写入剪贴板内存")
        try:
            ctypes.memmove(locked, encoded, len(encoded))
        finally:
            kernel32.GlobalUnlock(handle)
        if not user32.SetClipboardData(CF_UNICODETEXT, handle):
            raise RuntimeError("Windows 剪贴板写入失败")
        handle = None
    finally:
        if handle:
            kernel32.GlobalFree(handle)
        user32.CloseClipboard()
    return {"written": True, "length": len(text)}


def read_clipboard_text() -> str:
    if not hasattr(ctypes, "windll"):
        raise RuntimeError("当前系统不支持 Windows 原生剪贴板")
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    user32.OpenClipboard.argtypes = [ctypes.c_void_p]
    user32.OpenClipboard.restype = ctypes.c_bool
    user32.GetClipboardData.argtypes = [ctypes.c_uint]
    user32.GetClipboardData.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    _open_clipboard(user32)
    try:
        handle = user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return ""
        locked = kernel32.GlobalLock(handle)
        if not locked:
            return ""
        try:
            return ctypes.wstring_at(locked)
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()
