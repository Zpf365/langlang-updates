from __future__ import annotations

import threading
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from weimi_manager.db import Database
from weimi_manager.dongxiao.application.pool_service import ProductPoolService
from weimi_manager.dongxiao.application.runtime import DongxiaoRuntime
from weimi_manager.dongxiao.application.task_scheduler import (
    DongxiaoTaskScheduler,
    RetryPolicy,
    TaskExecutionError,
)
from weimi_manager.dongxiao.application.task_service import (
    DongxiaoTaskService,
)
from weimi_manager.dongxiao.domain.enums import (
    DataSource,
    ProductState,
    TaskItemStatus,
    TaskStatus,
)
from weimi_manager.dongxiao.domain.models import (
    Product,
    Sku,
    Task,
    TaskExecutionItem,
)
from weimi_manager.dongxiao.infrastructure.executors import (
    LocalSkuValidationExecutor,
)
from weimi_manager.dongxiao.infrastructure.repository import (
    DongxiaoRepository,
    IdempotencyConflict,
)


def _store(db: Database):
    store_id = db.add_store("任务店铺", "wx-phase4-task", "x" * 32)
    store = db.get_store(store_id)
    assert store is not None
    return store


def _seed_pool(db: Database, store) -> tuple[DongxiaoRepository, str]:
    repo = DongxiaoRepository(db.path)
    now = datetime(2026, 11, 18, 10, 0, tzinfo=UTC)
    for product_index in (1, 2):
        product_id = f"product-{product_index}"
        repo.upsert_product(
            Product(
                store_id=store.id,
                store_app_id=store.app_id,
                product_id=product_id,
                title=f"任务商品 {product_index}",
                state=ProductState.ACTIVE,
                min_price_cents=1000,
                stock_total=10,
                observed_at=now,
                data_source=DataSource.OFFICIAL_API,
                last_synced_at=now,
            )
        )
        for sku_index in (1, 2):
            repo.upsert_sku(
                Sku(
                    store_id=store.id,
                    store_app_id=store.app_id,
                    product_id=product_id,
                    sku_id=f"sku-{product_index}-{sku_index}",
                    price_cents=1000 + sku_index,
                    stock=10,
                    observed_at=now,
                )
            )
    pool = ProductPoolService(repo).create_pool(
        store,
        name="批量任务商品池",
        idempotency_key="phase4-task-pool",
    )
    ProductPoolService(repo).add_products(
        store,
        pool.pool_id,
        [
            ("product-1", "sku-1-1"),
            ("product-2", "sku-2-1"),
        ],
    )
    return repo, pool.pool_id


def test_pool_batch_task_is_persisted_and_idempotent(
    tmp_path: Path,
) -> None:
    db = Database(tmp_path / "phase4-task-create.db")
    store = _store(db)
    repo, pool_id = _seed_pool(db, store)
    service = DongxiaoTaskService(repo)

    first, created = service.create_pool_validation_task(
        store,
        pool_id,
        idempotency_key="pool-validation-1",
        actor_id="operator-1",
        quantity=1,
    )
    repeated, created_again = service.create_pool_validation_task(
        store,
        pool_id,
        idempotency_key="pool-validation-1",
        actor_id="operator-1",
        quantity=1,
    )

    assert created is True
    assert created_again is False
    assert repeated.task_id == first.task_id
    assert first.status is TaskStatus.PENDING
    assert first.total_items == 2
    items = service.list_items(first.task_id, store=store)
    assert [(item.product_id, item.sku_id) for item in items] == [
        ("product-1", "sku-1-1"),
        ("product-2", "sku-2-1"),
    ]

    with pytest.raises(IdempotencyConflict):
        service.create_pool_validation_task(
            store,
            pool_id,
            idempotency_key="pool-validation-1",
            actor_id="operator-1",
            quantity=2,
        )


