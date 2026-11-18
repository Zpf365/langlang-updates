from __future__ import annotations

import gc
import json
import tempfile
import threading
import time
import tkinter as tk
import tracemalloc
from datetime import UTC, datetime
from pathlib import Path

import psutil

from weimi_manager.db import Database
from weimi_manager.dongxiao.application.aftersale_service import (
    DongxiaoAftersaleService,
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
from weimi_manager.dongxiao.domain.catalog import CatalogProductData
from weimi_manager.dongxiao.domain.enums import (
    AftersaleStatus,
    DataSource,
    ProductState,
)
from weimi_manager.dongxiao.domain.models import (
    AftersaleCase,
    Product,
    Sku,
)
from weimi_manager.dongxiao.infrastructure.executors import (
    LocalSkuValidationExecutor,
)
from weimi_manager.dongxiao.infrastructure.repository import (
    DongxiaoRepository,
)
from weimi_manager.models import Order, OrderItem


def _items(store, count: int) -> list[CatalogProductData]:
    now = datetime(2026, 11, 18, 10, 0, tzinfo=UTC)
    result = []
    for index in range(count):
        product_id = str(1000000 + index)
        product = Product(
            store_id=store.id,
            store_app_id=store.app_id,
            product_id=product_id,
            title=f"第4阶段性能商品 {index}",
            state=ProductState.ACTIVE,
            min_price_cents=1000 + index % 100,
            stock_total=20,
            observed_at=now,
            data_source=DataSource.OFFICIAL_API,
            last_synced_at=now,
        )
        sku = Sku(
            store_id=store.id,
            store_app_id=store.app_id,
            product_id=product_id,
            sku_id=f"sku-{index}",
            price_cents=product.min_price_cents,
            stock=20,
            observed_at=now,
        )
        result.append(CatalogProductData(product=product, skus=(sku,)))
    return result


def _elapsed_ms(callable_) -> float:
    started = time.perf_counter()
    result = callable_()
    elapsed = (time.perf_counter() - started) * 1000
    return round(elapsed, 2), result


def benchmark_task_catalog(count: int) -> dict[str, object]:
    process = psutil.Process()
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / f"phase4-task-{count}.db")
        store_id = db.add_store(
            f"任务性能店铺 {count}",
            f"wx-phase4-benchmark-{count}",
            "x" * 32,
        )
        store = db.get_store(store_id)
        assert store is not None
        repo = DongxiaoRepository(db.path)
        items = _items(store, count)
        repo.upsert_catalog_items(items)
        pool = ProductPoolService(repo).create_pool(
            store,
            name=f"性能池 {count}",
            idempotency_key=f"performance-pool-{count}",
        )
        product_pool = ProductPoolService(repo)
        product_pool.add_products(
            store,
            pool.pool_id,
            [
                (item.product.product_id, item.skus[0].sku_id)
                for item in items
            ],
        )
        service = DongxiaoTaskService(repo)
        gc.collect()
        rss_before = process.memory_info().rss
        peak_rss = rss_before
        stop = threading.Event()

        def sample() -> None:
            nonlocal peak_rss
            while not stop.wait(0.005):
                peak_rss = max(peak_rss, process.memory_info().rss)

        sampler = threading.Thread(target=sample, daemon=True)
        sampler.start()
        tracemalloc.start()
        try:
            create_ms, (task, _created) = _elapsed_ms(
                lambda: service.create_pool_validation_task(
                    store,
                    pool.pool_id,
                    idempotency_key=f"performance-task-{count}",
                    actor_id="benchmark",
                )
            )
            _, python_peak = tracemalloc.get_traced_memory()
        finally:
            stop.set()
            sampler.join(timeout=2)
            tracemalloc.stop()
        rss_after = process.memory_info().rss
        execute_ms = None
        executed_items = 0
        if count == 1000:
            scheduler = DongxiaoTaskScheduler(
                repo,
                LocalSkuValidationExecutor(repo),
                poll_interval_seconds=0.02,
            )
            execute_ms, completed = _elapsed_ms(
                lambda: scheduler.run_until_idle(timeout=60)
            )
            task = repo.get_task(task.task_id, store_app_id=store.app_id)
            executed_items = sum(
                item.status.value == "succeeded"
                for item in service.list_items(task.task_id, store=store)
            )
            assert completed is True
        return {
            "products_and_items": count,
            "batch_task_create_ms": create_ms,
            "task_execute_1000_items_ms": execute_ms,
            "executed_items": executed_items,
            "peak_python_bytes": python_peak,
            "peak_rss_delta_bytes": max(0, peak_rss - rss_before),
            "rss_after_minus_before_bytes": rss_after - rss_before,
            "errors": 0,
        }


class _OrderAdapter:
    def __init__(self, orders: list[Order]) -> None:
        self.orders = {order.order_id: order for order in orders}

    def list_order_ids(self, _store, start_time, end_time, status=None):
        del start_time, end_time, status
        return list(self.orders)

    def get_order(self, _store, order_id):
        return self.orders[order_id]


