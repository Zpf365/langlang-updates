from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import UTC, datetime

from ..domain.catalog import CatalogSyncResult
from ..domain.queries import ProductFilter, ProductPage
from ..domain.rules import to_utc_iso
from ..infrastructure.catalog import CatalogDataError, ProductCatalogAdapter
from ..infrastructure.repository import DongxiaoRepository

ProgressCallback = Callable[[str, int, int], None]


class ProductCatalogService:
    def __init__(
        self,
        repository: DongxiaoRepository,
        adapter: ProductCatalogAdapter,
    ) -> None:
        self.repository = repository
        self.adapter = adapter

    def query(
        self,
        store_id: int,
        filters: ProductFilter,
    ) -> ProductPage:
        return self.repository.query_products(store_id, filters)

    def sync_store(
        self,
        store,
        *,
        progress: ProgressCallback | None = None,
        cancel_event: threading.Event | None = None,
    ) -> CatalogSyncResult:
        cursor = ""
        seen_cursors: set[str] = set()
        discovered = 0
        saved = 0
        errors: list[str] = []
        page_number = 0
        while True:
            if cancel_event is not None and cancel_event.is_set():
                break
            page_number += 1
            page = self.adapter.list_page(
                store,
                cursor=cursor,
                page_size=30,
            )
            discovered += len(page.items) + len(page.issues)
            errors.extend(
                f"{issue.product_id}: {issue.code}: {issue.message}"
                for issue in page.issues
            )
            if page.items:
                saved += self.repository.upsert_catalog_items(
                    list(page.items)
                )
            if progress:
                progress(
                    f"正在同步商品目录：第 {page_number} 页，"
                    f"已保存 {saved}/{discovered}",
                    discovered,
                    discovered,
                )
            next_cursor = str(page.next_cursor or "")
            if not next_cursor:
                break
            if next_cursor in seen_cursors:
                raise CatalogDataError("商品目录分页游标重复，已停止同步")
            seen_cursors.add(next_cursor)
            cursor = next_cursor

        return CatalogSyncResult(
            store_id=int(store.id),
            discovered=discovered,
            products_saved=saved,
            failed=len(errors),
            source=self.adapter.source,
            last_synced_at=to_utc_iso(datetime.now(UTC)),
            errors=tuple(errors),
        )
