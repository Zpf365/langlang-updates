"""Prepare one real upload task in the isolated source candidate."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weimi_manager.auth_config import AuthConfig
from weimi_manager.auth_service import build_auth_service
from weimi_manager.config import AppPaths
from weimi_manager.db import Database
from weimi_manager.service import SyncService
from weimi_manager.team_service import TeamService
from weimi_manager.web_session import WechatWebSession
from weimi_manager.webui.controller import (
    UPLOAD_DEFAULT_TEMPLATE,
    NextUiController,
)

TARGET_APP_ID = "wxed9ff5dd16887009"
TARGET_NAME = "言参冠旺商行"
TARGET_SOURCE_ID = "10001539842854"
OUTPUT = ROOT / "deliveries" / "1.8.92" / "guarantee-upload"
RUNTIME_DATA_DIR = OUTPUT / "runtime-data"
RUNTIME_DB = RUNTIME_DATA_DIR / "weimi_manager.db"


def build_controller(
    *,
    copy_database: bool = False,
) -> tuple[NextUiController, Any]:
    local_app_data = Path(
        os.environ.get("LOCALAPPDATA")
        or (Path.home() / "AppData" / "Local")
    )
    data_dir = local_app_data / "LangLang" / "data"
    paths = AppPaths(
        project_dir=ROOT,
        resource_dir=ROOT,
        data_dir=data_dir,
        database=data_dir / "weimi_manager.db",
    )
    auth_service = build_auth_service(
        AuthConfig.load(paths),
        data_dir.parent / "auth",
    )
    if auth_service is None:
        raise RuntimeError("账号授权服务未配置")
    accounts = auth_service.store.list_accounts()
    saved = next(
        (
            account
            for account in accounts
            if str(account.username).casefold() == "zpfly"
        ),
        accounts[0] if accounts else None,
    )
    if saved is None:
        raise RuntimeError("没有可恢复的保存账户")
    context = auth_service.restore_account(saved)
    source_database = paths.for_account(context.account_id).database
    RUNTIME_DATA_DIR.mkdir(parents=True, exist_ok=True)
    if copy_database:
        source = sqlite3.connect(
            f"{source_database.resolve().as_uri()}?mode=ro",
            uri=True,
        )
        destination = sqlite3.connect(RUNTIME_DB)
        try:
            source.backup(destination)
        finally:
            destination.close()
            source.close()
    database = Database(RUNTIME_DB)
    service = SyncService(database)
    controller = NextUiController(
        database,
        account_label=context.username,
        project_dir=ROOT,
        web_session=WechatWebSession(database, RUNTIME_DATA_DIR),
        product_service=service,
        auth_context=context,
        auth_client=auth_service.client,
        auth_service=auth_service,
        team_service=TeamService(
            auth_service.client,
            context.tokens.access_token,
        ),
    )
    return controller, context


def main() -> int:
    repeat = "--repeat" in sys.argv[1:]
    controller, context = build_controller(copy_database=not repeat)
    database = controller.db
    store = next(
        (
            item
            for item in database.list_stores()
            if item.app_id == TARGET_APP_ID
        ),
        None,
    )
    if store is None or store.name.strip() != TARGET_NAME:
        raise RuntimeError("目标店铺名称或 AppID 不匹配")
    batch_row = next(
        (
            row
            for row in controller._upload_batch_rows()
            if str(row.get("source_product_id") or "")
            == TARGET_SOURCE_ID
        ),
        None,
    )
    if batch_row is None:
        raise RuntimeError("目标商品尚未导入 Desktop\\2 批次")

    template = json.loads(json.dumps(UPLOAD_DEFAULT_TEMPLATE))
    template.update(
        {
            "name": "A-B微信2000",
            "price_multiplier": 1.1,
            "price_offset": 0,
            "stock": 0,
            "stock_mode": "source",
            "listing_mode": "immediate",
            "duplicate_filter_mode": "off",
            "skip_duplicate": False,
            "auto_open_category": True,
            "category_business_type": "individual",
            "deposit_limit": 2000,
            "fallback_category": "",
            "force_fallback_category": False,
        }
    )
    templates = controller._upload_templates()
    existing_template = next(
        (
            row
            for row in templates
            if str(row.get("name") or "") == "A-B微信2000"
        ),
        None,
    )
    if repeat:
        if existing_template is None:
            raise RuntimeError("重复测试所需模板不存在")
        template_id = str(existing_template["id"])
    else:
        normalized = controller._normalize_upload_template(template)
        template_id = f"tpl_{uuid.uuid4().hex[:10]}"
        now = datetime.now().isoformat(timespec="seconds")
        normalized["id"] = template_id
        normalized["created_at"] = now
        normalized["updated_at"] = now
        templates = [
            row
            for row in templates
            if str(row.get("name") or "") != "A-B微信2000"
        ]
        templates.insert(0, normalized)
        controller._save_upload_templates(templates)

    original_auto_resume = controller._upload_category_auto_resume_enabled()
    controller.dispatch(
        "upload.category-auto-resume.set",
        {"enabled": False},
    )
    created = controller.dispatch(
        "upload.tasks.create",
        {
            "batch_ids": [batch_row["batch_id"]],
            "store_ids": [store.id],
            "template_id": template_id,
        },
    )
    task = created["tasks"][0]
    if len(task.get("items") or []) != 1:
        raise RuntimeError("单件测试任务创建失败")
    evidence = {
        "account": context.username,
        "target_store": {
            "id": store.id,
            "name": store.name,
            "app_id": store.app_id,
        },
        "source_product_id": TARGET_SOURCE_ID,
        "batch_id": batch_row["batch_id"],
        "template_id": template_id,
        "template_snapshot": task["template"],
        "task_id": task["id"],
        "item_id": task["items"][0]["item_id"],
        "payload_categories": (
            batch_row.get("payload") or {}
        ).get("cats_v2"),
        "original_category_auto_resume": original_auto_resume,
    }
    OUTPUT.mkdir(parents=True, exist_ok=True)
    target = OUTPUT / (
        "prepared-repeat.json" if repeat else "prepared.json"
    )
    target.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(evidence, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
