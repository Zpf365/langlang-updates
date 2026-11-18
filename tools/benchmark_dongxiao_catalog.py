from __future__ import annotations

import gc
import json
import statistics
import tempfile
import threading
import time
import tkinter as tk
import tracemalloc
from datetime import UTC, datetime
from pathlib import Path

import psutil

from weimi_manager.db import Database
from weimi_manager.dongxiao.application.catalog_service import (
    ProductCatalogService,
)
from weimi_manager.dongxiao.application.pool_service import ProductPoolService
from weimi_manager.dongxiao.domain.catalog import CatalogProductData
from weimi_manager.dongxiao.domain.enums import (
    DataSource,
    ProductState,
)
from weimi_manager.dongxiao.domain.models import Product, Sku
from weimi_manager.dongxiao.domain.queries import ProductFilter
from weimi_manager.dongxiao.domain.rules import select_sku
from weimi_manager.dongxiao.infrastructure.catalog import (
    MockProductCatalogAdapter,
)
from weimi_manager.dongxiao.infrastructure.repository import (
    DongxiaoRepository,
)


def _items(store, count: int) -> list[CatalogProductData]:
    now = datetime(2026, 11, 18, 10, 0, tzinfo=UTC)
    result = []
    for index in range(count):
        product_id = str(100000 + index)
        product = Product(
            store_id=store.id,
            store_app_id=store.app_id,
            product_id=product_id,
            title=f"性能商品 {index} 中文标题",
            state=(
                ProductState.ACTIVE
                if index % 4
                else ProductState.INACTIVE
            ),
            min_price_cents=1000 + index % 200,
            stock_total=20,
            observed_at=now,
            sales_count=index % 7,
            sales_source=DataSource.MOCK,
            data_source=DataSource.MOCK,
            last_synced_at=now,
        )
        sku = Sku(
            store_id=store.id,
            store_app_id=store.app_id,
            product_id=product_id,
            sku_id=f"sku-{index}",
            specs=(("颜色", "黑色"), ("规格", str(index % 10))),
            price_cents=product.min_price_cents,
            stock=20,
            observed_at=now,
        )
        result.append(CatalogProductData(product=product, skus=(sku,)))
    return result


def _elapsed_ms(callable_) -> float:
    started = time.perf_counter()
    callable_()
    return round((time.perf_counter() - started) * 1000, 2)