def test_scheduler_runs_tasks_concurrently_with_per_store_isolation(
    tmp_path: Path,
) -> None:
    db = Database(tmp_path / "phase4-task-scheduler.db")
    first = _store(db)
    second_id = db.add_store(
        "第二任务店铺",
        "wx-phase4-task-two",
        "y" * 32,
    )
    second = db.get_store(second_id)
    assert second is not None
    first_repo, first_pool = _seed_pool(db, first)
    _unused, second_pool = _seed_pool(db, second)
    service = DongxiaoTaskService(first_repo)
    tasks = []
    for store, pool_id in (
        (first, first_pool),
        (first, first_pool),
        (second, second_pool),
        (second, second_pool),
    ):
        task, _created = service.create_pool_validation_task(
            store,
            pool_id,
            idempotency_key=f"scheduled-{len(tasks)}",
            actor_id="operator",
        )
        tasks.append(task)

    class SlowExecutor:
        def __init__(self) -> None:
            self.lock = threading.Lock()
            self.active = 0
            self.max_active = 0

        def execute(self, item):
            with self.lock:
                self.active += 1
                self.max_active = max(self.max_active, self.active)
            try:
                time.sleep(0.05)
                return {"validated": item.subject_id}
            finally:
                with self.lock:
                    self.active -= 1

    executor = SlowExecutor()
    scheduler = DongxiaoTaskScheduler(
        first_repo,
        executor,
        max_concurrent_tasks=2,
        max_tasks_per_store=1,
        poll_interval_seconds=0.02,
    )

    assert scheduler.run_until_idle(timeout=5) is True

    assert executor.max_active == 2
    for task in tasks:
        saved = first_repo.get_task(
            task.task_id,
            store_app_id=task.store_app_id,
        )
        assert saved is not None
        assert saved.status is TaskStatus.COMPLETED
        items = first_repo.list_task_execution_items(
            task.task_id,
            store_app_id=task.store_app_id,
        )
        assert [item.status.value for item in items] == [
            "succeeded",
            "succeeded",
        ]
    stats = first_repo.task_statistics(first.id)
    assert stats.total == 2
    assert stats.completed == 2
    assert stats.execution_items == 4
    assert stats.succeeded_items == 4
    attempts = first_repo.list_task_attempts(
        tasks[0].task_id,
        store_app_id=first.app_id,
    )
    assert len(attempts) == 2


def test_task_pause_resume_keeps_remaining_items_and_completes_once(
    tmp_path: Path,
) -> None:
    db = Database(tmp_path / "phase4-task-pause.db")
    store = _store(db)
    repo, pool_id = _seed_pool(db, store)
    service = DongxiaoTaskService(repo)
    task, _created = service.create_pool_validation_task(
        store,
        pool_id,
        idempotency_key="pause-resume",
        actor_id="operator",
    )
    started = threading.Event()
    release = threading.Event()
    executed: list[str] = []

    class BlockingExecutor:
        def execute(self, item):
            executed.append(item.item_id)
            if len(executed) == 1:
                started.set()
                release.wait(3)
            return {"ok": True}

    scheduler = DongxiaoTaskScheduler(
        repo,
        BlockingExecutor(),
        max_concurrent_tasks=1,
        max_tasks_per_store=1,
        poll_interval_seconds=0.02,
    )
    try:
        scheduler.start()
        assert started.wait(2)
        paused = service.pause(store, task.task_id, actor_id="operator")
        assert paused.status is TaskStatus.PAUSED
        release.set()
        deadline = time.monotonic() + 3
        while scheduler.wait_for_idle(timeout=0.05) is False:
            if time.monotonic() >= deadline:
                break

        resumed = service.resume(store, task.task_id, actor_id="operator")
        assert resumed.status is TaskStatus.PREPARING
        scheduler.enqueue(task.task_id)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            saved = repo.get_task(
                task.task_id,
                store_app_id=store.app_id,
            )
            if saved and saved.status is TaskStatus.COMPLETED:
                break
            time.sleep(0.02)
    finally:
        scheduler.stop()

    saved = repo.get_task(task.task_id, store_app_id=store.app_id)
    assert saved is not None
    assert saved.status is TaskStatus.COMPLETED
    assert len(executed) == 2
    items = service.list_items(task.task_id, store=store)
    assert [item.status.value for item in items] == [
        "succeeded",
        "succeeded",
    ]
    assert [item.attempt_count for item in items] == [1, 1]


