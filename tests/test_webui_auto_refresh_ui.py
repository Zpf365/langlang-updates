from __future__ import annotations

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


def test_store_and_order_auto_refresh_controls_are_visible() -> None:
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
    server = FrontendServer(static_root, controller, "auto-refresh-ui-token")
    server.start()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=True,
                executable_path=str(CHROME),
            )
            try:
                page = browser.new_page(
                    viewport={"width": 1500, "height": 900},
                    device_scale_factor=1,
                )
                page.goto(
                    f"{server.entry_url}#/shops",
                    wait_until="networkidle",
                )
                page.wait_for_selector(".auto-refresh-toggle")
                stores_toggle = page.locator(
                    ".auto-refresh-toggle"
                ).inner_text()
                stores_title = page.locator(
                    ".auto-refresh-toggle"
                ).get_attribute("title")
                page.goto(
                    f"{server.entry_url}#/orders",
                    wait_until="networkidle",
                )
                page.wait_for_selector(".a-toolbar .auto-refresh-toggle")
                orders_toggle = page.locator(
                    ".a-toolbar .auto-refresh-toggle"
                ).inner_text()
                orders_title = page.locator(
                    ".a-toolbar .auto-refresh-toggle"
                ).get_attribute("title")
                manual_labels = "\n".join(
                    [
                        page.locator(".a-toolbar").inner_text(),
                        page.locator(".a-filterbar").inner_text(),
                    ]
                )
                screenshot_dir = ROOT / "deliveries" / "1.8.77"
                screenshot_dir.mkdir(parents=True, exist_ok=True)
                page.screenshot(
                    path=str(
                        screenshot_dir / "orders-auto-refresh-enabled.png"
                    ),
                    full_page=True,
                )
            finally:
                browser.close()
    finally:
        server.stop()

    assert "自动刷新 开" in stores_toggle
    assert "后刷新" in stores_toggle
    assert "关闭自动刷新" in stores_title
    assert "自动刷新 开" in orders_toggle
    assert "后刷新" in orders_toggle
    assert "关闭自动刷新" in orders_title
    assert "同步订单" in manual_labels
    assert "快速刷新" in manual_labels
