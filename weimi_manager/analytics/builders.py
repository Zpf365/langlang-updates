from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from ..db import Database
from .privacy import thumbnail_host, trusted_thumbnail_url


PAID_ORDER_STATUSES = (17, 20, 21, 30, 100)


def business_date(now: datetime | None = None) -> str:
    return (now or datetime.now()).date().isoformat()


def _local_day_start(now: datetime) -> int:
    return int(now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())


def store_daily_payloads(
    db: Database,
    *,
    now: datetime | None = None,
) -> list[tuple[int, dict[str, Any]]]:
    current = now or datetime.now()
    day = business_date(current)
    metrics_map = db.store_metric_map()
    output = []
    for store in db.list_stores():
        metrics = metrics_map.get(int(store.id), {})
        attempt = metrics.get("_sales_attempt_status")
        if not isinstance(attempt, dict):
            attempt = {}
        today_status = str(attempt.get("today") or "")
        observed = bool(
            today_status == "updated"
            and not metrics.get("_sales_is_stale")
        )
        sources = metrics.get("_sales_period_sources")
        if not isinstance(sources, dict):
            sources = {}
        output.append(
            (
                int(store.id),
                {
                    "store_name": str(store.name or ""),
                    "app_id_masked": (
                        f"{str(store.app_id or '')[:2]}…"
                        f"{str(store.app_id or '')[-4:]}"
                        if len(str(store.app_id or "")) >= 8
                        else ""
                    ),
                    "business_date": day,
                    "gmv": (
                        int(metrics.get("gmv_today") or 0)
                        if observed
                        else None
                    ),
                    "paid_orders": (
                        int(metrics.get("today_orders") or 0)
                        if observed
                        else None
                    ),
                    "refund_amount": (
                        int(metrics.get("refund_gmv_today") or 0)
                        if observed
                        else None
                    ),
                    "refund_orders": (
                        int(metrics.get("refund_orders_today") or 0)
                        if observed
                        else None
                    ),
                    "online_products": int(
                        metrics.get("in_sale") or 0
                    ),
                    "new_products_today": int(
                        metrics.get("new_products_today") or 0
                    ),
                    "exposure": int(
                        metrics.get("recommend_exposure") or 0
                    ),
                    "score": metrics.get("dsr"),
                    "last_sync_at": str(
                        metrics.get("_sales_updated_at") or ""
                    ),
                    "data_fresh": bool(
                        observed
                        and not metrics.get("_sales_is_stale")
                    ),
                    "source": str(
                        sources.get("today")
                        or metrics.get("_sales_source")
                        or ""
                    ),
                    "_observed": observed,
                },
            )
        )
    return output


