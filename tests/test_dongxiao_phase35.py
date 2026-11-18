from __future__ import annotations

import csv
import io
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from weimi_manager.db import Database
from weimi_manager.dongxiao.application.catalog_service import (
    ProductCatalogService,
)
from weimi_manager.dongxiao.application.pool_service import ProductPoolService
from weimi_manager.dongxiao.domain.catalog import (
    CatalogFreshness,
    CatalogPageData,
    CatalogProductData,
    classify_catalog_freshness,
)
from weimi_manager.dongxiao.domain.enums import (
    DataSource,
    PresenceState,
    ProductState,
)
from weimi_manager.dongxiao.domain.models import Product, Sku
from weimi_manager.dongxiao.domain.rules import (
    csv_restore_text,
    csv_safe_text,
)
from weimi_manager.dongxiao.infrastructure.catalog import (
    CatalogTimeoutError,
    MockProductCatalogAdapter,
)
from weimi_manager.dongxiao.infrastructure.repository import (
    DongxiaoRepository,
)
from weimi_manager.wechat_api import (
    WechatShopClient,
    WechatTimeoutError,
)


class _TokenRefreshTransport:
    def __init__(self, error_code: int) -> None:
        self.error_code = error_code
        self.token_calls = 0
        self.api_tokens: list[str] = []

    def post_json(self, url, payload, timeout=25):
        del timeout
        if url.endswith("/cgi-bin/stable_token"):
            self.token_calls += 1
            return {
                "access_token": f"token-{self.token_calls}",
                "expires_in": 7200,
            }
        self.api_tokens.append(url.split("access_token=", 1)[1])
        if len(self.api_tokens) == 1:
            return {
                "errcode": self.error_code,
                "errmsg": "access token unavailable",
            }
        return {"errcode": 0, "errmsg": "ok", "product_ids": []}

    def get_json(self, url, timeout=25):
        raise AssertionError(f"unexpected GET: {url}")


@pytest.mark.parametrize("error_code", [40001, 42001])
def test_wechat_client_refreshes_expired_token_once(
    error_code: int,
) -> None:
    transport = _TokenRefreshTransport(error_code)
    client = WechatShopClient(
        "wx-test",
        "secret",
        transport=transport,
        sleeper=lambda _seconds: None,
        min_request_interval=0,
    )

    page = client.fetch_product_id_page()

    assert page.ids == []
    assert transport.token_calls == 2
    assert transport.api_tokens == ["token-1", "token-2"]


def test_product_catalog_passes_documented_page_size_cursor() -> None:
    class PageTransport:
        def __init__(self) -> None:
            self.payload = {}

        def post_json(self, url, payload, timeout=25):
            del timeout
            if url.endswith("/cgi-bin/stable_token"):
                return {"access_token": "token", "expires_in": 7200}
            self.payload = payload
            return {
                "errcode": 0,
                "errmsg": "ok",
                "product_ids": ["100001"],
                "next_key": "b64:next",
            }

        def get_json(self, url, timeout=25):
            raise AssertionError(f"unexpected GET: {url}")

    transport = PageTransport()
    client = WechatShopClient(
        "wx-test",
        "secret",
        transport=transport,
        min_request_interval=0,
    )

    page = client.fetch_product_id_page(
        next_key="b64:previous",
        page_size=10,
    )

    assert page.next_key == "b64:next"
    assert transport.payload == {
        "page_size": 10,
        "next_key": "b64:previous",
    }


def test_catalog_adapter_maps_real_transport_timeout_type() -> None:
    from weimi_manager.dongxiao.infrastructure.catalog import (
        OfficialProductCatalogAdapter,
    )

    class TimeoutClient:
        def fetch_product_id_page(
            self,
            cursor="",
            status=None,
            page_size=30,
        ):
            del cursor, status, page_size
            raise WechatTimeoutError("transport timeout")

    service = SimpleNamespace(client_for=lambda _store: TimeoutClient())
    store = SimpleNamespace(id=1, app_id="wx-timeout", name="超时店铺")

    with pytest.raises(CatalogTimeoutError):
        OfficialProductCatalogAdapter(service).list_page(store)


def _store(db: Database):
    store_id = db.add_store("数据一致性店铺", "wx-phase35", "x" * 32)
    store = db.get_store(store_id)
    assert store is not None
    return store


