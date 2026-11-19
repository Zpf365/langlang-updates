from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import unittest
import zipfile
from dataclasses import replace
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from weimi_manager.dashboard_metrics import build_dashboard_snapshot
from weimi_manager.db import Database
from weimi_manager.exporter import export_csv, export_xlsx
from weimi_manager.models import Order, OrderItem, Product, Store
from weimi_manager.procurement import normalize_procurement_records
from weimi_manager.security import CredentialProtector
from weimi_manager.service import SyncService
from weimi_manager.shop_private import ShopPrivateClient, ShopPrivateError
from weimi_manager.ui.app import MainWindow
from weimi_manager.updater import check_for_update
from weimi_manager.web_session import (
    WebSessionError,
    WechatWebSession,
    cookie_header,
    matching_identities,
    normalize_cookies,
    parse_cookie_header,
)
from weimi_manager.wechat_api import WechatShopClient, parse_order, parse_product


class CoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = Database(self.root / "test.db")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_secret_is_encrypted_and_round_trips(self) -> None:
        protector = CredentialProtector()
        encrypted = protector.protect("not-a-real-secret")
        self.assertNotIn(b"not-a-real-secret", encrypted)
        self.assertEqual(protector.unprotect(encrypted), "not-a-real-secret")

    def test_store_crud_and_cascade(self) -> None:
        store_id = self.db.add_store("测试店铺", "wx-test-001", "secret-value", "A组")
        store = self.db.get_store(store_id)
        self.assertIsNotNone(store)
        self.assertEqual(store.name, "测试店铺")
        self.assertEqual(self.db.get_store_secret(store_id)[1], "secret-value")
        self.assertNotIn("secret", repr(store))

        order = sample_order(store_id, "ORDER-1")
        self.db.upsert_order(order)
        self.assertEqual(len(self.db.list_orders(store_id=store_id)), 1)
        self.db.delete_store(store_id)
        self.assertEqual(self.db.list_orders(store_id=store_id), [])

    def test_store_revenue_excludes_unclaimed_gifts_and_non_sales(self) -> None:
        store_id = self.db.add_store(
            "成交口径测试",
            "wx-transaction-status",
            "secret",
        )
        base = sample_order(store_id, "ORDER-BASE")
        now = int(time.time())
        cases = (
            (20, 10000),
            (30, 5000),
            (100, 2500),
            (12, 50000),
            (200, 7000),
            (250, 3000),
            (10, 2000),
            (13, 4000),
        )
        for index, (status, amount) in enumerate(cases, 1):
            self.db.upsert_order(
                replace(
                    base,
                    order_id=f"ORDER-{index}",
                    status=status,
                    create_time=now,
                    update_time=now,
                    total_amount=amount,
                )
            )

        summary = self.db.store_summary(store_id)
        grouped = self.db.store_summary_map()[store_id]

        self.assertEqual(summary["today_revenue"], 17500)
        self.assertEqual(summary["today_orders"], 3)
        self.assertEqual(summary["week_revenue"], 17500)
        self.assertEqual(summary["month_revenue"], 17500)
        self.assertEqual(grouped["today_revenue"], 17500)
        self.assertEqual(grouped["today_orders"], 3)
        self.assertEqual(grouped["week_revenue"], 17500)
        self.assertEqual(grouped["month_revenue"], 17500)

    def test_store_metrics_do_not_replace_sales_with_platform_gift_gmv(
        self,
    ) -> None:
        store_id = self.db.add_store(
            "指标口径测试",
            "wx-metric-status",
            "secret",
        )
        base = sample_order(store_id, "ORDER-BASE")
        now = int(time.time())
        cases = (
            (20, 10000),
            (30, 5000),
            (100, 2500),
            (12, 50000),
            (200, 7000),
        )
        for index, (status, amount) in enumerate(cases, 1):
            self.db.upsert_order(
                replace(
                    base,
                    order_id=f"METRIC-{index}",
                    status=status,
                    create_time=now,
                    update_time=now,
                    total_amount=amount,
                )
            )
        store = self.db.get_store(store_id)
        assert store is not None
        service = SyncService(self.db)

        class FakePrivateClient:
            def __init__(self, _db, _store) -> None:
                pass

            def realtime_overview(self):
                return {
                    "pay_gmv": 999999,
                    "pay_uv": 88,
                    "pay_order_cnt": 66,
                    "pay_refund_gmv": 0,
                    "product_click_uv": 2,
                }

            def todo_overview(self):
                return {}

            def score_overview(self):
                return {}

            def evaluation_overview(self):
                return {}

            def account_balance(self):
                return {}

            def deposit_balance(self):
                return 0

            def recommend_overview(self, _yesterday):
                return {"exposure": 0}

            def search_overview(self, _days):
                return {"search_exposure_yesterday": 0}

            def audit_success_today(self):
                return 0

        with (
            patch.object(
                service,
                "_ensure_today_orders_cached",
                return_value=False,
            ),
            patch.object(
                service,
                "_analytics_days_for_metrics",
                return_value=[],
            ),
            patch.object(
                service,
                "fetch_product_audit_quota",
                return_value={},
            ),
            patch.object(
                service,
                "client_for",
                side_effect=ValueError("web-only store"),
            ),
            patch(
                "weimi_manager.service.ShopPrivateClient",
                FakePrivateClient,
            ),
        ):
            metrics = service.refresh_store_metrics(store)

        self.assertEqual(metrics["gmv_today"], 17500)
        self.assertEqual(metrics["gmv_7d"], 17500)
        self.assertEqual(metrics["gmv_30d"], 17500)
        self.assertEqual(metrics["today_orders"], 3)

    def test_empty_store_page_schedules_followup_refresh(self) -> None:
        tree = SimpleNamespace(children=[])
        refresh_calls = []
        scheduled = []

        class FakeStorePage:
            def __init__(self) -> None:
                self.tree = SimpleNamespace(get_children=lambda: list(tree.children))

            def refresh(self) -> None:
                refresh_calls.append(len(refresh_calls) + 1)
                if len(refresh_calls) >= 2:
                    tree.children = ["row-1"]

        fake_window = SimpleNamespace(
            store_page=FakeStorePage(),
            after=lambda delay, callback: scheduled.append((delay, callback)),
        )

        MainWindow._ensure_store_page_loaded(fake_window, attempt=0)
        self.assertEqual(refresh_calls, [1])
        self.assertEqual(len(scheduled), 1)
        MainWindow._ensure_store_page_loaded(fake_window, attempt=1)

        self.assertEqual(refresh_calls, [1, 2])
        self.assertEqual(
            fake_window.store_page.tree.get_children(),
            ["row-1"],
        )

    def test_startup_rebuilds_store_page_even_with_stale_rows(self) -> None:
        refresh_calls = []

        class FakeStorePage:
            def __init__(self) -> None:
                self.tree = SimpleNamespace(get_children=lambda: ["stale-row"])

            def refresh(self) -> None:
                refresh_calls.append(True)

        fake_window = SimpleNamespace(
            store_page=FakeStorePage(),
            after=lambda _delay, _callback: None,
        )

        MainWindow._ensure_store_page_loaded(fake_window, attempt=0)

        self.assertEqual(refresh_calls, [True])

    def test_store_group_management_persists_empty_and_assigned_groups(
        self,
    ) -> None:
        self.db.create_store_group("待分配")
        first = self.db.add_store("店铺一", "wx-group-001", "secret", "重点")
        second = self.db.add_store("店铺二", "wx-group-002", "secret", "重点")

        self.assertEqual(
            self.db.list_store_groups(),
            ["待分配", "重点"],
        )

        self.db.rename_store_group("重点", "核心")
        self.assertEqual(self.db.list_store_groups(), ["待分配", "核心"])
        self.assertEqual(self.db.get_store(first).group_name, "核心")
        self.assertEqual(self.db.get_store(second).group_name, "核心")

        self.db.delete_store_group("核心")
        self.assertEqual(self.db.list_store_groups(), ["待分配"])
        self.assertEqual(self.db.get_store(first).group_name, "")
        self.assertEqual(self.db.get_store(second).group_name, "")

    def test_existing_store_groups_are_registered_during_database_upgrade(
        self,
    ) -> None:
        store_id = self.db.add_store(
            "旧版分组店铺",
            "wx-legacy-group",
            "secret",
            "旧版分组",
        )
        with self.db.connect() as conn:
            conn.execute("DROP TABLE store_groups")

        upgraded = Database(self.root / "test.db")

        self.assertEqual(upgraded.list_store_groups(), ["旧版分组"])
        self.assertEqual(upgraded.get_store(store_id).group_name, "旧版分组")

    def test_web_session_is_encrypted_and_status_is_separate(self) -> None:
        store_id = self.db.add_store("网页登录测试", "wx-web-001", "secret")
        payload = {
            "cookies": [
                {"name": "session_id", "value": "not-a-real-cookie"},
                {"name": "shop_id", "value": "10001"},
            ],
            "source_url": "https://store.weixin.qq.com/shop",
        }
        self.db.set_web_session(store_id, payload, "已登录", "扫码登录成功")
        store = self.db.get_store(store_id)
        self.assertIsNotNone(store)
        self.assertEqual(store.status, "未验证")
        self.assertEqual(store.web_status, "已登录")
        restored = self.db.get_web_cookies(store_id)
        self.assertIsNotNone(restored)
        self.assertEqual(
            cookie_header(restored["cookies"]), "session_id=not-a-real-cookie; shop_id=10001"
        )
        self.assertEqual(
            parse_cookie_header("a=1; b=2"),
            [{"name": "a", "value": "1"}, {"name": "b", "value": "2"}],
        )
        self.assertEqual(
            len(normalize_cookies({"a": "1", "b": "2"})),
            2,
        )
        self.db.update_web_status(store_id, "已掉线", "Cookie 已过期")
        store = self.db.get_store(store_id)
        self.assertEqual(store.web_status, "已掉线")
        self.db.clear_web_session(store_id, "身份不匹配，已清除")
        self.db.update_web_status(store_id, "已掉线", "身份不匹配，请重新扫码")
        status, detail = WechatWebSession(self.db, self.root).probe(store)
        self.assertEqual(status, "已掉线")
        self.assertIn("身份不匹配", detail)

    def test_selected_web_sessions_only_clear_logged_in_selected_stores(
        self,
    ) -> None:
        selected_logged = self.db.add_store(
            "所选已登录",
            "wx-selected-logged",
            "secret-one",
        )
        selected_unlogged = self.db.add_store(
            "所选未登录",
            "wx-selected-unlogged",
            "secret-two",
        )
        unselected_logged = self.db.add_store(
            "未选已登录",
            "wx-unselected-logged",
            "secret-three",
        )
        for store_id in (selected_logged, unselected_logged):
            self.db.set_web_session(
                store_id,
                {
                    "cookies": [
                        {"name": "session", "value": f"cookie-{store_id}"}
                    ]
                },
                "已登录",
                "测试登录",
            )

        result = self.db.clear_selected_web_sessions(
            [selected_logged, selected_unlogged],
            "已离线所选店铺",
        )

        self.assertEqual(result, {"cleared": 1, "skipped": 1})
        selected = self.db.get_store(selected_logged)
        skipped = self.db.get_store(selected_unlogged)
        untouched = self.db.get_store(unselected_logged)
        self.assertEqual(selected.web_status, "未登录")
        self.assertEqual(selected.web_status_detail, "已离线所选店铺")
        self.assertIsNone(self.db.get_web_cookies(selected_logged))
        self.assertEqual(skipped.web_status, "未登录")
        self.assertEqual(untouched.web_status, "已登录")
        self.assertIsNotNone(self.db.get_web_cookies(unselected_logged))

    def test_clear_all_web_logins_updates_stores_and_procurement_in_batch(
        self,
    ) -> None:
        first_store = self.db.add_store(
            "全部离线一",
            "wx-clear-all-one",
            "secret-one",
        )
        second_store = self.db.add_store(
            "全部离线二",
            "wx-clear-all-two",
            "secret-two",
        )
        for store_id in (first_store, second_store):
            self.db.set_web_session(
                store_id,
                {"cookies": [{"name": "sid", "value": "cookie"}]},
                "已登录",
                "测试登录",
            )
        procurement_id = self.db.add_procurement_account(
            "1688",
            "采购账号",
        )
        self.db.set_procurement_session(
            procurement_id,
            {"cookies": [{"name": "sid", "value": "cookie"}]},
            "已登录",
        )

        result = self.db.clear_all_web_logins("账号中心批量清理")

        self.assertEqual(
            result,
            {"stores_cleared": 2, "procurement_cleared": 1},
        )
        for store_id in (first_store, second_store):
            self.assertEqual(self.db.get_store(store_id).web_status, "未登录")
            self.assertIsNone(self.db.get_web_cookies(store_id))
        self.assertEqual(
            self.db.get_procurement_account(procurement_id)["status"],
            "未登录",
        )
        self.assertIsNone(
            self.db.get_procurement_session(procurement_id)
        )

    def test_account_snapshot_clears_cookie_removed_on_another_device(
        self,
    ) -> None:
        source = Database(self.root / "snapshot-source.db")
        target = Database(self.root / "snapshot-target.db")
        source_id = source.add_store(
            "同步离线店铺",
            "wx-snapshot-offline",
            "secret",
        )
        target_id = target.add_store(
            "同步离线店铺",
            "wx-snapshot-offline",
            "secret",
        )
        for db, store_id in ((source, source_id), (target, target_id)):
            db.set_web_session(
                store_id,
                {"cookies": [{"name": "sid", "value": "cookie"}]},
                "已登录",
                "测试登录",
            )

        source.clear_selected_web_sessions(
            [source_id],
            "已离线所选店铺",
        )
        target.import_store_accounts(source.export_store_accounts())

        synced = target.get_store(target_id)
        self.assertEqual(synced.web_status, "未登录")
        self.assertEqual(synced.web_status_detail, "已离线所选店铺")
        self.assertIsNone(target.get_web_cookies(target_id))

    def test_account_snapshot_unlogged_status_overrides_stale_cookie(
        self,
    ) -> None:
        source = Database(self.root / "stale-cookie-source.db")
        target = Database(self.root / "stale-cookie-target.db")
        source_id = source.add_store(
            "快照残留 Cookie",
            "wx-snapshot-stale-cookie",
            "secret",
        )
        target_id = target.add_store(
            "快照残留 Cookie",
            "wx-snapshot-stale-cookie",
            "secret",
        )
        for db, store_id in ((source, source_id), (target, target_id)):
            db.set_web_session(
                store_id,
                {"cookies": [{"name": "sid", "value": "cookie"}]},
                "已登录",
                "测试登录",
            )
        snapshot = source.export_store_accounts()
        snapshot["stores"][0]["web_status"] = "未登录"
        snapshot["stores"][0]["web_status_detail"] = "快照明确离线"

        target.import_store_accounts(snapshot)

        synced = target.get_store(target_id)
        self.assertEqual(synced.web_status, "未登录")
        self.assertEqual(synced.web_status_detail, "快照明确离线")
        self.assertIsNone(target.get_web_cookies(target_id))

    def test_newer_local_login_survives_older_snapshot_import(
        self,
    ) -> None:
        source = Database(self.root / "older-snapshot-source.db")
        target = Database(self.root / "newer-login-target.db")
        source_id = source.add_store(
            "并发保护店铺",
            "wx-newer-local-login",
            "secret",
        )
        target_id = target.add_store(
            "并发保护店铺",
            "wx-newer-local-login",
            "secret",
        )
        source.set_web_session(
            source_id,
            {"cookies": [{"name": "sid", "value": "old-cookie"}]},
            "已登录",
            "旧服务器登录",
        )
        old_snapshot = source.export_store_accounts()
        old_snapshot["stores"][0]["web_session_updated_at"] = (
            "2026-01-01T00:00:00+00:00"
        )
        target.set_web_session(
            target_id,
            {"cookies": [{"name": "sid", "value": "new-cookie"}]},
            "已登录",
            "刚扫码登录",
        )

        target.import_store_accounts(old_snapshot)

        synced = target.get_store(target_id)
        self.assertEqual(synced.web_status, "已登录")
        self.assertEqual(synced.web_status_detail, "刚扫码登录")
        self.assertEqual(
            target.get_web_cookies(target_id)["cookies"][0]["value"],
            "new-cookie",
        )

    def test_reopen_preserves_store_secret_and_web_cookie(self) -> None:
        database_path = self.root / "stable" / "weimi_manager.db"
        first = Database(database_path)
        store_id = first.add_store(
            "升级保留测试",
            "wx-upgrade-preserve",
            "upgrade-secret",
        )
        first.set_web_session(
            store_id,
            {
                "cookies": [
                    {"name": "session_id", "value": "upgrade-cookie"},
                    {"name": "shop_id", "value": "preserved-shop"},
                ],
                "source_url": "https://store.weixin.qq.com/shop",
            },
            "已登录",
            "升级前登录状态",
        )

        reopened = Database(database_path)
        store = reopened.get_store(store_id)
        self.assertIsNotNone(store)
        self.assertEqual(store.web_status, "已登录")
        self.assertEqual(
            reopened.get_store_secret(store_id)[1],
            "upgrade-secret",
        )
        restored = reopened.get_web_cookies(store_id)
        self.assertIsNotNone(restored)
        self.assertEqual(
            cookie_header(restored["cookies"]),
            "session_id=upgrade-cookie; shop_id=preserved-shop",
        )

    def test_web_session_rejects_cookie_for_another_store(self) -> None:
        target_id = self.db.add_store(
            "允竹努胜小店",
            "wx-identity-target",
            "secret-target",
        )
        actual_id = self.db.add_store(
            "测试店铺",
            "wx-identity-actual",
            "secret-actual",
        )
        self.db.upsert_store_profile(
            target_id,
            {"nickname": "允竹努胜小店", "username": "gh_target"},
        )
        self.db.upsert_store_profile(
            actual_id,
            {"nickname": "实际账号商贸", "username": "gh_actual"},
        )
        identities = self.db.list_store_identities()
        matches = matching_identities(
            "工作台 实际账号商贸 gh_actual",
            identities,
        )
        self.assertEqual([row["store_id"] for row in matches], [actual_id])

        target = self.db.get_store(target_id)
        assert target
        web = WechatWebSession(self.db, self.root)
        payload = {
            "cookies": [
                {"name": "biz_token", "value": "token"},
                {"name": "device_id", "value": "device"},
            ]
        }
        response = SimpleNamespace(
            url="https://store.weixin.qq.com/shop/home",
            status_code=200,
            text="商品管理 订单管理 实际账号商贸 gh_actual",
        )
        with patch(
            "weimi_manager.web_session.requests.Session.get",
            return_value=response,
        ):
            identified_id, identified_name = web._identify_logged_in_store(
                payload["cookies"],
                identities,
            )
            self.assertEqual(identified_id, actual_id)
            self.assertEqual(identified_name, "测试店铺")
            status, detail = web.probe_payload(
                payload,
                store=target,
                require_identity=True,
            )
        self.assertEqual(status, "已掉线")
        self.assertIn("测试店铺", detail)
        self.assertIn("不是", detail)

        with patch(
            "weimi_manager.web_session.requests.Session.get",
            return_value=response,
        ):
            with self.assertRaises(WebSessionError):
                web.import_cookie_header(
                    target,
                    "biz_token=token; device_id=device",
                )
        self.assertIsNone(self.db.get_web_cookies(target_id))

    def test_order_upsert_replaces_items(self) -> None:
        store_id = self.db.add_store("测试店铺", "wx-test-002", "secret-value")
        store = self.db.get_store(store_id)
        assert store
        order = sample_order(store_id, "ORDER-2")
        self.db.upsert_order(order)
        order.status = 30
        order.status_text = "待收货"
        order.items = []
        self.db.upsert_order(order)
        saved = self.db.get_order(store_id, "ORDER-2")
        self.assertIsNotNone(saved)
        self.assertEqual(saved.status, 30)
        self.assertEqual(saved.items, [])

    def test_order_workbench_metadata_round_trips(self) -> None:
        store_id = self.db.add_store("工作台测试", "wx-test-workbench", "secret")
        self.db.upsert_order(sample_order(store_id, "ORDER-WORKBENCH"))
        updated = self.db.update_order_workbench(
            store_id,
            "ORDER-WORKBENCH",
            purchase_order_no="1688-001",
            pickup_code="A-1024",
            note="优先处理",
            profit=1200,
            cost=2800,
            is_marked=1,
            is_marked_sent=1,
        )
        self.assertEqual(updated["purchase_order_no"], "1688-001")
        self.db.update_order_workbench(
            store_id,
            "ORDER-WORKBENCH",
            pickup_code="A-2048",
        )
        saved = self.db.get_order_workbench_row(store_id, "ORDER-WORKBENCH")
        self.assertIsNotNone(saved)
        self.assertEqual(saved["purchase_order_no"], "1688-001")
        self.assertEqual(saved["pickup_code"], "A-2048")
        self.assertEqual(saved["profit"], 1200)
        self.assertEqual(saved["is_marked_sent"], 1)
        self.db.clear_store_cache(store_id)
        self.assertIsNone(self.db.get_order(store_id, "ORDER-WORKBENCH"))
        self.assertIsNone(self.db.get_order_workbench_row(store_id, "ORDER-WORKBENCH"))
        self.assertIsNotNone(self.db.get_store(store_id))

    def test_hosted_configuration_and_runs(self) -> None:
        store_id = self.db.add_store("托管测试", "wx-test-hosted", "secret")
        self.db.set_setting(
            "violation_blacklist",
            json.dumps([{"product_id": "BLOCKED-1"}]),
        )
        self.assertEqual(self.db.violation_blacklist_ids(), {"BLOCKED-1"})
        self.db.update_hosted_config(
            store_id,
            revoke_audit=1,
            list_products=1,
            enabled=1,
        )
        config = self.db.get_hosted_config(store_id)
        self.assertIsNotNone(config)
        self.assertEqual(config["revoke_audit"], 1)
        self.assertEqual(config["list_products"], 1)
        self.assertEqual(config["enabled"], 1)
        self.db.log_hosted_run(store_id, "撤销审核", "success", "测试")
        runs = self.db.list_hosted_runs()
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["store_name"], "托管测试")

    def test_procurement_accounts_orders_and_links(self) -> None:
        store_id = self.db.add_store("采购店铺", "wx-proc-test", "secret")
        self.db.upsert_order(sample_order(store_id, "ORDER-PROC"))
        account_id = self.db.add_procurement_account(
            "1688",
            "采购账号",
            "buyer",
        )
        self.db.set_procurement_session(
            account_id,
            {
                "cookies": [
                    {"name": "_m_h5_tk", "value": "token_123"},
                    {"name": "cookie2", "value": "value"},
                ]
            },
            "已登录",
        )
        restored = self.db.get_procurement_session(account_id)
        self.assertIsNotNone(restored)
        records = normalize_procurement_records(
            "1688",
            [
                {
                    "data": {
                        "tradeNo": "TB-001",
                        "mailNo": "SF1001",
                        "sendingCode": "A-01",
                        "itemName": "采购商品",
                        "payAmount": 1990,
                    }
                }
            ],
        )
        self.assertEqual(len(records), 1)
        procurement_id = self.db.upsert_procurement_order(
            account_id,
            "1688",
            records[0],
        )
        self.db.upsert_procurement_link(
            store_id,
            "ORDER-PROC",
            procurement_id,
            local_note="本地采购备注",
        )
        links = self.db.list_procurement_links(store_id, "ORDER-PROC")
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0]["pickup_code"], "A-01")
        self.assertEqual(links[0]["local_note"], "本地采购备注")

    def test_dashboard_uses_store_purchase_and_verified_profit_data(
        self,
    ) -> None:
        store_id = self.db.add_store(
            "大屏店铺",
            "wx-dashboard-test",
            "secret",
        )
        now = int(time.time())
        self.db.upsert_order(
            replace(
                sample_order(store_id, "DASHBOARD-ORDER"),
                create_time=now,
                total_amount=10000,
            )
        )
        account_id = self.db.add_procurement_account(
            "1688",
            "大屏采购账号",
        )
        procurement_id = self.db.upsert_procurement_order(
            account_id,
            "1688",
            {
                "external_order_id": "PURCHASE-DASHBOARD",
                "pay_amount": 6000,
            },
        )
        self.db.upsert_procurement_link(
            store_id,
            "DASHBOARD-ORDER",
            procurement_id,
        )
        self.db.update_order_workbench(
            store_id,
            "DASHBOARD-ORDER",
            profit=4000,
        )

        snapshot = build_dashboard_snapshot(
            self.db,
            "今日",
            "全部数据",
        )

        self.assertEqual(snapshot.store_orders, 1)
        self.assertEqual(snapshot.store_receipt, 10000)
        self.assertEqual(snapshot.procurement_orders, 1)
        self.assertEqual(snapshot.procurement_amount, 6000)
        self.assertEqual(snapshot.procurement_profit, 4000)
        self.assertAlmostEqual(snapshot.procurement_margin, 66.6666, places=3)
        self.assertEqual(snapshot.status_counts["待发货"], 1)
        self.assertEqual(snapshot.store_rank[0].label, "大屏店铺")
        self.assertEqual(snapshot.procurement_rank[0].label, "大屏采购账号")
        self.assertEqual(snapshot.profit_rank[0].label, "大屏店铺")
        purchase_only = build_dashboard_snapshot(
            self.db,
            "今日",
            "采购数据",
        )
        self.assertEqual(purchase_only.store_orders, 0)
        self.assertEqual(purchase_only.procurement_amount, 6000)
        store_only = build_dashboard_snapshot(
            self.db,
            "今日",
            "店铺数据",
        )
        self.assertEqual(store_only.store_receipt, 10000)
        self.assertEqual(store_only.procurement_orders, 0)

    def test_unclaimed_gift_orders_are_hidden_from_order_page(self) -> None:
        from weimi_manager.ui.pages import OrdersPage

        self.assertTrue(
            OrdersPage._is_unclaimed_gift_order(
                {
                    "status": 12,
                    "raw_json": json.dumps({"is_present": True}),
                }
            )
        )
        self.assertFalse(
            OrdersPage._is_unclaimed_gift_order(
                {
                    "status": 20,
                    "raw_json": json.dumps({"is_present": True}),
                }
            )
        )
        self.assertFalse(
            OrdersPage._is_unclaimed_gift_order(
                {
                    "status": 100,
                    "raw_json": json.dumps({"is_present": True}),
                }
            )
        )

    def test_automation_task_lifecycle(self) -> None:
        store_id = self.db.add_store("自动化店铺", "wx-auto-test", "secret")
        task_id = self.db.add_automation_task(
            "订单同步",
            "order_sync",
            store_id,
            60,
        )
        tasks = self.db.list_automation_tasks()
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["name"], "订单同步")
        self.db.update_automation_task(task_id, enabled=0, last_status="成功")
        self.db.log_automation_run(task_id, "success", "测试")
        self.assertEqual(self.db.list_automation_tasks()[0]["enabled"], 0)
        self.assertEqual(len(self.db.list_automation_runs()), 1)
        self.db.delete_automation_task(task_id)
        self.assertEqual(self.db.list_automation_tasks(), [])

    def test_database_backup_and_update_manifest(self) -> None:
        store_id = self.db.add_store("备份店铺", "wx-backup-test", "secret")
        backup = self.root / "backup.db"
        self.db.backup_to(backup)
        self.assertTrue(backup.exists())
        manifest = self.root / "update_manifest.json"
        manifest.write_text(
            json.dumps({"version": "99.0.0", "download_url": "https://example.test"}),
            encoding="utf-8",
        )
        info = check_for_update(str(manifest))
        self.assertTrue(info.available)
        self.assertEqual(info.latest_version, "99.0.0")
        self.assertIsNotNone(self.db.get_store(store_id))

    def test_daily_automatic_backup_is_idempotent(self) -> None:
        backup_dir = self.root / "backups"
        first = self.db.auto_backup(backup_dir, keep=3)
        second = self.db.auto_backup(backup_dir, keep=3)
        self.assertIsNotNone(first)
        self.assertIsNone(second)
        self.assertTrue(first and first.exists())

    def test_frozen_app_migrates_legacy_data_to_stable_user_directory(
        self,
    ) -> None:
        from weimi_manager.config import AppPaths

        app_dir = self.root / "portable"
        legacy_data = app_dir / "data"
        legacy_data.mkdir(parents=True)
        legacy_db = Database(legacy_data / "weimi_manager.db")
        legacy_db.add_store(
            "旧版店铺",
            "wx-legacy-migration",
            "legacy-secret",
        )
        profile_file = legacy_data / "edge_profiles" / "store_1" / "Cookies"
        profile_file.parent.mkdir(parents=True)
        profile_file.write_text("migrated-session", encoding="utf-8")
        local_app_data = self.root / "LocalAppData"
        with (
            patch.dict(
                os.environ,
                {
                    "LOCALAPPDATA": str(local_app_data),
                    "WEIMI_DATA_DIR": "",
                },
                clear=False,
            ),
            patch.object(sys, "frozen", True, create=True),
            patch.object(sys, "executable", str(app_dir / "LangLang.exe")),
        ):
            paths = AppPaths.discover()
        self.assertEqual(
            paths.data_dir.resolve(),
            (local_app_data / "LangLang" / "data").resolve(),
        )
        migrated = Database(paths.database)
        stores = migrated.list_stores()
        self.assertEqual(len(stores), 1)
        self.assertEqual(stores[0].app_id, "wx-legacy-migration")
        self.assertEqual(
            migrated.get_store_secret(stores[0].id)[1],
            "legacy-secret",
        )
        self.assertTrue((legacy_data / "weimi_manager.db").exists())
        self.assertEqual(
            (paths.data_dir / "edge_profiles" / "store_1" / "Cookies").read_text(encoding="utf-8"),
            "migrated-session",
        )

    def test_bulk_store_upsert_supports_thousands_and_survives_restart(
        self,
    ) -> None:
        records = [
            (
                f"规模店铺 {index}",
                f"wx-scale-{index:05d}",
                f"secret-{index:05d}",
            )
            for index in range(1000)
        ]
        created, updated, errors = self.db.upsert_stores(
            records,
            group_name="规模测试",
        )
        self.assertEqual((created, updated, errors), (1000, 0, []))
        self.assertEqual(self.db.data_counts()["stores"], 1000)

        stores = self.db.list_stores()
        self.assertEqual(len(stores), 1000)
        self.assertEqual(len({store.app_id for store in stores}), 1000)
        self.assertEqual(
            self.db.get_store_secret(stores[0].id)[1],
            "secret-00000",
        )
        self.assertEqual(
            self.db.get_store_secret(stores[-1].id)[1],
            "secret-00999",
        )
        self.assertEqual(
            len(self.db.list_stores("wx-scale-005")),
            100,
        )
        metrics = self.db.store_metric_map()
        self.assertEqual(len(metrics), 1000)
        self.assertEqual(
            self.db.set_store_groups(
                [store.id for store in stores],
                "全部重新分组",
            ),
            1000,
        )
        self.assertEqual(
            {store.group_name for store in self.db.list_stores()},
            {"全部重新分组"},
        )

        reopened = Database(self.root / "test.db")
        self.assertEqual(reopened.data_counts()["stores"], 1000)
        self.assertEqual(
            reopened.get_store_secret(stores[-1].id)[1],
            "secret-00999",
        )

        first_id = stores[0].id
        created, updated, errors = reopened.upsert_stores(
            [("", "wx-scale-00000", "rotated-secret")],
            group_name="更新组",
        )
        self.assertEqual((created, updated, errors), (0, 1, []))
        refreshed = reopened.get_store(first_id)
        assert refreshed
        self.assertEqual(refreshed.name, "规模店铺 0")
        self.assertEqual(refreshed.group_name, "更新组")
        self.assertEqual(
            reopened.get_store_secret(first_id)[1],
            "rotated-secret",
        )
        self.assertEqual(reopened.data_counts()["stores"], 1000)

    def test_mock_sync(self) -> None:
        store_id = self.db.add_store("演示店铺", "demo-store", "local")
        store = self.db.get_store(store_id)
        assert store
        service = SyncService(self.db)
        end_time = int(time.time())
        result = service.sync_orders(store, end_time - 6 * 86400, end_time)
        self.assertGreaterEqual(result.count, 1)
        self.assertGreaterEqual(len(self.db.list_orders(store_id=store_id)), 1)
        service.sync_products(store)
        self.assertGreaterEqual(len(self.db.list_products(store_id=store_id)), 1)
        self.assertGreaterEqual(self.db.product_catalog_stats(store_id)["done"], 1)
        snapshot = service.refresh_product_status_snapshot(store)
        self.assertGreaterEqual(snapshot.count, 1)
        self.assertGreaterEqual(
            self.db.get_product_status_snapshot(store_id).get("sales", 0),
            1,
        )
        product_id = self.db.list_products(store_id=store_id)[0]["product_id"]
        listing = service.set_products_listing(store, [product_id], listed=False)
        self.assertEqual(listing.success, 1)
        self.assertEqual(self.db.list_products(store_id=store_id)[0]["status"], 11)
        cached = self.db.get_product(store_id, product_id)
        assert cached
        sku_id = str(json.loads(cached["raw_json"])["skus"][0]["sku_id"])
        service.update_product_stock(store, product_id, sku_id, 1, 5)
        refreshed = self.db.get_product(store_id, product_id)
        assert refreshed
        self.assertEqual(refreshed["stock_total"], cached["stock_total"] + 5)
        first_order = self.db.list_orders(store_id=store_id)[0]
        service.decode_order_sensitive(store, first_order["order_id"])
        decoded = self.db.get_order(store_id, first_order["order_id"])
        self.assertIsNotNone(decoded)
        self.assertEqual(decoded.buyer_name, "演示买家")
        self.assertIn("演示地址", decoded.address)
        service.update_order_notes(store, first_order["order_id"], "优先处理", 1)
        self.assertEqual(
            self.db.get_order(store_id, first_order["order_id"]).merchant_notes,
            "优先处理",
        )
        product_written = service.product_write(
            store,
            "update",
            payload={"product_id": product_id, "title": "更新标题"},
        )
        self.assertEqual(product_written["errcode"], 0)
        service.sync_store_profile(store)
        self.assertEqual(self.db.get_store_profile(store_id)["nickname"], "澜琅演示店铺")
        service.sync_after_sales(store, end_time - 3600, end_time)
        self.assertGreaterEqual(len(self.db.list_after_sales(store_id)), 1)
        after_sale = self.db.list_after_sales(store_id)[0]
        action_result = service.aftersale_action(
            store,
            after_sale["after_sale_order_id"],
            "accept",
        )
        self.assertEqual(action_result.success, 1)
        service.sync_analytics_day(store, date.today().isoformat())
        overview = self.db.get_analytics_day(store_id, date.today().isoformat())
        self.assertIsNotNone(overview)
        self.assertGreater(overview["pay_gmv"], 0)
        shipping = service.refresh_shipping_options(store)
        self.assertEqual(shipping.count, 1)
        ready_order = next(
            row for row in self.db.list_orders(store_id=store_id) if row["status"] == 20
        )
        shipment = service.create_shipment(store, ready_order["order_id"])
        self.assertEqual(shipment["status"], "created")
        self.assertTrue(shipment["waybill_id"])
        completed = service.complete_shipment(store, ready_order["order_id"])
        self.assertEqual(completed["status"], "shipped")
        updated_order = self.db.get_order(store_id, ready_order["order_id"])
        self.assertIsNotNone(updated_order)
        self.assertEqual(updated_order.status, 21)

    def test_product_add_reads_nested_id_and_mirrors_remote_image(self) -> None:
        store_id = self.db.add_store(
            "图片转存测试",
            "wx-image-mirror",
            "secret",
        )
        store = self.db.get_store(store_id)
        assert store

        class FakeClient:
            def __init__(self) -> None:
                self.image_calls: list[str] = []
                self.add_payload: dict | None = None

            def upload_product_image_url(self, url: str) -> str:
                self.image_calls.append(url)
                return f"https://mmecimage.cn/uploaded-{len(self.image_calls)}"

            def add_product(self, payload: dict) -> dict:
                self.add_payload = payload
                return {
                    "errcode": 0,
                    "errmsg": "ok",
                    "data": {"product_id": "P-NEW"},
                }

            def fetch_product(self, *_args, **_kwargs) -> Product:
                raise RuntimeError("pending audit")

        client = FakeClient()
        service = SyncService(self.db)
        service.platform_write_authorizer = lambda _app_id, _action: None
        service.client_for = lambda _store: client
        source_urls = [
            "https://img.alicdn.com/example-1.jpg",
            "https://img.alicdn.com/example-2.jpg",
            "https://img.alicdn.com/example-3.jpg",
        ]
        result = service.product_write(
            store,
            "add",
            {
                "title": "测试商品",
                "head_imgs": source_urls,
                "desc_info": {"imgs": [], "desc": ""},
                "cats": [{"cat_id": "6033"}],
                "skus": [{"sale_price": 100, "stock_num": 1}],
                "deliver_method": 0,
                "express_info": {
                    "template_id": "freight-template-1",
                    "weight": 1000,
                    "express_type": 0,
                },
                "after_sale_info": {
                    "after_sale_address_id": "after-sale-address-1",
                },
            },
        )
        self.assertEqual(result["product_id"], "P-NEW")
        self.assertEqual(client.image_calls, source_urls)
        assert client.add_payload
        self.assertEqual(
            client.add_payload["head_imgs"][0],
            "https://mmecimage.cn/uploaded-1",
        )

    def test_product_write_warehouse_creates_then_delists(self) -> None:
        store_id = self.db.add_store(
            "仓库模式测试",
            "wx-warehouse-mode",
            "secret",
        )
        store = self.db.get_store(store_id)
        assert store
        self.db.set_setting(
            f"product_quota_{store_id}",
            json.dumps({"remaining": 9, "total": 20}),
        )
        self.db.set_setting(
            f"store_metrics_{store_id}",
            json.dumps(
                {
                    "quota_remaining": 9,
                    "quota_total": 20,
                    "product_total": 4,
                }
            ),
        )

        class FakeClient:
            def __init__(self) -> None:
                self.add_payload: dict | None = None
                self.delist_calls: list[str] = []

            def add_product(self, payload: dict) -> dict:
                self.add_payload = dict(payload)
                return {
                    "errcode": 0,
                    "data": {"product_id": "P-WAREHOUSE"},
                }

            def set_product_listing(
                self,
                product_id: str,
                listed: bool,
            ) -> dict:
                self.delist_calls.append(product_id)
                assert listed is False
                return {"errcode": 0}

            def fetch_product(self, *_args, **_kwargs):
                raise RuntimeError("audit pending")

        client = FakeClient()
        service = SyncService(self.db)
        service.platform_write_authorizer = lambda _app_id, _action: None
        result = service.product_write(
            store,
            "add",
            {
                "title": "仓库商品",
                "head_imgs": [
                    "https://mmecimage.cn/1.jpg",
                    "https://mmecimage.cn/2.jpg",
                    "https://mmecimage.cn/3.jpg",
                ],
                "desc_info": {
                    "imgs": ["https://mmecimage.cn/1.jpg"],
                    "desc": "",
                },
                "cats": [{"cat_id": "6033"}],
                "skus": [{"sale_price": 100, "stock_num": 1}],
                "express_info": {
                    "template_id": "freight-1",
                    "weight": 1000,
                    "express_type": 0,
                },
                "after_sale_info": {
                    "after_sale_address_id": "address-1",
                },
            },
            client=client,
            listing_mode="warehouse",
        )
        self.assertEqual(result["product_id"], "P-WAREHOUSE")
        assert client.add_payload
        self.assertEqual(client.add_payload["listing"], 1)
        self.assertEqual(client.delist_calls, ["P-WAREHOUSE"])
        quota = json.loads(self.db.get_setting(f"product_quota_{store_id}", "{}"))
        metrics = json.loads(self.db.get_setting(f"store_metrics_{store_id}", "{}"))
        self.assertEqual(quota["remaining"], 8)
        self.assertEqual(metrics["product_total"], 5)

    def test_refresh_store_metrics_fetches_fresh_quota(self) -> None:
        store_id = self.db.add_store(
            "额度刷新测试",
            "wx-quota-refresh",
            "secret",
        )
        store = self.db.get_store(store_id)
        assert store
        self.db.set_setting(
            f"product_quota_{store_id}",
            json.dumps({"remaining": 0, "total": 0}),
        )

        class FakeOfficialClient:
            def fetch_product_total(self, _status=None) -> int:
                return 100

            def fetch_fund_balance(self) -> dict:
                return {
                    "available_amount": 0,
                    "pending_amount": 0,
                }

            def fetch_product_audit_quota(self) -> dict:
                return {
                    "errcode": 0,
                    "audit_quota": {
                        "audit_total_remaining": 377,
                        "audit_total_quota": 500,
                    },
                }

        class FakePrivateClient:
            def __init__(self, *_args, **_kwargs) -> None:
                pass

            def realtime_overview(self) -> dict:
                raise ShopPrivateError("not logged in")

        service = SyncService(self.db)
        service.client_for = lambda _store: FakeOfficialClient()
        service._analytics_days_for_metrics = lambda _store: []
        with patch(
            "weimi_manager.service.ShopPrivateClient",
            FakePrivateClient,
        ):
            metrics = service.refresh_store_metrics(store)
        self.assertEqual(metrics["quota_remaining"], 377)
        self.assertEqual(metrics["quota_total"], 500)
        saved = json.loads(self.db.get_setting(f"product_quota_{store_id}", "{}"))
        self.assertEqual(
            saved,
            {"remaining": 377, "total": 500},
        )

    def test_product_payload_uses_remote_defaults_for_new_store(self) -> None:
        store_id = self.db.add_store(
            "新店铺",
            "wx-remote-defaults",
            "secret",
        )
        store = self.db.get_store(store_id)
        assert store
        default_payload = {
            "express_info": {
                "template_id": "shipping-template-1",
                "weight": 1000,
                "express_type": 0,
            },
            "after_sale_info": {
                "after_sale_address_id": "after-sale-address-1",
            },
            "head_imgs": [
                "https://mmecimage.cn/default-1",
                "https://mmecimage.cn/default-2",
                "https://mmecimage.cn/default-3",
            ],
            "desc_info": {
                "imgs": ["https://mmecimage.cn/detail-1"],
                "desc": "",
            },
        }

        class FakeClient:
            def __init__(self) -> None:
                self.fetch_ids = 0
                self.fetch_details = 0

            def fetch_product_ids(self, status=None):
                self.fetch_ids += 1
                assert status == 5
                return ["P-DEFAULT"]

            def fetch_product(self, _store, product_id):
                self.fetch_details += 1
                assert product_id == "P-DEFAULT"
                return SimpleNamespace(raw=default_payload)

        client = FakeClient()
        service = SyncService(self.db)
        prepared = service._prepare_product_payload(
            store,
            client,
            {
                "title": "测试商品",
                "head_imgs": ["https://mmecimage.cn/source-1"],
                "desc_info": {"imgs": [], "desc": ""},
                "cats": [{"cat_id": "6033"}],
                "skus": [{"sale_price": 100, "stock_num": 1}],
            },
        )
        self.assertEqual(
            prepared["express_info"]["template_id"],
            "shipping-template-1",
        )
        self.assertEqual(
            prepared["after_sale_info"]["after_sale_address_id"],
            "after-sale-address-1",
        )
        self.assertEqual(
            prepared["head_imgs"],
            [
                "https://mmecimage.cn/source-1",
                "https://mmecimage.cn/default-1",
                "https://mmecimage.cn/default-2",
            ],
        )
        self.assertEqual(prepared["listing"], 1)
        self.assertEqual(client.fetch_ids, 1)
        self.assertEqual(client.fetch_details, 1)

    def test_product_payload_uses_merchant_defaults_without_active_products(
        self,
    ) -> None:
        store_id = self.db.add_store(
            "只有商户配置的店铺",
            "wx-merchant-defaults",
            "secret",
        )
        store = self.db.get_store(store_id)
        assert store

        class FakeClient:
            def __init__(self) -> None:
                self.freight_calls = 0
                self.address_calls = 0

            def fetch_product_ids(self, status=None):
                assert status == 5
                return []

            def fetch_default_freight_template(self):
                self.freight_calls += 1
                return {
                    "template_id": "freight-template-1",
                    "name": "默认运费模板",
                    "is_default": 1,
                }

            def fetch_default_after_sale_address(self):
                self.address_calls += 1
                return {
                    "address_id": "after-sale-address-1",
                    "default_recv": 1,
                }

        client = FakeClient()
        service = SyncService(self.db)
        prepared = service._prepare_product_payload(
            store,
            client,
            {
                "title": "测试商品",
                "head_imgs": [
                    "https://mmecimage.cn/source-1",
                    "https://mmecimage.cn/source-2",
                    "https://mmecimage.cn/source-3",
                ],
                "desc_info": {
                    "imgs": ["https://mmecimage.cn/source-1"],
                    "desc": "",
                },
                "cats": [{"cat_id": "6033"}],
                "skus": [{"sale_price": 100, "stock_num": 1}],
                "deliver_method": 0,
                "express_info": {},
                "after_sale_info": {},
            },
        )
        self.assertEqual(
            prepared["express_info"]["template_id"],
            "freight-template-1",
        )
        self.assertEqual(
            prepared["after_sale_info"]["after_sale_address_id"],
            "after-sale-address-1",
        )
        self.assertEqual(prepared["deliver_method"], 0)
        self.assertEqual(client.freight_calls, 1)
        self.assertEqual(client.address_calls, 1)

    def test_product_payload_rejects_fewer_than_three_head_images(
        self,
    ) -> None:
        store_id = self.db.add_store(
            "主图不足店铺",
            "wx-head-image-check",
            "secret",
        )
        store = self.db.get_store(store_id)
        assert store

        class FakeClient:
            def fetch_product_ids(self, status=None):
                return []

            def fetch_default_freight_template(self):
                return {"template_id": "freight-template-1"}

            def fetch_default_after_sale_address(self):
                return {"address_id": "after-sale-address-1"}

        with self.assertRaisesRegex(
            ValueError,
            "商品主图少于3张",
        ):
            SyncService(self.db)._prepare_product_payload(
                store,
                FakeClient(),
                {
                    "title": "主图不足",
                    "head_imgs": [
                        "https://mmecimage.cn/source-1",
                        "https://mmecimage.cn/source-2",
                    ],
                    "cats": [{"cat_id": "6033"}],
                    "skus": [{"sale_price": 100, "stock_num": 1}],
                },
            )

    def test_product_payload_adds_required_category_attributes(self) -> None:
        store_id = self.db.add_store(
            "类目属性测试",
            "wx-required-attributes",
            "secret",
        )
        store = self.db.get_store(store_id)
        assert store

        class FakeClient:
            def fetch_category_product_rule(
                self,
                category_id,
                brand_id=2100000000,
                release_mode=0,
            ):
                assert category_id == "6117"
                assert brand_id == "2100000000"
                assert release_mode == 0
                return {
                    "info": {"name": "防晒手套/防晒袖套/冰袖"},
                    "product_attr_list": [
                        {
                            "name": "面料材质",
                            "type_v2": "select_one",
                            "value": "聚酯纤维;尼龙;棉",
                            "required_rule": {"rule_type": 1},
                        },
                        {
                            "name": "面料材质成分含量",
                            "type_v2": "decimal4_unit",
                            "value": "%",
                            "required_rule": {"rule_type": 1},
                        },
                    ],
                }

        prepared = SyncService(self.db)._prepare_product_payload(
            store,
            FakeClient(),
            {
                "title": "锦纶防晒手套",
                "head_imgs": [
                    "https://mmecimage.cn/1.jpg",
                    "https://mmecimage.cn/2.jpg",
                    "https://mmecimage.cn/3.jpg",
                ],
                "desc_info": {
                    "imgs": ["https://mmecimage.cn/1.jpg"],
                    "desc": "",
                },
                "cats": [
                    {"cat_id": "6033"},
                    {"cat_id": "6093"},
                    {"cat_id": "6117"},
                ],
                "cats_v2": [
                    {"cat_id": "10000111"},
                    {"cat_id": "10000114"},
                    {"cat_id": "6117"},
                ],
                "brand_id": "2100000000",
                "skus": [{"sale_price": 100, "stock_num": 1}],
                "express_info": {
                    "template_id": "freight-1",
                    "weight": 1000,
                    "express_type": 0,
                },
                "after_sale_info": {
                    "after_sale_address_id": "address-1",
                },
            },
        )
        attrs = {item["attr_key"]: item["attr_value"] for item in prepared["attrs"]}
        self.assertEqual(attrs["面料材质"], "尼龙")
        self.assertEqual(attrs["面料材质成分含量"], "100 %")

    def test_brand_id_removes_duplicate_brand_attributes(self) -> None:
        store_id = self.db.add_store(
            "品牌属性去重测试",
            "wx-brand-attribute-dedup",
            "secret",
        )
        store = self.db.get_store(store_id)
        assert store

        class FakeClient:
            @staticmethod
            def fetch_category_product_rule(
                category_id,
                brand_id=2100000000,
                release_mode=0,
            ):
                assert category_id == "6117"
                assert brand_id == "2100000000"
                assert release_mode == 0
                return {
                    "info": {"name": "女鞋"},
                    "product_attr_list": [
                        {
                            "name": "品牌",
                            "type_v2": "select_one",
                            "value": "无品牌;其他",
                            "required_rule": {"rule_type": 1},
                        },
                        {
                            "name": "鞋面材质",
                            "type_v2": "select_one",
                            "value": "PU;其他",
                            "required_rule": {"rule_type": 1},
                        },
                    ],
                }

        payload = {
            "brand_id": "2100000000",
            "cats": [{"cat_id": "6117"}],
            "attrs": [
                {"attr_key": "品牌", "attr_value": "旧品牌"},
                {"attr_key": "鞋面材质", "attr_value": "PU"},
                {"attr_key": "品牌", "attr_value": "新品牌"},
            ],
        }
        SyncService(self.db)._apply_required_category_attributes(
            store,
            FakeClient(),
            payload,
        )
        keys = [item["attr_key"] for item in payload["attrs"]]
        self.assertNotIn("品牌", keys)
        self.assertEqual(keys.count("鞋面材质"), 1)
        self.assertEqual(payload["brand_id"], "2100000000")

    def test_required_attribute_fill_is_rule_driven_for_other_products(
        self,
    ) -> None:
        service = SyncService(self.db)
        plastic = service._required_attribute_value(
            {
                "name": "主要材质",
                "type_v2": "select_one",
                "value": "玻璃;塑料;其他",
            },
            {"title": "500ml 塑料水瓶"},
            "塑料瓶",
        )
        paper = service._required_attribute_value(
            {
                "name": "主要原料",
                "type_v2": "select_one",
                "value": "原生木浆;竹浆;其他",
            },
            {"title": "家用抽纸"},
            "纸巾",
        )
        width = service._required_attribute_value(
            {
                "name": "桌面宽度",
                "type_v2": "decimal4_unit",
                "value": "cm",
            },
            {"title": "办公桌 120cm 宽"},
            "桌子",
        )
        capacity = service._required_attribute_value(
            {
                "name": "容量",
                "type_v2": "decimal4_unit",
                "value": "ml",
            },
            {"title": "水杯500 ml"},
            "杯子",
        )
        composition = service._required_attribute_value(
            {
                "name": "材质成分含量",
                "type_v2": "decimal4_unit",
                "value": "%",
            },
            {"title": "聚酯纤维围巾"},
            "围巾",
        )
        self.assertEqual(plastic, "塑料")
        self.assertEqual(paper, "其他")
        self.assertEqual(width, "120 cm")
        self.assertEqual(capacity, "500 ml")
        self.assertEqual(composition, "100 %")

    def test_task_log_records_network_operation_result(self) -> None:
        log_id = self.db.start_task_log(
            scope="comments",
            operation="刷新店铺数据",
            message="开始刷新",
            store_id=None,
            detail="scope=all",
        )
        self.db.finish_task_log(
            log_id,
            status="failed",
            message="额度接口失败",
            detail="HTTP 503",
            duration_ms=1250,
        )
        rows = self.db.list_task_logs(limit=10)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["operation"], "刷新店铺数据")
        self.assertEqual(rows[0]["status"], "failed")
        self.assertEqual(rows[0]["message"], "额度接口失败")
        self.assertEqual(rows[0]["duration_ms"], 1250)

    def test_task_log_does_not_treat_data_rows_as_errors(self) -> None:
        status, message, detail = MainWindow._task_result_log(
            [{"product_id": "P-1"}, {"product_id": "P-2"}]
        )
        self.assertEqual(status, "success")
        self.assertIn("2", message)
        self.assertEqual(detail, "返回记录数：2")
        status, message, detail = MainWindow._task_result_log(["店铺一：网络超时"])
        self.assertEqual(status, "partial")
        self.assertIn("1", message)
        self.assertEqual(detail, "店铺一：网络超时")

    def test_interrupted_task_reservation_is_recovered(self) -> None:
        log_id = self.db.start_task_log(
            scope="auto_refresh",
            operation="自动轮询店铺登录和经营数据",
            message="本批 2 家店铺",
        )
        self.assertEqual(self.db.recover_interrupted_task_logs(), 1)
        row = next(item for item in self.db.list_task_logs(limit=10) if item["id"] == log_id)
        self.assertEqual(row["status"], "failed")
        self.assertIn("中断", row["message"])
        self.assertIn("崩溃", row["detail"])

    def test_old_database_gains_task_logs_without_losing_data(self) -> None:
        store_id = self.db.add_store(
            "升级保留店铺",
            "wx-upgrade-preserve",
            "secret",
        )
        self.db.set_setting("upgrade_sentinel", "keep-me")
        with self.db.connect() as conn:
            conn.execute("DROP TABLE task_logs")
        upgraded = Database(self.root / "test.db")
        self.assertIsNotNone(upgraded.get_store(store_id))
        self.assertEqual(
            upgraded.get_setting("upgrade_sentinel"),
            "keep-me",
        )
        log_id = upgraded.start_task_log(
            scope="upgrade",
            operation="旧库升级后写入任务日志",
        )
        self.assertGreater(log_id, 0)

    def test_upload_history_persists_per_store_and_computer(self) -> None:
        first = self.db.add_store(
            "上传历史店铺一",
            "wx-upload-history-1",
            "secret",
        )
        second = self.db.add_store(
            "上传历史店铺二",
            "wx-upload-history-2",
            "secret",
        )
        self.db.record_upload_history(
            first,
            "SOURCE-A",
            "PRODUCT-A",
            "商品 A",
        )
        self.db.record_upload_history(
            second,
            "SOURCE-B",
            "PRODUCT-B",
            "商品 B",
        )
        reopened = Database(self.root / "test.db")
        self.assertEqual(
            reopened.uploaded_source_ids(first),
            {"SOURCE-A"},
        )
        self.assertEqual(
            reopened.uploaded_source_ids(),
            {"SOURCE-A", "SOURCE-B"},
        )

    def test_upload_metrics_update_immediately(self) -> None:
        store_id = self.db.add_store(
            "即时指标店铺",
            "wx-immediate-metrics",
            "secret",
        )
        self.db.set_setting(
            f"product_quota_{store_id}",
            json.dumps({"remaining": 10, "total": 20}),
        )
        self.db.set_setting(
            f"store_metrics_{store_id}",
            json.dumps(
                {
                    "quota_remaining": 10,
                    "quota_total": 20,
                    "product_total": 5,
                }
            ),
        )
        self.db.adjust_upload_metrics(
            store_id,
            product_delta=1,
            quota_delta=-1,
        )
        quota = json.loads(self.db.get_setting(f"product_quota_{store_id}", "{}"))
        metrics = json.loads(self.db.get_setting(f"store_metrics_{store_id}", "{}"))
        self.assertEqual(quota["remaining"], 9)
        self.assertEqual(metrics["quota_remaining"], 9)
        self.assertEqual(metrics["product_total"], 6)
        self.assertTrue(metrics["_pending_refresh"])

    def test_activity_preview_applies_discount_and_one_yuan_floor(self) -> None:
        store_id = self.db.add_store(
            "活动预检测试",
            "wx-activity-preview",
            "secret",
        )
        self.db.update_web_status(store_id, "已登录", "测试")
        store = self.db.get_store(store_id)
        assert store
        self.db.upsert_product(
            Product(
                store_id=store_id,
                store_name=store.name,
                product_id="P-ACTIVITY",
                title="活动商品",
                status=5,
                status_text="上架",
                edit_status=4,
                min_price=800,
                sku_count=2,
                stock_total=20,
                spu_code="",
                out_product_id="",
                head_img="",
                raw={
                    "skuList": [
                        {"skuId": "SKU-8", "price": 800, "stockNum": 10},
                        {"skuId": "SKU-20", "price": 2000, "stockNum": 10},
                    ]
                },
            )
        )

        class FakePrivateClient:
            applied: list[int] = []

            def __init__(self, *args, **kwargs):
                pass

            def account_close_status(self):
                return 100

            def activity_products(self, *args, **kwargs):
                return {"productList": []}

            def activity_eligible_products(self, activity_id):
                return [
                    {
                        "productId": "P-ACTIVITY",
                        "skuId": "SKU-8",
                        "salePrice": "800",
                        "commStockNum": "10",
                        "invalidString": "",
                    },
                    {
                        "productId": "P-ACTIVITY",
                        "skuId": "SKU-20",
                        "salePrice": "2000",
                        "commStockNum": "10",
                        "invalidString": "",
                    },
                    {
                        "productId": "P-ACTIVITY",
                        "skuId": "SKU-2005",
                        "salePrice": "2005",
                        "commStockNum": "10",
                        "invalidString": "",
                    },
                ]

            def apply_platform_activity(self, activity_id):
                self.applied.append(activity_id)
                return {"ret": 0}

        with patch(
            "weimi_manager.service.ShopPrivateClient",
            FakePrivateClient,
        ):
            result = SyncService(self.db).activity_preview(
                store,
                1323,
                discount_basis_points=9000,
                min_discount_cents=100,
            )
        self.assertEqual(result["status"], "可预览")
        rows = {row["sku_id"]: row for row in result["rows"]}
        self.assertEqual(rows["SKU-8"]["activity_price"], 700)
        self.assertEqual(rows["SKU-8"]["discount_cents"], 100)
        self.assertEqual(rows["SKU-20"]["activity_price"], 1800)
        self.assertEqual(rows["SKU-20"]["discount_cents"], 200)
        self.assertEqual(
            rows["SKU-2005"]["activity_price"],
            1805,
            "9折出现半分钱时必须四舍五入到18.05元",
        )
        with patch(
            "weimi_manager.service.ShopPrivateClient",
            FakePrivateClient,
        ):
            service = SyncService(self.db)
            service.platform_write_authorizer = (
                lambda _app_id, _action: None
            )
            applied = service.apply_platform_activity(
                store,
                1323,
            )
        self.assertEqual(applied["activity_id"], 1323)
        self.assertEqual(FakePrivateClient.applied, [1323])

        empty_id = self.db.add_store(
            "无商品活动店",
            "wx-activity-empty",
            "secret",
        )
        self.db.update_web_status(empty_id, "已登录", "测试")
        empty_store = self.db.get_store(empty_id)
        assert empty_store

        class FakeOfficialClient:
            def fetch_product_total(self, status=None):
                return 0

        class FakeEmptyPrivateClient(FakePrivateClient):
            def activity_eligible_products(self, activity_id):
                return []

        empty_service = SyncService(self.db)
        with (
            patch(
                "weimi_manager.service.ShopPrivateClient",
                FakeEmptyPrivateClient,
            ),
            patch.object(
                empty_service,
                "client_for",
                return_value=FakeOfficialClient(),
            ),
        ):
            empty = empty_service.activity_preview(
                empty_store,
                1323,
            )
        self.assertEqual(empty["status"], "跳过")
        self.assertIn("没有", empty["reason"])

    def test_activity_operation_records_persist(self) -> None:
        self.db.record_activity_operation(
            "活动申报",
            "成功",
            store_id=12,
            store_name="记录测试店",
            activity_id=1323,
            item_count=1,
            message="申报成功",
            detail={"ret": 0},
        )
        records = self.db.list_activity_operations()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["action"], "活动申报")
        self.assertEqual(records[0]["store_name"], "记录测试店")
        self.assertEqual(records[0]["activity_id"], 1323)

    def test_activity_submit_uses_bulk_payload_and_splits_at_200_products(
        self,
    ) -> None:
        store_id = self.db.add_store(
            "批量活动测试",
            "wx-activity-bulk",
            "secret",
        )
        self.db.update_web_status(store_id, "已登录", "测试")
        store = self.db.get_store(store_id)
        assert store
        calls = []

        class FakePrivateClient:
            def __init__(self, *args, **kwargs):
                pass

            def activity_validate_products(self, activity_id, items):
                calls.append(("validate", len(items), dict(items[0])))
                return {
                    "code": 0,
                    "resultList": [
                        {
                            "product": {
                                "productId": item["productId"],
                                "skuId": item["skuId"],
                            },
                            "invalidString": "",
                            "invalidStringPrecheck": "",
                        }
                        for item in items
                    ],
                }

            def activity_update_products(
                self,
                activity_id,
                items,
                action_type,
                add_source,
            ):
                calls.append(("update", len(items), dict(items[0]), action_type, add_source))
                return {"code": 0, "checkResultList": []}

        items = [
            {
                "productId": f"P-{index:03d}",
                "skuId": f"S-{index:03d}",
                "newActivityPrice": 100,
                "applyType": 1,
            }
            for index in range(201)
        ]
        with patch(
            "weimi_manager.service.ShopPrivateClient",
            FakePrivateClient,
        ):
            service = SyncService(self.db)
            service.platform_write_authorizer = (
                lambda _app_id, _action: None
            )
            result = service.activity_submit_items(
                store,
                1323,
                items,
            )
        self.assertEqual(result["count"], 201)
        self.assertEqual(result["batch_count"], 2)
        updates = [call for call in calls if call[0] == "update"]
        self.assertEqual([call[1] for call in updates], [200, 1])
        self.assertTrue(all(call[3:] == (2, 3) for call in updates))
        self.assertEqual(updates[0][2]["activityStockNum"], 0)
        self.assertEqual(updates[0][2]["stockMode"], 1)
        self.assertEqual(updates[0][2]["sellingPointWording"], "")

    def test_product_catalog_resumes_after_cancel(self) -> None:
        store_id = self.db.add_store("目录测试", "wx-catalog-test", "secret")
        store = self.db.get_store(store_id)
        assert store
        cancel = threading.Event()

        class CatalogClient:
            def __init__(self):
                self.calls = 0

            def fetch_product_id_page(self, next_key="", status=None):
                self.calls += 1
                if not next_key:
                    cancel.set()
                    return type(
                        "Page", (), {"ids": ["P1", "P2"], "next_key": "NEXT", "has_more": True}
                    )()
                return type("Page", (), {"ids": ["P3"], "next_key": "", "has_more": False})()

        service = SyncService(self.db)
        client = CatalogClient()
        service.client_for = lambda _store: client
        first = service.scan_product_catalog(store, cancel_event=cancel)
        self.assertTrue(first.cancelled)
        state = self.db.get_product_scan_state(store_id)
        self.assertEqual(state["next_key"], "NEXT")
        cancel.clear()
        second = service.scan_product_catalog(store, cancel_event=cancel)
        self.assertFalse(second.cancelled)
        self.assertEqual(second.count, 3)
        self.assertEqual(self.db.product_catalog_stats(store_id)["total"], 3)

    def test_export_csv_and_xlsx(self) -> None:
        headers = ["店铺", "订单号", "金额"]
        rows = [["测试店", "123456", 19.9], ["另一店", "=HYPERLINK()", 0]]
        csv_path = self.root / "orders.csv"
        xlsx_path = self.root / "orders.xlsx"
        export_csv(csv_path, headers, rows)
        export_xlsx(xlsx_path, "订单", headers, rows)
        self.assertIn("测试店", csv_path.read_text(encoding="utf-8-sig"))
        with zipfile.ZipFile(xlsx_path) as archive:
            sheet = archive.read("xl/worksheets/sheet1.xml").decode("utf-8")
        self.assertIn("订单号", sheet)
        self.assertIn("'=HYPERLINK()", sheet)

    def test_parse_live_payloads(self) -> None:
        store = Store(1, "店铺", "wx", "", "ok", None, "")
        order = parse_order(
            store,
            {
                "order_id": "10001",
                "status": 20,
                "create_time": 1700000000,
                "update_time": 1700000001,
                "order_detail": {
                    "product_infos": [
                        {
                            "product_id": 1,
                            "sku_id": 2,
                            "title": "商品",
                            "sku_cnt": 2,
                            "sale_price": 500,
                            "sku_attrs": [{"attr_key": "颜色", "attr_value": "红色"}],
                        }
                    ],
                    "pay_info": {"pay_time": 1700000002},
                    "price_info": {"order_price": 900, "product_price": 1000, "freight": 0},
                    "delivery_info": {
                        "address_info": {
                            "province_name": "湖北省",
                            "city_name": "武汉市",
                            "county_name": "青山区",
                            "detail_info": "测试地址",
                            "user_name": "测试买家",
                            "tel_number": "138",
                        }
                    },
                },
            },
        )
        self.assertEqual(order.items[0].sku_count, 2)
        self.assertEqual(order.total_amount, 900)
        self.assertEqual(order.discount_amount, 100)
        self.assertIn("湖北省武汉市青山区测试地址", order.address)

        product = parse_product(
            store,
            {
                "product_id": "P1",
                "title": "商品",
                "status": 5,
                "edit_status": 4,
                "min_price": 199,
                "head_imgs": ["https://example.invalid/p.png"],
                "skus": [{"sku_id": "S1", "stock_num": 8}, {"sku_id": "S2", "stock_num": 2}],
            },
        )
        self.assertEqual(product.sku_count, 2)
        self.assertEqual(product.stock_total, 10)
        self.assertEqual(product.status_text, "上架")

    def test_client_paginates_and_caches_token(self) -> None:
        transport = FakeTransport()
        client = WechatShopClient(
            "wx-test",
            "secret",
            transport=transport,
            sleeper=lambda _: None,
            min_request_interval=0,
        )
        result = client.fetch_order_ids(1700000000, 1700003600)
        self.assertEqual(result, ["1", "2"])
        self.assertEqual(client.get_access_token(), "TOKEN")
        self.assertEqual(transport.token_calls, 1)

    def test_client_builds_product_write_requests(self) -> None:
        transport = WriteTransport()
        client = WechatShopClient(
            "wx-test",
            "secret",
            transport=transport,
            sleeper=lambda _: None,
            min_request_interval=0,
            write_authorizer=lambda _app_id, _action: None,
        )
        client.set_product_listing("P1", listed=True)
        client.set_product_listing("P1", listed=False)
        client.update_product_stock("P1", "S1", 1, 5)
        self.assertEqual(transport.payloads[0], {"product_id": "P1"})
        self.assertEqual(transport.payloads[1], {"product_id": "P1"})
        self.assertEqual(
            transport.payloads[2],
            {"product_id": "P1", "sku_id": "S1", "diff_type": 1, "num": 5},
        )

    def test_client_builds_official_league_and_price_requests(self) -> None:
        calls = []
        client = object.__new__(WechatShopClient)
        client.app_id = "wx-test"
        client.write_authorizer = lambda _app_id, _action: None

        def post(path, payload):
            calls.append((path, dict(payload)))
            if path.endswith("/channels/ec/product/get"):
                return {
                    "errcode": 0,
                    "edit_product": {
                        "product_id": "100",
                        "status": 5,
                        "title": "测试商品",
                        "head_imgs": ["https://example.invalid/1.png"],
                        "deliver_method": 0,
                        "skus": [
                            {
                                "sku_id": 11,
                                "sale_price": 1000,
                                "stock_num": 8,
                            }
                        ],
                        "read_only_field": "ignored",
                    },
                }
            return {"errcode": 0, "errmsg": "ok"}

        client._post_api = post
        client._post_write_api = (
            lambda path, payload, *, action: post(path, payload)
        )
        client.batch_add_league_items(
            [{"product_id": "100", "ratio": 20}],
        )
        client.update_product_prices("100", 1.1)
        client.create_limited_discount_task(
            {
                "product_id": "100",
                "start_time": 100,
                "end_time": 200,
                "limited_discount_skus": [
                    {
                        "sku_id": "11",
                        "sale_price": 900,
                        "sale_stock": 8,
                    }
                ],
            }
        )
        self.assertEqual(
            calls[0][0],
            "/channels/ec/league/item/batchadd",
        )
        self.assertEqual(calls[0][1]["list"][0]["product_id"], 100)
        self.assertEqual(calls[0][1]["list"][0]["ratio"], 20.0)
        update_payload = calls[2][1]
        self.assertEqual(calls[2][0], "/channels/ec/product/update")
        self.assertEqual(update_payload["skus"][0]["sale_price"], 1100)
        self.assertNotIn("read_only_field", update_payload)
        self.assertEqual(
            calls[3][0],
            "/channels/ec/product/limiteddiscounttask/add",
        )

    def test_limited_discount_uses_platform_limit_and_half_up_rounding(
        self,
    ) -> None:
        store_id = self.db.add_store(
            "限时抢购九折测试",
            "wx-limited-discount-nine",
            "secret",
        )
        store = self.db.get_store(store_id)
        assert store
        calls = []

        class FakeClient:
            @staticmethod
            def fetch_product_payload(_product_id, data_type=3):
                assert data_type == 3
                return {
                    "edit_product": {
                        "skus": [
                            {
                                "sku_id": "SKU-ODD",
                                "sale_price": 2005,
                                "stock_num": 10,
                            }
                        ]
                    }
                }

            @staticmethod
            def create_limited_discount_task(payload):
                calls.append(payload)
                return {"errcode": 0}

        service = SyncService(self.db)
        service.platform_write_authorizer = lambda _app_id, _action: None
        service.client_for = lambda _store: FakeClient()
        errors = service.create_limited_discount_tasks(
            store,
            ["P-ODD"],
            0.85,
            start_time=1000,
            end_time=2000,
        )
        self.assertEqual(errors, [])
        self.assertEqual(
            calls[0]["limited_discount_skus"][0]["sale_price"],
            1704,
            "0.85必须按8.5折计算，并把17.0425元四舍五入为17.04元",
        )
        self.assertEqual(
            calls[0]["limited_discount_skus"][0]["sale_stock"],
            10,
        )
        with self.assertRaisesRegex(ValueError, "0.85"):
            service.create_limited_discount_tasks(
                store,
                ["P-ODD"],
                0.86,
                start_time=1000,
                end_time=2000,
            )
        self.assertEqual(len(calls), 1)

    def test_client_selects_default_freight_and_after_sale_address(
        self,
    ) -> None:
        client = object.__new__(WechatShopClient)
        calls = []

        def post(path, payload):
            calls.append((path, dict(payload)))
            if path.endswith("getfreighttemplatelist"):
                return {"template_id_list": ["101", "102"]}
            if path.endswith("getfreighttemplatedetail"):
                return {
                    "freight_template": {
                        "template_id": payload["template_id"],
                        "name": "模板",
                        "is_default": int(payload["template_id"] == "102"),
                    }
                }
            if path.endswith("address/list"):
                return {"address_id_list": ["201", "202"]}
            if path.endswith("address/get"):
                return {
                    "address_detail": {
                        "address_id": payload["address_id"],
                        "recv_addr": 1,
                        "default_recv": int(payload["address_id"] == 202),
                    }
                }
            raise AssertionError(path)

        client._post_api = post
        freight = client.fetch_default_freight_template()
        address = client.fetch_default_after_sale_address()
        self.assertEqual(freight["template_id"], "102")
        self.assertEqual(address["address_id"], "202")
        self.assertIn(
            (
                "/channels/ec/merchant/getfreighttemplatelist",
                {"offset": 0, "limit": 20},
            ),
            calls,
        )
        self.assertIn(
            (
                "/channels/ec/merchant/address/list",
                {"offset": 0, "limit": 20},
            ),
            calls,
        )

    def test_products_page_query_runs_in_background_thread(self) -> None:
        from weimi_manager.ui.pages import ProductsPage

        completed = threading.Event()
        worker_name = {"value": ""}

        class FixedValue:
            def __init__(self, value: str) -> None:
                self.value = value

            def get(self) -> str:
                return self.value

        class FakeDb:
            @staticmethod
            def list_products(**_kwargs):
                worker_name["value"] = threading.current_thread().name
                return []

        class FakeWindow:
            @staticmethod
            def set_status(*_args, **_kwargs) -> None:
                pass

            @staticmethod
            def _post(*_args, **_kwargs) -> None:
                completed.set()

        page = object.__new__(ProductsPage)
        page.db = FakeDb()
        page.window = FakeWindow()
        page._store_lookup = {"全部店铺": None}
        page.store_var = FixedValue("全部店铺")
        page.search_var = FixedValue("")
        page.listing_time_var = FixedValue("不限")
        page.listing_start_var = FixedValue("")
        page.listing_end_var = FixedValue("")
        page.sort_var = FixedValue("默认排序")
        page.active_tab_key = "sales"
        page._query_generation = 0
        page._fresh_scope = None
        page._fresh_store_ids = set()
        page._query_loading = False
        page._query_dirty = False
        page._query_background()
        self.assertTrue(completed.wait(1))
        self.assertNotEqual(worker_name["value"], "MainThread")
        self.assertIn("langlang-product-query", worker_name["value"])

    def test_product_query_does_not_block_store_login_task(self) -> None:
        from weimi_manager.ui.app import MainWindow

        query_started = threading.Event()
        release_query = threading.Event()
        login_started = threading.Event()
        events: list[str] = []

        class FakeDb:
            @staticmethod
            def start_task_log(**_kwargs):
                return 1

            @staticmethod
            def finish_task_log(**_kwargs):
                pass

        window = object.__new__(MainWindow)
        window._task_running = False
        window._running_tasks = {}
        window._pages = {}
        window._active_page = "products"
        window.diagnostics = SimpleNamespace(record=lambda *_args, **_kwargs: None)
        window.set_status = lambda *_args, **_kwargs: None
        window._post = lambda callback, *args: callback(*args)
        window._finish_task_log = lambda *_args, **_kwargs: None
        window._task_result_log = MainWindow._task_result_log

        def query_work(_progress):
            events.append("query-start")
            query_started.set()
            release_query.wait(2)
            events.append("query-end")
            return "query-done"

        def login_work(_progress):
            events.append("login-start")
            login_started.set()
            return "login-done"

        try:
            self.assertTrue(
                window.run_task(
                    query_work,
                    lambda _result: None,
                    lambda _error: None,
                )
            )
            self.assertTrue(query_started.wait(1))
            window._active_page = "stores"
            self.assertTrue(
                window.run_task(
                    login_work,
                    lambda _result: None,
                    lambda _error: None,
                )
            )
            self.assertTrue(login_started.wait(1))
            self.assertIn("login-start", events)
            self.assertTrue(window._task_running)
        finally:
            release_query.set()
            self.assertTrue(query_started.wait(1))

    def test_same_scope_task_still_blocks_duplicate_work(self) -> None:
        from weimi_manager.ui.app import MainWindow

        first_started = threading.Event()
        release_first = threading.Event()

        window = object.__new__(MainWindow)
        window._task_running = False
        window._running_tasks = {}
        window._pages = {}
        window._active_page = "stores"
        window.diagnostics = SimpleNamespace(record=lambda *_args, **_kwargs: None)
        window.set_status = lambda *_args, **_kwargs: None
        window._post = lambda callback, *args: callback(*args)
        window._finish_task_log = lambda *_args, **_kwargs: None
        window._task_result_log = MainWindow._task_result_log

        def blocking_work(_progress):
            first_started.set()
            release_first.wait(2)
            return "done"

        try:
            self.assertTrue(
                window.run_task(
                    blocking_work,
                    lambda _result: None,
                    lambda _error: None,
                    log_title="refresh-store-data",
                )
            )
            self.assertTrue(first_started.wait(1))
            self.assertFalse(
                window.run_task(
                    lambda _progress: "duplicate",
                    lambda _result: None,
                    lambda _error: None,
                    log_title="refresh-store-data",
                )
            )
        finally:
            release_first.set()

    def test_named_tasks_on_same_page_can_run_concurrently(self) -> None:
        from weimi_manager.ui.app import MainWindow

        first_started = threading.Event()
        second_started = threading.Event()
        release_first = threading.Event()
        first_done = threading.Event()

        window = object.__new__(MainWindow)
        window._task_running = False
        window._running_tasks = {}
        window._pages = {}
        window._active_page = "stores"
        window.diagnostics = SimpleNamespace(record=lambda *_args, **_kwargs: None)
        window.set_status = lambda *_args, **_kwargs: None
        window._post = lambda callback, *args: callback(*args)
        window._finish_task_log = lambda *_args, **_kwargs: None
        window._task_result_log = MainWindow._task_result_log

        def refresh_work(_progress):
            first_started.set()
            release_first.wait(2)
            return "refresh-done"

        def scan_work(_progress):
            second_started.set()
            return "scan-done"

        try:
            self.assertTrue(
                window.run_task(
                    refresh_work,
                    lambda _result: first_done.set(),
                    lambda _error: first_done.set(),
                    log_title="刷新店铺数据和提审额度",
                )
            )
            self.assertTrue(first_started.wait(1))
            self.assertTrue(
                window.run_task(
                    scan_work,
                    lambda _result: None,
                    lambda _error: None,
                    log_title="多店铺扫码登录",
                )
            )
            self.assertTrue(second_started.wait(1))
        finally:
            release_first.set()
        self.assertTrue(first_done.wait(1))

    def test_query_all_stores_loads_uncached_logged_in_shop_products(self) -> None:
        from weimi_manager.shop_private import ShopPrivateClient
        from weimi_manager.ui.pages import ProductsPage

        shop_ids = [self.db.add_store(f"Shop {i}", f"wx-shop-{i}", "") for i in range(4)]
        for store_id in shop_ids[:3]:
            self.db.update_web_status(store_id, "已登录")

        requests = []

        class FakeClient(ShopPrivateClient):
            def __init__(self, _db, store):
                self.store = store
                if store.web_status != "已登录":
                    raise RuntimeError("Fixture shop is not logged in")

            def scan_page(self, tab_key, page=1, page_size=50):
                requests.append((self.store.id, tab_key, page))
                start = (page - 1) * page_size
                return {
                    "totalNum": 251,
                    "productList": [
                        {"spuId": str(i), "title": f"Product {i}", "status": 5}
                        for i in range(start, min(start + page_size, 251))
                    ],
                }

        page = object.__new__(ProductsPage)
        page.db = self.db
        page.window = SimpleNamespace(
            service=SyncService(self.db),
            _task_running=False,
            set_status=lambda *_args, **_kwargs: None,
            run_task=lambda work, done, *_args, **_kwargs: done(work(lambda *_args: None)),
        )
        page._store_lookup = {"全部店铺": None}
        page.store_var = SimpleNamespace(get=lambda: "全部店铺")
        page.active_tab_key = "sales"
        page.cancel_event = threading.Event()
        page._query_generation = 0
        page._query = lambda: None
        renders = []
        page._query_background = lambda: renders.append(self.db.list_products())

        with (
            patch("weimi_manager.service.ShopPrivateClient", FakeClient),
            patch.object(self.db, "get_web_cookies", return_value={"cookies": []}),
            patch("weimi_manager.ui.pages.messagebox.showwarning"),
        ):
            page._query_products()
            page._query_products()

        self.assertEqual(
            requests,
            [(store_id, "sales", number) for store_id in shop_ids[:3] for number in range(1, 7)]
            * 2,
        )
        self.assertEqual([len(rows) for rows in renders], [753, 753])
        self.assertEqual({row["store_id"] for row in renders[0]}, set(shop_ids[:3]))

    def test_window_close_does_not_wait_for_cloud_sync(self) -> None:
        events: list[str] = []
        fake = SimpleNamespace(
            _closing=False,
            account_store_sync=SimpleNamespace(push=lambda: events.append("push")),
            withdraw=lambda: events.append("withdraw"),
            destroy=lambda: events.append("destroy"),
        )
        MainWindow._on_close(fake)
        self.assertEqual(events, ["withdraw", "destroy"])

    def test_window_close_prepares_upload_recovery_before_destroy(self) -> None:
        events: list[str] = []
        fake = SimpleNamespace(
            _closing=False,
            _pages={"upload": SimpleNamespace(prepare_for_close=lambda: events.append("upload"))},
            account_store_sync=SimpleNamespace(push=lambda: events.append("push")),
            withdraw=lambda: events.append("withdraw"),
            destroy=lambda: events.append("destroy"),
        )

        MainWindow._on_close(fake)

        self.assertEqual(events, ["upload", "withdraw", "destroy"])

    def test_product_day_totals_use_pay_count_field(self) -> None:
        client = WechatShopClient("wx-test", "secret")
        client.fetch_shop_product_metrics = lambda *_args, **_kwargs: {
            "total_count": 2,
            "product_list": [
                {
                    "data": {
                        "pay_gmv": "100",
                        "pay_uv": "1",
                        "pay_cnt": "2",
                        "pay_refund_gmv": "20",
                        "product_click_uv": "4",
                    }
                },
                {
                    "data": {
                        "pay_gmv": "50",
                        "pay_uv": "1",
                        "pay_cnt": "1",
                        "pay_refund_gmv": "0",
                        "product_click_uv": "2",
                    }
                },
            ],
        }
        self.assertEqual(
            client.fetch_product_day_totals("20261029"),
            {
                "pay_gmv": 150,
                "pay_uv": 2,
                "pay_order_cnt": 3,
                "pay_refund_gmv": 20,
                "product_click_uv": 6,
            },
        )

    def test_platform_violation_records_read_nested_items(self) -> None:
        client = object.__new__(ShopPrivateClient)

        def fake_get(path, *_args, **_kwargs):
            if "prewarn" in path:
                return {"code": 0, "record": {"items": []}}
            return {
                "code": 0,
                "record": {
                    "items": [
                        {
                            "punishUuid": "R1",
                            "punishReason": "价格违规",
                            "punishObject": [
                                {
                                    "productId": "10001",
                                    "productDesc": "测试商品",
                                }
                            ],
                            "status": 5,
                            "recommendation": "及时改价",
                        }
                    ]
                },
            }

        client._get = fake_get
        client._post = lambda *_args, **_kwargs: {
            "code": 0,
            "preWarnObjects": [],
        }
        rows = client.platform_violation_records()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["record_id"], "R1")
        self.assertEqual(rows[0]["product_id"], "10001")
        self.assertEqual(rows[0]["title"], "测试商品")
        self.assertEqual(rows[0]["reason"], "价格违规")
        self.assertEqual(rows[0]["status"], "超时未申诉")
        self.assertEqual(rows[0]["fix"], "及时改价")

    def test_store_info_uses_get_request(self) -> None:
        transport = GetTransport()
        client = WechatShopClient(
            "wx-test",
            "secret",
            transport=transport,
            sleeper=lambda _: None,
            min_request_interval=0,
        )
        info = client.fetch_store_info()
        self.assertEqual(info["nickname"], "API店铺")
        self.assertEqual(transport.get_calls, 1)

    def test_window_smoke(self) -> None:
        from weimi_manager.config import AppPaths
        from weimi_manager.ui.app import MainWindow
        from weimi_manager.ui.dialogs import (
            CouponFormDialog,
            ProductFormDialog,
            StoreDialog,
        )
        from weimi_manager.ui.skeletons import _display_timestamp

        formatted_timestamp = _display_timestamp(1792921952)
        self.assertRegex(
            formatted_timestamp,
            r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$",
        )
        self.assertNotEqual(formatted_timestamp, "1792921952")
        os.environ["WEIMI_DATA_DIR"] = str(self.root / "ui-data")
        previous_backup_setting = os.environ.get("WEIMI_DISABLE_AUTO_BACKUP")
        previous_update_setting = os.environ.get("WEIMI_DISABLE_AUTO_UPDATE")
        os.environ["WEIMI_DISABLE_AUTO_BACKUP"] = "1"
        os.environ["WEIMI_DISABLE_AUTO_UPDATE"] = "1"
        try:
            window = MainWindow(AppPaths.discover())
            window.update_idletasks()
            window.update()
            self.assertEqual(window.title().split(" · ")[0], "澜琅")
            window.set_status("测试忙碌状态", busy=True)
            window.update_idletasks()
            window.set_status("测试完成", busy=False)
            expected_pages = {
                "workbench",
                "diagnostics",
                "stores",
                "orders",
                "products",
                "upload",
                "operations",
                "customer_service",
                "marketing",
                "storefront",
                "finance",
                "procurement",
                "analytics",
                "automation",
                "settings",
                "account",
                "parsed",
                "order_management",
                "store_groups",
                "double11",
                "violation_blacklist",
                "dynamic",
                "no_traffic",
                "hosted",
                "team",
                "sales_related",
            }
            self.assertIn("stores", window._pages)
            self.assertLess(len(window._pages), len(expected_pages))
            for page_key in expected_pages:
                window.show_page(page_key)
                window.update_idletasks()
            self.assertEqual(set(window._pages), expected_pages)
            window.show_page("stores")
            window.store_page._add_store()
            window.update_idletasks()
            store_dialog = next(
                child
                for child in window.store_page.winfo_children()
                if isinstance(child, StoreDialog)
            )
            store_dialog.name_var.set("自动关闭测试店铺")
            store_dialog.app_id_var.set("wx_test_close_001")
            store_dialog.secret_var.set("x" * 32)
            store_dialog._save()
            window.update_idletasks()
            window.update()
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                window.update()
                if any(
                    store.app_id == "wx_test_close_001"
                    for store in window.db.list_stores()
                ):
                    break
                time.sleep(0.02)
            self.assertFalse(store_dialog.winfo_exists())
            self.assertIsNotNone(
                next(
                    (
                        store
                        for store in window.db.list_stores()
                        if store.app_id == "wx_test_close_001"
                    ),
                    None,
                )
            )
            window.set_status("上一页遗留结果", busy=False)
            window.show_page("finance")
            window.update_idletasks()
            self.assertNotIn("上一页遗留结果", window.status_var.get())
            self.assertTrue(window.status_var.get())
            self.assertFalse(window._task_running)
            window.show_page("storefront")
            window.update_idletasks()
            self.assertNotIn("上一页遗留结果", window.status_var.get())
            self.assertTrue(window.status_var.get())
            self.assertFalse(window._task_running)
            window.show_page("automation")
            window.update_idletasks()
            self.assertEqual(
                str(window.automation_page.run_task_button["state"]),
                "disabled",
            )
            window.show_page("hosted")
            window.update_idletasks()
            self.assertEqual(
                str(window.hosted_page.execute_all_button["state"]),
                "disabled",
            )
            window.show_page("dynamic")
            window.update_idletasks()
            self.assertEqual(window.title_var.get(), "动销建议")
            self.assertTrue(window.dynamic_page.empty_label.winfo_exists())
            window.show_page("sales_related")
            window.update_idletasks()
            self.assertEqual(window.title_var.get(), "动销中心")
            self.assertEqual(
                type(window.sales_related_page).__name__,
                "DongxiaoPage",
            )
            window.show_page("operations")
            window.update_idletasks()
            self.assertTrue(window.operations_page._action_buttons)
            self.assertTrue(
                all(
                    str(action_button["state"]) == "disabled"
                    for action_button in window.operations_page._action_buttons
                )
            )
            window.show_page("procurement")
            window.update_idletasks()
            self.assertEqual(
                str(window.procurement_page.procurement_account_buttons[0]["state"]),
                "disabled",
            )
            window.show_page("no_traffic")
            window.update_idletasks()
            self.assertEqual(
                str(window.no_traffic_page.no_traffic_execute_button["state"]),
                "disabled",
            )
            self.assertEqual(
                str(window.no_traffic_page.no_traffic_select_all_button["state"]),
                "disabled",
            )
            window.show_page("upload")
            window.update_idletasks()
            self.assertEqual(
                str(window.upload_page.task_start_button["state"]),
                "disabled",
            )
            self.assertEqual(
                str(window.upload_page.template_edit_button["state"]),
                "disabled",
            )
            product_form = ProductFormDialog(
                window,
                "测试商品表单",
                {
                    "title": "测试商品",
                    "cats_v2": [{"cat_id": "10000111"}],
                    "head_imgs": [],
                    "desc_info": {"imgs": [], "desc": ""},
                    "skus": [{"price": 100, "stock_num": 1, "sku_attrs": []}],
                    "extra_service": {},
                },
                "add",
                lambda _payload: None,
            )
            product_form.update_idletasks()
            collected = product_form._collect()
            self.assertEqual(collected["title"], "测试商品")
            self.assertEqual(collected["cats_v2"], [{"cat_id": "10000111"}])
            product_form.destroy()
            coupon_form = CouponFormDialog(
                window,
                "测试优惠券表单",
                {
                    "type": 104,
                    "name": "测试优惠券",
                    "discount_info": {"discount_fee": 500},
                    "receive_info": {"total_num": 10},
                    "valid_info": {"valid_type": 2, "valid_day_num": 7},
                },
                "add",
                lambda _payload: None,
            )
            coupon_form.update_idletasks()
            coupon_payload = coupon_form._collect()
            self.assertEqual(coupon_payload["name"], "测试优惠券")
            self.assertEqual(coupon_payload["discount_info"]["discount_fee"], 500)
            coupon_form.destroy()
            window._open_big_screen()
            window.update_idletasks()
            window.update()
            self.assertIsNotNone(window._big_screen_window)
            self.assertTrue(window._big_screen_window.winfo_exists())
            self.assertTrue(bool(window._big_screen_window.attributes("-fullscreen")))
            window._big_screen_window.screen_page.tree.focus_set()
            window._big_screen_window.screen_page.tree.event_generate("<Escape>")
            window.update_idletasks()
            window.update()
            deadline = time.monotonic() + 2
            while (
                window._big_screen_window is not None
                and time.monotonic() < deadline
            ):
                window.update()
                time.sleep(0.01)
            self.assertIsNone(window._big_screen_window)
            window.order_management_page._open_workbench()
            window.update_idletasks()
            workbench = window._order_workbench_window
            self.assertIsNotNone(workbench)
            self.assertTrue(workbench.winfo_exists())
            workbench.show("profit")
            workbench.update_idletasks()
            self.assertIn("利润统计", workbench.heading_var.get())
            workbench.show("messages")
            workbench.update_idletasks()
            self.assertIn("消息管理", workbench.heading_var.get())
            workbench.close()
            window.destroy()
        finally:
            os.environ.pop("WEIMI_DATA_DIR", None)
            if previous_backup_setting is None:
                os.environ.pop("WEIMI_DISABLE_AUTO_BACKUP", None)
            else:
                os.environ["WEIMI_DISABLE_AUTO_BACKUP"] = previous_backup_setting
            if previous_update_setting is None:
                os.environ.pop("WEIMI_DISABLE_AUTO_UPDATE", None)
            else:
                os.environ["WEIMI_DISABLE_AUTO_UPDATE"] = previous_update_setting


