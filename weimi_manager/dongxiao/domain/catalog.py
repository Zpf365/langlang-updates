from __future__ import annotations

from dataclasses import dataclass

from .enums import DataSource
from .models import Product, Sku


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
