from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from weimi_manager.db import Database
from weimi_manager.models import Order, is_pending_purchase_order
from weimi_manager.procurement import normalize_procurement_records
from weimi_manager.service import SyncService
from weimi_manager.ui.pages import OrdersPage
from weimi_manager.ui.store_page import parse_store_batch_line


class OrdersPageRefreshTests(unittest.TestCase):
    def test_refresh_reloads_stores_and_runs_order_query(self) -> None:
        page = object.__new__(OrdersPage)
        page._reload_stores = mock.Mock()
        page._query = mock.Mock()

        page.refresh()

        page._reload_stores.assert_called_once_with()
        page._query.assert_called_once_with()


class ProcurementAmountTests(unittest.TestCase):
    def test_taobao_decimal_yuan_amount_is_converted_to_integer_cents(self) -> None:
        records = normalize_procurement_records(
            "taobao",
            [{"orderId": "TB-AMOUNT-1", "actualFee": "19.905"}],
        )

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["pay_amount"], 1991)
        self.assertEqual(records[0]["pay_amount_source"], "actualFee")
        self.assertEqual(records[0]["pay_amount_unit"], "cent")

    def test_1688_integer_amount_remains_cents_without_float_rounding(self) -> None:
        records = normalize_procurement_records(
            "1688",
            [{"tradeNo": "ALI-AMOUNT-1", "payAmount": "1990"}],
        )

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["pay_amount"], 1990)
        self.assertEqual(records[0]["pay_amount_source"], "payAmount")
        self.assertEqual(records[0]["pay_amount_unit"], "cent")

    def test_existing_pay_amount_is_preserved_when_database_reopens(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "legacy-amount.db"
            db = Database(path)
            account_id = db.add_procurement_account("1688", "采购账号", "")
            db.upsert_procurement_order(
                account_id,
                "1688",
                {
                    "external_order_id": "LEGACY-AMOUNT-1",
                    "trade_no": "LEGACY-AMOUNT-1",
                    "pay_amount": 1990,
                    "raw": {"payAmount": 1990},
                },
            )

            reopened = Database(path)
            records = reopened.list_procurement_orders(account_id)

            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["pay_amount"], 1990)


class ProcurementAccountSecurityTests(unittest.TestCase):
    def test_account_fields_and_secrets_are_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            db = Database(Path(temp) / "procurement-account.db")
            account_id = db.add_procurement_account(
                "taobao",
                "测试采购账号",
                "buyer-1",
                nickname="采购昵称",
                group_name="一组",
                remark="测试备注",
                auto_login=True,
            )
            db.set_procurement_account_secrets(
                account_id,
                password="test-password",
                alipay_password="alipay-password",
            )
            db.set_procurement_alipay_session(
                account_id,
                {"cookies": [{"name": "session", "value": "cookie"}]},
                "alipay-user",
            )

            account = db.get_procurement_account(account_id)
            assert account is not None
            self.assertEqual(account["nickname"], "采购昵称")
            self.assertEqual(account["group_name"], "一组")
            self.assertEqual(account["remark"], "测试备注")
            self.assertEqual(account["auto_login"], 1)
            self.assertEqual(account["alipay_uid"], "alipay-user")
            self.assertEqual(
                db.get_procurement_account_password(account_id),
                "test-password",
            )
            self.assertEqual(
                db.get_procurement_alipay_password(account_id),
                "alipay-password",
            )
            self.assertIsNotNone(
                db.get_procurement_alipay_session(account_id)
            )

    def test_legacy_procurement_account_table_is_upgraded_with_backup(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "legacy-procurement.db"
            conn = sqlite3.connect(path)
            try:
                conn.execute(
                    """
                    CREATE TABLE procurement_accounts (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        platform TEXT NOT NULL,
                        label TEXT NOT NULL,
                        login_id TEXT NOT NULL DEFAULT '',
                        profile_dir TEXT NOT NULL DEFAULT '',
                        status TEXT NOT NULL DEFAULT '未登录',
                        last_login TEXT,
                        last_sync TEXT,
                        last_error TEXT NOT NULL DEFAULT '',
                        created_at TEXT NOT NULL
                    )
                    """
                )
                conn.execute(
                    """
                    INSERT INTO procurement_accounts (
                        platform, label, login_id, created_at
                    ) VALUES ('1688', '旧账号', 'old-user', '2026-01-01')
                    """
                )
                conn.commit()
            finally:
                conn.close()

            db = Database(path)
            account = db.list_procurement_accounts()[0]

            self.assertEqual(account["nickname"], "")
            self.assertEqual(account["auto_login"], 0)
            self.assertEqual(account["account_type"], 2)
            self.assertTrue(
                path.with_name(
                    "legacy-procurement.db.pre-procurement-account-fields.bak"
                ).exists()
            )


class ProcurementSchemaValidationTests(unittest.TestCase):
    def test_item_name_without_platform_order_identity_is_rejected(self) -> None:
        records = normalize_procurement_records(
            "taobao",
            [{"data": {"itemName": "只有商品标题"}}],
        )

        self.assertEqual(records, [])

    def test_generic_code_without_platform_order_identity_is_rejected(self) -> None:
        records = normalize_procurement_records(
            "1688",
            [{"data": {"code": "A-01"}}],
        )

        self.assertEqual(records, [])

    def test_platform_order_identity_is_sufficient_with_item_details(self) -> None:
        records = normalize_procurement_records(
            "taobao",
            [{"data": {"orderId": "TB-SCHEMA-1", "itemName": "采购商品"}}],
        )

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["external_order_id"], "TB-SCHEMA-1")