def _catalog_item(
    store,
    *,
    sku_count: int,
    state: ProductState = ProductState.ACTIVE,
    price_offset: int = 0,
    stock: int = 10,
    unavailable_sku_ids: frozenset[str] = frozenset(),
) -> CatalogProductData:
    now = datetime(2026, 11, 18, 10, 0, tzinfo=UTC)
    product_id = "product-consistent"
    skus = tuple(
        Sku(
            store_id=store.id,
            store_app_id=store.app_id,
            product_id=product_id,
            sku_id=f"sku-{index}",
            price_cents=1000 + index + price_offset,
            stock=stock,
            available=(
                stock > 0 and f"sku-{index}" not in unavailable_sku_ids
            ),
            observed_at=now,
        )
        for index in range(1, sku_count + 1)
    )
    return CatalogProductData(
        product=Product(
            store_id=store.id,
            store_app_id=store.app_id,
            product_id=product_id,
            title="一致性商品",
            state=state,
            min_price_cents=1001 + price_offset,
            stock_total=stock * sku_count,
            observed_at=now,
            data_source=DataSource.MOCK,
            last_synced_at=now,
        ),
        skus=skus,
    )


def _sync(store, repo, item: CatalogProductData):
    return ProductCatalogService(
        repo,
        MockProductCatalogAdapter((item,)),
    ).sync_store(store)


def test_successful_sync_reconciles_removed_skus_without_deleting_history(
    tmp_path: Path,
) -> None:
    db = Database(tmp_path / "phase35-sku-reconciliation.db")
    store = _store(db)
    repo = DongxiaoRepository(db.path)
    _sync(store, repo, _catalog_item(store, sku_count=10))

    result = _sync(
        store,
        repo,
        _catalog_item(
            store,
            sku_count=8,
            price_offset=200,
            stock=0,
            state=ProductState.INACTIVE,
            unavailable_sku_ids=frozenset({"sku-8"}),
        ),
    )

    assert result.complete is True
    assert result.reconciled_skus == 2
    current = repo.list_skus(store.id, "product-consistent")
    assert [sku.sku_id for sku in current] == [
        f"sku-{index}" for index in range(1, 9)
    ]
    assert current[0].price_cents == 1201
    assert current[0].stock == 0
    assert current[0].available is False
    assert current[-1].available is False
    historical = repo.list_skus(
        store.id,
        "product-consistent",
        include_missing=True,
    )
    assert len(historical) == 10
    assert {
        sku.sku_id
        for sku in historical
        if sku.presence_state is PresenceState.MISSING
    } == {"sku-9", "sku-10"}
    product = repo.get_product(store.id, "product-consistent")
    assert product is not None
    assert product.state is ProductState.INACTIVE
    assert product.min_price_cents == 1201
    assert product.stock_total == 0
    with sqlite3.connect(db.path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM dongxiao_product_snapshots"
        ).fetchone()[0] == 20


def test_partial_detail_failure_does_not_overwrite_or_hide_old_product(
    tmp_path: Path,
) -> None:
    db = Database(tmp_path / "phase35-detail-failure.db")
    store = _store(db)
    repo = DongxiaoRepository(db.path)
    original = _catalog_item(store, sku_count=3)
    _sync(store, repo, original)

    result = ProductCatalogService(
        repo,
        MockProductCatalogAdapter(
            (original,),
            fail_product_ids=frozenset({"product-consistent"}),
        ),
    ).sync_store(store)

    assert result.complete is False
    assert result.status == "failed"
    product = repo.get_product(store.id, "product-consistent")
    assert product is not None
    assert product.presence_state is PresenceState.PRESENT
    assert len(repo.list_skus(store.id, "product-consistent")) == 3


def test_second_page_failure_keeps_first_page_and_does_not_reconcile_missing(
    tmp_path: Path,
) -> None:
    db = Database(tmp_path / "phase35-page-failure.db")
    store = _store(db)
    repo = DongxiaoRepository(db.path)
    first_item = _catalog_item(store, sku_count=2)
    second_item = CatalogProductData(
        product=Product(
            store_id=store.id,
            store_app_id=store.app_id,
            product_id="product-page-two",
            title="第二页旧商品",
            state=ProductState.ACTIVE,
            min_price_cents=1001,
            stock_total=10,
            observed_at=datetime(2026, 11, 18, 10, 0, tzinfo=UTC),
            data_source=DataSource.MOCK,
            last_synced_at=datetime(2026, 11, 18, 10, 0, tzinfo=UTC),
        ),
        skus=(),
    )
    ProductCatalogService(
        repo,
        MockProductCatalogAdapter((first_item, second_item)),
    ).sync_store(store)
    older = repo.get_product(store.id, "product-page-two")
    assert older is not None

    changed_first = _catalog_item(
        store,
        sku_count=2,
        price_offset=300,
    )

    class SecondPageFailureAdapter:
        source = DataSource.MOCK

        def list_page(self, _store, *, cursor="", page_size=30):
            del page_size
            if cursor:
                raise CatalogTimeoutError("第二页超时")
            return CatalogPageData(
                items=(changed_first,),
                next_cursor="page-2",
                source=DataSource.MOCK,
            )

    result = ProductCatalogService(
        repo,
        SecondPageFailureAdapter(),
    ).sync_store(store)

    assert result.complete is False
    assert result.status == "partial"
    changed = repo.get_product(store.id, "product-consistent")
    assert changed is not None
    assert changed.min_price_cents == 1301
    preserved = repo.get_product(store.id, "product-page-two")
    assert preserved is not None
    assert preserved.presence_state is PresenceState.PRESENT
    assert preserved.title == "第二页旧商品"


