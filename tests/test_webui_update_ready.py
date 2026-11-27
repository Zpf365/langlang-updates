from __future__ import annotations

from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

from weimi_manager.db import Database
from weimi_manager.webui.controller import NextUiController
from weimi_manager.webui.server import FrontendServer

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "webui" / "dist"
CHROME = ROOT / "browser_runtime" / "chromium-1254" / "chrome-win64" / "chrome.exe"


@pytest.mark.skipif(
    not (STATIC / "index.html").is_file() or not CHROME.is_file(),
    reason="需要 production build 和本地 Chromium",
)
def test_built_webui_sets_hot_update_ready_marker(tmp_path: Path) -> None:
    server = FrontendServer(
        STATIC,
        NextUiController(Database(tmp_path / "webui.db")),
        "ready-test-token",
    )
    server.start()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                executable_path=str(CHROME),
                headless=True,
            )
            try:
                page = browser.new_page()
                page.goto(server.entry_url, wait_until="networkidle")
                page.wait_for_function("document.documentElement.dataset.langlangReady === '1'")
            finally:
                browser.close()
    finally:
        server.stop()