class FakeTransport:
    def __init__(self):
        self.token_calls = 0
        self.order_list_calls = 0

    def post_json(self, url: str, payload: dict, timeout: int = 25) -> dict:
        if url.endswith("/cgi-bin/stable_token"):
            self.token_calls += 1
            return {"access_token": "TOKEN", "expires_in": 7200}
        if "/channels/ec/order/list/get" in url:
            self.order_list_calls += 1
            if self.order_list_calls == 1:
                return {"errcode": 0, "order_id_list": ["1"], "next_key": "NEXT", "has_more": True}
            return {"errcode": 0, "order_id_list": ["2"], "next_key": "", "has_more": False}
        raise AssertionError(f"Unexpected URL: {url}")


class WriteTransport:
    def __init__(self):
        self.payloads = []

    def post_json(self, url: str, payload: dict, timeout: int = 25) -> dict:
        if url.endswith("/cgi-bin/stable_token"):
            return {"access_token": "TOKEN", "expires_in": 7200}
        if any(
            path in url
            for path in (
                "/channels/ec/product/listing",
                "/channels/ec/product/delisting",
                "/channels/ec/product/stock/update",
            )
        ):
            self.payloads.append(payload)
            return {"errcode": 0, "errmsg": "ok"}
        raise AssertionError(f"Unexpected URL: {url}")


