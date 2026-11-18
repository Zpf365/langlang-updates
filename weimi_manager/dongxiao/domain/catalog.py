from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from .enums import DataSource, PresenceState
from .models import Product, Sku
from .rules import ensure_utc


class CatalogFreshness(StrEnum):
    LIVE_OFFICIAL = "live_official"
    LOCAL_CACHE = "local_cache"
    STALE_CACHE = "stale_cache"
    MOCK = "mock"
    UNKNOWN = "unknown"


def classify_catalog_freshness(
    product: Product,
    *,
    now: datetime | None = None,
    stale_after: timedelta = timedelta(hours=24),
) -> CatalogFreshness:
    current_time = ensure_utc(now or datetime.now(UTC), field="缓存判断时间")
    if product.data_source is DataSource.MOCK:
        return CatalogFreshness.MOCK
    if (
        product.presence_state is PresenceState.MISSING
        or product.last_synced_at is None
        or current_time - product.last_synced_at > stale_after
    ):
        return (
            CatalogFreshness.STALE_CACHE
            if product.from_cache
            else CatalogFreshness.UNKNOWN
        )
    if product.from_cache:
        return CatalogFreshness.LOCAL_CACHE
    if product.data_source is DataSource.OFFICIAL_API:
        return CatalogFreshness.LIVE_OFFICIAL
    return CatalogFreshness.UNKNOWN


@dataclass(frozen=True, slots=True)
class CatalogProductData:
    product: Product
    skus: tuple[Sku, ...]


@dataclass(frozen=True, slots=True)
class CatalogIssue:
    product_id: str
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class CatalogPageData:
    items: tuple[CatalogProductData, ...]
    next_cursor: str = ""
    total: int | None = None
    source: DataSource = DataSource.UNKNOWN
    issues: tuple[CatalogIssue, ...] = ()


@dataclass(frozen=True, slots=True)
class CatalogSyncResult:
    store_id: int
    discovered: int
    products_saved: int
    failed: int
    source: DataSource
    last_synced_at: str
    errors: tuple[str, ...] = ()
    complete: bool = False
    reconciled_products: int = 0
    reconciled_skus: int = 0
    run_id: str = ""
    status: str = "failed"
