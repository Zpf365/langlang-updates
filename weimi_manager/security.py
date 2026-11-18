from __future__ import annotations

import ctypes
import os
import sys
from ctypes import wintypes


class CredentialError(RuntimeError):
    pass


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
    ]


def _blob(data: bytes) -> tuple[_DataBlob, ctypes.Array]:
    buffer = ctypes.create_string_buffer(data)
    blob = _DataBlob(
        len(data),
        ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)),
    )
    return blob, buffer


class CredentialProtector:
    """Protect local secrets with the current Windows user's DPAPI key."""

    _ENTROPY = b"weimi-manager/v1"
    _CRYPTPROTECT_UI_FORBIDDEN = 0x1

    def protect(self, secret: str) -> bytes:
        if not secret:
            raise CredentialError("AppSecret 不能为空")
        if sys.platform != "win32":
            raise CredentialError("当前版本仅支持 Windows DPAPI 加密")
        raw = secret.encode("utf-8")
        input_blob, input_buffer = _blob(raw)
        entropy_blob, entropy_buffer = _blob(self._ENTROPY)
        output_blob = _DataBlob()
        ok = ctypes.windll.crypt32.CryptProtectData(
            ctypes.byref(input_blob),
            "WeimiManager AppSecret",
            ctypes.byref(entropy_blob),
            None,
            None,
            self._CRYPTPROTECT_UI_FORBIDDEN,
            ctypes.byref(output_blob),
        )
        del input_buffer, entropy_buffer
        if not ok:
            raise CredentialError(f"Windows 密钥加密失败，错误码 {ctypes.get_last_error()}")
        try:
            return ctypes.string_at(output_blob.pbData, output_blob.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(output_blob.pbData)

    def unprotect(self, protected: bytes) -> str:
        if sys.platform != "win32":
            raise CredentialError("当前版本仅支持 Windows DPAPI 解密")
        input_blob, input_buffer = _blob(protected)
        entropy_blob, entropy_buffer = _blob(self._ENTROPY)
        output_blob = _DataBlob()
        ok = ctypes.windll.crypt32.CryptUnprotectData(
            ctypes.byref(input_blob),
            None,
            ctypes.byref(entropy_blob),
            None,
            None,
            self._CRYPTPROTECT_UI_FORBIDDEN,
            ctypes.byref(output_blob),
        )
        del input_buffer, entropy_buffer
        if not ok:
            raise CredentialError(f"Windows 密钥解密失败，错误码 {ctypes.get_last_error()}")
        try:
            raw = ctypes.string_at(output_blob.pbData, output_blob.cbData)
            return raw.decode("utf-8")
        finally:
            ctypes.windll.kernel32.LocalFree(output_blob.pbData)

