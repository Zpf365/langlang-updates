from __future__ import annotations

import argparse
import gc
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psutil
from benchmark_dongxiao_catalog import benchmark_catalog
from benchmark_dongxiao_phase4 import (
    _items,
    _OrderAdapter,
    benchmark_orders,
    benchmark_restart_recovery,
    benchmark_task_catalog,
)
from benchmark_dongxiao_phase4 import (
    benchmark_ui_responsiveness as benchmark_phase4_ui,
)

from weimi_manager.db import Database
from weimi_manager.dongxiao.application.order_service import (
    DongxiaoOrderService,
)
from weimi_manager.dongxiao.application.pool_service import ProductPoolService
from weimi_manager.dongxiao.application.task_scheduler import (
    DongxiaoTaskScheduler,
)
from weimi_manager.dongxiao.application.task_service import (
    DongxiaoTaskService,
)
from weimi_manager.dongxiao.domain.enums import (
    AftersaleStatus,
    TaskItemStatus,
    TaskStatus,
)
from weimi_manager.dongxiao.domain.models import (
    AftersaleCase,
    Task,
    TaskExecutionItem,
)
from weimi_manager.dongxiao.domain.queries import ProductFilter
from weimi_manager.dongxiao.infrastructure.executors import (
    LocalSkuValidationExecutor,
)
from weimi_manager.dongxiao.infrastructure.repository import (
    DongxiaoRepository,
)
from weimi_manager.models import Order, OrderItem


def _elapsed_ms(callable_) -> float:
    started = time.perf_counter()
    callable_()
    return round((time.perf_counter() - started) * 1000, 2)


def benchmark_aftersales(count: int = 1000) -> dict[str, object]:
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "phase5-aftersales.db")
        store_id = db.add_store(
            "Phase5 售后性能店铺",
            "wx-phase5-aftersale-stress",
            "x" * 32,
        )
        store = db.get_store(store_id)
        assert store is not None
        repository = DongxiaoRepository(db.path)
        now = datetime(2026, 11, 18, 10, 0, tzinfo=UTC)
        cases = [
            AftersaleCase(
                case_id=f"phase5-case-{index}",
                store_id=store.id,
                store_app_id=store.app_id,
                platform_case_id=f"platform-case-{index}",
                status=AftersaleStatus.PENDING_REVIEW,
                requested_amount_cents=1000 + index,
                approved_amount_cents=0,
                idempotency_key=f"phase5-case-{index}",
                created_at=now,
                updated_at=now,
                source="platform_api",
                platform_status="MERCHANT_PROCESSING",
                platform_state=AftersaleStatus.PENDING_REVIEW,
                platform_state_updated_at=now,
                after_sale_type="REFUND",
                paid_amount_cents=10000,
                last_synced_at=now,
            )
            for index in range(count)
        ]

        def write_cases() -> None:
            for case in cases:
                repository.upsert_aftersale_case(case)

        elapsed = _elapsed_ms(write_cases)
        saved = len(repository.list_aftersale_cases(store.id, limit=count))
        return {
            "records": count,
            "sync_write_ms": elapsed,
            "saved": saved,
            "database_bytes": db.path.stat().st_size,
            "errors": 0 if saved == count else 1,
        }


