from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ...security import CredentialError, CredentialProtector


class BuyerSessionVaultError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class BuyerSessionRecord:
    buyer_account_id: str
    provider: str
    opaque_session: str
    issued_at: str
    expires_at: str = ""

    def metadata(self) -> dict[str, str]:
        return {
            "buyer_account_id": self.buyer_account_id,
            "provider": self.provider,
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
        }


class BuyerSessionVault:
    """DPAPI-protected buyer session store outside the SQLite database."""

    def __init__(
        self,
        data_dir: Path,
        *,
        protector: CredentialProtector | None = None,
    ) -> None:
        self.path = Path(data_dir) / "buyer_sessions.bin"
        self.protector = protector or CredentialProtector()

    def _read(self) -> dict[str, dict[str, Any]]:
        if not self.path.is_file():
            return {}
        try:
            raw = self.protector.unprotect(self.path.read_bytes())
            payload = json.loads(raw)
        except (
            OSError,
            CredentialError,
            UnicodeDecodeError,
            json.JSONDecodeError,
        ) as exc:
            raise BuyerSessionVaultError("买家会话保险箱无法读取") from exc
        if not isinstance(payload, dict):
            raise BuyerSessionVaultError("买家会话保险箱格式不正确")
        return {
            str(key): value
            for key, value in payload.items()
            if isinstance(value, dict)
        }

    def _write(self, payload: dict[str, dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        serialized = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        protected = self.protector.protect(serialized)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_bytes(protected)
        os.replace(temporary, self.path)

    def save(self, record: BuyerSessionRecord) -> None:
        if not record.buyer_account_id.strip():
            raise ValueError("买家账号 ID 不能为空")
        if not record.opaque_session.strip():
            raise ValueError("官方买家会话对象不能为空")
        payload = self._read()
        payload[record.buyer_account_id] = {
            "provider": record.provider,
            "opaque_session": record.opaque_session,
            "issued_at": record.issued_at,
            "expires_at": record.expires_at,
        }
        self._write(payload)

    def get(self, buyer_account_id: str) -> BuyerSessionRecord | None:
        item = self._read().get(str(buyer_account_id))
        if item is None:
            return None
        return BuyerSessionRecord(
            buyer_account_id=str(buyer_account_id),
            provider=str(item.get("provider") or ""),
            opaque_session=str(item.get("opaque_session") or ""),
            issued_at=str(item.get("issued_at") or ""),
            expires_at=str(item.get("expires_at") or ""),
        )

    def list_metadata(self) -> list[dict[str, str]]:
        records = []
        for account_id in sorted(self._read()):
            record = self.get(account_id)
            if record is not None:
                records.append(record.metadata())
        return records

    def remove(self, buyer_account_id: str) -> None:
        payload = self._read()
        if payload.pop(str(buyer_account_id), None) is not None:
            if payload:
                self._write(payload)
            else:
                try:
                    self.path.unlink()
                except FileNotFoundError:
                    pass

    def fingerprint(self, buyer_account_id: str) -> str:
        record = self.get(buyer_account_id)
        if record is None:
            return ""
        return hashlib.sha256(
            f"{record.provider}\0{record.opaque_session}".encode("utf-8")
        ).hexdigest()

    @staticmethod
    def now_iso() -> str:
        return datetime.now(UTC).isoformat(timespec="seconds")
