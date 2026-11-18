from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from weimi_manager.account_store_sync import (
    VAULT_METADATA_KEY,
    AccountStoreSync,
    decrypt_store_snapshot,
    derive_vault_key,
    encrypt_store_snapshot,
)
from weimi_manager.db import Database


class AccountStoreSyncTests(unittest.TestCase):
    def test_store_accounts_round_trip_between_local_databases(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = Database(root / "source.db")
            store_id = source.add_store(
                "迁移店铺",
                "wx-portable-store",
                "portable-secret",
                "测试分组",
            )
            source.create_store_group("空分组也要同步")
            source.set_web_session(
                store_id,
                {
                    "cookies": [
                        {"name": "session_id", "value": "cookie-value"}
                    ],
                    "source_url": "https://store.weixin.qq.com/shop",
                },
                "已登录",
                "测试登录",
            )
            snapshot = source.export_store_accounts()

            target = Database(root / "target.db")
            result = target.import_store_accounts(snapshot)
            stores = target.list_stores()
            self.assertEqual(result["created"], 1)
            self.assertEqual(len(stores), 1)
            self.assertEqual(stores[0].name, "迁移店铺")
            self.assertEqual(stores[0].group_name, "测试分组")
            self.assertEqual(
                set(target.list_store_groups()),
                {"测试分组", "空分组也要同步"},
            )
            self.assertEqual(stores[0].web_status, "已登录")
            self.assertEqual(
                target.get_store_secret(stores[0].id)[1],
                "portable-secret",
            )
            self.assertEqual(
                target.get_web_cookies(stores[0].id)["cookies"][0]["value"],
                "cookie-value",
            )

    def test_encrypted_vault_round_trip_and_wrong_password(self) -> None:
        account_id = "account-123"
        snapshot = {
            "type": "langlang-store-accounts",
            "version": 1,
            "stores": [
                {
                    "app_id": "wx-portable",
                    "name": "测试店铺",
                    "secret": "not-plaintext-in-vault",
                }
            ],
        }
        key = derive_vault_key("correct-password", account_id)
        envelope = encrypt_store_snapshot(snapshot, key, account_id)
        serialized = json.dumps(envelope)
        self.assertNotIn("not-plaintext-in-vault", serialized)
        self.assertEqual(
            decrypt_store_snapshot(envelope, key, account_id),
            snapshot,
        )
        with self.assertRaises(ValueError):
            decrypt_store_snapshot(
                envelope,
                derive_vault_key("wrong-password", account_id),
                account_id,
            )

    def test_sync_pulls_and_pushes_encrypted_account_metadata(self) -> None:
        class FakeDb:
            def __init__(self) -> None:
                self.imported = None

            def export_store_accounts(self) -> dict:
                return {
                    "type": "langlang-store-accounts",
                    "version": 1,
                    "stores": [{"app_id": "wx-one", "name": "店铺一"}],
                }

            def import_store_accounts(self, snapshot: dict) -> dict:
                self.imported = snapshot
                return {"created": 1, "updated": 0, "logins": 1}

        class FakeAuthClient:
            def __init__(self) -> None:
                self.metadata = None

            def update_user_metadata(
                self,
                access_token: str,
                metadata: dict,
            ) -> None:
                self.access_token = access_token
                self.metadata = metadata

        db = FakeDb()
        client = FakeAuthClient()
        account_id = "account-456"
        key = derive_vault_key("password", account_id)
        tokens = SimpleNamespace(
            account_id=account_id,
            access_token="access-token",
            metadata={},
        )
        sync = AccountStoreSync(
            db=db,
            auth_client=client,
            tokens=tokens,
            vault_key=key,
        )
        sync.push()
        envelope = client.metadata[VAULT_METADATA_KEY]

        tokens.metadata = {VAULT_METADATA_KEY: envelope}
        pulled = sync.pull()
        self.assertEqual(pulled["created"], 1)
        self.assertEqual(
            db.imported["stores"][0]["name"],
            "店铺一",
        )

    def test_push_merges_latest_server_snapshot_first(self) -> None:
        account_id = "account-merge"
        key = derive_vault_key("password", account_id)
        remote_snapshot = {
            "type": "langlang-store-accounts",
            "version": 1,
            "groups": [],
            "stores": [{"app_id": "remote-store", "name": "远端店铺"}],
        }
        remote_envelope = encrypt_store_snapshot(
            remote_snapshot,
            key,
            account_id,
        )

        class MergeDb:
            def __init__(self) -> None:
                self.stores = {"local-store": {"app_id": "local-store", "name": "本机店铺"}}

            def import_store_accounts(self, snapshot):
                for item in snapshot.get("stores") or []:
                    self.stores[str(item["app_id"])] = item
                return {"created": 1, "updated": 0, "logins": 0}

            def export_store_accounts(self):
                return {
                    "type": "langlang-store-accounts",
                    "version": 1,
                    "groups": [],
                    "stores": list(self.stores.values()),
                }

        class MergeClient:
            def __init__(self) -> None:
                self.uploaded = None

            def get_user(self, _access_token):
                return {
                    "user_metadata": {
                        VAULT_METADATA_KEY: remote_envelope,
                    }
                }

            def update_user_metadata(self, _access_token, metadata):
                self.uploaded = metadata
                return {}

        db = MergeDb()
        client = MergeClient()
        sync = AccountStoreSync(
            db=db,
            auth_client=client,
            tokens=SimpleNamespace(
                account_id=account_id,
                access_token="access",
                metadata={},
            ),
            vault_key=key,
        )
        sync.push()
        uploaded = decrypt_store_snapshot(
            client.uploaded[VAULT_METADATA_KEY],
            key,
            account_id,
        )
        self.assertEqual(
            {item["app_id"] for item in uploaded["stores"]},
            {"local-store", "remote-store"},
        )

    def test_organization_vault_uses_server_scoped_rpc(self) -> None:
        class OrgDb:
            def __init__(self) -> None:
                self.imported = None

            def import_store_accounts(self, snapshot):
                self.imported = snapshot
                return {"created": 1, "updated": 0, "logins": 1}

            def export_store_accounts(self):
                return {
                    "type": "langlang-store-accounts",
                    "version": 1,
                    "groups": [],
                    "stores": [{"app_id": "wx-org", "name": "组织店铺"}],
                }

        class OrgClient:
            def __init__(self) -> None:
                self.pushed = None

            def pull_organization_vault(self, _access_token):
                return {
                    "type": "langlang-store-accounts",
                    "version": 1,
                    "groups": [],
                    "stores": [{"app_id": "wx-remote", "name": "远端店铺"}],
                }

            def push_organization_vault(self, _access_token, snapshot):
                self.pushed = snapshot
                return {"stored": True, "store_count": 1}

        db = OrgDb()
        client = OrgClient()
        sync = AccountStoreSync(
            db=db,
            auth_client=client,
            tokens=SimpleNamespace(access_token="access", metadata={}),
            vault_key=b"x" * 32,
            organization_id="org-1",
        )
        pulled = sync.pull()
        sync.push()
        self.assertEqual(pulled["created"], 1)
        self.assertEqual(client.pushed["stores"][0]["app_id"], "wx-org")

    def test_pending_cloud_deletion_flushes_before_pull(self) -> None:
        events: list[str] = []

        class DeleteDb:
            def __init__(self) -> None:
                self.settings: dict[str, str] = {}

            def get_setting(self, key, default=""):
                return self.settings.get(key, default)

            def set_setting(self, key, value):
                self.settings[key] = value

            def export_store_accounts(self):
                return {
                    "type": "langlang-store-accounts",
                    "version": 1,
                    "stores": [],
                    "groups": [],
                }

            def import_store_accounts(self, _snapshot):
                events.append("pull")
                return {"created": 0, "updated": 0, "logins": 0}

            def list_stores(self):
                return []

        class DeleteClient:
            @staticmethod
            def delete_organization_stores(_access_token, app_ids):
                events.append("delete:" + ",".join(app_ids))
                return {"stored": True, "removed": len(app_ids)}

            @staticmethod
            def pull_organization_vault(_access_token):
                return {
                    "type": "langlang-store-accounts",
                    "version": 1,
                    "stores": [],
                    "groups": [],
                    "deleted_stores": {},
                }

        db = DeleteDb()
        sync = AccountStoreSync(
            db=db,
            auth_client=DeleteClient(),
            tokens=SimpleNamespace(
                access_token="access",
                account_id="owner-user",
                metadata={},
            ),
            vault_key=b"x" * 32,
            organization_id="org-1",
        )
        sync.queue_deletions(["wx-delete"])
        sync.pull()

        self.assertEqual(events, ["delete:wx-delete", "pull"])
        self.assertEqual(
            json.loads(
                db.settings["pending_cloud_store_deletions:owner-user"]
            ),
            [],
        )

    def test_deleted_store_tombstone_removes_only_older_local_store(self) -> None:
        class TombstoneDb:
            def __init__(self) -> None:
                self.deleted: list[int] = []

            def import_store_accounts(self, _snapshot):
                return {"created": 0, "updated": 0, "logins": 0}

            def list_stores(self):
                return [
                    SimpleNamespace(
                        id=1,
                        app_id="wx-old",
                        name="旧店",
                        created_at="2026-11-11T00:00:00+00:00",
                    ),
                    SimpleNamespace(
                        id=2,
                        app_id="wx-readded",
                        name="重新添加",
                        created_at="2026-11-13T00:00:00+00:00",
                    ),
                ]

            def delete_store(self, store_id):
                self.deleted.append(store_id)

            def get_setting(self, _key, default=""):
                return default

            def set_setting(self, _key, _value):
                return None

        class TombstoneClient:
            @staticmethod
            def pull_organization_vault(_access_token):
                return {
                    "type": "langlang-store-accounts",
                    "version": 1,
                    "stores": [],
                    "groups": [],
                    "deleted_stores": {
                        "wx-old": "2026-11-12T00:00:00+00:00",
                        "wx-readded": "2026-11-12T00:00:00+00:00",
                    },
                }

        db = TombstoneDb()
        sync = AccountStoreSync(
            db=db,
            auth_client=TombstoneClient(),
            tokens=SimpleNamespace(
                access_token="access",
                account_id="owner-user",
                metadata={},
            ),
            vault_key=b"x" * 32,
            organization_id="org-1",
        )
        sync.pull()

        self.assertEqual(db.deleted, [1])

    def test_organization_pull_imports_order_workbench_rows(self) -> None:
        class WorkbenchDb:
            def __init__(self) -> None:
                self.applied = []

            def import_store_accounts(self, _snapshot):
                return {"created": 0, "updated": 0, "logins": 0}

            def list_stores(self):
                return [
                    SimpleNamespace(
                        id=3,
                        app_id="wx-workbench",
                        created_at="2026-11-12T00:00:00+00:00",
                    )
                ]

            def apply_remote_order_workbench(
                self,
                store_id,
                order_id,
                payload,
            ):
                self.applied.append((store_id, order_id, payload))

            def get_setting(self, _key, default=""):
                return default

            def set_setting(self, _key, _value):
                return None

        class WorkbenchClient:
            @staticmethod
            def pull_organization_vault(_access_token):
                return {
                    "type": "langlang-store-accounts",
                    "version": 1,
                    "stores": [],
                    "groups": [],
                    "deleted_stores": {},
                }

            @staticmethod
            def pull_organization_order_workbench(_access_token, app_ids):
                assert app_ids == ["wx-workbench"]
                return [
                    {
                        "store_app_id": "wx-workbench",
                        "order_id": "order-1",
                        "payload": {"note": "云端备注"},
                    }
                ]

        db = WorkbenchDb()
        sync = AccountStoreSync(
            db=db,
            auth_client=WorkbenchClient(),
            tokens=SimpleNamespace(
                access_token="access",
                account_id="owner-user",
                metadata={},
            ),
            vault_key=b"x" * 32,
            organization_id="org-1",
        )
        sync.pull()

        self.assertEqual(
            db.applied,
            [(3, "order-1", {"note": "云端备注"})],
        )

    def test_organization_pull_imports_shared_settings(self) -> None:
        class SettingsDb:
            def __init__(self) -> None:
                self.settings = {}

            def import_store_accounts(self, _snapshot):
                return {"created": 0, "updated": 0, "logins": 0}

            def list_stores(self):
                return []

            def apply_remote_setting(self, key, value):
                self.settings[key] = value

            def get_setting(self, _key, default=""):
                return default

            def set_setting(self, _key, _value):
                return None

        class SettingsClient:
            @staticmethod
            def pull_organization_vault(_access_token):
                return {
                    "type": "langlang-store-accounts",
                    "version": 1,
                    "stores": [],
                    "groups": [],
                    "deleted_stores": {},
                }

            @staticmethod
            def pull_organization_settings(_access_token):
                return [
                    {"key": "store_page_size", "value": "200"},
                    {"key": "upload_tasks", "value": "[" + "x" * 1000 + "]"},
                ]

        db = SettingsDb()
        sync = AccountStoreSync(
            db=db,
            auth_client=SettingsClient(),
            tokens=SimpleNamespace(
                access_token="access",
                account_id="owner-user",
                metadata={},
            ),
            vault_key=b"x" * 32,
            organization_id="org-1",
        )
        sync.pull()

        self.assertEqual(db.settings["store_page_size"], "200")
        self.assertNotIn("upload_tasks", db.settings)

    def test_subaccount_refresh_removes_revoked_store_bindings(self) -> None:
        class RevokeDb:
            def __init__(self) -> None:
                self.settings = {
                    "organization_synced_appids:sub-user": '["wx-old","wx-keep"]'
                }
                self.deleted = []

            def import_store_accounts(self, _snapshot):
                return {"created": 1, "updated": 0, "logins": 1}

            def get_setting(self, key, default=""):
                return self.settings.get(key, default)

            def set_setting(self, key, value):
                self.settings[key] = value

            def list_stores(self):
                return [
                    SimpleNamespace(id=1, app_id="wx-old", name="旧店"),
                    SimpleNamespace(id=2, app_id="wx-keep", name="保留店"),
                ]

            def delete_store(self, store_id):
                self.deleted.append(store_id)

        class RevokeClient:
            @staticmethod
            def pull_organization_vault(_access_token):
                return {
                    "type": "langlang-store-accounts",
                    "version": 1,
                    "groups": [],
                    "stores": [{"app_id": "wx-keep", "name": "保留店"}],
                }

        db = RevokeDb()
        sync = AccountStoreSync(
            db=db,
            auth_client=RevokeClient(),
            tokens=SimpleNamespace(
                access_token="access",
                account_id="sub-user",
                metadata={},
            ),
            vault_key=b"x" * 32,
            organization_id="org-1",
            member_role="operator",
        )
        sync.pull()
        self.assertEqual(db.deleted, [1])

    def test_two_computers_converge_store_name_and_group(self) -> None:
        class CentralClient:
            def __init__(self) -> None:
                self.snapshot = {
                    "type": "langlang-store-accounts",
                    "version": 1,
                    "groups": [],
                    "stores": [],
                }

            def pull_organization_vault(self, _access_token):
                return json.loads(json.dumps(self.snapshot))

            def push_organization_vault(self, _access_token, snapshot):
                incoming = snapshot.get("stores") or []
                merged = {
                    str(item.get("app_id")): item
                    for item in self.snapshot.get("stores") or []
                }
                for item in incoming:
                    merged[str(item.get("app_id"))] = item
                self.snapshot["stores"] = list(merged.values())
                self.snapshot["groups"] = list(snapshot.get("groups") or [])
                return {
                    "stored": True,
                    "store_count": len(self.snapshot["stores"]),
                }

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_a = Database(root / "computer-a.db")
            db_b = Database(root / "computer-b.db")
            central = CentralClient()
            tokens_a = SimpleNamespace(
                access_token="access-a",
                account_id="owner-a",
                metadata={},
            )
            tokens_b = SimpleNamespace(
                access_token="access-b",
                account_id="owner-b",
                metadata={},
            )
            sync_a = AccountStoreSync(
                db=db_a,
                auth_client=central,
                tokens=tokens_a,
                vault_key=b"x" * 32,
                organization_id="org-1",
            )
            sync_b = AccountStoreSync(
                db=db_b,
                auth_client=central,
                tokens=tokens_b,
                vault_key=b"x" * 32,
                organization_id="org-1",
            )

            store_id = db_a.add_store(
                "电脑 A 店铺",
                "wx-shared",
                "",
                "原分组",
            )
            sync_a.push_current()
            sync_b.pull()
            store_b = db_b.list_stores()[0]
            db_b.update_store(
                store_b.id,
                "电脑 B 改名",
                "同步分组",
                None,
            )
            sync_b.push_current()
            sync_a.pull()

            store_a = next(
                item for item in db_a.list_stores()
                if item.id == store_id
            )
            self.assertEqual(store_a.name, "电脑 B 改名")
            self.assertEqual(store_a.group_name, "同步分组")
            self.assertIn("同步分组", db_a.list_store_groups())


if __name__ == "__main__":
    unittest.main()
