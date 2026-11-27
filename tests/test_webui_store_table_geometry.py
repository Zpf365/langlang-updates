from __future__ import annotations

import gc
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1]
CHROME = (
    ROOT
    / "browser_runtime"
    / "chromium-1254"
    / "chrome-win64"
    / "chrome.exe"
)
PRODUCT_WIDTHS = [64, 465, 190, 140, 130, 110, 110, 110, 110, 110, 100, 330]


def _run_in_child(request: pytest.FixtureRequest) -> bool:
    if os.environ.get("LANGLANG_GEOMETRY_CHILD") == "1":
        return False
    gc.collect()
    environment = os.environ.copy()
    environment["LANGLANG_GEOMETRY_CHILD"] = "1"
    environment.setdefault("PYTHONIOENCODING", "utf-8")
    node_id = str(request.node.nodeid)
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", node_id, "--maxfail=1"],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
        check=False,
    )
    assert completed.returncode == 0, (
        f"Chromium 几何测试失败: {node_id}\n"
        f"stdout:\n{completed.stdout[-12000:]}\n"
        f"stderr:\n{completed.stderr[-12000:]}"
    )
    return True


def test_store_table_headers_and_cells_share_exact_fixed_grid(
    request: pytest.FixtureRequest,
) -> None:
    if _run_in_child(request):
        return
    pytest.importorskip("playwright.sync_api")
    if not CHROME.is_file():
        pytest.skip("bundled Chromium is unavailable")
    static_root = ROOT / "webui" / "dist"
    if not (static_root / "index.html").is_file():
        pytest.skip("webui production build is unavailable")

    from playwright.sync_api import sync_playwright

    from weimi_manager.webui.main import _smoke_controller
    from weimi_manager.webui.server import FrontendServer

    controller, _data_dir = _smoke_controller()
    demo_store = controller.db.list_stores()[0]
    controller.db.update_store(
        demo_store.id,
        demo_store.name,
        demo_store.group_name,
        "s" * 64,
    )
    controller.db.set_setting(
        f"store_metrics_{demo_store.id}",
        json.dumps(
            {
                "recommend_exposure": 16754,
                "recommend_exposure_yesterday": 0,
                "recommend_exposure_7d": 125600,
                "recommend_exposure_count": 21084,
                "recommend_exposure_yesterday_count": 0,
                "recommend_exposure_7d_count": 150000,
                "recommend_exposure_people": 16754,
                "recommend_exposure_yesterday_people": 0,
                "recommend_exposure_7d_people": 125600,
                "product_exposure_today": 16754,
                "product_exposure_yesterday": 0,
                "product_exposure_7d": 125600,
                "product_exposure_count_today": 21084,
                "product_exposure_count_yesterday": 0,
                "product_exposure_count_7d": 150000,
                "_exposure_attempt_status": {
                    "today": "updated",
                    "yesterday": "updated",
                    "seven_days": "updated",
                },
                "_exposure_count_attempt_status": {
                    "today": "updated",
                    "yesterday": "updated",
                    "seven_days": "updated",
                },
                "_exposure_people_attempt_status": {
                    "today": "updated",
                    "yesterday": "updated",
                    "seven_days": "updated",
                },
                "_exposure_updated_at": "2026-11-22 10:00:00",
                "_exposure_source": "官方曝光区间接口",
                "_exposure_period_updated_at": {
                    "today": "2026-11-27 10:00:00",
                    "yesterday": "2026-11-27 10:00:00",
                    "seven_days": "2026-11-27 10:00:00",
                },
                "_exposure_period_sources": {
                    "today": "官方曝光区间接口",
                    "yesterday": "官方曝光区间接口",
                    "seven_days": "官方曝光区间接口",
                },
            },
            ensure_ascii=False,
        ),
    )
    server = FrontendServer(static_root, controller, "table-geometry-token")
    server.start()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=True,
                executable_path=str(CHROME),
            )
            try:
                page = browser.new_page(
                    viewport={"width": 1500, "height": 760},
                    device_scale_factor=1,
                )
                page.goto(
                    f"{server.entry_url}#/shops",
                    wait_until="networkidle",
                )
                page.get_by_role("button", name="完整表格", exact=True).click()
                page.wait_for_selector(".store-table tbody tr td")
                max_scroll = page.locator(
                    ".store-table-card > .table-scroll"
                ).evaluate(
                    "(element) => element.scrollWidth - element.clientWidth"
                )
                mid_scroll = min(500, max_scroll)
                scroll_positions = sorted({0, mid_scroll, max_scroll})
                geometries = {}
                for scroll_left in scroll_positions:
                    page.locator(
                        ".store-table-card > .table-scroll"
                    ).evaluate(
                        "(element, offset) => element.scrollLeft = offset",
                        scroll_left,
                    )
                    page.wait_for_timeout(100)
                    geometries[scroll_left] = page.evaluate(
                    """
                    () => {
                      const rect = (element) => {
                        const value = element.getBoundingClientRect();
                        return {
                          left: value.left,
                          right: value.right,
                          width: value.width,
                          text: (element.textContent || "").trim(),
                        };
                      };
                      const table = document.querySelector(".store-table");
                      const scroll = document.querySelector(
                        ".store-table-card > .table-scroll"
                      );
                      const scrollRect = scroll.getBoundingClientRect();
                      const firstRow = Array.from(table.tHead.rows[0].cells);
                      const secondRow = Array.from(table.tHead.rows[1].cells);
                      const bodyCells = Array.from(
                        table.tBodies[0].rows[0].cells
                      );
                      const columns = [];
                      let columnIndex = 0;
                      for (const cell of firstRow) {
                        const span = Number(cell.colSpan || 1);
                        if (span === 1) {
                          columns.push({
                            columnIndex,
                            header: rect(cell),
                            body: rect(bodyCells[columnIndex]),
                          });
                        }
                        columnIndex += span;
                      }
                      const exposureGroup = rect(firstRow[3]);
                      const exposure = [0, 1, 2].map((offset) => ({
                        columnIndex: 3 + offset,
                        group: exposureGroup,
                        header: rect(secondRow[offset]),
                        body: rect(bodyCells[3 + offset]),
                      }));
                      const salesLines = [8, 9, 10, 11].flatMap(
                        (columnIndex) => Array.from(
                          bodyCells[columnIndex].querySelectorAll(
                                        ".store-sale-line, .store-refund-line"
                          )
                        ).map((line) => ({
                          text: (line.textContent || "").trim(),
                          fontSize: getComputedStyle(line).fontSize,
                          scrollWidth: line.scrollWidth,
                          clientWidth: line.clientWidth,
                        }))
                      );
                      return {
                        scrollLeft: scroll.scrollLeft,
                        scrollLeftEdge: scrollRect.left,
                        columns,
                        exposure,
                        salesLines,
                      };
                    }
                    """
                )
                screenshot_dir = ROOT / "deliveries" / "1.9.01.1"
                screenshot_dir.mkdir(parents=True, exist_ok=True)
                page.locator(
                    ".store-table-card > .table-scroll"
                ).evaluate("(element) => element.scrollLeft = 0")
                page.wait_for_timeout(100)
                page.screenshot(
                    path=str(
                        screenshot_dir
                        / "store-table-exposure-count-people.png"
                    ),
                    full_page=True,
                )
                page.locator(
                    ".store-table-card > .table-scroll"
                ).evaluate(
                    "(element, offset) => element.scrollLeft = offset",
                    mid_scroll,
                )
                page.wait_for_timeout(100)
                page.screenshot(
                    path=str(screenshot_dir / "store-table-sticky-expanded.png"),
                    full_page=True,
                )
                page.locator(
                    ".store-table-card > .table-scroll"
                ).evaluate(
                    "(element, offset) => element.scrollLeft = offset",
                    max_scroll,
                )
                page.wait_for_timeout(100)
                page.screenshot(
                    path=str(
                        screenshot_dir / "store-table-identity-right.png"
                    ),
                    full_page=True,
                )
                collapse_width = page.evaluate(
                    """() => document.querySelectorAll(
                      '.store-table tbody tr:first-child td'
                    )[20].getBoundingClientRect().width"""
                )
                page.locator(".secret-show").first.click()
                page.wait_for_function(
                    """() => document.querySelector(
                      '.secret-show'
                    )?.textContent.trim() === 'hide'"""
                )
                expanded = page.evaluate(
                    """
                    () => {
                      const cell = document.querySelector(
                        '.store-table tbody tr .secret-cell'
                      );
                      const button = cell.querySelector('.secret-show');
                      const header = Array.from(
                        document.querySelectorAll(
                          '.store-table thead tr:first-child th'
                        )
                      ).find(
                        (item) => item.textContent.trim() === 'AppSecret'
                      );
                      const cellRect = cell.getBoundingClientRect();
                      const buttonRect = button.getBoundingClientRect();
                      const headerRect = header.getBoundingClientRect();
                      return {
                        cellWidth: cellRect.width,
                        buttonText: button.textContent.trim(),
                        buttonVisible:
                          buttonRect.left >= cellRect.left - 1 &&
                          buttonRect.right <= cellRect.right + 1,
                        alignment:
                          Math.abs(headerRect.left - cellRect.left) +
                          Math.abs(headerRect.right - cellRect.right),
                      };
                    }
                    """
                )
                page.locator(".secret-show").first.click()
                page.wait_for_function(
                    """() => document.querySelector(
                      '.secret-show'
                    )?.textContent.trim() === 'show'"""
                )
                restored_width = page.evaluate(
                    """() => document.querySelectorAll(
                      '.store-table tbody tr:first-child td'
                    )[20].getBoundingClientRect().width"""
                )
            finally:
                browser.close()
    finally:
        server.stop()

    geometry = geometries[mid_scroll]
    assert geometry["scrollLeft"] == mid_scroll
    assert geometry["columns"][2]["header"]["text"] == "店铺名称"
    columns_by_index = {
        item["columnIndex"]: item
        for item in geometry["columns"]
    }
    assert columns_by_index[19]["header"]["text"] == "AppID"
    assert columns_by_index[20]["header"]["text"] == "AppSecret"
    assert columns_by_index[21]["header"]["text"] == "登录状态"
    assert geometry["exposure"][0]["body"]["text"] == "1.67万+/2.1万+"
    for scroll_left, snapshot in geometries.items():
        assert snapshot["scrollLeft"] == scroll_left
        name_header = snapshot["columns"][2]["header"]
        name_body = snapshot["columns"][2]["body"]
        expected_name_left = snapshot["scrollLeftEdge"] + 98
        assert abs(name_header["left"] - expected_name_left) <= 1
        assert abs(name_body["left"] - expected_name_left) <= 1
        assert abs(name_header["right"] - name_body["right"]) <= 1
    assert 340 <= expanded["cellWidth"] <= 560
    assert expanded["buttonText"] == "hide"
    assert expanded["buttonVisible"] is True
    assert expanded["alignment"] <= 1
    assert abs(collapse_width - restored_width) <= 1
    assert len(geometry["columns"]) == 16
    assert geometry["salesLines"]
    assert all(
        line["fontSize"] in {"11px", "14px"}
        and line["scrollWidth"] <= line["clientWidth"] + 1
        for line in geometry["salesLines"]
    )
    for item in geometry["columns"]:
        assert abs(item["header"]["left"] - item["body"]["left"]) <= 1
        assert abs(item["header"]["right"] - item["body"]["right"]) <= 1
        assert item["header"]["width"] > 0
        assert item["body"]["width"] > 0

    group = geometry["exposure"][0]["group"]
    assert len(geometry["exposure"]) == 3
    for item in geometry["exposure"]:
        assert abs(item["header"]["left"] - item["body"]["left"]) <= 1
        assert abs(item["header"]["right"] - item["body"]["right"]) <= 1
    assert abs(group["left"] - geometry["exposure"][0]["header"]["left"]) <= 1
    assert abs(group["right"] - geometry["exposure"][-1]["header"]["right"]) <= 1


