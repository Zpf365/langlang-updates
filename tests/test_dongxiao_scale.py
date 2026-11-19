from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from weimi_manager.db import Database
from weimi_manager.dongxiao.domain.catalog import CatalogProductData
from weimi_manager.dongxiao.domain.enums import DataSource, ProductState
from weimi_manager.dongxiao.domain.models import Product, Sku
from weimi_manager.dongxiao.domain.queries import ProductFilter
from weimi_manager.dongxiao.infrastructure.repository import (
    DongxiaoRepository,
)


@pytest.mark.parametrize("product_count", [1000, 10000])
def test_catalog_filtering_and_pagination_scale(
    tmp_path: Path,
    product_count: int,
) -> None:
    db = Database(tmp_path / f"scale-{product_count}.db")
    store_id = db.add_store(
        "规模店铺",
        f"wx-scale-{product_count}",
        "x" * 32,
    )
    store = db.get_store(store_id)
    assert store is not None
    repo = DongxiaoRepository(db.path)
    now = datetime(2026, 11, 18, 10, 0, tzinfo=UTC)
    items = []
    for index in range(product_count):
        product_id = f"{1000000 + index}"
        product = Product(
            store_id=store.id,
            store_app_id=store.app_id,
            product_id=product_id,
            title=f"规模商品 {index}",
            state=(
                ProductState.ACTIVE
                if index % 3
                else ProductState.INACTIVE
            ),
            min_price_cents=1000 + index % 100,
            stock_total=10,
            observed_at=now,
            sales_count=index % 5,
            sales_source=DataSource.OFFICIAL_API,
            data_source=DataSource.OFFICIAL_API,
            last_synced_at=now,
        )
        sku = Sku(
            store_id=store.id,
            store_app_id=store.app_id,
            product_id=product_id,
            sku_id=f"sku-{index}",
            price_cents=product.min_price_cents,
            stock=10,
            observed_at=now,
        )
        items.append(CatalogProductData(product=product, skus=(sku,)))

    assert repo.upsert_catalog_items(items) == product_count
    page = repo.query_products(
        store.id,
        ProductFilter(page=2, page_size=500),
    )

    assert page.total == product_count
    assert len(page.items) == min(500, max(0, product_count - 500))
    assert page.page_count == (product_count + 499) // 500


def test_three_hundred_authorized_stores_remain_individually_selectable(
    tmp_path: Path,
) -> None:
    db = Database(tmp_path / "stores-300.db")
    for index in range(300):
        db.add_store(
            f"授权店铺 {index:03d}",
            f"wx-store-scale-{index:03d}",
            "x" * 32,
        )

    stores = db.list_stores()

    assert len(stores) == 300
    assert len({store.id for store in stores}) == 300
    assert len({store.app_id for store in stores}) == 300
    assert {stores[0].name, stores[-1].name} == {
        "授权店铺 000",
        "授权店铺 299",
    }
