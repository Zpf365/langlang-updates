from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QApplication

from weimi_manager.auth_client import LicenseStatus, SessionTokens
from weimi_manager.auth_service import AuthContext
from weimi_manager.config import AppPaths
from weimi_manager.pyside.main_window import PAGE_DEFINITIONS, FeaturePage, MainWindow
from weimi_manager.pyside.task_manager import TaskManager
from weimi_manager.pyside.workspace import build_workspace


def test_pyside_shell_keeps_all_legacy_routes_and_caches_pages(tmp_path: Path) -> None:
    application = QApplication.instance() or QApplication([])
    tokens = SessionTokens(
        user_id="shell",
        account_id="shell",
        username="test",
        email="shell@example.invalid",
        access_token="access",
        refresh_token="refresh",
        expires_at="2099-01-01T00:00:00+00:00",
    )
    context = AuthContext(
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
    data_dir = tmp_path / "data"
    paths = AppPaths(
        project_dir=tmp_path,
        resource_dir=tmp_path,
        data_dir=data_dir,
        database=data_dir / "weimi_manager.db",
    )
    manager = TaskManager(max_workers=2)
    workspace = build_workspace(
        paths,
        context,
        manager,
    )
    window = MainWindow(workspace, lambda _context: None, lambda: None)
    try:
        assert set(PAGE_DEFINITIONS) == {
            "workbench",
            "diagnostics",
            "stores",
            "order_management",
            "orders",
            "parsed",
            "products",
            "upload",
            "operations",
            "customer_service",
            "marketing",
            "analytics",
            "storefront",
            "finance",
            "procurement",
            "automation",
            "settings",
            "account",
            "store_groups",
            "activity_center",
            "team",
            "violation_blacklist",
            "dynamic",
            "no_traffic",
            "hosted",
        }
        for route in PAGE_DEFINITIONS:
            window.navigate(route)
        assert all(not isinstance(page, FeaturePage) for page in window._pages.values())
        first_pages = dict(window._pages)
        for route in PAGE_DEFINITIONS:
            window.navigate(route)
        assert window._pages == first_pages
        assert window.stack.count() == len(PAGE_DEFINITIONS)
    finally:
        window.close()
        workspace.close()
        manager.shutdown()
        window.deleteLater()
        application.processEvents()
