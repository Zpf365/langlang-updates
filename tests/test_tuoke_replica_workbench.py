from __future__ import annotations

import tempfile
import time
import tkinter as tk
import unittest
from pathlib import Path
from types import SimpleNamespace

from weimi_manager.dashboard_metrics import build_dashboard_snapshot
from weimi_manager.db import Database
from weimi_manager.models import Order, OrderItem, is_unclaimed_gift_order
from weimi_manager.service import SyncService
from weimi_manager.ui.order_flag_dialog import FLAG_COLORS, OrderFlagNoteDialog
from weimi_manager.ui.order_workbench import ProfitStatsPage, StoreAfterSalePage
from weimi_manager.ui.pages import OrdersPage
from weimi_manager.ui.product_card_list import ProductCardList
from weimi_manager.ui.product_link_dialog import auto_match_skus
from weimi_manager.ui.virtual_order_card_list import OrderCardList


def make_order(
    store_id: int,
    order_id: str,
    status: int,
    amount: int,
    *,
    raw: dict | None = None,
) -> Order:
    now = int(time.time())
    return Order(
        store_id=store_id,
        store_name="测试店铺",
        order_id=order_id,
        status=status,
        status_text=str(status),
        create_time=now,
        update_time=now,
        pay_time=now,
        total_amount=amount,
        product_amount=amount,
        freight_amount=0,
        discount_amount=0,
        buyer_name="买家",
        phone="",
        address="",
        waybill_id="",
        carrier="",
        merchant_notes="",
        raw=raw or {},
    )


