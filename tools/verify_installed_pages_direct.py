"""Verify installed WebUI resources against an isolated copy of account data."""

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


TARGET = Path(r"D:\WG\LangLang")
OUTPUT = ROOT / "deliveries" / "1.8.83"
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
WORK = OUTPUT / "installed-pages-data"
DATABASE_PATH = WORK / "data" / "weimi_manager.db"


def backup_database() -> None:
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
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
    configure_runtime_logging(OUTPUT / "installed-pages-logs")
    database = Database(DATABASE_PATH)
    controller = NextUiController(
        database,
        account_label="zpfly",
        project_dir=TARGET,
        web_session=WechatWebSession(database, DATABASE_PATH.parent),
        product_service=SyncService(database),
    )
    server = FrontendServer(
        TARGET / "_internal" / "webui" / "dist",
        controller,
    )
    server.start()
    blocked_actions = {
        "stores.refresh.start",
        "stores.refresh.status",
        "orders.sync",
        "orders.after-sales.sync",
        "orders.quick-refresh.start",
        "orders.quick-refresh.status",
    }

    def handle(route) -> None:
        payload = json.loads(route.request.post_data or "{}")
        action = str(payload.get("action") or "")
        if action not in blocked_actions:
            route.continue_()
            return
        if action in {
            "stores.refresh.start",
            "orders.quick-refresh.start",
        }:
            data = {"job_id": "installed-page-verification"}
        elif action in {
            "stores.refresh.status",
            "orders.quick-refresh.status",
        }:
            data = {
                "status": "completed",
                "current": 9,
                "total": 9,
                "succeeded": 9,
                "failed": 0,
                "credential_succeeded": 9,
                "credential_superseded": 0,
                "credential_failed": 0,
                "credential_deferred": 0,
                "errors": [],
            }
        else:
            data = {
                "succeeded": 0,
                "failed": 0,
                "status_changes": [],
                "guard": "read_only_verification",
            }
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {"ok": True, "data": data},
                ensure_ascii=False,
            ),
        )

    pages = {
        "stores": ("#/shops", "店铺管理", "installed-stores-1.8.83.png"),
        "orders": ("#/orders", "订单管理", "installed-orders-1.8.83.png"),
        "products": (
            "#/managed-products",
            "商品管理",
            "installed-products-1.8.83.png",
        ),
        "activity": ("#/activity", "活动管理", "installed-activity-1.8.83.png"),
        "big_screen_entry": (
            "#/big-screen",
            "数据大屏",
            "installed-big-screen-entry-1.8.83.png",
        ),
    }
    results: dict[str, object] = {
        "install_path": str(TARGET),
        "database_source": str(SOURCE),
        "database_copy": str(DATABASE_PATH),
        "pages": {},
    }
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                channel="msedge",
                headless=True,
            )
            page = browser.new_page(
                viewport={"width": 1780, "height": 1100},
                device_scale_factor=1,
            )
            page.route("**/api/action", handle)
            for key, (route, title, screenshot_name) in pages.items():
                page.goto(
                    f"{server.base_url}/?token={server.token}{route}",
                    wait_until="domcontentloaded",
                    timeout=90_000,
                )
                page.wait_for_selector(".app-shell", timeout=60_000)
                page.wait_for_timeout(1800)
                body_text = page.locator("body").inner_text()
                screenshot = OUTPUT / screenshot_name
                page.screenshot(path=screenshot, full_page=False)
                results["pages"][key] = {
                    "route": route,
                    "title_visible": title in body_text,
                    "screenshot": str(screenshot),
                }
            browser.close()
    finally:
        server.stop()

    results["store_count"] = len(database.list_stores())
    results["passed"] = bool(
        results["store_count"] == 9
        and all(
            item["title_visible"]
            for item in results["pages"].values()
        )
    )
    target = OUTPUT / "installed-pages-evidence.json"
    target.write_text(
        json.dumps(results, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0 if results["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
