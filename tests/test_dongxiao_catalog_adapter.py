from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

from weimi_manager.db import Database
from weimi_manager.dongxiao.domain.enums import DataSource, ProductState
from weimi_manager.dongxiao.domain.models import Product, Sku
from weimi_manager.dongxiao.infrastructure.catalog import (
    CachedProductCatalogAdapter,
    CatalogAccessError,
    CatalogOfflineError,
    CatalogTimeoutError,
    OfficialProductCatalogAdapter,
)
from weimi_manager.dongxiao.infrastructure.repository import (
    DongxiaoRepository,
)


class _FakeClient:
    def fetch_product_id_page(self, cursor: str = "", status=None):
        assert cursor == ""
        return SimpleNamespace(
            ids=["100001"],
            next_key="next-page",
            has_more=True,
        )

    def fetch_product(self, store, product_id: str):
        assert store.app_id == "wx-catalog"
        return SimpleNamespace(
            store_id=store.id,
            store_name=store.name,
            product_id=product_id,
            title="官方商品",
            status=5,
            status_text="上架",
            edit_status=0,
            min_price=1200,
            sku_count=1,
            stock_total=8,
            raw={
                "product_id": product_id,
                "status": 5,
                "skus": [
                    {
                        "sku_id": "sku-1",
                        "sale_price": 1200,
                        "stock_num": 8,
                        "sku_attrs": [
                            {"attr_key": "颜色", "attr_value": "黑色"}
                        ],
                    }
                ],
            },
        )


def test_official_adapter_maps_only_fields_with_official_provenance() -> None:
    service = SimpleNamespace(client_for=lambda _store: _FakeClient())
    adapter = OfficialProductCatalogAdapter(service)
    store = SimpleNamespace(
        id=11,
        name="官方接口店铺",
        app_id="wx-catalog",
    )

    page = adapter.list_page(store, page_size=10)

    assert page.next_cursor == "next-page"
    assert page.source is DataSource.OFFICIAL_API
    assert len(page.items) == 1
    product = page.items[0].product
    sku = page.items[0].skus[0]
    assert product.product_id == "100001"
    assert product.state is ProductState.ACTIVE
    assert product.data_source is DataSource.OFFICIAL_API
    assert product.sales_count is None
    assert product.listing_time is None
    assert sku.sku_id == "sku-1"
    assert sku.price_cents == 1200
    assert sku.stock == 8
    assert sku.specs == (("颜色", "黑色"),)


def test_official_adapter_returns_partial_results_on_detail_timeout() -> None:
    class PartialClient(_FakeClient):
        def fetch_product_id_page(self, cursor: str = "", status=None):
            return SimpleNamespace(
                ids=["100001", "100002"],
                next_key="",
                has_more=False,
            )

        def fetch_product(self, store, product_id: str):
            if product_id == "100002":
                raise requests.Timeout("timeout")
            return super().fetch_product(store, product_id)

    service = SimpleNamespace(client_for=lambda _store: PartialClient())
    adapter = OfficialProductCatalogAdapter(service)
    store = SimpleNamespace(
        id=11,
        name="官方接口店铺",
        app_id="wx-catalog",
    )

    page = adapter.list_page(store)

    assert [item.product.product_id for item in page.items] == ["100001"]
    assert len(page.issues) == 1
    assert page.issues[0].code == "timeout"


def test_official_adapter_maps_catalog_timeout() -> None:
    class TimeoutClient:
        def fetch_product_id_page(self, cursor: str = "", status=None):
            raise requests.Timeout("timeout")

    service = SimpleNamespace(client_for=lambda _store: TimeoutClient())
    adapter = OfficialProductCatalogAdapter(service)
    store = SimpleNamespace(id=11, name="店铺", app_id="wx-catalog")

    try:
        adapter.list_page(store)
    except CatalogTimeoutError as exc:
        assert "超时" in str(exc)
    else:
        raise AssertionError("expected CatalogTimeoutError")


def test_official_adapter_maps_offline_and_permission_errors() -> None:
    store = SimpleNamespace(id=11, name="店铺", app_id="wx-catalog")
    offline_service = SimpleNamespace(
        client_for=lambda _store: (_ for _ in ()).throw(
            requests.ConnectionError("offline")
        )
    )
    with pytest.raises(CatalogOfflineError):
        OfficialProductCatalogAdapter(offline_service).list_page(store)

    denied_service = SimpleNamespace(
        client_for=lambda _store: (_ for _ in ()).throw(
            PermissionError("denied")
        )
    )
    with pytest.raises(CatalogAccessError):
        OfficialProductCatalogAdapter(denied_service).list_page(store)


def test_cached_adapter_is_marked_local_cache_without_claiming_mock(
    tmp_path: Path,
) -> None:
    db = Database(tmp_path / "cache.db")
    store_id = db.add_store("缓存店铺", "wx-cache-store", "x" * 32)
    store = db.get_store(store_id)
    assert store is not None
    repo = DongxiaoRepository(db.path)
    now = datetime(2026, 11, 18, 10, 0, tzinfo=UTC)
    repo.upsert_product(
        Product(
            store_id=store.id,
            store_app_id=store.app_id,
            product_id="100001",
            title="缓存商品",
            state=ProductState.ACTIVE,
            min_price_cents=1200,
            stock_total=8,
            observed_at=now,
            data_source=DataSource.OFFICIAL_API,
            last_synced_at=now,
        )
    )
    repo.upsert_sku(
        Sku(
            store_id=store.id,
            store_app_id=store.app_id,
            product_id="100001",
            sku_id="sku-cache",
            price_cents=1200,
            stock=8,
            observed_at=now,
        )
    )

    page = CachedProductCatalogAdapter(repo).list_page(store)

    assert page.source is DataSource.LOCAL_CACHE
    assert page.items[0].product.from_cache is True
    assert page.items[0].product.data_source is DataSource.OFFICIAL_API
