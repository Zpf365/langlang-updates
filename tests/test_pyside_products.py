from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QApplication

from weimi_manager.auth_client import LicenseStatus, SessionTokens
from weimi_manager.auth_service import AuthContext
from weimi_manager.config import AppPaths
from weimi_manager.models import Product
from weimi_manager.pyside.pages.products import ProductsPage
from weimi_manager.pyside.task_manager import TaskManager
from weimi_manager.pyside.workspace import build_workspace


def _context() -> AuthContext:
    tokens = SessionTokens(
        user_id="products",
        account_id="products",
        username="products",
        email="products@example.invalid",
        access_token="access",
        refresh_token="refresh",
        expires_at="2099-01-01T00:00:00+00:00",
    )
    return AuthContext(
        tokens=tokens,
        license=LicenseStatus(
            active=True,
            permanent=True,
            expires_at=None,
            status_text="测试授权",
            remaining_days=999999,
            max_devices=1,
            device_bound=True,
            server_time="2026-01-01T00:00:00+00:00",
        ),
        device_hash="device",
        device_name="test",
    )


def test_products_page_uses_legacy_tab_rules_and_local_rows(tmp_path: Path) -> None:
    application = QApplication.instance() or QApplication([])
    data_dir = tmp_path / "data"
    paths = AppPaths(
        project_dir=tmp_path,
        resource_dir=tmp_path,
        data_dir=data_dir,
        database=data_dir / "weimi_manager.db",
    )
    manager = TaskManager(max_workers=2)
    workspace = build_workspace(paths, _context(), manager)
    try:
        store_id = workspace.db.add_store("Store", "wx-product", "")
        for product_id, status in (("sale", 5), ("offline", 11)):
            workspace.db.upsert_product(
                Product(
                    store_id=store_id,
                    store_name="Store",
                    product_id=product_id,
                    title=product_id,
                    status=status,
                    status_text="上架" if status == 5 else "下架",
                    edit_status=0,
                    min_price=100,
                    sku_count=1,
                    stock_total=10,
                    spu_code="",
                    out_product_id="",
                    head_img="",
                    raw={},
                )
            )
        page = ProductsPage(workspace)
        try:
            assert page.active_tab_key == "sales"
            assert {row["product_id"] for row in page.visible_rows} == {"sale"}
            page._set_tab("offline")
            assert {row["product_id"] for row in page.visible_rows} == {"offline"}
            counts = page.tab_counts
            assert counts["sales"] == 1
            assert counts["offline"] == 1
        finally:
            page.deleteLater()
    finally:
        workspace.close()
        manager.shutdown()
        application.processEvents()
