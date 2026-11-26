from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from weimi_manager.db import Database
from weimi_manager.dongxiao.application.pool_service import ProductPoolService
from weimi_manager.dongxiao.domain.enums import (
    DataSource,
    ProductPoolStatus,
    ProductState,
)
from weimi_manager.dongxiao.domain.models import Product, Sku
from weimi_manager.dongxiao.domain.rules import StoreIsolationError
from weimi_manager.dongxiao.infrastructure.repository import (
    DongxiaoRepository,
)


def _seed_products(db: Database, store) -> DongxiaoRepository:
    repo = DongxiaoRepository(db.path)
    now = datetime(2026, 11, 18, 10, 0, tzinfo=UTC)
    for index in (1, 2):
        product_id = f"product-{index}"
        repo.upsert_product(
            Product(
                store_id=store.id,
                store_app_id=store.app_id,
                product_id=product_id,
                title=f"商品 {index}",
                state=ProductState.ACTIVE,
                min_price_cents=1000,
                stock_total=5,
                observed_at=now,
                data_source=DataSource.OFFICIAL_API,
                last_synced_at=now,
            )
        )
        for sku_index in range(1, index + 2):
            repo.upsert_sku(
                Sku(
                    store_id=store.id,
                    store_app_id=store.app_id,
                    product_id=product_id,
                    sku_id=f"sku-{index}-{sku_index}",
                    price_cents=1000 + sku_index,
                    stock=5,
                    available=True,
                    observed_at=now,
                )
            )
    return repo


def test_pool_add_deduplicate_copy_rename_sort_remove_and_stats(
    tmp_path: Path,
) -> None:
    db = Database(tmp_path / "pool.db")
    store_id = db.add_store("商品池店铺", "wx-pool-store", "x" * 32)
    store = db.get_store(store_id)
    assert store is not None
    repo = _seed_products(db, store)
    service = ProductPoolService(repo)

    pool = service.create_pool(
        store,
        name="主商品池",
        group_name="测试分组",
        idempotency_key="pool-create-1",
    )
    assert pool.status is ProductPoolStatus.ACTIVE
    first_add = service.add_products(
        store,
        pool.pool_id,
        [
            ("product-1", ""),
            ("product-1", ""),
            ("product-1", "sku-1-1"),
            ("product-1", "sku-1-2"),
            ("product-2", "sku-2-1"),
        ],
    )
    assert first_add == {"added": 4, "skipped": 1}
    second_add = service.add_products(
        store,
        pool.pool_id,
        [("product-1", "sku-1-1"), ("product-1", "sku-1-1")],
    )
    assert second_add == {"added": 0, "skipped": 2}

    stats = service.stats(store, pool.pool_id)
    assert stats["item_count"] == 4
    assert stats["product_count"] == 2
    assert stats["product_level_count"] == 1
    assert stats["sku_level_count"] == 3

    items = service.details(store, pool.pool_id)
    service.reorder(
        store,
        pool.pool_id,
        [row["item_id"] for row in reversed(items)],
    )
    reordered = service.details(store, pool.pool_id)
    assert [row["item_id"] for row in reordered] == [
        row["item_id"] for row in reversed(items)
    ]

    renamed = service.rename(
        store,
        pool.pool_id,
        name="重命名商品池",
        group_name="新分组",
    )
    assert renamed.name == "重命名商品池"
    assert renamed.group_name == "新分组"

    copied = service.copy(
        store,
        pool.pool_id,
        name="复制商品池",
        group_name="复制分组",
    )
    assert copied.copied_from_pool_id == pool.pool_id
    copied_items = service.details(store, copied.pool_id)
    assert len(copied_items) == 4

    service.remove(store, copied.pool_id, copied_items[0]["item_id"])
    assert service.stats(store, copied.pool_id)["item_count"] == 3
    assert service.stats(store, pool.pool_id)["item_count"] == 4

    history = service.history(store, pool.pool_id)
    assert {row["action"] for row in history} >= {
        "pool_created",
        "item_added",
        "items_reordered",
        "pool_renamed",
    }
    exported = service.export_csv(store, copied.pool_id)
    assert exported.startswith("product_id,sku_id,position")
    assert len(exported.splitlines()) == 4


def test_pool_isolation_rejects_cross_store_access(tmp_path: Path) -> None:
    db = Database(tmp_path / "pool-isolation.db")
    first_id = db.add_store("店铺一", "wx-pool-one", "x" * 32)
    second_id = db.add_store("店铺二", "wx-pool-two", "y" * 32)
    first = db.get_store(first_id)
    second = db.get_store(second_id)
    assert first is not None and second is not None
    repo = _seed_products(db, first)
    service = ProductPoolService(repo)
    pool = service.create_pool(
        first,
        name="隔离商品池",
        idempotency_key="pool-isolation",
    )

    with pytest.raises(StoreIsolationError):
        service.details(second, pool.pool_id)
