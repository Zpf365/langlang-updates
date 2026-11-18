from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from weimi_manager.auth_client import AuthError
from weimi_manager.cloud_commands import (
    AuthorizedDatabaseAdapter,
    AuthorizedServiceAdapter,
    AuthorizedWebSessionAdapter,
    CloudCommandClient,
)
from weimi_manager.db import Database
from weimi_manager.models import Store


class FakeClient:
    def __init__(self) -> None:
        self.authorized: list[tuple[str, str, str]] = []
        self.completed: list[tuple[str, bool, dict, str]] = []
        self.denied = False
        self.workbench_rows = []
        self.workbench_fail = False
        self.settings_rows = []

    def authorize_organization_command(
        self,
        access_token,
        request_id,
        command,
        store_app_id,
    ):
        self.authorized.append((request_id, command, store_app_id))
        if self.denied:
            raise AuthError("没有执行该操作的权限", 403)
        return {
            "request_id": request_id,
            "authorized": True,
            "command": command,
            "store_app_id": store_app_id,
        }

    def complete_organization_command(
        self,
        _access_token,
        request_id,
        succeeded,
        result=None,
        error="",
    ):
        self.completed.append(
            (request_id, succeeded, result or {}, error)
        )
        return {}

    def upsert_organization_order_workbench(self, _access_token, rows):
        if self.workbench_fail:
            raise RuntimeError("workbench unavailable")
        self.workbench_rows.extend(rows)
        return {"saved": len(rows)}

    def upsert_organization_settings(self, _access_token, rows):
        self.settings_rows.extend(rows)
        return {"saved": len(rows)}


