from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from weimi_manager import service as service_module
from weimi_manager.address_parser import parse_address_text
from weimi_manager.db import Database
from weimi_manager.models import Order, OrderItem
from weimi_manager.service import SyncService
from weimi_manager.webui.controller import NextUiController


class SafetyProcurementService:
    def __init__(self) -> None:
        self.open_calls = []
        self.verify_calls = []
        self.close_calls = []
        self.verify_result = {
            "status": "not_found",
            "reason": "未发现新采购订单",
            "read_attempts": 1,
        }

    def open_procurement_window(
        self,
        account_id,
        url,
        address=None,
        details=None,
    ):
        self.open_calls.append(
            (account_id, url, dict(address or {}), dict(details or {}))
        )
        return {
            "status": "opened",
            "reused": len(self.open_calls) > 1,
            "page_url": url,
        }

    def verify_procurement_purchase(self, account_id, expected):
        self.verify_calls.append((account_id, expected))
        return dict(self.verify_result)

    def close_procurement_window(self, account_id):
        self.close_calls.append(account_id)
        return True

    def decode_order_sensitive(self, store, order_id):
        self.db.update_order_sensitive(
            store.id,
            order_id,
            "张三",
            "13800138000",
            "浙江省杭州市西湖区文三路88号",
            {
                "province": "浙江省",
                "city": "杭州市",
                "county": "西湖区",
                "street": "",
                "address": "文三路88号",
            },
        )

    def sync_procurement_note_to_wechat(self, *args, **kwargs):
        raise AssertionError("不得自动写微信官方备注")


def _controller(tmp_path: Path):
    database = Database(tmp_path / "safety.db")
    store_id = database.add_store("再百宽栾商行", "wx-safe", "secret")
    account_id = database.add_procurement_account(
        "1688",
        "1688采购",
        "fixture-buyer",
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
            store_name="再百宽栾商行",
            order_id="9000000000000000001",
            status=20,
            status_text="待发货",
            create_time=1_700_000_000,
            update_time=1_700_000_010,
            pay_time=1_700_000_005,
            total_amount=6800,
            product_amount=6800,
            freight_amount=0,
            discount_amount=0,
            buyer_name="张*",
            phone="138****0000",
            address="浙江省杭州市****",
            waybill_id="",
            carrier="",
            merchant_notes="",
            items=[
                OrderItem(
                    product_id="PRODUCT-1",
                    sku_id="LOCAL-SKU-1",
                    title="本地商品",
                    sku_count=1,
                    sale_price=6800,
                    thumb_url="https://example.test/product.jpg",
                    sku_attributes="黑色 / XL",
                )
            ],
            raw={
                "order_detail": {
                    "delivery_info": {
                        "address_info": {
                            "user_name": "张*",
                            "tel_number": "138****0000",
                            "province_name": "浙江省",
                            "city_name": "杭州市",
                            "county_name": "",
                            "detail_info": "****",
                        }
                    },
                    "ext_info": {
                        "customer_notes": "请放门卫，不要打电话",
                    }
                }
            },
        )
    )
    link_id = database.upsert_product_purchase_link(
        store_id,
        "PRODUCT-1",
        {
            "source_url": "https://detail.1688.com/offer/1048048297681.html",
            "platform": "1688",
            "account_id": account_id,
            "source_product_id": "1048048297681",
        },
    )
    service = SafetyProcurementService()
    service.db = database
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
        now_provider=lambda: datetime(2026, 11, 25, 22, 0, 0),
    )
    return controller, service, store_id, account_id, link_id


def _open(controller, store_id, account_id, link_id):
    return controller.dispatch(
        "orders.procurement.purchase-open",
        {
            "store_id": store_id,
            "order_id": "9000000000000000001",
            "product_id": "PRODUCT-1",
            "link_id": link_id,
            "account_id": account_id,
        },
    )


def _verify(controller, store_id, account_id, link_id):
    return controller.dispatch(
        "orders.procurement.purchase-confirm",
        {
            "store_id": store_id,
            "order_id": "9000000000000000001",
            "product_id": "PRODUCT-1",
            "link_id": link_id,
            "account_id": account_id,
            "expected_amount_cents": 4680,
            "expected_sku": "黑色 / XL",
        },
    )