class PendingPurchaseTests(unittest.TestCase):
    def test_unpaid_order_is_not_pending_purchase(self) -> None:
        order = {"status": 10, "pay_time": 0}

        self.assertFalse(is_pending_purchase_order(order, {}, False))

    def test_paid_unlinked_order_is_pending_purchase(self) -> None:
        order = {"status": 20, "pay_time": 1_700_000_000}

        self.assertTrue(is_pending_purchase_order(order, {}, False))

    def test_order_without_payment_timestamp_is_not_treated_as_paid(self) -> None:
        order = {"status": 20, "pay_time": 0}

        self.assertFalse(is_pending_purchase_order(order, {}, False))

    def test_refunded_cancelled_or_after_sale_order_is_protected(self) -> None:
        for order in (
            {"status": 200, "pay_time": 1_700_000_000},
            {"status": 250, "pay_time": 1_700_000_000},
        ):
            self.assertFalse(is_pending_purchase_order(order, {}, False))
        self.assertFalse(
            is_pending_purchase_order(
                {"status": 20, "pay_time": 1_700_000_000},
                {},
                True,
            )
        )

    def test_linked_procurement_order_is_not_pending_purchase(self) -> None:
        order = {"status": 20, "pay_time": 1_700_000_000}

        self.assertFalse(
            is_pending_purchase_order(
                order,
                {"purchase_order_no": "PROC-1"},
                False,
            )
        )


def _test_order(store_id: int, order_id: str) -> Order:
    return Order(
        store_id=store_id,
        store_name="测试店铺",
        order_id=order_id,
        status=20,
        status_text="待发货",
        create_time=1_700_000_000,
        update_time=1_700_000_001,
        pay_time=1_700_000_002,
        total_amount=100,
        product_amount=100,
        freight_amount=0,
        discount_amount=0,
        buyer_name="测试买家",
        phone="",
        address="",
        waybill_id="",
        carrier="",
        merchant_notes="",
    )


