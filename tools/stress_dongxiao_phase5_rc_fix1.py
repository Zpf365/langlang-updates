from __future__ import annotations

import argparse
import gc
import json
import sqlite3
import tempfile
import threading
import time
import tkinter as tk
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psutil
from benchmark_dongxiao_phase4 import _items, _OrderAdapter

from weimi_manager.db import Database
from weimi_manager.dongxiao.application.catalog_service import (
    ProductCatalogService,
)
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
from weimi_manager.dongxiao.infrastructure.catalog import (
    MockProductCatalogAdapter,
)
from weimi_manager.dongxiao.infrastructure.executors import (
    LocalSkuValidationExecutor,
)
from weimi_manager.dongxiao.infrastructure.repository import (
    DongxiaoRepository,
)
from weimi_manager.models import Order, OrderItem


def _orders(store, index: int, count: int = 6) -> list[Order]:
    now = int(time.time())
    return [
        Order(
            store_id=store.id,
            store_name=store.name,
            order_id=f"RC-{store.id}-{index}-{item}",
            status=100,
            status_text="完成",
            create_time=now,
            update_time=now + item,
            pay_time=now,
            total_amount=1000,
            product_amount=1000,
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
                    product_id=f"p-{store.id}-{item}",
                    sku_id=f"s-{store.id}-{item}",
                    title="隔离压力商品",
                    sku_count=1,
                    sale_price=1000,
                )
            ],
            raw={"status": 100},
        )
        for item in range(count)
    ]


def _insert_restart_probe(
    repository: DongxiaoRepository,
    store,
    index: int,
) -> tuple[str, str]:
    now = datetime.now(UTC)
    task = Task(
        task_id=f"restart-probe-task-{index}",
        store_id=store.id,
        store_app_id=store.app_id,
        task_type="aftersale_decision",
        idempotency_key=f"restart-probe-{store.id}-{index}",
        status=TaskStatus.RUNNING,
        total_items=1,
        succeeded_items=0,
        failed_items=0,
        created_at=now,
        updated_at=now,
        lease_owner="force-closed-process",
        lease_expires_at=now + timedelta(minutes=5),
        last_heartbeat_at=now,
        execution_mode="platform_write",
    )
    item = TaskExecutionItem(
        item_id=f"restart-probe-item-{index}",
        task_id=task.task_id,
        store_id=store.id,
        store_app_id=store.app_id,
        subject_type="aftersale",
        subject_id=f"probe-case-{index}",
        status=TaskItemStatus.RUNNING,
        attempt_count=1,
        max_attempts=1,
        created_at=now,
        updated_at=now,
    )
    repository.create_task_with_execution_items(task, [item])
    return task.task_id, item.item_id


