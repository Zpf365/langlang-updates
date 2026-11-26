"""Verify the non-order 1.8.95 WebUI changes against a temporary database."""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from playwright.sync_api import sync_playwright

from weimi_manager.db import Database
from weimi_manager.webui.controller import NextUiController
from weimi_manager.webui.server import FrontendServer


class _Client:
    def fetch_freight_template_ids(self) -> list[str]:
        return [
            "1103871390004",
            "1103871380004",
            "1103871366004",
            "1103871356004",
        ]

    def fetch_freight_template_detail(self, template_id: str) -> dict[str, str]:
        return {
            "template_id": template_id,
            "template_name": template_id,
        }


class _ProductService:
    def client_for(self, _store: Any) -> _Client:
        return _Client()

    def _product_payload_defaults(
        self,
        _store: Any,
        _client: _Client,
    ) -> dict[str, Any]:
        return {
            "express_info": {"template_id": "1103871390004"},
            "after_sale_info": {
                "after_sale_address_id": "address-candidate",
            },
        }

    def refresh_store_metrics(self, store: Any) -> dict[str, Any]:
        return {
            "_sales_attempt_status": {"today": "updated"},
            "_sales_period_sources": {"today": "候选只读测试来源"},
            "_sales_updated_at": "2026-11-25 12:00:00",
            "_sales_is_stale": False,
            "gmv_today": 0,
            "today_orders": 0,
            "refund_gmv_today": 0,
            "refund_orders_today": 0,
            "in_sale": 0,
            "_online_product_source": "候选只读测试来源",
        }