class ProcurementLinkScopeTests(unittest.TestCase):
    def test_same_external_order_can_be_linked_for_two_stores(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            db = Database(Path(temp) / "round1.db")
            first_store = db.add_store("店铺一", "wx-scope-1", "secret-1")
            second_store = db.add_store("店铺二", "wx-scope-2", "secret-2")
            db.upsert_order(_test_order(first_store, "ORDER-SAME"))
            db.upsert_order(_test_order(second_store, "ORDER-SAME"))
            account_id = db.add_procurement_account("1688", "采购账号", "")
            procurement_id = db.upsert_procurement_order(
                account_id,
                "1688",
                {
                    "external_order_id": "PROC-1",
                    "trade_no": "PROC-1",
                    "raw": {},
                },
            )

            db.upsert_procurement_link(first_store, "ORDER-SAME", procurement_id)
            db.upsert_procurement_link(second_store, "ORDER-SAME", procurement_id)

            self.assertEqual(len(db.list_procurement_links(first_store)), 1)
            self.assertEqual(len(db.list_procurement_links(second_store)), 1)

    def test_legacy_link_table_is_backed_up_and_migrated_without_data_loss(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "round1.db"
            db = Database(path)
            first_store = db.add_store("店铺一", "wx-migrate-1", "secret-1")
            db.upsert_order(_test_order(first_store, "ORDER-MIGRATE"))
            account_id = db.add_procurement_account("1688", "采购账号", "")
            procurement_id = db.upsert_procurement_order(
                account_id,
                "1688",
                {
                    "external_order_id": "PROC-MIGRATE",
                    "trade_no": "PROC-MIGRATE",
                    "raw": {},
                },
            )
            db.upsert_procurement_link(
                first_store,
                "ORDER-MIGRATE",
                procurement_id,
                local_note="保留备注",
            )
            with db.connect() as conn:
                conn.execute(
                    "ALTER TABLE procurement_links "
                    "RENAME TO procurement_links_current"
                )
                conn.execute(
                    """
                    CREATE TABLE procurement_links (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        store_id INTEGER NOT NULL,
                        order_id TEXT NOT NULL,
                        procurement_order_id INTEGER NOT NULL,
                        match_type TEXT NOT NULL DEFAULT 'manual',
                        confidence INTEGER NOT NULL DEFAULT 100,
                        local_note TEXT NOT NULL DEFAULT '',
                        sync_wechat_note INTEGER NOT NULL DEFAULT 0,
                        wechat_note_synced_at TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        UNIQUE(order_id, procurement_order_id),
                        FOREIGN KEY (store_id, order_id)
                            REFERENCES orders(store_id, order_id)
                            ON DELETE CASCADE,
                        FOREIGN KEY (procurement_order_id)
                            REFERENCES procurement_orders(id)
                            ON DELETE CASCADE
                    )
                    """
                )
                conn.execute(
                    """
                    INSERT INTO procurement_links
                    SELECT * FROM procurement_links_current
                    """
                )
                conn.execute("DROP TABLE procurement_links_current")

            migrated = Database(path)

            links = migrated.list_procurement_links(first_store)
            self.assertEqual(len(links), 1)
            self.assertEqual(links[0]["local_note"], "保留备注")
            with migrated.connect() as conn:
                self.assertEqual(
                    conn.execute("PRAGMA integrity_check").fetchone()[0],
                    "ok",
                )
                self.assertEqual(
                    conn.execute("PRAGMA foreign_key_check").fetchall(),
                    [],
                )
            self.assertTrue(
                path.with_name("round1.db.pre-ll003.bak").exists()
            )


class WebOnlyStoreTests(unittest.TestCase):
    def test_web_only_store_can_be_created_without_app_secret(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            db = Database(Path(temp) / "web-only.db")

            store_id = db.add_store("网页店铺", "wx-web-only", "")
            store = db.get_store(store_id)

            self.assertIsNotNone(store)
            assert store is not None
            self.assertEqual(store.status, "仅网页登录")
            self.assertEqual(db.get_store_secret(store_id)[1], "")
            with db.connect() as conn:
                protected = conn.execute(
                    "SELECT secret_protected FROM stores WHERE id=?",
                    (store_id,),
                ).fetchone()[0]
            self.assertEqual(protected, b"")
            with self.assertRaisesRegex(ValueError, "仅使用网页登录"):
                SyncService(db).client_for(store)

    def test_blank_secret_import_does_not_erase_existing_api_secret(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            db = Database(Path(temp) / "web-only-preserve.db")
            db.add_store("接口店铺", "wx-has-secret", "real-secret")

            created, updated, errors = db.upsert_stores(
                [("接口店铺", "wx-has-secret", "")]
            )

            self.assertEqual((created, updated, errors), (0, 1, []))
            self.assertEqual(
                db.get_store_secret(
                    db.list_stores("wx-has-secret")[0].id
                )[1],
                "real-secret",
            )


class StoreBatchLineTests(unittest.TestCase):
    def test_web_only_name_and_app_id_do_not_shift_name_into_secret(self) -> None:
        self.assertEqual(
            parse_store_batch_line("网页店铺 wx-web-only-1"),
            ("网页店铺", "wx-web-only-1", ""),
        )

    def test_full_api_credentials_still_parse_secret(self) -> None:
        secret = "a" * 32

        self.assertEqual(
            parse_store_batch_line(f"接口店铺 wx-api-1 {secret}"),
            ("接口店铺", "wx-api-1", secret),
        )


if __name__ == "__main__":
    unittest.main()
