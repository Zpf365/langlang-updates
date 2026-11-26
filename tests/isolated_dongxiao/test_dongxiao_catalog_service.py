from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from weimi_manager.db import Database
from weimi_manager.dongxiao.application.catalog_service import (
    ProductCatalogService,
)
from weimi_manager.dongxiao.domain.catalog import CatalogProductData
from weimi_manager.dongxiao.domain.enums import DataSource, ProductState
from weimi_manager.dongxiao.domain.models import Product, Sku
from weimi_manager.dongxiao.infrastructure.catalog import (
    MockProductCatalogAdapter,
)
from weimi_manager.dongxiao.infrastructure.repository import (
    DongxiaoRepository,
)


def _catalog_items(store, count: int) -> tuple[CatalogProductData, ...]:
    now = datetime(2026, 11, 18, 10, 0, tzinfo=UTC)
    items = []
    for index in range(1, count + 1):
        product_id = str(100000 + index)
        product = Product(
            store_id=store.id,
            store_app_id=store.app_id,
            product_id=product_id,
            title=f"模拟商品 {index}",
            state=ProductState.ACTIVE,
            min_price_cents=1000 + index,
            stock_total=5,
            observed_at=now,
            data_source=DataSource.MOCK,
            from_cache=False,
            last_synced_at=now,
        )
        sku = Sku(
            store_id=store.id,
            store_app_id=store.app_id,
            product_id=product_id,
            sku_id=f"sku-{index}",
            price_cents=1000 + index,
            stock=5,
            available=True,
            observed_at=now,
        )
        items.append(CatalogProductData(product=product, skus=(sku,)))
    return tuple(items)


def test_catalog_sync_saves_partial_results_without_duplicate_products(
    tmp_path: Path,
) -> None:
    db = Database(tmp_path / "sync.db")
    store_id = db.add_store("同步店铺", "wx-catalog-sync", "x" * 32)
    store = db.get_store(store_id)
    assert store is not None
    repo = DongxiaoRepository(db.path)
    adapter = MockProductCatalogAdapter(
        _catalog_items(store, 3),
        fail_product_ids=frozenset({"100002"}),
    )
    service = ProductCatalogService(repo, adapter)

    first = service.sync_store(store)
    assert first.discovered == 3
    assert first.products_saved == 2
    assert first.failed == 1
    assert len(repo.list_products(store.id)) == 2

    second = service.sync_store(store)
    assert second.products_saved == 2
    assert len(repo.list_products(store.id)) == 2
