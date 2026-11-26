"""Capture Chromium screenshots for the 1.8.93 order acceptance evidence."""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from playwright.sync_api import sync_playwright

from tools.verify_order_platform_1_8_93 import (
    ORDER_ID,
    OUTPUT,
    build_controller,
)
from weimi_manager.web_session import browser_launch_options
from weimi_manager.webui.server import FrontendServer


def main() -> int:
    controller = build_controller()
    controller.dispatch(
        "orders.update-note-state",
        {
            "store_id": 16,
            "order_id": ORDER_ID,
            "scope": "软件备注",
            "note": "",
            "flag": "",
            "sync_store": False,
        },
    )
    server = FrontendServer(ROOT / "webui" / "dist", controller)
    server.start()
    try:
        with sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(
                str(OUTPUT / "chromium-profile"),
                **browser_launch_options(
                    headless=True,
                    viewport={"width": 1920, "height": 1080},
                ),
            )
            try:
                page = (
                    context.pages[0]
                    if context.pages
                    else context.new_page()
                )
                page.goto(
                    f"{server.entry_url}#/orders",
                    wait_until="domcontentloaded",
                    timeout=60000,
                )
                page.wait_for_timeout(2500)
                search = page.locator(
                    'input[placeholder*="订单号、采购单号、物流单号"]'
                ).first
                search.wait_for(state="visible", timeout=60000)
                search.fill(ORDER_ID)
                page.wait_for_timeout(900)
                page.locator(".order-table-scroll").evaluate(
                    "(element) => { element.scrollLeft = element.scrollWidth; }"
                )
                page.wait_for_timeout(300)
                page.screenshot(
                    path=OUTPUT / "order-flag-and-waybill.png",
                    full_page=True,
                )
                page.get_by_title(
                    "查看官方物流轨迹 435458667553701"
                ).click()
                page.wait_for_timeout(1200)
                page.screenshot(
                    path=OUTPUT / "order-logistics-dialog.png",
                    full_page=True,
                )
                page.get_by_role("button", name="关闭", exact=True).last.click()
                page.locator(".order-table-scroll").evaluate(
                    "(element) => { element.scrollLeft = 0; }"
                )
                page.wait_for_timeout(300)
                page.locator('button[title^="定位商品"]').first.click()
                page.wait_for_timeout(1300)
                page.screenshot(
                    path=OUTPUT / "order-product-located.png",
                    full_page=True,
                )
                page.goto(
                    f"{server.entry_url}#/procurement-accounts",
                    wait_until="domcontentloaded",
                    timeout=60000,
                )
                page.wait_for_timeout(1200)
                page.screenshot(
                    path=OUTPUT / "procurement-account-state.png",
                    full_page=True,
                )
            finally:
                context.close()
    finally:
        server.stop()
    print(OUTPUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
