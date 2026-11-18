from __future__ import annotations

from types import SimpleNamespace

from PySide6.QtWidgets import QApplication

from weimi_manager.pyside.pages.workbench import WorkbenchPage


def test_workbench_reads_legacy_counts_and_session_alerts() -> None:
    application = QApplication.instance() or QApplication([])

    class Db:
        @staticmethod
        def data_counts():
            return {
                "stores": 3,
                "pending_shipping": 7,
                "catalog_done": 122,
                "today_orders": 5,
            }

        @staticmethod
        def list_stores():
            return [
                SimpleNamespace(name="A", web_status="已登录"),
                SimpleNamespace(name="B", web_status="已掉线"),
            ]

    workspace = SimpleNamespace(db=Db())
    page = WorkbenchPage(workspace)
    try:
        assert page.store_card.value_label.text() == "3"
        assert page.shipping_card.value_label.text() == "7"
        assert page.product_card.value_label.text() == "122"
        assert page.order_card.value_label.text() == "5"
        assert "B" in page.session_alert.text()
    finally:
        page.deleteLater()
        application.processEvents()
