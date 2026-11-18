from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtWidgets import QApplication

from weimi_manager.auth_client import LicenseStatus, SessionTokens
from weimi_manager.auth_service import AuthContext
from weimi_manager.config import AppPaths
from weimi_manager.models import Order
from weimi_manager.pyside.pages.orders import OrdersPage
from weimi_manager.pyside.task_manager import TaskManager
from weimi_manager.pyside.workspace import build_workspace


def _context() -> AuthContext:
    tokens = SessionTokens(
        user_id="orders",
        account_id="orders",
        username="orders",
        email="orders@example.invalid",
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


def test_orders_page_preserves_legacy_tabs_and_rows(tmp_path: Path) -> None:
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
        store_id = workspace.db.add_store("Store", "wx-order", "")
        now = int(datetime.now().timestamp())
        workspace.db.upsert_order(
            Order(
                store_id=store_id,
                store_name="Store",
                order_id="ORDER-1",
                status=20,
                status_text="待发货",
                create_time=now,
                update_time=now,
                pay_time=now,
                total_amount=1990,
                product_amount=1990,
                freight_amount=0,
                discount_amount=0,
                buyer_name="Buyer",
                phone="***",
                address="Test",
                waybill_id="YT-1",
                carrier="YTO",
                merchant_notes="",
                items=[],
                raw={},
            )
        )
        page = OrdersPage(workspace)
        try:
            assert page.table.model_data.rowCount() == 1
            assert page.tab_counts["pending_delivery"] == 1
            page._set_tab("pending_delivery")
            assert page.table.model_data.rowCount() == 1
        finally:
            page.deleteLater()
    finally:
        workspace.close()
        manager.shutdown()
        application.processEvents()
