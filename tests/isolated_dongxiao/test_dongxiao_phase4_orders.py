from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import requests

from weimi_manager.db import Database
from weimi_manager.dongxiao.application.aftersale_service import (
    DongxiaoAftersaleService,
)
from weimi_manager.dongxiao.application.order_service import (
    DongxiaoOrderService,
)
from weimi_manager.dongxiao.application.task_scheduler import (
    DongxiaoTaskScheduler,
    RetryPolicy,
)
from weimi_manager.dongxiao.application.task_service import (
    DongxiaoTaskService,
)
from weimi_manager.dongxiao.domain.enums import OrderStatus
from weimi_manager.dongxiao.infrastructure.executors import (
    CompositeTaskExecutor,
)
from weimi_manager.dongxiao.infrastructure.repository import (
    DongxiaoRepository,
)
from weimi_manager.models import Order, OrderItem


class FakeOrderAdapter:
    def __init__(self, order: Order) -> None:
        self.order = order
        self.list_calls = 0
        self.detail_calls = 0

    def list_order_ids(
        self,
        _store,
        start_time: int,
        end_time: int,
        status: int | None = None,
    ):
        del start_time, end_time, status
        self.list_calls += 1
        return [self.order.order_id]

    def get_order(self, _store, order_id: str):
        self.detail_calls += 1
        assert order_id == self.order.order_id
        return self.order


def _store(db: Database):
    store_id = db.add_store("订单店铺", "wx-phase4-order", "x" * 32)
    store = db.get_store(store_id)
    assert store is not None
    return store


def _order(store) -> Order:
    return Order(
        store_id=store.id,
        store_name=store.name,
        order_id="3700000000000000001",
        status=100,
        status_text="完成",
        create_time=1_765_000_000,
        update_time=1_765_003_600,
        pay_time=1_765_000_100,
        total_amount=8800,
        product_amount=9000,
        freight_amount=0,
        discount_amount=200,
        buyer_name="不应写入动销日志",
        phone="13800000000",
        address="敏感地址",
        waybill_id="SF123",
        carrier="顺丰",
        merchant_notes="",
        items=[
            OrderItem(
                product_id="product-1",
                sku_id="sku-1",
                title="真实订单商品",
                sku_count=2,
                sale_price=4500,
            )
        ],
        raw={"status": 100},
    )


def test_real_order_sync_is_idempotent_traceable_and_task_linkable(
    tmp_path: Path,
) -> None:
    db = Database(tmp_path / "phase4-orders.db")
    store = _store(db)
    repo = DongxiaoRepository(db.path)
    adapter = FakeOrderAdapter(_order(store))
    service = DongxiaoOrderService(repo, adapter)

    first = service.sync_orders(
        store,
        start_time=1_764_999_000,
        end_time=1_765_003_000,
    )
    second = service.sync_orders(
        store,
        start_time=1_764_999_000,
        end_time=1_765_003_000,
    )

    assert first.saved == 1
    assert second.saved == 1
    records = service.list_orders(store)
    assert len(records) == 1
    record = records[0]
    assert record.platform_order_id == "3700000000000000001"
    assert record.status is OrderStatus.COMPLETED
    assert record.total_amount_cents == 8800
    assert record.item_count == 1
    assert record.items[0]["product_id"] == "product-1"
    assert "buyer_name" not in record.items[0]
    snapshots = repo.list_order_snapshots(
        store.app_id,
        record.platform_order_id,
    )
    assert len(snapshots) == 2

    task, created = DongxiaoTaskService(
        repo
    ).create_order_verification_task(
        store,
        [record.platform_order_id],
        idempotency_key="verify-order-1",
        actor_id="operator",
        requested_at=datetime(2026, 11, 18, 10, 0, tzinfo=UTC),
    )
    assert created is True
    assert task.total_items == 1
    items = repo.list_task_execution_items(
        task.task_id,
        store_app_id=store.app_id,
    )
    assert items[0].subject_type == "order"
    assert items[0].subject_id == record.platform_order_id

    scheduler = DongxiaoTaskScheduler(
        repo,
        CompositeTaskExecutor(
            repo,
            order_service=service,
            aftersale_service=DongxiaoAftersaleService(
                repo,
                adapter,
            ),
        ),
        poll_interval_seconds=0.02,
    )
    assert scheduler.run_until_idle(timeout=5) is True
    completed = repo.get_task(task.task_id, store_app_id=store.app_id)
    assert completed is not None
    assert completed.status.value == "completed"
    assert adapter.detail_calls >= 3


def test_order_verification_task_retries_transient_network_failure(
    tmp_path: Path,
) -> None:
    db = Database(tmp_path / "phase4-order-retry.db")
    store = _store(db)
    repo = DongxiaoRepository(db.path)
    order = _order(store)

    class RetryAdapter(FakeOrderAdapter):
        def __init__(self) -> None:
            super().__init__(order)
            self.fail_next = 1

        def get_order(self, _store, order_id: str):
            if self.fail_next:
                self.fail_next -= 1
                self.detail_calls += 1
                raise requests.Timeout("temporary timeout")
            return super().get_order(_store, order_id)

    adapter = RetryAdapter()
    order_service = DongxiaoOrderService(repo, adapter)
    initial = order_service.sync_orders(
        store,
        start_time=1_764_999_000,
        end_time=1_765_003_000,
    )
    assert initial.failed == 1
    order_service.sync_orders(
        store,
        start_time=1_764_999_000,
        end_time=1_765_003_000,
    )
    adapter.fail_next = 1
    task, _created = DongxiaoTaskService(
        repo
    ).create_order_verification_task(
        store,
        [order.order_id],
        idempotency_key="retry-order-verification",
        actor_id="operator",
        max_attempts=2,
    )
    scheduler = DongxiaoTaskScheduler(
        repo,
        CompositeTaskExecutor(
            repo,
            order_service=order_service,
            aftersale_service=DongxiaoAftersaleService(repo, adapter),
        ),
        retry_policy=RetryPolicy(
            base_delay_seconds=0,
            max_delay_seconds=0,
        ),
        poll_interval_seconds=0.02,
    )

    assert scheduler.run_until_idle(timeout=5) is True

    saved = repo.get_task(task.task_id, store_app_id=store.app_id)
    assert saved is not None
    assert saved.status.value == "completed"
    item = repo.list_task_execution_items(
        task.task_id,
        store_app_id=store.app_id,
    )[0]
    assert item.attempt_count == 2