def test_transient_retry_is_bounded_and_unknown_result_is_not_retried(
    tmp_path: Path,
) -> None:
    db = Database(tmp_path / "phase4-task-retry.db")
    store = _store(db)
    repo, pool_id = _seed_pool(db, store)
    service = DongxiaoTaskService(repo)
    task, _created = service.create_pool_validation_task(
        store,
        pool_id,
        idempotency_key="retry-once",
        actor_id="operator",
        max_attempts=2,
    )
    calls = {"count": 0}

    class RetryThenSuccess:
        def execute(self, item):
            del item
            calls["count"] += 1
            if calls["count"] == 1:
                raise TaskExecutionError(
                    "temporary",
                    "临时失败",
                    retryable=True,
                )
            return {"ok": True}

    scheduler = DongxiaoTaskScheduler(
        repo,
        RetryThenSuccess(),
        retry_policy=RetryPolicy(base_delay_seconds=0, max_delay_seconds=0),
        poll_interval_seconds=0.02,
    )
    assert scheduler.run_until_idle(timeout=5) is True
    saved = repo.get_task(task.task_id, store_app_id=store.app_id)
    assert saved is not None
    assert saved.status is TaskStatus.COMPLETED
    assert calls["count"] == 3
    attempts = [item.attempt_count for item in service.list_items(task.task_id, store=store)]
    assert attempts == [2, 1]

    unknown_task, _created = service.create_pool_validation_task(
        store,
        pool_id,
        idempotency_key="unknown-result",
        actor_id="operator",
    )

    class UnknownExecutor:
        def __init__(self) -> None:
            self.count = 0

        def execute(self, item):
            del item
            self.count += 1
            raise TaskExecutionError(
                "unknown",
                "平台结果未知",
                unknown=True,
            )

    unknown_executor = UnknownExecutor()
    unknown_scheduler = DongxiaoTaskScheduler(
        repo,
        unknown_executor,
        poll_interval_seconds=0.02,
    )
    assert unknown_scheduler.run_until_idle(timeout=5) is True
    unknown_saved = repo.get_task(
        unknown_task.task_id,
        store_app_id=store.app_id,
    )
    assert unknown_saved is not None
    assert unknown_saved.status is TaskStatus.UNKNOWN
    assert unknown_executor.count == 2
    assert {
        item.status.value
        for item in service.list_items(unknown_task.task_id, store=store)
    } == {"unknown"}
    with pytest.raises(Exception):
        repo.transition_task(
            unknown_task.task_id,
            store_id=store.id,
            store_app_id=store.app_id,
            event="recover_unknown",
        )


def test_cancel_marks_pending_execution_items_without_deleting_task(
    tmp_path: Path,
) -> None:
    db = Database(tmp_path / "phase4-task-cancel.db")
    store = _store(db)
    repo, pool_id = _seed_pool(db, store)
    service = DongxiaoTaskService(repo)
    task, _created = service.create_pool_validation_task(
        store,
        pool_id,
        idempotency_key="cancel-task",
        actor_id="operator",
    )

    cancelled = service.cancel(store, task.task_id, actor_id="operator")

    assert cancelled.status is TaskStatus.CANCELLED
    assert {
        item.status.value
        for item in service.list_items(task.task_id, store=store)
    } == {"cancelled"}


def test_restart_recovers_local_task_but_requires_platform_write_verification(
    tmp_path: Path,
) -> None:
    db = Database(tmp_path / "phase4-task-restart.db")
    store = _store(db)
    repo, pool_id = _seed_pool(db, store)
    service = DongxiaoTaskService(repo)
    local_task, _created = service.create_pool_validation_task(
        store,
        pool_id,
        idempotency_key="restart-local",
        actor_id="operator",
    )
    repo.transition_task(
        local_task.task_id,
        store_id=store.id,
        store_app_id=store.app_id,
        event="start_preparing",
    )
    repo.transition_task(
        local_task.task_id,
        store_id=store.id,
        store_app_id=store.app_id,
        event="start_running",
    )
    claimed = repo.claim_next_execution_item(
        local_task.task_id,
        store_app_id=store.app_id,
        owner="crashed-worker",
    )
    assert claimed is not None

    now = datetime(2026, 11, 18, 10, 0, tzinfo=UTC)
    platform_task = Task(
        task_id="platform-write-crash",
        store_id=store.id,
        store_app_id=store.app_id,
        task_type="platform_write",
        idempotency_key="platform-write-crash",
        status=TaskStatus.RUNNING,
        total_items=1,
        succeeded_items=0,
        failed_items=0,
        created_at=now,
        updated_at=now,
        execution_mode="platform_write",
    )
    repo.create_task(platform_task)
    repo.add_task_execution_items(
        [
            TaskExecutionItem(
                item_id="platform-item",
                task_id=platform_task.task_id,
                store_id=store.id,
                store_app_id=store.app_id,
                subject_type="aftersale",
                subject_id="case-1",
                status=TaskItemStatus.RUNNING,
                attempt_count=1,
                max_attempts=1,
                created_at=now,
                updated_at=now,
            )
        ]
    )

    assert repo.recover_interrupted_tasks() == 2

    local_saved = repo.get_task(
        local_task.task_id,
        store_app_id=store.app_id,
    )
    assert local_saved is not None
    assert local_saved.status is TaskStatus.PREPARING
    local_items = repo.list_task_execution_items(
        local_task.task_id,
        store_app_id=store.app_id,
    )
    assert local_items[0].status is TaskItemStatus.PENDING

    platform_saved = repo.get_task(
        platform_task.task_id,
        store_app_id=store.app_id,
    )
    assert platform_saved is not None
    assert platform_saved.status is TaskStatus.UNKNOWN
    platform_items = repo.list_task_execution_items(
        platform_task.task_id,
        store_app_id=store.app_id,
    )
    assert platform_items[0].status is TaskItemStatus.UNKNOWN


