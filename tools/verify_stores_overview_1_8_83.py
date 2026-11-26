"""Exercise the 1.8.83 store overview and right-click batch actions."""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weimi_manager.db import Database  # noqa: E402
from weimi_manager.runtime_logging import (  # noqa: E402
    configure_runtime_logging,
)
from weimi_manager.service import SyncService  # noqa: E402
from weimi_manager.web_session import WechatWebSession  # noqa: E402
from weimi_manager.webui.controller import NextUiController  # noqa: E402
from weimi_manager.webui.server import FrontendServer  # noqa: E402


CANDIDATE = ROOT / "release" / "LangLang_1.8.83_Windows_x64"
DELIVERY = ROOT / "deliveries" / "1.8.83"
WORK = DELIVERY / "overview-real"
DATA_DIR = WORK / "data"
DATABASE_PATH = DATA_DIR / "weimi_manager.db"
SOURCE = (
    Path.home()
    / "AppData"
    / "Local"
    / "LangLang"
    / "accounts"
    / "81a4f9fe921283cdda75468c"
    / "data"
    / "weimi_manager.db"
)


def backup_database() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    source = sqlite3.connect(
        f"{SOURCE.resolve().as_uri()}?mode=ro",
        uri=True,
    )
    destination = sqlite3.connect(DATABASE_PATH)
    try:
        source.backup(destination)
    finally:
        destination.close()
        source.close()


