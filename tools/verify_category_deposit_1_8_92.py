"""Read-only comparison of WeChat category deposits for Desktop\\1."""

from __future__ import annotations

import json
import argparse
import sqlite3
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weimi_manager.db import Database  # noqa: E402
from weimi_manager.shop_private import (  # noqa: E402
    CATEGORY_DEPOSIT_PATH,
    CATEGORY_RELATION_PATH,
    CATEGORY_REQUIREMENT_PATH,
    ShopPrivateClient,
)

SOURCE_DB = (
    Path.home()
    / "AppData"
    / "Local"
    / "LangLang"
    / "accounts"
    / "81a4f9fe921283cdda75468c"
    / "data"
    / "weimi_manager.db"
)
ZHINIAO_DB = Path(
    r"D:\只更鸟\知更鸟微店订单助手\data\ordersync.db"
)
ZHINIAO_CATEGORY_LIBRARY = Path(
    r"D:\只更鸟\知更鸟微店订单助手\data\wechat-category-library.json"
)
OUTPUT = ROOT / "deliveries" / "1.8.92" / "category-deposit"
TARGET_DB = OUTPUT / "data" / "weimi_manager.db"
TARGET_APP_ID = "wxed9ff5dd16887009"
TARGET_NAME = "言参冠旺商行"


def backup_database() -> None:
    TARGET_DB.parent.mkdir(parents=True, exist_ok=True)
    source = sqlite3.connect(
        f"{SOURCE_DB.resolve().as_uri()}?mode=ro",
        uri=True,
    )
    destination = sqlite3.connect(TARGET_DB)
    try:
        source.backup(destination)
    finally:
        destination.close()
        source.close()


def path_ids(raw: dict[str, Any]) -> list[str]:
    for key in ("categoryPathIds", "category_path_ids", "id_path"):
        values = raw.get(key)
        if isinstance(values, list) and values:
            return [str(value) for value in values if str(value)]
    leaf = str(
        raw.get("catId")
        or raw.get("categoryId")
        or raw.get("category_id")
        or ""
    )
    return [leaf] if leaf else []


