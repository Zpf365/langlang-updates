from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import time
import tkinter as tk
from datetime import UTC, datetime
from pathlib import Path

import psutil

from weimi_manager.db import Database
from weimi_manager.dongxiao.application.pool_service import ProductPoolService
from weimi_manager.dongxiao.application.task_scheduler import (
    DongxiaoTaskScheduler,
)
from weimi_manager.dongxiao.application.task_service import DongxiaoTaskService
from weimi_manager.dongxiao.desktop_cart.domain import (
    CartAdapterMode,
    CartSkuMode,
    CartSpeedMode,
    OutOfStockPolicy,
)
from weimi_manager.dongxiao.desktop_cart.executor import DesktopCartExecutor
from weimi_manager.dongxiao.desktop_cart.repository import (
    DesktopCartRepository,
)
from weimi_manager.dongxiao.desktop_cart.service import DesktopCartService
from weimi_manager.dongxiao.domain.catalog import CatalogProductData
from weimi_manager.dongxiao.domain.enums import DataSource, ProductState
from weimi_manager.dongxiao.domain.models import Product, Sku
from weimi_manager.dongxiao.domain.queries import ProductFilter
from weimi_manager.dongxiao.infrastructure.repository import DongxiaoRepository


def _catalog_items(store, count: int) -> list[CatalogProductData]:
    now = datetime.now(UTC)
    items = []
    for index in range(count):
        product_id = f"bench-product-{index:06d}"
        price = 1000 + index % 500
        product = Product(
            store_id=store.id,
            store_app_id=store.app_id,
            product_id=product_id,
            title=f"性能商品 {index:06d}",
            state=ProductState.ACTIVE,
            min_price_cents=price,
            stock_total=100,
            observed_at=now,
            sales_count=index % 50,
            sales_source=DataSource.OFFICIAL_API,
            listing_time=now,
            listing_source=DataSource.OFFICIAL_API,
            data_source=DataSource.OFFICIAL_API,
            last_synced_at=now,
        )
        sku = Sku(
            store_id=store.id,
            store_app_id=store.app_id,
            product_id=product_id,
            sku_id=f"bench-sku-{index:06d}",
            specs=(("规格", f"规格{index % 10}"),),
            price_cents=price,
            stock=100,
            purchase_min=1,
            purchase_max=20,
            observed_at=now,
        )
        items.append(CatalogProductData(product=product, skus=(sku,)))
    return items


def _new_store(database: Database, suffix: str):
    store_id = database.add_store(
        f"最终性能店铺-{suffix}",
        f"wx-final-bench-{suffix}",
        "x" * 32,
    )
    store = database.get_store(store_id)
    assert store is not None
    return store


def _database_stats(path: Path) -> dict[str, object]:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        page_size = int(conn.execute("PRAGMA page_size").fetchone()[0])
        page_count = int(conn.execute("PRAGMA page_count").fetchone()[0])
        freelist_count = int(
            conn.execute("PRAGMA freelist_count").fetchone()[0]
        )
        tables = (
            "dongxiao_products",
            "dongxiao_skus",
            "dongxiao_product_pools",
            "dongxiao_product_pool_items",
            "dongxiao_tasks",
            "dongxiao_task_execution_items",
            "dongxiao_execution_attempts",
            "dongxiao_order_details",
            "dongxiao_aftersale_cases",
            "dongxiao_audit_events",
            "dongxiao_cart_accounts",
            "dongxiao_cart_batches",
            "dongxiao_cart_assignments",
            "dongxiao_cart_results",
            "dongxiao_cart_verifications",
        )
        counts = {
            table: int(
                conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            )
            for table in tables
        }
        integrity = str(conn.execute("PRAGMA integrity_check").fetchone()[0])
        foreign_keys = len(conn.execute("PRAGMA foreign_key_check").fetchall())
    finally:
        conn.close()
    wal = Path(f"{path}-wal")
    shm = Path(f"{path}-shm")
    return {
        "main_bytes": path.stat().st_size if path.exists() else 0,
        "wal_bytes": wal.stat().st_size if wal.exists() else 0,
        "shm_bytes": shm.stat().st_size if shm.exists() else 0,
        "page_size": page_size,
        "page_count": page_count,
        "freelist_count": freelist_count,
        "logical_used_bytes": page_size
        * (page_count - freelist_count),
        "table_rows": counts,
        "integrity_check": integrity,
        "foreign_key_errors": foreign_keys,
    }


