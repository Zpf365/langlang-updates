"""Verify real order flags, packages, traces and product navigation."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weimi_manager.db import Database
from weimi_manager.runtime_logging import configure_runtime_logging
from weimi_manager.service import SyncService
from weimi_manager.web_session import WechatWebSession
from weimi_manager.webui.controller import NextUiController
from weimi_manager.webui.desktop import run_desktop


OUTPUT = ROOT / "deliveries" / "1.8.93-order-flight"
DATA_DIR = OUTPUT / "runtime-data"
DATABASE = DATA_DIR / "weimi_manager.db"
STORE_APP_ID = "wx8e6b6bd9df8abda6"
STORE_NAME = "掘阅宽栾商行"
ORDER_ID = "3740750222608964096"
PRODUCT_ID = "10001900604305"


def _source_database() -> Path:
    local_app_data = Path(
        os.environ.get("LOCALAPPDATA")
        or (Path.home() / "AppData" / "Local")
    )
    return (
        local_app_data
        / "LangLang"
        / "accounts"
        / "81a4f9fe921283cdda75468c"
        / "data"
        / "weimi_manager.db"
    )


def _copy_database(reset: bool) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if reset and DATABASE.exists():
        DATABASE.unlink()
    if DATABASE.exists():
        return
    source_path = _source_database()
    if not source_path.is_file():
        raise FileNotFoundError(source_path)
    source = sqlite3.connect(
        f"{source_path.resolve().as_uri()}?mode=ro",
        uri=True,
    )
    destination = sqlite3.connect(DATABASE)
    try:
        source.backup(destination)
    finally:
        destination.close()
        source.close()


def build_controller() -> NextUiController:
    _copy_database(reset=False)
    database = Database(DATABASE)
    service = SyncService(database)
    return NextUiController(
        database,
        project_dir=ROOT,
        account_label="zpfly",
        web_session=WechatWebSession(database, DATA_DIR),
        product_service=service,
    )


def _find_store(controller: NextUiController):
    store = next(
        (
            row
            for row in controller.db.list_stores()
            if row.app_id == STORE_APP_ID
        ),
        None,
    )
    if store is None or store.name != STORE_NAME:
        raise RuntimeError(
            "目标店铺不存在或名称不匹配："
            f"{getattr(store, 'id', '')} / {getattr(store, 'name', '')}"
        )
    return store


def prepare() -> dict:
    controller = build_controller()
    store = _find_store(controller)
    refreshed = controller.dispatch(
        "orders.refresh-one",
        {"store_id": store.id, "order_id": ORDER_ID},
    )
    logistics = controller.dispatch(
        "orders.logistics.track",
        {
            "store_id": store.id,
            "order_id": ORDER_ID,
            "waybill_id": "435458667553701",
        },
    )
    product = controller.dispatch(
        "products.locate",
        {
            "store_id": store.id,
            "product_id": PRODUCT_ID,
        },
    )
    row = refreshed["order"]
    evidence = {
        "store": {
            "id": store.id,
            "name": store.name,
            "app_id": store.app_id,
        },
        "order_id": ORDER_ID,
        "waybill_id": "435458667553701",
        "carrier": "YUNDA",
        "flag": {
            "code": row.get("official_note_color_code"),
            "name": row.get("official_note_color"),
            "present": row.get("official_flag_present"),
            "merchant_notes": row.get("merchant_notes"),
        },
        "packages": row.get("packages"),
        "logistics": {
            "status": logistics["package"].get("status"),
            "delivery_name": logistics["package"].get("delivery_name"),
            "trace_count": logistics["trace_count"],
            "traces": logistics["package"].get("traces"),
            "source": logistics["source"],
            "checked_at": logistics["checked_at"],
        },
        "product_navigation": {
            "store_id": product["store_id"],
            "product_id": product["product_id"],
            "title": product["product"].get("title"),
            "source": product["source"],
        },
    }
    OUTPUT.mkdir(parents=True, exist_ok=True)
    target = OUTPUT / "platform-evidence.json"
    target.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(evidence, ensure_ascii=False, indent=2))
    return evidence


def launch_ui() -> int:
    controller = build_controller()
    _find_store(controller)
    return run_desktop(
        controller,
        resource_dir=ROOT,
        title="澜琅 1.8.93 订单官方字段验收",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reset", action="store_true")
    parser.add_argument("--ui", action="store_true")
    args = parser.parse_args(argv)
    manager = configure_runtime_logging(DATA_DIR)
    print(f"RUN_ID={manager.run_id}")
    if args.reset:
        _copy_database(reset=True)
    if args.ui:
        return launch_ui()
    prepare()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
