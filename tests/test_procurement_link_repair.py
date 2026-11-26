from __future__ import annotations

"""Offline regression coverage for the procurement-link repair batch."""

import copy
import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from weimi_manager.db import Database
from weimi_manager.models import Order, OrderItem
from weimi_manager.procurement import (
    ProcurementError,
    normalize_supplier_capture,
)
from weimi_manager.webui.controller import NextUiController


FIXTURES = Path(__file__).parent / "fixtures" / "procurement"


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_1688_capture_uses_only_target_offer_and_never_defaults_specs() -> None:
    payload = normalize_supplier_capture(
        "1688",
        _fixture("1688_target_capture.json"),
    )

    assert payload["source_product_id"] == "1048048297681"
    assert payload["title"] == "官方目标商品"
    assert [sku["sku_id"] for sku in payload["skus"]] == [
        "REAL-BLACK-XL",
        "REAL-WHITE-L",
    ]
    assert all("默认规格" not in sku["label"] for sku in payload["skus"])


def test_taobao_capture_rejects_recommendation_title() -> None:
    payload = normalize_supplier_capture(
        "taobao",
        _fixture("taobao_target_capture.json"),
    )

    assert payload["source_product_id"] == "1012686581380"
    assert payload["title"] == "淘宝官方目标商品"
    assert payload["skus"][0]["sku_id"] == "TAOBAO-REAL-1"


def test_source_capture_without_real_target_sku_or_stock_fails() -> None:
    capture = _fixture("1688_target_capture.json")
    target = capture["responses"][1]["data"]["data"]
    target["skuInfos"] = [
        {
            "skuId": "MISSING-STOCK",
            "price": "46.80",
            "specAttrs": "颜色:黑色",
        }
    ]

    with pytest.raises(ProcurementError, match="真实 SKU"):
        normalize_supplier_capture("1688", capture)

    target["skuInfos"] = []
    target["price"] = "46.80"
    with pytest.raises(ProcurementError, match="真实 SKU"):
        normalize_supplier_capture("1688", capture)

    target["skuInfos"] = [
        {
            "skuId": "MISSING-PRICE",
            "stock": 20,
            "specAttrs": "颜色:黑色",
        }
    ]
    with pytest.raises(ProcurementError, match="真实 SKU"):
        normalize_supplier_capture("1688", capture)


def test_source_capture_rejects_mismatched_target_product() -> None:
    capture = _fixture("1688_target_capture.json")
    capture["offer_id"] = "777777777777"

    with pytest.raises(ProcurementError, match="目标 1688 商品"):
        normalize_supplier_capture("1688", capture)


def test_target_1688_capture_never_falls_back_to_recommendation_dom_title() -> None:
    capture = _fixture("1688_target_capture.json")
    target = capture["responses"][1]["data"]["data"]
    target.pop("subject", None)
    capture["target_source_id"] = "1048048297681"

    with pytest.raises(ProcurementError, match="标题"):
        normalize_supplier_capture("1688", capture)


class PreviewService:
    def __init__(self, database: Database) -> None:
        self.db = database
        self.preview_calls = []

    def inspect_procurement_product(self, account_id, url):
        self.preview_calls.append((account_id, url))
        return {
            "platform": "1688",
            "source_product_id": "1048048297681",
            "source_url": url,
            "title": "官方目标商品",
            "main_image": "",
            "skus": [
                {
                    "sku_id": "REAL-BLACK-XL",
                    "label": "黑色 / XL",
                    "attrs": [
                        {"attr_key": "颜色", "attr_value": "黑色"},
                        {"attr_key": "尺码", "attr_value": "XL"},
                    ],
                    "price": 4680,
                    "stock": 20,
                }
            ],
        }