def _catalog_write_benchmark(tmp_path: Path, count: int) -> dict[str, object]:
    database = Database(tmp_path / f"catalog-{count}.db")
    store = _new_store(database, f"catalog-{count}")
    repository = DongxiaoRepository(database.path)
    items = _catalog_items(store, count)
    process = psutil.Process()
    rss_before = process.memory_info().rss
    started = time.perf_counter()
    written = repository.upsert_catalog_items(items)
    elapsed = time.perf_counter() - started
    rss_after = process.memory_info().rss
    query_started = time.perf_counter()
    page = repository.query_products(
        store.id,
        ProductFilter(
            keyword="性能商品",
            min_price_cents=1000,
            max_price_cents=1499,
            page=1,
            page_size=100,
        ),
    )
    query_elapsed = time.perf_counter() - query_started
    return {
        "count": count,
        "written": written,
        "write_seconds": round(elapsed, 4),
        "write_rows_per_second": round(count / max(elapsed, 1e-9), 2),
        "rss_delta_bytes": rss_after - rss_before,
        "page_query_ms": round(query_elapsed * 1000, 2),
        "page_first_count": len(page.items),
        "page_total": page.total,
        "database": _database_stats(database.path),
    }


def _cart_execution_benchmark(tmp_path: Path) -> dict[str, object]:
    database = Database(tmp_path / "cart-execute.db")
    store = _new_store(database, "cart-execute")
    catalog = DongxiaoRepository(database.path)
    cart_repository = DesktopCartRepository(database.path)
    items = _catalog_items(store, 1000)
    catalog.upsert_catalog_items(items)
    task_service = DongxiaoTaskService(catalog)
    pool_service = ProductPoolService(catalog)
    pool = pool_service.create_pool(
        store,
        name="1000 商品加购性能池",
        idempotency_key="bench-cart-pool",
    )
    pool_service.add_products(
        store,
        pool.pool_id,
        [
            (item.product.product_id, item.skus[0].sku_id)
            for item in items
        ],
    )
    executor = DesktopCartExecutor(
        cart_repository,
        sleeper=lambda _seconds: None,
    )
    scheduler = DongxiaoTaskScheduler(
        catalog,
        executor,
        max_concurrent_tasks=1,
        max_tasks_per_store=1,
        poll_interval_seconds=0.01,
    )
    service = DesktopCartService(
        cart_repository,
        catalog,
        task_service,
        scheduler=scheduler,
        sleeper=lambda _seconds: None,
    )
    account = service.create_account(
        store,
        display_name="1000 商品性能账号",
        adapter_mode=CartAdapterMode.MOCK,
        permission_checker=lambda _permission: True,
        actor_id="benchmark",
    )
    preview = service.preview_batch(
        store,
        pool_id=pool.pool_id,
        quantity_per_product=1,
        sku_mode=CartSkuMode.FIRST_AVAILABLE,
        specified_sku_id="",
        out_of_stock_policy=OutOfStockPolicy.SKIP,
        products_per_batch=100,
        account_ids=[account.account_id],
        adapter_mode=CartAdapterMode.MOCK,
        permission_checker=lambda _permission: True,
    )
    batch, _created = service.create_batch(
        store,
        name="1000 商品 Mock 加购",
        idempotency_key="bench-cart-batch",
        preview=preview,
        quantity_per_product=1,
        products_per_batch=100,
        sku_mode=CartSkuMode.FIRST_AVAILABLE,
        specified_sku_id="",
        out_of_stock_policy=OutOfStockPolicy.SKIP,
        speed_mode=CartSpeedMode.CUSTOM,
        interval_ms=250,
        concurrency=1,
        account_ids=[account.account_id],
        permission_checker=lambda _permission: True,
        actor_id="benchmark",
    )
    service.confirm_batch(
        store,
        batch.batch_id,
        permission_checker=lambda _permission: True,
        actor_id="benchmark",
    )
    process = psutil.Process()
    rss_before = process.memory_info().rss
    try:
        root = tk.Tk()
        root.withdraw()
    except tk.TclError:
        root = None
    max_gap_ms = 0.0
    last_tick = [time.perf_counter()]
    if root is not None:
        def tick() -> None:
            nonlocal max_gap_ms
            now = time.perf_counter()
            max_gap_ms = max(max_gap_ms, (now - last_tick[0]) * 1000)
            last_tick[0] = now
            root.after(10, tick)

        root.after(10, tick)
    started = time.perf_counter()
    deadline = time.monotonic() + 120
    while True:
        scheduler.run_until_idle(timeout=0.02)
        if root is not None:
            root.update()
        task_rows = catalog.list_tasks(store.id)
        if task_rows and all(
            item.status.value
            in {
                "completed",
                "partially_failed",
                "failed",
                "cancelled",
                "unknown",
            }
            for item in task_rows
        ):
            break
        if time.monotonic() >= deadline:
            raise TimeoutError("1000 商品加购性能测试超时")
        time.sleep(0.01)
    elapsed = time.perf_counter() - started
    if root is not None:
        root.update()
        root.destroy()
    synced = service.sync_progress(batch.batch_id)
    results = cart_repository.list_results(
        batch_id=batch.batch_id,
        limit=10_000,
    )
    rss_after = process.memory_info().rss
    scheduler.stop()
    return {
        "products": 1000,
        "elapsed_seconds": round(elapsed, 4),
        "products_per_second": round(1000 / max(elapsed, 1e-9), 2),
        "batch_status": synced.status.value,
        "result_count": len(results),
        "succeeded": sum(item.status.value == "succeeded" for item in results),
        "unknown": sum(item.status.value == "unknown" for item in results),
        "tk_max_event_gap_ms": round(max_gap_ms, 2),
        "rss_delta_bytes": rss_after - rss_before,
        "database": _database_stats(database.path),
    }


