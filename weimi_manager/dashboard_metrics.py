from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any

from .models import is_unclaimed_gift_order, order_counts_as_transaction


@dataclass(frozen=True)
class DashboardRankRow:
    label: str
    values: tuple[int | str, ...]


@dataclass(frozen=True)
class DashboardSnapshot:
    store_orders: int = 0
    store_receipt: int = 0
    procurement_orders: int = 0
    procurement_amount: int = 0
    procurement_profit: int = 0
    procurement_margin: float = 0.0
    status_counts: dict[str, int] = field(default_factory=dict)
    today_hourly: list[int] = field(default_factory=lambda: [0] * 24)
    yesterday_hourly: list[int] = field(default_factory=lambda: [0] * 24)
    store_rank: list[DashboardRankRow] = field(default_factory=list)
    procurement_rank: list[DashboardRankRow] = field(default_factory=list)
    profit_rank: list[DashboardRankRow] = field(default_factory=list)


PERIOD_DAYS = {
    "今日": 1,
    "昨日": 1,
    "近7天": 7,
    "近15天": 15,
    "近30天": 30,
}


def period_bounds(period: str, today: date | None = None) -> tuple[datetime, datetime]:
    current = today or date.today()
    days = PERIOD_DAYS.get(period, 1)
    end_date = current - timedelta(days=1) if period == "昨日" else current
    start_date = end_date - timedelta(days=days - 1)
    return (
        datetime.combine(start_date, time.min),
        datetime.combine(end_date, time.max),
    )