def benchmark_multistore_tasks() -> dict[str, object]:
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "phase5-multistore.db")
        repository = DongxiaoRepository(db.path)
        stores = []
        for index in range(2):
            store_id = db.add_store(
                f"并发店铺 {index + 1}",
                f"wx-phase5-concurrent-{index + 1}",
                "x" * 32,
            )
            store = db.get_store(store_id)
            assert store is not None
            stores.append(store)
            items = _items(store, 200)
            repository.upsert_catalog_items(items)
            pool = ProductPoolService(repository).create_pool(
                store,
                name=f"并发池 {index + 1}",
                idempotency_key=f"phase5-concurrent-pool-{index + 1}",
            )
            ProductPoolService(repository).add_products(
                store,
                pool.pool_id,
                [
                    (item.product.product_id, item.skus[0].sku_id)
                    for item in items
                ],
            )
            for task_index in range(2):
                DongxiaoTaskService(
                    repository
                ).create_pool_validation_task(
                    store,
                    pool.pool_id,
                    idempotency_key=(
                        f"phase5-concurrent-task-{index + 1}-{task_index}"
                    ),
                    actor_id="stress",
                )
        scheduler = DongxiaoTaskScheduler(
            repository,
            LocalSkuValidationExecutor(repository),
            max_concurrent_tasks=2,
            max_tasks_per_store=1,
            poll_interval_seconds=0.02,
        )
        outcome: dict[str, bool] = {}
        try:
            elapsed = _elapsed_ms(
                lambda: outcome.setdefault(
                    "completed",
                    scheduler.run_until_idle(timeout=120),
                )
            )
        finally:
            scheduler.stop()
        completed = bool(outcome.get("completed"))
        tasks = [
            task
            for store in stores
            for task in repository.list_tasks(store.id, limit=100)
        ]
        failures = sum(
            task.status is not TaskStatus.COMPLETED for task in tasks
        )
        return {
            "stores": len(stores),
            "tasks": len(tasks),
            "items": 800,
            "execute_ms": elapsed,
            "scheduler_completed": completed,
            "completed_tasks": len(tasks) - failures,
            "failed_tasks": failures,
            "database_bytes": db.path.stat().st_size,
            "errors": 0 if completed and failures == 0 else 1,
        }


def benchmark_network_interruption() -> dict[str, object]:
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "phase5-network.db")
        store_id = db.add_store(
            "断网测试店铺",
            "wx-phase5-network",
            "x" * 32,
        )
        store = db.get_store(store_id)
        assert store is not None
        repository = DongxiaoRepository(db.path)
        order = Order(
            store_id=store.id,
            store_name=store.name,
            order_id="phase5-network-order",
            status=100,
            status_text="完成",
            create_time=1_765_000_000,
            update_time=1_765_003_600,
            pay_time=1_765_000_100,
            total_amount=5000,
            product_amount=5000,
            freight_amount=0,
            discount_amount=0,
            buyer_name="",
            phone="",
            address="",
            waybill_id="",
            carrier="",
            merchant_notes="",
            items=[
                OrderItem(
                    product_id="phase5-product",
                    sku_id="phase5-sku",
                    title="断网保留商品",
                    sku_count=1,
                    sale_price=5000,
                )
            ],
            raw={"status": 100},
        )
        DongxiaoOrderService(
            repository,
            _OrderAdapter([order]),
        ).sync_orders(
            store,
            start_time=1_764_999_000,
            end_time=1_765_999_000,
        )

        class OfflineAdapter(_OrderAdapter):
            def list_order_ids(
                self,
                _store,
                start_time,
                end_time,
                status=None,
            ):
                del start_time, end_time, status
                raise ConnectionError("simulated network interruption")

        started = time.perf_counter()
        errors = 0
        try:
            DongxiaoOrderService(
                repository,
                OfflineAdapter([order]),
            ).sync_orders(
                store,
                start_time=1_764_999_000,
                end_time=1_765_999_000,
            )
        except ConnectionError:
            errors = 1
        elapsed = round((time.perf_counter() - started) * 1000, 2)
        retained = len(repository.list_order_records(store.id, limit=10))
        return {
            "interruption_detected": errors == 1,
            "elapsed_ms": elapsed,
            "retained_orders": retained,
            "errors": 0 if errors == 1 and retained == 1 else 1,
        }


