from __future__ import annotations

import copy
import json
import threading
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

import weimi_manager.webui.controller as controller_module
from weimi_manager.db import Database
from weimi_manager.models import Order, OrderItem
from weimi_manager.procurement import (
    extract_purchase_completion_evidence,
    match_procurement_purchase,
    normalize_procurement_records,
)
from weimi_manager.service import SyncService
from weimi_manager.webui.controller import NextUiController


SOURCE_ID = "1048048297681"
SOURCE_URL = f"https://detail.1688.com/offer/{SOURCE_ID}.html"
UPSTREAM_ORDER_ID = "3317035837075001057"
SUCCESS_URL = (
    "https://order.1688.com/order/make_order_success.htm?"
    "returnUrl=https%3A%2F%2Ftrade.1688.com%2Forder%2Ftrade_flow.htm"
    "%3Fspm%3Da%26orderId%3D3317035837075001057%26userType%3D3"
)


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


def test_success_url_extracts_order_id_without_waiting_for_page_body() -> None:
    evidence = extract_purchase_completion_evidence(SUCCESS_URL)

    assert evidence == {
        "detected": True,
        "reason": "make_order_success",
        "upstream_order_id": UPSTREAM_ORDER_ID,
    }


def test_exact_upstream_order_id_wins_over_new_order_matching() -> None:
    target = _record(UPSTREAM_ORDER_ID)
    matched = match_procurement_purchase(
        [_record("OTHER-NEW-ORDER"), target],
        source_product_id=SOURCE_ID,
        quantity=1,
        amount_cents=4680,
        sku_tokens=["黑色", "XL"],
        started_after=0,
        before_order_ids=set(),
        upstream_order_id=UPSTREAM_ORDER_ID,
    )
    summary_only = match_procurement_purchase(
        [{"external_order_id": UPSTREAM_ORDER_ID}],
        source_product_id=SOURCE_ID,
        quantity=1,
        amount_cents=4680,
        sku_tokens=[],
        started_after=0,
        before_order_ids=set(),
        upstream_order_id=UPSTREAM_ORDER_ID,
    )
    missing = match_procurement_purchase(
        [_record("OTHER-NEW-ORDER")],
        source_product_id=SOURCE_ID,
        quantity=1,
        amount_cents=4680,
        sku_tokens=[],
        started_after=0,
        before_order_ids=set(),
        upstream_order_id=UPSTREAM_ORDER_ID,
    )

    assert matched["status"] == "confirmed"
    assert matched["order"]["external_order_id"] == UPSTREAM_ORDER_ID
    assert missing["status"] == "not_found"
    assert UPSTREAM_ORDER_ID in missing["reason"]
    assert summary_only["status"] == "not_found"
    assert "商品明细尚未可读" in summary_only["reason"]


def test_nested_order_list_json_merges_summary_and_detail_fields() -> None:
    order_payload = {
        "data": {
            "data": {
                "result": json.dumps({
                    "data": {
                        "data": [
                            {
                                "orderId": UPSTREAM_ORDER_ID,
                                "status": "WAIT_SELLER_SEND_GOODS",
                                "statusText": "等待卖家发货",
                                "gmtCreate": "2026-11-26 17:20:00",
                                "totalAmount": "0",
                                "carriage": "0",
                                "sellerName": "真实上家店铺",
                                "displayItems": [{
                                    "offerId": SOURCE_ID,
                                    "subject": "真实采购商品",
                                    "itemNum": 1,
                                    "skuName": "混色一套",
                                }],
                            },
                            {"orderId": UPSTREAM_ORDER_ID},
                        ]
                    }
                }, ensure_ascii=False),
            }
        }
    }

    records = normalize_procurement_records("1688", [order_payload])
    assert len(records) == 1
    record = next(
        row for row in records
        if row["external_order_id"] == UPSTREAM_ORDER_ID
    )

    assert record["goods_title"] == "真实采购商品"
    assert record["goods_count"] == 1
    assert record["sku_text"] == "混色一套"
    assert record["seller"] == "真实上家店铺"
    assert record["status_text"] == "等待卖家发货"
    assert record["purchase_time"] == "2026-11-26 17:20:00"
    assert record["freight_amount"] == 0
    assert SOURCE_ID in record["source_product_ids"]


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


