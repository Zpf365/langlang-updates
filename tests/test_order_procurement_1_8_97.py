from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from weimi_manager.db import Database
from weimi_manager.models import Order, OrderItem, SyncResult
from weimi_manager.webui.controller import NextUiController


class ProcurementService:
    def __init__(self, database: Database):
        self.db = database
        self.opened = []
        self.delivery_updates = []

    def inspect_procurement_product(self, account_id, url):
        return {
            "platform": "1688",
            "source_product_id": "9001",
            "source_url": url,
            "title": "采购商品",
            "main_image": "",
            "skus": [
                {
                    "sku_id": "SOURCE-BLACK",
                    "label": "黑色 / XL",
                    "attrs": [{"attr_key": "颜色", "attr_value": "黑色"}],
                    "price": 4680,
                    "stock": 20,
                }
            ],
        }

    def open_procurement_product(self, account_id, url, attrs):
        self.opened.append((account_id, url, attrs))
        return {"product_url": url, "selected_attrs": attrs}

    def sync_procurement_account(self, account_id):
        self.db.upsert_procurement_order(
            account_id,
            "1688",
            {
                "external_order_id": "PURCHASE-NEW-1",
                "trade_no": "PURCHASE-NEW-1",
                "mail_no": "YT123456",
                "carrier": "YUNDA",
                "status": "shipped",
                "status_text": "已发货",
                "goods_title": "采购商品",
                "goods_count": 1,
                "pay_amount": 4680,
            },
        )
        return SyncResult("procurement", 1, "ok", success=1)

    def update_order_delivery_packages(self, store, order_id, packages, product_infos):
        self.delivery_updates.append(
            (store.id, order_id, packages, product_infos)
        )
        first = packages[0]
        self.db.update_order_delivery(
            store.id,
            order_id,
            first["waybill_id"],
            first["delivery_id"],
            21,
            "部分发货",
        )

    def refresh_order_official(self, store, order_id):
        return {"order_id": order_id}


class CapturingAnalytics:
    def __init__(self) -> None:
        self.events = []

    def emit(
        self,
        module_key,
        event_type,
        payload=None,
        *,
        store_ref="",
        batch_id="",
        occurred_at=None,
        observed=True,
    ):
        self.events.append(
            {
                "module": module_key,
                "event_type": event_type,
                "payload": dict(payload or {}),
                "store_ref": store_ref,
                "batch_id": batch_id,
                "occurred_at": occurred_at,
                "observed": observed,
            }
        )
        return True

    def defer(self, _task) -> bool:
        return False


def _controller(tmp_path: Path):
    database = Database(tmp_path / "procurement-flow.db")
    store_id = database.add_store("采购测试店", "wx-procurement", "secret")
    account_id = database.add_procurement_account(
        "1688",
        "采购一号",
        "buyer-1688",
    )
    database.set_procurement_session(
        account_id,
        {"cookies": [{"name": "cookie2", "value": "x"}]},
        "已登录",
        "",
    )
    database.upsert_order(
        Order(
            store_id=store_id,
            store_name="采购测试店",
            order_id="ORDER-PROC-1",
            status=20,
            status_text="待发货",
            create_time=1_700_000_000,
            update_time=1_700_000_010,
            pay_time=1_700_000_005,
            total_amount=6800,
            product_amount=6800,
            freight_amount=0,
            discount_amount=0,
            buyer_name="隐私买家",
            phone="13800000000",
            address="隐私地址",
            waybill_id="",
            carrier="",
            merchant_notes="",
            items=[
                OrderItem(
                    product_id="P-1",
                    sku_id="SKU-1",
                    title="商品一",
                    sku_count=1,
                    sale_price=6800,
                    sku_attributes="黑色 / XL",
                )
            ],
            raw={
                "order_detail": {
                    "delivery_info": {
                        "address_info": {
                            "user_name": "隐私买家",
                            "tel_number": "13800000000",
                            "province_name": "浙江省",
                            "city_name": "杭州市",
                            "county_name": "西湖区",
                            "street_name": "",
                            "detail_info": "隐私地址",
                        }
                    },
                    "settle_info": {"predict_commission_fee": 120},
                }
            },
        )
    )
    service = ProcurementService(database)
    context = SimpleNamespace(
        account_id="220a9616-2346-441b-9e3f-f5f4f5b824fd",
        username="zpfly",
        offline=False,
        member_role="owner",
        permissions={},
        entitlements={},
        tokens=SimpleNamespace(access_token="token"),
    )
    controller = NextUiController(
        database,
        product_service=service,
        auth_context=context,
        now_provider=lambda: __import__("datetime").datetime(2026, 11, 25, 12, 0, 0),
    )
    return controller, service, store_id, account_id


