from __future__ import annotations

import argparse
import json
import os
import tempfile
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any

from openpyxl import Workbook

from weimi_manager.db import Database
from weimi_manager.shop_private import ShopPrivateClient
from weimi_manager.web_session import WechatWebSession


PRINT_LOCK = Lock()


def emit(payload: dict[str, Any]) -> None:
    line = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    with PRINT_LOCK:
        print(line, flush=True)


@dataclass(frozen=True)
class Candidate:
    product_id: str
    sku_id: str
    sale_price: int
    activity_price: int


def key_of(row: dict[str, Any]) -> tuple[str, str]:
    product = row.get("product")
    product = product if isinstance(product, dict) else {}
    return (
        str(product.get("productId") or row.get("productId") or ""),
        str(product.get("skuId") or row.get("skuId") or ""),
    )


def reason_of(row: dict[str, Any]) -> str:
    return str(
        row.get("invalidString")
        or row.get("invalidStringPrecheck")
        or ""
    ).strip()


def validate_candidates(
    client: ShopPrivateClient,
    activity_id: int,
    rows: list[dict[str, Any]],
    discount_basis_points: int,
    min_discount_cents: int,
) -> tuple[list[Candidate], Counter[str]]:
    candidates: list[Candidate] = []
    invalid_reasons: Counter[str] = Counter()
    for start in range(0, len(rows), 200):
        batch = rows[start : start + 200]
        validate_payload = []
        source_by_key: dict[tuple[str, str], dict[str, Any]] = {}
        for row in batch:
            key = key_of(row)
            if not all(key):
                invalid_reasons["商品ID或SKU ID缺失"] += 1
                continue
            source_by_key[key] = row
            validate_payload.append(
                {
                    "productId": key[0],
                    "skuId": key[1],
                    "activityStockNum": 0,
                    "activityPrice": 1,
                    "sellingPointWording": "",
                    "stockMode": 1,
                    "applyType": 0,
                }
            )
        if not validate_payload:
            continue
        validation = client.activity_validate_products(
            activity_id,
            validate_payload,
        )
        validated_keys: set[tuple[str, str]] = set()
        for result in validation.get("resultList") or []:
            if not isinstance(result, dict):
                continue
            key = key_of(result)
            validated_keys.add(key)
            reason = reason_of(result)
            try:
                valid_price = int(result.get("validPrice") or 0)
            except (TypeError, ValueError):
                valid_price = 0
            if reason:
                invalid_reasons[reason.splitlines()[0][:180]] += 1
                continue
            source = source_by_key.get(key, {})
            try:
                sale_price = int(
                    result.get("salePrice")
                    or source.get("salePrice")
                    or 0
                )
            except (TypeError, ValueError):
                sale_price = 0
            if sale_price <= 0:
                invalid_reasons["平台未返回商品售价"] += 1
                continue
            configured_price = min(
                int(round(sale_price * discount_basis_points / 10000)),
                sale_price - min_discount_cents,
            )
            activity_price = (
                min(configured_price, valid_price)
                if valid_price > 0
                else configured_price
            )
            if activity_price <= 0:
                invalid_reasons["按折扣计算后活动价无效"] += 1
                continue
            candidates.append(
                Candidate(
                    product_id=key[0],
                    sku_id=key[1],
                    sale_price=sale_price,
                    activity_price=activity_price,
                )
            )
        for key in source_by_key.keys() - validated_keys:
            invalid_reasons["平台未返回SKU校验结果"] += 1
    return candidates, invalid_reasons