class AutoPurchaseService:
    def __init__(
        self,
        responses: list[dict],
        *,
        wait_gate=None,
        signal=None,
    ) -> None:
        self.responses = list(responses)
        self.wait_gate = wait_gate
        self.signal = signal or {
            "status": "returned_to_software",
            "reason": "采购窗口已关闭，开始自动核对",
        }
        self.open_calls = []
        self.verify_calls = []
        self.close_calls = []
        self.events = []

    def open_procurement_window(
        self,
        account_id,
        url,
        address,
        details,
    ):
        self.open_calls.append(
            {
                "account_id": account_id,
                "url": url,
                "address": copy.deepcopy(address),
                "details": copy.deepcopy(details),
            }
        )
        return {"status": "opened", "reused": False}

    def wait_for_procurement_purchase_signal(
        self,
        account_id,
        *,
        timeout_seconds,
    ):
        self.events.append("wait")
        if self.wait_gate is not None:
            self.wait_gate.wait(timeout=3)
        return copy.deepcopy(self.signal)

    def verify_procurement_purchase(self, account_id, expected):
        self.events.append("verify")
        self.verify_calls.append(
            {
                "account_id": account_id,
                "expected": copy.deepcopy(expected),
            }
        )
        if not self.responses:
            return {
                "status": "not_found",
                "reason": "未发现目标商品的新采购订单",
            }
        return copy.deepcopy(self.responses.pop(0))

    def close_procurement_window(self, account_id):
        self.events.append("close")
        self.close_calls.append(account_id)
        return True


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


def _open_purchase(controller, store_id, product_id, link_id, account_id):
    return controller.dispatch(
        "orders.procurement.purchase-open",
        {
            "store_id": store_id,
            "order_id": "CUSTOMER-ORDER-A",
            "product_id": product_id,
            "link_id": link_id,
            "account_id": account_id,
        },
    )


def _wait_auto_verify(controller, job_id, terminal):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        status = controller.dispatch(
            "orders.procurement.purchase-auto-verify.status",
            {"job_id": job_id},
        )
        if status["status"] in terminal:
            return status
        time.sleep(0.02)
    raise AssertionError("自动核对任务未在测试时限内结束")


def _auto_verify_constants(monkeypatch, attempts=2):
    monkeypatch.setattr(
        controller_module,
        "PROCUREMENT_AUTO_VERIFY_MAX_ATTEMPTS",
        attempts,
    )
    monkeypatch.setattr(
        controller_module,
        "PROCUREMENT_AUTO_VERIFY_RETRY_SECONDS",
        0,
    )
    monkeypatch.setattr(
        controller_module,
        "PROCUREMENT_AUTO_VERIFY_POST_TRIGGER_SECONDS",
        0,
    )
    monkeypatch.setattr(
        controller_module,
        "PROCUREMENT_AUTO_VERIFY_WAIT_SECONDS",
        3,
    )


def test_open_starts_automatic_readback_and_confirms_without_ui_confirmation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _auto_verify_constants(monkeypatch, attempts=3)
    service = AutoPurchaseService(
        [
            {
                "status": "not_found",
                "reason": "官方订单尚未出现在列表",
            },
            {
                "status": "confirmed",
                "page_closed": True,
                "read_attempts": 1,
                "order": _record(),
            },
        ]
    )
    controller, store_a, _store_b, account, links = _controller(
        tmp_path,
        service,
    )

    opened = _open_purchase(
        controller,
        store_a,
        "PRODUCT-1",
        links[store_a]["id"],
        account,
    )
    assert opened["verification_job_id"]
    status = _wait_auto_verify(
        controller,
        opened["verification_job_id"],
        {"confirmed"},
    )

    assert status["attempt"] == 2
    assert len(service.verify_calls) == 2
    assert service.close_calls == [account]
    assert controller.db.list_procurement_purchase_records(
        store_a,
        "CUSTOMER-ORDER-A",
        "PRODUCT-1",
        link_id=links[store_a]["id"],
    )
    assert "已自动回读" in status["message"]


