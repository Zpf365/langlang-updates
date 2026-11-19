from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from weimi_manager.db import Database
from weimi_manager.dongxiao.application.pool_service import ProductPoolService
from weimi_manager.dongxiao.application.task_scheduler import (
    DongxiaoTaskScheduler,
)
from weimi_manager.dongxiao.application.task_service import DongxiaoTaskService
from weimi_manager.dongxiao.desktop_cart.domain import (
    CartAdapterMode,
    CartBatchStatus,
    CartCapabilityState,
    CartResultStatus,
    CartSkuMode,
    CartSpeedMode,
    OutOfStockPolicy,
    PendingOrderStatus,
)
from weimi_manager.dongxiao.desktop_cart.executor import DesktopCartExecutor
from weimi_manager.dongxiao.desktop_cart.repository import (
    DesktopCartRepository,
)
from weimi_manager.dongxiao.desktop_cart.service import DesktopCartService
from weimi_manager.dongxiao.domain.enums import DataSource, ProductState
from weimi_manager.dongxiao.domain.models import Product, Sku
from weimi_manager.dongxiao.infrastructure.repository import DongxiaoRepository


def _store(db: Database, suffix: str = "desktop"):
    store_id = db.add_store(
        f"桌面加购店铺-{suffix}",
        f"wx-desktop-{suffix}",
        "x" * 32,
    )
    store = db.get_store(store_id)
    assert store is not None
    return store


def _pool(
    repository: DongxiaoRepository,
    store,
    *,
    key: str,
    products: int = 2,
) -> str:
    now = datetime(2026, 11, 19, 8, 0, tzinfo=UTC)
    pairs = []
    for index in range(1, products + 1):
        product_id = f"product-{key}-{index}"
        repository.upsert_product(
            Product(
                store_id=store.id,
                store_app_id=store.app_id,
                product_id=product_id,
                title=f"加购商品 {index}",
                state=ProductState.ACTIVE,
                min_price_cents=1000 + index,
                stock_total=20,
                observed_at=now,
                data_source=DataSource.OFFICIAL_API,
                last_synced_at=now,
            )
        )
        repository.upsert_sku(
            Sku(
                store_id=store.id,
                store_app_id=store.app_id,
                product_id=product_id,
                sku_id=f"sku-{key}-{index}",
                specs=(("颜色", f"颜色{index}"),),
                price_cents=1000 + index,
                stock=20,
                purchase_min=1,
                purchase_max=10,
                observed_at=now,
            )
        )
        pairs.append((product_id, f"sku-{key}-{index}"))
    service = ProductPoolService(repository)
    pool = service.create_pool(
        store,
        name=f"加购池-{key}",
        idempotency_key=f"pool-{key}",
    )
    service.add_products(store, pool.pool_id, pairs)
    return pool.pool_id


def _services(tmp_path: Path):
    db = Database(tmp_path / "desktop-cart.db")
    store = _store(db)
    catalog = DongxiaoRepository(db.path)
    cart_repository = DesktopCartRepository(db.path)
    tasks = DongxiaoTaskService(catalog)
    executor = DesktopCartExecutor(
        cart_repository,
        sleeper=lambda _seconds: None,
    )
    scheduler = DongxiaoTaskScheduler(
        catalog,
        executor,
        max_concurrent_tasks=1,
        max_tasks_per_store=1,
        poll_interval_seconds=0.02,
    )
    service = DesktopCartService(
        cart_repository,
        catalog,
        tasks,
        scheduler=scheduler,
        sleeper=lambda _seconds: None,
    )
    return db, store, catalog, cart_repository, tasks, scheduler, service


