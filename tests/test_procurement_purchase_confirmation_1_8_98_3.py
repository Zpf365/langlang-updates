from __future__ import annotations

import copy
import json
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from weimi_manager.db import Database
from weimi_manager.models import Order, OrderItem
from weimi_manager.procurement import match_procurement_purchase
from weimi_manager.webui.controller import NextUiController


SOURCE_ID = "1048048297681"
SOURCE_URL = f"https://detail.1688.com/offer/{SOURCE_ID}.html"


def _record(
    external_order_id: str = "1688-ORDER-NEW-1",
    *,
    pay_amount: int = 4680,
    quantity: int = 1,
    sku_id: str = "SUPPLIER-BLACK-XL",
    spec: str = "黑色 / XL",
    create_time: int | None = None,
) -> dict:
    return {
        "external_order_id": external_order_id,
        "trade_no": external_order_id,
        "mail_no": "",
        "status": "WAIT_SELLER_SEND_GOODS",
        "status_text": "等待卖家发货",
        "goods_title": "官方目标商品",
        "goods_count": quantity,
        "pay_amount": pay_amount,
        "seller": "目标上家店铺",
        "purchase_time": str(create_time or int(time.time())),
        "sku_text": spec,
        "source_product_ids": [SOURCE_ID],
        "raw": {
            "tradeNo": external_order_id,
            "offerId": SOURCE_ID,
            "skuId": sku_id,
            "specAttrs": f"颜色:{spec.split('/')[0].strip()}",
            "price": str(pay_amount / max(1, quantity) / 100),
            "postFee": "0.00",
            "createTime": str(create_time or int(time.time())),
        },
    }


def test_matcher_requires_one_new_order_matching_product_sku_quantity_amount() -> None:
    expected = {
        "source_product_id": SOURCE_ID,
        "quantity": 1,
        "amount_cents": 4680,
        "sku_tokens": ["黑色", "XL"],
        "started_after": int(time.time()) - 10,
        "before_order_ids": {"1688-OLD-1"},
    }
    matched = match_procurement_purchase(
        [_record("1688-OLD-1"), _record()],
        **expected,
    )
    assert matched["status"] == "confirmed"
    assert matched["order"]["external_order_id"] == "1688-ORDER-NEW-1"

    ambiguous = match_procurement_purchase(
        [_record("1688-A"), _record("1688-B")],
        **expected,
    )
    assert ambiguous["status"] == "ambiguous"

    conflict = match_procurement_purchase(
        [_record(pay_amount=5000)],
        **expected,
    )
    assert conflict["status"] == "conflict"
    assert "金额" in conflict["reason"]

    missing = match_procurement_purchase(
        [_record("1688-OLD-1")],
        **expected,
    )
    assert missing["status"] == "not_found"


class PurchaseConfirmationService:
    def __init__(self, responses: list[dict] | None = None) -> None:
        self.responses = list(responses or [])
        self.calls = []
        self.note_sync_calls = 0

    def confirm_procurement_purchase(
        self,
        account_id,
        url,
        expected,
    ):
        self.calls.append(
            {
                "account_id": account_id,
                "url": url,
                "expected": copy.deepcopy(expected),
            }
        )
        if not self.responses:
            return {
                "status": "not_found",
                "page_closed": False,
                "read_attempts": 5,
                "reason": "未发现新采购订单",
            }
        return self.responses.pop(0)

    def sync_procurement_note_to_wechat(self, *args, **kwargs):
        self.note_sync_calls += 1
        raise AssertionError("本轮不得修改微信官方备注")


def _controller(tmp_path: Path, service: PurchaseConfirmationService):
    database = Database(tmp_path / "purchase-confirmation.db")
    store_a = database.add_store("店铺A", "wx-store-a", "secret-a")
    store_b = database.add_store("店铺B", "wx-store-b", "secret-b")
    account = database.add_procurement_account(
        "1688",
        "1688采购",
        "fixture-buyer",
    )
    database.set_procurement_session(
        account,
        {"cookies": [{"name": "cookie2", "value": "x"}]},
        "已登录",
        "",
    )
    for store_id, order_id in (
        (store_a, "CUSTOMER-ORDER-A"),
        (store_b, "CUSTOMER-ORDER-B"),
    ):
        database.upsert_order(
            Order(
                store_id=store_id,
                store_name=f"店铺{store_id}",
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
                merchant_notes="原始平台备注",
                items=[
                    OrderItem(
                        product_id=f"PRODUCT-{store_id}",
                        sku_id=f"LOCAL-SKU-{store_id}",
                        title="本地客户商品",
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
                        }
                    }
                },
            )
        )
        controller_link = database.upsert_product_purchase_link(
            store_id,
            f"PRODUCT-{store_id}",
            {
                "source_url": SOURCE_URL,
                "platform": "1688",
                "account_id": account,
                "source_product_id": SOURCE_ID,
            },
        )
        database.update_order_workbench(
            store_id,
            order_id,
            fulfillment_stage="pending_purchase",
            note="用户软件备注保留",
        )
        database.set_setting(
            f"purchase_link_{store_id}",
            str(controller_link),
        )
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
        now_provider=lambda: datetime(2026, 11, 25, 21, 0, 0),
    )
    links = {}
    for store_id, order_id in (
        (store_a, "CUSTOMER-ORDER-A"),
        (store_b, "CUSTOMER-ORDER-B"),
    ):
        links[store_id] = database.list_product_purchase_links(
            store_id,
            f"PRODUCT-{store_id}",
        )[0]
    return controller, store_a, store_b, account, links


