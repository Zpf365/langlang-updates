from __future__ import annotations

import gc
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
CHROME = (
    ROOT
    / "browser_runtime"
    / "chromium-1254"
    / "chrome-win64"
    / "chrome.exe"
)


def _run_in_child(request: pytest.FixtureRequest) -> bool:
    if os.environ.get("LANGLANG_ORDER_CONTEXT_CHILD") == "1":
        return False
    gc.collect()
    environment = os.environ.copy()
    environment["LANGLANG_ORDER_CONTEXT_CHILD"] = "1"
    environment.setdefault("PYTHONIOENCODING", "utf-8")
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            str(request.node.nodeid),
            "--maxfail=1",
        ],
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
        "订单右键菜单 Chromium 验证失败\n"
        f"stdout:\n{completed.stdout[-12000:]}\n"
        f"stderr:\n{completed.stderr[-12000:]}"
    )
    return True


def test_order_context_menu_preserves_selection_and_stays_in_viewport(
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
    server = FrontendServer(
        static_root,
        controller,
        "order-context-token",
    )
    server.start()
    evidence = ROOT / "candidate" / "1.8.98-final" / "evidence"
    evidence.mkdir(parents=True, exist_ok=True)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=True,
                executable_path=str(CHROME),
            )
            try:
                page = browser.new_page(
                    viewport={"width": 1800, "height": 1000}
                )

                def handle_action(route) -> None:
                    payload = json.loads(route.request.post_data or "{}")
                    if str(payload.get("action") or "") != "orders.sync":
                        route.continue_()
                        return
                    route.fulfill(
                        status=200,
                        content_type="application/json",
                        body=json.dumps(
                            {
                                "ok": True,
                                "data": {
                                    "succeeded": 1,
                                    "status_changes": [],
                                },
                            },
                            ensure_ascii=False,
                        ),
                    )

                page.route("**/api/action", handle_action)
                page.goto(
                    f"{server.entry_url}#/orders",
                    wait_until="domcontentloaded",
                    timeout=90_000,
                )
                rows = page.locator(".order-a-row")
                rows.first.wait_for(timeout=30_000)
                assert rows.count() >= 3
                rows.nth(0).locator("input[type=checkbox]").check()
                rows.nth(1).locator("input[type=checkbox]").check()

                rows.nth(0).click(button="right")
                menu = page.locator(".order-context-menu")
                menu.wait_for(timeout=10_000)
                assert menu.locator("header").inner_text() == "已选 2 条订单"
                labels = menu.locator('[role="menuitem"]').all_inner_texts()
                for label in (
                    "选择当前项",
                    "全选当前页",
                    "全选筛选结果",
                    "反选",
                    "取消选择",
                    "刷新所选订单",
                    "打开待处理",
                    "复制订单号",
                    "批量标记",
                    "批量取消标记",
                ):
                    assert label in labels
                assert rows.nth(0).locator(
                    "input[type=checkbox]"
                ).is_checked()
                assert rows.nth(1).locator(
                    "input[type=checkbox]"
                ).is_checked()
                page.screenshot(
                    path=evidence / "order-context-menu.png",
                    full_page=False,
                )

                menu.get_by_role(
                    "menuitem",
                    name="复制订单号",
                    exact=True,
                ).click()
                page.get_by_text(
                    "已复制 2 个完整订单号",
                    exact=True,
                ).wait_for(timeout=10_000)

                rows.nth(0).click(button="right")
                menu.wait_for(timeout=10_000)
                menu.get_by_role(
                    "menuitem",
                    name="批量标记",
                    exact=True,
                ).click()
                page.get_by_text(
                    "批量标记完成：成功 2",
                    exact=True,
                ).wait_for(timeout=10_000)

                rows.nth(0).click(button="right")
                menu.wait_for(timeout=10_000)
                menu.get_by_role(
                    "menuitem",
                    name="批量取消标记",
                    exact=True,
                ).click()
                page.get_by_text(
                    "批量取消标记完成：成功 2",
                    exact=True,
                ).wait_for(timeout=10_000)

                rows.nth(0).click(button="right")
                menu.wait_for(timeout=10_000)
                page.keyboard.press("Escape")
                menu.wait_for(state="detached")

                rows.nth(2).click(button="right")
                menu.wait_for(timeout=10_000)
                assert menu.locator("header").inner_text() == "已选 1 条订单"
                checked = rows.locator("input[type=checkbox]:checked")
                assert checked.count() == 1
                assert rows.nth(2).locator(
                    "input[type=checkbox]"
                ).is_checked()

                boundary = rows.nth(2).evaluate(
                    """
                    (element) => {
                      const event = new MouseEvent("contextmenu", {
                        bubbles: true,
                        cancelable: true,
                        clientX: window.innerWidth - 2,
                        clientY: window.innerHeight - 2,
                        button: 2,
                      });
                      const prevented = !element.dispatchEvent(event);
                      return {
                        prevented,
                        defaultPrevented: event.defaultPrevented,
                      };
                    }
                    """
                )
                assert boundary == {
                    "prevented": True,
                    "defaultPrevented": True,
                }
                rect = menu.evaluate(
                    """
                    (element) => {
                      const value = element.getBoundingClientRect();
                      return {
                        left: value.left,
                        top: value.top,
                        right: value.right,
                        bottom: value.bottom,
                        width: window.innerWidth,
                        height: window.innerHeight,
                      };
                    }
                    """
                )
                assert rect["left"] >= 0
                assert rect["top"] >= 0
                assert rect["right"] <= rect["width"]
                assert rect["bottom"] <= rect["height"]

                menu.wait_for()
                page.keyboard.press("ArrowDown")
                assert menu.locator(
                    '[role="menuitem"]:focus'
                ).count() == 1
                page.locator(".a-page-head h1").click()
                menu.wait_for(state="detached")
            finally:
                browser.close()
    finally:
        server.stop()
