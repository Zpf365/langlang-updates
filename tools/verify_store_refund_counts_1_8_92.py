"""Read-only verification of store refund amount/count consistency."""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weimi_manager.db import Database  # noqa: E402
from weimi_manager.service import SyncService  # noqa: E402

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
OUTPUT = ROOT / "deliveries" / "1.8.92" / "refund-real"
TARGET = OUTPUT / "data" / "weimi_manager.db"
STORE_IDS = (14, 15, 16, 17, 18)


def backup_database() -> None:
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    source = sqlite3.connect(
        f"{SOURCE.resolve().as_uri()}?mode=ro",
        uri=True,
    )
    destination = sqlite3.connect(TARGET)
    try:
        source.backup(destination)
    finally:
        destination.close()
        source.close()


def main() -> int:
    backup_database()
    database = Database(TARGET)
    service = SyncService(database)
    stores = []
    for store_id in STORE_IDS:
        store = database.get_store(store_id)
        if store is None:
            raise RuntimeError(f"真实店铺 {store_id} 不存在，停止只读联调")
        metrics = service.refresh_store_metrics(store)
        stores.append(
            {
                "store_id": store.id,
                "store_name": store.name,
                "refund_amounts": {
                    "today": metrics.get("refund_gmv_today"),
                    "yesterday": metrics.get("refund_gmv_yesterday"),
                    "seven_days": metrics.get("refund_gmv_7d"),
                    "thirty_days": metrics.get("refund_gmv_30d"),
                },
                "refund_order_counts": metrics.get(
                    "_refund_order_counts"
                ),
                "refund_order_cache": metrics.get(
                    "_refund_order_cache"
                ),
                "errors": metrics.get("_errors", []),
            }
        )
    evidence = {
        "source": str(SOURCE),
        "copy": str(TARGET),
        "stores": stores,
    }
    output = OUTPUT / "refund-evidence.json"
    output.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(evidence, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
