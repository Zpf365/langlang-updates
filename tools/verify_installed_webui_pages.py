"""Verify installed WebUI pages with the saved account session."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from playwright.sync_api import sync_playwright  # noqa: E402

from weimi_manager.auth_config import AuthConfig  # noqa: E402
from weimi_manager.auth_service import build_auth_service  # noqa: E402
from weimi_manager.config import AppPaths  # noqa: E402
from weimi_manager.db import Database  # noqa: E402
from weimi_manager.runtime_logging import (  # noqa: E402
    configure_runtime_logging,
)
from weimi_manager.service import SyncService  # noqa: E402
from weimi_manager.team_service import TeamService  # noqa: E402
from weimi_manager.web_session import WechatWebSession  # noqa: E402
from weimi_manager.webui.controller import NextUiController  # noqa: E402
from weimi_manager.webui.server import FrontendServer  # noqa: E402


TARGET = Path(r"D:\WG\LangLang")
LOCAL_APP_DATA = Path(
    os.environ.get("LOCALAPPDATA")
    or (Path.home() / "AppData" / "Local")
)
OUTPUT = ROOT / "deliveries" / "1.8.82"


def main() -> int:
    data_dir = LOCAL_APP_DATA / "LangLang" / "data"
    paths = AppPaths(
        project_dir=ROOT,
        resource_dir=TARGET / "_internal",
        data_dir=data_dir,
        database=data_dir / "weimi_manager.db",
    )
    configure_runtime_logging(OUTPUT / "installed-webui-logs")
    auth = build_auth_service(
        AuthConfig.load(paths),
        LOCAL_APP_DATA / "LangLang" / "auth",
    )
    if auth is None:
        raise RuntimeError("AuthService unavailable")
    saved = auth.store.find("zpfly") or auth.store.list_accounts()[0]
    context = auth.restore_account(saved)
    account_paths = paths.for_account(context.account_id)
    database = Database(account_paths.database)
    controller = NextUiController(
        database,
        account_label=context.username,
        project_dir=TARGET,
        web_session=WechatWebSession(database, account_paths.data_dir),
        product_service=SyncService(database),
        auth_context=context,
        auth_client=auth.client,
        auth_service=auth,
        team_service=TeamService(
            auth.client,
            context.tokens.access_token,
        ),
    )
    server = FrontendServer(
        TARGET / "_internal" / "webui" / "dist",
        controller,
    )
    server.start()
    pages = {
        "stores": "#/shops",
        "orders": "#/orders",
        "products": "#/managed-products",
        "activity": "#/activity",
    }
    results: dict[str, object] = {}
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                channel="msedge",
                headless=True,
            )
            page = browser.new_page(
                viewport={"width": 1780, "height": 1100}
            )
            token = server.token
            for name, route in pages.items():
                page.goto(
                    f"{server.base_url}/?token={token}{route}",
                    wait_until="networkidle",
                    timeout=90_000,
                )
                page.wait_for_selector(".app-shell", timeout=60_000)
                page.wait_for_timeout(2500)
                text = page.locator("body").inner_text()
                screenshot = OUTPUT / f"installed-{name}.png"
                page.screenshot(path=screenshot, full_page=False)
                results[name] = {
                    "route": route,
                    "page_loaded": True,
                    "screenshot": str(screenshot),
                    "contains_expected_title": {
                        "stores": "店铺管理",
                        "orders": "订单管理",
                        "products": "商品管理",
                        "activity": "活动管理",
                    }[name]
                    in text,
                }
            browser.close()
    finally:
        store_count = len(database.list_stores())
        server.stop()
    results["account"] = context.username
    results["store_count"] = store_count
    target = OUTPUT / "installed-webui-verification.json"
    target.write_text(
        json.dumps(results, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