def _confirm(controller, store_id, product_id, link_id, account_id):
    return controller.dispatch(
        "orders.procurement.purchase-confirm",
        {
            "store_id": store_id,
            "order_id": f"CUSTOMER-ORDER-{'A' if store_id == 1 else 'B'}",
            "product_id": product_id,
            "link_id": link_id,
            "account_id": account_id,
            "expected_amount_cents": 4680,
            "expected_sku": "黑色 / XL",
        },
    )


def test_unique_readback_writes_structured_purchase_and_software_note(
    tmp_path: Path,
) -> None:
    service = PurchaseConfirmationService(
        [{
            "status": "confirmed",
            "page_closed": True,
            "read_attempts": 2,
            "order": _record(),
        }]
    )
    controller, store_a, _store_b, account, links = _controller(
        tmp_path,
        service,
    )

    result = _confirm(
        controller,
        store_a,
        "PRODUCT-1",
        links[store_a]["id"],
        account,
    )
    assert result["status"] == "confirmed"
    assert result["page_closed"] is True
    assert result["read_attempts"] == 2
    workbench = controller.db.get_order_workbench_row(
        store_a,
        "CUSTOMER-ORDER-A",
    )
    assert workbench["fulfillment_stage"] == "wait_supplier"
    assert workbench["purchase_order_no"] == "1688-ORDER-NEW-1"
    assert workbench["note"] == "用户软件备注保留"
    order = controller.db.get_order(store_a, "CUSTOMER-ORDER-A")
    assert order.status == 20
    links_saved = controller.db.list_procurement_links(
        store_a,
        "CUSTOMER-ORDER-A",
    )
    assert len(links_saved) == 1
    assert links_saved[0]["sync_wechat_note"] == 0
    note_data = json.loads(links_saved[0]["local_note"])
    assert note_data["product_id"] == "PRODUCT-1"
    assert note_data["upstream_order_id"] == "1688-ORDER-NEW-1"
    assert note_data["platform"] == "1688"
    assert note_data["seller"] == "目标上家店铺"
    assert note_data["cost"] == 4680
    assert service.note_sync_calls == 0


def test_purchase_records_are_scoped_to_the_confirmed_link(
    tmp_path: Path,
) -> None:
    response = _record("1688-LINK-ONE")
    response.update(
        {
            "trade_no": "1688-LINK-ONE",
            "mail_no": "SF1234567890",
            "carrier": "SF",
        }
    )
    service = PurchaseConfirmationService(
        [{
            "status": "confirmed",
            "page_closed": True,
            "read_attempts": 1,
            "order": response,
        }]
    )
    controller, store_a, _store_b, account, links = _controller(
        tmp_path,
        service,
    )
    first_link_id = int(links[store_a]["id"])
    confirmed = _confirm(
        controller,
        store_a,
        "PRODUCT-1",
        first_link_id,
        account,
    )
    assert confirmed["status"] == "confirmed"

    second_link_id = controller.db.upsert_product_purchase_link(
        store_a,
        "PRODUCT-1",
        {
            "source_url": "https://detail.1688.com/offer/1048048297682.html",
            "platform": "1688",
            "account_id": account,
            "source_product_id": "1048048297682",
        },
    )
    controller.db.upsert_procurement_purchase_record(
        store_a,
        "CUSTOMER-ORDER-A",
        {
            "product_id": "PRODUCT-1",
            "link_id": second_link_id,
            "store_sku_id": "LOCAL-SKU-1",
            "account_id": account,
            "platform": "1688",
            "source_url": "https://detail.1688.com/offer/1048048297682.html",
            "source_product_id": "1048048297682",
            "upstream_order_id": "1688-LINK-TWO",
            "trade_no": "1688-LINK-TWO",
            "mail_no": "ZTO9876543210",
            "carrier": "ZTO",
            "upstream_shop": "第二上家",
            "goods_title": "第二采购商品",
            "upstream_sku": "蓝色",
            "quantity": 1,
            "unit_amount": 4200,
            "paid_amount": 4200,
            "freight_amount": 0,
            "status": "WAIT_SELLER_SEND_GOODS",
            "status_text": "等待卖家发货",
            "purchased_at": "2026-11-26T12:00:00",
        },
    )

    detail = controller.dispatch(
        "orders.workbench.detail",
        {"store_id": store_a, "order_id": "CUSTOMER-ORDER-A"},
    )
    product_links = {
        int(row["id"]): row
        for row in detail["product_purchase_links"]
        if row["product_id"] == "PRODUCT-1"
    }
    first_record = product_links[first_link_id]["purchase_record"]
    second_record = product_links[second_link_id]["purchase_record"]
    assert first_record["link_id"] == first_link_id
    assert first_record["upstream_order_id"] == "1688-LINK-ONE"
    assert first_record["mail_no"] == "SF1234567890"
    assert first_record["carrier"] == "SF"
    assert second_record["link_id"] == second_link_id
    assert second_record["upstream_order_id"] == "1688-LINK-TWO"
    assert second_record["mail_no"] == "ZTO9876543210"
    assert second_record["carrier"] == "ZTO"

    procurement_link = next(
        row
        for row in detail["procurement_links"]
        if row["external_order_id"] == "1688-LINK-ONE"
    )
    assert procurement_link["mail_no"] == "SF1234567890"
    assert procurement_link["carrier"] == "SF"


