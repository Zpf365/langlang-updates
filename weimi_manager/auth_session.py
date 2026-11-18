from __future__ import annotations

import hashlib
import json
import os
import socket
import sys
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from .security import CredentialError, CredentialProtector


@dataclass(frozen=True)
class SavedAccount:
    account_id: str
    username: str
    email: str
    access_token: str
    refresh_token: str
    expires_at: str
    last_login_at: str
    license_active: bool = False
    license_permanent: bool = False
    license_expires_at: str = ""
    last_server_time: str = ""
    last_verified_local_time: str = ""
    organization_id: str = ""
    member_role: str = "owner"
    permissions: dict[str, bool] = field(default_factory=dict)
    password: str = field(default="", repr=False, compare=False)

    @classmethod
    def from_dict(cls, payload: dict) -> "SavedAccount | None":
        try:
            account = cls(
                account_id=str(payload["account_id"]),
                username=str(payload["username"]),
                email=str(payload.get("email") or ""),
                access_token=str(payload.get("access_token") or ""),
                refresh_token=str(payload.get("refresh_token") or ""),
                expires_at=str(payload.get("expires_at") or ""),
                last_login_at=str(payload.get("last_login_at") or ""),
                license_active=bool(payload.get("license_active")),
                license_permanent=bool(payload.get("license_permanent")),
                license_expires_at=str(
                    payload.get("license_expires_at") or ""
                ),
                last_server_time=str(payload.get("last_server_time") or ""),
                last_verified_local_time=str(
                    payload.get("last_verified_local_time") or ""
                ),
                organization_id=str(
                    payload.get("organization_id") or ""
                ),
                member_role=str(payload.get("member_role") or "owner"),
                permissions=(
                    {
                        str(key): bool(value)
                        for key, value in payload.get("permissions", {}).items()
                    }
                    if isinstance(payload.get("permissions"), dict)
                    else {}
                ),
                password=str(payload.get("password") or ""),
            )
        except (KeyError, TypeError, ValueError):
            return None
        if not account.account_id or not account.username:
            return None
        return account


class AuthSessionStore:
    """Persist refresh tokens with the current Windows user's DPAPI key."""

    def __init__(
        self,
        base_dir: Path,
        protector: CredentialProtector | None = None,
    ) -> None:
        self.base_dir = Path(base_dir)
        self.path = self.base_dir / "saved_accounts.bin"
        self.protector = protector or CredentialProtector()

    def list_accounts(self) -> list[SavedAccount]:
        if not self.path.is_file():
            return []
        try:
            raw = self.protector.unprotect(self.path.read_bytes())
            payload = json.loads(raw)
        except (OSError, CredentialError, UnicodeDecodeError, json.JSONDecodeError):
            return []
        if not isinstance(payload, list):
            return []
        accounts = [
            account
            for item in payload
            if isinstance(item, dict)
            for account in [SavedAccount.from_dict(item)]
            if account is not None
        ]
        return sorted(
            accounts,
            key=lambda item: item.last_login_at,
            reverse=True,
        )

    def _write(self, accounts: list[SavedAccount]) -> None:
        self.base_dir.mkdir(parents=True, exist_ok=True)
        serialized = json.dumps(
            [asdict(account) for account in accounts],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        protected = self.protector.protect(serialized)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_bytes(protected)
        os.replace(temporary, self.path)

    def save(self, account: SavedAccount) -> None:
        normalized_username = account.username.strip().casefold()
        accounts = [
            existing
            for existing in self.list_accounts()
            if existing.account_id != account.account_id
            and existing.username.strip().casefold() != normalized_username
        ]
        accounts.insert(0, account)
        self._write(accounts[:20])

    def remove(self, account_id: str) -> None:
        accounts = [
            account
            for account in self.list_accounts()
            if account.account_id != account_id
        ]
        if accounts:
            self._write(accounts)
            return
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass

    def find(self, username: str) -> SavedAccount | None:
        normalized = username.strip().casefold()
        return next(
            (
                account
                for account in self.list_accounts()
                if account.username.casefold() == normalized
            ),
            None,
        )


class DeviceIdentity:
    """Provide a stable, non-raw machine identifier for license binding."""

    @staticmethod
    def machine_guid() -> str:
        if sys.platform == "win32":
            try:
                import winreg

                with winreg.OpenKey(
                    winreg.HKEY_LOCAL_MACHINE,
                    r"SOFTWARE\Microsoft\Cryptography",
                ) as key:
                    value, _kind = winreg.QueryValueEx(key, "MachineGuid")
                    if value:
                        return str(value)
            except OSError:
                pass
        return f"{socket.gethostname()}|{os.environ.get('USERNAME', '')}"

    @classmethod
    def device_hash(cls) -> str:
        material = f"langlang-device-v1:{cls.machine_guid()}".encode("utf-8")
        return hashlib.sha256(material).hexdigest()

    @staticmethod
    def device_name() -> str:
        return (socket.gethostname() or "Windows 电脑")[:80]

    @staticmethod
    def now_iso() -> str:
        return datetime.now(UTC).isoformat(timespec="seconds")
