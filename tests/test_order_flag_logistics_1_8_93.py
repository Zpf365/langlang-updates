from __future__ import annotations

import json
from pathlib import Path

import pytest

from weimi_manager.db import Database
from weimi_manager.models import Order, OrderItem, Product
from weimi_manager.service import SyncService
from weimi_manager.shop_private import normalize_order_private_detail
from weimi_manager.webui.controller import NextUiController


def _order(
    store_id: int,
    store_name: str,
    order_id: str,
    *,
    flag_color: int = 0,
    merchant_notes: str = "",
    packages: list[dict] | None = None,
) -> Order:
    raw = {
        "order_detail": {
            "ext_info": {
                "merchant_notes": merchant_notes,
                "merchant_notes_tag_color": flag_color,
            },
            "delivery_info": {
                "delivery_product_info": packages or [],
            },
        }
    }
    return Order(
        store_id=store_id,
        store_name=store_name,
        order_id=order_id,
        status=30,
        status_text="待收货",
        create_time=1_700_000_000,
        update_time=1_700_000_010,
        pay_time=1_700_000_005,
        total_amount=3553,
        product_amount=4180,
        freight_amount=0,
        discount_amount=627,
        buyer_name="测试买家",
        phone="13800000000",
        address="测试地址",
        waybill_id=(packages or [{}])[0].get("waybill_id", "")
        if packages
        else "",
        carrier=(packages or [{}])[0].get("delivery_id", "")
        if packages
        else "",
        merchant_notes=merchant_notes,
        items=[
            OrderItem(
                product_id="10001900604305",
                sku_id="35712704218",
                title="测试商品",
                sku_count=1,
                sale_price=3553,
                thumb_url="https://example.invalid/product.jpg",
            )
        ],
        raw=raw,
    )


def test_private_order_detail_normalizes_flag_packages_and_traces() -> None:
    detail = normalize_order_private_detail(
        {
            "code": 0,
            "commonInfo": {
                "orderId": "order-1",
                "status": 30,
                "statusStr": "已发货",
            },
            "orderInfo": {"merchantNotes": ""},
            "flagInfo": {"flagNotes": "", "flagColor": 1},
            "expressInfo": {
                "deliveryProductInfo": [
                    {
                        "deliveryId": "YUNDA",
                        "waybillId": "435458667553701",
                        "productInfos": [
                            {
                                "productId": "10001900604305",
                                "skuId": "35712704218",
                                "productCnt": 1,
                            }
                        ],
                    }
                ]
            },
            "expressData": {
                "deliveryList": [
                    {
                        "title": "运输中",
                        "waybillId": "435458667553701",
                        "deliveryName": "韵达速递",
                        "traces": [
                            {
                                "acceptTime": "2026-11-24 19:48:15",
                                "acceptAddress": "【郑州市】运输中",
                            }
                        ],
                    }
                ]
            },
            "orderProducts": {
                "orderProductInfo": [
                    {
                        "productId": "10001900604305",
                        "skuId": "35712704218",
                        "title": "测试商品",
                        "productCnt": 1,
                    }
                ]
            },
        }
    )

    assert detail["merchant_notes"] == ""
    assert detail["merchant_notes_tag_color"] == 1
    assert detail["packages"][0]["delivery_id"] == "YUNDA"
    assert detail["packages"][0]["traces"][0]["time"] == "2026-11-24 19:48:15"
    assert detail["products"][0]["product_id"] == "10001900604305"


def test_order_list_keeps_official_flag_when_note_is_empty_and_exposes_packages(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "webui.db")
    store_id = database.add_store("掘阅宽栾商行", "wx-order-flag", "secret")
    database.upsert_order(
        _order(
            store_id,
            "掘阅宽栾商行",
            "3740750222608964096",
            flag_color=1,
            packages=[
                {
                    "waybill_id": "435458667553701",
                    "delivery_id": "YUNDA",
                    "delivery_name": "韵达速递",
                    "product_infos": [],
                }
            ],
        )
    )

    listed = NextUiController(database).dispatch(
        "orders.list",
        {"store_id": store_id},
    )["orders"][0]

    assert listed["merchant_notes"] == ""
    assert listed["official_note_color"] == "红"
    assert listed["official_note_color_code"] == 1
    assert listed["official_flag_present"] is True
    assert listed["packages"] == [
        {
            "waybill_id": "435458667553701",
            "delivery_id": "YUNDA",
            "delivery_name": "韵达速递",
            "product_infos": [],
        }
    ]