def benchmark_database_exception_injection() -> dict[str, object]:
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "phase5-db-exception.db")
        store_id = db.add_store(
            "数据库异常注入店铺",
            "wx-phase5-db-exception",
            "x" * 32,
        )
        store = db.get_store(store_id)
        assert store is not None
        repository = DongxiaoRepository(db.path)
        connection = sqlite3.connect(db.path)
        try:
            connection.executescript(
                """
                CREATE TRIGGER phase5_exception_probe
                BEFORE INSERT ON dongxiao_skus
                WHEN NEW.sku_id='sku-boom'
                BEGIN
                    SELECT RAISE(ABORT, 'injected database failure');
                END;
                """
            )
            connection.commit()
        finally:
            connection.close()
        items = _items(store, 2)
        items[1] = replace(
            items[1],
            skus=(
                replace(items[1].skus[0], sku_id="sku-boom"),
            ),
        )
        started = time.perf_counter()
        caught = False
        try:
            repository.upsert_catalog_items(items)
        except sqlite3.IntegrityError:
            caught = True
        elapsed = round((time.perf_counter() - started) * 1000, 2)
        products = repository.query_products(
            store.id,
            ProductFilter(page=1, page_size=10),
        ).total
        return {
            "exception_caught": caught,
            "rollback_elapsed_ms": elapsed,
            "partial_products": products,
            "errors": 0 if caught and products == 0 else 1,
        }


def _force_exit_worker(path: Path) -> None:
    db = Database(path)
    store_id = db.add_store(
        "强制退出店铺",
        "wx-phase5-force-exit",
        "x" * 32,
    )
    store = db.get_store(store_id)
    assert store is not None
    repository = DongxiaoRepository(db.path)
    now = datetime.now(UTC)
    task = Task(
        task_id="phase5-force-exit-task",
        store_id=store.id,
        store_app_id=store.app_id,
        task_type="aftersale_decision",
        idempotency_key="phase5-force-exit-task",
        status=TaskStatus.RUNNING,
        total_items=1,
        succeeded_items=0,
        failed_items=0,
        created_at=now,
        updated_at=now,
        lease_owner="force-exit-worker",
        lease_expires_at=now + timedelta(minutes=5),
        last_heartbeat_at=now,
        execution_mode="platform_write",
    )
    repository.create_task_with_execution_items(
        task,
        [
            TaskExecutionItem(
                item_id="phase5-force-exit-item",
                task_id=task.task_id,
                store_id=store.id,
                store_app_id=store.app_id,
                subject_type="aftersale",
                subject_id="phase5-force-case",
                status=TaskItemStatus.RUNNING,
                attempt_count=1,
                max_attempts=1,
                created_at=now,
                updated_at=now,
            )
        ],
    )
    os._exit(23)


def benchmark_forced_exit_restart() -> dict[str, object]:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "phase5-force-exit.db"
        process = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).resolve()),
                "--force-worker",
                str(path),
            ],
            check=False,
            timeout=60,
        )
        repository = DongxiaoRepository(path)
        started = time.perf_counter()
        recovered = repository.recover_interrupted_tasks()
        elapsed = round((time.perf_counter() - started) * 1000, 2)
        task = repository.get_task(
            "phase5-force-exit-task",
            store_app_id="wx-phase5-force-exit",
        )
        items = repository.list_task_execution_items(
            "phase5-force-exit-task",
            store_app_id="wx-phase5-force-exit",
        )
        unknown = (
            task is not None
            and task.status is TaskStatus.UNKNOWN
            and len(items) == 1
            and items[0].status is TaskItemStatus.UNKNOWN
        )
        return {
            "worker_exit_code": process.returncode,
            "recovered_tasks": recovered,
            "recovery_ms": elapsed,
            "task_unknown": unknown,
            "errors": 0 if process.returncode == 23 and unknown else 1,
        }