def test_store_overview_has_no_horizontal_scroll_and_expands_details(
    request: pytest.FixtureRequest,
) -> None:
    if _run_in_child(request):
        return
    pytest.importorskip("playwright.sync_api")
    if not CHROME.is_file():
        pytest.skip("bundled Chromium is unavailable")
    static_root = ROOT / "webui" / "dist"
    if not (static_root / "index.html").is_file():
        pytest.skip("webui production build is unavailable")

    from playwright.sync_api import sync_playwright

    from weimi_manager.webui.main import _smoke_controller
    from weimi_manager.webui.server import FrontendServer

    controller, _data_dir = _smoke_controller()
    store = controller.db.list_stores()[0]
    controller.db.set_setting(
        f"store_metrics_{store.id}",
        json.dumps(
            {
                "gmv_today": 5300,
                "today_orders": 1,
                "gmv_yesterday": 6200,
                "orders_yesterday": 2,
                "gmv_7d": 7300,
                "orders_7d": 3,
                "gmv_30d": 8400,
                "orders_30d": 4,
                "refund_gmv_today": 0,
                "refund_gmv_yesterday": 200,
                "refund_gmv_7d": 300,
                "refund_gmv_30d": 400,
                "product_exposure_today": 16754,
                "product_exposure_yesterday": 0,
                "product_exposure_7d": 125600,
                "product_exposure_count_today": 21084,
                "product_exposure_count_yesterday": 0,
                "product_exposure_count_7d": 150000,
                "_refund_order_counts": {
                    "today": 0,
                    "yesterday": 1,
                    "seven_days": 2,
                    "thirty_days": 3,
                },
                "_refund_order_cache": {
                    "status": "fresh",
                    "updated_at": "2026-11-23 11:00:00",
                    "hint": "",
                },
                "_sales_attempt_status": {
                    "today": "updated",
                    "yesterday": "updated",
                    "seven_days": "updated",
                    "thirty_days": "updated",
                },
                "_sales_period_sources": {
                    "today": "微信小店实时成交统计",
                    "yesterday": "微信小店后台昨日成交统计",
                    "seven_days": "微信小店后台近7天成交统计",
                    "thirty_days": "微信小店后台近30天成交统计",
                },
                "_exposure_period_updated_at": {
                    "today": "2026-11-27 10:00:00",
                    "yesterday": "2026-11-27 10:00:00",
                    "seven_days": "2026-11-27 10:00:00",
                },
                "_exposure_period_sources": {
                    "today": "官方商品转化接口",
                    "yesterday": "官方商品转化接口",
                    "seven_days": "官方商品转化接口",
                },
            },
            ensure_ascii=False,
        ),
    )
    server = FrontendServer(
        static_root,
        controller,
        "store-overview-token",
    )
    server.start()
    snapshots = {}
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=True,
                executable_path=str(CHROME),
            )
            try:
                page = browser.new_page(device_scale_factor=1)
                page.goto(
                    f"{server.entry_url}#/shops",
                    wait_until="domcontentloaded",
                    timeout=60_000,
                )
                page.wait_for_selector(".store-overview-table tbody tr")
                for width in (1440, 1366, 1100, 860):
                    page.set_viewport_size({"width": width, "height": 820})
                    page.wait_for_timeout(120)
                    snapshots[width] = page.evaluate(
                        """
                        () => {
                          const scroll = document.querySelector(
                            ".overview-table-scroll"
                          );
                          const table = document.querySelector(
                            ".store-overview-table"
                          );
                          const first = document.querySelector(
                            ".overview-main-row"
                          );
                          const second = document.querySelectorAll(
                            ".overview-main-row"
                          )[1];
                          const appId = first.cells[8];
                          const secret = first.cells[9];
                          const login = first.cells[10];
                          const action = first.cells[11];
                          const salesGeometry = (row) => Array.from(
                            row.querySelectorAll(".overview-sales-cell")
                          ).map((cell) => {
                            const value = cell.getBoundingClientRect();
                            const lines = cell.querySelector(".store-sales-value");
                            const linesValue = lines.getBoundingClientRect();
                            const lineStyles = Array.from(
                              cell.querySelectorAll(
                                ".store-sale-line, .store-refund-line"
                              )
                            ).map((line) => ({
                              fontSize: getComputedStyle(line).fontSize,
                              scrollWidth: line.scrollWidth,
                              clientWidth: line.clientWidth,
                            }));
                            return {
                              text: cell.innerText.trim(),
                              hasRefund: Boolean(
                                cell.querySelector(".store-refund-line")
                              ),
                              left: value.left,
                              right: value.right,
                              cellHeight: value.height,
                              linesHeight: linesValue.height,
                              single: lines.classList.contains("single-line"),
                              lineStyles,
                            };
                          });
                          return {
                            scrollWidth: scroll.scrollWidth,
                            clientWidth: scroll.clientWidth,
                            tableWidth: table.getBoundingClientRect().width,
                            appIdLeft: appId.getBoundingClientRect().left,
                            secretLeft: secret.getBoundingClientRect().left,
                            loginLeft: login.getBoundingClientRect().left,
                            actionLeft: action.getBoundingClientRect().left,
                            salesText: Array.from(
                              first.querySelectorAll(".overview-sales-cell")
                            ).map((cell) => cell.innerText.trim()),
                            firstSales: salesGeometry(first),
                            secondSales: salesGeometry(second),
                          };
                        }
                        """
                    )
                page.set_viewport_size({"width": 1440, "height": 820})
                page.wait_for_timeout(120)
                screenshot_dir = ROOT / "deliveries" / "1.9.01.1"
                screenshot_dir.mkdir(parents=True, exist_ok=True)
                for screenshot_width in (1366, 1920):
                    page.set_viewport_size(
                        {"width": screenshot_width, "height": 820}
                    )
                    page.wait_for_timeout(120)
                    page.screenshot(
                        path=str(
                            screenshot_dir
                            / (
                                "store-overview-refund-"
                                f"{screenshot_width}.png"
                            )
                        ),
                        full_page=True,
                    )
                page.set_viewport_size({"width": 1440, "height": 820})
                page.wait_for_timeout(120)
                page.get_by_role("button", name="详情", exact=True).first.click()
                page.wait_for_selector(".overview-details-row")
                expanded = page.evaluate(
                    """
                    () => ({
                      detailVisible: Boolean(
                        document.querySelector(".overview-details-row")
                      ),
                      hasExposure: document.querySelector(
                        ".overview-details-row"
                      ).innerText.includes("商品曝光"),
                      hasFunds: document.querySelector(
                        ".overview-details-row"
                      ).innerText.includes("资金与保证金"),
                      hasScores: document.querySelector(
                        ".overview-details-row"
                      ).innerText.includes("体验分"),
                    })
                    """
                )
                page.screenshot(
                    path=str(
                        screenshot_dir
                        / "store-overview-details-1440.png"
                    ),
                    full_page=True,
                )
                page.get_by_role("button", name="收起", exact=True).first.click()
                page.wait_for_selector(
                    ".overview-details-row",
                    state="detached",
                )
                collapsed = page.locator(".overview-details-row").count()
                rows = page.locator(".overview-main-row")
                for index in range(3):
                    rows.nth(index).locator(
                        "input[type=checkbox]"
                    ).check()
                context_result = rows.nth(2).evaluate(
                    """
                    (element) => {
                      const event = new MouseEvent("contextmenu", {
                        bubbles: true,
                        cancelable: true,
                        clientX: 460,
                        clientY: 360,
                        button: 2,
                      });
                      const dispatchResult = element.dispatchEvent(event);
                      return {
                        defaultPrevented: event.defaultPrevented,
                        dispatchResult,
                      };
                    }
                    """
                )
                page.wait_for_selector(".store-context-menu")
                context_menu = page.evaluate(
                    """
                    () => {
                      const menu = document.querySelector(
                        ".store-context-menu"
                      );
                      const rect = menu.getBoundingClientRect();
                      return {
                        title: menu.querySelector("header").innerText.trim(),
                        items: Array.from(
                          menu.querySelectorAll('[role="menuitem"]')
                        ).map((item) => item.innerText.trim()),
                        left: rect.left,
                        top: rect.top,
                        right: rect.right,
                        bottom: rect.bottom,
                        viewportWidth: window.innerWidth,
                        viewportHeight: window.innerHeight,
                      };
                    }
                    """
                )
                page.keyboard.press("Escape")
                page.wait_for_selector(
                    ".store-context-menu",
                    state="detached",
                )
            finally:
                browser.close()
    finally:
        server.stop()

    for width, snapshot in snapshots.items():
        assert snapshot["scrollWidth"] <= snapshot["clientWidth"] + 1, width
        assert snapshot["tableWidth"] <= snapshot["clientWidth"] + 1, width
        assert len(snapshot["salesText"]) == 4
        assert snapshot["firstSales"][0]["hasRefund"] is False
        assert snapshot["firstSales"][0]["single"] is True
        assert snapshot["firstSales"][0]["text"] == "53/1"
        assert snapshot["firstSales"][1]["hasRefund"] is True
        assert snapshot["firstSales"][1]["single"] is False
        assert "2/1" in snapshot["firstSales"][1]["text"]
        for first_cell, second_cell in zip(
            snapshot["firstSales"],
            snapshot["secondSales"],
        ):
            assert abs(first_cell["left"] - second_cell["left"]) <= 1
            assert abs(first_cell["right"] - second_cell["right"]) <= 1
            assert abs(first_cell["cellHeight"] - second_cell["cellHeight"]) <= 1
            assert first_cell["linesHeight"] >= 29
            for line in (
                first_cell["lineStyles"]
                + second_cell["lineStyles"]
            ):
                assert line["fontSize"] in {"11px", "13px", "14px"}
                assert line["scrollWidth"] <= line["clientWidth"] + 1
    assert snapshots[1440]["appIdLeft"] < snapshots[1440]["secretLeft"]
    assert snapshots[1440]["secretLeft"] < snapshots[1440]["loginLeft"]
    assert snapshots[1440]["loginLeft"] < snapshots[1440]["actionLeft"]
    assert expanded == {
        "detailVisible": True,
        "hasExposure": True,
        "hasFunds": True,
        "hasScores": True,
    }
    assert collapsed == 0
    assert context_result == {
        "defaultPrevented": True,
        "dispatchResult": False,
    }
    assert context_menu["title"] == "已选 3 家店铺"
    assert context_menu["items"] == [
        "选择当前项",
        "全选当前页",
        "全选筛选结果",
        "反选",
        "取消选择",
        "刷新选中项",
        "复制选中项编号",
        "批量登录",
        "批量设置分组…",
        "新建分组…",
        "打开后台",
        "批量删除所选…",
    ]
    assert context_menu["left"] >= 0
    assert context_menu["top"] >= 0
    assert context_menu["right"] <= context_menu["viewportWidth"]
    assert context_menu["bottom"] <= context_menu["viewportHeight"]


