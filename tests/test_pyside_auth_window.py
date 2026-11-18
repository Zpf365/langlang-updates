from __future__ import annotations

from types import SimpleNamespace

from PySide6.QtWidgets import QApplication

from weimi_manager.pyside.auth_window import AuthWindow
from weimi_manager.pyside.task_manager import TaskManager


def test_pyside_auth_window_exposes_login_register_recovery_and_license_forms() -> None:
    application = QApplication.instance() or QApplication([])

    class Store:
        @staticmethod
        def list_accounts():
            return []

    service = SimpleNamespace(store=Store())
    manager = TaskManager(max_workers=2)
    window = AuthWindow(service, manager)
    try:
        assert window.windowTitle().startswith("澜琅 · 登录")
        assert window.form_stack.count() >= 4
        window._show_form("login")
        assert window.username_input.isVisibleTo(window)
        window._show_form("register")
        assert window.register_email.isVisibleTo(window)
        window._show_form("recovery")
        assert window.recovery_identifier.isVisibleTo(window)
    finally:
        window.close()
        manager.shutdown()
        application.processEvents()