def benchmark_continuous_runtime(
    duration_seconds: float = 10.0,
) -> dict[str, object]:
    process = psutil.Process()
    process.cpu_percent(interval=None)
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "phase5-soak.db")
        repository = DongxiaoRepository(db.path)
        stores = []
        for store_index in range(2):
            store_id = db.add_store(
                f"持续运行店铺 {store_index + 1}",
                f"wx-phase5-soak-{store_index + 1}",
                "x" * 32,
            )
            store = db.get_store(store_id)
            assert store is not None
            stores.append(store)
            items = _items(store, 500)
            repository.upsert_catalog_items(items)
            pool = ProductPoolService(repository).create_pool(
                store,
                name=f"持续池 {store_index + 1}",
                idempotency_key=f"phase5-soak-pool-{store_index + 1}",
            )
            ProductPoolService(repository).add_products(
                store,
                pool.pool_id,
                [
                    (item.product.product_id, item.skus[0].sku_id)
                    for item in items
                ],
            )
            for task_index in range(2):
                DongxiaoTaskService(
                    repository
                ).create_pool_validation_task(
                    store,
                    pool.pool_id,
                    idempotency_key=(
                        f"phase5-soak-task-{store_index + 1}-{task_index}"
                    ),
                    actor_id="soak",
                )
        scheduler = DongxiaoTaskScheduler(
            repository,
            LocalSkuValidationExecutor(repository),
            max_concurrent_tasks=2,
            max_tasks_per_store=1,
            poll_interval_seconds=0.02,
        )
        gc.collect()
        rss_samples: list[int] = []
        cpu_samples: list[float] = []
        thread_samples: list[int] = []
        scheduler.start()
        deadline = time.monotonic() + max(1.0, duration_seconds)
        try:
            while time.monotonic() < deadline:
                rss_samples.append(process.memory_info().rss)
                cpu_samples.append(process.cpu_percent(interval=None))
                thread_samples.append(process.num_threads())
                time.sleep(0.2)
            completed = scheduler.wait_for_idle(timeout=60)
        finally:
            scheduler.stop()
        tasks = [
            task
            for store in stores
            for task in repository.list_tasks(store.id, limit=100)
        ]
        failures = sum(
            task.status is not TaskStatus.COMPLETED for task in tasks
        )
        return {
            "duration_seconds": duration_seconds,
            "tasks": len(tasks),
            "items": 2000,
            "completed": completed,
            "failed_tasks": failures,
            "cpu_percent_max": round(max(cpu_samples, default=0.0), 2),
            "rss_start_bytes": rss_samples[0] if rss_samples else 0,
            "rss_peak_bytes": max(rss_samples, default=0),
            "rss_end_bytes": rss_samples[-1] if rss_samples else 0,
            "threads_max": max(thread_samples, default=0),
            "threads_end": process.num_threads(),
            "database_bytes": db.path.stat().st_size,
            "errors": 0 if completed and failures == 0 else 1,
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    parser.add_argument("--force-worker", type=Path)
    parser.add_argument("--soak-seconds", type=float, default=10.0)
    args = parser.parse_args()
    if args.force_worker:
        _force_exit_worker(args.force_worker)
        return 0

    process = psutil.Process()
    started_at = datetime.now(UTC)
    threads_before = process.num_threads()
    python_threads_before = threading.active_count()
    result = {
        "tested_at": started_at.isoformat(timespec="seconds"),
        "python": sys.version,
        "platform": sys.platform,
        "cpu_count": os.cpu_count(),
        "database": "temporary isolated SQLite only",
        "catalog": [
            benchmark_catalog(1000),
            benchmark_catalog(10000),
        ],
        "tasks": [
            benchmark_task_catalog(1000),
            benchmark_task_catalog(10000),
        ],
        "orders": benchmark_orders(1000),
        "aftersales": benchmark_aftersales(1000),
        "multistore": benchmark_multistore_tasks(),
        "network_interruption": benchmark_network_interruption(),
        "database_exception": benchmark_database_exception_injection(),
        "forced_exit_restart": benchmark_forced_exit_restart(),
        "ui": benchmark_phase4_ui(),
        "restart_recovery": benchmark_restart_recovery(200),
        "continuous": benchmark_continuous_runtime(args.soak_seconds),
    }
    result["threads_before"] = threads_before
    for _attempt in range(50):
        gc.collect()
        if process.num_threads() <= threads_before + 1:
            break
        time.sleep(0.1)
    result["threads_after"] = process.num_threads()
    result["python_threads_before"] = python_threads_before
    result["python_threads_after"] = threading.active_count()
    result["thread_names_after"] = sorted(
        thread.name for thread in threading.enumerate()
    )
    result["thread_leak_detected"] = (
        result["python_threads_after"] > python_threads_before + 2
        or any(
            thread.name.startswith("dongxiao-task")
            for thread in threading.enumerate()
        )
    )
    result["native_thread_delta"] = result["threads_after"] - threads_before
    result["tested_until"] = datetime.now(UTC).isoformat(timespec="seconds")
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if not result["thread_leak_detected"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