class DashboardTransactionConsistencyTests(unittest.TestCase):
    def test_unclaimed_gifts_do_not_enter_receipts_or_charts(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            db = Database(Path(temp) / "dashboard.db")
            store_id = db.add_store("测试店铺", "wx-dashboard", "secret")
            db.upsert_order(
                make_order(store_id, "PAID-1", 20, 10_000)
            )
            db.upsert_order(
                make_order(
                    store_id,
                    "GIFT-1",
                    12,
                    50_000,
                    raw={"is_present": True},
                )
            )
            db.upsert_order(
                make_order(store_id, "REFUND-1", 200, 3_000)
            )

            snapshot = build_dashboard_snapshot(
                db,
                "今日",
                "全部数据",
            )

            self.assertEqual(snapshot.store_orders, 1)
            self.assertEqual(snapshot.store_receipt, 10_000)
            self.assertEqual(sum(snapshot.today_hourly), 1)
            self.assertEqual(snapshot.status_counts["退款中"], 1)
            self.assertEqual(snapshot.status_counts["异常订单"], 0)


class AfterSaleWorkbenchTests(unittest.TestCase):
    def test_after_sale_tabs_cover_refund_return_exchange_and_reject(self) -> None:
        self.assertTrue(
            StoreAfterSalePage._tab_matches(
                {
                    "status": "MERCHANT_PROCESSING",
                    "status_text": "商家受理中",
                    "after_sale_type": "REFUND",
                },
                "仅退款",
            )
        )
        self.assertTrue(
            StoreAfterSalePage._tab_matches(
                {
                    "status": "USER_WAIT_RETURN",
                    "status_text": "待买家退货",
                    "after_sale_type": "RETURN",
                },
                "退货退款",
            )
        )
        self.assertTrue(
            StoreAfterSalePage._tab_matches(
                {
                    "status": "MERCHANT_REJECT_EXCHANGE",
                    "status_text": "商家拒绝换货",
                    "after_sale_type": "EXCHANGE",
                },
                "换货",
            )
        )
        self.assertTrue(
            StoreAfterSalePage._tab_matches(
                {
                    "status": "MERCHANT_REJECT_REFUND",
                    "status_text": "商家拒绝退款",
                },
                "拒绝售后",
            )
        )

    def test_local_remove_does_not_delete_the_order(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            db = Database(Path(temp) / "after-sale.db")
            store_id = db.add_store("测试店铺", "wx-after-sale", "secret")
            db.upsert_order(make_order(store_id, "ORDER-1", 20, 1_000))
            db.upsert_after_sale(
                store_id,
                {
                    "after_sale_order_id": "AS-1",
                    "order_id": "ORDER-1",
                    "status": "MERCHANT_PROCESSING",
                    "type": "REFUND",
                    "refund_info": {"amount": 500},
                },
            )

            db.delete_after_sale(store_id, "AS-1")

            self.assertEqual(db.list_after_sales(store_id), [])
            self.assertIsNotNone(db.get_order(store_id, "ORDER-1"))


class TransitCaseRepositoryTests(unittest.TestCase):
    def test_transfer_case_can_be_created_listed_and_updated(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            db = Database(Path(temp) / "transfer.db")
            store_id = db.add_store("测试店铺", "wx-transfer", "secret")
            db.upsert_order(make_order(store_id, "ORDER-T1", 20, 2_000))

            case_id = db.create_transfer_case(
                {
                    "case_type": "internal",
                    "store_id": store_id,
                    "order_id": "ORDER-T1",
                    "source_order_no": "SOURCE-1",
                    "transfer_order_no": "TRANSFER-1",
                    "work_order_no": "WORK-1",
                    "tracking_no": "TRACK-1",
                    "warehouse": "一仓",
                    "status": "退货待入仓",
                    "status_text": "退货待入仓",
                    "note": "测试",
                }
            )

            rows = db.list_transfer_cases(case_type="internal")
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["store_name"], "测试店铺")
            self.assertEqual(rows[0]["transfer_order_no"], "TRANSFER-1")

            db.update_transfer_case(
                case_id,
                status="退货已入仓",
                status_text="退货已入仓",
            )
            updated = db.list_transfer_cases(case_type="internal")[0]
            self.assertEqual(updated["status"], "退货已入仓")

    def test_transfer_cases_survive_database_reopen(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "transfer-reopen.db"
            db = Database(path)
            store_id = db.add_store("测试店铺", "wx-transfer-2", "secret")
            db.create_transfer_case(
                {
                    "case_type": "detention",
                    "store_id": store_id,
                    "transfer_order_no": "TRANSFER-2",
                    "tracking_no": "TRACK-2",
                    "status": "无头件",
                }
            )

            reopened = Database(path)
            rows = reopened.list_transfer_cases(case_type="detention")

            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["transfer_order_no"], "TRANSFER-2")

    def test_transfer_schema_upgrade_backs_up_existing_database(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "transfer-backup.db"
            db = Database(path)
            db.add_store("测试店铺", "wx-transfer-3", "secret")
            with db.connect() as conn:
                conn.execute("DROP TABLE transfer_cases")

            reopened = Database(path)

            self.assertTrue(
                path.with_name("transfer-backup.db.pre-transfer-cases.bak").exists()
            )
            self.assertEqual(
                reopened.list_transfer_cases(case_type="internal"),
                [],
            )


class ProfitPageHelperTests(unittest.TestCase):
    def test_fee_value_reads_nested_explicit_amounts_only(self) -> None:
        raw = {
            "order_detail": {
                "price_info": {
                    "commission_fee": 120,
                    "platform_fee": 35,
                }
            }
        }

        self.assertEqual(
            ProfitStatsPage._fee_value(raw, ("commission_fee",)),
            120,
        )
        self.assertEqual(
            ProfitStatsPage._fee_value(raw, ("platform_fee",)),
            35,
        )
        self.assertEqual(
            ProfitStatsPage._fee_value(raw, ("missing_fee",)),
            0,
        )

    def test_unknown_gift_state_is_treated_as_unclaimed(self) -> None:
        self.assertTrue(is_unclaimed_gift_order({"status": 12}))
        self.assertFalse(
            is_unclaimed_gift_order(
                {
                    "status": 12,
                    "raw_json": '{"is_present": false}',
                }
            )
        )


class ProductSkuLinkTests(unittest.TestCase):
    def test_product_links_and_sku_mappings_persist(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "product-links.db"
            db = Database(path)
            store_id = db.add_store("关联店铺", "wx-product-links", "secret")
            link_id = db.upsert_product_purchase_link(
                store_id,
                "PRODUCT-1",
                {
                    "source_url": "https://detail.1688.com/offer/123456.html",
                    "platform": "1688",
                    "use_transfer_warehouse": True,
                    "commission_pct": 8.5,
                    "note": "主链接",
                    "address_info": {
                        "recipient_name": "采购人",
                        "recipient_phone": "13800000000",
                        "recipient_address": "测试地址",
                    },
                    "source_product_id": "123456",
                    "source_skus": [
                        {
                            "out_sku_id": "SOURCE-SKU-1",
                            "sale_price": 1000,
                            "sku_attrs": [
                                {"attr_key": "颜色", "attr_value": "红色"}
                            ],
                        }
                    ],
                },
            )
            db.replace_product_sku_mappings(
                store_id,
                "PRODUCT-1",
                [
                    {
                        "store_sku_id": "STORE-SKU-1",
                        "purchase_link_id": link_id,
                        "purchase_sku_id": "SOURCE-SKU-1",
                        "purchase_attrs": [
                            {"attr_key": "颜色", "attr_value": "红色"}
                        ],
                        "purchase_price": 1000,
                    }
                ],
            )

            reopened = Database(path)
            links = reopened.list_product_purchase_links(
                store_id,
                "PRODUCT-1",
            )
            mappings = reopened.list_product_sku_mappings(
                store_id,
                "PRODUCT-1",
            )
            summary = reopened.product_purchase_link_map()

            self.assertEqual(len(links), 1)
            self.assertEqual(links[0]["commission_pct"], 8.5)
            self.assertIn("测试地址", links[0]["address_info_json"])
            self.assertEqual(len(mappings), 1)
            self.assertEqual(
                summary[(store_id, "PRODUCT-1")]["mapping_count"],
                1,
            )

    def test_auto_match_uses_attributes_before_price(self) -> None:
        store_skus = [
            {
                "sku_id": "STORE-RED",
                "sale_price": 1200,
                "sku_attrs": [{"attr_key": "颜色", "attr_value": "红色"}],
            },
            {
                "sku_id": "STORE-BLUE",
                "sale_price": 1300,
                "sku_attrs": [{"attr_key": "颜色", "attr_value": "蓝色"}],
            },
        ]
        source_skus = [
            {
                "out_sku_id": "SOURCE-BLUE",
                "sale_price": 1200,
                "sku_attrs": [{"attr_key": "颜色", "attr_value": "蓝色"}],
            },
            {
                "out_sku_id": "SOURCE-RED",
                "sale_price": 1300,
                "sku_attrs": [{"attr_key": "颜色", "attr_value": "红色"}],
            },
        ]

        matches = auto_match_skus(store_skus, source_skus)

        self.assertEqual(matches["STORE-RED"]["out_sku_id"], "SOURCE-RED")
        self.assertEqual(matches["STORE-BLUE"]["out_sku_id"], "SOURCE-BLUE")

    def test_product_link_creates_auto_dispatch_task_for_paid_order(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            db = Database(Path(temp) / "dispatch.db")
            store_id = db.add_store("派单店铺", "wx-dispatch", "secret")
            link_id = db.upsert_product_purchase_link(
                store_id,
                "PRODUCT-DISPATCH",
                {
                    "source_url": "https://detail.1688.com/offer/999999.html",
                    "platform": "1688",
                    "source_skus": [
                        {
                            "out_sku_id": "SOURCE-RED-M",
                            "sale_price": 1000,
                            "sku_attrs": [
                                {"attr_key": "颜色", "attr_value": "红色"}
                            ],
                        }
                    ],
                },
            )
            db.replace_product_sku_mappings(
                store_id,
                "PRODUCT-DISPATCH",
                [
                    {
                        "store_sku_id": "STORE-RED-M",
                        "purchase_link_id": link_id,
                        "purchase_sku_id": "SOURCE-RED-M",
                        "purchase_price": 1000,
                    }
                ],
            )
            order = make_order(store_id, "ORDER-DISPATCH", 20, 2_000)
            order.items = [
                OrderItem(
                    product_id="PRODUCT-DISPATCH",
                    sku_id="STORE-RED-M",
                    title="派单商品",
                    sku_count=2,
                    sale_price=2_000,
                )
            ]
            db.upsert_order(order)

            db.sync_auto_dispatch_tasks()
            tasks = db.list_order_dispatch_tasks(
                store_id,
                "ORDER-DISPATCH",
            )

            self.assertEqual(len(tasks), 1)
            self.assertEqual(tasks[0]["purchase_link_id"], link_id)
            self.assertEqual(tasks[0]["store_sku_id"], "STORE-RED-M")
            self.assertEqual(tasks[0]["quantity"], 2)
            self.assertEqual(tasks[0]["status"], "pending")

    def test_auto_dispatch_requires_mapping_when_multiple_links_exist(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            db = Database(Path(temp) / "dispatch-multiple.db")
            store_id = db.add_store("多链接店铺", "wx-dispatch-multiple", "secret")
            for suffix in ("A", "B"):
                db.upsert_product_purchase_link(
                    store_id,
                    "PRODUCT-MULTI",
                    {
                        "source_url": f"https://detail.1688.com/offer/{suffix}.html",
                        "platform": "1688",
                    },
                )
            order = make_order(store_id, "ORDER-MULTI", 20, 2_000)
            order.items = [
                OrderItem(
                    product_id="PRODUCT-MULTI",
                    sku_id="STORE-SKU-UNMAPPED",
                    title="未映射商品",
                    sku_count=1,
                    sale_price=2_000,
                )
            ]
            db.upsert_order(order)

            db.sync_auto_dispatch_tasks()

            self.assertEqual(
                db.list_order_dispatch_tasks(store_id, "ORDER-MULTI"),
                [],
            )

    def test_auto_dispatch_allows_single_link_without_sku_mapping(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            db = Database(Path(temp) / "dispatch-single.db")
            store_id = db.add_store("单链接店铺", "wx-dispatch-single", "secret")
            link_id = db.upsert_product_purchase_link(
                store_id,
                "PRODUCT-SINGLE",
                {
                    "source_url": "https://detail.1688.com/offer/SINGLE.html",
                    "platform": "1688",
                },
            )
            order = make_order(store_id, "ORDER-SINGLE", 20, 1_000)
            order.items = [
                OrderItem(
                    product_id="PRODUCT-SINGLE",
                    sku_id="STORE-DEFAULT",
                    title="默认规格商品",
                    sku_count=1,
                    sale_price=1_000,
                )
            ]
            db.upsert_order(order)

            db.sync_auto_dispatch_tasks()
            tasks = db.list_order_dispatch_tasks(store_id, "ORDER-SINGLE")

            self.assertEqual(len(tasks), 1)
            self.assertEqual(tasks[0]["purchase_link_id"], link_id)


class SingleOrderSyncTests(unittest.TestCase):
    def test_sync_order_fetches_and_saves_only_requested_order(self) -> None:
        fetched: list[str] = []
        saved: list[Order] = []
        expected = make_order(1, "ORDER-SINGLE-SYNC", 20, 1_000)

        class FakeClient:
            @staticmethod
            def fetch_order(_store, order_id):
                fetched.append(str(order_id))
                return expected

        service = object.__new__(SyncService)
        service.client_for = lambda _store: FakeClient()
        service.db = SimpleNamespace(
            upsert_order=lambda order: saved.append(order),
            sync_auto_dispatch_tasks=lambda: 0,
        )

        result = service.sync_order(
            SimpleNamespace(id=1, app_id="wx-single-order"),
            "ORDER-SINGLE-SYNC",
        )

        self.assertEqual(fetched, ["ORDER-SINGLE-SYNC"])
        self.assertEqual(saved, [expected])
        self.assertEqual(result.count, 1)
        self.assertEqual(result.success, 1)


class OrderProductImageTests(unittest.TestCase):
    def test_nested_product_thumbnail_is_extracted(self) -> None:
        payload = {
            "product_infos": [
                {
                    "product_id": "P-1",
                    "sku_list": [
                        {
                            "thumb_url": (
                                "https://mmecimage.cn/p/test-product.jpg"
                            )
                        }
                    ],
                }
            ]
        }

        self.assertEqual(
            OrdersPage._payload_image_url(payload),
            "https://mmecimage.cn/p/test-product.jpg",
        )

    def test_order_image_falls_back_to_cached_product_head_image(self) -> None:
        class FakeDb:
            @staticmethod
            def get_product(_store_id, _product_id):
                return {
                    "head_img": "https://mmecimage.cn/p/fallback.jpg",
                    "raw_json": "{}",
                }

        page = object.__new__(OrdersPage)
        page.db = FakeDb()
        page._order_image_cache = {}

        value = page._order_image_url(
            {"store_id": 1, "order_id": "ORDER-1"},
            {"product_id": "PRODUCT-1"},
        )

        self.assertEqual(value, "https://mmecimage.cn/p/fallback.jpg")


class OrderWorkflowStateTests(unittest.TestCase):
    @staticmethod
    def _page(stage: str = "", linked: bool = False):
        page = object.__new__(OrdersPage)
        page._workbench = {
            (1, "ORDER-1"): {
                "fulfillment_stage": stage,
                "purchase_order_no": "PROC-1" if linked else "",
                "is_marked_sent": 0,
            }
        }
        page._order_account_map = {(1, "ORDER-1")} if linked else set()
        page._dispatch_map = {}
        page._after_sale_order_ids = set()
        return page

    @staticmethod
    def _order(status: int) -> dict:
        return {
            "store_id": 1,
            "order_id": "ORDER-1",
            "status": status,
            "pay_time": 1_700_000_000,
            "create_time": int(time.time()),
            "update_time": int(time.time()),
        }

    def test_primary_tabs_are_mutually_exclusive(self) -> None:
        pending = self._page()
        self.assertEqual(
            pending._order_flow_stage(self._order(20)),
            "pending_purchase",
        )

        purchased = self._page(linked=True)
        self.assertEqual(
            purchased._order_flow_stage(self._order(20)),
            "purchased_pending_shipment",
        )

        shipped = self._page(stage="shipped", linked=True)
        self.assertEqual(
            shipped._order_flow_stage(self._order(20)),
            "shipped",
        )

        completed = self._page(stage="completed", linked=True)
        self.assertEqual(
            completed._order_flow_stage(self._order(20)),
            "completed",
        )

    def test_exception_stage_overrides_pending_stage(self) -> None:
        page = self._page(stage="exception")
        self.assertEqual(
            page._order_flow_stage(self._order(20)),
            "exception",
        )

    def test_auto_dispatch_alone_does_not_mark_order_purchased(self) -> None:
        page = self._page()
        page._dispatch_map = {(1, "ORDER-1"): [{"status": "pending"}]}
        self.assertEqual(
            page._order_flow_stage(self._order(20)),
            "pending_purchase",
        )

    def test_linked_procurement_numbers_are_visible_and_searchable(self) -> None:
        page = self._page(linked=True)
        page._order_purchase_map = {
            (1, "ORDER-1"): ["1688-ORDER-1", "1688-ORDER-2"]
        }
        page._workbench[(1, "ORDER-1")]["purchase_order_no"] = "MANUAL-1"

        numbers = page._purchase_numbers_for(self._order(20))

        self.assertEqual(
            numbers,
            ["MANUAL-1", "1688-ORDER-1", "1688-ORDER-2"],
        )
        self.assertNotIn("MANUAL-1", page._order_purchase_map[(1, "ORDER-1")])

    def test_flags_notes_and_workflow_stage_persist(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            db = Database(Path(temp) / "order-workflow.db")
            store_id = db.add_store("流程店铺", "wx-order-flow", "secret")
            db.upsert_order(make_order(store_id, "ORDER-FLOW", 20, 2_000))

            db.update_order_workbench(
                store_id,
                "ORDER-FLOW",
                note="软件备注",
                flag_color="红",
                ignored=1,
                fulfillment_stage="purchased",
                purchased_at="2026-11-15T12:00:00",
            )
            row = db.get_order_workbench_row(store_id, "ORDER-FLOW")

            self.assertIsNotNone(row)
            assert row is not None
            self.assertEqual(row["note"], "软件备注")
            self.assertEqual(row["flag_color"], "红")
            self.assertEqual(row["ignored"], 1)
            self.assertEqual(row["fulfillment_stage"], "purchased")

    def test_order_workflow_schema_upgrade_creates_backup(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "workflow-upgrade.db"
            db = Database(path)
            db.add_store("升级店铺", "wx-workflow-upgrade", "secret")
            with db.connect() as conn:
                conn.execute("DROP TABLE order_dispatch_tasks")

            reopened = Database(path)

            self.assertTrue(
                path.with_name(
                    "workflow-upgrade.db.pre-order-workflow.bak"
                ).exists()
            )
            self.assertEqual(reopened.list_order_dispatch_tasks(), [])


class OrderFlagDialogTests(unittest.TestCase):
    def test_flag_dialog_defaults_to_software_note_and_has_six_colors(self) -> None:
        root = tk.Tk()
        root.withdraw()
        saved: list[dict[str, object]] = []
        try:
            dialog = OrderFlagNoteDialog(
                root,
                count=1,
                current_flag="红",
                current_software_note="软件备注",
                current_store_note="店铺备注",
                on_save=saved.append,
            )
            dialog.withdraw()

            self.assertEqual(dialog.note_type_var.get(), "软件备注")
            self.assertEqual(dialog.flag_var.get(), "红")
            self.assertEqual(dialog.text.get("1.0", "end").strip(), "软件备注")
            self.assertEqual(set(FLAG_COLORS), {"红", "黄", "蓝", "绿", "紫", "橙"})

            dialog._switch_note_type()
            dialog.note_type_var.set("店铺备注")
            dialog._switch_note_type()
            self.assertEqual(dialog.text.get("1.0", "end").strip(), "店铺备注")

            dialog.text.delete("1.0", "end")
            dialog.text.insert("1.0", "异常订单备注")
            dialog._save()
            self.assertEqual(
                saved,
                [
                    {
                        "flag": "红",
                        "scope": "店铺备注",
                        "note": "异常订单备注",
                        "sync_store": False,
                    }
                ],
            )
        finally:
            root.destroy()


class CardActionVisibilityTests(unittest.TestCase):
    @staticmethod
    def _button_texts(widget) -> list[str]:
        result: list[str] = []
        for child in widget.winfo_children():
            if isinstance(child, tk.Button):
                result.append(str(child.cget("text")))
            result.extend(CardActionVisibilityTests._button_texts(child))
        return result

    def test_order_card_exposes_decrypt_copy_and_address_edit_actions(self) -> None:
        try:
            root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(str(exc))
        root.withdraw()
        try:
            cards = OrderCardList(root, lambda _action, _item: None)
            cards.set_rows(
                [
                    {
                        "iid": "0",
                        "row": {
                            "store_id": 1,
                            "order_id": "ORDER-1",
                        },
                        "status_text": "买家已付款",
                        "created_text": "2026-11-15 10:00",
                        "product_title": "测试商品",
                        "product_lines": [],
                        "buyer_name": "买家",
                        "buyer_detail": "测试地址",
                        "notes": [],
                    }
                ]
            )
            root.update_idletasks()
            labels = cards.action_labels()
            self.assertIn("复制", labels)
            self.assertIn("⧉", labels)
            self.assertIn("◉", labels)
            self.assertIn("✎", labels)
            self.assertIn("补录采购订单", labels)
        finally:
            root.destroy()

    def test_product_card_exposes_copy_edit_and_source_replacement(self) -> None:
        try:
            root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(str(exc))
        root.withdraw()
        try:
            cards = ProductCardList(root, lambda _action, _index: None)
            cards.set_rows(
                [
                    {
                        "iid": "0",
                        "index": 0,
                        "title": "测试商品",
                        "merchant_code": "1098691125768",
                        "product_id": "WX-1",
                        "store": "测试店铺",
                        "price": "¥10.00",
                        "sold": "0",
                        "has_sku": "是",
                        "status": "销售中",
                        "group": "",
                        "related": "未关联",
                        "created_at": "2026-11-15",
                    }
                ]
            )
            root.update_idletasks()
            labels = self._button_texts(cards)
            self.assertIn("复制标题", labels)
            self.assertIn("改标题", labels)
            self.assertIn("关联/更换上家", labels)
            self.assertGreaterEqual(labels.count("复制"), 2)
        finally:
            root.destroy()


if __name__ == "__main__":
    unittest.main()