def test_auto_verify_retries_are_bounded_and_failure_stays_pending(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _auto_verify_constants(monkeypatch, attempts=2)
    service = AutoPurchaseService(
        [
            {
                "status": "not_found",
                "reason": "未发现目标商品的新采购订单",
            },
            {
                "status": "not_found",
                "reason": "未发现目标商品的新采购订单",
            },
        ]
    )
    controller, store_a, _store_b, account, links = _controller(
        tmp_path,
        service,
    )

    opened = _open_purchase(
        controller,
        store_a,
        "PRODUCT-1",
        links[store_a]["id"],
        account,
    )
    status = _wait_auto_verify(
        controller,
        opened["verification_job_id"],
        {"pending", "error"},
    )

    assert status["status"] == "pending"
    assert len(service.verify_calls) == 2
    assert controller.db.list_procurement_purchase_records(
        store_a,
        "CUSTOMER-ORDER-A",
        "PRODUCT-1",
    ) == []
    attempt = controller.db.get_procurement_purchase_attempt(
        store_a,
        "CUSTOMER-ORDER-A",
        "PRODUCT-1",
        links[store_a]["id"],
    )
    assert attempt["status"] == "verification_pending"


def test_auto_verify_conflict_stops_without_writing_success(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _auto_verify_constants(monkeypatch, attempts=4)
    service = AutoPurchaseService(
        [{
            "status": "conflict",
            "reason": "金额或 SKU 冲突",
        }]
    )
    controller, store_a, _store_b, account, links = _controller(
        tmp_path,
        service,
    )

    opened = _open_purchase(
        controller,
        store_a,
        "PRODUCT-1",
        links[store_a]["id"],
        account,
    )
    status = _wait_auto_verify(
        controller,
        opened["verification_job_id"],
        {"pending", "error"},
    )

    assert status["status"] == "pending"
    assert len(service.verify_calls) == 1
    assert controller.db.list_procurement_purchase_records(
        store_a,
        "CUSTOMER-ORDER-A",
        "PRODUCT-1",
    ) == []


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        ("ambiguous", "发现两个候选采购订单"),
        ("login_required", "采购账号登录已失效"),
        ("risk_control", "1688 官方订单读取触发安全验证/风控"),
        ("error", "官方订单读取超时"),
    ],
)
def test_auto_verify_terminal_failures_never_write_success(
    tmp_path: Path,
    monkeypatch,
    status: str,
    reason: str,
) -> None:
    _auto_verify_constants(monkeypatch, attempts=4)
    service = AutoPurchaseService(
        [{"status": status, "reason": reason}],
    )
    controller, store_a, _store_b, account, links = _controller(
        tmp_path,
        service,
    )

    opened = _open_purchase(
        controller,
        store_a,
        "PRODUCT-1",
        links[store_a]["id"],
        account,
    )
    result = _wait_auto_verify(
        controller,
        opened["verification_job_id"],
        {"pending", "error"},
    )

    assert result["status"] == "pending"
    assert len(service.verify_calls) == 1
    assert controller.db.list_procurement_purchase_records(
        store_a,
        "CUSTOMER-ORDER-A",
        "PRODUCT-1",
    ) == []
    attempt = controller.db.get_procurement_purchase_attempt(
        store_a,
        "CUSTOMER-ORDER-A",
        "PRODUCT-1",
        links[store_a]["id"],
    )
    assert attempt["status"] == "verification_pending"


