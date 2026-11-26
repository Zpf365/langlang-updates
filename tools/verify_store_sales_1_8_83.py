"""Read real official sales values against an isolated database copy."""

from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weimi_manager.db import Database  # noqa: E402
from weimi_manager.service import SyncService  # noqa: E402
from weimi_manager.shop_private import ShopPrivateClient  # noqa: E402

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
OUTPUT = ROOT / "deliveries" / "1.8.83" / "sales-real"
TARGET = OUTPUT / "data" / "weimi_manager.db"


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
    store = database.get_store(15)
    if store is None or store.name != "盾眉宽栾商行":
        raise RuntimeError("真实店铺 15 名称不匹配，停止只读联调")
    captured: dict[str, object] = {}

    class RecordingClient(ShopPrivateClient):
        def realtime_overview(self):
            result = super().realtime_overview()
            captured["realtime"] = dict(result)
            return result

        def period_overview(self, days=1, offset_days=1):
            result = super().period_overview(
                days,
                offset_days=offset_days,
            )
            captured[f"period_{days}_{offset_days}"] = dict(result)
            return result

    service = SyncService(database)
    with patch(
        "weimi_manager.service.ShopPrivateClient",
        side_effect=lambda *_args, **_kwargs: RecordingClient(database, store),
    ):
        metrics = service.refresh_store_metrics(store)

    now = int(time.time())
    status12_rows = [
        row
        for row in database.list_orders(store_id=store.id, limit=100000)
        if int(row.get("status") or 0) == 12
    ]
    ages = [
        (now - int(row.get("create_time") or 0)) / 3600
        for row in status12_rows
    ]
    evidence = {
        "store_id": store.id,
        "store_name": store.name,
        "official_realtime": captured.get("realtime", {}),
        "official_yesterday": captured.get("period_1_1", {}),
        "official_7d": captured.get("period_7_1", {}),
        "official_30d": captured.get("period_30_1", {}),
        "final_today": metrics.get("gmv_today"),
        "today_orders": metrics.get("orders_today"),
        "final_yesterday": metrics.get("gmv_yesterday"),
        "final_7d": metrics.get("gmv_7d"),
        "final_30d": metrics.get("gmv_30d"),
        "refund_amounts": {
            "today": metrics.get("refund_gmv_today"),
            "yesterday": metrics.get("refund_gmv_yesterday"),
            "seven_days": metrics.get("refund_gmv_7d"),
            "thirty_days": metrics.get("refund_gmv_30d"),
        },
        "refund_order_counts": metrics.get("_refund_order_counts"),
        "refund_order_cache": metrics.get("_refund_order_cache"),
        "gift_exclusion": metrics.get("_gift_exclusion"),
        "status12_cached_orders": len(status12_rows),
        "status12_min_age_hours": round(min(ages), 3) if ages else None,
        "status12_within_24h": sum(age < 24 for age in ages),
        "errors": metrics.get("_errors", []),
        "source": str(SOURCE),
        "copy": str(TARGET),
    }
    target = OUTPUT / "sales-evidence.json"
    target.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(evidence, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