def relation_rows(client: ShopPrivateClient) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for status, label in ((1, "已生效"), (2, "审核中")):
        result = client._post(
            CATEGORY_RELATION_PATH,
            {"status": status},
        )
        for item in result.get("categoryRelationList") or []:
            if not isinstance(item, dict):
                continue
            category_id = str(
                item.get("id")
                or item.get("categoryId")
                or (item.get("category") or {}).get("categoryId")
                or ""
            )
            if category_id:
                rows[category_id] = {
                    **item,
                    "_state": label,
                }
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", choices=("1", "2"), default="1")
    args = parser.parse_args()
    package_dir = Path.home() / "Desktop" / args.dataset
    output = (
        OUTPUT
        if args.dataset == "1"
        else ROOT / "deliveries" / "1.8.92" / "category-deposit-2"
    )
    backup_database()
    database = Database(TARGET_DB)
    store = next(
        (
            item
            for item in database.list_stores()
            if str(item.app_id).strip() == TARGET_APP_ID
        ),
        None,
    )
    if (
        store is None
        or str(store.name).strip() != TARGET_NAME
        or str(store.app_id).strip() != TARGET_APP_ID
    ):
        raise RuntimeError("目标店铺名称或 AppID 不匹配，停止联调")

    source_rows = []
    for path in sorted(package_dir.glob("*.json")):
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
        ids = path_ids(raw)
        source_rows.append(
            {
                "source_product_id": str(
                    raw.get("productId")
                    or raw.get("itemId")
                    or path.stem
                ),
                "title": str(raw.get("title") or ""),
                "category_path_ids": ids,
                "category_path": str(
                    raw.get("categoryPath")
                    or raw.get("category_path")
                    or ""
                ),
                "file": path.name,
            }
        )
    category_ids = sorted(
        {
            values[-1]
            for row in source_rows
            if (values := row["category_path_ids"])
        }
    )

    zhiniao = sqlite3.connect(
        f"{ZHINIAO_DB.resolve().as_uri()}?mode=ro",
        uri=True,
    )
    zhiniao.row_factory = sqlite3.Row
    try:
        publish_records = {
            str(row["source_product_id"]): dict(row)
            for row in zhiniao.execute(
                """
                SELECT source_product_id, wechat_product_id, title, published_at
                FROM product_publish_records
                WHERE appid=?
                """,
                (TARGET_APP_ID,),
            )
        }
    finally:
        zhiniao.close()
    category_library_raw = json.loads(
        ZHINIAO_CATEGORY_LIBRARY.read_text(encoding="utf-8-sig")
    )
    category_library = category_library_raw.get("categories") or {}

    client = ShopPrivateClient(database, store)
    online_product_snapshot = client.online_product_count()
    relation_by_id = relation_rows(client)
    deposit_result = client._post(
        CATEGORY_DEPOSIT_PATH,
        {
            "categoryList": [
                {"categoryId": category_id}
                for category_id in category_ids
            ]
        },
    )
    deposit_by_id = {
        str((item.get("category") or {}).get("categoryId") or ""): item
        for item in deposit_result.get("categoryDepositList") or []
        if isinstance(item, dict)
    }
    requirement_result = client._post(
        CATEGORY_REQUIREMENT_PATH,
        {"categoryIds": [int(value) for value in category_ids]},
    )
    requirement_data = client._data(requirement_result)
    qualification_ids = {
        str(value)
        for value in requirement_data.get(
            "needQualificationIdList"
        )
        or []
    }
    checks = client.category_application_checks(category_ids)

    rows = []
    for source in source_rows:
        leaf_id = (
            source["category_path_ids"][-1]
            if source["category_path_ids"]
            else ""
        )
        relation = relation_by_id.get(leaf_id) or {}
        deposit = deposit_by_id.get(leaf_id) or {}
        check = checks.get(leaf_id) or {}
        library_row = category_library.get(leaf_id) or {}
        publish = publish_records.get(source["source_product_id"])
        rows.append(
            {
                **source,
                "category_path": (
                    source.get("category_path")
                    or str(library_row.get("fullPath") or "")
                ),
                "leaf_category_id": leaf_id,
                "individual_deposit_open": library_row.get(
                    "individualDepositOpen"
                ),
                "individual_deposit_cents": library_row.get(
                    "individualDepositCents"
                ),
                "enterprise_deposit_open": library_row.get(
                    "enterpriseDepositOpen"
                ),
                "enterprise_deposit_cents": library_row.get(
                    "enterpriseDepositCents"
                ),
                "relation_state": relation.get("_state")
                or "未查到",
                "relation_reason": str(
                    relation.get("uneffectiveReason")
                    or relation.get("reason")
                    or ""
                ),
                "required_deposit": deposit.get("requiredDeposit"),
                "amount_to_pay": deposit.get("amountToPay"),
                "deposit_balance": deposit.get("depositBalance"),
                "deposit_keys": sorted(deposit.keys()),
                "qualification_required": leaf_id
                in qualification_ids,
                "blue_check_state": check.get("state"),
                "blue_check_label": check.get("label"),
                "blue_check_reason": check.get("reason"),
                "zhiniao_status": (
                    "success" if publish else "not_in_success_records"
                ),
                "zhiniao_wechat_product_id": (
                    str(publish.get("wechat_product_id") or "")
                    if publish
                    else ""
                ),
            }
        )

    evidence = {
        "source_database": str(SOURCE_DB),
        "isolated_database": str(TARGET_DB),
        "target_store": {
            "id": store.id,
            "name": store.name,
            "app_id": store.app_id,
        },
        "online_product_snapshot": online_product_snapshot,
        "package_count": len(source_rows),
        "unique_source_products": len(
            {row["source_product_id"] for row in source_rows}
        ),
        "unique_leaf_categories": len(category_ids),
        "zhiniao_success_records": len(publish_records),
        "official_deposit_summary": {
            "code": deposit_result.get("code"),
            "error_message": deposit_result.get("errorMessage"),
            "deposit_balance": deposit_result.get("depositBalance"),
            "real_deposit_balance": deposit_result.get(
                "realDepositBalance"
            ),
            "operation_required_deposit": deposit_result.get(
                "operationRequiredDeposit"
            ),
        },
        "category_count_by_required_deposit": dict(
            Counter(
                str(
                    (deposit_by_id.get(category_id) or {}).get(
                        "requiredDeposit"
                    )
                )
                for category_id in category_ids
            )
        ),
        "product_count_by_required_deposit": dict(
            Counter(
                str(
                    (deposit_by_id.get(row["leaf_category_id"]) or {}).get(
                        "requiredDeposit"
                    )
                )
                for row in rows
            )
        ),
        "product_count_by_blue_state": dict(
            Counter(str(row["blue_check_state"]) for row in rows)
        ),
        "rows": rows,
    }
    output.mkdir(parents=True, exist_ok=True)
    target = output / "category-deposit-evidence.json"
    target.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                key: value
                for key, value in evidence.items()
                if key != "rows"
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