def _task_creation_benchmark(tmp_path: Path, count: int = 10_000) -> dict[str, object]:
    database = Database(tmp_path / "task-create-10000.db")
    store = _new_store(database, "task-create")
    repository = DongxiaoRepository(database.path)
    item = _catalog_items(store, 1)[0]
    repository.upsert_catalog_items([item])
    service = DongxiaoTaskService(repository)
    started = time.perf_counter()
    for index in range(count):
        service.create_desktop_cart_task(
            store,
            batch_id=f"bench-task-batch-{index}",
            account_id="bench-account",
            items=[
                {
                    "product_id": item.product.product_id,
                    "sku_id": item.skus[0].sku_id,
                    "product_title": item.product.title,
                    "unit_price_cents": item.product.min_price_cents,
                    "quantity": 1,
                    "batch_no": 1,
                    "store_name": store.name,
                }
            ],
            idempotency_key=f"bench-task-{index}",
            actor_id="benchmark",
            execution_mode="local_safe",
            speed_mode="conservative",
            interval_ms=2500,
            concurrency=1,
            adapter_mode="mock",
        )
    elapsed = time.perf_counter() - started
    return {
        "tasks": count,
        "elapsed_seconds": round(elapsed, 4),
        "tasks_per_second": round(count / max(elapsed, 1e-9), 2),
        "database": _database_stats(database.path),
    }


def main() -> int:
    output = Path("artifacts/final_release/performance.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    process = psutil.Process()
    result: dict[str, object] = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "environment": {
            "python": os.sys.version,
            "platform": os.name,
            "logical_cpu": psutil.cpu_count(),
            "pid": process.pid,
        },
        "measurements": {},
    }
    with tempfile.TemporaryDirectory() as raw:
        tmp_path = Path(raw)
        measurements = result["measurements"]
        assert isinstance(measurements, dict)
        measurements["catalog_1000"] = _catalog_write_benchmark(
            tmp_path,
            1000,
        )
        measurements["catalog_10000"] = _catalog_write_benchmark(
            tmp_path,
            10_000,
        )
        measurements["cart_execution_1000"] = _cart_execution_benchmark(
            tmp_path
        )
        measurements["task_creation_10000"] = _task_creation_benchmark(
            tmp_path
        )
    result["rss_bytes_end"] = process.memory_info().rss
    output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
