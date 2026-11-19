from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from weimi_manager.db import Database
from weimi_manager.dongxiao.domain.enums import (
    DataSource,
    ProductState,
)
from weimi_manager.dongxiao.domain.models import Product
from weimi_manager.dongxiao.domain.queries import ProductFilter
from weimi_manager.dongxiao.infrastructure.repository import (
    DongxiaoRepository,
)


def test_product_filter_uses_trusted_fields_and_database_pagination(
    tmp_path: Path,
) -> None:
    db = Database(tmp_path / "filter.db")
    store_id = db.add_store("筛选店铺", "wx-filter-store", "x" * 32)
    other_store_id = db.add_store(
        "其他店铺",
        "wx-other-filter-store",
        "y" * 32,
    )
    no_sales_store_id = db.add_store(
        "无销量来源店铺",
        "wx-no-sales-source",
        "z" * 32,
    )
    empty_store_id = db.add_store(
        "空商品店铺",
        "wx-empty-products",
        "e" * 32,
    )
    store = db.get_store(store_id)
    other = db.get_store(other_store_id)
    no_sales_store = db.get_store(no_sales_store_id)
    empty_store = db.get_store(empty_store_id)
    assert (
        store is not None
        and other is not None
        and no_sales_store is not None
        and empty_store is not None
    )
    repo = DongxiaoRepository(db.path)
    now = datetime(2026, 11, 18, 10, 0, tzinfo=UTC)

    products = (
        Product(
            store_id=store.id,
            store_app_id=store.app_id,
            product_id="100001",
            title="零销量黑色外套",
            state=ProductState.ACTIVE,
            min_price_cents=1000,
            stock_total=3,
            observed_at=now,
            sales_count=0,
            sales_source=DataSource.OFFICIAL_API,
            listing_time=now,
            listing_source=DataSource.OFFICIAL_API,
            data_source=DataSource.OFFICIAL_API,
            last_synced_at=now,
        ),
        Product(
            store_id=store.id,
            store_app_id=store.app_id,
            product_id="100002",
            title="有销量黑色外套",
            state=ProductState.ACTIVE,
            min_price_cents=2000,
            stock_total=3,
            observed_at=now,
            sales_count=5,
            sales_source=DataSource.OFFICIAL_API,
            listing_time=now,
            listing_source=DataSource.OFFICIAL_API,
            data_source=DataSource.OFFICIAL_API,
            last_synced_at=now,
        ),
        Product(
            store_id=store.id,
            store_app_id=store.app_id,
            product_id="100003",
            title="缺少销量黑色外套",
            state=ProductState.ACTIVE,
            min_price_cents=1500,
            stock_total=3,
            observed_at=now,
            data_source=DataSource.OFFICIAL_API,
            last_synced_at=now,
        ),
        Product(
            store_id=store.id,
            store_app_id=store.app_id,
            product_id="100004",
            title="网页指标黑色外套",
            state=ProductState.INACTIVE,
            min_price_cents=1800,
            stock_total=3,
            observed_at=now,
            sales_count=0,
            sales_source=DataSource.WEB_BACKEND,
            listing_time=now,
            listing_source=DataSource.WEB_BACKEND,
            data_source=DataSource.WEB_BACKEND,
            last_synced_at=now,
        ),
        Product(
            store_id=other.id,
            store_app_id=other.app_id,
            product_id="100001",
            title="其他店铺黑色外套",
            state=ProductState.ACTIVE,
            min_price_cents=1000,
            stock_total=3,
            observed_at=now,
            sales_count=0,
            sales_source=DataSource.OFFICIAL_API,
            data_source=DataSource.OFFICIAL_API,
            last_synced_at=now,
        ),
        Product(
            store_id=no_sales_store.id,
            store_app_id=no_sales_store.app_id,
            product_id="300001",
            title="网页零销量商品",
            state=ProductState.ACTIVE,
            min_price_cents=1000,
            stock_total=3,
            observed_at=now,
            sales_count=0,
            sales_source=DataSource.WEB_BACKEND,
            data_source=DataSource.WEB_BACKEND,
            last_synced_at=now,
        ),
    )
    for product in products:
        repo.upsert_product(product)

    zero_sales = repo.query_products(
        store.id,
        ProductFilter(zero_sales=True, page_size=20),
    )
    assert [item.product_id for item in zero_sales.items] == ["100001"]
    assert zero_sales.total == 1
    assert "sales" not in zero_sales.unavailable_fields

    combined = repo.query_products(
        store.id,
        ProductFilter(
            keyword="黑色",
            states=(ProductState.ACTIVE,),
            min_price_cents=1200,
            max_price_cents=2100,
            sales_min=1,
            sales_max=10,
            listed_from=now,
            page=1,
            page_size=1,
        ),
    )
    assert [item.product_id for item in combined.items] == ["100002"]
    assert combined.total == 1
    assert combined.page_count == 1

    exact_title = repo.query_products(
        store.id,
        ProductFilter(
            keyword="有销量黑色外套",
            title_exact=True,
            page_size=20,
        ),
    )
    assert [item.product_id for item in exact_title.items] == ["100002"]
    exact_missing = repo.query_products(
        store.id,
        ProductFilter(
            keyword="黑色",
            title_exact=True,
            page_size=20,
        ),
    )
    assert exact_missing.items == ()

    only_untrusted = repo.query_products(
        other.id,
        ProductFilter(zero_sales=True),
    )
    assert [item.product_id for item in only_untrusted.items] == ["100001"]

    unavailable = repo.query_products(
        no_sales_store.id,
        ProductFilter(zero_sales=True),
    )
    assert unavailable.items == ()
    assert "sales" in unavailable.unavailable_fields

    empty = repo.query_products(empty_store.id, ProductFilter())
    assert empty.items == ()
    assert empty.total == 0
