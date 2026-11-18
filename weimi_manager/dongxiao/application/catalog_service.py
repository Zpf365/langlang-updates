from __future__ import annotations

import threading
import uuid
from collections.abc import Callable
from datetime import UTC, datetime

from ..domain.catalog import CatalogSyncResult
from ..domain.queries import ProductFilter, ProductPage
from ..domain.rules import to_utc_iso
from ..infrastructure.catalog import (
    CatalogAdapterError,
    CatalogDataError,
    ProductCatalogAdapter,
)
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
        started_at = datetime.now(UTC)
        run_id = uuid.uuid4().hex
        cursor = ""
        seen_cursors: set[str] = set()
        seen_product_ids: set[str] = set()
        discovered = 0
        saved = 0
        reconciled_skus = 0
        errors: list[str] = []
        page_number = 0
        cancelled = False
        while True:
            if cancel_event is not None and cancel_event.is_set():
                cancelled = True
                break
            page_number += 1
            try:
                page = self.adapter.list_page(
                    store,
                    cursor=cursor,
                    page_size=30,
                )
            except CatalogAdapterError as exc:
                errors.append(f"目录第 {page_number} 页失败: {exc}")
                break
            discovered += len(page.items) + len(page.issues)
            errors.extend(
                f"{issue.product_id}: {issue.code}: {issue.message}"
                for issue in page.issues
            )
            if page.items:
                seen_product_ids.update(
                    item.product.product_id for item in page.items
                )
                page_saved, page_reconciled_skus = (
                    self.repository.upsert_catalog_items(
                        list(page.items),
                        return_reconciled=True,
                    )
                )
                saved += page_saved
                reconciled_skus += page_reconciled_skus
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

        complete = not cancelled and not errors
        reconciled_products = 0
        if complete:
            (
                reconciled_products,
                missing_product_skus,
            ) = self.repository.reconcile_catalog_missing(
                store_id=int(store.id),
                store_app_id=str(store.app_id),
                seen_product_ids=seen_product_ids,
            )
            reconciled_skus += missing_product_skus
        finished_at = datetime.now(UTC)
        status = (
            "cancelled"
            if cancelled
            else (
                "complete"
                if complete
                else ("partial" if saved else "failed")
            )
        )
        self.repository.record_catalog_sync_run(
            run_id=run_id,
            store_id=int(store.id),
            store_app_id=str(store.app_id),
            source=self.adapter.source,
            status=status,
            discovered=discovered,
            saved=saved,
            failed=len(errors),
            reconciled_products=reconciled_products,
            reconciled_skus=reconciled_skus,
            errors=errors,
            started_at=started_at,
            finished_at=finished_at,
        )
        return CatalogSyncResult(
            store_id=int(store.id),
            discovered=discovered,
            products_saved=saved,
            failed=len(errors),
            source=self.adapter.source,
            last_synced_at=to_utc_iso(finished_at),
            errors=tuple(errors),
            complete=complete,
            reconciled_products=reconciled_products,
            reconciled_skus=reconciled_skus,
            run_id=run_id,
            status=status,
        )
