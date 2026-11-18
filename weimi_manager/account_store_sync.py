from __future__ import annotations

import base64
import hashlib
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .cloud_commands import is_local_only_setting

VAULT_METADATA_KEY = "langlang_store_vault_v1"
VAULT_ITERATIONS = 600_000


def derive_vault_key(password: str, account_id: str) -> bytes:
    if not password:
        raise ValueError("账户密码为空，无法生成店铺同步密钥")
    salt = hashlib.sha256(
        f"langlang-store-vault:{account_id}".encode("utf-8")
    ).digest()[:16]
    return hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        VAULT_ITERATIONS,
        dklen=32,
    )


def _associated_data(account_id: str) -> bytes:
    return f"langlang-store-vault-v1:{account_id}".encode("utf-8")


def encrypt_store_snapshot(
    snapshot: dict[str, Any],
    key: bytes,
    account_id: str,
) -> dict[str, Any]:
    nonce = os.urandom(12)
    plaintext = json.dumps(
        snapshot,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    ciphertext = AESGCM(key).encrypt(
        nonce,
        plaintext,
        _associated_data(account_id),
    )
    return {
        "type": "langlang-encrypted-store-vault",
        "version": 1,
        "iterations": VAULT_ITERATIONS,
        "nonce": base64.b64encode(nonce).decode("ascii"),
        "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
    }


def decrypt_store_snapshot(
    envelope: dict[str, Any],
    key: bytes,
    account_id: str,
) -> dict[str, Any]:
    if not isinstance(envelope, dict):
        raise ValueError("账户店铺保险箱格式不正确")
    try:
        nonce = base64.b64decode(str(envelope["nonce"]))
        ciphertext = base64.b64decode(str(envelope["ciphertext"]))
        plaintext = AESGCM(key).decrypt(
            nonce,
            ciphertext,
            _associated_data(account_id),
        )
        payload = json.loads(plaintext.decode("utf-8"))
    except (KeyError, ValueError, json.JSONDecodeError, InvalidTag) as exc:
        raise ValueError("账户店铺保险箱无法解密，请检查密码") from exc
    if not isinstance(payload, dict):
        raise ValueError("账户店铺保险箱内容不正确")
    return payload


@dataclass
class AccountStoreSync:
    db: Any
    auth_client: Any
    tokens: Any
    vault_key: bytes
    organization_id: str = ""
    member_role: str = "owner"
    member_permissions: dict[str, bool] | None = None

    @property
    def account_id(self) -> str:
        return str(getattr(self.tokens, "account_id", "") or "")

    @property
    def _pending_deletion_key(self) -> str:
        return f"pending_cloud_store_deletions:{self.account_id}"

    def queue_deletions(self, app_ids: list[str]) -> None:
        normalized = {
            str(app_id).strip()
            for app_id in app_ids
            if str(app_id).strip()
        }
        if not normalized:
            return
        try:
            pending = set(
                json.loads(
                    self.db.get_setting(
                        self._pending_deletion_key,
                        "[]",
                    )
                )
            )
        except (TypeError, ValueError):
            pending = set()
        pending.update(normalized)
        self.db.set_setting(
            self._pending_deletion_key,
            json.dumps(sorted(pending), ensure_ascii=False),
        )

    def delete_now(self, app_ids: list[str]) -> dict[str, Any]:
        normalized = sorted(
            {
                str(app_id).strip()
                for app_id in app_ids
                if str(app_id).strip()
            }
        )
        if not normalized:
            return {"stored": True, "removed": 0}
        if self.organization_id:
            delete_org = getattr(
                self.auth_client,
                "delete_organization_stores",
                None,
            )
            if not callable(delete_org):
                raise ValueError("客户端不支持删除云端组织店铺")
            return delete_org(
                str(getattr(self.tokens, "access_token", "") or ""),
                normalized,
            )
        snapshot = self.db.export_store_accounts()
        removed = {
            str(item.get("app_id") or "")
            for item in snapshot.get("stores") or []
            if isinstance(item, dict)
            and str(item.get("app_id") or "") in set(normalized)
        }
        snapshot["stores"] = [
            item
            for item in snapshot.get("stores") or []
            if isinstance(item, dict)
            and str(item.get("app_id") or "") not in set(normalized)
        ]
        result = self._upload_snapshot(snapshot)
        return {
            **result,
            "stored": True,
            "removed": len(removed),
        }

    def _pending_deletions(self) -> set[str]:
        get_setting = getattr(self.db, "get_setting", None)
        if not callable(get_setting):
            return set()
        try:
            payload = json.loads(
                get_setting(
                    self._pending_deletion_key,
                    "[]",
                )
            )
        except (TypeError, ValueError):
            return set()
        return {
            str(app_id).strip()
            for app_id in payload
            if str(app_id).strip()
        } if isinstance(payload, list) else set()

    def _flush_pending_deletions(self) -> dict[str, Any]:
        pending = self._pending_deletions()
        if not pending:
            return {}
        ordered = sorted(pending)
        if self.organization_id:
            delete_org = getattr(
                self.auth_client,
                "delete_organization_stores",
                None,
            )
            if not callable(delete_org):
                raise ValueError("客户端不支持删除云端组织店铺")
            result = delete_org(
                str(getattr(self.tokens, "access_token", "") or ""),
                ordered,
            )
        else:
            result = self._upload_snapshot(
                self.db.export_store_accounts()
            )
        remaining = self._pending_deletions() - set(ordered)
        self.db.set_setting(
            self._pending_deletion_key,
            json.dumps(sorted(remaining), ensure_ascii=False),
        )
        return result if isinstance(result, dict) else {}

    def _apply_deleted_tombstones(self, snapshot: dict[str, Any]) -> None:
        deleted = snapshot.get("deleted_stores")
        if not isinstance(deleted, dict):
            return
        local = {
            store.app_id: store
            for store in self.db.list_stores()
        }
        for app_id, deleted_at in deleted.items():
            store = local.get(str(app_id))
            if store is None:
                continue
            try:
                store_created = datetime.fromisoformat(
                    str(store.created_at).replace("Z", "+00:00")
                )
                tombstone = datetime.fromisoformat(
                    str(deleted_at).replace("Z", "+00:00")
                )
            except ValueError:
                continue
            if store_created.tzinfo is None:
                store_created = store_created.replace(tzinfo=UTC)
            if tombstone.tzinfo is None:
                tombstone = tombstone.replace(tzinfo=UTC)
            if store_created <= tombstone:
                self.db.delete_store(store.id)

    def _upload_snapshot(
        self,
        snapshot: dict[str, Any],
    ) -> dict[str, Any]:
        envelope = encrypt_store_snapshot(
            snapshot,
            self.vault_key,
            self.account_id,
        )
        result = self.auth_client.update_user_metadata(
            self.tokens.access_token,
            {VAULT_METADATA_KEY: envelope},
        )
        try:
            self.tokens.metadata = {
                **dict(getattr(self.tokens, "metadata", {}) or {}),
                VAULT_METADATA_KEY: envelope,
            }
        except (AttributeError, TypeError):
            pass
        return {
            **(result if isinstance(result, dict) else {}),
            "stored": True,
        }

    def _refresh_metadata(self) -> dict[str, Any]:
        get_user = getattr(self.auth_client, "get_user", None)
        if not callable(get_user):
            metadata = getattr(self.tokens, "metadata", {})
            return metadata if isinstance(metadata, dict) else {}
        user = get_user(str(getattr(self.tokens, "access_token", "") or ""))
        metadata = user.get("user_metadata")
        metadata = metadata if isinstance(metadata, dict) else {}
        try:
            self.tokens.metadata = dict(metadata)
        except (AttributeError, TypeError):
            pass
        return metadata

    def _pull_order_workbench(self) -> None:
        if not self.organization_id:
            return
        permissions = self.member_permissions or {}
        if (
            self.member_role not in {"owner", "admin"}
            and not permissions.get("orders_view")
        ):
            return
        pull_workbench = getattr(
            self.auth_client,
            "pull_organization_order_workbench",
            None,
        )
        if not callable(pull_workbench):
            return
        local_stores = {
            store.app_id: store.id
            for store in self.db.list_stores()
        }
        rows = pull_workbench(
            str(getattr(self.tokens, "access_token", "") or ""),
            sorted(local_stores),
        )
        for row in rows:
            if not isinstance(row, dict):
                continue
            store_id = local_stores.get(str(row.get("store_app_id") or ""))
            order_id = str(row.get("order_id") or "")
            payload = row.get("payload")
            if store_id is None or not order_id or not isinstance(payload, dict):
                continue
            try:
                self.db.apply_remote_order_workbench(
                    store_id,
                    order_id,
                    payload,
                )
            except Exception:
                # The order may not have been fetched on this computer yet.
                continue

    def _pull_settings(self) -> None:
        if not self.organization_id:
            return
        pull_settings = getattr(
            self.auth_client,
            "pull_organization_settings",
            None,
        )
        if not callable(pull_settings):
            return
        rows = pull_settings(
            str(getattr(self.tokens, "access_token", "") or "")
        )
        for row in rows:
            if not isinstance(row, dict):
                continue
            key = str(row.get("key") or "").strip()
            if not key or is_local_only_setting(key):
                continue
            self.db.apply_remote_setting(
                key,
                str(row.get("value") or ""),
            )

    def pull(
        self,
        *,
        include_workbench: bool = True,
        include_settings: bool = True,
    ) -> dict[str, int]:
        self._flush_pending_deletions()
        if self.organization_id:
            pull_org = getattr(
                self.auth_client,
                "pull_organization_vault",
                None,
            )
            if not callable(pull_org):
                raise ValueError("客户端不支持组织店铺同步")
            snapshot = pull_org(
                str(getattr(self.tokens, "access_token", "") or "")
            )
            imported = self.db.import_store_accounts(snapshot)
            self._apply_deleted_tombstones(snapshot)
            if include_workbench:
                self._pull_order_workbench()
            if include_settings:
                self._pull_settings()
            if self.member_role not in {"owner", "admin"}:
                key = (
                    "organization_synced_appids:"
                    + str(getattr(self.tokens, "account_id", "") or "")
                )
                try:
                    previous = set(
                        json.loads(self.db.get_setting(key, "[]"))
                    )
                except (TypeError, ValueError):
                    previous = set()
                incoming = {
                    str(item.get("app_id") or "")
                    for item in snapshot.get("stores") or []
                    if isinstance(item, dict)
                }
                revoked = previous - incoming
                for store in self.db.list_stores():
                    if store.app_id in revoked:
                        self.db.delete_store(store.id)
                self.db.set_setting(
                    key,
                    json.dumps(sorted(incoming), ensure_ascii=False),
                )
            return imported
        metadata = self._refresh_metadata()
        envelope = metadata.get(VAULT_METADATA_KEY)
        if not isinstance(envelope, dict):
            return {"created": 0, "updated": 0, "logins": 0}
        snapshot = decrypt_store_snapshot(
            envelope,
            self.vault_key,
            self.account_id,
        )
        imported = self.db.import_store_accounts(snapshot)
        self._apply_deleted_tombstones(snapshot)
        return imported

    def push(self) -> dict[str, Any]:
        # Merge the latest server snapshot before uploading the local export.
        # This prevents one computer from replacing stores added elsewhere.
        self.pull()
        return self.push_current()

    def push_current(self) -> dict[str, Any]:
        """Upload the current local snapshot without an additional pull."""
        snapshot = self.db.export_store_accounts()
        if self.organization_id:
            push_org = getattr(
                self.auth_client,
                "push_organization_vault",
                None,
            )
            if not callable(push_org):
                raise ValueError("客户端不支持组织店铺同步")
            result = push_org(
                str(getattr(self.tokens, "access_token", "") or ""),
                snapshot,
            )
            return result if isinstance(result, dict) else {}
        return self._upload_snapshot(snapshot)