class GetTransport:
    def __init__(self):
        self.get_calls = 0

    def post_json(self, url: str, payload: dict, timeout: int = 25) -> dict:
        if url.endswith("/cgi-bin/stable_token"):
            return {"access_token": "TOKEN", "expires_in": 7200}
        raise AssertionError(f"Unexpected POST URL: {url}")

    def get_json(self, url: str, timeout: int = 25) -> dict:
        self.get_calls += 1
        if "/channels/ec/basics/info/get" in url:
            return {"errcode": 0, "info": {"nickname": "API店铺"}}
        raise AssertionError(f"Unexpected GET URL: {url}")


def sample_order(store_id: int, order_id: str) -> Order:
    return Order(
        store_id=store_id,
        store_name="测试店铺",
        order_id=order_id,
        status=20,
        status_text="待发货",
        create_time=1700000000,
        update_time=1700000001,
        pay_time=1700000002,
        total_amount=1990,
        product_amount=1990,
        freight_amount=0,
        discount_amount=0,
        buyer_name="张三",
        phone="13800000000",
        address="湖北省武汉市",
        waybill_id="",
        carrier="",
        merchant_notes="",
        items=[
            OrderItem(
                product_id="P1",
                sku_id="S1",
                title="商品",
                sku_count=1,
                sale_price=1990,
            )
        ],
        raw={},
    )


if __name__ == "__main__":
    unittest.main()
