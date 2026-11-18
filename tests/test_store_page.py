from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from weimi_manager.account_store_sync import (
    decrypt_store_snapshot,
    derive_vault_key,
)
from weimi_manager.db import Database
from weimi_manager.ui.store_page import StorePage


class StorePageContractTests(unittest.TestCase):
    def test_store_metrics_are_cached_between_rapid_refreshes(self) -> None:
        page = object.__new__(StorePage)
        page.db = SimpleNamespace(
            store_metric_map=mock.Mock(
                side_effect=[
                    {1: {"orders_today": 1}},
                    {1: {"orders_today": 2}},
                ]
            )
        )
        page._metrics_by_id = {}
        page._metrics_cache_at = 0.0
        with mock.patch(
            "weimi_manager.ui.store_page.time.monotonic",
            side_effect=[100.0, 100.5, 101.0],
        ):
            page._refresh_metrics_if_due()
            page._refresh_metrics_if_due()
            page.invalidate_metrics_cache()
            page._refresh_metrics_if_due()
        self.assertEqual(page.db.store_metric_map.call_count, 2)
        self.assertEqual(page._metrics_by_id[1]["orders_today"], 2)

    def test_incremental_store_refresh_is_coalesced(self) -> None:
        scheduled = []
        page = object.__new__(StorePage)
        page.tk = object()
        page.winfo_ismapped = lambda: True
        page._data_refresh_job = None
        page.after = lambda delay, callback: (
            scheduled.append((delay, callback)) or "job-1"
        )
        page._schedule_incremental_refresh()
        page._schedule_incremental_refresh()
        self.assertEqual(len(scheduled), 1)
        self.assertEqual(
            scheduled[0][0],
            StorePage._INCREMENTAL_REFRESH_MS,
        )

    def test_encrypted_store_export_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = Database(root / "source.db")
            first_id = source.add_store(
                "店铺一",
                "wx-export-one",
                "secret-one",
            )
            source.add_store(
                "店铺二",
                "wx-export-two",
                "secret-two",
            )
            source.set_web_session(
                first_id,
                {
                    "cookies": [
                        {"name": "session", "value": "cookie-value"}
                    ]
                },
                "已登录",
                "测试",
            )
            account_id = "zpfly-account"
            key = derive_vault_key("master-password", account_id)
            page = object.__new__(StorePage)
            page.db = source
            page.window = SimpleNamespace(
                auth_context=SimpleNamespace(
                    account_id=account_id,
                    vault_key=key,
                )
            )

            envelope = page._encrypted_store_export(
                source.list_stores()
            )
            serialized = json.dumps(envelope)
            self.assertNotIn("secret-one", serialized)
            self.assertNotIn("cookie-value", serialized)
            snapshot = decrypt_store_snapshot(
                envelope,
                key,
                account_id,
            )
            target = Database(root / "target.db")
            imported = target.import_store_accounts(snapshot)
            stores = target.list_stores()

        self.assertEqual(imported["created"], 2)
        self.assertEqual(imported["logins"], 1)
        self.assertEqual(
            {store.app_id for store in stores},
            {"wx-export-one", "wx-export-two"},
        )

    def test_refresh_pulls_cloud_stores_before_refreshing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "stores.db")
            db.add_store("本机店铺", "wx-local", "local-secret")
            probed: list[str] = []
            refreshed: list[str] = []

            class AccountSync:
                @staticmethod
                def pull():
                    db.add_store(
                        "其他电脑店铺",
                        "wx-remote",
                        "remote-secret",
                    )
                    return {"created": 1, "updated": 0, "logins": 1}

            class FakeWindow:
                account_store_sync = AccountSync()

                @staticmethod
                def set_status(*_args, **_kwargs):
                    return None

                @staticmethod
                def _post(callback, *args):
                    callback(*args)

                @staticmethod
                def store_data_changed():
                    return None

                @staticmethod
                def run_task(work, done, _error, **kwargs):
                    result = work(None)
                    done(result)
                    return True

            page = object.__new__(StorePage)
            page.db = db
            page.window = FakeWindow()
            page.window.web_session = SimpleNamespace(
                probe=lambda store: probed.append(store.app_id)
            )
            page.window.service = SimpleNamespace(
                refresh_store_metrics=lambda store: refreshed.append(
                    store.app_id
                )
            )
            page._selected_stores = lambda: []
            page.refresh = lambda: None

            page._refresh_store_data()

        self.assertCountEqual(
            probed,
            ["wx-local", "wx-remote"],
        )
        self.assertCountEqual(
            refreshed,
            ["wx-local", "wx-remote"],
        )

    def test_empty_store_view_reloads_database_from_disk(self) -> None:
        expected = [
            SimpleNamespace(id=1, name="店铺一"),
            SimpleNamespace(id=2, name="店铺二"),
        ]

        class EmptyDb:
            @staticmethod
            def list_stores():
                return []

        class FreshDb:
            @staticmethod
            def list_stores():
                return expected

        class FakeWindow:
            @staticmethod
            def reload_account_database():
                return FreshDb()

        page = object.__new__(StorePage)
        page.db = EmptyDb()
        page.window = FakeWindow()

        loaded = page._load_stores_for_refresh()

        self.assertEqual(loaded, expected)
        self.assertIsInstance(page.db, FreshDb)

    def test_empty_database_object_falls_back_to_direct_sqlite_rows(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            real_db = Database(Path(tmp) / "stores.db")
            real_db.add_store("磁盘店铺", "wx-direct", "secret")

            class EmptyDb:
                path = real_db.path

                @staticmethod
                def list_stores():
                    return []

            class FakeWindow:
                @staticmethod
                def reload_account_database():
                    return None

            page = object.__new__(StorePage)
            page.db = EmptyDb()
            page.window = FakeWindow()

            loaded = page._load_stores_for_refresh()

            self.assertEqual([store.name for store in loaded], ["磁盘店铺"])

    def test_store_page_has_all_original_fields_in_order(self) -> None:
        self.assertEqual(
            [label for _key, label, _width in StorePage.field_definitions],
            [
                "店铺名称",
                "分组",
                "AppID",
                "AppSecret",
                "登录状态",
                "在售",
                "仓库",
                "全部商品",
                "提审额度",
                "今日成交",
                "昨日成交",
                "7日成交",
                "30日成交",
                "今日买家",
                "30日买家",
                "今日订单",
                "昨日订单",
                "商品点击",
                "30日访客",
                "今日退款",
                "30日退款",
                "售后",
                "店铺预警",
                "店铺违规",
                "可提现",
                "暂预留",
                "待结算",
                "保证金",
                "体验分",
                "商品体验",
                "物流体验",
                "服务体验",
                "商品好评率",
                "品质退款率",
                "待发货",
                "待收货",
                "推荐曝光",
                "昨日推荐曝光",
                "昨日搜索曝光",
                "今日成功",
                "数据更新",
            ],
        )
        self.assertEqual(len(StorePage.field_definitions), 41)

    def test_compact_columns_match_original(self) -> None:
        self.assertEqual(
            StorePage.compact_fields,
            (
                "name",
                "group",
                "app_id",
                "secret",
                "in_sale",
                "quota",
                "gmv_today",
                "gmv_7d",
                "gmv_30d",
                "login",
            ),
        )

    def test_table_formatters(self) -> None:
        self.assertEqual(StorePage._format_count(4722), "4,722")
        self.assertEqual(StorePage._format_score(4.7), "4.70")
        self.assertEqual(StorePage._format_percent(0.0), "0%")
        self.assertEqual(
            StorePage._format_quota(
                {"quota_remaining": 500, "quota_total": 500}
            ),
            "500/500",
        )
        self.assertEqual(
            StorePage._format_quota(
                {
                    "quota_remaining": 0,
                    "quota_total": 0,
                    "quota_error": "permission denied",
                }
            ),
            "获取失败",
        )

    def test_right_click_backend_uses_exact_store(self) -> None:
        target = SimpleNamespace(
            id=9,
            name="右键目标店铺",
        )
        wrong = SimpleNamespace(
            id=3,
            name="错误勾选店铺",
        )
        opened = []

        class FakeWebSession:
            @staticmethod
            def open_official_backend(store):
                opened.append(store)

        class FakeWindow:
            web_session = FakeWebSession()

            @staticmethod
            def set_status(*_args, **_kwargs) -> None:
                pass

            @staticmethod
            def store_data_changed() -> None:
                pass

            @staticmethod
            def run_task(
                work,
                done,
                _error,
                progress=None,
                log_title="",
                **_kwargs,
            ) -> bool:
                done(work(progress))
                return True

        page = object.__new__(StorePage)
        page.window = FakeWindow()
        page._primary_store = lambda: wrong
        page._open_store_backend(target)
        self.assertEqual(opened, [target])

    def test_store_metric_refresh_runs_stores_concurrently(self) -> None:
        stores = [
            SimpleNamespace(id=1, name="店铺一"),
            SimpleNamespace(id=2, name="店铺二"),
            SimpleNamespace(id=3, name="店铺三"),
        ]
        state = {
            "active": 0,
            "max_active": 0,
            "lock": threading.Lock(),
        }

        class FakeService:
            @staticmethod
            def refresh_store_metrics(_store):
                with state["lock"]:
                    state["active"] += 1
                    state["max_active"] = max(
                        state["max_active"],
                        state["active"],
                    )
                try:
                    time.sleep(0.04)
                finally:
                    with state["lock"]:
                        state["active"] -= 1
                return {}

        class FakeWebSession:
            @staticmethod
            def probe(_store):
                return "已登录", "ok"

        class FakeWindow:
            service = FakeService()
            web_session = FakeWebSession()

            @staticmethod
            def set_status(*_args, **_kwargs) -> None:
                pass

            @staticmethod
            def store_data_changed() -> None:
                pass

            @staticmethod
            def _post(*_args, **_kwargs) -> None:
                pass

            @staticmethod
            def run_task(
                work,
                done,
                _error,
                progress=None,
                log_title="",
                **_kwargs,
            ) -> bool:
                done(work(progress))
                return True

        page = object.__new__(StorePage)
        page.window = FakeWindow()
        page._refresh_store_data(stores)
        self.assertGreaterEqual(state["max_active"], 2)

    def test_store_refresh_and_scan_use_independent_task_scopes(self) -> None:
        store = SimpleNamespace(
            id=7,
            name="测试店铺",
            web_status="未登录",
            app_id="wx-test-store",
        )
        scopes = []

        class FakeWindow:
            @staticmethod
            def set_status(*_args, **_kwargs) -> None:
                pass

            @staticmethod
            def _post(*_args, **_kwargs) -> None:
                pass

            @staticmethod
            def store_data_changed() -> None:
                pass

            @staticmethod
            def run_task(
                _work,
                _done,
                _error,
                progress=None,
                log_title="",
                task_scope="",
                **_kwargs,
            ) -> bool:
                scopes.append(task_scope)
                return True

        page = object.__new__(StorePage)
        page.window = FakeWindow()
        page.db = SimpleNamespace(list_stores=lambda: [store])
        page._selected_stores = lambda: [store]

        page._refresh_store_data([store])
        page._scan_login()

        self.assertEqual(
            scopes,
            ["stores:refresh-data", "stores:scan-login"],
        )

    def test_delete_store_queues_cloud_removal(self) -> None:
        store = SimpleNamespace(
            id=9,
            name="待删除店铺",
            app_id="wx-delete-store",
        )
        deleted_ids: list[int] = []
        cloud_deleted: list[list[str]] = []
        commands: list[tuple[str, list[str]]] = []

        class FakeWindow:
            account_store_sync = SimpleNamespace(
                delete_now=lambda app_ids: cloud_deleted.append(app_ids)
                or {"stored": True, "removed": len(app_ids)}
            )

            @staticmethod
            def store_data_changed():
                return None

            @staticmethod
            def set_status(*_args, **_kwargs):
                return None

            @staticmethod
            def run_task(
                work,
                done,
                _error,
                progress=None,
                log_title="",
                task_scope="",
                **kwargs,
            ):
                commands.append(
                    (
                        kwargs.get("command", ""),
                        kwargs.get("store_app_ids", []),
                    )
                )
                done(work(progress))
                return True

        page = object.__new__(StorePage)
        page.window = FakeWindow()
        page.db = SimpleNamespace(
            delete_store=lambda store_id: deleted_ids.append(store_id)
        )
        page._checked_ids = {9}
        page._selected_stores = lambda: [store]
        page._has_permission = lambda _permission: True
        with mock.patch(
            "weimi_manager.ui.store_page.messagebox.askyesno",
            return_value=True,
        ):
            page._delete_store()

        self.assertEqual(deleted_ids, [9])
        self.assertEqual(cloud_deleted, [["wx-delete-store"]])
        # The database adapter now owns command authorization and audit.
        self.assertEqual(commands, [("", [])])
        self.assertEqual(page._checked_ids, set())

    def test_delete_store_prefers_explicit_app_id_adapter(self) -> None:
        store = SimpleNamespace(
            id=11,
            name="明确 AppID 店铺",
            app_id="wx-explicit-delete",
        )
        calls: list[tuple[int, str]] = []

        class FakeWindow:
            account_store_sync = SimpleNamespace(
                delete_now=lambda _app_ids: (_ for _ in ()).throw(
                    AssertionError("fallback deletion should not run")
                )
            )

            @staticmethod
            def store_data_changed():
                return None

            @staticmethod
            def set_status(*_args, **_kwargs):
                return None

            @staticmethod
            def run_task(work, done, _error, progress=None, **_kwargs):
                done(work(progress))
                return True

        page = object.__new__(StorePage)
        page.window = FakeWindow()
        page.db = SimpleNamespace(
            delete_store_for_app_id=lambda store_id, app_id: (
                calls.append((store_id, app_id))
                or {"stored": True, "removed": 1}
            )
        )
        page._checked_ids = {11}
        page._selected_stores = lambda: [store]
        page._has_permission = lambda _permission: True
        with mock.patch(
            "weimi_manager.ui.store_page.messagebox.askyesno",
            return_value=True,
        ):
            page._delete_store()

        self.assertEqual(calls, [(11, "wx-explicit-delete")])

    def test_delete_store_keeps_local_store_when_cloud_delete_fails(
        self,
    ) -> None:
        store = SimpleNamespace(
            id=10,
            name="云端删除失败",
            app_id="wx-cloud-failure",
        )
        deleted_ids: list[int] = []
        errors: list[str] = []

        class FakeWindow:
            account_store_sync = SimpleNamespace(
                delete_now=lambda _app_ids: (_ for _ in ()).throw(
                    RuntimeError("offline")
                )
            )

            @staticmethod
            def store_data_changed():
                return None

            @staticmethod
            def set_status(*_args, **_kwargs):
                return None

            @staticmethod
            def run_task(
                work,
                done,
                error,
                progress=None,
                log_title="",
                task_scope="",
                **kwargs,
            ):
                try:
                    done(work(progress))
                except Exception as exc:
                    errors.append(str(exc))
                    error(str(exc))
                return True

        page = object.__new__(StorePage)
        page.window = FakeWindow()
        page.db = SimpleNamespace(
            delete_store=lambda store_id: deleted_ids.append(store_id)
        )
        page._checked_ids = {10}
        page._selected_stores = lambda: [store]
        page._has_permission = lambda _permission: True
        with mock.patch(
            "weimi_manager.ui.store_page.messagebox.askyesno",
            return_value=True,
        ), mock.patch(
            "weimi_manager.ui.store_page.messagebox.showerror",
        ):
            page._delete_store()

        self.assertEqual(errors, ["offline"])
        self.assertEqual(deleted_ids, [])
        self.assertEqual(page._checked_ids, {10})


if __name__ == "__main__":
    unittest.main()