def main() -> int:
    backup_database()
    configure_runtime_logging(DELIVERY / "verification-logs")
    database = Database(DATABASE_PATH)
    stores = database.list_stores()
    if len(stores) < 9:
        raise RuntimeError(f"真实店铺不足 9 家：{len(stores)}")
    controller = NextUiController(
        database,
        account_label="zpfly",
        project_dir=ROOT,
        web_session=WechatWebSession(database, DATA_DIR),
        product_service=SyncService(database),
    )
    server = FrontendServer(
        CANDIDATE / "_internal" / "webui" / "dist",
        controller,
    )
    server.start()
    refresh_calls = 0

    def handle(route) -> None:
        nonlocal refresh_calls
        payload = json.loads(route.request.post_data or "{}")
        action = str(payload.get("action") or "")
        if action == "stores.refresh.start":
            refresh_calls += 1
            response = {"job_id": "overview-acceptance"}
        elif action == "stores.refresh.status":
            response = {
                "status": "completed",
                "current": len(stores),
                "total": len(stores),
                "succeeded": len(stores),
                "failed": 0,
                "credential_succeeded": len(stores),
                "credential_superseded": 0,
                "credential_failed": 0,
                "credential_deferred": 0,
                "errors": [],
            }
        else:
            route.continue_()
            return
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {"ok": True, "data": response},
                ensure_ascii=False,
            ),
        )

    results: dict[str, object] = {
        "candidate_exe": str(CANDIDATE / "LangLang.exe"),
        "database_source": str(SOURCE),
        "database_copy": str(DATABASE_PATH),
        "store_count_before": len(stores),
        "default_view": "overview",
    }
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                channel="msedge",
                headless=True,
            )
            page = browser.new_page(device_scale_factor=1)
            page.route("**/api/action", handle)
            page.goto(
                f"{server.base_url}/?token={server.token}#/shops",
                wait_until="domcontentloaded",
                timeout=90_000,
            )
            page.wait_for_selector(".store-overview-row, .overview-main-row")
            page.wait_for_selector(".runtime-notice-toggle")
            geometry = {}
            for width in (1440, 1366, 1100, 860):
                page.set_viewport_size({"width": width, "height": 860})
                page.wait_for_timeout(180)
                geometry[width] = page.evaluate(
                    """
                    () => {
                      const scroll = document.querySelector(
                        ".overview-table-scroll"
                      );
                      const sales = Array.from(
                        document.querySelectorAll(".overview-sales-cell")
                      ).slice(0, 4).map((cell) => cell.innerText.trim());
                      return {
                        scrollWidth: scroll.scrollWidth,
                        clientWidth: scroll.clientWidth,
                        rowCount: document.querySelectorAll(
                          ".overview-main-row"
                        ).length,
                        sales,
                        runtimeNotice: document.querySelector(
                          ".runtime-notice-toggle"
                        )?.innerText.trim(),
                      };
                    }
                    """
                )
                page.screenshot(
                    path=DELIVERY / f"stores-overview-{width}-1.8.83.png",
                    full_page=False,
                )

            page.set_viewport_size({"width": 1440, "height": 900})
            page.wait_for_timeout(150)
            page.get_by_role("button", name="详情", exact=True).first.click()
            page.wait_for_selector(".overview-details-row")
            page.screenshot(
                path=DELIVERY / "stores-overview-expanded-1.8.83.png",
                full_page=False,
            )
            details = page.locator(".overview-details-row").first.inner_text()
            page.get_by_role("button", name="收起", exact=True).first.click()

            rows = page.locator(".overview-main-row")
            for index in range(3):
                rows.nth(index).locator("input[type=checkbox]").check()
            context_result = rows.nth(2).evaluate(
                """
                (element) => {
                  const event = new MouseEvent("contextmenu", {
                    bubbles: true,
                    cancelable: true,
                    clientX: 540,
                    clientY: 420,
                    button: 2,
                  });
                  element.dispatchEvent(event);
                  return event.defaultPrevented;
                }
                """
            )
            page.wait_for_selector(".store-context-menu")
            menu_text = page.locator(".store-context-menu").inner_text()
            page.screenshot(
                path=DELIVERY / "stores-context-menu-3-1.8.83.png",
                full_page=False,
            )
            page.get_by_role("menuitem", name="批量设置分组…").click()
            page.wait_for_selector(".batch-group-dialog")
            page.locator(".batch-group-dialog input").fill("1.8.83验收分组")
            page.screenshot(
                path=DELIVERY / "stores-group-dialog-1.8.83.png",
                full_page=False,
            )
            page.get_by_role("button", name="保存分组", exact=True).click()
            page.wait_for_function(
                """() => document.body.innerText.includes(
                  '已将 3 家店铺设为1.8.83验收分组'
                )"""
            )
            group_success = page.locator(".toast").inner_text().strip()
            page.screenshot(
                path=DELIVERY / "stores-group-success-1.8.83.png",
                full_page=False,
            )

            rows.nth(2).click(button="right")
            page.wait_for_selector(".store-context-menu")
            page.get_by_role("menuitem", name="批量删除所选…").click()
            page.wait_for_selector(".modal-mask .modal-card")
            confirm_text = page.locator(".modal-card").last.inner_text()
            page.screenshot(
                path=DELIVERY / "stores-batch-delete-confirm-1.8.83.png",
                full_page=False,
            )
            page.get_by_role("button", name="确认批量删除", exact=True).click()
            page.wait_for_function(
                """() => document.body.innerText.includes(
                  '已删除 3 家本地店铺'
                )"""
            )
            delete_success = page.locator(".toast").inner_text().strip()
            page.screenshot(
                path=DELIVERY / "stores-batch-delete-success-1.8.83.png",
                full_page=False,
            )
            remaining_rows = page.locator(".overview-main-row").count()
            browser.close()

        results["geometry"] = geometry
        results["context_menu"] = {
            "default_prevented": context_result,
            "text": menu_text,
        }
        results["group_success"] = group_success
        results["delete_confirm_contains_warning"] = (
            "不会删除微信官方店铺" in confirm_text
        )
        results["delete_success"] = delete_success
        results["remaining_store_count"] = remaining_rows
        results["refresh_calls"] = refresh_calls
        checks = (
            context_result is True,
            "已选 3 家店铺" in menu_text,
            "商品曝光" in details,
            "资金与保证金" in details,
            "体验分" in details,
            "已删除 3 家本地店铺" in delete_success,
            remaining_rows == len(stores) - 3,
        )
        if not all(checks):
            raise RuntimeError("店铺总览或右键批量操作实机验收失败")
        results["passed"] = True
    finally:
        server.stop()

    target = DELIVERY / "stores-overview-evidence.json"
    target.write_text(
        json.dumps(results, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