def test_repeated_and_concurrent_imports_do_not_duplicate_catalog_rows(
    tmp_path: Path,
) -> None:
    db = Database(tmp_path / "phase35-concurrent.db")
    store = _store(db)
    repo = DongxiaoRepository(db.path)
    item = _catalog_item(store, sku_count=5)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda _index: repo.upsert_catalog_items([item]),
                range(2),
            )
        )
    ProductCatalogService(
        repo,
        MockProductCatalogAdapter((item,)),
    ).sync_store(store)

    assert results == [1, 1]
    assert len(repo.list_products(store.id)) == 1
    assert len(repo.list_skus(store.id, "product-consistent")) == 5
    with sqlite3.connect(db.path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM dongxiao_products"
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM dongxiao_skus WHERE presence_state='present'"
        ).fetchone()[0] == 5
        assert conn.execute(
            "SELECT COUNT(*) FROM dongxiao_catalog_sync_runs"
        ).fetchone()[0] == 1


def test_complete_empty_catalog_marks_product_missing_but_keeps_history(
    tmp_path: Path,
) -> None:
    db = Database(tmp_path / "phase35-deleted-product.db")
    store = _store(db)
    repo = DongxiaoRepository(db.path)
    item = _catalog_item(store, sku_count=2)
    for _index in range(3):
        ProductCatalogService(
            repo,
            MockProductCatalogAdapter((item,)),
        ).sync_store(store)

    assert len(repo.list_products(store.id)) == 1
    assert len(repo.list_skus(store.id, "product-consistent")) == 2
    with sqlite3.connect(db.path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM dongxiao_product_snapshots"
        ).fetchone()[0] == 9
        assert conn.execute(
            "SELECT COUNT(*) FROM dongxiao_catalog_sync_runs"
        ).fetchone()[0] == 3

    result = ProductCatalogService(
        repo,
        MockProductCatalogAdapter(()),
    ).sync_store(store)

    assert result.complete is True
    assert result.reconciled_products == 1
    assert result.reconciled_skus == 2
    product = repo.get_product(store.id, "product-consistent")
    assert product is not None
    assert product.presence_state is PresenceState.MISSING
    assert product.missing_since is not None
    assert repo.list_skus(store.id, "product-consistent") == []
    historical = repo.list_skus(
        store.id,
        "product-consistent",
        include_missing=True,
    )
    assert {sku.presence_state for sku in historical} == {
        PresenceState.MISSING
    }
    with sqlite3.connect(db.path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM dongxiao_product_snapshots"
        ).fetchone()[0] == 9


def test_store_switch_during_sync_cannot_cross_store_reconcile(
    tmp_path: Path,
) -> None:
    db = Database(tmp_path / "phase35-store-switch.db")
    first = _store(db)
    second_id = db.add_store(
        "第二家同步店铺",
        "wx-phase35-two",
        "y" * 32,
    )
    second = db.get_store(second_id)
    assert second is not None
    repo = DongxiaoRepository(db.path)
    first_item = _catalog_item(first, sku_count=2)
    second_item = _catalog_item(second, sku_count=2)
    for store, item in ((first, first_item), (second, second_item)):
        ProductCatalogService(
            repo,
            MockProductCatalogAdapter((item,)),
        ).sync_store(store)

    ProductCatalogService(
        repo,
        MockProductCatalogAdapter(()),
    ).sync_store(second)

    first_product = repo.get_product(first.id, "product-consistent")
    second_product = repo.get_product(second.id, "product-consistent")
    assert first_product is not None
    assert first_product.presence_state is PresenceState.PRESENT
    assert second_product is not None
    assert second_product.presence_state is PresenceState.MISSING
    assert len(repo.list_skus(first.id, "product-consistent")) == 2
    assert repo.list_skus(second.id, "product-consistent") == []