def load_candidates(
    client: ShopPrivateClient,
    activity_id: int,
    discount_basis_points: int,
    min_discount_cents: int,
) -> tuple[list[Candidate], dict[str, Any]]:
    submitted = client.activity_submitted_products(activity_id)
    submitted_keys = {key_of(row) for row in submitted if all(key_of(row))}
    eligible = client.activity_eligible_products(activity_id, page_size=50)
    seen: set[tuple[str, str]] = set()
    available_rows = []
    already_submitted = 0
    invalid_now = 0
    for row in eligible:
        key = key_of(row)
        if not all(key) or key in seen:
            continue
        seen.add(key)
        if key in submitted_keys:
            already_submitted += 1
            continue
        reason = reason_of(row)
        if reason:
            invalid_now += 1
            continue
        available_rows.append(row)
    candidates, invalid_reasons = validate_candidates(
        client,
        activity_id,
        available_rows,
        discount_basis_points,
        min_discount_cents,
    )
    metrics = {
        "submitted_before": len(submitted_keys),
        "eligible_now": len(seen),
        "already_submitted_in_eligible": already_submitted,
        "invalid_before_validation": invalid_now,
        "validated_candidates": len(candidates),
        "validation_invalid": sum(invalid_reasons.values()),
        "invalid_reasons": dict(invalid_reasons.most_common(20)),
    }
    return candidates, metrics


def write_batch(
    batch: list[Candidate],
    directory: Path,
    index: int,
) -> Path:
    path = directory / f"activity_batch_{index:04d}.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "sheet1"
    sheet.append(
        [
            "商品ID（SPU id）",
            "规格ID（SKU id）",
            "活动报名价（元）",
        ]
    )
    for item in batch:
        sheet.append(
            [
                item.product_id,
                item.sku_id,
                item.activity_price / 100,
            ]
        )
    workbook.save(path)
    return path


