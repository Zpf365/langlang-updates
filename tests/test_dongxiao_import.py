from __future__ import annotations

from pathlib import Path

from weimi_manager.db import Database
from weimi_manager.dongxiao.domain.enums import ProductImportStatus
from weimi_manager.dongxiao.domain.imports import parse_product_import
from weimi_manager.dongxiao.infrastructure.repository import (
    DongxiaoRepository,
)


def test_product_link_import_keeps_unresolvable_share_text_pending() -> None:
    text = """
    123456789012345
    https://channels.weixin.qq.com/shop/product?product_id=987654321
    https://evil.example/product/abcdef
    #微信小店://示例店铺/not-a-product-id
    """

    preview = parse_product_import(text)

    assert [item.status for item in preview.items] == [
        ProductImportStatus.READY,
        ProductImportStatus.READY,
        ProductImportStatus.INVALID,
        ProductImportStatus.PENDING_RESOLUTION,
    ]
    assert preview.items[0].product_id == "123456789012345"
    assert preview.items[1].product_id == "987654321"
    assert preview.items[2].product_id is None
    assert preview.items[3].product_id is None
    assert "不能编造" in preview.items[3].reason


def test_product_link_import_reports_duplicates_and_unsafe_urls() -> None:
    text = """
    123456
    123456
    https://user:pass@channels.weixin.qq.com/shop/product?product_id=1
    http://channels.weixin.qq.com.evil.example/product?product_id=2
    #微信小店://同一店铺/同一分享码
    #微信小店://同一店铺/同一分享码
    """

    preview = parse_product_import(text)

    assert [item.status for item in preview.items] == [
        ProductImportStatus.READY,
        ProductImportStatus.DUPLICATE,
        ProductImportStatus.INVALID,
        ProductImportStatus.INVALID,
        ProductImportStatus.PENDING_RESOLUTION,
        ProductImportStatus.DUPLICATE,
    ]
    assert preview.duplicate_count == 2
    assert preview.items[0].status is ProductImportStatus.READY
    assert preview.items[2].status is ProductImportStatus.INVALID
    assert preview.items[3].status is ProductImportStatus.INVALID
    assert preview.items[4].status is ProductImportStatus.PENDING_RESOLUTION
    assert preview.ready_count == 1
    assert preview.invalid_count == 2
    assert preview.pending_count == 1


def test_import_preview_persists_pending_and_reports_duplicates(
    tmp_path: Path,
) -> None:
    db = Database(tmp_path / "imports.db")
    store_id = db.add_store("导入店铺", "wx-import-store", "x" * 32)
    store = db.get_store(store_id)
    assert store is not None
    repo = DongxiaoRepository(db.path)
    preview = parse_product_import(
        "\n".join(
            (
                "123456",
                "123456",
                "#微信小店://示例店铺/等待授权解析",
            )
        )
    )

    result = repo.save_import_preview(store, preview)

    assert result == {
        "saved": 3,
        "duplicates": 1,
        "ready": 1,
        "pending": 1,
        "invalid": 0,
    }
    rows = repo.list_import_items(store.id)
    assert {
        row["status"] for row in rows
    } == {
        ProductImportStatus.READY.value,
        ProductImportStatus.DUPLICATE.value,
        ProductImportStatus.PENDING_RESOLUTION.value,
    }