def test_mock_cart_requires_server_verification_and_builds_pending_draft(
    tmp_path: Path,
) -> None:
    db, store, catalog, cart_repository, _tasks, scheduler, service = _services(
        tmp_path
    )
    pool_id = _pool(catalog, store, key="main")
    account = service.create_account(
        store,
        display_name="测试执行账号",
        adapter_mode=CartAdapterMode.MOCK,
        permission_checker=lambda _permission: True,
        actor_id="tester",
    )
    preview = service.preview_batch(
        store,
        pool_id=pool_id,
        quantity_per_product=2,
        sku_mode=CartSkuMode.FIRST_AVAILABLE,
        specified_sku_id="",
        out_of_stock_policy=OutOfStockPolicy.SKIP,
        products_per_batch=1,
        account_ids=[account.account_id],
        adapter_mode=CartAdapterMode.MOCK,
        permission_checker=lambda _permission: True,
    )
    assert preview.ready_count == 2
    assert preview.capability.state is CartCapabilityState.MOCK_ONLY

    batch, created = service.create_batch(
        store,
        name="测试批量加购",
        idempotency_key="cart-request-1",
        preview=preview,
        quantity_per_product=2,
        products_per_batch=1,
        sku_mode=CartSkuMode.FIRST_AVAILABLE,
        specified_sku_id="",
        out_of_stock_policy=OutOfStockPolicy.SKIP,
        speed_mode=CartSpeedMode.CUSTOM,
        interval_ms=250,
        concurrency=1,
        account_ids=[account.account_id],
        permission_checker=lambda _permission: True,
        actor_id="tester",
    )
    assert created is True
    service.confirm_batch(
        store,
        batch.batch_id,
        permission_checker=lambda _permission: True,
        actor_id="tester",
    )
    assert scheduler.run_until_idle(timeout=10) is True
    assignments = cart_repository.list_assignments(batch.batch_id)
    assert assignments and assignments[0]["task_id"]
    task = catalog.get_task(
        str(assignments[0]["task_id"]),
        store_app_id=store.app_id,
    )
    assert task is not None
    assert task.status.value == "completed", (
        task.status,
        task.status_reason,
        task.last_error,
    )

    synced = service.sync_progress(batch.batch_id)
    assert synced.status is CartBatchStatus.COMPLETED
    results = cart_repository.list_results(
        batch_id=batch.batch_id,
        store_id=store.id,
    )
    assert len(results) == 2
    assert all(result.status is CartResultStatus.SUCCEEDED for result in results)
    assert all(result.verification_token for result in results)
    assert all(result.verification_source is CartAdapterMode.MOCK for result in results)

    drafts = service.create_pending_drafts(
        store,
        batch.batch_id,
        actor_id="tester",
    )
    assert len(drafts) == 1
    assert drafts[0].status is PendingOrderStatus.AWAITING_APPROVAL
    assert drafts[0].item_count == 2
    approved = service.decide_pending_draft(
        store,
        drafts[0].draft_id,
        approve=True,
        actor_id="approver",
        permission_checker=lambda _permission: True,
    )
    assert approved.status is PendingOrderStatus.APPROVED
    with pytest.raises(Exception, match="不支持"):
        service.request_official_order_creation(drafts[0].draft_id)
    scheduler.stop()


def test_official_cart_is_fail_closed_and_store_isolation_is_enforced(
    tmp_path: Path,
) -> None:
    db, store, catalog, cart_repository, _tasks, scheduler, service = _services(
        tmp_path
    )
    pool_id = _pool(catalog, store, key="guard")
    official = service.create_account(
        store,
        display_name="真实账号待授权",
        adapter_mode=CartAdapterMode.OFFICIAL,
        permission_checker=lambda _permission: True,
        actor_id="tester",
    )
    preview = service.preview_batch(
        store,
        pool_id=pool_id,
        quantity_per_product=1,
        sku_mode=CartSkuMode.LOWEST_PRICE,
        specified_sku_id="",
        out_of_stock_policy=OutOfStockPolicy.FAIL,
        products_per_batch=10,
        account_ids=[official.account_id],
        adapter_mode=CartAdapterMode.OFFICIAL,
        permission_checker=lambda _permission: True,
    )
    assert preview.capability.state is CartCapabilityState.OFFICIAL_UNSUPPORTED
    batch, _created = service.create_batch(
        store,
        name="官方能力缺失",
        idempotency_key="official-guard",
        preview=preview,
        quantity_per_product=1,
        products_per_batch=10,
        sku_mode=CartSkuMode.LOWEST_PRICE,
        specified_sku_id="",
        out_of_stock_policy=OutOfStockPolicy.FAIL,
        speed_mode=CartSpeedMode.CONSERVATIVE,
        interval_ms=None,
        concurrency=None,
        account_ids=[official.account_id],
        permission_checker=lambda _permission: True,
        actor_id="tester",
    )
    with pytest.raises(Exception, match="微信小店商家开放接口"):
        service.confirm_batch(
            store,
            batch.batch_id,
            permission_checker=lambda _permission: True,
            actor_id="tester",
        )
    assert cart_repository.list_results(batch_id=batch.batch_id) == []

    other = _store(db, "other")
    with pytest.raises(Exception, match="不属于当前店铺"):
        cart_repository.get_account(
            official.account_id,
            store_id=other.id,
            store_app_id=other.app_id,
        )
    scheduler.stop()