def _exception_probe(path: Path) -> bool:
    db = Database(path)
    store_id = db.add_store(
        "压力异常探针店铺",
        "wx-stress-exception-probe",
        "x" * 32,
    )
    store = db.get_store(store_id)
    assert store is not None
    repository = DongxiaoRepository(db.path)
    conn = sqlite3.connect(db.path)
    try:
        conn.executescript(
            """
            CREATE TRIGGER stress_exception_probe
            BEFORE INSERT ON dongxiao_skus
            WHEN NEW.sku_id='stress-boom'
            BEGIN
                SELECT RAISE(ABORT, 'stress injected failure');
            END;
            """
        )
        conn.commit()
    finally:
        conn.close()
    items = _items(store, 2)
    items[1] = replace(
        items[1],
        skus=(replace(items[1].skus[0], sku_id="stress-boom"),),
    )
    caught = False
    try:
        repository.upsert_catalog_items(items)
    except sqlite3.IntegrityError:
        caught = True
    return caught


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration-seconds", type=int, default=1800)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    duration = max(60, int(args.duration_seconds))
    process = psutil.Process()
    process.cpu_percent(interval=None)
    python_threads_before = threading.active_count()
    native_threads_before = process.num_threads()
    rss_samples: list[int] = []
    cpu_samples: list[float] = []
    python_thread_samples: list[int] = []
    native_thread_samples: list[int] = []
    errors: list[str] = []
    task_ids: list[tuple[object, str]] = []
    task_created = 0
    task_completed = 0
    task_failed = 0
    task_failure_samples: list[dict[str, object]] = []
    order_written = 0
    aftersale_written = 0
    catalog_runs = 0
    page_switches = 0
    network_recoveries = 0
    restart_recoveries = 0
    unknown_not_replayed = 0
    recovery_times: list[float] = []
    tk_gap_samples: list[float] = []

    try:
        root = tk.Tk()
        root.withdraw()
    except tk.TclError:
        root = None
    page_frames: list[tk.Frame] = []
    if root is not None:
        for _index in range(10):
            frame = tk.Frame(root)
            frame.pack(fill="both", expand=True)
            page_frames.append(frame)
    last_tick = 0.0

    def ui_tick() -> None:
        nonlocal last_tick
        now = time.perf_counter()
        tk_gap_samples.append((now - last_tick) * 1000)
        last_tick = now
        if root is not None:
            root.after(10, ui_tick)

    def pump_ui(seconds: float) -> None:
        deadline = time.monotonic() + max(0.0, seconds)
        while time.monotonic() < deadline:
            if root is not None:
                root.update()
            remaining = deadline - time.monotonic()
            if remaining > 0:
                time.sleep(min(0.005, remaining))

    with tempfile.TemporaryDirectory() as tmp:
        root_path = Path(tmp)
        db = Database(root_path / "rc-fix1-soak.db")
        repository = DongxiaoRepository(db.path)
        stores = []
        pools = []
        catalogs = []
        for store_index in range(2):
            store_id = db.add_store(
                f"RC Fix1 压力店铺 {store_index + 1}",
                f"wx-rc-fix1-soak-{store_index + 1}",
                "x" * 32,
            )
            store = db.get_store(store_id)
            assert store is not None
            stores.append(store)
            catalog_items = _items(store, 120)
            catalog = ProductCatalogService(
                repository,
                MockProductCatalogAdapter(catalog_items),
            )
            catalog.sync_store(store)
            catalogs.append(catalog)
            pool = ProductPoolService(repository).create_pool(
                store,
                name=f"RC Fix1 压力池 {store_index + 1}",
                idempotency_key=f"rc-fix1-soak-pool-{store_index + 1}",
            )
            ProductPoolService(repository).add_products(
                store,
                pool.pool_id,
                [
                    (item.product.product_id, item.skus[0].sku_id)
                    for item in catalog_items[:80]
                ],
            )
            pools.append(pool)

        task_service = DongxiaoTaskService(repository)
        scheduler = DongxiaoTaskScheduler(
            repository,
            LocalSkuValidationExecutor(repository),
            max_concurrent_tasks=2,
            max_tasks_per_store=1,
            poll_interval_seconds=0.02,
        )
        scheduler.start()
        last_tick = time.perf_counter()
        if root is not None:
            root.after(10, ui_tick)
        started = time.monotonic()
        deadline = started + duration
        cycle = 0
        next_network_at = started + 30
        next_database_at = started + 90
        next_restart_at = started + 300
        try:
            while time.monotonic() < deadline:
                cycle += 1
                iteration_started = time.monotonic()
                if root is not None and page_frames:
                    page_frames[cycle % len(page_frames)].tkraise()
                    root.update()
                    page_switches += 1

                operational_result: dict[str, object] = {}
                operational_due = cycle % 20 == 1

                def write_operational_data() -> None:
                    order_count = 0
                    aftersale_count = 0
                    catalog_count = 0
                    created_tasks: list[tuple[object, str]] = []
                    local_errors: list[str] = []
                    try:
                        for store, pool in zip(
                            stores,
                            pools,
                            strict=True,
                        ):
                            task, _created = (
                                task_service.create_pool_validation_task(
                                    store,
                                    pool.pool_id,
                                    idempotency_key=(
                                        f"rc-fix1-soak-task-"
                                        f"{store.id}-{cycle}"
                                    ),
                                    actor_id="soak",
                                    max_attempts=2,
                                )
                            )
                            created_tasks.append((store, task.task_id))
                        if operational_due:
                            for store in stores:
                                orders = _orders(store, cycle)
                                result = DongxiaoOrderService(
                                    repository,
                                    _OrderAdapter(orders),
                                ).sync_orders(
                                    store,
                                    start_time=int(time.time()) - 3600,
                                    end_time=int(time.time()) + 60,
                                )
                                order_count += result.saved
                                now = datetime.now(UTC)
                                for item in range(6):
                                    repository.upsert_aftersale_case(
                                        AftersaleCase(
                                            case_id=(
                                                f"soak-case-{store.id}-"
                                                f"{cycle}-{item}"
                                            ),
                                            store_id=store.id,
                                            store_app_id=store.app_id,
                                            platform_case_id=(
                                                f"platform-soak-{store.id}-"
                                                f"{cycle}-{item}"
                                            ),
                                            status=(
                                                AftersaleStatus.PENDING_REVIEW
                                            ),
                                            requested_amount_cents=1000,
                                            approved_amount_cents=0,
                                            idempotency_key=(
                                                f"soak-case-{store.id}-"
                                                f"{cycle}-{item}"
                                            ),
                                            created_at=now,
                                            updated_at=now,
                                            source="platform_api",
                                            platform_status=(
                                                "MERCHANT_PROCESSING"
                                            ),
                                            platform_state=(
                                                AftersaleStatus.PENDING_REVIEW
                                            ),
                                            platform_state_updated_at=now,
                                            after_sale_type="REFUND",
                                            paid_amount_cents=2000,
                                            last_synced_at=now,
                                        )
                                    )
                                    aftersale_count += 1
                            for store, catalog in zip(
                                stores,
                                catalogs,
                                strict=True,
                            ):
                                try:
                                    catalog.sync_store(store)
                                    catalog_count += 1
                                except Exception as exc:
                                    local_errors.append(
                                        "catalog: "
                                        f"{type(exc).__name__}: {exc}"
                                    )
                    except Exception as exc:
                        local_errors.append(
                            f"operational: {type(exc).__name__}: {exc}"
                        )
                    operational_result.update({
                        "tasks": created_tasks,
                        "orders": order_count,
                        "aftersales": aftersale_count,
                        "catalog_runs": catalog_count,
                        "errors": local_errors,
                    })

                operational_thread = threading.Thread(
                    target=write_operational_data,
                    name="rc-fix1-soak-data",
                    daemon=True,
                )
                operational_thread.start()
                while operational_thread.is_alive():
                    pump_ui(0.02)
                operational_thread.join()
                current_tasks = list(
                    operational_result.get("tasks") or []
                )
                task_created += len(current_tasks)
                scheduler.enqueue()
                pending_tasks = list(current_tasks)
                wait_deadline = time.monotonic() + 30
                while pending_tasks and time.monotonic() < wait_deadline:
                    remaining_tasks = []
                    for store, task_id in pending_tasks:
                        saved = repository.get_task(
                            task_id,
                            store_app_id=store.app_id,
                        )
                        if saved is None:
                            task_failed += 1
                            continue
                        if saved.status in {
                            TaskStatus.PENDING,
                            TaskStatus.PREPARING,
                            TaskStatus.RUNNING,
                            TaskStatus.PAUSED,
                        }:
                            remaining_tasks.append((store, task_id))
                            continue
                        if saved.status is TaskStatus.COMPLETED:
                            task_completed += 1
                        else:
                            task_failed += 1
                            items = repository.list_task_execution_items(
                                task_id,
                                store_app_id=store.app_id,
                            )
                            task_failure_samples.append({
                                "task_id": task_id,
                                "status": saved.status.value,
                                "reason": saved.status_reason,
                                "last_error": saved.last_error,
                                "item_errors": [
                                    item.last_error_message
                                    for item in items
                                    if item.last_error_message
                                ][:3],
                            })
                    pending_tasks = remaining_tasks
                    if pending_tasks:
                        pump_ui(0.05)
                if pending_tasks:
                    errors.append(f"cycle {cycle}: scheduler timeout")
                    for store, task_id in pending_tasks:
                        task_failed += 1
                    task_ids.extend(pending_tasks)
                order_written += int(operational_result.get("orders") or 0)
                aftersale_written += int(
                    operational_result.get("aftersales") or 0
                )
                catalog_runs += int(
                    operational_result.get("catalog_runs") or 0
                )
                errors.extend(
                    str(item)
                    for item in operational_result.get("errors") or []
                )

                now_monotonic = time.monotonic()
                if now_monotonic >= next_network_at:
                    for store in stores:
                        before = len(
                            repository.list_order_records(
                                store.id,
                                limit=100000,
                            )
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
                                raise ConnectionError(
                                    "simulated intermittent network"
                                )
                        try:
                            DongxiaoOrderService(
                                repository,
                                OfflineAdapter([]),
                            ).sync_orders(
                                store,
                                start_time=int(time.time()) - 60,
                                end_time=int(time.time()),
                            )
                        except ConnectionError:
                            after = len(
                                repository.list_order_records(
                                    store.id,
                                    limit=100000,
                                )
                            )
                            if after >= before:
                                network_recoveries += 1
                    next_network_at = now_monotonic + 30

                if now_monotonic >= next_database_at:
                    try:
                        if not _exception_probe(
                            root_path / f"exception-{cycle}.db"
                        ):
                            errors.append("database exception probe missed")
                    except Exception as exc:
                        errors.append(
                            f"database exception: {type(exc).__name__}: {exc}"
                        )
                    next_database_at = now_monotonic + 120

                if now_monotonic >= next_restart_at:
                    restart_started = time.perf_counter()
                    task_id, item_id = _insert_restart_probe(
                        repository,
                        stores[0],
                        cycle,
                    )
                    repository.recover_interrupted_tasks()
                    task = repository.get_task(
                        task_id,
                        store_app_id=stores[0].app_id,
                    )
                    items = repository.list_task_execution_items(
                        task_id,
                        store_app_id=stores[0].app_id,
                    )
                    runnable_ids = {
                        item.task_id
                        for item in repository.list_runnable_tasks(limit=500)
                    }
                    if (
                        task is not None
                        and task.status is TaskStatus.UNKNOWN
                        and items
                        and items[0].item_id == item_id
                        and items[0].status is TaskItemStatus.UNKNOWN
                        and task_id not in runnable_ids
                    ):
                        restart_recoveries += 1
                        unknown_not_replayed += 1
                    recovery_times.append(
                        round(
                            (time.perf_counter() - restart_started) * 1000,
                            2,
                        )
                    )
                    next_restart_at = now_monotonic + 300

                rss_samples.append(process.memory_info().rss)
                cpu_samples.append(process.cpu_percent(interval=None))
                python_thread_samples.append(threading.active_count())
                native_thread_samples.append(process.num_threads())
                remaining = 0.5 - (time.monotonic() - iteration_started)
                if remaining > 0:
                    pump_ui(remaining)
        finally:
            scheduler.stop()
            if root is not None:
                root.destroy()
        gc.collect()
        database_size = db.path.stat().st_size
        elapsed = time.monotonic() - started

    result = {
        "tested_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "duration_seconds": round(elapsed, 2),
        "isolated_database": True,
        "cycles": cycle,
        "stores": 2,
        "task_created": task_created,
        "task_completed": task_completed,
        "task_failed": task_failed,
        "task_failure_samples": task_failure_samples[:20],
        "task_completion_rate": round(
            task_completed / max(1, task_created),
            6,
        ),
        "orders_written": order_written,
        "aftersales_written": aftersale_written,
        "catalog_sync_runs": catalog_runs,
        "page_switches": page_switches,
        "network_recoveries": network_recoveries,
        "restart_recoveries": restart_recoveries,
        "unknown_not_replayed": unknown_not_replayed,
        "recovery_times_ms": recovery_times,
        "error_count": len(errors),
        "errors": errors[:100],
        "cpu_percent_max": round(max(cpu_samples, default=0.0), 2),
        "rss_start_bytes": rss_samples[0] if rss_samples else 0,
        "rss_peak_bytes": max(rss_samples, default=0),
        "rss_end_bytes": rss_samples[-1] if rss_samples else 0,
        "python_threads_before": python_threads_before,
        "python_threads_peak": max(python_thread_samples, default=0),
        "python_threads_end": threading.active_count(),
        "native_threads_before": native_threads_before,
        "native_threads_peak": max(native_thread_samples, default=0),
        "native_threads_end": process.num_threads(),
        "tk_measured": bool(tk_gap_samples),
        "tk_max_gap_ms": round(max(tk_gap_samples, default=0.0), 2),
        "database_bytes": database_size,
    }
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return (
        0
        if (
            not errors
            and task_failed == 0
            and task_completed == task_created
            and unknown_not_replayed == restart_recoveries
        )
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
