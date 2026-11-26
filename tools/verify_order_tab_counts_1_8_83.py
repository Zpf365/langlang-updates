"""Verify order tab counts against a read-only copy of real account data."""

from __future__ import annotations

import json
import re
import shutil
import sqlite3
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

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


CANDIDATE = ROOT / "release" / "LangLang_1.8.83_Windows_x64"
DELIVERY = ROOT / "deliveries" / "1.8.83"
ACCEPTANCE = DELIVERY / "acceptance"
DATA_DIR = ACCEPTANCE / "data"
AUTH_DIR = ACCEPTANCE / "auth"
DATABASE_PATH = DATA_DIR / "weimi_manager.db"
STORE_NAME = "幸却冠旺商行"
EXPECTED_STORE_ID = 14


def tab_count(labels: list[str], label: str) -> int:
    for value in labels:
        match = re.match(rf"^{re.escape(label)}(?:\s+(\d+))?$", value.strip())
        if match:
            return int(match.group(1) or 0)
    raise AssertionError(f"未找到页签 {label}：{labels}")


def local_counts(store_id: int) -> dict[str, int]:
    connection = sqlite3.connect(
        f"{DATABASE_PATH.resolve().as_uri()}?mode=ro",
        uri=True,
    )
    try:
        if store_id:
            rows = connection.execute(
                "SELECT status, COUNT(*) FROM orders WHERE store_id=? GROUP BY status",
                (store_id,),
            ).fetchall()
        else:
            rows = connection.execute(
                "SELECT status, COUNT(*) FROM orders GROUP BY status"
            ).fetchall()
    finally:
        connection.close()
    status_counts = {int(status): int(count) for status, count in rows}
    return {
        "all": sum(status_counts.values()),
        "wait_ship": status_counts.get(17, 0) + status_counts.get(20, 0),
    }


def install_sync_guard(page) -> None:
    def handle(route) -> None:
        try:
            payload = json.loads(route.request.post_data or "{}")
        except json.JSONDecodeError:
            payload = {}
        if str(payload.get("action") or "") == "orders.sync":
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(
                    {
                        "ok": True,
                        "data": {
                            "succeeded": 0,
                            "failed": 0,
                            "status_changes": [],
                            "guard": "read_only_acceptance",
                        },
                    },
                    ensure_ascii=False,
                ),
            )
            return
        route.continue_()

    page.route("**/api/action", handle)


def select_store(page, label: str) -> None:
    page.locator(".a-store-select").select_option(label=label)
    page.wait_for_timeout(350)


def collect_state(page, label: str) -> dict[str, object]:
    page.get_by_role("button", name=re.compile(r"^待发货")).click()
    page.wait_for_timeout(250)
    labels = page.locator(".a-tabs > button").all_inner_texts()
    rows = page.locator("tbody tr.order-a-row").count()
    all_count = tab_count(labels, "全部")
    wait_count = tab_count(labels, "待发货")
    return {
        "selection": label,
        "tabs": [item.strip() for item in labels],
        "all_count": all_count,
        "wait_ship_count": wait_count,
        "visible_wait_ship_rows": rows,
        "counts_match": wait_count == rows,
    }