def _controller(tmp_path: Path):
    database = Database(tmp_path / "procurement-link-repair.db")
    store_id = database.add_store("采购链接测试店", "wx-link-test", "secret")
    account_1688 = database.add_procurement_account(
        "1688",
        "1688账号",
        "buyer-1688",
    )
    account_taobao = database.add_procurement_account(
        "淘宝",
        "淘宝账号",
        "buyer-taobao",
    )
    database.set_procurement_session(
        account_1688,
        {"cookies": [{"name": "cookie2", "value": "x"}]},
        "已登录",
        "",
    )
    database.set_procurement_session(
        account_taobao,
        {"cookies": [{"name": "cookie2", "value": "x"}]},
        "已登录",
        "",
    )
    database.upsert_order(
        Order(
            store_id=store_id,
            store_name="采购链接测试店",
            order_id="ORDER-LINK-1",
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
                    product_id="P-LINK-1",
                    sku_id="LOCAL-SKU-1",
                    title="本地订单商品",
                    sku_count=1,
                    sale_price=6800,
                    sku_attributes="黑色 / XL",
                )
            ],
            raw={},
        )
    )
    service = PreviewService(database)
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
        now_provider=lambda: datetime(2026, 11, 25, 12, 0, 0),
    )
    return controller, service, store_id, account_1688, account_taobao


def test_preview_rejects_account_platform_mismatch_before_platform_read(
    tmp_path: Path,
) -> None:
    controller, service, store_id, _account_1688, account_taobao = _controller(
        tmp_path
    )

    with pytest.raises(ValueError, match="平台不一致"):
        controller.dispatch(
            "orders.procurement.product-preview",
            {
                "store_id": store_id,
                "order_id": "ORDER-LINK-1",
                "product_id": "P-LINK-1",
                "store_sku_id": "LOCAL-SKU-1",
                "account_id": account_taobao,
                "url": "https://detail.1688.com/offer/1048048297681.html",
            },
        )

    assert service.preview_calls == []


def test_preview_repeat_uses_snapshot_cache_until_forced(
    tmp_path: Path,
) -> None:
    controller, service, store_id, account_1688, _account_taobao = _controller(
        tmp_path
    )
    payload = {
        "store_id": store_id,
        "order_id": "ORDER-LINK-1",
        "product_id": "P-LINK-1",
        "store_sku_id": "LOCAL-SKU-1",
        "account_id": account_1688,
        "url": "https://detail.1688.com/offer/1048048297681.html",
    }

    first = controller.dispatch("orders.procurement.product-preview", payload)
    second = controller.dispatch("orders.procurement.product-preview", payload)
    forced = controller.dispatch(
        "orders.procurement.product-preview",
        {**payload, "force_refresh": True},
    )

    assert first["store_skus"][0]["sku_attributes"] == "黑色 / XL"
    assert second["cache_hit"] is True
    assert forced["cache_hit"] is False
    assert len(service.preview_calls) == 2


def test_link_save_rejects_a_source_sku_not_present_in_snapshot(
    tmp_path: Path,
) -> None:
    controller, _service, store_id, account_1688, _account_taobao = _controller(
        tmp_path
    )
    preview = controller.dispatch(
        "orders.procurement.product-preview",
        {
            "store_id": store_id,
            "order_id": "ORDER-LINK-1",
            "product_id": "P-LINK-1",
            "store_sku_id": "LOCAL-SKU-1",
            "account_id": account_1688,
            "url": "https://detail.1688.com/offer/1048048297681.html",
        },
    )
    mapping = copy.deepcopy(preview["mappings"][0])
    mapping["purchase_sku_id"] = "NOT-IN-SNAPSHOT"

    with pytest.raises(ValueError, match="不属于已读取快照"):
        controller.dispatch(
            "orders.procurement.link-save",
            {
                "store_id": store_id,
                "order_id": "ORDER-LINK-1",
                "links": [
                    {
                        "product_id": "P-LINK-1",
                        "account_id": account_1688,
                        "snapshot": preview["snapshot"],
                    }
                ],
                "mappings": [
                    {
                        **mapping,
                        "product_id": "P-LINK-1",
                        "source_url": preview["snapshot"]["source_url"],
                    }
                ],
            },
        )
