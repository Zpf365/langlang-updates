from __future__ import annotations

import json
from datetime import datetime, time, timedelta
from typing import Any

from ..db import SHANGHAI_TZ, Database


SHIPPED_ORDER_STATUSES = {21, 30}
SIGNED_ORDER_STATUS = 100
STAGNANT_SECONDS = 48 * 60 * 60
FINAL_AFTER_SALE_MARKERS = (
    "SUCCESS",
    "CLOSED",
    "CANCEL",
    "REJECT",
    "REFUSE",
)


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _json_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _shipment_time(order: dict[str, Any]) -> int:
    raw = _json_dict(order.get("raw_json"))
    delivery_info = (
        ((raw.get("order_detail") or {}).get("delivery_info"))
        if isinstance(raw.get("order_detail"), dict)
        else {}
    )
    if not isinstance(delivery_info, dict):
        return 0
    values = [
        _int(delivery_info.get("ship_done_time")),
        _int(delivery_info.get("delivery_time")),
    ]
    for item in delivery_info.get("delivery_product_info") or []:
        if isinstance(item, dict):
            values.append(_int(item.get("delivery_time")))
    return max(values, default=0)


def _return_waybill(row: dict[str, Any]) -> str:
    raw = _json_dict(row.get("raw_json"))
    return_info = raw.get("return_info")
    if not isinstance(return_info, dict):
        return ""
    return str(return_info.get("waybill_id") or "").strip()


def _is_completed_return(row: dict[str, Any]) -> bool:
    status = str(row.get("status") or "").strip().upper()
    after_sale_type = str(row.get("after_sale_type") or "").strip().upper()
    return bool(
        "RETURN_SUCCESS" in status
        or (
            after_sale_type == "RETURN"
            and (
                _int(row.get("update_time")) > 0
                or _return_waybill(row)
            )
            and "RETURN_SUCCESS" in status
        )
    )


def _is_open_after_sale(row: dict[str, Any]) -> bool:
    status = str(row.get("status") or "").strip().upper()
    return bool(
        row.get("after_sale_order_id")
        and not any(marker in status for marker in FINAL_AFTER_SALE_MARKERS)
    )


def _after_sale_priority(rows: list[dict[str, Any]]) -> tuple[str, str]:
    for row in rows:
        if _is_completed_return(row):
            return (
                "returned",
                str(row.get("status_text") or "退货退款完成"),
            )
    for row in rows:
        if _is_open_after_sale(row):
            return (
                "after_sale",
                str(row.get("status_text") or "售后处理中"),
            )
    return "", ""


