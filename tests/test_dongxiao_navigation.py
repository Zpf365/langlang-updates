from __future__ import annotations

import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from weimi_manager.config import AppPaths
from weimi_manager.dongxiao.domain.catalog import CatalogSyncResult
from weimi_manager.dongxiao.domain.enums import DataSource
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
        assert window.dongxiao_runtime is not None
        assert page.runtime is window.dongxiao_runtime
        assert window.title_var.get() == "动销中心"
        assert [
            button.cget("text")
            for button in page.section_buttons.values()
        ] == [
            "总览与任务",
            "店铺与授权",
            "商品池管理",
            "商品筛选",
            "SKU 规则",
            "批量加购",
            "待付款订单",
            "多账号任务",
            "买家业务工作台",
            "任务中心",
            "订单与售后",
            "售后工单",
            "日志与诊断",
            "真实操作管理",
            "实盘前检查",
        ]

        page.section_buttons["tasks"].invoke()
        window.update_idletasks()
        assert page.section_title_var.get() == "任务中心"

        assert page.store_combo.winfo_exists()
        assert page.product_tree.winfo_exists()
        assert str(page.product_tree.cget("selectmode")) == "extended"
        assert set(page.product_tree["columns"]) == {
            "product_id",
            "title",
            "state",
            "price",
            "stock",
            "sales",
            "listed",
            "source",
            "synced_at",
        }
        assert page.sync_products_button.cget("text") == "同步商品"
        assert page.apply_filter_button.cget("text") == "应用筛选"
        assert page.import_products_button.cget("text") == "批量导入"
        assert page.pool_combo.winfo_exists()
        assert {
            "created",
            "task_id",
            "type",
            "status",
            "progress",
            "error",
        } == set(page.task_tree["columns"])
        assert {
            "order_id",
            "status",
            "total",
            "items",
            "created",
            "updated",
            "synced",
        } == set(page.order_tree["columns"])
        assert "case_id" in page.aftersale_tree["columns"]
        assert "action" in page.audit_tree["columns"]
        assert page.cart_account_tree.winfo_exists()
        assert set(page.cart_account_tree["columns"]) == {
            "name",
            "mode",
            "status",
            "store",
            "checked",
            "error",
        }
        assert page.cart_preview_tree.winfo_exists()
        assert "product" in page.cart_preview_tree["columns"]
        assert page.pending_order_tree.winfo_exists()
        assert "status" in page.pending_order_tree["columns"]
        assert page.buyer_account_tree.winfo_exists()
        assert "identity" in page.buyer_account_tree["columns"]
        assert "nickname" in page.buyer_account_tree["columns"]
        assert "remark" in page.buyer_account_tree["columns"]
        assert "switch_state" in page.buyer_account_tree["columns"]
        assert "session" in page.buyer_account_tree["columns"]
        assert page.wechat_client_check_button.cget("text") == "检查微信客户端"
        assert page.buyer_profile_edit_button.cget("text") == "编辑买家资料"
        assert page.buyer_identity_confirm_button.cget("text") == "人工确认身份"
        assert page.buyer_switch_history_button.cget("text") == "切换历史"
        assert page.wechat_switch_help_button.cget("text") == "官方切换说明"
        assert page.buyer_switch_start_button.cget("text") == "开始切换账号"
        assert page.buyer_switch_complete_button.cget("text") == "完成切换确认"
        assert page.buyer_switch_cancel_button.cget("text") == "取消切换"
        assert page.official_product_button.cget("text") == "打开官方商品链接"
        assert page.buyer_task_events_button.cget("text") == "任务事件日志"
        assert page.buyer_revalidate_button.cget("text") == "重新核验价格库存"
        assert page.buyer_run_tree.winfo_exists()
        assert "order" in page.buyer_run_tree["columns"]
        assert "order_status" in page.buyer_run_tree["columns"]
        page.section_buttons["orders"].invoke()
        window.update_idletasks()
        assert page.section_title_var.get() == "订单与售后"

        store_id = window.base_db.add_store(
            "动销界面测试店铺",
            "wx-dongxiao-ui-test",
            "x" * 32,
        )
        store = window.base_db.get_store(store_id)
        assert store is not None
        page._stores = [store]
        page.store_var.set(f"{store.name} · {store.app_id}")
        started = threading.Event()
        release = threading.Event()

        class BlockingCatalogService:
            @staticmethod
            def sync_store(_store, **_kwargs):
                started.set()
                release.wait(2)
                return CatalogSyncResult(
                    store_id=store.id,
                    discovered=0,
                    products_saved=0,
                    failed=0,
                    source=DataSource.MOCK,
                    last_synced_at="2026-11-18T10:00:00+00:00",
                )

        page.official_catalog_service = BlockingCatalogService()
        page._load_products = lambda: None
        with mock.patch(
            "weimi_manager.ui.dongxiao_page.messagebox.showinfo"
        ):
            page._sync_products()
            assert started.wait(1)
            page._sync_products()
            assert str(page.sync_products_button["state"]) == "disabled"
            window.show_page("stores")
            window.update_idletasks()
            release.set()
            deadline = time.monotonic() + 3
            scope = f"dongxiao:sync-products:{store.id}"
            while (
                window.has_running_task(scope)
                and time.monotonic() < deadline
            ):
                window.update()
                time.sleep(0.01)
        assert window._active_page == "stores"
        assert not window.has_running_task(scope)
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