def _parse_time(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    for candidate in (text, text.replace("Z", "+00:00")):
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError:
            continue
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone().replace(tzinfo=None)
        return parsed
    for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(text[:19], pattern)
        except ValueError:
            continue
    return None


def _within(value: Any, start: datetime, end: datetime) -> bool:
    parsed = _parse_time(value)
    return parsed is not None and start <= parsed <= end


def _hourly_counts(
    orders: list[dict[str, Any]],
    start: datetime,
    end: datetime,
) -> list[int]:
    counts = [0] * 24
    start_ts = int(start.timestamp())
    end_ts = int(end.timestamp())
    for row in orders:
        created = int(row.get("create_time") or 0)
        if start_ts <= created <= end_ts:
            counts[datetime.fromtimestamp(created).hour] += 1
    return counts


def build_dashboard_snapshot(
    db: Any,
    period: str,
    data_source: str = "全部数据",
) -> DashboardSnapshot:
    start, end = period_bounds(period)
    start_ts = int(start.timestamp())
    end_ts = int(end.timestamp())
    stores = list(db.list_stores())
    store_lookup = {store.id: store for store in stores}
    all_orders: list[dict[str, Any]] = []
    period_orders: list[dict[str, Any]] = []
    visible_orders: list[dict[str, Any]] = []
    store_aggregates: dict[int, dict[str, int]] = {
        store.id: {"amount": 0, "orders": 0, "procured": 0}
        for store in stores
    }
    for store in stores:
        rows = db.list_orders(
            store_id=store.id,
            start_time=start_ts,
            end_time=end_ts,
        )
        period_orders.extend(rows)
        visible = [
            row
            for row in rows
            if not is_unclaimed_gift_order(row)
        ]
        visible_orders.extend(visible)
        valid = [
            row
            for row in visible
            if order_counts_as_transaction(row.get("status"))
        ]
        all_orders.extend(valid)
        store_aggregates[store.id]["orders"] += len(valid)
        store_aggregates[store.id]["amount"] += sum(
            int(row.get("total_amount") or 0) for row in valid
        )

    links = list(db.list_procurement_links())
    linked_orders: set[tuple[int, str]] = {
        (int(link.get("store_id") or 0), str(link.get("order_id") or ""))
        for link in links
    }
    for store_id, order_id in linked_orders:
        if store_id in store_aggregates:
            store_aggregates[store_id]["procured"] += 1

    purchase_rows = [
        row
        for row in db.list_procurement_orders()
        if _within(row.get("updated_at") or row.get("fetched_at"), start, end)
    ]
    purchase_amount = sum(int(row.get("pay_amount") or 0) for row in purchase_rows)

    workbench = db.get_order_workbench()
    linked_cost: dict[tuple[int, str], tuple[int, set[int]]] = {}
    procurement_lookup = {
        int(row["id"]): row for row in db.list_procurement_orders()
    }
    for link in links:
        key = (int(link.get("store_id") or 0), str(link.get("order_id") or ""))
        procurement_id = int(link.get("procurement_order_id") or 0)
        purchase = procurement_lookup.get(procurement_id, {})
        amount = int(purchase.get("pay_amount") or link.get("pay_amount") or 0)
        total, ids = linked_cost.get(key, (0, set()))
        if procurement_id not in ids:
            total += amount
            ids.add(procurement_id)
        linked_cost[key] = (total, ids)

    profit_by_store: dict[int, int] = {}
    profit_order_counts: dict[int, int] = {}
    for row in visible_orders:
        if not order_counts_as_transaction(row.get("status")):
            continue
        key = (int(row.get("store_id") or 0), str(row.get("order_id") or ""))
        explicit = int((workbench.get(key) or {}).get("profit") or 0)
        if explicit:
            profit = explicit
        elif key in linked_cost:
            profit = int(row.get("total_amount") or 0) - linked_cost[key][0]
        else:
            continue
        store_id = key[0]
        profit_by_store[store_id] = profit_by_store.get(store_id, 0) + profit
        profit_order_counts[store_id] = profit_order_counts.get(store_id, 0) + 1

    status_counts = {
        "待采购": 0,
        "待发货": 0,
        "已发货": 0,
        "退款中": 0,
        "交易成功": 0,
        "异常订单": 0,
    }
    for row in visible_orders:
        status = int(row.get("status") or 0)
        key = (int(row.get("store_id") or 0), str(row.get("order_id") or ""))
        if status == 20 and key not in linked_orders:
            status_counts["待采购"] += 1
        elif status == 20:
            status_counts["待发货"] += 1
        elif status in {21, 30}:
            status_counts["已发货"] += 1
        elif status == 200:
            status_counts["退款中"] += 1
        elif status == 100:
            status_counts["交易成功"] += 1
        elif status in {12, 13}:
            status_counts["异常订单"] += 1

    today_start = datetime.combine(date.today(), time.min)
    yesterday_start = today_start - timedelta(days=1)
    yesterday_end = today_start - timedelta(microseconds=1)
    today_orders = [
        row
        for row in db.list_orders(
            start_time=int(today_start.timestamp()),
            end_time=int(
                today_start.replace(
                    hour=23,
                    minute=59,
                    second=59,
                ).timestamp()
            ),
            limit=100000,
        )
        if not is_unclaimed_gift_order(row)
        and order_counts_as_transaction(row.get("status"))
    ]
    yesterday_orders = [
        row
        for row in db.list_orders(
            start_time=int(yesterday_start.timestamp()),
            end_time=int(yesterday_end.timestamp()),
            limit=100000,
        )
        if not is_unclaimed_gift_order(row)
        and order_counts_as_transaction(row.get("status"))
    ]

    store_rank = sorted(
        (
            DashboardRankRow(
                store_lookup[store_id].name,
                (
                    values["amount"],
                    values["orders"],
                    values["procured"],
                ),
            )
            for store_id, values in store_aggregates.items()
            if store_id in store_lookup
        ),
        key=lambda row: int(row.values[0]),
        reverse=True,
    )
    purchase_counts: dict[str, int] = {}
    for row in purchase_rows:
        label = str(row.get("account_label") or row.get("platform") or "未命名账号")
        purchase_counts[label] = purchase_counts.get(label, 0) + 1
    procurement_rank = [
        DashboardRankRow(label, (count,))
        for label, count in sorted(
            purchase_counts.items(),
            key=lambda item: item[1],
            reverse=True,
        )
    ]
    profit_rank = sorted(
        (
            DashboardRankRow(
                store_lookup[store_id].name,
                (profit_order_counts.get(store_id, 0), profit),
            )
            for store_id, profit in profit_by_store.items()
            if store_id in store_lookup
        ),
        key=lambda row: int(row.values[1]),
        reverse=True,
    )

    include_store = data_source in {"全部数据", "店铺数据"}
    include_procurement = data_source in {"全部数据", "采购数据"}
    store_orders = len(all_orders) if include_store else 0
    store_receipt = (
        sum(int(row.get("total_amount") or 0) for row in all_orders)
        if include_store
        else 0
    )
    purchase_count = len(purchase_rows) if include_procurement else 0
    purchase_amount = purchase_amount if include_procurement else 0
    procurement_profit = (
        sum(profit_by_store.values()) if include_procurement else 0
    )
    margin = (
        (procurement_profit / purchase_amount) * 100
        if purchase_amount
        else 0.0
    )
    return DashboardSnapshot(
        store_orders=store_orders,
        store_receipt=store_receipt,
        procurement_orders=purchase_count,
        procurement_amount=purchase_amount,
        procurement_profit=procurement_profit,
        procurement_margin=margin,
        status_counts=status_counts,
        today_hourly=_hourly_counts(
            today_orders,
            today_start,
            today_start.replace(hour=23, minute=59, second=59),
        ),
        yesterday_hourly=_hourly_counts(
            yesterday_orders,
            yesterday_start,
            yesterday_end,
        ),
        store_rank=store_rank if include_store else [],
        procurement_rank=procurement_rank if include_procurement else [],
        profit_rank=profit_rank if include_procurement else [],
    )