def _seed(database: Database) -> None:
    database.add_store("允竹努胜小店", "wx-candidate-a", "secret")
    database.add_store("赤影云册精选", "wx-candidate-b", "secret")
    database.add_store("言参冠旺商行", "wx-candidate-c", "secret")
    database.set_store_groups(
        [1, 2, 3],
        "候选测试分组",
    )
    database.set_setting(
        "upload_batch",
        json.dumps(
            [
                {
                    "batch_id": "candidate-batch-1",
                    "source_product_id": "CANDIDATE-001",
                    "title": "候选上货店铺分配验证商品",
                    "price": 1680,
                    "stock": 18,
                    "platform": "1688",
                    "source_file": "candidate.json",
                    "payload": {
                        "title": "候选上货店铺分配验证商品",
                        "main_imgs": ["https://example.invalid/main.jpg"],
                        "skus": [
                            {
                                "sku_id": "SKU-1",
                                "sale_price": 1680,
                                "stock_num": 18,
                            }
                        ],
                    },
                }
            ],
            ensure_ascii=False,
        ),
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--frontend",
        type=Path,
        default=ROOT / "webui" / "dist",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "candidate" / "1.8.97" / "evidence",
    )
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    chrome = (
        ROOT
        / "browser_runtime"
        / "chromium-1254"
        / "chrome-win64"
        / "chrome.exe"
    )
    result: dict[str, Any] = {}
    with tempfile.TemporaryDirectory(prefix="langlang-1895-") as temp:
        database = Database(Path(temp) / "candidate.db")
        _seed(database)
        controller = NextUiController(
            database,
            account_label="候选自动验证",
            product_service=_ProductService(),
        )
        controller.dispatch(
            "upload.templates.save",
            {
                "template": {
                    "name": "候选验证模板",
                    "duplicate_filter_mode": "store",
                }
            },
        )
        server = FrontendServer(args.frontend.resolve(), controller)
        server.start()
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(
                    headless=True,
                    executable_path=str(chrome),
                )
                try:
                    page = browser.new_page(
                        viewport={"width": 1800, "height": 1120}
                    )
                    page.goto(
                        f"{server.entry_url}#/product-upload",
                        wait_until="networkidle",
                        timeout=90_000,
                    )
                    page.get_by_role(
                        "button",
                        name="上传商品",
                        exact=True,
                    ).click()
                    page.get_by_text(
                        "候选上货店铺分配验证商品",
                        exact=False,
                    ).wait_for(timeout=30_000)
                    result["old_row_store_selector"] = (
                        page.locator(".row-store-select").count()
                    )
                    page.locator(
                        ".upload-table tbody tr input[type=checkbox]"
                    ).first.check()
                    page.get_by_role(
                        "button",
                        name="选择店铺并创建任务",
                        exact=True,
                    ).click()
                    dialog = page.get_by_role(
                        "dialog",
                        name="选择分配店铺",
                    )
                    dialog.wait_for(timeout=30_000)
                    page.wait_for_timeout(1200)
                    first_row = page.locator(
                        ".upload-store-row"
                    ).first
                    result["initial_config_values"] = [
                        first_row.locator("select").nth(index).input_value()
                        for index in range(3)
                    ]
                    page.screenshot(
                        path=args.output / "upload-store-selection.png",
                        full_page=False,
                    )
                    first_row.click(button="right")
                    menu = page.locator(".upload-store-menu")
                    menu.wait_for(timeout=10_000)
                    result["upload_context_menu"] = menu.inner_text()
                    page.screenshot(
                        path=args.output / "upload-store-context-menu.png",
                        full_page=False,
                    )
                    page.keyboard.press("Escape")
                    option_texts = (
                        first_row.locator("select option")
                        .all_inner_texts()
                    )
                    result["store_option_texts"] = option_texts
                    result["numeric_only_options"] = [
                        text
                        for text in option_texts
                        if re.fullmatch(r"\d+", text.strip())
                    ]
                    result["brand_has_no_brand"] = any(
                        "无品牌" in item for item in option_texts
                    )
                    page.locator(".all-stores").click()
                    page.get_by_text(
                        "每个店铺都上传",
                        exact=True,
                    ).click()
                    page.locator(".loaded-line").filter(
                        has_text="3/3 家店铺配置"
                    ).wait_for(timeout=30_000)
                    result["all_selected_config_values"] = [
                        page.locator(".upload-store-row select").nth(index).input_value()
                        for index in range(9)
                    ]
                    page.screenshot(
                        path=args.output / "upload-stores-selected.png",
                        full_page=False,
                    )
                    page.get_by_role(
                        "button",
                        name="生成并开始上传",
                        exact=True,
                    ).click()
                    page.locator(".task-item").first.wait_for(
                        timeout=30_000
                    )
                    page.wait_for_timeout(500)
                    result["task_page_url"] = page.url
                    result["task_items"] = [
                        item.inner_text()
                        for item in page.locator(".task-item").all()
                    ]
                    page.screenshot(
                        path=args.output / "upload-task-after-create.png",
                        full_page=False,
                    )

                    page.goto(
                        f"{server.entry_url}#/shops",
                        wait_until="domcontentloaded",
                        timeout=90_000,
                    )
                    page.locator(".store-page").wait_for(timeout=30_000)
                    page.wait_for_timeout(2200)
                    sales_cells = page.locator(
                        ".overview-sales-cell"
                    )
                    result["sales_cell_titles"] = [
                        sales_cells.nth(index).get_attribute("title")
                        for index in range(sales_cells.count())
                    ]
                    store_row = page.locator(
                        ".overview-main-row"
                    ).first
                    store_row.click(button="right")
                    store_menu = page.locator(".store-context-menu")
                    store_menu.wait_for(timeout=10_000)
                    result["store_context_menu"] = store_menu.inner_text()
                    page.screenshot(
                        path=args.output / "stores-context-menu.png",
                        full_page=False,
                    )
                    page.wait_for_timeout(500)
                    result["store_page_has_fake_service_error"] = (
                        "has no attribute" in page.locator("body").inner_text()
                    )
                    page.goto(
                        f"{server.entry_url}#/activity",
                        wait_until="domcontentloaded",
                        timeout=90_000,
                    )
                    page.get_by_text(
                        "限时限量购活动",
                        exact=True,
                    ).first.click()
                    smart_strip = page.locator(".smart-auto-strip")
                    smart_strip.wait_for(timeout=30_000)
                    result["smart_automation_status"] = (
                        smart_strip.inner_text()
                    )
                    page.screenshot(
                        path=args.output / "activity-smart-automation.png",
                        full_page=False,
                    )
                    page.goto(
                        f"{server.entry_url}#/finance",
                        wait_until="domcontentloaded",
                        timeout=30_000,
                    )
                    page.wait_for_timeout(700)
                    result["finance_route_after_guard"] = page.url.split("#", 1)[-1]
                    page.goto(
                        f"{server.entry_url}#/more",
                        wait_until="domcontentloaded",
                        timeout=30_000,
                    )
                    page.wait_for_timeout(700)
                    result["runtime_logs_route_after_guard"] = page.url.split("#", 1)[-1]
                finally:
                    browser.close()
        finally:
            server.stop()
    target = args.output / "verification.json"
    target.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