def benchmark_catalog(count: int) -> dict[str, object]:
    process = psutil.Process()
    errors = 0
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / f"benchmark-{count}.db")
        store_id = db.add_store(
            f"性能店铺 {count}",
            f"wx-benchmark-{count}",
            "x" * 32,
        )
        store = db.get_store(store_id)
        if store is None:
            raise RuntimeError("benchmark store creation failed")
        repo = DongxiaoRepository(db.path)
        pool_service = ProductPoolService(repo)

        gc.collect()
        rss_before = process.memory_info().rss
        peak_rss = rss_before
        stop_sampling = threading.Event()

        def sample_memory() -> None:
            nonlocal peak_rss
            while not stop_sampling.wait(0.005):
                peak_rss = max(peak_rss, process.memory_info().rss)

        sampler = threading.Thread(
            target=sample_memory,
            name="benchmark-memory",
            daemon=True,
        )
        sampler.start()
        tracemalloc.start()
        try:
            items = _items(store, count)
            write_ms = _elapsed_ms(
                lambda: repo.upsert_catalog_items(
                    items,
                    return_reconciled=True,
                )
            )
            pagination_ms = _elapsed_ms(
                lambda: repo.query_products(
                    store.id,
                    ProductFilter(page=2, page_size=500),
                )
            )
            filter_ms = _elapsed_ms(
                lambda: repo.query_products(
                    store.id,
                    ProductFilter(
                        states=(ProductState.ACTIVE,),
                        sales_min=2,
                        sales_max=5,
                        min_price_cents=1050,
                        max_price_cents=1150,
                        page=2,
                        page_size=500,
                    ),
                )
            )
            first_product = items[0].product.product_id
            candidates = repo.list_skus(store.id, first_product)
            started = time.perf_counter()
            for _index in range(10000):
                selection = select_sku(
                    store_id=store.id,
                    product_id=first_product,
                    candidates=candidates,
                    quantity=1,
                    mode="lowest_price",
                )
                if selection.selected is None:
                    errors += 1
            sku_rule_total_ms = (
                time.perf_counter() - started
            ) * 1000

            pool = pool_service.create_pool(
                store,
                name=f"性能商品池 {count}",
                idempotency_key=f"benchmark-pool-{count}",
            )
            pool_service.add_products(
                store,
                pool.pool_id,
                [
                    (item.product.product_id, item.skus[0].sku_id)
                    for item in items
                ],
            )
            csv_ms = _elapsed_ms(
                lambda: pool_service.export_csv(store, pool.pool_id)
            )
            _, python_peak = tracemalloc.get_traced_memory()
        finally:
            stop_sampling.set()
            sampler.join(timeout=2)
            tracemalloc.stop()
        gc.collect()
        rss_after = process.memory_info().rss

    return {
        "requested_products": count,
        "write_ms": write_ms,
        "pagination_query_ms": pagination_ms,
        "multi_condition_filter_ms": filter_ms,
        "sku_rule_10000_calls_ms": round(sku_rule_total_ms, 2),
        "sku_rule_per_call_us": round(
            sku_rule_total_ms * 1000 / 10000,
            2,
        ),
        "csv_export_ms": csv_ms,
        "peak_python_bytes": python_peak,
        "peak_rss_delta_bytes": max(0, peak_rss - rss_before),
        "rss_after_minus_before_bytes": rss_after - rss_before,
        "errors": errors,
    }


def benchmark_ui_responsiveness(product_count: int = 2000) -> dict[str, object]:
    try:
        root = tk.Tk()
        root.withdraw()
    except tk.TclError as exc:
        return {
            "measured": False,
            "reason": f"Tk unavailable: {exc}",
            "errors": 1,
        }
    try:
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "benchmark-ui.db")
            store_id = db.add_store(
                "UI 响应性能店铺",
                "wx-benchmark-ui",
                "x" * 32,
            )
            store = db.get_store(store_id)
            if store is None:
                raise RuntimeError("benchmark store creation failed")
            repo = DongxiaoRepository(db.path)
            service = ProductCatalogService(
                repo,
                MockProductCatalogAdapter(
                    _items(store, product_count)
                ),
            )
            ticks: list[float] = []
            started = time.perf_counter()
            result: dict[str, object] = {}

            def background() -> None:
                result["sync"] = service.sync_store(store)
                result["elapsed_ms"] = (
                    time.perf_counter() - started
                ) * 1000

            def tick() -> None:
                now = time.perf_counter()
                ticks.append(now)
                if "sync" not in result and now - started < 30:
                    root.after(10, tick)
                else:
                    root.after(50, root.destroy)

            worker = threading.Thread(
                target=background,
                name="benchmark-ui-sync",
                daemon=True,
            )
            root.after(10, tick)
            worker.start()
            root.mainloop()
            worker.join(timeout=5)

        gaps_ms = [
            (right - left) * 1000
            for left, right in zip(ticks, ticks[1:])
        ]
        return {
            "measured": True,
            "products": product_count,
            "sync_ms": round(float(result["elapsed_ms"]), 2),
            "event_loop_ticks": len(ticks),
            "max_gap_ms": round(max(gaps_ms, default=0), 2),
            "p95_gap_ms": round(
                statistics.quantiles(gaps_ms, n=20)[-1]
                if len(gaps_ms) >= 20
                else max(gaps_ms, default=0),
                2,
            ),
            "errors": 0 if result.get("sync") else 1,
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
                "catalog_results": [
                    benchmark_catalog(1000),
                    benchmark_catalog(10000),
                ],
                "ui_responsiveness": benchmark_ui_responsiveness(),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
