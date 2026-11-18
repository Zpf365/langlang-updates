from __future__ import annotations

import ast
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
APP_SOURCE = PROJECT_ROOT / "weimi_manager" / "ui" / "app.py"
PAGE_SOURCE = PROJECT_ROOT / "weimi_manager" / "ui" / "sales_related_page.py"


def test_sales_related_route_is_registered_and_team_route_is_preserved() -> None:
    source = APP_SOURCE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    class_node = next(
        node for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "MainWindow"
    )
    permissions = next(
        node.value for node in class_node.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name)
            and target.id == "_PAGE_PERMISSIONS"
            for target in node.targets
        )
    )
    permission_keys = {key.value for key in permissions.keys if isinstance(key, ast.Constant)}
    assert "sales_related" in permission_keys
    assert "team" in permission_keys


def test_team_page_remains_in_more_menu_and_new_module_has_requested_label() -> None:
    source = APP_SOURCE.read_text(encoding="utf-8")
    assert '("团队账号", "team", "team_manage")' in source
    assert '("team", "团队账号"' not in source
    assert '("sales_related", "动销相关", "procurement")' in source
    page_source = PAGE_SOURCE.read_text(encoding="utf-8")
    assert 'title = "动销相关"' in page_source
    ast.parse(page_source)


def test_original_dynamic_analysis_route_is_not_renamed_or_removed() -> None:
    source = APP_SOURCE.read_text(encoding="utf-8")
    assert '("dynamic", "动销分析", "operations")' in source
    assert '"dynamic": "products_view"' in source


def test_sales_related_page_exposes_mobile_task_controls() -> None:
    source = PAGE_SOURCE.read_text(encoding="utf-8")
    assert "手机采购任务" in source
    assert "逐条下发到手机" in source
    assert "parse_mobile_task_links" in source
    assert "mobile_sales_list_devices" in source
    assert "mobile_sales_owner_enqueue_task" in source
    assert "mobile_sales_owner_list_tasks" in source
    assert "_poll_mobile_queue" in source
    assert "MobileWechatAutomation" in source
    assert "等待 Codex 按技术任务书实现业务模块" not in source


def test_wechat_shop_links_are_kept_raw_and_ordered_one_per_line() -> None:
    from weimi_manager.ui.sales_related_page import parse_mobile_task_links

    first = "#微信小店://智枢飞恩严选/pGyFgaFSyJha3pJ"
    second = "#微信小店://智枢飞恩严选/tilK50uzEvuYUhf"
    links, invalid = parse_mobile_task_links(
        f"{first}\n\n{second}\n不是链接"
    )

    assert links == [first, second]
    assert invalid == [
        (4, "每行必须是原始微信小店链接或 HTTPS 链接")
    ]


def test_only_an_unexpired_mobile_lease_starts_automation() -> None:
    from weimi_manager.ui.sales_related_page import mobile_lease_is_active

    assert mobile_lease_is_active("2099-01-01T00:00:00+00:00")
    assert mobile_lease_is_active("2099-01-01T00:00:00Z")
    assert not mobile_lease_is_active("2000-01-01T00:00:00+00:00")
    assert not mobile_lease_is_active("")


def test_empty_mobile_device_refresh_is_visible_to_user() -> None:
    from weimi_manager.ui.sales_related_page import SalesRelatedPage

    page = object.__new__(SalesRelatedPage)
    page._devices = [{"stale": True}]
    page._device_by_label = {"stale": {"device_id": "old"}}
    page.mobile_tree = mock.Mock()
    page.mobile_tree.get_children.return_value = ()
    page.device_combo = mock.Mock()
    page.device_var = mock.Mock()
    page.device_var.get.return_value = ""
    page.mobile_status_var = mock.Mock()

    page._apply_device_rows([])

    page.mobile_tree.insert.assert_called_once()
    values = page.mobile_tree.insert.call_args.kwargs["values"]
    assert values[0] == "暂无已绑定手机设备"
    page.mobile_status_var.set.assert_called_once_with(
        "刷新完成：未发现已绑定手机设备，请先在安卓端使用同一账号登录绑定"
    )