def product_daily_payloads(
    db: Database,
    *,
    exposure_threshold: int = 1000,
    limit: int = 2000,
    now: datetime | None = None,
) -> list[tuple[int, dict[str, Any]]]:
    current = now or datetime.now()
    day = business_date(current)
    today_start = _local_day_start(current)
    yesterday_start = int(
        (current.replace(hour=0, minute=0, second=0, microsecond=0)
         - timedelta(days=1)).timestamp()
    )
    with db.connect() as conn:
        sales_rows = conn.execute(
            """
            SELECT oi.store_id, oi.product_id,
                   COUNT(DISTINCT CASE WHEN o.pay_time>=:today_start OR (
                       o.pay_time=0 AND o.create_time>=:today_start
                   ) THEN o.order_id END) AS today_orders,
                   SUM(CASE WHEN o.pay_time>=:today_start OR (
                       o.pay_time=0 AND o.create_time>=:today_start
                   ) THEN oi.sku_count ELSE 0 END) AS today_units,
                   COUNT(DISTINCT CASE WHEN (
                       (o.pay_time>=:yesterday_start AND
                        o.pay_time<:today_start) OR (
                           o.pay_time=0 AND
                           o.create_time>=:yesterday_start AND
                           o.create_time<:today_start
                       )
                   ) THEN o.order_id END) AS yesterday_orders,
                   SUM(CASE WHEN (
                       (o.pay_time>=:yesterday_start AND
                        o.pay_time<:today_start) OR (
                           o.pay_time=0 AND
                           o.create_time>=:yesterday_start AND
                           o.create_time<:today_start
                       )
                   ) THEN oi.sku_count ELSE 0 END) AS yesterday_units,
                   COALESCE(SUM(CASE WHEN o.pay_time>=:today_start OR (
                       o.pay_time=0 AND o.create_time>=:today_start
                   ) THEN oi.sale_price * oi.sku_count ELSE 0 END), 0)
                       AS today_sales_amount
            FROM order_items oi
            JOIN orders o
              ON o.store_id=oi.store_id AND o.order_id=oi.order_id
            WHERE o.status IN (17, 20, 21, 30, 100)
              AND o.create_time>=:yesterday_start
            GROUP BY oi.store_id, oi.product_id
            """,
            {
                "today_start": today_start,
                "yesterday_start": yesterday_start,
            },
        ).fetchall()
    paid_counts = {
        (int(row["store_id"]), str(row["product_id"])): {
            "today_orders": int(row["today_orders"] or 0),
            "today_units": int(row["today_units"] or 0),
            "yesterday_orders": int(row["yesterday_orders"] or 0),
            "yesterday_units": int(row["yesterday_units"] or 0),
            "today_sales_amount": int(row["today_sales_amount"] or 0),
        }
        for row in sales_rows
    }
    output: list[tuple[int, dict[str, Any]]] = []
    for product in db.list_products():
        store_id = int(product.get("store_id") or 0)
        product_id = str(product.get("product_id") or "")
        if not store_id or not product_id:
            continue
        metrics = db.get_product_metrics_map(store_id).get(product_id, {})
        paid = paid_counts.get(
            (store_id, product_id),
            {
                "today_orders": 0,
                "today_units": 0,
                "yesterday_orders": 0,
                "yesterday_units": 0,
                "today_sales_amount": 0,
            },
        )
        exposure = int(
            metrics.get("recommend_views30")
            or metrics.get("views30")
            or 0
        )
        period_sales = int(metrics.get("sold30") or 0)
        if not (
            paid["today_orders"] > 0
            or paid["yesterday_orders"] > 0
            or period_sales > 0
            or exposure >= max(0, int(exposure_threshold))
        ):
            continue
        try:
            raw = json.loads(str(product.get("raw_json") or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError):
            raw = {}
        raw = raw if isinstance(raw, dict) else {}
        thumbnail = trusted_thumbnail_url(product.get("head_img"))
        payload = {
            "business_date": day,
            "product_id": product_id,
            "title": str(product.get("title") or ""),
            "category_id": str(
                raw.get("category_id")
                or raw.get("categoryId")
                or ""
            ),
            "category_path": str(
                raw.get("category_path")
                or raw.get("categoryPath")
                or raw.get("category_name")
                or ""
            ),
            "status": str(product.get("status_text") or ""),
            "first_listed_at": str(
                raw.get("first_listed_at")
                or raw.get("create_time")
                or ""
            ),
            "last_listed_at": str(
                raw.get("last_listed_at")
                or raw.get("update_time")
                or ""
            ),
            "price": int(product.get("min_price") or 0),
            "stock": int(product.get("stock_total") or 0),
            "sku_count": int(product.get("sku_count") or 0),
            "source_platform": str(
                raw.get("source_platform")
                or raw.get("platform")
                or ""
            ),
            "source_product_id": str(
                raw.get("source_product_id")
                or product.get("out_product_id")
                or ""
            ),
            "thumbnail_url": thumbnail,
            "thumbnail_host": thumbnail_host(thumbnail),
            "thumbnail_updated_at": str(
                product.get("synced_at") or ""
            ),
            "paid_orders_today": paid["today_orders"],
            "paid_orders_yesterday": paid["yesterday_orders"],
            "paid_units_today": paid["today_units"],
            "paid_units_yesterday": paid["yesterday_units"],
            "period_sales": period_sales,
            "exposure": exposure,
            "exposure_people": int(metrics.get("views30") or 0),
            "click_count": int(metrics.get("click_cnt") or 0),
            "click_people": int(metrics.get("click_uv") or 0),
            "paid_orders": paid["today_orders"],
            "sales_count": paid["today_units"],
            "sales_amount": paid["today_sales_amount"],
            "refund_amount": 0,
            "purchase_cost": 0,
            "commission": 0,
            "profit": 0,
        }
        if not thumbnail:
            payload.pop("thumbnail_url", None)
            payload.pop("thumbnail_host", None)
        output.append((store_id, payload))
        if len(output) >= max(1, min(10000, int(limit))):
            break
    return output