@pytest.mark.parametrize(
    "text",
    (
        "=1+1",
        "+SUM(A1:A2)",
        "-10+20",
        "@SUM(A1:A2)",
        "\t=1+1",
        "\r=1+1",
    ),
)
def test_csv_formula_prefixes_are_safely_escaped_and_reversible(
    text: str,
) -> None:
    safe = csv_safe_text(text)

    assert not safe.startswith(("=", "+", "-", "@"))
    assert csv_restore_text(safe) == text
    assert csv_safe_text("微信商品") == "微信商品"
    assert csv_restore_text("'普通文本") == "'普通文本"


def test_pool_csv_export_and_import_preserve_text_without_formula_execution(
    tmp_path: Path,
) -> None:
    db = Database(tmp_path / "phase35-csv.db")
    store_id = db.add_store(
        "@危险店铺",
        "wx-phase35-csv",
        "x" * 32,
    )
    store = db.get_store(store_id)
    assert store is not None
    repo = DongxiaoRepository(db.path)
    now = datetime(2026, 11, 18, 10, 0, tzinfo=UTC)
    repo.upsert_product(
        Product(
            store_id=store.id,
            store_app_id=store.app_id,
            product_id="=100001",
            title='=HYPERLINK("https://example.invalid") 中文商品',
            state=ProductState.ACTIVE,
            min_price_cents=1234,
            stock_total=5,
            observed_at=now,
            data_source=DataSource.OFFICIAL_API,
            last_synced_at=now,
        )
    )
    repo.upsert_sku(
        Sku(
            store_id=store.id,
            store_app_id=store.app_id,
            product_id="=100001",
            sku_id="+SKU-1",
            price_cents=1234,
            stock=5,
            observed_at=now,
        )
    )
    service = ProductPoolService(repo)
    source = service.create_pool(
        store,
        name="=源商品池",
        idempotency_key="phase35-csv-source",
    )
    service.add_products(store, source.pool_id, [("=100001", "+SKU-1")])

    exported = service.export_csv(store, source.pool_id)
    rows = list(csv.DictReader(io.StringIO(exported)))

    assert rows[0]["product_id"].startswith("'")
    assert rows[0]["sku_id"].startswith("'")
    assert rows[0]["title"].startswith("'")
    assert rows[0]["store_name"].startswith("'")
    assert "中文商品" in rows[0]["title"]

    target = service.create_pool(
        store,
        name="导入目标",
        idempotency_key="phase35-csv-target",
    )
    imported = service.import_csv(store, target.pool_id, exported)
    details = service.details(store, target.pool_id)
    assert imported == {"added": 1, "skipped": 0, "invalid": 0}
    assert details[0]["product_id"] == "=100001"
    assert details[0]["sku_id"] == "+SKU-1"


def test_catalog_freshness_distinguishes_live_cache_stale_and_mock() -> None:
    now = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)

    def product(
        *,
        source: DataSource,
        from_cache: bool,
        synced_at: datetime | None,
        presence: PresenceState = PresenceState.PRESENT,
    ) -> Product:
        return Product(
            store_id=1,
            store_app_id="wx-freshness",
            product_id="100001",
            title="新鲜度商品",
            state=ProductState.ACTIVE,
            min_price_cents=100,
            stock_total=1,
            observed_at=now,
            data_source=source,
            from_cache=from_cache,
            last_synced_at=synced_at,
            presence_state=presence,
            missing_since=now if presence is PresenceState.MISSING else None,
        )

    assert classify_catalog_freshness(
        product(
            source=DataSource.OFFICIAL_API,
            from_cache=False,
            synced_at=now,
        ),
        now=now,
    ) is CatalogFreshness.LIVE_OFFICIAL
    assert classify_catalog_freshness(
        product(
            source=DataSource.OFFICIAL_API,
            from_cache=True,
            synced_at=now,
        ),
        now=now,
    ) is CatalogFreshness.LOCAL_CACHE
    assert classify_catalog_freshness(
        product(
            source=DataSource.OFFICIAL_API,
            from_cache=True,
            synced_at=now,
            presence=PresenceState.MISSING,
        ),
        now=now,
    ) is CatalogFreshness.STALE_CACHE
    assert classify_catalog_freshness(
        product(
            source=DataSource.MOCK,
            from_cache=False,
            synced_at=now,
        ),
        now=now,
    ) is CatalogFreshness.MOCK