def logistics_monitor_payload(
    db: Database,
    payload: dict[str, Any] | None = None,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    values = payload if isinstance(payload, dict) else {}
    store_id = values.get("store_id")
    try:
        selected_store_id = int(store_id) if store_id not in (None, "") else None
    except (TypeError, ValueError):
        selected_store_id = None
    current = now or datetime.now(SHANGHAI_TZ)
    if current.tzinfo is None:
        current = current.replace(tzinfo=SHANGHAI_TZ)
    now_ts = int(current.timestamp())
    day_start_ts = int(
        datetime.combine(current.date(), time.min, SHANGHAI_TZ).timestamp()
    )
    orders = db.list_orders(store_id=selected_store_id, limit=100000)
    after_sales = db.list_after_sales(store_id=selected_store_id)
    by_order: dict[tuple[int, str], list[dict[str, Any]]] = {}
    for row in after_sales:
        key = (int(row.get("store_id") or 0), str(row.get("order_id") or ""))
        if key[1]:
            by_order.setdefault(key, []).append(row)
    for rows in by_order.values():
        rows.sort(
            key=lambda row: (
                _int(row.get("update_time")),
                _int(row.get("create_time")),
            ),
            reverse=True,
        )

    abnormal_rows: list[dict[str, Any]] = []
    in_transit = 0
    awaiting_pickup = 0
    transporting = 0
    signed_7d = 0
    returned = 0
    stagnant = 0
    after_sale_count = 0
    today_abnormal = 0
    for order in orders:
        status = _int(order.get("status"))
        update_time = _int(order.get("update_time"))
        ship_time = _shipment_time(order)
        key = (
            int(order.get("store_id") or 0),
            str(order.get("order_id") or ""),
        )
        related = by_order.get(key, [])
        if (
            status == SIGNED_ORDER_STATUS
            and update_time >= now_ts - 7 * 86400
        ):
            signed_7d += 1
        waybill_id = str(order.get("waybill_id") or "").strip()
        if status in {20, *SHIPPED_ORDER_STATUSES} and waybill_id:
            in_transit += 1
            if status == 20 or not ship_time:
                awaiting_pickup += 1
            else:
                transporting += 1

        abnormal_kind, trace_text = _after_sale_priority(related)
        abnormal_at = 0
        elapsed_base = update_time
        if abnormal_kind == "returned":
            returned += 1
            abnormal_at = max(
                (_int(row.get("update_time")) for row in related),
                default=update_time,
            )
            elapsed_base = abnormal_at
        elif abnormal_kind == "after_sale":
            after_sale_count += 1
            abnormal_at = max(
                (_int(row.get("update_time")) for row in related),
                default=update_time,
            )
            elapsed_base = abnormal_at
        elif (
            status in SHIPPED_ORDER_STATUSES
            and waybill_id
            and now_ts - max(update_time, ship_time) > STAGNANT_SECONDS
        ):
            abnormal_kind = "stagnant"
            trace_text = (
                f"平台最后订单状态：{order.get('status_text') or status}"
            )
            elapsed_base = max(update_time, ship_time)
            abnormal_at = elapsed_base + STAGNANT_SECONDS
            stagnant += 1
        if not abnormal_kind:
            continue
        if abnormal_at >= day_start_ts:
            today_abnormal += 1
        latest_after_sale = related[0] if related else {}
        abnormal_rows.append(
            {
                "store_id": key[0],
                "store_name": str(order.get("store_name") or ""),
                "order_id": key[1],
                "status": status,
                "status_text": str(order.get("status_text") or ""),
                "carrier": str(order.get("carrier") or ""),
                "waybill_id": waybill_id,
                "logistics_status": abnormal_kind,
                "logistics_status_text": {
                    "returned": "已退回",
                    "stagnant": "停滞",
                    "after_sale": "售后",
                }[abnormal_kind],
                "elapsed_seconds": max(0, now_ts - elapsed_base),
                "latest_trace": trace_text or "暂无真实轨迹明细",
                "trace_source": (
                    "本地已同步订单与售后状态；轨迹明细接口未接入"
                    if not related
                    else "本地已同步售后状态；轨迹明细接口未接入"
                ),
                "abnormal_at": abnormal_at,
                "sort_time": ship_time or update_time,
                "after_sale_status": str(
                    latest_after_sale.get("status_text") or ""
                ),
            }
        )

    metric_filter = str(values.get("metric_filter") or "abnormal")
    filterable_kinds = {
        "returned": "returned",
        "stagnant": "stagnant",
        "after_sale": "after_sale",
    }
    count_only_filters = {
        "in_transit": "在途仅做数量统计，不展开订单列表。",
        "awaiting_pickup": "待揽收仅做数量统计，不展开订单列表。",
        "transporting": "运输中仅做数量统计，不展开订单列表。",
        "signed_7d": "近7天已签收仅做数量统计，不列入异常列表。",
    }
    if metric_filter in filterable_kinds:
        visible_rows = [
            row
            for row in abnormal_rows
            if row["logistics_status"] == filterable_kinds[metric_filter]
        ]
        empty_message = "没有符合条件的异常订单"
    elif metric_filter == "today_abnormal":
        visible_rows = [
            row for row in abnormal_rows if row["abnormal_at"] >= day_start_ts
        ]
        empty_message = "今天没有新增异常订单"
    elif metric_filter in count_only_filters:
        visible_rows = []
        empty_message = count_only_filters[metric_filter]
    else:
        metric_filter = "abnormal"
        visible_rows = list(abnormal_rows)
        empty_message = "没有符合条件的异常订单"

    sort_mode = str(values.get("sort") or "elapsed")
    if sort_mode == "ship_time":
        visible_rows.sort(
            key=lambda row: (row["sort_time"], row["elapsed_seconds"]),
        )
    else:
        visible_rows.sort(
            key=lambda row: (
                row["elapsed_seconds"],
                row["sort_time"],
            ),
            reverse=True,
        )
    page_size = max(1, min(10000, _int(values.get("page_size"), 50)))
    total = len(visible_rows)
    page_count = max(1, (total + page_size - 1) // page_size)
    page = max(1, min(page_count, _int(values.get("page"), 1)))
    start = (page - 1) * page_size
    stores = [
        {"id": store.id, "name": store.name}
        for store in db.list_stores()
    ]
    return {
        "generated_at": current.strftime("%Y-%m-%d %H:%M:%S"),
        "source": "本地已同步订单与售后状态；物流轨迹明细接口未接入",
        "active_filter": metric_filter,
        "empty_message": empty_message,
        "stores": stores,
        "metrics": {
            "in_transit": in_transit,
            "awaiting_pickup": awaiting_pickup,
            "transporting": transporting,
            "returned": returned,
            "stagnant": stagnant,
            "after_sale": after_sale_count,
            "abnormal": returned + stagnant + after_sale_count,
            "signed_7d": signed_7d,
            "today_abnormal": today_abnormal,
        },
        "rows": visible_rows[start : start + page_size],
        "total": total,
        "page": page,
        "page_size": page_size,
        "page_count": page_count,
    }