class CloudCommandTests(unittest.TestCase):
    def test_execute_authorizes_then_records_success(self) -> None:
        client = FakeClient()
        commands = CloudCommandClient(client, "access")  # type: ignore[arg-type]
        result = commands.execute(
            "stores.delete",
            "wx-store",
            lambda: {"removed": 1},
        )

        self.assertEqual(result, {"removed": 1})
        self.assertEqual(client.authorized[0][1:], (
            "stores.delete",
            "wx-store",
        ))
        self.assertTrue(client.completed[0][1])

    def test_execute_records_failure_and_does_not_swallow_it(self) -> None:
        client = FakeClient()
        commands = CloudCommandClient(client, "access")  # type: ignore[arg-type]

        with self.assertRaisesRegex(RuntimeError, "platform failed"):
            commands.execute(
                "products.edit",
                "wx-store",
                lambda: (_ for _ in ()).throw(
                    RuntimeError("platform failed")
                ),
            )

        self.assertFalse(client.completed[0][1])
        self.assertIn("platform failed", client.completed[0][3])

    def test_store_scoped_command_requires_store(self) -> None:
        client = FakeClient()
        commands = CloudCommandClient(client, "access")  # type: ignore[arg-type]

        with self.assertRaises(AuthError):
            commands.execute_many(
                "stores.delete",
                [],
                lambda: None,
            )
        self.assertEqual(client.authorized, [])

    def test_batch_command_authorizes_every_store(self) -> None:
        client = FakeClient()
        commands = CloudCommandClient(client, "access")  # type: ignore[arg-type]
        commands.execute_many(
            "stores.login",
            ["wx-one", "wx-two"],
            lambda: "ok",
        )

        self.assertEqual(
            [item[2] for item in client.authorized],
            ["wx-one", "wx-two"],
        )
        self.assertEqual(len(client.completed), 2)

    def test_service_adapter_authorizes_platform_write(self) -> None:
        client = FakeClient()
        commands = CloudCommandClient(client, "access")  # type: ignore[arg-type]

        class Service:
            @staticmethod
            def update_order_notes(store, order_id, notes):
                return (store.app_id, order_id, notes)

        service = AuthorizedServiceAdapter(Service(), commands)
        store = Store(
            id=1,
            name="店铺",
            app_id="wx-service",
            group_name="",
            status="",
            last_sync_at=None,
            created_at="2026-11-12T00:00:00+00:00",
        )
        result = service.update_order_notes(store, "order-1", "备注")

        self.assertEqual(result, ("wx-service", "order-1", "备注"))
        self.assertEqual(
            client.authorized[0][1:],
            ("orders.edit", "wx-service"),
        )

    def test_web_session_adapter_authorizes_login(self) -> None:
        client = FakeClient()
        commands = CloudCommandClient(client, "access")  # type: ignore[arg-type]

        class Session:
            @staticmethod
            def interactive_login(store):
                return store.app_id

        session = AuthorizedWebSessionAdapter(Session(), commands)
        store = Store(
            id=1,
            name="店铺",
            app_id="wx-login",
            group_name="",
            status="",
            last_sync_at=None,
            created_at="2026-11-12T00:00:00+00:00",
        )
        self.assertEqual(session.interactive_login(store), "wx-login")
        self.assertEqual(
            client.authorized[0][1:],
            ("stores.login", "wx-login"),
        )

    def test_database_adapter_commits_workbench_to_cloud(self) -> None:
        client = FakeClient()
        commands = CloudCommandClient(client, "access")  # type: ignore[arg-type]

        class Db:
            def __init__(self):
                self.row = None

            def get_store(self, _store_id):
                return Store(
                    id=1,
                    name="店铺",
                    app_id="wx-note",
                    group_name="",
                    status="",
                    last_sync_at=None,
                    created_at="2026-11-12T00:00:00+00:00",
                )

            def get_order_workbench_row(self, *_args):
                return dict(self.row) if self.row else None

            def update_order_workbench(self, _store_id, _order_id, **changes):
                self.row = {**(self.row or {}), **changes}
                return dict(self.row)

            def delete_order_workbench_row(self, *_args):
                self.row = None

        db = Db()
        adapter = AuthorizedDatabaseAdapter(db, commands)
        result = adapter.update_order_workbench(
            1,
            "order-1",
            note="跨电脑备注",
        )

        self.assertEqual(result["note"], "跨电脑备注")
        self.assertEqual(client.workbench_rows[0]["order_id"], "order-1")
        self.assertEqual(
            client.workbench_rows[0]["payload"]["note"],
            "跨电脑备注",
        )

    def test_database_adapter_rolls_back_when_cloud_workbench_fails(
        self,
    ) -> None:
        client = FakeClient()
        client.workbench_fail = True
        commands = CloudCommandClient(client, "access")  # type: ignore[arg-type]

        class Db:
            def __init__(self):
                self.row = {"note": "旧备注"}

            def get_store(self, _store_id):
                return Store(
                    id=1,
                    name="店铺",
                    app_id="wx-note",
                    group_name="",
                    status="",
                    last_sync_at=None,
                    created_at="2026-11-12T00:00:00+00:00",
                )

            def get_order_workbench_row(self, *_args):
                return dict(self.row)

            def update_order_workbench(self, _store_id, _order_id, **changes):
                for key, value in changes.items():
                    if value is not None:
                        self.row[key] = value
                return dict(self.row)

            def delete_order_workbench_row(self, *_args):
                self.row = None

        db = Db()
        adapter = AuthorizedDatabaseAdapter(db, commands)
        with self.assertRaisesRegex(RuntimeError, "workbench unavailable"):
            adapter.update_order_workbench(
                1,
                "order-1",
                note="失败备注",
            )
        self.assertEqual(db.row["note"], "旧备注")

    def test_database_adapter_syncs_shared_setting(self) -> None:
        client = FakeClient()
        commands = CloudCommandClient(client, "access")  # type: ignore[arg-type]

        class Db:
            def __init__(self):
                self.settings = {}

            def get_setting(self, key, default=""):
                return self.settings.get(key, default)

            def set_setting(self, key, value):
                self.settings[key] = value

            def delete_setting(self, key):
                self.settings.pop(key, None)

        db = Db()
        adapter = AuthorizedDatabaseAdapter(db, commands)
        adapter.set_setting("store_page_size", "200")

        self.assertEqual(db.settings["store_page_size"], "200")
        self.assertEqual(
            client.settings_rows[0],
            {"key": "store_page_size", "value": "200"},
        )

    def test_database_adapter_keeps_device_cursor_local(self) -> None:
        client = FakeClient()
        commands = CloudCommandClient(client, "access")  # type: ignore[arg-type]

        class Db:
            def __init__(self):
                self.settings = {}

            def get_setting(self, key, default=""):
                return self.settings.get(key, default)

            def set_setting(self, key, value):
                self.settings[key] = value

            def delete_setting(self, key):
                self.settings.pop(key, None)

        db = Db()
        adapter = AuthorizedDatabaseAdapter(db, commands)
        adapter.set_setting("auto_refresh_cursor", "3")

        self.assertEqual(db.settings["auto_refresh_cursor"], "3")
        self.assertEqual(client.settings_rows, [])

    def test_large_upload_task_snapshot_never_times_out_in_cloud_settings(
        self,
    ) -> None:
        client = FakeClient()
        commands = CloudCommandClient(client, "access")  # type: ignore[arg-type]

        class Db:
            def __init__(self):
                self.settings = {}

            def get_setting(self, key, default=""):
                return self.settings.get(key, default)

            def set_setting(self, key, value):
                self.settings[key] = value

            def delete_setting(self, key):
                self.settings.pop(key, None)

        db = Db()
        adapter = AuthorizedDatabaseAdapter(db, commands)
        payload = "[" + ("x" * 2_000_000) + "]"

        adapter.set_setting("upload_tasks", payload)

        self.assertEqual(db.settings["upload_tasks"], payload)
        self.assertEqual(client.settings_rows, [])

    def test_store_add_is_rolled_back_when_cloud_snapshot_fails(self) -> None:
        client = FakeClient()
        commands = CloudCommandClient(client, "access")  # type: ignore[arg-type]

        class StoreSync:
            @staticmethod
            def pull():
                return {"created": 0, "updated": 0, "logins": 0}

            @staticmethod
            def push_current():
                raise RuntimeError("cloud unavailable")

        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "langlang.db")
            adapter = AuthorizedDatabaseAdapter(
                db,
                commands,
                StoreSync(),
            )

            with self.assertRaisesRegex(RuntimeError, "cloud unavailable"):
                adapter.add_store(
                    "不可提交店铺",
                    "wx-rollback",
                    "",
                    "测试",
                )

            self.assertEqual(db.list_stores(), [])
            self.assertFalse(client.completed[0][1])

    def test_store_add_returns_only_after_cloud_snapshot_succeeds(self) -> None:
        client = FakeClient()
        commands = CloudCommandClient(client, "access")  # type: ignore[arg-type]

        class StoreSync:
            def __init__(self) -> None:
                self.snapshots = []

            @staticmethod
            def pull():
                return {"created": 0, "updated": 0, "logins": 0}

            def push_current(self):
                self.snapshots.append("pushed")
                return {"stored": True}

        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "langlang.db")
            sync = StoreSync()
            adapter = AuthorizedDatabaseAdapter(
                db,
                commands,
                sync,
            )

            store_id = adapter.add_store(
                "跨电脑店铺",
                "wx-committed",
                "",
                "运营一组",
            )

            self.assertGreater(store_id, 0)
            self.assertEqual(sync.snapshots, ["pushed"])
            self.assertTrue(client.completed[0][1])
            self.assertEqual(db.list_stores()[0].name, "跨电脑店铺")

    def test_store_delete_fails_before_local_change_when_cloud_rejects(
        self,
    ) -> None:
        client = FakeClient()
        commands = CloudCommandClient(client, "access")  # type: ignore[arg-type]

        class StoreSync:
            @staticmethod
            def pull():
                return {}

            @staticmethod
            def delete_now(_app_ids):
                raise RuntimeError("delete rejected")

        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "langlang.db")
            store_id = db.add_store(
                "保留店铺",
                "wx-keep",
                "",
            )
            adapter = AuthorizedDatabaseAdapter(
                db,
                commands,
                StoreSync(),
            )

            with self.assertRaisesRegex(RuntimeError, "delete rejected"):
                adapter.delete_store(store_id)

            self.assertEqual(
                [store.app_id for store in db.list_stores()],
                ["wx-keep"],
            )

    def test_store_delete_uses_explicit_app_id_for_authorization(self) -> None:
        client = FakeClient()
        commands = CloudCommandClient(client, "access")  # type: ignore[arg-type]

        class StoreSync:
            @staticmethod
            def delete_now(app_ids):
                assert app_ids == ["wx-explicit"]
                return {"stored": True, "removed": 1}

        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "langlang.db")
            store_id = db.add_store(
                "明确授权店铺",
                "wx-explicit",
                "",
            )
            adapter = AuthorizedDatabaseAdapter(
                db,
                commands,
                StoreSync(),
            )

            result = adapter.delete_store_for_app_id(
                store_id,
                "wx-explicit",
            )

            self.assertEqual(result["removed"], 1)
            self.assertEqual(db.list_stores(), [])
            self.assertEqual(
                client.authorized[0][1:],
                ("stores.delete", "wx-explicit"),
            )

    def test_store_edit_rolls_back_all_local_fields_on_cloud_failure(
        self,
    ) -> None:
        client = FakeClient()
        commands = CloudCommandClient(client, "access")  # type: ignore[arg-type]

        class StoreSync:
            @staticmethod
            def pull():
                return {}

            @staticmethod
            def push_current():
                raise RuntimeError("cloud timed out")

        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "langlang.db")
            store_id = db.add_store(
                "原店铺",
                "wx-edit",
                "old-secret",
                "原分组",
            )
            db.set_web_session(
                store_id,
                {"cookies": [{"name": "sid", "value": "old-cookie"}]},
                "已登录",
                "旧登录态",
            )
            adapter = AuthorizedDatabaseAdapter(
                db,
                commands,
                StoreSync(),
            )

            with self.assertRaisesRegex(RuntimeError, "cloud timed out"):
                adapter.update_store(
                    store_id,
                    "新店铺",
                    "新分组",
                    "new-secret",
                )

            restored = db.get_store(store_id)
            self.assertIsNotNone(restored)
            self.assertEqual(restored.name, "原店铺")
            self.assertEqual(restored.group_name, "原分组")
            self.assertEqual(db.get_store_secret(store_id)[1], "old-secret")
            self.assertEqual(
                db.get_web_cookies(store_id)["cookies"][0]["value"],
                "old-cookie",
            )

    def test_scan_login_batch_pulls_and_pushes_vault_once(self) -> None:
        client = FakeClient()
        commands = CloudCommandClient(client, "access")  # type: ignore[arg-type]

        class StoreSync:
            def __init__(self) -> None:
                self.pulls = 0
                self.pushes = 0

            def pull(self, **_kwargs):
                self.pulls += 1
                return {}

            def push_current(self):
                self.pushes += 1
                return {"stored": True}

        store_sync = StoreSync()
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "langlang.db")
            first_id = db.add_store("店铺一", "wx-batch-one", "")
            second_id = db.add_store("店铺二", "wx-batch-two", "")
            adapter = AuthorizedDatabaseAdapter(
                db,
                commands,
                store_sync,
            )

            adapter.begin_store_login_batch()
            adapter.set_web_session(
                first_id,
                {"cookies": [{"name": "sid", "value": "one"}]},
                "已登录",
                "逐店登录",
            )
            adapter.set_web_session(
                second_id,
                {"cookies": [{"name": "sid", "value": "two"}]},
                "已登录",
                "逐店登录",
            )
            result = adapter.end_store_login_batch()

            self.assertEqual(store_sync.pulls, 1)
            self.assertEqual(store_sync.pushes, 1)
            self.assertTrue(result["stored"])
            self.assertEqual(client.authorized, [])
            self.assertEqual(
                db.get_store(first_id).web_status,
                "已登录",
            )
            self.assertEqual(
                db.get_store(second_id).web_status,
                "已登录",
            )


if __name__ == "__main__":
    unittest.main()