def submit_store(
    db: Database,
    data_dir: Path,
    store_id: int,
    activity_id: int,
    max_batches: int,
    discount_basis_points: int,
    min_discount_cents: int,
) -> dict[str, Any]:
    store = db.get_store(store_id)
    if store is None:
        return {"store_id": store_id, "ok": False, "error": "店铺不存在"}
    if store.web_status != "已登录":
        return {
            "store_id": store_id,
            "store": store.name,
            "ok": False,
            "error": f"网页{store.web_status}",
        }
    started = time.time()
    try:
        client = ShopPrivateClient(db, store)
        candidates, metrics = load_candidates(
            client,
            activity_id,
            discount_basis_points,
            min_discount_cents,
        )
    except Exception as exc:
        return {
            "store_id": store_id,
            "store": store.name,
            "ok": False,
            "error": f"预检失败：{exc}",
        }
    emit(
        {
            "event": "prepared",
            "store_id": store_id,
            "store": store.name,
            "activity_id": activity_id,
            **metrics,
        }
    )
    batches = [
        candidates[index : index + 200]
        for index in range(0, len(candidates), 200)
    ][: max(0, int(max_batches))]
    submitted_count = 0
    returned_count = 0
    skipped_count = 0
    failures: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(
        prefix=f"langlang_platform_{store_id}_"
    ) as temp_dir:
        directory = Path(temp_dir)
        for index, batch in enumerate(batches, 1):
            path = write_batch(batch, directory, index)
            emit(
                {
                    "event": "batch_start",
                    "store_id": store_id,
                    "store": store.name,
                    "activity_id": activity_id,
                    "batch": index,
                    "batch_count": len(batches),
                    "rows": len(batch),
                }
            )
            last_error = ""
            for attempt in range(1, 4):
                try:
                    result = WechatWebSession(
                        db,
                        data_dir,
                    ).bulk_register_activity_products(
                        store,
                        activity_id,
                        path,
                        expected_count=len(batch),
                    )
                    returned_count += int(result.get("count") or 0)
                    submitted_count += min(
                        int(result.get("valid_count") or 0),
                        len(batch),
                    )
                    skipped_count += int(result.get("skipped_count") or 0)
                    db.record_activity_operation(
                        "活动商品提交",
                        "成功",
                        store.id,
                        store.name,
                        int(activity_id),
                        int(result.get("count") or 0),
                        (
                            f"微信原后台批量导入第 {index} 批，"
                            f"确认 {result.get('count') or 0} 个SKU"
                        ),
                        {
                            key: result.get(key)
                            for key in (
                                "count",
                                "expected_count",
                                "recognized_products",
                                "imported_count",
                                "valid_count",
                                "skipped_count",
                                "shortfall",
                            )
                        },
                    )
                    emit(
                        {
                            "event": "batch_done",
                            "store_id": store_id,
                            "store": store.name,
                            "activity_id": activity_id,
                            "batch": index,
                            "rows": len(batch),
                            "confirmed": int(result.get("count") or 0),
                            "valid": int(result.get("valid_count") or 0),
                            "skipped": int(result.get("skipped_count") or 0),
                            "shortfall": int(result.get("shortfall") or 0),
                            "attempt": attempt,
                        }
                    )
                    last_error = ""
                    break
                except Exception as exc:
                    last_error = f"{type(exc).__name__}: {exc}"
                    emit(
                        {
                            "event": "batch_retry",
                            "store_id": store_id,
                            "store": store.name,
                            "activity_id": activity_id,
                            "batch": index,
                            "attempt": attempt,
                            "error": last_error,
                        }
                    )
                    if attempt < 3:
                        time.sleep(3 * attempt)
            if last_error:
                failures.append(
                    {
                        "batch": index,
                        "row_count": len(batch),
                        "error": last_error,
                    }
                )
                db.record_activity_operation(
                    "活动商品提交",
                    "失败",
                    store.id,
                    store.name,
                    int(activity_id),
                    len(batch),
                    f"微信原后台批量导入第 {index} 批失败：{last_error}",
                )
    return {
        "store_id": store_id,
        "store": store.name,
        "activity_id": activity_id,
        "ok": not failures,
        "validated": len(candidates),
        "scheduled_batches": len(batches),
        "valid_count_reported": submitted_count,
        "confirmed": returned_count,
        "skipped": skipped_count,
        "failures": failures,
        "seconds": round(time.time() - started, 1),
        "initial_metrics": metrics,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "通过微信小店原版活动页批量导入商品。"
            "商品最终提交只调用原后台页面流程。"
        )
    )
    parser.add_argument("--activity-id", type=int, required=True)
    parser.add_argument("--store-ids", required=True)
    parser.add_argument("--max-batches", type=int, default=999)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--discount", type=float, default=9.0)
    parser.add_argument("--minimum-discount", type=float, default=1.0)
    parser.add_argument(
        "--data-dir",
        default=str(
            Path(os.environ.get("LOCALAPPDATA") or Path.home())
            / "LangLang"
            / "data"
        ),
    )
    args = parser.parse_args()
    store_ids = [
        int(value.strip())
        for value in args.store_ids.split(",")
        if value.strip()
    ]
    data_dir = Path(args.data_dir)
    db = Database(data_dir / "weimi_manager.db")
    discount_basis_points = max(
        100,
        min(10000, int(round(float(args.discount) * 1000))),
    )
    min_discount_cents = max(0, int(round(args.minimum_discount * 100)))
    results = []
    with ThreadPoolExecutor(
        max_workers=max(1, min(int(args.workers), len(store_ids) or 1))
    ) as executor:
        futures = {
            executor.submit(
                submit_store,
                db,
                data_dir,
                store_id,
                args.activity_id,
                args.max_batches,
                discount_basis_points,
                min_discount_cents,
            ): store_id
            for store_id in store_ids
        }
        for future in as_completed(futures):
            store_id = futures[future]
            try:
                result = future.result()
            except Exception as exc:
                result = {
                    "store_id": store_id,
                    "ok": False,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            results.append(result)
            emit({"event": "store_done", **result})
    print(
        json.dumps(
            {"event": "finished", "results": results},
            ensure_ascii=False,
            indent=2,
        ),
        flush=True,
    )
    return 0 if all(result.get("ok") for result in results) else 2


if __name__ == "__main__":
    raise SystemExit(main())
