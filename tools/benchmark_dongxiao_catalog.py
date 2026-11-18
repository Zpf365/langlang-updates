from __future__ import annotations

import gc
import json
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

import psutil

from weimi_manager.db import Database
from weimi_manager.dongxiao.domain.catalog import CatalogProductData
from weimi_manager.dongxiao.domain.enums import DataSource, ProductState
from weimi_manager.dongxiao.domain.models import Product, Sku
from weimi_manager.dongxiao.domain.queries import ProductFilter
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
            title=f"性能商品 {index}",
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
            price_cents=product.min_price_cents,
            stock=20,
            observed_at=now,
        )
        result.append(CatalogProductData(product=product, skus=(sku,)))
    return result


def benchmark(count: int) -> dict[str, object]:
    process = psutil.Process()
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
        gc.collect()
        rss_before = process.memory_info().rss
        started = time.perf_counter()
        items = _items(store, count)
        saved = repo.upsert_catalog_items(items)
        page = repo.query_products(
            store.id,
            ProductFilter(page=2, page_size=500),
        )
        untrusted_field_filter = repo.query_products(
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
        elapsed = time.perf_counter() - started
        gc.collect()
        rss_after = process.memory_info().rss
    return {
        "requested_products": count,
        "saved_products": saved,
        "catalog_total": page.total,
        "returned_page_items": len(page.items),
        "untrusted_sales_filtered_total": untrusted_field_filter.total,
        "elapsed_ms": round(elapsed * 1000, 2),
        "rss_delta_bytes": rss_after - rss_before,
        "errors": 0,
    }


def main() -> int:
    print(
        json.dumps(
            {
                "tested_at": datetime.now(UTC).isoformat(timespec="seconds"),
                "database": "temporary",
                "results": [
                    benchmark(1000),
                    benchmark(10000),
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