def test_product_table_headers_and_cells_share_exact_fixed_grid(
    request: pytest.FixtureRequest,
) -> None:
    if _run_in_child(request):
        return
    pytest.importorskip("playwright.sync_api")
    if not CHROME.is_file():
        pytest.skip("bundled Chromium is unavailable")
    static_root = ROOT / "webui" / "dist"
    if not (static_root / "index.html").is_file():
        pytest.skip("webui production build is unavailable")

    from playwright.sync_api import sync_playwright

    from weimi_manager.models import Product
    from weimi_manager.webui.main import _smoke_controller
    from weimi_manager.webui.server import FrontendServer

    controller, _data_dir = _smoke_controller()
    store = controller.db.list_stores()[0]
    controller.db.upsert_product(
        Product(
            store.id,
            store.name,
            "product-grid",
            "表格几何测试商品",
            5,
            "销售中",
            0,
            1990,
            1,
            10,
            "SPU-GRID",
            "OUT-GRID",
            "",
            {"total_sold_num": 12},
        )
    )
    controller.db.upsert_product_metrics(
        store.id,
        "product-grid",
        views30=123,
        sold30=8,
        recommend_views30=99,
    )
    server = FrontendServer(static_root, controller, "product-grid-token")
    server.start()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=True,
                executable_path=str(CHROME),
            )
            try:
                page = browser.new_page(
                    viewport={"width": 1780, "height": 1000},
                    device_scale_factor=1,
                )
                page.goto(
                    f"{server.entry_url}#/managed-products",
                    wait_until="domcontentloaded",
                    timeout=60000,
                )
                page.wait_for_selector(".product-table tbody tr td")
                page.wait_for_function(
                    """() => (
                      document.querySelector(
                        '.product-table tbody tr'
                      )?.cells.length || 0
                    ) >= 12"""
                )
                geometry = page.evaluate(
                    """
                    () => {
                      const rect = (element) => {
                        const value = element.getBoundingClientRect();
                        return {
                          left: value.left,
                          right: value.right,
                          width: value.width,
                        };
                      };
                      const table = document.querySelector(".product-table");
                      const firstRow = Array.from(table.tHead.rows[0].cells);
                      const secondRow = Array.from(table.tHead.rows[1].cells);
                      const bodyCells = Array.from(
                        table.tBodies[0].rows[0].cells
                      );
                      const columns = [];
                      let columnIndex = 0;
                      for (const cell of firstRow) {
                        const span = Number(cell.colSpan || 1);
                        if (span === 1) {
                          columns.push({
                            columnIndex,
                            header: rect(cell),
                            body: rect(bodyCells[columnIndex]),
                          });
                        }
                        columnIndex += span;
                      }
                      const group = rect(firstRow[6]);
                      const metrics = [0, 1, 2].map((offset) => ({
                        columnIndex: 6 + offset,
                        header: rect(secondRow[offset]),
                        body: rect(bodyCells[6 + offset]),
                      }));
                      return {columns, group, metrics};
                    }
                    """
                )
            finally:
                browser.close()
    finally:
        server.stop()

    assert len(geometry["columns"]) == 9
    for item in geometry["columns"]:
        assert abs(item["header"]["left"] - item["body"]["left"]) <= 1
        assert abs(item["header"]["right"] - item["body"]["right"]) <= 1
        expected = PRODUCT_WIDTHS[item["columnIndex"]]
        assert abs(item["header"]["width"] - expected) <= 1
        assert abs(item["body"]["width"] - expected) <= 1
    group = geometry["group"]
    assert abs(group["left"] - geometry["metrics"][0]["header"]["left"]) <= 1
    assert abs(group["right"] - geometry["metrics"][-1]["header"]["right"]) <= 1