def benchmark_orders(count: int = 1000) -> dict[str, object]:
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "phase4-orders.db")
        store_id = db.add_store(
            "订单性能店铺",
            "wx-phase4-order-benchmark",
            "x" * 32,
        )
        store = db.get_store(store_id)
        assert store is not None
        orders = [
            Order(
                store_id=store.id,
                store_name=store.name,
                order_id=str(3700000000000000000 + index),
                status=100,
                status_text="完成",
                create_time=1_765_000_000 + index,
                update_time=1_765_003_600 + index,
                pay_time=1_765_000_100 + index,
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
                        product_id=f"product-{index}",
                        sku_id=f"sku-{index}",
                        title=f"订单性能商品 {index}",
                        sku_count=1,
                        sale_price=5000,
                    )
                ],
                raw={"status": 100},
            )
            for index in range(count)
        ]
        repo = DongxiaoRepository(db.path)
        service = DongxiaoOrderService(repo, _OrderAdapter(orders))
        sync_ms, result = _elapsed_ms(
            lambda: service.sync_orders(
                store,
                start_time=1_764_999_000,
                end_time=1_765_999_000,
            )
        )
        list_ms, records = _elapsed_ms(
            lambda: service.list_orders(store, limit=500)
        )
        return {
            "orders": count,
            "order_sync_ms": sync_ms,
            "saved": result.saved,
            "order_page_query_500_ms": list_ms,
            "returned": len(records),
            "errors": result.failed,
        }


def benchmark_aftersale_rules() -> dict[str, object]:
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "phase4-aftersales.db")
        store_id = db.add_store(
            "售后性能店铺",
            "wx-phase4-aftersale-benchmark",
            "x" * 32,
        )
        store = db.get_store(store_id)
        assert store is not None
        repo = DongxiaoRepository(db.path)
        now = datetime.now(UTC)
        case = repo.upsert_aftersale_case(
            AftersaleCase(
                case_id="benchmark-case",
                store_id=store.id,
                store_app_id=store.app_id,
                platform_case_id="benchmark-platform-case",
                status=AftersaleStatus.PENDING_REVIEW,
                requested_amount_cents=5000,
                approved_amount_cents=0,
                paid_amount_cents=10000,
                idempotency_key="benchmark-case",
                created_at=now,
                updated_at=now,
                source="platform_api",
            )
        )
        service = DongxiaoAftersaleService(repo, object())
        started = time.perf_counter()
        for _index in range(10000):
            service.preview_amount(case, approved_amount_cents=5000)
        elapsed = (time.perf_counter() - started) * 1000
        return {
            "calls": 10000,
            "amount_preview_total_ms": round(elapsed, 2),
            "amount_preview_per_call_us": round(elapsed * 1000 / 10000, 2),
            "errors": 0,
        }


def benchmark_ui_responsiveness() -> dict[str, object]:
    try:
        root = tk.Tk()
        root.withdraw()
    except tk.TclError as exc:
        return {"measured": False, "reason": str(exc), "errors": 1}
    try:
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "phase4-ui.db")
            store_id = db.add_store(
                "UI 任务性能店铺",
                "wx-phase4-ui-benchmark",
                "x" * 32,
            )
            store = db.get_store(store_id)
            assert store is not None
            repo = DongxiaoRepository(db.path)
            items = _items(store, 500)
            repo.upsert_catalog_items(items)
            pool = ProductPoolService(repo).create_pool(
                store,
                name="UI 性能池",
                idempotency_key="ui-performance-pool",
            )
            ProductPoolService(repo).add_products(
                store,
                pool.pool_id,
                [
                    (item.product.product_id, item.skus[0].sku_id)
                    for item in items
                ],
            )
            task, _created = DongxiaoTaskService(
                repo
            ).create_pool_validation_task(
                store,
                pool.pool_id,
                idempotency_key="ui-performance-task",
                actor_id="benchmark",
            )
            scheduler = DongxiaoTaskScheduler(
                repo,
                LocalSkuValidationExecutor(repo),
                poll_interval_seconds=0.02,
            )
            ticks: list[float] = []
            result: dict[str, object] = {}
            started = time.perf_counter()

            def worker() -> None:
                result["ok"] = scheduler.run_until_idle(timeout=60)
                result["elapsed_ms"] = (
                    time.perf_counter() - started
                ) * 1000

            def tick() -> None:
                ticks.append(time.perf_counter())
                if "ok" not in result and time.perf_counter() - started < 60:
                    root.after(10, tick)
                else:
                    root.after(50, root.destroy)

            thread = threading.Thread(target=worker, daemon=True)
            root.after(10, tick)
            thread.start()
            root.mainloop()
            thread.join(timeout=5)
        gaps = [
            (right - left) * 1000
            for left, right in zip(ticks, ticks[1:])
        ]
        return {
            "measured": True,
            "task_items": 500,
            "background_execute_ms": round(float(result["elapsed_ms"]), 2),
            "event_loop_ticks": len(ticks),
            "max_gap_ms": round(max(gaps, default=0), 2),
            "errors": 0 if result.get("ok") else 1,
            "task_id": task.task_id,
        }
    finally:
        try:
            root.destroy()
        except tk.TclError:
            pass


def main() -> int:
    print(
        json.dumps(
            {
                "tested_at": datetime.now(UTC).isoformat(timespec="seconds"),
                "database": "temporary isolated SQLite",
                "tasks": [
                    benchmark_task_catalog(1000),
                    benchmark_task_catalog(10000),
                ],
                "orders": benchmark_orders(),
                "aftersales": benchmark_aftersale_rules(),
                "ui": benchmark_ui_responsiveness(),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