def test_ambiguous_or_conflict_does_not_close_or_persist_success(
    tmp_path: Path,
) -> None:
    service = PurchaseConfirmationService(
        [
            {
                "status": "ambiguous",
                "page_closed": False,
                "read_attempts": 3,
                "reason": "发现两个候选采购订单",
            },
            {
                "status": "conflict",
                "page_closed": False,
                "read_attempts": 4,
                "reason": "金额或 SKU 冲突",
            },
        ]
    )
    controller, store_a, _store_b, account, links = _controller(
        tmp_path,
        service,
    )

    ambiguous = _confirm(
        controller,
        store_a,
        "PRODUCT-1",
        links[store_a]["id"],
        account,
    )
    conflict = _confirm(
        controller,
        store_a,
        "PRODUCT-1",
        links[store_a]["id"],
        account,
    )

    assert ambiguous["status"] == "ambiguous"
    assert ambiguous["page_closed"] is False
    assert conflict["status"] == "conflict"
    assert conflict["page_closed"] is False
    assert controller.db.list_procurement_purchase_records(
        store_a,
        "CUSTOMER-ORDER-A",
        "PRODUCT-1",
    ) == []
    workbench = controller.db.get_order_workbench_row(
        store_a,
        "CUSTOMER-ORDER-A",
    )
    assert workbench["fulfillment_stage"] == "pending_purchase"
    assert "待核实" in workbench["last_error"]


def test_no_new_order_keeps_failure_pending(tmp_path: Path) -> None:
    service = PurchaseConfirmationService()
    controller, store_a, _store_b, account, links = _controller(
        tmp_path,
        service,
    )
    result = _confirm(
        controller,
        store_a,
        "PRODUCT-1",
        links[store_a]["id"],
        account,
    )
    assert result["status"] == "not_found"
    assert result["page_closed"] is False
    assert result["read_attempts"] == 5
    assert controller.db.list_procurement_purchase_records(
        store_a,
        "CUSTOMER-ORDER-A",
        "PRODUCT-1",
    ) == []


def test_repeat_click_is_idempotent_after_confirmed(tmp_path: Path) -> None:
    service = PurchaseConfirmationService(
        [{
            "status": "confirmed",
            "page_closed": True,
            "read_attempts": 1,
            "order": _record(),
        }]
    )
    controller, store_a, _store_b, account, links = _controller(
        tmp_path,
        service,
    )
    first = _confirm(
        controller,
        store_a,
        "PRODUCT-1",
        links[store_a]["id"],
        account,
    )
    second = _confirm(
        controller,
        store_a,
        "PRODUCT-1",
        links[store_a]["id"],
        account,
    )
    assert first["status"] == "confirmed"
    assert second["status"] == "confirmed"
    assert second["reused"] is True
    assert len(service.calls) == 1


def test_store_isolation_keeps_other_store_unpurchased(tmp_path: Path) -> None:
    service = PurchaseConfirmationService(
        [{
            "status": "confirmed",
            "page_closed": True,
            "read_attempts": 1,
            "order": _record(),
        }]
    )
    controller, store_a, store_b, account, links = _controller(
        tmp_path,
        service,
    )
    _confirm(
        controller,
        store_a,
        "PRODUCT-1",
        links[store_a]["id"],
        account,
    )
    assert controller.db.list_procurement_purchase_records(
        store_a,
        "CUSTOMER-ORDER-A",
        "PRODUCT-1",
    )
    assert controller.db.list_procurement_purchase_records(
        store_b,
        "CUSTOMER-ORDER-B",
        "PRODUCT-2",
    ) == []
