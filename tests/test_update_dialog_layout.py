from __future__ import annotations

import json
import gc
import os
import subprocess
import sys
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


def test_update_dialog_is_wide_short_and_keeps_footer_visible() -> None:
    # Playwright + Chromium can activate Tcl's cross-thread async handlers in
    # the parent pytest process on Windows. Keep this native layout probe in a
    # child interpreter so later Tk/PySide tests do not abort at GC time.
    if os.environ.get("LANGLANG_UPDATE_LAYOUT_CHILD") != "1":
        # Flush Tk wrapper cycles left by earlier dialog tests in the main
        # thread before starting Playwright's worker threads.
        gc.collect()
        environment = os.environ.copy()
        environment["LANGLANG_UPDATE_LAYOUT_CHILD"] = "1"
        environment.setdefault("PYTHONIOENCODING", "utf-8")
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                str(Path(__file__).resolve()),
                "--maxfail=1",
            ],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
            check=False,
        )
        assert completed.returncode == 0, (
            "更新弹窗 Chromium 布局测试失败\n"
            f"stdout:\n{completed.stdout[-12000:]}\n"
            f"stderr:\n{completed.stderr[-12000:]}"
        )
        return

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
    server = FrontendServer(static_root, controller, "update-layout-token")
    server.start()
    update_info = {
        "current_version": "1.8.83",
        "latest_version": "1.8.84",
        "available": True,
        "download_url": "https://example.invalid/update.zip",
        "notes": "完整说明应保留在 Release 页面。",
        "release_notes_url": (
            "https://github.com/Zpf365/langlang-updates/"
            "releases/tag/v1.8.84"
        ),
        "summary": [
            {
                "title": "更新提示",
                "items": [
                    "更新弹窗改为宽屏、短高度布局，长内容只在内部分区滚动，底部操作按钮固定可见。",
                ],
            },
            {
                "title": "数据大屏",
                "items": [
                    "数据大屏入口收进更多菜单，点击后直接打开原版全屏大屏，不再经过说明页。",
                ],
            },
            {
                "title": "排行榜",
                "items": [
                    "无数据排行榜隐藏表格主体，保留与周围一致的深色面板，不影响有数据榜单。",
                ],
            },
            {
                "title": "阅读体验",
                "items": [
                    "更新摘要限制为高层重点，避免在弹窗内重复展开完整技术更新日志。",
                ],
            },
            {
                "title": "按钮状态",
                "items": [
                    "稍后与立即更新在 1366×768、1280×720 和低分辨率窗口中始终可操作。",
                ],
            },
            {
                "title": "完整说明",
                "items": [
                    "完整更新内容继续保留在 GitHub Release 页面，弹窗提供独立查看入口。",
                ],
            },
        ],
        "channel": "stable",
        "prerelease": False,
    }

    def handle(route) -> None:
        payload = json.loads(route.request.post_data or "{}")
        action = str(payload.get("action") or "")
        if action == "update.check":
            data = update_info
        elif action == "logs.webui-events":
            data = {"accepted": 0}
        else:
            route.continue_()
            return
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"ok": True, "data": data}, ensure_ascii=False),
        )

    snapshots: dict[str, dict] = {}
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=True,
                executable_path=str(CHROME),
            )
            try:
                for name, width, height, scale in (
                    ("1366x768", 1366, 768, 1),
                    ("1280x720", 1280, 720, 1),
                    ("1024x768", 1024, 768, 1),
                    ("high-dpi", 1280, 720, 2),
                ):
                    page = browser.new_page(
                        viewport={"width": width, "height": height},
                        device_scale_factor=scale,
                    )
                    page.route("**/api/action", handle)
                    page.goto(
                        f"{server.base_url}/?token={server.token}#/shops",
                        wait_until="domcontentloaded",
                        timeout=60_000,
                    )
                    page.wait_for_selector(".sidebar-update-badge", timeout=30_000)
                    page.locator(".sidebar-update-badge").click()
                    page.wait_for_selector(".update-modal", timeout=10_000)
                    snapshots[name] = page.evaluate(
                        """
                        () => {
                          const modal = document.querySelector(".update-modal");
                          const body = modal.querySelector(".modal-body");
                          const footer = modal.querySelector(".modal-footer");
                          const modalRect = modal.getBoundingClientRect();
                          const footerRect = footer.getBoundingClientRect();
                          return {
                            modalWidth: modalRect.width,
                            modalHeight: modalRect.height,
                            modalTop: modalRect.top,
                            modalBottom: modalRect.bottom,
                            viewportWidth: window.innerWidth,
                            viewportHeight: window.innerHeight,
                            bodyClientHeight: body.clientHeight,
                            bodyScrollHeight: body.scrollHeight,
                            footerTop: footerRect.top,
                            footerBottom: footerRect.bottom,
                            masks: document.querySelectorAll(".modal-mask").length,
                            laterVisible: Boolean(
                              Array.from(footer.querySelectorAll("button"))
                                .find((button) => button.innerText.trim() === "稍后")
                            ),
                            updateVisible: Boolean(
                              Array.from(footer.querySelectorAll("button"))
                                .find((button) => button.innerText.trim() === "立即更新")
                            ),
                            pointCount: document.querySelectorAll(
                              ".update-summary-grid li"
                            ).length,
                          };
                        }
                        """
                    )
                    page.get_by_role("button", name="稍后", exact=True).click()
                    page.wait_for_selector(".update-modal", state="detached")
                    snapshots[name]["closed"] = True
                    page.close()
            finally:
                browser.close()
    finally:
        server.stop()

    for name, snapshot in snapshots.items():
        assert snapshot["masks"] == 1, name
        assert snapshot["pointCount"] == 6, name
        assert snapshot["modalWidth"] >= min(900, snapshot["viewportWidth"] - 52), name
        assert snapshot["modalHeight"] <= snapshot["viewportHeight"] * 0.71, name
        assert snapshot["modalTop"] >= 0, name
        assert snapshot["modalBottom"] <= snapshot["viewportHeight"], name
        assert snapshot["footerBottom"] <= snapshot["viewportHeight"], name
        assert snapshot["laterVisible"] is True, name
        assert snapshot["updateVisible"] is True, name
        assert snapshot["bodyScrollHeight"] >= snapshot["bodyClientHeight"], name
        assert snapshot["closed"] is True, name
