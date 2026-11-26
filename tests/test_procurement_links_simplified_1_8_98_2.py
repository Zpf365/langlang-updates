from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from weimi_manager.db import Database
from weimi_manager.models import Order, OrderItem
from weimi_manager.webui.controller import NextUiController


class SimplifiedProcurementService:
    def __init__(self) -> None:
        self.opened: list[tuple[int, str, list[dict[str, str]]]] = []

    def open_procurement_product(self, account_id, url, attrs):
        self.opened.append((account_id, url, attrs))
        return {
            "product_url": url,
            "selected_attrs": attrs,
            "closed_at": "2026-11-25T20:00:00",
            "result": "verification_required",
        }


def _controller(tmp_path: Path):
    database = Database(tmp_path / "simplified-procurement.db")
    first_store = database.add_store("再百宽栾商行", "wx-store-1", "secret-1")
    second_store = database.add_store("第二测试店", "wx-store-2", "secret-2")
    taobao_account = database.add_procurement_account(
        "淘宝",
        "淘宝采购",
        "fixture-buyer",
    )
    account_1688 = database.add_procurement_account(
        "1688",
        "1688采购",
        "fixture-buyer",
    )
    for account_id in (taobao_account, account_1688):
        database.set_procurement_session(
            account_id,
            {"cookies": [{"name": "cookie2", "value": "x"}]},
            "已登录",
            "",
        )

    for store_id, order_id in (
        (first_store, "ORDER-LINK-1"),
        (second_store, "ORDER-LINK-2"),
    ):
        database.upsert_order(
            Order(
                store_id=store_id,
                store_name="采购链接测试店",
                order_id=order_id,
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
                        product_id="PRODUCT-001",
                        sku_id=f"LOCAL-SKU-{store_id}",
                        title="本地订单商品",
                        sku_count=1,
                        sale_price=6800,
                        sku_attributes="黑色 / XL",
                    ),
                    *(
                        [
                            OrderItem(
                                product_id="PRODUCT-002",
                                sku_id=f"LOCAL-SKU-2-{store_id}",
                                title="本地订单商品二",
                                sku_count=1,
                                sale_price=3900,
                                sku_attributes="白色 / L",
                            )
                        ]
                        if store_id == first_store
                        else []
                    ),
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
                        }
                    }
                },
            )
        )
    service = SimplifiedProcurementService()
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
        now_provider=lambda: datetime(2026, 11, 25, 20, 0, 0),
    )
    return (
        controller,
        service,
        first_store,
        second_store,
        taobao_account,
        account_1688,
    )


def _save_link(
    controller: NextUiController,
    store_id: int,
    *,
    order_id: str = "ORDER-LINK-1",
    product_id: str = "PRODUCT-001",
    url: str,
    account_id: int | None = None,
):
    return controller.dispatch(
        "orders.procurement.link-upsert",
        {
            "store_id": store_id,
            "order_id": order_id,
            "product_id": product_id,
            "account_id": account_id,
            "url": url,
        },
    )


def test_saves_taobao_and_1688_links_without_sku_mapping_and_reloads(
    tmp_path: Path,
) -> None:
    controller, _service, store_id, _second, taobao, account_1688 = _controller(
        tmp_path
    )

    _save_link(
        controller,
        store_id,
        url="https://item.taobao.com/item.htm?id=1012686581380",
    )
    _save_link(
        controller,
        store_id,
        url="https://detail.1688.com/offer/1048048297681.html",
    )

    detail = controller.dispatch(
        "orders.workbench.detail",
        {"store_id": store_id, "order_id": "ORDER-LINK-1"},
    )
    links = detail["product_purchase_links"]
    assert len(links) == 2
    assert {link["platform"] for link in links} == {"淘宝", "1688"}
    assert {link["account_id"] for link in links} == {
        int(taobao),
        int(account_1688),
    }
    assert detail["product_sku_mappings"] == []


def test_normalized_duplicate_url_updates_same_product_link(
    tmp_path: Path,
) -> None:
    controller, _service, store_id, _second, _taobao, _account_1688 = _controller(
        tmp_path
    )

    first = _save_link(
        controller,
        store_id,
        url="https://detail.1688.com/offer/1048048297681.html?spm=first",
    )
    second = _save_link(
        controller,
        store_id,
        url="https://detail.1688.com/offer/1048048297681.html",
    )

    assert first["duplicate"] is False
    assert second["duplicate"] is True
    assert first["link"]["id"] == second["link"]["id"]
    assert len(
        controller.db.list_product_purchase_links(
            store_id,
            "PRODUCT-001",
        )
    ) == 1


def test_store_and_product_scope_is_isolated(tmp_path: Path) -> None:
    controller, _service, first_store, second_store, _taobao, _account_1688 = (
        _controller(tmp_path)
    )
    url = "https://detail.1688.com/offer/1048048297681.html"

    _save_link(controller, first_store, url=url)
    _save_link(controller, first_store, product_id="PRODUCT-002", url=url)
    _save_link(
        controller,
        second_store,
        order_id="ORDER-LINK-2",
        url=url,
    )

    assert len(
        controller.db.list_product_purchase_links(first_store, "PRODUCT-001")
    ) == 1
    assert len(
        controller.db.list_product_purchase_links(first_store, "PRODUCT-002")
    ) == 1
    assert len(
        controller.db.list_product_purchase_links(second_store, "PRODUCT-001")
    ) == 1
    assert not controller.db.list_product_purchase_links(
        first_store,
        "ORDER-LINK-1",
    )


def test_link_without_matching_account_can_save_but_cannot_open(
    tmp_path: Path,
) -> None:
    controller, service, _store_id, _second, taobao, _account_1688 = _controller(
        tmp_path
    )
    controller.db.update_procurement_account(taobao, status="登录已失效")
    result = _save_link(
        controller,
        _store_id,
        url="https://item.taobao.com/item.htm?id=1012686581380",
    )

    assert result["link"]["source_url"].endswith(
        "item.htm?id=1012686581380"
    )
    with pytest.raises(ValueError, match="没有可用的淘宝采购账号"):
        controller.dispatch(
            "orders.procurement.link-open",
            {
                "store_id": _store_id,
                "order_id": "ORDER-LINK-1",
                "product_id": "PRODUCT-001",
                "link_id": result["link"]["id"],
            },
        )
    assert service.opened == []


def test_open_link_uses_only_the_matching_platform_account(
    tmp_path: Path,
) -> None:
    controller, service, store_id, _second, taobao, account_1688 = _controller(
        tmp_path
    )
    taobao_link = _save_link(
        controller,
        store_id,
        url="https://item.taobao.com/item.htm?id=1012686581380",
    )["link"]
    link_1688 = _save_link(
        controller,
        store_id,
        url="https://detail.1688.com/offer/1048048297681.html",
    )["link"]

    controller.dispatch(
        "orders.procurement.link-open",
        {
            "store_id": store_id,
            "order_id": "ORDER-LINK-1",
            "product_id": "PRODUCT-001",
            "link_id": taobao_link["id"],
        },
    )
    controller.dispatch(
        "orders.procurement.link-open",
        {
            "store_id": store_id,
            "order_id": "ORDER-LINK-1",
            "product_id": "PRODUCT-001",
            "link_id": link_1688["id"],
        },
    )

    assert [call[0] for call in service.opened] == [taobao, account_1688]
    assert service.opened[0][1].startswith("https://item.taobao.com/")
    assert service.opened[1][1].startswith("https://detail.1688.com/")
