"""Verify the aligned upload workflow without another platform write."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from playwright.sync_api import sync_playwright

from tools.prepare_guarantee_upload_test import build_controller
from weimi_manager.webui.server import FrontendServer

OUTPUT = ROOT / "deliveries" / "1.8.92" / "guarantee-upload"
CHROME = (
    ROOT
    / "browser_runtime"
    / "chromium-1254"
    / "chrome-win64"
    / "chrome.exe"
)
SOURCE_ID = "10001573695588"


def main() -> int:
    controller, account = build_controller(copy_database=False)
    server = FrontendServer(ROOT / "webui" / "dist", controller)
    server.start()
    evidence: dict = {
        "account": account.username,
        "source_product_id": SOURCE_ID,
        "platform_write_started": False,
    }
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=True,
                executable_path=str(CHROME),
            )
            try:
                page = browser.new_page(
                    viewport={"width": 1800, "height": 1100}
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
                page.get_by_placeholder("关键词（空格分隔）").fill(
                    SOURCE_ID
                )
                row = page.locator(
                    ".upload-table tbody tr"
                ).filter(has_text=SOURCE_ID).first
                row.wait_for(state="visible", timeout=60_000)
                row.locator(
                    ".row-store-select"
                ).select_option(label="言参冠旺商行")
                row.locator("input[type=checkbox]").check()
                page.screenshot(
                    path=OUTPUT / "ui-workflow-row-assignment.png",
                    full_page=True,
                )
                page.get_by_role(
                    "button",
                    name="分配店铺并创建任务",
                    exact=True,
                ).click()
                page.get_by_text(
                    "每件商品先在上传表中分配店铺",
                    exact=False,
                ).wait_for(timeout=60_000)
                assignment = page.locator(".assignment-row").first
                assignment.wait_for(state="visible", timeout=60_000)
                page.wait_for_timeout(5000)
                evidence["assignment_text"] = (
                    assignment.inner_text()
                )
                evidence["freight_options"] = (
                    assignment.locator("select").nth(0)
                    .locator("option").all_inner_texts()
                )
                evidence["brand_options"] = (
                    assignment.locator("select").nth(1)
                    .locator("option").all_inner_texts()
                )
                evidence["after_sale_options"] = (
                    assignment.locator("select").nth(2)
                    .locator("option").all_inner_texts()
                )
                page.screenshot(
                    path=OUTPUT / "ui-workflow-store-options.png",
                    full_page=True,
                )
                page.get_by_role(
                    "button",
                    name="创建本地任务",
                    exact=True,
                ).click()
                page.wait_for_selector(
                    ".task-item",
                    timeout=60_000,
                )
                page.wait_for_timeout(500)
                page.screenshot(
                    path=OUTPUT / "ui-workflow-task-created.png",
                    full_page=True,
                )
                evidence["task_items"] = [
                    item.inner_text()
                    for item in page.locator(".task-item").all()
                ]
                pending_task = page.locator(
                    ".task-item"
                ).filter(has_text="A-B微信2000").filter(
                    has_text="待执行"
                ).first
                pending_task.locator("input[type=checkbox]").check()
                page.get_by_role(
                    "button",
                    name="开始 / 继续（1）",
                    exact=True,
                ).click()
                risk_copy = page.locator(".risk-copy")
                risk_copy.wait_for(state="visible", timeout=30_000)
                evidence["start_confirmation"] = (
                    risk_copy.inner_text()
                )
                page.screenshot(
                    path=OUTPUT / "ui-workflow-start-confirmation.png",
                    full_page=True,
                )
                page.get_by_role(
                    "button",
                    name="取消",
                    exact=True,
                ).last.click()
            finally:
                browser.close()
    finally:
        server.stop()
    (OUTPUT / "workflow-ui-evidence.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(evidence, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
