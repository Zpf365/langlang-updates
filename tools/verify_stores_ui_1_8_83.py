"""Verify compact runtime-notice control and one-call store login in 1.8.83."""

from __future__ import annotations

import json
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


def main() -> int:
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
    stores = database.list_stores()
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
    login_requests: list[list[int]] = []
    refresh_requests: list[list[int]] = []
    clear_cache_requests = 0
    results: dict[str, object] = {
        "candidate_exe": str(CANDIDATE / "LangLang.exe"),
        "account": context.username,
        "store_count": len(stores),
        "network_policy": (
            "refresh, clear-cache and login were deterministic UI guards; "
            "real data was loaded by stores.list"
        ),
    }

    def handle(route) -> None:
        nonlocal clear_cache_requests
        payload = json.loads(route.request.post_data or "{}")
        action = str(payload.get("action") or "")
        data = payload.get("payload") if isinstance(payload.get("payload"), dict) else {}
        if action == "stores.refresh.start":
            ids = [int(value) for value in data.get("store_ids") or []]
            refresh_requests.append(ids)
            response = {"job_id": "acceptance-refresh"}
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
        elif action == "stores.clear-cache":
            clear_cache_requests += 1
            response = {"removed": 3}
        elif action == "stores.login":
            ids = [int(value) for value in data.get("store_ids") or []]
            login_requests.append(ids)
            response = {
                "success": True,
                "login_confirmed": True,
                "requested_store_ids": ids,
                "requested_logged_in_ids": ids,
                "not_logged_in_store_ids": [],
                "missing_store_ids": [],
                "requested_logged_in_count": len(ids),
                "matched_store_ids": ids,
                "saved_store_count": len(ids),
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

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                channel="msedge",
                headless=True,
            )
            page = browser.new_page(
                viewport={"width": 980, "height": 640},
                device_scale_factor=1,
            )
            page.route("**/api/action", handle)
            page.goto(
                f"{server.base_url}/?token={server.token}#/shops",
                wait_until="domcontentloaded",
                timeout=90_000,
            )
            page.wait_for_selector(".runtime-notice-toggle", timeout=60_000)
            page.wait_for_selector("tbody tr", timeout=60_000)

            toggle = page.locator(".runtime-notice-toggle")
            initial_text = toggle.inner_text().strip()
            initial_pressed = toggle.get_attribute("aria-pressed")
            page.get_by_role("button", name="清理缓存", exact=True).click()
            page.wait_for_selector(".toast", timeout=10_000)
            toast_text = page.locator(".toast").inner_text().strip()
            expanded_png = DELIVERY / "stores-runtime-notice-expanded-1.8.83.png"
            page.screenshot(path=expanded_png, full_page=False)

            toggle.click()
            page.wait_for_selector(".toast", state="detached", timeout=5_000)
            hidden_text = toggle.inner_text().strip()
            hidden_pressed = toggle.get_attribute("aria-pressed")
            hidden_png = DELIVERY / "stores-runtime-notice-hidden-1.8.83.png"
            page.screenshot(path=hidden_png, full_page=False)

            toggle.click()
            page.wait_for_selector(".toast", timeout=5_000)
            restored_text = page.locator(".toast").inner_text().strip()
            restored_png = DELIVERY / "stores-runtime-notice-restored-1.8.83.png"
            page.screenshot(path=restored_png, full_page=False)

            page.locator("thead .selection-column input[type=checkbox]").check()
            page.get_by_role("button", name="登录", exact=True).click()
            page.wait_for_timeout(500)
            login_message = page.locator(".toast").inner_text().strip()
            browser.close()

        results["runtime_notice"] = {
            "initial_text": initial_text,
            "initial_aria_pressed": initial_pressed,
            "toast_text": toast_text,
            "hidden_text": hidden_text,
            "hidden_aria_pressed": hidden_pressed,
            "restored_toast_text": restored_text,
            "expanded_screenshot": str(expanded_png),
            "hidden_screenshot": str(hidden_png),
            "restored_screenshot": str(restored_png),
        }
        results["batch_login"] = {
            "selected_store_count": len(stores),
            "rpc_count": len(login_requests),
            "rpc_store_ids": login_requests,
            "message": login_message,
        }
        checks = (
            initial_text.endswith("开"),
            initial_pressed == "true",
            "已清理 3 项本地缓存" in toast_text,
            hidden_text.endswith("关"),
            hidden_pressed == "false",
            restored_text == toast_text,
            len(login_requests) == 1,
            login_requests[0] == [store.id for store in stores],
            clear_cache_requests == 1,
        )
        if not all(checks):
            raise RuntimeError("运行提示开关或批量登录 RPC 契约不一致")
        results["passed"] = True
    finally:
        server.stop()

    target = DELIVERY / "stores-ui-evidence.json"
    target.write_text(
        json.dumps(results, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
