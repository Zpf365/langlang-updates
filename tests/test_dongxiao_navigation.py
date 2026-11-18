from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from weimi_manager.config import AppPaths
from weimi_manager.ui.app import MainWindow
from weimi_manager.ui.dongxiao_page import DongxiaoPage


def test_formal_sales_related_route_opens_dongxiao_center(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("WEIMI_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("WEIMI_DISABLE_AUTO_BACKUP", "1")
    monkeypatch.setenv("WEIMI_DISABLE_AUTO_UPDATE", "1")

    window = MainWindow(AppPaths.discover())
    window.withdraw()
    try:
        window.show_page("sales_related")
        window.update_idletasks()

        page = window.sales_related_page
        assert isinstance(page, DongxiaoPage)
        assert window.title_var.get() == "动销中心"
        assert [
            button.cget("text")
            for button in page.section_buttons.values()
        ] == [
            "总览",
            "店铺连接",
            "商品池",
            "商品筛选",
            "任务中心",
            "真实订单",
            "售后工单",
            "日志审计",
        ]

        page.section_buttons["tasks"].invoke()
        window.update_idletasks()
        assert page.section_title_var.get() == "任务中心"
    finally:
        window.destroy()


def test_dongxiao_route_keeps_procurement_and_after_sales_gate() -> None:
    for permissions, expected in (
        ({}, False),
        ({"procurement": True}, True),
        ({"after_sales": True}, True),
    ):
        window = object.__new__(MainWindow)
        window.auth_context = SimpleNamespace(
            member_role="operator",
            permissions=permissions,
        )
        assert window._can_sales_related() is expected


def test_dongxiao_route_blocks_operator_without_permission() -> None:
    window = object.__new__(MainWindow)
    window.auth_context = SimpleNamespace(
        member_role="operator",
        permissions={},
    )
    window._pages = {}

    with mock.patch("weimi_manager.ui.app.messagebox.showwarning") as warning:
        window.show_page("sales_related")

    warning.assert_called_once()
    assert window._pages == {}