def test_open_purchase_is_blocked_by_encrypted_address(
    tmp_path: Path,
) -> None:
    controller, service, store_id, account_id, link_id = _controller(tmp_path)

    with pytest.raises(ValueError, match="采购地址已加密/不完整"):
        _open(controller, store_id, account_id, link_id)
    assert service.open_calls == []
    assert service.verify_calls == []


def test_address_parse_and_software_cache_unblocks_purchase(
    tmp_path: Path,
) -> None:
    controller, service, store_id, account_id, link_id = _controller(tmp_path)
    parsed = controller.dispatch(
        "orders.address.parse",
        {
            "text": (
                "收货人：张三\n"
                "电话：13800138000\n"
                "浙江省 杭州市 西湖区 文三路 88 号 1 幢 101"
            )
        },
    )
    assert parsed["valid"] is True
    saved = controller.dispatch(
        "orders.address.save-software",
        {
            "store_id": store_id,
            "order_id": "9000000000000000001",
            "address": parsed["address"],
        },
    )
    assert saved["address_ready"] is True

    opened = _open(controller, store_id, account_id, link_id)
    assert opened["status"] == "page_opened_pending_payment"
    assert len(service.open_calls) == 1
    assert service.open_calls[0][2]["user_name"] == "张三"
    assert service.open_calls[0][3]["items"][0]["product_id"] == "PRODUCT-1"
    assert service.open_calls[0][3]["recipient"]["user_name"] == "张三"
    assert service.open_calls[0][3]["order"]["id"] == "9000000000000000001"
    assert "门卫" in service.open_calls[0][3]["order"]["customer_message"]
    assert service.open_calls[0][3]["items"][0]["thumb_url"]
    assert service.verify_calls == []
    detail = controller.dispatch(
        "orders.workbench.detail",
        {
            "store_id": store_id,
            "order_id": "9000000000000000001",
        },
    )
    link = next(
        row
        for row in detail["product_purchase_links"]
        if row["id"] == link_id
    )
    assert link["purchase_attempt_status"] == "page_open_pending_payment"
    assert link["purchase_confirmed"] is False


def test_manual_verify_is_required_and_runs_once_per_click(
    tmp_path: Path,
) -> None:
    controller, service, store_id, account_id, link_id = _controller(tmp_path)
    controller.dispatch(
        "orders.address.save-software",
        {
            "store_id": store_id,
            "order_id": "9000000000000000001",
            "address": {
                "user_name": "张三",
                "tel_number": "13800138000",
                "province_name": "浙江省",
                "city_name": "杭州市",
                "county_name": "西湖区",
                "street_name": "",
                "detail_info": "文三路88号",
            },
        },
    )
    without_open = _verify(controller, store_id, account_id, link_id)
    assert without_open["status"] == "open_required"
    assert service.verify_calls == []

    _open(controller, store_id, account_id, link_id)
    failed = _verify(controller, store_id, account_id, link_id)
    assert failed["status"] == "not_found"
    assert failed["page_closed"] is False
    assert len(service.verify_calls) == 1
    assert service.close_calls == []


def test_concurrent_manual_verify_is_mutually_exclusive(
    tmp_path: Path,
) -> None:
    controller, service, store_id, account_id, link_id = _controller(tmp_path)
    controller.dispatch(
        "orders.address.save-software",
        {
            "store_id": store_id,
            "order_id": "9000000000000000001",
            "address": {
                "user_name": "张三",
                "tel_number": "13800138000",
                "province_name": "浙江省",
                "city_name": "杭州市",
                "county_name": "西湖区",
                "street_name": "",
                "detail_info": "文三路88号",
            },
        },
    )
    verify_started = threading.Event()
    release_verify = threading.Event()
    original_verify = service.verify_procurement_purchase

    def blocking_verify(account, expected):
        verify_started.set()
        assert release_verify.wait(5)
        return original_verify(account, expected)

    service.verify_procurement_purchase = blocking_verify
    _open(controller, store_id, account_id, link_id)
    first_result = {}

    def run_first_verify() -> None:
        first_result.update(_verify(controller, store_id, account_id, link_id))

    worker = threading.Thread(target=run_first_verify)
    worker.start()
    assert verify_started.wait(5)
    duplicate = _verify(controller, store_id, account_id, link_id)
    assert duplicate["status"] == "in_progress"
    release_verify.set()
    worker.join(5)
    assert not worker.is_alive()
    assert first_result["status"] == "not_found"
    assert len(service.verify_calls) == 1


