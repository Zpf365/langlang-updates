from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
import requests

from weimi_manager.db import Database
from weimi_manager.dongxiao.application.aftersale_service import (
    DongxiaoAftersaleService,
)
from weimi_manager.dongxiao.application.order_service import (
    DongxiaoOrderService,
)
from weimi_manager.dongxiao.domain.enums import (
    AftersaleStatus,
    OrderStatus,
    OrderVerificationSource,
)
from weimi_manager.dongxiao.domain.models import AftersaleCase, OrderRecord
from weimi_manager.dongxiao.infrastructure.repository import (
    DongxiaoRepository,
)
from weimi_manager.wechat_api import (
    WechatApiError,
    WechatShopClient,
)


class PagingTransport:
    def __init__(
        self,
        responses: list[dict],
        *,
        fail_on_call: int | None = None,
    ) -> None:
        self.responses = list(responses)
        self.fail_on_call = fail_on_call
        self.api_calls = 0

    def post_json(self, url, payload, timeout=25):
        del payload, timeout
        if url.endswith("/cgi-bin/stable_token"):
            return {"access_token": "token", "expires_in": 7200}
        self.api_calls += 1
        if self.fail_on_call == self.api_calls:
            raise requests.ConnectionError("network interrupted")
        if not self.responses:
            raise AssertionError("unexpected extra page request")
        return self.responses.pop(0)

    def get_json(self, url, timeout=25):
        raise AssertionError(f"unexpected GET {url}")


def _client(transport: PagingTransport) -> WechatShopClient:
    return WechatShopClient(
        "wx-pagination",
        "secret",
        transport=transport,
        min_request_interval=0,
    )


def test_order_pagination_rejects_missing_cursor() -> None:
    client = _client(
        PagingTransport(
            [
                {
                    "errcode": 0,
                    "order_id_list": ["order-1"],
                    "next_key": "",
                    "has_more": True,
                }
            ]
        )
    )

    with pytest.raises(WechatApiError, match="缺少 next_key"):
        client.fetch_order_ids(1, 2)


def test_order_pagination_rejects_repeated_cursor() -> None:
    client = _client(
        PagingTransport(
            [
                {
                    "errcode": 0,
                    "order_id_list": ["order-1"],
                    "next_key": "same",
                    "has_more": True,
                },
                {
                    "errcode": 0,
                    "order_id_list": ["order-2"],
                    "next_key": "same",
                    "has_more": True,
                },
            ]
        )
    )

    with pytest.raises(WechatApiError, match="游标重复"):
        client.fetch_order_ids(1, 2)


def test_order_pagination_has_maximum_page_guard(monkeypatch) -> None:
    import weimi_manager.wechat_api as api

    monkeypatch.setattr(api, "MAX_LIST_PAGES", 2)
    client = _client(
        PagingTransport(
            [
                {
                    "errcode": 0,
                    "order_id_list": ["order-1"],
                    "next_key": "page-2",
                    "has_more": True,
                },
                {
                    "errcode": 0,
                    "order_id_list": ["order-2"],
                    "next_key": "page-3",
                    "has_more": True,
                },
            ]
        )
    )

    with pytest.raises(WechatApiError, match="最大页数"):
        client.fetch_order_ids(1, 2)


def test_aftersale_pagination_rejects_missing_cursor_and_network_interrupt() -> None:
    missing = _client(
        PagingTransport(
            [
                {
                    "errcode": 0,
                    "after_sale_order_id_list": ["after-1"],
                    "next_key": "",
                    "has_more": True,
                }
            ]
        )
    )
    with pytest.raises(WechatApiError, match="缺少 next_key"):
        missing.fetch_after_sale_ids(1, 2)

    interrupted = _client(
        PagingTransport(
            [
                {
                    "errcode": 0,
                    "after_sale_order_id_list": ["after-1"],
                    "next_key": "page-2",
                    "has_more": True,
                }
            ],
            fail_on_call=2,
        )
    )
    with pytest.raises(requests.ConnectionError):
        interrupted.fetch_after_sale_ids(1, 2)


def test_duplicate_ids_across_pages_are_deduplicated() -> None:
    client = _client(
        PagingTransport(
            [
                {
                    "errcode": 0,
                    "order_id_list": ["order-1", "order-2"],
                    "next_key": "page-2",
                    "has_more": True,
                },
                {
                    "errcode": 0,
                    "order_id_list": ["order-2", "order-3"],
                    "next_key": "",
                    "has_more": False,
                },
            ]
        )
    )

    assert client.fetch_order_ids(1, 2) == [
        "order-1",
        "order-2",
        "order-3",
    ]


class InterruptingOrderAdapter:
    def list_order_ids(self, *_args, **_kwargs):
        raise requests.Timeout("order page interrupted")

    def get_order(self, *_args, **_kwargs):
        raise AssertionError("detail must not be fetched")


def test_incomplete_order_sync_keeps_existing_history(tmp_path: Path) -> None:
    db = Database(tmp_path / "fix1-order-pagination.db")
    store_id = db.add_store("分页店铺", "wx-fix1-order-page", "x" * 32)
    store = db.get_store(store_id)
    assert store is not None
    repo = DongxiaoRepository(db.path)
    record = repo.upsert_order_record(
        OrderRecord(
            order_ref_id="existing-order-ref",
            store_id=store.id,
            store_app_id=store.app_id,
            platform_order_id="existing-order",
            status=OrderStatus.COMPLETED,
            total_amount_cents=100,
            product_amount_cents=100,
            freight_amount_cents=0,
            discount_amount_cents=0,
            item_count=0,
            source=OrderVerificationSource.PLATFORM_API,
            last_synced_at=datetime.now(UTC),
        )
    )
    service = DongxiaoOrderService(repo, InterruptingOrderAdapter())

    with pytest.raises(requests.Timeout):
        service.sync_orders(store, start_time=1, end_time=2)

    assert repo.get_order_record(
        store.id,
        record.platform_order_id,
    ) is not None


def test_incomplete_aftersale_sync_keeps_existing_history(tmp_path: Path) -> None:
    db = Database(tmp_path / "fix1-aftersale-pagination.db")
    store_id = db.add_store("售后分页店铺", "wx-fix1-aftersale-page", "x" * 32)
    store = db.get_store(store_id)
    assert store is not None
    repo = DongxiaoRepository(db.path)
    now = datetime.now(UTC)
    case = repo.upsert_aftersale_case(
        AftersaleCase(
            case_id="existing-case",
            store_id=store.id,
            store_app_id=store.app_id,
            platform_case_id="existing-platform-case",
            status=AftersaleStatus.PENDING_REVIEW,
            platform_state=AftersaleStatus.PENDING_REVIEW,
            requested_amount_cents=100,
            approved_amount_cents=0,
            paid_amount_cents=100,
            idempotency_key="existing-case",
            created_at=now,
            updated_at=now,
            source="platform_api",
        )
    )

    class InterruptingAdapter:
        def list_aftersale_ids(self, *_args, **_kwargs):
            raise requests.Timeout("aftersale page interrupted")

        def get_aftersale(self, *_args, **_kwargs):
            raise AssertionError("detail must not be fetched")

    service = DongxiaoAftersaleService(repo, InterruptingAdapter())
    with pytest.raises(requests.Timeout):
        service.sync_aftersales(store, start_time=1, end_time=2)

    assert repo.get_aftersale_case(
        store.id,
        case.case_id,
    ) is not None
