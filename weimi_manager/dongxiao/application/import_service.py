from __future__ import annotations

import re
from dataclasses import replace

from ..domain.enums import ProductImportStatus
from ..domain.imports import (
    ProductImportPreview,
    parse_product_import,
)
from ..infrastructure.repository import DongxiaoRepository

_PRODUCT_ID_PATTERN = re.compile(r"^[0-9]{6,64}$")


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

    def associate(
        self,
        store,
        preview: ProductImportPreview,
        *,
        line_number: int,
        product_id: str,
    ) -> ProductImportPreview:
        normalized_id = str(product_id or "").strip()
        if not _PRODUCT_ID_PATTERN.fullmatch(normalized_id):
            raise ValueError("商品 ID 必须是 6 到 64 位数字")
        product = self.repository.get_product(
            int(store.id),
            normalized_id,
        )
        if product is None or product.store_app_id != str(store.app_id):
            raise ValueError("该商品不在当前店铺的授权商品目录中")
        target = next(
            (
                item
                for item in preview.items
                if item.line_number == int(line_number)
            ),
            None,
        )
        if target is None:
            raise ValueError("找不到要关联的导入行")
        if target.status is not ProductImportStatus.PENDING_RESOLUTION:
            raise ValueError("只有待解析行可以手动关联商品")
        linked = replace(
            target,
            status=ProductImportStatus.READY,
            product_id=normalized_id,
            dedupe_key=f"product:{normalized_id}",
            reason="已通过授权商品目录手动关联，保留原始分享文本",
        )
        return ProductImportPreview(
            tuple(
                linked if item.line_number == target.line_number else item
                for item in preview.items
            )
        )