def test_preview_matches_sku_and_saves_multi_level_procurement(tmp_path: Path) -> None:
    controller, _service, store_id, account_id = _controller(tmp_path)
    preview = controller.dispatch(
        "orders.procurement.product-preview",
        {
            "store_id": store_id,
            "order_id": "ORDER-PROC-1",
            "product_id": "P-1",
            "account_id": account_id,
            "url": "https://detail.1688.com/offer/9001.html",
        },
    )
    assert preview["matched_count"] == 1
    mapping = preview["mappings"][0]
    saved = controller.dispatch(
        "orders.procurement.link-save",
        {
            "store_id": store_id,
            "order_id": "ORDER-PROC-1",
            "links": [
                {
                    "product_id": "P-1",
                    "account_id": account_id,
                    "snapshot": preview["snapshot"],
                }
            ],
            "mappings": [
                {
                    **mapping,
                    "product_id": "P-1",
                    "source_url": preview["snapshot"]["source_url"],
                }
            ],
        },
    )
    assert saved["mapping_count"] == 1
    assert controller.db.list_product_purchase_links(store_id, "P-1")
    order = controller.dispatch(
        "orders.list",
        {"store_id": store_id, "limit": 100},
    )["orders"][0]
    assert order["procurement_state"] == "wait_purchase"
    assert order["needs_procurement"] is True


def test_purchase_open_reads_back_exactly_one_procurement_order(tmp_path: Path) -> None:
    controller, service, store_id, account_id = _controller(tmp_path)
    preview = controller.dispatch(
        "orders.procurement.product-preview",
        {
            "store_id": store_id,
            "order_id": "ORDER-PROC-1",
            "product_id": "P-1",
            "account_id": account_id,
            "url": "https://detail.1688.com/offer/9001.html",
        },
    )
    controller.dispatch(
        "orders.procurement.link-save",
        {
            "store_id": store_id,
            "order_id": "ORDER-PROC-1",
            "links": [{
                "product_id": "P-1",
                "account_id": account_id,
                "snapshot": preview["snapshot"],
            }],
            "mappings": [{
                **preview["mappings"][0],
                "product_id": "P-1",
                "source_url": preview["snapshot"]["source_url"],
            }],
        },
    )
    result = controller.dispatch(
        "orders.procurement.purchase-open",
        {
            "store_id": store_id,
            "order_id": "ORDER-PROC-1",
            "account_id": account_id,
        },
    )
    assert service.opened
    assert result["new_procurement_orders"] == 1
    task = controller.db.list_order_dispatch_tasks(store_id, "ORDER-PROC-1")[0]
    assert task["status"] == "wait_supplier"
    assert task["procurement_order_id"] > 0


def test_monitor_auto_ships_and_skips_when_aftersale_exists(tmp_path: Path) -> None:
    controller, service, store_id, account_id = _controller(tmp_path)
    preview = controller.dispatch(
        "orders.procurement.product-preview",
        {
            "store_id": store_id,
            "order_id": "ORDER-PROC-1",
            "product_id": "P-1",
            "account_id": account_id,
            "url": "https://detail.1688.com/offer/9001.html",
        },
    )
    controller.dispatch(
        "orders.procurement.link-save",
        {
            "store_id": store_id,
            "order_id": "ORDER-PROC-1",
            "links": [{
                "product_id": "P-1",
                "account_id": account_id,
                "snapshot": preview["snapshot"],
            }],
            "mappings": [{
                **preview["mappings"][0],
                "product_id": "P-1",
                "source_url": preview["snapshot"]["source_url"],
            }],
        },
    )
    controller.dispatch(
        "orders.procurement.purchase-open",
        {
            "store_id": store_id,
            "order_id": "ORDER-PROC-1",
            "account_id": account_id,
        },
    )
    result = controller._monitor_procurement_automation(force=True)
    assert result["completed"] == 1
    assert service.delivery_updates
    workbench = controller.db.get_order_workbench_row(
        store_id,
        "ORDER-PROC-1",
    )
    assert workbench["fulfillment_stage"] == "shipped"
    assert workbench["cost"] == 4680
    assert workbench["profit"] == 2000