def test_cart_history_remains_readable_after_store_deletion(
    tmp_path: Path,
) -> None:
    db, store, catalog, cart_repository, _tasks, scheduler, service = _services(
        tmp_path
    )
    pool_id = _pool(catalog, store, key="history")
    account = service.create_account(
        store,
        display_name="历史记录账号",
        adapter_mode=CartAdapterMode.MOCK,
        permission_checker=lambda _permission: True,
        actor_id="tester",
    )
    preview = service.preview_batch(
        store,
        pool_id=pool_id,
        quantity_per_product=1,
        sku_mode=CartSkuMode.FIRST_AVAILABLE,
        specified_sku_id="",
        out_of_stock_policy=OutOfStockPolicy.SKIP,
        products_per_batch=10,
        account_ids=[account.account_id],
        adapter_mode=CartAdapterMode.MOCK,
        permission_checker=lambda _permission: True,
    )
    batch, _created = service.create_batch(
        store,
        name="删除店铺历史",
        idempotency_key="history-after-delete",
        preview=preview,
        quantity_per_product=1,
        products_per_batch=10,
        sku_mode=CartSkuMode.FIRST_AVAILABLE,
        specified_sku_id="",
        out_of_stock_policy=OutOfStockPolicy.SKIP,
        speed_mode=CartSpeedMode.CONSERVATIVE,
        interval_ms=None,
        concurrency=None,
        account_ids=[account.account_id],
        permission_checker=lambda _permission: True,
        actor_id="tester",
    )
    service.confirm_batch(
        store,
        batch.batch_id,
        permission_checker=lambda _permission: True,
        actor_id="tester",
    )
    assert scheduler.run_until_idle(timeout=10) is True

    conn = sqlite3.connect(db.path)
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("DELETE FROM stores WHERE id=?", (store.id,))
        conn.commit()
    finally:
        conn.close()

    accounts = cart_repository.list_accounts(include_deleted_store=True)
    batches = cart_repository.list_batches(include_deleted_store=True)
    results = cart_repository.list_results(batch_id=batch.batch_id)
    assert accounts[0].store_id is None
    assert batches[0].store_id is None
    assert len(results) == 2
    assert all(result.store_id is None for result in results)
    assert all(result.store_app_id == store.app_id for result in results)
    scheduler.stop()


def test_multi_account_cart_tasks_respect_configured_concurrency(
    tmp_path: Path,
) -> None:
    db = Database(tmp_path / "desktop-cart-concurrency.db")
    store = _store(db, "concurrency")
    catalog = DongxiaoRepository(db.path)
    pool_id = _pool(catalog, store, key="concurrency")
    cart_repository = DesktopCartRepository(db.path)
    tasks = DongxiaoTaskService(catalog)
    base_executor = DesktopCartExecutor(
        cart_repository,
        sleeper=lambda _seconds: None,
    )

    class SlowExecutor:
        def __init__(self) -> None:
            import threading

            self.lock = threading.Lock()
            self.active = 0
            self.maximum = 0

        def execute(self, item):
            with self.lock:
                self.active += 1
                self.maximum = max(self.maximum, self.active)
            try:
                import time

                time.sleep(0.1)
                return base_executor.execute(item)
            finally:
                with self.lock:
                    self.active -= 1

        def verify(self, item):
            return base_executor.verify(item)

    slow = SlowExecutor()
    scheduler = DongxiaoTaskScheduler(
        catalog,
        slow,
        max_concurrent_tasks=2,
        max_tasks_per_store=1,
        poll_interval_seconds=0.02,
    )
    service = DesktopCartService(
        cart_repository,
        catalog,
        tasks,
        scheduler=scheduler,
        sleeper=lambda _seconds: None,
    )
    accounts = [
        service.create_account(
            store,
            display_name=f"并发账号 {index}",
            adapter_mode=CartAdapterMode.MOCK,
            permission_checker=lambda _permission: True,
            actor_id="tester",
        )
        for index in (1, 2)
    ]
    preview = service.preview_batch(
        store,
        pool_id=pool_id,
        quantity_per_product=1,
        sku_mode=CartSkuMode.FIRST_AVAILABLE,
        specified_sku_id="",
        out_of_stock_policy=OutOfStockPolicy.SKIP,
        products_per_batch=10,
        account_ids=[account.account_id for account in accounts],
        adapter_mode=CartAdapterMode.MOCK,
        permission_checker=lambda _permission: True,
    )
    batch, _created = service.create_batch(
        store,
        name="双账号并发",
        idempotency_key="cart-concurrency-two",
        preview=preview,
        quantity_per_product=1,
        products_per_batch=10,
        sku_mode=CartSkuMode.FIRST_AVAILABLE,
        specified_sku_id="",
        out_of_stock_policy=OutOfStockPolicy.SKIP,
        speed_mode=CartSpeedMode.CUSTOM,
        interval_ms=250,
        concurrency=2,
        account_ids=[account.account_id for account in accounts],
        permission_checker=lambda _permission: True,
        actor_id="tester",
    )
    service.confirm_batch(
        store,
        batch.batch_id,
        permission_checker=lambda _permission: True,
        actor_id="tester",
    )
    assert scheduler.run_until_idle(timeout=15) is True

    assert slow.maximum == 2
    assignments = cart_repository.list_assignments(batch.batch_id)
    assert {row["status"] for row in assignments} == {"pending"}
    service.sync_progress(batch.batch_id)
    assignments = cart_repository.list_assignments(batch.batch_id)
    assert {row["status"] for row in assignments} == {"completed"}
    scheduler.stop()