def main() -> int:
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    source_database = (
        Path.home()
        / "AppData"
        / "Local"
        / "LangLang"
        / "accounts"
        / "81a4f9fe921283cdda75468c"
        / "data"
        / "weimi_manager.db"
    )
    source_auth = (
        Path.home()
        / "AppData"
        / "Local"
        / "LangLang"
        / "auth"
        / "saved_accounts.bin"
    )
    if not DATABASE_PATH.is_file():
        shutil.copy2(source_database, DATABASE_PATH)
    AUTH_DIR.mkdir(parents=True, exist_ok=True)
    if not (AUTH_DIR / "saved_accounts.bin").is_file():
        shutil.copy2(source_auth, AUTH_DIR / "saved_accounts.bin")

    paths = AppPaths(
        project_dir=ROOT,
        resource_dir=CANDIDATE / "_internal",
        data_dir=DATA_DIR,
        database=DATABASE_PATH,
    )
    configure_runtime_logging(DELIVERY / "verification-logs")
    auth = build_auth_service(AuthConfig.load(paths), AUTH_DIR)
    if auth is None:
        raise RuntimeError("AuthService unavailable")
    saved = auth.store.find("zpfly") or auth.store.list_accounts()[0]
    context = auth.restore_account(saved)
    database = Database(DATABASE_PATH)

    connection = sqlite3.connect(
        f"{DATABASE_PATH.resolve().as_uri()}?mode=ro",
        uri=True,
    )
    try:
        store_row = connection.execute(
            "SELECT id, name FROM stores WHERE id=?",
            (EXPECTED_STORE_ID,),
        ).fetchone()
    finally:
        connection.close()
    if not store_row or str(store_row[1]) != STORE_NAME:
        raise RuntimeError(
            f"店铺 {EXPECTED_STORE_ID} 名称不是 {STORE_NAME}，停止验收"
        )

    controller = NextUiController(
        database,
        account_label=context.username,
        project_dir=ROOT,
        web_session=WechatWebSession(database, DATA_DIR),
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
        CANDIDATE / "_internal" / "webui" / "dist",
        controller,
    )
    server.start()
    results: dict[str, object] = {
        "candidate_exe": str(CANDIDATE / "LangLang.exe"),
        "account": context.username,
        "store_id": EXPECTED_STORE_ID,
        "store_name": STORE_NAME,
        "network_policy": "orders.sync intercepted as no-op; all other RPCs used",
        "database_source": str(source_database),
        "database_copy": str(DATABASE_PATH),
    }
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                channel="msedge",
                headless=True,
            )
            page = browser.new_page(
                viewport={"width": 1760, "height": 1040},
                device_scale_factor=1,
            )
            install_sync_guard(page)
            page.goto(
                f"{server.base_url}/?token={server.token}#/orders",
                wait_until="domcontentloaded",
                timeout=90_000,
            )
            page.wait_for_selector(".a-tabs", timeout=60_000)
            page.wait_for_timeout(1_500)

            expected_store = local_counts(EXPECTED_STORE_ID)
            select_store(page, STORE_NAME)
            store_state = collect_state(page, STORE_NAME)
            store_screenshot = DELIVERY / "order-tabs-store14-1.8.83.png"
            page.screenshot(path=store_screenshot, full_page=False)

            expected_all = local_counts(0)
            select_store(page, "全部店铺")
            all_state = collect_state(page, "全部店铺")
            all_screenshot = DELIVERY / "order-tabs-all-stores-1.8.83.png"
            page.screenshot(path=all_screenshot, full_page=False)
            browser.close()

        results["store_expected"] = expected_store
        results["store_observed"] = store_state
        results["store_screenshot"] = str(store_screenshot)
        results["all_expected"] = expected_all
        results["all_observed"] = all_state
        results["all_screenshot"] = str(all_screenshot)
        expected_checks = (
            store_state["all_count"] == expected_store["all"],
            store_state["wait_ship_count"] == expected_store["wait_ship"],
            store_state["visible_wait_ship_rows"] == expected_store["wait_ship"],
            all_state["all_count"] == expected_all["all"],
            all_state["wait_ship_count"] == expected_all["wait_ship"],
            all_state["visible_wait_ship_rows"] == expected_all["wait_ship"],
        )
        if not all(expected_checks):
            raise RuntimeError("页签计数或列表数量与数据库范围不一致")
        results["passed"] = True
    finally:
        store_count = len(database.list_stores())
        results["store_count"] = store_count
        server.stop()

    target = DELIVERY / "order-tab-count-evidence.json"
    target.write_text(
        json.dumps(results, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