def test_unique_confirm_closes_window_and_repeat_is_idempotent(
    tmp_path: Path,
) -> None:
    controller, service, store_id, account_id, link_id = _controller(tmp_path)
    controller.dispatch(
        "orders.address.save-software",
        {
            "store_id": store_id,
            "order_id": "9000000000000000001",
            "address": {
                "user_name": "张三",
                "tel_number": "13800138000",
                "province_name": "浙江省",
                "city_name": "杭州市",
                "county_name": "西湖区",
                "street_name": "",
                "detail_info": "文三路88号",
            },
        },
    )
    service.verify_result = {
        "status": "confirmed",
        "read_attempts": 1,
        "order": {
            "external_order_id": "1688-UNIQUE",
            "trade_no": "1688-UNIQUE",
            "status": "WAIT_SELLER_SEND_GOODS",
            "status_text": "等待卖家发货",
            "goods_title": "目标商品",
            "goods_count": 1,
            "pay_amount": 4680,
            "seller": "上家",
            "purchase_time": "2026-11-25T22:01:00",
            "sku_text": "黑色 / XL",
            "unit_price": 4680,
            "freight_amount": 0,
            "raw": {
                "offerId": "1048048297681",
                "tradeNo": "1688-UNIQUE",
            },
        },
    }
    _open(controller, store_id, account_id, link_id)
    first = _verify(controller, store_id, account_id, link_id)
    second = _verify(controller, store_id, account_id, link_id)

    assert first["status"] == "confirmed"
    assert first["page_closed"] is True
    assert service.close_calls == [account_id]
    assert second["reused"] is True
    assert len(service.verify_calls) == 1


def test_reopen_uses_one_controlled_window_per_account(
    tmp_path: Path,
) -> None:
    controller, service, store_id, account_id, link_id = _controller(tmp_path)
    controller.dispatch(
        "orders.address.save-software",
        {
            "store_id": store_id,
            "order_id": "9000000000000000001",
            "address": {
                "user_name": "张三",
                "tel_number": "13800138000",
                "province_name": "浙江省",
                "city_name": "杭州市",
                "county_name": "西湖区",
                "street_name": "",
                "detail_info": "文三路88号",
            },
        },
    )
    _open(controller, store_id, account_id, link_id)
    _open(controller, store_id, account_id, link_id)
    assert len(service.open_calls) == 2
    assert service.open_calls[0][2:] == service.open_calls[1][2:]
    assert service.verify_calls == []


def test_ambiguous_verify_keeps_window_open_without_success_record(
    tmp_path: Path,
) -> None:
    controller, service, store_id, account_id, link_id = _controller(tmp_path)
    controller.dispatch(
        "orders.address.save-software",
        {
            "store_id": store_id,
            "order_id": "9000000000000000001",
            "address": {
                "user_name": "张三",
                "tel_number": "13800138000",
                "province_name": "浙江省",
                "city_name": "杭州市",
                "county_name": "西湖区",
                "street_name": "",
                "detail_info": "文三路88号",
            },
        },
    )
    service.verify_result = {
        "status": "ambiguous",
        "reason": "发现两个候选订单",
        "read_attempts": 1,
    }
    _open(controller, store_id, account_id, link_id)
    result = _verify(controller, store_id, account_id, link_id)
    assert result["status"] == "ambiguous"
    assert result["page_closed"] is False
    assert service.close_calls == []
    assert controller.db.list_procurement_purchase_records(
        store_id,
        "9000000000000000001",
        "PRODUCT-1",
    ) == []


def test_parse_address_supports_labels_spaces_and_missing_fields() -> None:
    parsed = parse_address_text(
        "张三 13800138000 浙江省 杭州市 西湖区 文三路88号"
    )
    assert parsed["valid"] is True
    assert parsed["address"]["county_name"] == "西湖区"
    missing = parse_address_text("张三 13800138000 杭州市")
    assert missing["valid"] is False
    assert missing["address"]["user_name"] == "张三"
    assert "省" in missing["reason"] or "区县" in missing["reason"]


