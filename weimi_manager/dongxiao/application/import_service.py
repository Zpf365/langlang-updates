from __future__ import annotations

from ..domain.imports import ProductImportPreview, parse_product_import
from ..infrastructure.repository import DongxiaoRepository


class ProductImportService:
    def __init__(self, repository: DongxiaoRepository) -> None:
        self.repository = repository

    def preview(self, text: str) -> ProductImportPreview:
        return parse_product_import(text)

    def persist(
        self,
        store,
        preview: ProductImportPreview,
    ) -> dict[str, int]:
        return self.repository.save_import_preview(store, preview)

    def list_pending(self, store) -> list[dict[str, object]]:
        return self.repository.list_import_items(
            int(store.id),
            status="pending_resolution",
            limit=5000,
        )