def test_local_sku_task_records_partial_failure_from_current_snapshot(
    tmp_path: Path,
) -> None:
    db = Database(tmp_path / "phase4-task-sku-validation.db")
    store = _store(db)
    repo, pool_id = _seed_pool(db, store)
    now = datetime(2026, 11, 18, 10, 0, tzinfo=UTC)
    repo.upsert_sku(
        Sku(
            store_id=store.id,
            store_app_id=store.app_id,
            product_id="product-1",
            sku_id="sku-1-1",
            price_cents=1001,
            stock=0,
            available=False,
            observed_at=now,
        )
    )
    task, _created = DongxiaoTaskService(
        repo
    ).create_pool_validation_task(
        store,
        pool_id,
        idempotency_key="sku-validation-partial",
        actor_id="operator",
    )
    scheduler = DongxiaoTaskScheduler(
        repo,
        LocalSkuValidationExecutor(repo),
        poll_interval_seconds=0.02,
    )

    assert scheduler.run_until_idle(timeout=5) is True

    saved = repo.get_task(task.task_id, store_app_id=store.app_id)
    assert saved is not None
    assert saved.status is TaskStatus.PARTIALLY_FAILED
    assert saved.succeeded_items == 1
    assert saved.failed_items == 1
    items = repo.list_task_execution_items(
        task.task_id,
        store_app_id=store.app_id,
    )
    assert [item.status.value for item in items] == [
        "failed",
        "succeeded",
    ]
    assert items[0].last_error_code in {"out_of_stock", "unavailable"}


def test_failed_task_manual_retry_is_persisted_and_resumable(
    tmp_path: Path,
) -> None:
    db = Database(tmp_path / "phase4-task-manual-retry.db")
    store = _store(db)
    repo, pool_id = _seed_pool(db, store)
    service = DongxiaoTaskService(repo)
    task, _created = service.create_pool_validation_task(
        store,
        pool_id,
        idempotency_key="manual-retry",
        actor_id="operator",
    )

    class FailFirstExecutor:
        def __init__(self) -> None:
            self.calls = 0

        def execute(self, item):
            del item
            self.calls += 1
            if self.calls <= 2:
                raise TaskExecutionError(
                    "definitive_failure",
                    "首次明确失败",
                )
            return {"ok": True}

    executor = FailFirstExecutor()
    scheduler = DongxiaoTaskScheduler(
        repo,
        executor,
        poll_interval_seconds=0.02,
    )
    assert scheduler.run_until_idle(timeout=5) is True
    failed = repo.get_task(task.task_id, store_app_id=store.app_id)
    assert failed is not None
    assert failed.status is TaskStatus.FAILED

    resumed = service.retry_failed(store, task.task_id, actor_id="operator")
    assert resumed.status is TaskStatus.PREPARING
    assert scheduler.run_until_idle(timeout=5) is True
    completed = repo.get_task(task.task_id, store_app_id=store.app_id)
    assert completed is not None
    assert completed.status is TaskStatus.COMPLETED
    attempts = repo.list_task_attempts(
        task.task_id,
        store_app_id=store.app_id,
    )
    assert len(attempts) == 4


def test_runtime_starts_only_for_recoverable_tasks_and_resumes_after_restart(
    tmp_path: Path,
) -> None:
    db = Database(tmp_path / "phase4-runtime-recovery.db")
    store = _store(db)
    repo, pool_id = _seed_pool(db, store)
    task, _created = DongxiaoTaskService(
        repo
    ).create_pool_validation_task(
        store,
        pool_id,
        idempotency_key="runtime-recovery",
        actor_id="operator",
    )

    runtime = DongxiaoRuntime(db.path, object())
    try:
        assert runtime.start_if_needed() is True
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            saved = runtime.repository.get_task(
                task.task_id,
                store_app_id=store.app_id,
            )
            if saved and saved.status is TaskStatus.COMPLETED:
                break
            time.sleep(0.02)
        saved = runtime.repository.get_task(
            task.task_id,
            store_app_id=store.app_id,
        )
        assert saved is not None
        assert saved.status is TaskStatus.COMPLETED
    finally:
        runtime.stop()

    idle_runtime = DongxiaoRuntime(db.path, object())
    try:
        assert idle_runtime.start_if_needed() is False
    finally:
        idle_runtime.stop()
