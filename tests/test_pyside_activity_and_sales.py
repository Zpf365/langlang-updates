from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace

from PySide6.QtWidgets import QApplication

from weimi_manager.db import Database
from weimi_manager.pyside.pages.activity_center import ActivityCenterPage
from weimi_manager.pyside.pages.sales_related import SalesRelatedPage
from weimi_manager.pyside.task_manager import TaskManager


def test_activity_center_is_six_tab_pyside_page() -> None:
    application = QApplication.instance() or QApplication([])
    manager = TaskManager(max_workers=2)
    with tempfile.TemporaryDirectory() as tmp:
        workspace = SimpleNamespace(
            db=Database(Path(tmp) / "activity.db"),
            service=SimpleNamespace(),
            web_session=SimpleNamespace(),
            task_manager=manager,
            auth_context=None,
            auth_client=None,
        )
        page = ActivityCenterPage(workspace)
        try:
            assert page.title == "活动中心"
            assert page.limited_page.discount.value() == 8.5
            assert page.limited_page.minimum.value() == 1.0
            assert page.limited_page.duration.value() == 168
            assert page.limited_page.discount.maximum() == 8.5
            assert page.limited_page.minimum.minimum() == 1.0
            assert [
                page.tabs.tabText(index)
                for index in range(page.tabs.count())
            ] == [
                "平台活动",
                "限时限量购活动",
                "优惠券活动",
                "限时限量购续期",
                "优惠券管理",
                "执行记录",
            ]
        finally:
            page.deleteLater()
            manager.shutdown()
            application.processEvents()


def test_sales_related_is_seven_tab_pyside_page() -> None:
    application = QApplication.instance() or QApplication([])
    manager = TaskManager(max_workers=2)
    with tempfile.TemporaryDirectory() as tmp:
        workspace = SimpleNamespace(
            db=Database(Path(tmp) / "sales.db"),
            service=SimpleNamespace(),
            web_session=SimpleNamespace(),
            task_manager=manager,
            auth_context=None,
            auth_client=None,
        )
        page = SalesRelatedPage(workspace)
        try:
            assert page.title == "动销相关"
            assert [
                page.tabs.tabText(index)
                for index in range(page.tabs.count())
            ] == [
                "采购任务",
                "SKU 与数量规则",
                "订单记录",
                "售后协同",
                "买家白名单",
                "规则设置",
                "执行日志",
            ]
        finally:
            page.deleteLater()
            manager.shutdown()
            application.processEvents()


def test_formal_launcher_keeps_original_ui_until_parity() -> None:
    launcher = (
        Path(__file__).resolve().parents[1] / "launcher.py"
    ).read_text(encoding="utf-8")
    assert "from weimi_manager.main import main" in launcher
    assert "weimi_manager.pyside.main" not in launcher
