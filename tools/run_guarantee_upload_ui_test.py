"""Submit one prepared Desktop\\2 item through the candidate WebUI."""

from __future__ import annotations

import dataclasses
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from playwright.sync_api import sync_playwright

from tools.prepare_guarantee_upload_test import build_controller
from weimi_manager.webui.server import FrontendServer

OUTPUT = ROOT / "deliveries" / "1.8.92" / "guarantee-upload"
PREPARED = OUTPUT / "prepared.json"
CHROME = (
    ROOT
    / "browser_runtime"
    / "chromium-1254"
    / "chrome-win64"
    / "chrome.exe"
)


def current_upload_job(controller, job_id: str) -> dict:
    with controller._upload_jobs_lock:
        return dict(controller._upload_jobs.get(job_id) or {})


def main() -> int:
    repeat = "--repeat" in sys.argv[1:]
    phase = (
        "repeat"
        if repeat
        else ("resume" if "--resume" in sys.argv[1:] else "initial")
    )
    prepared_path = (
        OUTPUT / "prepared-repeat.json" if repeat else PREPARED
    )
    prepared = json.loads(prepared_path.read_text(encoding="utf-8"))
    task_id = prepared["task_id"]
    controller, context = build_controller(copy_database=False)
    controller.dispatch(
        "upload.category-auto-resume.set",
        {"enabled": False},
    )
    task = next(
        (
            row
            for row in controller._upload_tasks()
            if str(row.get("id") or "") == task_id
        ),
        None,
    )
    if task is None or len(task.get("items") or []) != 1:
        raise RuntimeError("单件测试任务不存在或商品数量不为1")
    if str(task.get("store_name") or "") != "言参冠旺商行":
        raise RuntimeError("单件测试任务目标店铺不匹配")

    server = FrontendServer(ROOT / "webui" / "dist", controller)
    server.start()
    evidence = {
        "account": context.username,
        "task_id": task_id,
        "source_product_id": prepared["source_product_id"],
        "template_snapshot": task.get("template"),
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
                    name="上传任务",
                    exact=True,
                ).click()
                task_item = page.locator(
                    ".task-item"
                ).filter(has_text="A-B微信2000").first
                task_item.wait_for(state="visible", timeout=60_000)
                task_item.locator("input[type=checkbox]").check()
                page.get_by_text(
                    "已选任务 1 · 已选商品 0"
                ).wait_for(timeout=30_000)
                page.screenshot(
                    path=OUTPUT / f"ui-{phase}-task-selected.png",
                    full_page=True,
                )
                page.get_by_role(
                    "button",
                    name="开始 / 继续（1）",
                    exact=True,
                ).click()
                page.get_by_text(
                    "确认提交微信小店",
                    exact=True,
                ).wait_for(timeout=30_000)
                page.screenshot(
                    path=OUTPUT / f"ui-{phase}-risk-confirm.png",
                    full_page=True,
                )
                page.get_by_role(
                    "button",
                    name="开始真实提交",
                    exact=True,
                ).click()

                started_at = time.time()
                job_id = ""
                job: dict = {}
                while time.time() - started_at < 300:
                    job = current_upload_job(
                        controller,
                        job_id,
                    ) if job_id else {}
                    if not job_id:
                        with controller._upload_jobs_lock:
                            ids = [
                                key
                                for key, value in controller._upload_jobs.items()
                                if value.get("kind") == "run"
                            ]
                        if ids:
                            job_id = str(ids[-1])
                            job = current_upload_job(controller, job_id)
                    status = str(job.get("status") or "")
                    if status in {
                        "completed",
                        "partial",
                        "failed",
                        "paused",
                        "cancelled",
                    }:
                        break
                    time.sleep(0.5)
                else:
                    raise RuntimeError("单件上传超时")
                page.wait_for_timeout(1500)
                page.screenshot(
                    path=OUTPUT / f"ui-{phase}-task-result.png",
                    full_page=True,
                )

                refreshed_tasks = controller._upload_tasks()
                result_task = next(
                    (
                        row
                        for row in refreshed_tasks
                        if str(row.get("id") or "") == task_id
                    ),
                    {},
                )
                result_item = (result_task.get("items") or [{}])[0]
                official_product = None
                product_id = str(result_item.get("product_id") or "")
                if product_id:
                    try:
                        product = controller.product_service.fetch_product_detail(
                            next(
                                store
                                for store in controller.db.list_stores()
                                if store.id == int(result_task["store_id"])
                            ),
                            product_id,
                        )
                    except Exception as exc:
                        official_product = {
                            "error": str(exc)[:1000],
                        }
                    else:
                        official_product = dataclasses.asdict(product)
                        official_product.pop("raw", None)
                evidence.update(
                    {
                        "job": {
                            key: value
                            for key, value in job.items()
                            if key not in {"runner"}
                            and not key.startswith("_")
                        },
                        "task": result_task,
                        "official_product": official_product,
                    }
                )
            finally:
                browser.close()
    finally:
        server.stop()
        controller.dispatch(
            "upload.category-auto-resume.set",
            {
                "enabled": bool(
                    prepared.get(
                        "original_category_auto_resume",
                        True,
                    )
                )
            },
        )
    (OUTPUT / f"result-{phase}.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "source_product_id": evidence["source_product_id"],
                "status": evidence["task"]["items"][0]["status"],
                "message": evidence["task"]["items"][0]["message"],
                "product_id": evidence["task"]["items"][0].get("product_id"),
                "official_product": evidence["official_product"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