def test_phone_extension_is_accepted_when_saving_complete_address(
    tmp_path: Path,
) -> None:
    controller, _service, store_id, _account_id, _link_id = _controller(
        tmp_path
    )
    result = controller.dispatch(
        "orders.address.save-software",
        {
            "store_id": store_id,
            "order_id": "9000000000000000001",
            "address": {
                "user_name": "测试收件人",
                "tel_number": "13800138000-6155",
                "province_name": "测试省",
                "city_name": "测试市",
                "county_name": "测试区",
                "street_name": "",
                "detail_info": "测试街道虚拟仓库一号门",
            },
        },
    )
    assert result["address_ready"] is True
    assert result["address"]["tel_number"] == "13800138000-6155"


def test_complete_software_address_is_not_overwritten_by_masked_refresh(
    tmp_path: Path,
) -> None:
    controller, _service, store_id, _account_id, _link_id = _controller(
        tmp_path
    )
    plain = {
        "user_name": "张三",
        "tel_number": "13800138000",
        "province_name": "浙江省",
        "city_name": "杭州市",
        "county_name": "西湖区",
        "street_name": "",
        "detail_info": "文三路88号",
    }
    controller.db.upsert_order_address_cache(
        store_id,
        "9000000000000000001",
        plain,
        source="software_manual",
        force=True,
    )
    controller.db.upsert_order_address_cache(
        store_id,
        "9000000000000000001",
        {
            **plain,
            "user_name": "张*",
            "tel_number": "138****0000",
            "county_name": "",
            "detail_info": "****",
        },
        source="official_sync",
        source_updated_at="2026-11-25T23:00:00",
    )
    cached = controller.db.get_order_address_cache(
        store_id,
        "9000000000000000001",
    )
    assert cached["address"] == plain
    detail = controller.dispatch(
        "orders.workbench.detail",
        {
            "store_id": store_id,
            "order_id": "9000000000000000001",
        },
    )
    assert detail["procurement_address_ready"] is True
    assert detail["order"]["address_info"]["user_name"] == "张三"
    assert detail["order"]["address_info"]["tel_number"] == "13800138000"


def test_link_delete_does_not_require_address_or_an_open_window(
    tmp_path: Path,
) -> None:
    controller, service, store_id, _account_id, link_id = _controller(tmp_path)

    result = controller.dispatch(
        "orders.procurement.link-delete",
        {
            "store_id": store_id,
            "order_id": "9000000000000000001",
            "product_id": "PRODUCT-1",
            "link_id": link_id,
        },
    )

    assert result["deleted"] == link_id
    assert result["detail"]["product_purchase_links"] == []
    assert service.open_calls == []
    assert service.verify_calls == []


def test_decrypted_address_is_cached_and_survives_later_masked_refresh(
    tmp_path: Path,
) -> None:
    controller, _service, store_id, _account_id, _link_id = _controller(
        tmp_path
    )
    decrypted = controller.dispatch(
        "orders.decrypt-address",
        {
            "store_id": store_id,
            "order_id": "9000000000000000001",
        },
    )
    assert decrypted["address_ready"] is True
    controller.db.upsert_order_address_cache(
        store_id,
        "9000000000000000001",
        {
            "user_name": "张*",
            "tel_number": "138****0000",
            "province_name": "浙江省",
            "city_name": "杭州市",
            "county_name": "",
            "street_name": "",
            "detail_info": "****",
        },
        source="official_sync",
    )
    detail = controller.dispatch(
        "orders.workbench.detail",
        {
            "store_id": store_id,
            "order_id": "9000000000000000001",
        },
    )
    assert detail["procurement_address_ready"] is True
    assert detail["procurement_address"]["user_name"] == "张三"
    assert detail["procurement_address"]["tel_number"] == "13800138000"
    listed = controller.dispatch(
        "orders.list",
        {
            "store_id": store_id,
            "limit": 100,
        },
    )["orders"][0]
    assert listed["buyer_name"] == "张三"
    assert listed["phone"] == "13800138000"
    assert "文三路88号" in listed["address"]