def test_product_operation_log_collapses_without_overlap_and_restores_width(
    request: pytest.FixtureRequest,
) -> None:
    if _run_in_child(request):
        return
    pytest.importorskip("playwright.sync_api")
    if not CHROME.is_file():
        pytest.skip("bundled Chromium is unavailable")
    static_root = ROOT / "webui" / "dist"
    if not (static_root / "index.html").is_file():
        pytest.skip("webui production build is unavailable")

    from playwright.sync_api import sync_playwright

    from weimi_manager.models import Product
    from weimi_manager.webui.main import _smoke_controller
    from weimi_manager.webui.server import FrontendServer

    controller, _data_dir = _smoke_controller()
    store = controller.db.list_stores()[0]
    controller.db.upsert_product(
        Product(
            store.id,
            store.name,
            "product-log-layout",
            "操作日志折叠布局测试商品",
            5,
            "销售中",
            0,
            1990,
            1,
            10,
            "SPU-LOG",
            "OUT-LOG",
            "",
            {"total_sold_num": 12},
        )
    )
    for index in range(12):
        controller._log_product_operation(
            store,
            "product-log-layout",
            "操作日志折叠布局测试商品",
            "删除",
            "success",
        )
    server = FrontendServer(
        static_root,
        controller,
        "product-log-layout-token",
    )
    server.start()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=True,
                executable_path=str(CHROME),
            )
            try:
                page = browser.new_page(
                    viewport={"width": 1780, "height": 1000},
                    device_scale_factor=1,
                )
                page.goto(
                    f"{server.entry_url}#/managed-products",
                    wait_until="networkidle",
                )
                page.wait_for_selector(".product-table tbody tr td")
                page.wait_for_selector(".operation-log-list article")
                expanded = page.evaluate(
                    """
                    () => {
                      const rect = (selector) => {
                        const value = document.querySelector(
                          selector
                        ).getBoundingClientRect();
                        return {
                          left: value.left,
                          right: value.right,
                          top: value.top,
                          bottom: value.bottom,
                          width: value.width,
                        };
                      };
                      return {
                        grid: rect(".product-content-grid"),
                        products: rect(".product-list-card"),
                        logs: rect(".operation-log"),
                        pagination: rect(".product-pagination"),
                      };
                    }
                    """
                )
                page.locator(".operation-log-list").evaluate(
                    "(element) => element.scrollTop = 120"
                )
                log_scroll = page.locator(
                    ".operation-log-list"
                ).evaluate("(element) => element.scrollTop")
                screenshot_dir = ROOT / "deliveries" / "1.8.77"
                screenshot_dir.mkdir(parents=True, exist_ok=True)
                page.screenshot(
                    path=str(
                        screenshot_dir
                        / "product-operation-log-expanded.png"
                    ),
                    full_page=True,
                )
                page.locator(".operation-log-collapse").click()
                page.wait_for_timeout(260)
                collapsed = page.evaluate(
                    """
                    () => {
                      const rect = (selector) => {
                        const value = document.querySelector(
                          selector
                        ).getBoundingClientRect();
                        return {
                          left: value.left,
                          right: value.right,
                          top: value.top,
                          bottom: value.bottom,
                          width: value.width,
                        };
                      };
                      return {
                        className: document.querySelector(
                          ".product-content-grid"
                        ).className,
                        logVisibility: getComputedStyle(
                          document.querySelector(".operation-log")
                        ).visibility,
                        grid: rect(".product-content-grid"),
                        products: rect(".product-list-card"),
                        expand: rect(".operation-log-expand"),
                        pagination: rect(".product-pagination"),
                      };
                    }
                    """
                )
                page.screenshot(
                    path=str(
                        screenshot_dir
                        / "product-operation-log-collapsed.png"
                    ),
                    full_page=True,
                )
                page.locator(".operation-log-expand").click()
                page.wait_for_timeout(260)
                restored = page.evaluate(
                    """
                    () => {
                      const value = document.querySelector(
                        ".product-list-card"
                      ).getBoundingClientRect();
                      return {
                        width: value.width,
                        logVisibility: getComputedStyle(
                          document.querySelector(".operation-log")
                        ).visibility,
                        scrollTop: document.querySelector(
                          ".operation-log-list"
                        ).scrollTop,
                      };
                    }
                    """
                )
            finally:
                browser.close()
    finally:
        server.stop()

    assert expanded["products"]["right"] <= expanded["logs"]["left"] + 1
    assert "logs-collapsed" in collapsed["className"]
    assert collapsed["logVisibility"] == "hidden"
    assert collapsed["products"]["width"] >= collapsed["grid"]["width"] - 50
    assert collapsed["products"]["right"] <= collapsed["expand"]["left"] + 1
    assert collapsed["pagination"]["right"] <= collapsed["grid"]["right"] + 1
    assert collapsed["pagination"]["bottom"] <= collapsed["grid"]["bottom"] + 1
    assert abs(restored["width"] - expanded["products"]["width"]) <= 1
    assert restored["logVisibility"] == "visible"
    assert restored["scrollTop"] == log_scroll