def test_procurement_events_keep_product_refs_through_shipping(
    tmp_path: Path,
) -> None:
    controller, _service, store_id, account_id = _controller(tmp_path)
    analytics = CapturingAnalytics()
    controller.analytics = analytics
    preview = controller.dispatch(
        "orders.procurement.product-preview",
        {
            "store_id": store_id,
            "order_id": "ORDER-PROC-1",
            "product_id": "P-1",
            "account_id": account_id,
            "url": "https://detail.1688.com/offer/9001.html",
        },
    )
    controller.dispatch(
        "orders.procurement.link-save",
        {
            "store_id": store_id,
            "order_id": "ORDER-PROC-1",
            "links": [{
                "product_id": "P-1",
                "account_id": account_id,
                "snapshot": preview["snapshot"],
            }],
            "mappings": [{
                **preview["mappings"][0],
                "product_id": "P-1",
                "source_url": preview["snapshot"]["source_url"],
            }],
        },
    )
    controller.dispatch(
        "orders.procurement.purchase-open",
        {
            "store_id": store_id,
            "order_id": "ORDER-PROC-1",
            "account_id": account_id,
        },
    )
    controller._monitor_procurement_automation(force=True)

    by_type = {}
    for event in analytics.events:
        by_type.setdefault(event["event_type"], []).append(event)
    for event_type in (
        "procurement.link_associated",
        "procurement.purchase_submitted",
        "procurement.supplier_status_changed",
        "procurement.tracking_received",
        "procurement.store_shipped",
    ):
        assert event_type in by_type
        payload = by_type[event_type][0]["payload"]
        assert payload["product_id"] == "P-1"
        assert payload["sku_ref"] == "SKU-1"
        assert payload["quantity"] == 1
        assert payload["store_order_item_ref"] == "P-1:SKU-1"
    assert by_type["procurement.purchase_submitted"][0]["payload"][
        "procurement_ref"
    ] == "PURCHASE-NEW-1"


def test_aftersale_freezes_automatic_procurement_actions(tmp_path: Path) -> None:
    controller, service, store_id, account_id = _controller(tmp_path)
    analytics = CapturingAnalytics()
    controller.analytics = analytics
    preview = controller.dispatch(
        "orders.procurement.product-preview",
        {
            "store_id": store_id,
            "order_id": "ORDER-PROC-1",
            "product_id": "P-1",
            "account_id": account_id,
            "url": "https://detail.1688.com/offer/9001.html",
        },
    )
    controller.dispatch(
        "orders.procurement.link-save",
        {
            "store_id": store_id,
            "order_id": "ORDER-PROC-1",
            "links": [{
                "product_id": "P-1",
                "account_id": account_id,
                "snapshot": preview["snapshot"],
            }],
            "mappings": [{
                **preview["mappings"][0],
                "product_id": "P-1",
                "source_url": preview["snapshot"]["source_url"],
            }],
        },
    )
    controller.dispatch(
        "orders.procurement.purchase-open",
        {
            "store_id": store_id,
            "order_id": "ORDER-PROC-1",
            "account_id": account_id,
        },
    )
    controller.db.upsert_after_sale(
        store_id,
        {
            "after_sale_order_id": "AS-PROC-1",
            "order_id": "ORDER-PROC-1",
            "status": "MERCHANT_PROCESSING",
            "type": "仅退款",
            "refund_info": {"amount": 1000},
            "create_time": 1_700_000_000,
            "update_time": 1_700_000_010,
        },
    )
    controller._monitor_procurement_automation(force=True)
    workbench = controller.db.get_order_workbench_row(
        store_id,
        "ORDER-PROC-1",
    )
    assert workbench["fulfillment_stage"] == "aftersale"
    assert service.delivery_updates == []
    aftersale_events = [
        event
        for event in analytics.events
        if event["event_type"] == "procurement.after_sale_changed"
    ]
    assert aftersale_events
    payload = aftersale_events[-1]["payload"]
    assert payload["product_id"] == "P-1"
    assert payload["sku_ref"] == "SKU-1"
    assert payload["quantity"] == 1
    assert payload["store_order_item_ref"] == "P-1:SKU-1"
