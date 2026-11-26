from __future__ import annotations

import time
from dataclasses import replace

from weimi_manager.db import Database
from weimi_manager.models import Order, SyncResult
from weimi_manager.service import SyncService
from weimi_manager.webui.controller import NextUiController


class RefreshOrdersService:
    def __init__(self, database):
        self.database = database
        self.calls = []

    def refresh_orders_recent(
        self,
        store,
        start_time,
        end_time,
        current_order_ids,
    ):
        self.calls.append(
            (
                store.id,
                int(start_time),
                int(end_time),
                list(current_order_ids),
            )
        )
        order = self.database.get_order(store.id, "OLD-ORDER")
        assert order is not None
        self.database.upsert_order(replace(order, status=200, status_text="售后完成"))
        return SyncResult("orders", len(current_order_ids), "ok", success=1)


class PlatformOrderClient:
    def __init__(self, database, store_id):
        self.database = database
        self.store_id = store_id
        self.update_calls = []
        self.detail_calls = []

    def fetch_order_ids_by_update(self, start_time, end_time):
        self.update_calls.append((start_time, end_time))
        return ["UPDATED-ORDER"]

    def fetch_order(self, store, order_id):
        self.detail_calls.append(order_id)
        created = int(time.time()) - 10 * 86400
        status = 200 if order_id == "UPDATED-ORDER" else 30
        return Order(
            store_id=self.store_id,
            store_name="刷新店铺",
            order_id=order_id,
            status=status,
            status_text="售后退款完成" if status == 200 else "待收货",
            create_time=created,
            update_time=int(time.time()),
            pay_time=created,
            total_amount=1000,
            product_amount=1000,
            freight_amount=0,
            discount_amount=0,
            buyer_name="测试买家",
            phone="13800138000",
            address="测试地址",
            waybill_id="",
            carrier="",
            merchant_notes="",
            items=[],
            raw={},
        )


def _seed(tmp_path):
    database = Database(tmp_path / "orders-refresh.db")
    store_id = database.add_store("刷新店铺", "wx-order-refresh", "secret")
    created = int(time.time()) - 10 * 86400
    database.upsert_order(
        Order(
            store_id=store_id,
            store_name="刷新店铺",
            order_id="OLD-ORDER",
            status=30,
            status_text="待收货",
            create_time=created,
            update_time=created,
            pay_time=created,
            total_amount=1000,
            product_amount=1000,
            freight_amount=0,
            discount_amount=0,
            buyer_name="测试买家",
            phone="13800138000",
            address="测试地址",
            waybill_id="",
            carrier="",
            merchant_notes="",
            items=[],
            raw={},
        )
    )
    return database, store_id


def test_global_order_sync_uses_update_window_and_current_order_ids(
    tmp_path,
) -> None:
    database, store_id = _seed(tmp_path)
    service = RefreshOrdersService(database)
    controller = NextUiController(database, product_service=service)

    result = controller.dispatch(
        "orders.sync",
        {
            "store_id": store_id,
            "current_order_ids": {str(store_id): ["OLD-ORDER"]},
        },
    )

    assert service.calls
    _store_id, start_time, end_time, current_ids = service.calls[0]
    assert end_time - start_time == 48 * 3600
    assert current_ids == ["OLD-ORDER"]
    assert result["status_changes"] == [
        {
            "store_id": store_id,
            "store_name": "刷新店铺",
            "order_id": "OLD-ORDER",
            "before_status": 30,
            "after_status": 200,
            "before_status_text": "待收货",
            "after_status_text": "售后退款完成",
        }
    ]
    order = database.get_order(store_id, "OLD-ORDER")
    assert order is not None
    assert order.status == 200


def test_quick_refresh_uses_update_window_and_returns_status_changes(
    tmp_path,
) -> None:
    database, store_id = _seed(tmp_path)
    service = RefreshOrdersService(database)
    controller = NextUiController(database, product_service=service)

    started = controller.dispatch(
        "orders.quick-refresh.start",
        {
            "store_id": store_id,
            "current_order_ids": {str(store_id): ["OLD-ORDER"]},
        },
    )
    deadline = time.time() + 5
    job = {}
    while time.time() < deadline:
        job = controller.dispatch(
            "orders.quick-refresh.status",
            {"job_id": started["job_id"]},
        )
        if job["status"] in {"completed", "partial", "failed"}:
            break
        time.sleep(0.02)

    assert job["status"] == "completed"
    _store_id, start_time, end_time, current_ids = service.calls[0]
    assert end_time - start_time == 48 * 3600
    assert current_ids == ["OLD-ORDER"]
    assert job["status_changes"][0]["before_status"] == 30
    assert job["status_changes"][0]["after_status"] == 200


def test_sync_service_combines_update_time_ids_with_current_page_ids(
    tmp_path,
) -> None:
    database, store_id = _seed(tmp_path)
    store = database.get_store(store_id)
    assert store is not None
    client = PlatformOrderClient(database, store_id)
    service = SyncService(database)
    service.client_for = lambda _store: client

    result = service.refresh_orders_recent(
        store,
        int(time.time()) - 48 * 3600,
        int(time.time()),
        ["OLD-ORDER"],
    )

    assert result.count == 2
    assert client.update_calls
    assert client.detail_calls == ["UPDATED-ORDER", "OLD-ORDER"]
    updated = database.get_order(store_id, "UPDATED-ORDER")
    assert updated is not None
    assert updated.status == 200