@pytest.mark.parametrize(
    ("code", "name", "present"),
    [
        (0, "", False),
        (1, "红", True),
        (2, "橙", True),
        (3, "绿", True),
        (4, "蓝", True),
    ],
)
def test_official_flag_color_mapping(
    tmp_path: Path,
    code: int,
    name: str,
    present: bool,
) -> None:
    database = Database(tmp_path / f"flag-{code}.db")
    store_id = database.add_store("旗帜店铺", f"wx-flag-{code}", "secret")
    database.upsert_order(
        _order(
            store_id,
            "旗帜店铺",
            f"order-{code}",
            flag_color=code,
        )
    )

    row = NextUiController(database).dispatch(
        "orders.list",
        {"store_id": store_id},
    )["orders"][0]

    assert row["official_note_color_code"] == code
    assert row["official_note_color"] == name
    assert row["official_flag_present"] is present
    assert row["merchant_notes"] == ""


def test_same_order_id_in_different_stores_does_not_share_local_flags(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "webui.db")
    first = database.add_store("店铺一", "wx-order-a", "secret")
    second = database.add_store("店铺二", "wx-order-b", "secret")
    order_id = "same-order"
    database.upsert_order(
        _order(first, "店铺一", order_id, flag_color=1)
    )
    database.upsert_order(
        _order(second, "店铺二", order_id, flag_color=4)
    )
    controller = NextUiController(database)
    controller.dispatch(
        "orders.update-note-state",
        {
            "store_id": first,
            "order_id": order_id,
            "scope": "软件备注",
            "note": "仅店铺一",
            "flag": "红",
            "sync_store": False,
        },
    )

    first_row = controller.dispatch(
        "orders.list",
        {"store_id": first},
    )["orders"][0]
    second_row = controller.dispatch(
        "orders.list",
        {"store_id": second},
    )["orders"][0]

    assert first_row["software_note"] == "仅店铺一"
    assert first_row["official_note_color"] == "红"
    assert second_row["software_note"] == ""
    assert second_row["official_note_color"] == "蓝"


class _PrivateClient:
    def __init__(self, official: dict):
        self.official = official
        self.writes: list[tuple[str, str, int]] = []

    def update_order_notes_private(
        self,
        order_id: str,
        notes: str,
        color: int,
    ) -> None:
        self.writes.append((order_id, notes, color))

    def order_detail(self, _order_id: str) -> dict:
        return dict(self.official)


def test_note_write_requires_official_readback_match(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "webui.db")
    store = database.add_store("店铺", "wx-note-readback", "secret")
    assert database.get_store(store) is not None
    service = SyncService(database)
    private = _PrivateClient(
        {
            "merchant_notes": "平台不同内容",
            "merchant_notes_tag_color": 1,
            "packages": [],
        }
    )
    service.platform_write_authorizer = lambda _app_id, _action: None
    import weimi_manager.service as service_module

    original = service_module.ShopPrivateClient
    service_module.ShopPrivateClient = lambda _db, _store: private
    try:
        with pytest.raises(RuntimeError, match="回读不一致"):
            service.update_order_notes(
                database.get_store(store),
                "order-1",
                "本地写入",
                1,
            )
    finally:
        service_module.ShopPrivateClient = original


