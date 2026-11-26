from __future__ import annotations

import json
import time
from typing import Any


UNSUPPORTED_ENABLED_FLAGS = {
    "enable_invite": "达人邀约",
    "enable_cancel_price_drop": "取消单品直降",
    "enable_direct_discount": "直降享权益",
}


def run_hosted_store(db: Any, service: Any, store: Any, config: dict[str, Any]) -> str:
    """Run the supported web-console hosting actions for one store."""
    unsupported = [
        label
        for key, label in UNSUPPORTED_ENABLED_FLAGS.items()
        if config.get(key)
    ]
    if unsupported:
        message = "以下项目尚未接入接口：" + "、".join(unsupported)
        db.log_hosted_run(store.id, "未执行", "failed", message)
        return f"{store.name}: {message}"

    actions: list[str] = []
    errors: list[str] = []
    try:
        blacklist = db.violation_blacklist_ids()
        products = [
            row
            for row in db.list_products(store_id=store.id)
            if str(row.get("product_id") or "") not in blacklist
        ]
        active_products = [
            row for row in products if int(row.get("status") or 0) == 5
        ]
        active_ids = [
            str(row["product_id"])
            for row in active_products
            if row.get("product_id")
        ]
        fetch_all_active = getattr(
            service,
            "fetch_active_product_ids",
            None,
        )
        if callable(fetch_all_active):
            active_ids = [
                product_id
                for product_id in fetch_all_active(store)
                if product_id not in blacklist
            ]

        if config.get("enable_league") and active_ids:
            result = service.promote_products_to_league(
                store,
                active_ids,
                float(config.get("league_commission_percent") or 20),
            )
            requested = int(
                result.get("requested_count") or len(active_ids)
            )
            failures = int(
                result.get("failed_count")
                if result.get("failed_count") is not None
                else sum(
                    1
                    for row in result.get("result_info_list") or []
                    if isinstance(row, dict)
                    and int(row.get("errcode") or 0) != 0
                )
            )
            actions.append(
                f"全店联盟佣金 {max(0, requested - failures)}/{requested}"
            )
            if int(result.get("errcode") or 0) != 0:
                errors.append(
                    "添加联盟推广："
                    + str(result.get("errmsg") or "微信接口返回失败")
                )
            if failures:
                errors.append(f"添加联盟推广失败 {failures} 个")

        if config.get("enable_latest_league"):
            league = service.fetch_league_products(store)
            actions.append(f"读取联盟商品 {len(league.get('items') or [])}")

        if config.get("enable_commission"):
            league = service.fetch_league_products(store)
            league_ids = [
                str(row.get("product_id") or "")
                for row in league.get("items") or []
                if isinstance(row, dict) and row.get("product_id")
            ]
            commission_errors = service.update_league_ratio(
                store,
                league_ids,
                float(config.get("product_commission_percent") or 20),
            )
            errors.extend(f"修改佣金：{error}" for error in commission_errors)
            actions.append(f"修改佣金 {max(0, len(league_ids) - len(commission_errors))}")

        if config.get("enable_revoke"):
            ids = [
                str(row["product_id"])
                for row in products
                if int(row.get("status") or 0) == 2
            ][:500]
            for product_id in ids:
                service.product_write(store, "cancel_audit", product_id=product_id)
            actions.append(f"撤销审核 {len(ids)}")

        if config.get("enable_delete_failed"):
            ids = [
                str(row["product_id"])
                for row in products
                if int(row.get("status") or 0) == 3
            ][:500]
            for product_id in ids:
                service.product_write(store, "delete", product_id=product_id)
            actions.append(f"删除审核失败 {len(ids)}")

        if config.get("enable_list"):
            listing_count = max(1, int(config.get("listing_count") or 200))
            ids = [
                str(row["product_id"])
                for row in products
                if int(row.get("status") or 0) == 11
            ][:listing_count]
            if ids:
                service.set_products_listing(store, ids, True)
            actions.append(f"上架 {len(ids)}")

        if config.get("enable_no_traffic"):
            ids = _no_traffic_ids(
                products,
                db.get_product_metrics_map(store.id),
                days=max(1, int(config.get("no_traffic_days") or 30)),
                max_sales=max(0, int(config.get("no_traffic_sales") or 0)),
            )
            if ids:
                service.private_batch_product_action(store, ids, "recycle")
            actions.append(f"清理无流量 {len(ids)}")

        if config.get("enable_cancel_flash_sale"):
            cancel_errors = service.stop_active_limited_discounts(store)
            errors.extend(f"取消限时抢购：{error}" for error in cancel_errors)
            actions.append("取消限时抢购")

        if config.get("enable_prefill_11"):
            try:
                drafts = json.loads(
                    db.get_setting(f"double11_prefill_{store.id}", "[]")
                )
            except (TypeError, ValueError, json.JSONDecodeError):
                drafts = []
            if isinstance(drafts, list) and drafts:
                service.double11_submit(store, drafts)
                actions.append(f"双11预填报 {len(drafts)}")
            else:
                actions.append("双11预填报：无草稿")

        status = "failed" if errors else "success"
        db.log_hosted_run(
            store.id,
            "、".join(actions) or "无动作",
            status,
            "；".join(errors),
        )
        return f"{store.name}: {'；'.join(errors)}" if errors else ""
    except Exception as exc:
        db.log_hosted_run(
            store.id,
            "、".join(actions) or "执行失败",
            "failed",
            str(exc),
        )
        return f"{store.name}: {exc}"


def _no_traffic_ids(
    products: list[dict[str, Any]],
    metrics: dict[str, dict[str, Any]],
    *,
    days: int,
    max_sales: int,
) -> list[str]:
    cutoff = time.time() - max(1, days) * 86400
    selected = []
    for row in products:
        if int(row.get("status") or 0) != 5:
            continue
        product_id = str(row.get("product_id") or "")
        product_metrics = metrics.get(product_id)
        if not product_id or not product_metrics:
            continue
        if int(product_metrics.get("views30") or 0) > 0:
            continue
        if int(product_metrics.get("click_cnt") or 0) > 0:
            continue
        try:
            raw = json.loads(str(row.get("raw_json") or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError):
            raw = {}
        if int(raw.get("total_sold_num") or raw.get("sold_num") or 0) > max_sales:
            continue
        listed_at = 0
        for key in ("_private_listing_time", "listing_time", "create_time", "update_time"):
            try:
                listed_at = int(float(raw.get(key) or 0))
            except (TypeError, ValueError):
                listed_at = 0
            if listed_at > 0:
                break
        if listed_at > 10_000_000_000:
            listed_at /= 1000
        if listed_at <= 0 or listed_at > cutoff:
            continue
        selected.append(product_id)
    return selected[:500]