def test_1688_purchase_no_longer_opens_dead_member_address_page() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "weimi_manager"
        / "procurement.py"
    ).read_text(encoding="utf-8")
    assert "member.1688.com/member/address/list.htm" not in source
    assert "member1.taobao.com/member/fresh/deliver_address.htm" not in source
    assert "_create_official_address" not in source
    window_source = source.split(
        "class ControlledPurchaseWindow:",
        1,
    )[1].split("class MtopClient:", 1)[0]
    assert window_source.count("page.goto(") == 1
    assert "page.goto(\n                self.url" in window_source
    before_manual_verify = window_source.split("def verify_once", 1)[0]
    assert 'self.commands.put(("verify"' not in before_manual_verify
    assert window_source.count("_read_order_list_in_context") == 1
    assert 'self.commands.put(("verify"' in window_source
    assert "for candidate in active_pages" in window_source
    assert "smart_make_order" in window_source


def test_service_reuses_the_same_product_window_without_address_work(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = Database(tmp_path / "window-reuse.db")
    account_id = database.add_procurement_account(
        "1688",
        "1688采购",
        "fixture-buyer",
    )
    windows = []

    class FakeWindow:
        def __init__(self, _db, _data_dir, account, url, *_args) -> None:
            self.account = account
            self.url = url
            self.closed = threading.Event()
            self.closed_count = 0
            windows.append(self)

        def start(self) -> None:
            return None

        def close(self) -> None:
            self.closed_count += 1
            self.closed.set()

    monkeypatch.setattr(
        service_module,
        "ControlledPurchaseWindow",
        FakeWindow,
    )
    service = SimpleNamespace(
        db=database,
        _procurement_windows={},
        _procurement_windows_lock=threading.RLock(),
    )
    url = "https://detail.1688.com/offer/1048048297681.html"

    first = SyncService.open_procurement_window(
        service,
        account_id,
        url,
    )
    second = SyncService.open_procurement_window(
        service,
        account_id,
        url,
    )

    assert first["reused"] is False
    assert second["reused"] is True
    assert len(windows) == 1
    assert windows[0].closed_count == 0


def test_service_closes_the_old_product_before_opening_a_new_one(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = Database(tmp_path / "window-switch.db")
    account_id = database.add_procurement_account(
        "1688",
        "1688采购",
        "fixture-buyer",
    )
    windows = []

    class FakeWindow:
        def __init__(self, _db, _data_dir, _account, url, *_args) -> None:
            self.url = url
            self.closed = threading.Event()
            self.closed_count = 0
            windows.append(self)

        def start(self) -> None:
            return None

        def close(self) -> None:
            self.closed_count += 1
            self.closed.set()

    monkeypatch.setattr(
        service_module,
        "ControlledPurchaseWindow",
        FakeWindow,
    )
    service = SimpleNamespace(
        db=database,
        _procurement_windows={},
        _procurement_windows_lock=threading.RLock(),
    )

    first = SyncService.open_procurement_window(
        service,
        account_id,
        "https://detail.1688.com/offer/1048048297681.html",
    )
    second = SyncService.open_procurement_window(
        service,
        account_id,
        "https://detail.1688.com/offer/1048048297682.html",
    )

    assert first["reused"] is False
    assert second["reused"] is False
    assert len(windows) == 2
    assert windows[0].closed_count == 1
    assert windows[1].closed_count == 0
    assert service._procurement_windows[account_id] is windows[1]


def test_purchase_overlay_contains_order_sku_and_recipient() -> None:
    from weimi_manager.procurement import purchase_overlay_script

    script = purchase_overlay_script(
        {
            "account": {"label": "1688-fixture-buyer"},
            "store": {"name": "再百宽栾商行"},
            "order": {
                "id": "9000000000000000001",
                "total_amount_text": "¥10.78",
                "cost_text": "待填写",
                "profit_text": "—",
                "customer_message": "请放门卫",
            },
            "items": [
                {
                    "product_id": "10001978844472",
                    "sku_id": "37352283522",
                    "title": "本地商品",
                    "sku_attributes": "颜色:五合一刷-灰色1个装",
                    "quantity": 1,
                    "sale_price_text": "¥10.78",
                }
            ],
            "recipient": {
                "user_name": "测试收件人",
                "tel_number": "13800138000-6155",
                "full_address": "测试省测试市测试区测试街道",
            },
        }
    )
    assert "langlang-procurement-sidebar" in script
    assert "9000000000000000001" in script
    assert "37352283522" in script
    assert "13800138000-6155" in script
    assert "客户留言" in script
    assert "请放门卫" in script
    assert "row.append(img)" in script