def test_note_write_updates_cache_only_after_official_match(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "webui.db")
    store_id = database.add_store("店铺", "wx-note-success", "secret")
    database.upsert_order(_order(store_id, "店铺", "order-1"))
    service = SyncService(database)
    private = _PrivateClient(
        {
            "merchant_notes": "AUTOTEST-NOTE",
            "merchant_notes_tag_color": 2,
            "packages": [
                {
                    "waybill_id": "Y-1",
                    "delivery_id": "YUNDA",
                    "delivery_name": "韵达速递",
                    "product_infos": [],
                }
            ],
        }
    )
    service.platform_write_authorizer = lambda _app_id, _action: None
    import weimi_manager.service as service_module

    original = service_module.ShopPrivateClient
    service_module.ShopPrivateClient = lambda _db, _store: private
    try:
        result = service.update_order_notes(
            database.get_store(store_id),
            "order-1",
            "AUTOTEST-NOTE",
            2,
        )
    finally:
        service_module.ShopPrivateClient = original

    assert result["merchant_notes"] == "AUTOTEST-NOTE"
    order = database.get_order(store_id, "order-1")
    assert order is not None
    assert order.merchant_notes == "AUTOTEST-NOTE"
    assert order.waybill_id == "Y-1"
    raw_detail = order.raw["order_detail"]
    assert raw_detail["ext_info"]["merchant_notes_tag_color"] == 2


class _OrderService:
    def __init__(self, detail: dict):
        self.detail = detail
        self.refreshed: list[tuple[int, str]] = []

    def refresh_order_official(self, store, order_id: str) -> dict:
        self.refreshed.append((store.id, order_id))
        return self.detail

    def official_order_detail(self, _store, _order_id: str) -> dict:
        return self.detail


def test_logistics_track_selects_exact_package_and_reports_missing(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "webui.db")
    store_id = database.add_store("物流店铺", "wx-logistics", "secret")
    database.upsert_order(_order(store_id, "物流店铺", "order-logistics"))
    detail = {
        "store_id": store_id,
        "order_id": "order-logistics",
        "merchant_notes": "",
        "merchant_notes_tag_color": 1,
        "checked_at": "2026-11-24T20:00:00",
        "source": "微信小店商家后台订单详情",
        "packages": [
            {
                "waybill_id": "A-1",
                "delivery_id": "YUNDA",
                "delivery_name": "韵达速递",
                "status": "运输中",
                "traces": [{"time": "t1", "description": "节点一"}],
            },
            {
                "waybill_id": "B-2",
                "delivery_id": "YTO",
                "delivery_name": "圆通速递",
                "status": "已揽收",
                "traces": [{"time": "t2", "description": "节点二"}],
            },
        ],
    }
    controller = NextUiController(
        database,
        product_service=_OrderService(detail),
    )

    result = controller.dispatch(
        "orders.logistics.track",
        {
            "store_id": store_id,
            "order_id": "order-logistics",
            "waybill_id": "B-2",
        },
    )

    assert result["waybill_id"] == "B-2"
    assert result["package"]["delivery_id"] == "YTO"
    assert result["package"]["traces"][0]["description"] == "节点二"
    with pytest.raises(ValueError, match="A-1、B-2"):
        controller.dispatch(
            "orders.logistics.track",
            {
                "store_id": store_id,
                "order_id": "order-logistics",
                "waybill_id": "C-3",
            },
        )


def test_product_locate_is_store_scoped(tmp_path: Path) -> None:
    database = Database(tmp_path / "webui.db")
    first = database.add_store("商品店一", "wx-product-locate-a", "secret")
    second = database.add_store("商品店二", "wx-product-locate-b", "secret")
    database.upsert_product(
        Product(
            store_id=first,
            store_name="商品店一",
            product_id="10001900604305",
            title="测试商品",
            status=5,
            status_text="销售中",
            edit_status=4,
            min_price=3553,
            sku_count=1,
            stock_total=10,
            spu_code="",
            out_product_id="",
            head_img="",
            raw={},
        )
    )
    controller = NextUiController(database)

    found = controller.dispatch(
        "products.locate",
        {"store_id": first, "product_id": "10001900604305"},
    )

    assert found["store_id"] == first
    assert found["product"]["store_id"] == first
    assert found["product"]["title"] == "测试商品"
    with pytest.raises(RuntimeError, match="未初始化"):
        controller.dispatch(
            "products.locate",
            {"store_id": second, "product_id": "10001900604305"},
        )