def test_auto_verify_is_deduplicated_for_the_same_open_page(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _auto_verify_constants(monkeypatch, attempts=1)
    gate = __import__("threading").Event()
    service = AutoPurchaseService(
        [{
            "status": "confirmed",
            "page_closed": True,
            "read_attempts": 1,
            "order": _record(),
        }],
        wait_gate=gate,
    )
    controller, store_a, _store_b, account, links = _controller(
        tmp_path,
        service,
    )

    first = _open_purchase(
        controller,
        store_a,
        "PRODUCT-1",
        links[store_a]["id"],
        account,
    )
    second = _open_purchase(
        controller,
        store_a,
        "PRODUCT-1",
        links[store_a]["id"],
        account,
    )
    assert first["verification_job_id"] == second["verification_job_id"]
    waiting = controller.dispatch(
        "orders.procurement.purchase-auto-verify.status",
        {"job_id": first["verification_job_id"]},
    )
    assert waiting["status"] == "waiting"
    gate.set()
    _wait_auto_verify(
        controller,
        first["verification_job_id"],
        {"confirmed"},
    )
    assert len(service.verify_calls) == 1


def test_auto_verify_does_not_read_orders_before_payment_signal(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _auto_verify_constants(monkeypatch, attempts=3)
    service = AutoPurchaseService(
        [],
        signal={
            "status": "timeout",
            "reason": "采购页面仍打开，尚未检测到付款完成",
        },
    )
    controller, store_a, _store_b, account, links = _controller(
        tmp_path,
        service,
    )

    opened = _open_purchase(
        controller,
        store_a,
        "PRODUCT-1",
        links[store_a]["id"],
        account,
    )
    status = _wait_auto_verify(
        controller,
        opened["verification_job_id"],
        {"pending", "error"},
    )

    assert status["status"] == "pending"
    assert service.verify_calls == []
    assert controller.db.list_procurement_purchase_records(
        store_a,
        "CUSTOMER-ORDER-A",
        "PRODUCT-1",
    ) == []


def test_auto_verify_uses_success_order_id_closes_first_and_is_idempotent(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _auto_verify_constants(monkeypatch, attempts=1)
    matched_order = _record(UPSTREAM_ORDER_ID)
    matched_order.update(
        {
            "mail_no": "SF1234567890",
            "carrier": "SF",
            "status_text": "等待卖家发货",
        }
    )
    service = AutoPurchaseService(
        [{
            "status": "confirmed",
            "page_closed": True,
            "read_attempts": 1,
            "order": matched_order,
        }],
        signal={
            "status": "payment_observed",
            "reason": "make_order_success",
            "upstream_order_id": UPSTREAM_ORDER_ID,
        },
    )
    controller, store_a, _store_b, account, links = _controller(
        tmp_path,
        service,
    )

    opened = _open_purchase(
        controller,
        store_a,
        "PRODUCT-1",
        links[store_a]["id"],
        account,
    )
    status = _wait_auto_verify(
        controller,
        opened["verification_job_id"],
        {"confirmed"},
    )

    assert service.events[:3] == ["wait", "close", "verify"]
    assert service.verify_calls[0]["expected"]["upstream_order_id"] == (
        UPSTREAM_ORDER_ID
    )
    assert status["upstream_order_id"] == UPSTREAM_ORDER_ID
    assert status["detail"]["workbench"]["fulfillment_stage"] == "wait_supplier"
    records = controller.db.list_procurement_purchase_records(
        store_a,
        "CUSTOMER-ORDER-A",
        "PRODUCT-1",
        link_id=links[store_a]["id"],
    )
    assert len(records) == 1
    assert records[0]["upstream_order_id"] == UPSTREAM_ORDER_ID
    assert records[0]["mail_no"] == "SF1234567890"
    assert records[0]["carrier"] == "SF"

    repeated = controller.dispatch(
        "orders.procurement.purchase-confirm",
        {
            "store_id": store_a,
            "order_id": "CUSTOMER-ORDER-A",
            "product_id": "PRODUCT-1",
            "link_id": links[store_a]["id"],
            "account_id": account,
            "upstream_order_id": UPSTREAM_ORDER_ID,
        },
    )
    assert repeated["status"] == "confirmed"
    assert repeated["reused"] is True
    assert len(service.verify_calls) == 1
    assert len(controller.db.list_procurement_purchase_records(
        store_a,
        "CUSTOMER-ORDER-A",
        "PRODUCT-1",
        link_id=links[store_a]["id"],
    )) == 1

    different_order = controller.dispatch(
        "orders.procurement.purchase-confirm",
        {
            "store_id": store_a,
            "order_id": "CUSTOMER-ORDER-A",
            "product_id": "PRODUCT-1",
            "link_id": links[store_a]["id"],
            "account_id": account,
            "upstream_order_id": "3317035837075001999",
        },
    )
    assert different_order["status"] == "conflict"
    assert "已关联其它上游订单" in different_order["reason"]
    assert len(service.verify_calls) == 1


def test_exact_official_read_failure_never_marks_order_purchased(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _auto_verify_constants(monkeypatch, attempts=1)
    service = AutoPurchaseService(
        [{
            "status": "not_found",
            "reason": f"官方尚未回读到采购订单 {UPSTREAM_ORDER_ID}",
        }],
        signal={
            "status": "payment_observed",
            "reason": "make_order_success",
            "upstream_order_id": UPSTREAM_ORDER_ID,
        },
    )
    controller, store_a, _store_b, account, links = _controller(
        tmp_path,
        service,
    )

    opened = _open_purchase(
        controller,
        store_a,
        "PRODUCT-1",
        links[store_a]["id"],
        account,
    )
    status = _wait_auto_verify(
        controller,
        opened["verification_job_id"],
        {"pending", "error"},
    )

    assert status["status"] == "pending"
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


def test_service_waits_for_payment_or_return_signal_without_polling_orders() -> None:
    service = object.__new__(SyncService)
    service._procurement_windows_lock = threading.RLock()
    window = SimpleNamespace(
        purchase_observed=threading.Event(),
        returned_to_software=threading.Event(),
        closed=threading.Event(),
        purchase_signal_reason="",
    )
    service._procurement_windows = {7: window}

    def signal() -> None:
        window.purchase_signal_reason = "payment_success_page"
        window.purchase_observed.set()

    timer = threading.Timer(0.05, signal)
    timer.start()
    result = service.wait_for_procurement_purchase_signal(
        7,
        timeout_seconds=1,
    )
    timer.join()

    assert result == {
        "status": "payment_observed",
        "reason": "payment_success_page",
    }
