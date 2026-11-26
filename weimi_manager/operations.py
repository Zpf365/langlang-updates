from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from collections import Counter
from datetime import datetime, timedelta
from math import ceil
from typing import Any

from .db import Database
from .models import order_counts_as_transaction


SOURCE_REALTIME = "官方后台实时"
SOURCE_API_T1 = "官方 API T+1"
SOURCE_SQLITE = "本地 SQLite"
SOURCE_CALC = "本地计算"

DEFAULT_OPERATION_RULES: dict[str, float | int] = {
    "score_drop_threshold": 0.3,
    "zero_exposure_hours": 2,
    "zero_sales_days": 2,
    "no_new_product_days": 3,
    "first_response_seconds": 90,
    "no_reply_rate": 10.0,
    "quality_refund_watch_rate": 5.0,
    "high_views_threshold": 500,
    "low_pay_threshold": 5,
}

PLAYBOOKS = (
    {
        "key": "traffic",
        "title": "访客下降",
        "lead": "先区分全店、单商品和单渠道下降，再核对商品状态与硬阻断。",
        "steps": (
            ("0–30 分", "排除下架、售罄、违规与价格库存错误", "确认异常发生在店铺、商品还是渠道。"),
            ("2–6 小时", "保存基线并只改一个变量", "找到最早开始下降的真实指标。"),
            ("24–72 小时", "补强真实内容和合规流量入口", "曝光恢复，同时点击率和支付转化不下降。"),
        ),
    },
    {
        "key": "launch",
        "title": "新品破零",
        "lead": "新品先做发布体检，再做真实内容承接和 14 天分层。",
        "steps": (
            ("0–24 小时", "确认可搜索、可打开、可加购、可结算", "商品发布链路完整。"),
            ("1–3 天", "补齐商品卡、二维码、短链和真实用户触点", "出现可解释的真实来源。"),
            ("14–30 天", "保留、重做或淘汰", "商品进入明确生命周期层级。"),
        ),
    },
    {
        "key": "click",
        "title": "点击率低",
        "lead": "先检查曝光人群匹配，再单变量测试主图、标题或到手价。",
        "steps": (
            ("判断", "拆分推荐、搜索和其他渠道点击率", "定位下降集中的渠道。"),
            ("首轮", "只改主图或首屏标题", "点击率提升且退款率不升高。"),
            ("延伸", "补齐价格、库存和信任信息", "进入详情后的加购率同步改善。"),
        ),
    },
    {
        "key": "conversion",
        "title": "转化低",
        "lead": "按浏览、点击、加购、创建订单和支付逐层判断。",
        "steps": (
            ("详情", "修正图文、SKU、尺寸和材质承诺", "加购率改善且退款不上升。"),
            ("加购", "检查价值感、运费和优惠门槛", "加购率改善。"),
            ("支付", "修复结算、库存和地址支付摩擦", "加购到支付转化提升。"),
        ),
    },
    {
        "key": "review",
        "title": "差评 / 品退",
        "lead": "先按商品、SKU、原因和批次止损，再用真实经营修复指标。",
        "steps": (
            ("30 分钟", "聚类售后和评价原因", "形成可处理的问题桶。"),
            ("24 小时", "处理售后、改图改描述、修正包装", "同类新增差评和品退下降。"),
            ("7–30 天", "提升低退款商品成交占比，等待旧记录滚动", "差评率与品质退款率下降。"),
        ),
    },
    {
        "key": "service",
        "title": "客服超时",
        "lead": "优先处理仍在有效窗口、尚未回复的售后和高客单会话。",
        "steps": (
            ("今日", "按超时、售后和金额排序", "未回复会话数量下降。"),
            ("24 小时", "把高频问题前置到商品详情和快捷回复", "首响不超过 90 秒。"),
            ("7 天", "按真实咨询热力排班", "不回复率不超过 10% 且连续下降。"),
        ),
    },
    {
        "key": "lifecycle",
        "title": "零销量 / 未上货",
        "lead": "区分没曝光、没点击、没支付，以及供给和商品池老化。",
        "steps": (
            ("零销量", "判断卡在漏斗哪一层", "恢复可持续成交而非一次性补单。"),
            ("未上货", "核对供应链、状态和在售 SKU", "有效在售商品恢复。"),
            ("生命周期", "分层潜力、畅销和衰退商品", "商品池结构改善。"),
        ),
    },
)


def _number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and number not in {float("inf"), float("-inf")} else None


def _integer(value: Any) -> int | None:
    number = _number(value)
    return int(number) if number is not None else None


def _score(value: Any) -> float | None:
    number = _number(value)
    if number is None or number <= 0 or number > 5:
        return None
    return round(number, 2)


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _safe_error_message(value: Any) -> str:
    message = str(value or "").strip()
    message = re.sub(
        r"(?i)\b(app\s*secret|appsecret|access\s*token|refresh\s*token|"
        r"authorization|cookie)\b[^;,\n]*",
        r"\1 [已隐藏]",
        message,
    )
    message = re.sub(r"(?i)\brid\s*[:=]\s*[A-Za-z0-9_-]+", "rid:[已隐藏]", message)
    return message[:500]


def _rules(db: Database) -> dict[str, float]:
    output = {key: float(value) for key, value in DEFAULT_OPERATION_RULES.items()}
    for key, value in db.get_operation_rules().items():
        number = _number(value)
        if number is not None:
            output[key] = number
    return output


def _store_ids(payload: dict[str, Any]) -> set[int]:
    output: set[int] = set()
    for value in payload.get("store_ids") or []:
        try:
            output.add(int(value))
        except (TypeError, ValueError):
            continue
    return output


def _range_start(range_name: str, now: datetime) -> tuple[datetime, str]:
    if range_name == "7d":
        return now - timedelta(days=7), "近 7 日"
    if range_name == "30d":
        return now - timedelta(days=30), "近 30 日"
    return now.replace(hour=0, minute=0, second=0, microsecond=0), "今日"


def _is_overdue(due_at: Any, generated_at: str) -> bool:
    if not due_at:
        return False
    try:
        due = datetime.fromisoformat(str(due_at).replace("T", " "))
        current = datetime.fromisoformat(str(generated_at).replace("T", " "))
    except (TypeError, ValueError):
        return False
    return due < current


def _days_since_local_date(value: Any, now: datetime) -> int | None:
    if not value:
        return None
    try:
        observed = datetime.fromisoformat(str(value))
        if observed.tzinfo is not None:
            observed = observed.astimezone()
        return max(0, (now.date() - observed.date()).days)
    except (TypeError, ValueError):
        return None


def _order_transaction(order: dict[str, Any]) -> bool:
    try:
        return order_counts_as_transaction(order.get("status"))
    except Exception:
        return str(order.get("status_text") or "") not in {
            "待付款",
            "已取消",
            "已关闭",
            "礼物待收下",
        }


def _consecutive_zero_sales_days(
    orders: list[dict[str, Any]],
    now: datetime,
) -> int | None:
    if not orders:
        return None
    sold_dates = {
        datetime.fromtimestamp(int(order.get("create_time") or 0)).date()
        for order in orders
        if _order_transaction(order) and int(order.get("create_time") or 0) > 0
    }
    if not sold_dates:
        return None
    count = 0
    cursor = now.date() - timedelta(days=1)
    while count < 365 and cursor not in sold_dates:
        count += 1
        cursor -= timedelta(days=1)
    return count


def _product_order_momentum(
    orders: list[dict[str, Any]],
    now: datetime,
) -> dict[str, Any]:
    unique_orders: dict[str, Any] = {}
    for order in orders:
        if not _order_transaction(order):
            continue
        order_id = str(order.get("order_id") or "").strip()
        timestamp = int(order.get("pay_time") or order.get("create_time") or 0)
        if not order_id or timestamp <= 0:
            continue
        unique_orders[order_id] = datetime.fromtimestamp(timestamp).date()
    order_dates = set(unique_orders.values())
    today = now.date()
    orders_today = sum(value == today for value in unique_orders.values())
    orders_7d = sum(
        today - timedelta(days=6) <= value <= today
        for value in unique_orders.values()
    )
    anchor = (
        today
        if today in order_dates
        else today - timedelta(days=1)
        if today - timedelta(days=1) in order_dates
        else None
    )
    consecutive_days = 0
    cursor = anchor
    while cursor is not None and cursor in order_dates:
        consecutive_days += 1
        cursor -= timedelta(days=1)
    return {
        "orders_today": orders_today,
        "consecutive_order_days": consecutive_days,
        "orders_7d": orders_7d,
        "sales_momentum": orders_today >= 2 or consecutive_days >= 2,
    }


def _source_items(
    stores: list[Any],
    metrics: dict[int, dict[str, Any]],
    analytics: dict[int, list[dict[str, Any]]],
    orders: list[dict[str, Any]],
    generated_at: str,
) -> list[dict[str, Any]]:
    metric_times = [
        str((metrics.get(store.id) or {}).get("stats_at") or "")
        for store in stores
    ]
    analytics_times = [
        str(row.get("synced_at") or "")
        for rows in analytics.values()
        for row in rows
    ]
    order_times = [str(order.get("synced_at") or "") for order in orders]
    metric_errors = sorted(
        {
            _safe_error_message(error)
            for store in stores
            for error in ((metrics.get(store.id) or {}).get("_errors") or [])
            if _safe_error_message(error)
        }
    )
    return [
        {
            "key": "official_realtime",
            "label": SOURCE_REALTIME,
            "updated_at": max(metric_times, default="") or None,
            "last_success_at": max((value for value in metric_times if value), default="") or None,
            "status": "partial" if metric_errors else "ready",
            "fields": [
                "店铺体验分",
                "商品/物流/服务体验分",
                "推荐与搜索曝光",
                "商品点击",
                "质量退款率",
                "好评率",
            ],
            "error": "；".join(metric_errors[:8]),
        },
        {
            "key": "official_api",
            "label": SOURCE_API_T1,
            "updated_at": max(analytics_times, default="") or None,
            "last_success_at": max((value for value in analytics_times if value), default="") or None,
            "status": "ready" if analytics_times else "missing",
            "fields": ["成交金额", "成交人数", "支付订单", "退款金额", "商品点击人数"],
            "error": "" if analytics_times else "尚未读取到官方 T+1 分析数据",
        },
        {
            "key": "sqlite",
            "label": SOURCE_SQLITE,
            "updated_at": max(order_times, default="") or None,
            "last_success_at": max((value for value in order_times if value), default="") or None,
            "status": "ready" if orders else "missing",
            "fields": ["订单", "售后", "商品缓存", "行动任务", "巡检规则"],
            "error": "" if orders else "本地尚无订单记录",
        },
        {
            "key": "local_calc",
            "label": SOURCE_CALC,
            "updated_at": generated_at,
            "last_success_at": generated_at,
            "status": "ready",
            "fields": ["趋势", "异常优先级", "差评率", "品质退款率", "流量需求", "客服未回复率"],
            "error": "",
        },
    ]


def build_store_rows(
    db: Database,
    *,
    selected_store_ids: set[int] | None = None,
    search: str = "",
) -> tuple[list[dict[str, Any]], dict[int, dict[str, Any]]]:
    now = datetime.now()
    search_terms = [
        term.casefold()
        for term in search.replace("，", " ").split()
        if term.strip()
    ]
    stores = [
        store
        for store in db.list_stores()
        if (not selected_store_ids or store.id in selected_store_ids)
        and (
            not search_terms
            or any(
                term in f"{store.name} {store.app_id} {store.group_name}".casefold()
                for term in search_terms
            )
        )
    ]
    metrics = db.store_metric_map()
    first_seen_by_store = db.product_first_seen_map(
        {store.id for store in stores}
    )
    all_orders = db.list_orders(limit=100_000)
    orders_by_store: dict[int, list[dict[str, Any]]] = {}
    for order in all_orders:
        orders_by_store.setdefault(int(order.get("store_id") or 0), []).append(order)
    after_sales_by_store: dict[int, list[dict[str, Any]]] = {}
    for row in db.list_after_sales():
        after_sales_by_store.setdefault(int(row.get("store_id") or 0), []).append(row)
    rules = _rules(db)
    previous_prefix = "webui_previous_store_metrics_"
    previous_values = db.get_settings_with_prefix(previous_prefix)
    result: list[dict[str, Any]] = []
    for store in stores:
        current = dict(metrics.get(store.id) or {})
        previous = _json_object(previous_values.get(f"{previous_prefix}{store.id}"))
        orders = orders_by_store.get(store.id, [])
        after_sales = after_sales_by_store.get(store.id, [])
        score_fields = ("dsr", "score_product", "score_deliver", "score_service")
        scores = {key: _score(current.get(key)) for key in score_fields}
        trends: dict[str, float | None] = {}
        for key in score_fields:
            before = _score(previous.get(key))
            trends[key] = (
                round(float(scores[key]) - float(before), 2)
                if scores[key] is not None and before is not None
                else None
            )
        open_after_sales = [
            row
            for row in after_sales
            if "完成" not in str(row.get("status_text") or "")
        ]
        quality_refunds = [
            row
            for row in after_sales
            if any(
                marker in str(row.get("reason_text") or "")
                for marker in ("品质", "质量", "破损", "瑕疵", "功能", "色差", "尺寸")
            )
        ]
        shipped_order_count = sum(
            bool(str(order.get("waybill_id") or "").strip())
            for order in orders
        )
        local_quality_ratio = (
            len(quality_refunds) / shipped_order_count * 100
            if shipped_order_count > 0
            else None
        )
        quality_ratio = (
            round(local_quality_ratio, 2)
            if local_quality_ratio is not None
            else _number(current.get("quality_refund_ratio"))
        )
        score_drop = trends.get("dsr")
        exposure_today = _integer(current.get("recommend_exposure"))
        exposure_yesterday = _integer(current.get("recommend_exposure_yesterday"))
        alert_level = ""
        alert_reasons: list[str] = []
        if store.web_status not in {"已登录", ""}:
            alert_level = "risk"
            alert_reasons.append(f"店铺登录状态为{store.web_status}")
        if score_drop is not None and score_drop <= -float(rules["score_drop_threshold"]):
            alert_level = "risk"
            alert_reasons.append(f"体验分较上次下降 {abs(score_drop):.2f}")
        if quality_ratio is not None and quality_ratio > float(rules["quality_refund_watch_rate"]):
            alert_level = "risk"
            alert_reasons.append(f"品质退款率 {quality_ratio:.2f}% 超过本地观察阈值")
        if (
            exposure_today == 0
            and exposure_yesterday is not None
            and exposure_yesterday > 0
        ):
            alert_level = alert_level or "watch"
            alert_reasons.append(
                f"今日推荐曝光为 0，上次同步的昨日曝光为 {exposure_yesterday}"
            )
        if open_after_sales:
            alert_level = alert_level or "watch"
            alert_reasons.append(f"{len(open_after_sales)} 个售后单尚未完成")
        first_seen = first_seen_by_store.get(store.id, {})
        latest_first_seen_at = first_seen.get("latest_first_seen_at")
        no_new_product_days = _days_since_local_date(
            latest_first_seen_at,
            now,
        )
        if (
            no_new_product_days is not None
            and no_new_product_days >= int(rules["no_new_product_days"])
            and first_seen.get("observed_count", 0) > 0
        ):
            alert_level = alert_level or "watch"
            alert_reasons.append(
                f"本地连续 {no_new_product_days} 天未观察到新商品首次出现"
            )
        daily = db.list_analytics_days(store.id, limit=30)
        exposure_series = [
            {
                "business_date": str(row.get("business_date") or ""),
                "pay_gmv": _integer(row.get("pay_gmv")),
                "pay_uv": _integer(row.get("pay_uv")),
                "product_click_uv": _integer(row.get("product_click_uv")),
                "synced_at": str(row.get("synced_at") or ""),
            }
            for row in reversed(daily)
        ]
        click_uv = sum(int(row.get("product_click_uv") or 0) for row in daily)
        pay_uv = sum(int(row.get("pay_uv") or 0) for row in daily)
        conversion = round(pay_uv / click_uv * 100, 2) if click_uv > 0 else None
        result.append(
            {
                "store_id": store.id,
                "store_name": store.name,
                "app_id": store.app_id,
                "group_name": store.group_name,
                "web_status": store.web_status,
                "web_status_detail": store.web_status_detail,
                "scores": scores,
                "score_trends": trends,
                "exposure_today": exposure_today,
                "exposure_yesterday": exposure_yesterday,
                "search_exposure_yesterday": _integer(
                    current.get("search_exposure_yesterday")
                ),
                "product_click_today": _integer(
                    current.get("product_click_today")
                ),
                "visitors_30d": _integer(current.get("visitors_30d")),
                "orders_today": _integer(current.get("orders_today")),
                "orders_yesterday": _integer(current.get("orders_yesterday")),
                "gmv_today": _integer(current.get("gmv_today")),
                "gmv_7d": _integer(current.get("gmv_7d")),
                "gmv_30d": _integer(current.get("gmv_30d")),
                "refund_30d": _integer(current.get("refund_30d")),
                "quality_refund_ratio": quality_ratio,
                "good_eval_ratio": _number(current.get("good_eval_ratio")),
                "aftersale_open": len(open_after_sales),
                "quality_refund_count": len(quality_refunds),
                "in_sale": _integer(current.get("in_sale")),
                "warehouse": _integer(current.get("warehouse")),
                "product_total": _integer(current.get("product_total")),
                "wait_send": _integer(current.get("wait_send")),
                "wait_receive": _integer(current.get("wait_receive")),
                "express_error": _integer(current.get("express_err")),
                "complaint": _integer(current.get("complaint")),
                "zero_sales_days": _consecutive_zero_sales_days(orders, now),
                "no_new_product_days": no_new_product_days,
                "latest_new_product_observed_at": latest_first_seen_at,
                "observed_product_count": first_seen.get("observed_count", 0),
                "conversion_30d": conversion,
                "alert_level": alert_level,
                "alert_reasons": alert_reasons,
                "stats_at": str(current.get("stats_at") or current.get("updated_at") or "") or None,
                "errors": [
                    _safe_error_message(error)
                    for error in (current.get("_errors") or [])
                    if _safe_error_message(error)
                ],
                "exposure_series": exposure_series,
                "source_labels": [SOURCE_REALTIME, SOURCE_API_T1, SOURCE_SQLITE, SOURCE_CALC],
            }
        )
    return result, metrics


def _build_logistics_diagnosis(
    store: dict[str, Any],
    after_sales: list[dict[str, Any]],
    orders: list[dict[str, Any]],
    *,
    now: datetime,
) -> dict[str, Any]:
    score = _score((store.get("scores") or {}).get("score_deliver"))
    trend = _number((store.get("score_trends") or {}).get("score_deliver"))
    evidence: list[dict[str, Any]] = []

    express_error = _integer(store.get("express_error")) or 0
    if express_error > 0:
        evidence.append(
            {
                "code": "express_error",
                "title": f"平台物流异常 {express_error} 单",
                "severity": "high",
                "evidence": "官方后台待办中的物流异常数量",
                "action": "逐单核对承运商、物流轨迹和异常原因；先处理停滞、退回和长时间无更新订单，并保存处理结果。",
                "source": SOURCE_REALTIME,
            }
        )
    wait_send = _integer(store.get("wait_send")) or 0
    if wait_send > 0:
        evidence.append(
            {
                "code": "wait_send",
                "title": f"待发货 {wait_send} 单",
                "severity": "high",
                "evidence": "官方后台待发货数量",
                "action": "优先处理超过承诺时效的订单；核对库存和打单，缺货订单及时与买家沟通并走合规售后。",
                "source": SOURCE_REALTIME,
            }
        )
    wait_receive = _integer(store.get("wait_receive")) or 0
    if wait_receive > 0:
        evidence.append(
            {
                "code": "wait_receive",
                "title": f"运输中待收货 {wait_receive} 单",
                "severity": "watch",
                "evidence": "官方后台待收货数量",
                "action": "检查运输停滞和派送异常；主动同步物流进度，必要时联系承运商核实并保留沟通记录。",
                "source": SOURCE_REALTIME,
            }
        )
    if trend is not None and trend < 0:
        evidence.append(
            {
                "code": "negative_trend",
                "title": f"物流体验分较上次下降 {abs(trend):.2f}",
                "severity": "high" if abs(trend) >= 0.1 else "watch",
                "evidence": "官方物流体验分与上一次刷新快照比较",
                "action": "复核本次变化对应的订单、承运商和异常物流，不通过刷单或虚假评价修复分数。",
                "source": SOURCE_CALC,
            }
        )
    logistics_markers = (
        "物流",
        "快递",
        "发货",
        "配送",
        "送达",
        "未收到",
        "延迟",
        "揽收",
        "签收",
        "运输",
    )
    logistics_after_sales = [
        row
        for row in after_sales
        if any(
            marker in str(row.get("reason_text") or "")
            for marker in logistics_markers
        )
    ]
    if logistics_after_sales:
        reasons = Counter(
            str(row.get("reason_text") or "未填写原因")
            for row in logistics_after_sales
        )
        reason_text = "、".join(
            f"{reason} {count} 单"
            for reason, count in reasons.most_common(5)
        )
        evidence.append(
            {
                "code": "logistics_after_sale",
                "title": f"物流相关售后 {len(logistics_after_sales)} 单",
                "severity": "high",
                "evidence": reason_text,
                "action": "按原因分组回访和处理；先解决未送达、延迟和派送异常，再核对承运商时效与发货承诺。",
                "source": SOURCE_SQLITE,
            }
        )
    if score is not None and not evidence:
        evidence.append(
            {
                "code": "score_only",
                "title": "官方当前仅返回物流体验总分",
                "severity": "watch",
                "evidence": "未取得可解释的物流分项分子、分母或评价明细",
                "action": "打开店铺体验分后台核对具体指标和评价明细；本系统不根据总分猜造原因。",
                "source": SOURCE_REALTIME,
            }
        )

    cutoff = int((now - timedelta(days=14)).timestamp())
    shipped_14d = [
        order
        for order in orders
        if int(order.get("create_time") or 0) >= cutoff
        and str(order.get("waybill_id") or "").strip()
        and order_counts_as_transaction(order.get("status"))
    ]
    observed_orders = len(shipped_14d)
    dilution: list[dict[str, Any]] = []
    if score is not None and 0 < score < 5 and observed_orders > 0:
        for target in (4.5, 4.6, 4.7):
            if score >= target:
                full_score_orders = 0
                realistic_orders = 0
            else:
                full_score_orders = max(
                    0,
                    ceil(observed_orders * (target - score) / (5 - target)),
                )
                realistic_orders = (
                    max(
                        0,
                        ceil(
                            observed_orders
                            * (target - score)
                            / (4.8 - target)
                        ),
                    )
                    if target < 4.8
                    else None
                )
            dilution.append(
                {
                    "target": target,
                    "new_full_score_orders": full_score_orders,
                    "new_4_8_score_orders": realistic_orders,
                }
            )

    return {
        "score": score,
        "score_trend": trend,
        "status": (
            "risk"
            if any(row["severity"] == "high" for row in evidence)
            else "watch"
            if evidence
            else "unknown"
        ),
        "evidence": evidence,
        "observed_shipped_orders_14d": observed_orders,
        "dilution": dilution,
        "formula": (
            "新增同分订单数 = 当前观测订单数 × "
            "(目标分 - 当前分) ÷ (新增订单分 - 目标分)，向上取整"
        ),
        "assumption": (
            "假设新增订单均获得物流体验满分 5.00，并用最近 14 天已发货"
            "有效订单数近似当前评价样本量；仅用于运营测算，不代表平台最终分数公式。"
        ),
        "source_labels": [SOURCE_REALTIME, SOURCE_SQLITE, SOURCE_CALC],
    }


def build_product_rows(
    db: Database,
    *,
    selected_store_ids: set[int] | None = None,
    search: str = "",
    opportunity: str = "",
) -> tuple[list[dict[str, Any]], int, bool]:
    stores = {
        store.id: store
        for store in db.list_stores()
        if not selected_store_ids or store.id in selected_store_ids
    }
    products = [
        row
        for row in db.list_products(search=search)
        if int(row.get("store_id") or 0) in stores
    ]
    order_links = [
        row
        for row in db.list_order_product_links()
        if int(row.get("store_id") or 0) in stores
    ]
    after_sales = [
        row
        for row in db.list_after_sales()
        if int(row.get("store_id") or 0) in stores
    ]
    orders_by_product: dict[tuple[int, str], list[dict[str, Any]]] = {}
    for order in order_links:
        key = (
            int(order.get("store_id") or 0),
            str(order.get("product_id") or ""),
        )
        orders_by_product.setdefault(key, []).append(order)
    after_sales_by_order: dict[tuple[int, str], list[dict[str, Any]]] = {}
    for row in after_sales:
        after_sales_by_order.setdefault(
            (int(row.get("store_id") or 0), str(row.get("order_id") or "")),
            [],
        ).append(row)
    metrics_by_store: dict[int, dict[str, dict[str, Any]]] = {}
    rules = _rules(db)
    rows: list[dict[str, Any]] = []
    for product in products:
        store_id = int(product.get("store_id") or 0)
        product_id = str(product.get("product_id") or "")
        if store_id not in metrics_by_store:
            metrics_by_store[store_id] = db.get_product_metrics_map(store_id)
        metric = metrics_by_store[store_id].get(product_id)
        views = _integer(metric.get("views30")) if metric else None
        click_cnt = _integer(metric.get("click_cnt")) if metric else None
        click_uv = _integer(metric.get("click_uv")) if metric else None
        pay_uv = _integer(metric.get("pay_uv")) if metric else None
        sold30 = _integer(metric.get("sold30")) if metric else None
        related_orders = orders_by_product.get((store_id, product_id), [])
        momentum = _product_order_momentum(related_orders, datetime.now())
        related_after_sales = [
            after_sale
            for order in related_orders
            for after_sale in after_sales_by_order.get(
                (store_id, str(order.get("order_id") or "")),
                [],
            )
        ]
        quality_after_sales = [
            row
            for row in related_after_sales
            if any(
                marker in str(row.get("reason_text") or "")
                for marker in ("品质", "质量", "破损", "瑕疵", "功能", "色差", "尺寸")
            )
        ]
        tags: list[str] = []
        categories: list[str] = []
        if (
            views is not None
            and pay_uv is not None
            and views >= int(rules["high_views_threshold"])
            and pay_uv < int(rules["low_pay_threshold"])
        ):
            categories.append("potential")
            tags.append("高浏览低成交")
        if (sold30 or 0) > 0 and (pay_uv or 0) > 0:
            categories.append("hot")
            tags.append("有真实成交")
        if momentum["sales_momentum"]:
            categories.append("sales_momentum")
            tags.append("销售升温")
        if quality_after_sales or len(related_after_sales) >= 2:
            categories.append("risk")
            tags.append("售后集中")
        if not tags:
            tags.append("数据不足" if metric is None else "常规观察")
        if opportunity and opportunity not in categories:
            continue
        raw = _json_object(product.get("raw_json"))
        ctr = (
            click_cnt / views * 100
            if click_cnt is not None and views not in (None, 0)
            else None
        )
        pay_conversion = (
            pay_uv / click_uv * 100
            if pay_uv is not None and click_uv not in (None, 0)
            else None
        )
        refund_rate = (
            len(related_after_sales) / len(related_orders) * 100
            if related_orders
            else None
        )
        rows.append(
            {
                "store_id": store_id,
                "store_name": stores[store_id].name,
                "product_id": product_id,
                "title": str(product.get("title") or ""),
                "status": _integer(product.get("status")),
                "status_text": str(product.get("status_text") or product.get("status") or ""),
                "edit_status": str(product.get("edit_status") or ""),
                "price": _integer(product.get("min_price")),
                "stock": _integer(product.get("stock_total")),
                "sku_count": _integer(product.get("sku_count")),
                "sales": _integer(raw.get("total_sold_num")),
                "head_img": str(product.get("head_img") or ""),
                "synced_at": str(product.get("synced_at") or "") or None,
                "metrics": {
                    "views30": views,
                    "sold30": sold30,
                    "click_cnt": click_cnt,
                    "click_uv": click_uv,
                    "pay_cnt": _integer(metric.get("pay_cnt")) if metric else None,
                    "pay_uv": pay_uv,
                    "pay_gmv": _integer(metric.get("pay_gmv")) if metric else None,
                    "updated_at": str(metric.get("updated_at") or "") if metric else None,
                },
                "click_rate": round(ctr, 2) if ctr is not None else None,
                "pay_conversion": (
                    round(pay_conversion, 2) if pay_conversion is not None else None
                ),
                "refund_rate": round(refund_rate, 2) if refund_rate is not None else None,
                "after_sale_count": len(related_after_sales),
                "quality_after_sale_count": len(quality_after_sales),
                "orders_today": momentum["orders_today"],
                "consecutive_order_days": momentum["consecutive_order_days"],
                "orders_7d": momentum["orders_7d"],
                "info_score": raw.get("info_score"),
                "cmp_price_info": raw.get("cmp_price_info"),
                "sale_limit_info": raw.get("sale_limit_info"),
                "audit_info": raw.get("audit_info"),
                "opportunity_categories": categories,
                "tags": tags,
                "source_labels": [SOURCE_SQLITE, SOURCE_API_T1, SOURCE_CALC],
            }
        )
    rows.sort(
        key=lambda row: (
            row["opportunity_categories"] == [],
            -(row["metrics"]["views30"] or 0),
            -(row["metrics"]["pay_gmv"] or 0),
        )
    )
    return rows, len(products), False


def dashboard_payload(
    db: Database,
    payload: dict[str, Any],
) -> dict[str, Any]:
    now = datetime.now()
    generated_at = now.strftime("%Y-%m-%d %H:%M:%S")
    selected = _store_ids(payload)
    search = str(payload.get("search") or "").strip()
    range_name = str(payload.get("range") or "today")
    status_filter = str(payload.get("status") or "all")
    store_rows, metrics = build_store_rows(
        db,
        selected_store_ids=selected,
        search=search,
    )
    if status_filter in {"risk", "watch", "ok"}:
        store_rows = [
            row for row in store_rows if (row["alert_level"] or "ok") == status_filter
        ]
    product_rows, _, _ = build_product_rows(
        db,
        selected_store_ids=selected,
    )
    task_rows = db.list_operation_tasks()
    dsr_values = [
        float(row["scores"]["dsr"])
        for row in store_rows
        if row["scores"]["dsr"] is not None
    ]
    exposure_values = [
        row["exposure_today"]
        for row in store_rows
        if row["exposure_today"] is not None
    ]
    analytics = {
        store_id: db.list_analytics_days(store_id, limit=30)
        for store_id in {int(row["store_id"]) for row in store_rows}
    }
    total_click_uv = sum(
        int(row.get("product_click_uv") or 0)
        for rows in analytics.values()
        for row in rows
    )
    total_pay_uv = sum(
        int(row.get("pay_uv") or 0)
        for rows in analytics.values()
        for row in rows
    )
    weighted_conversion = (
        round(total_pay_uv / total_click_uv * 100, 2)
        if total_click_uv > 0
        else None
    )
    alerts: list[dict[str, Any]] = []
    for row in store_rows:
        for reason in row["alert_reasons"]:
            priority = "P0" if row["alert_level"] == "risk" else "P1"
            alerts.append(
                {
                    "alert_key": (
                        f"store:{row['store_id']}:"
                        f"{hashlib.sha1(reason.encode('utf-8')).hexdigest()[:12]}"
                    ),
                    "event_at": row["stats_at"] or generated_at,
                    "store_id": row["store_id"],
                    "store_name": row["store_name"],
                    "product_id": "",
                    "product_title": "",
                    "category": "店铺健康",
                    "title": reason,
                    "evidence": [
                        f"体验分 {row['scores']['dsr'] if row['scores']['dsr'] is not None else '暂无数据'}",
                        f"今日推荐曝光 {row['exposure_today'] if row['exposure_today'] is not None else '暂无数据'}",
                        f"未完成售后 {row['aftersale_open']} 单",
                        *(
                            [
                                f"本地最近首次观测新商品："
                                f"{row['latest_new_product_observed_at']}，"
                                f"距今 {row['no_new_product_days']} 天"
                            ]
                            if row["latest_new_product_observed_at"]
                            and row["no_new_product_days"] is not None
                            else []
                        ),
                    ],
                    "confidence": "高" if row["web_status"] not in {"已登录", ""} else "中",
                    "priority": priority,
                    "suggested_action": "先核对商品状态、违规、售后和流量来源，再决定是否调整内容或价格。",
                    "backend_target": "score" if "体验分" in reason else "store",
                }
            )
    for product in product_rows:
        if "高浏览低成交" in product["tags"]:
            alerts.append(
                {
                    "alert_key": f"product:{product['store_id']}:{product['product_id']}:potential",
                    "event_at": product["metrics"]["updated_at"] or generated_at,
                    "store_id": product["store_id"],
                    "store_name": product["store_name"],
                    "product_id": product["product_id"],
                    "product_title": product["title"],
                    "category": "商品机会",
                    "title": f"{product['title']} 高浏览低成交",
                    "evidence": [
                        f"近 30 日浏览 {product['metrics']['views30']}",
                        f"支付人数 {product['metrics']['pay_uv']}",
                        f"点击率 {product['click_rate']}%" if product["click_rate"] is not None else "点击率暂无数据",
                    ],
                    "confidence": "中",
                    "priority": "P1",
                    "suggested_action": "检查商品卡、到手价、详情、SKU 与运费，不做虚假成交或诱导评价。",
                    "backend_target": "product",
                }
            )
        if "售后集中" in product["tags"]:
            alerts.append(
                {
                    "alert_key": f"product:{product['store_id']}:{product['product_id']}:risk",
                    "event_at": product["synced_at"] or generated_at,
                    "store_id": product["store_id"],
                    "store_name": product["store_name"],
                    "product_id": product["product_id"],
                    "product_title": product["title"],
                    "category": "商品售后",
                    "title": f"{product['title']} 售后集中",
                    "evidence": [
                        f"关联售后 {product['after_sale_count']} 单",
                        f"原因含品质关键词 {product['quality_after_sale_count']} 单",
                    ],
                    "confidence": "中",
                    "priority": "P1",
                    "suggested_action": "按售后原因修复商品描述、包装或批次，并处理仍在有效窗口的售后。",
                    "backend_target": "aftersale",
                }
            )
    alerts.sort(
        key=lambda item: (
            0 if item["priority"] == "P0" else 1,
            item["event_at"],
        ),
        reverse=False,
    )
    all_orders = db.list_orders(limit=100_000)
    return {
        "generated_at": generated_at,
        "range": range_name,
        "range_start": _range_start(range_name, now)[0].isoformat(timespec="seconds"),
        "summary": {
            "stores_total": len(store_rows),
            "stores_risk": sum(row["alert_level"] == "risk" for row in store_rows),
            "stores_watch": sum(row["alert_level"] == "watch" for row in store_rows),
            "avg_dsr": round(sum(dsr_values) / len(dsr_values), 2) if dsr_values else None,
            "exposure_today": sum(exposure_values) if exposure_values else None,
            "conversion_30d": weighted_conversion,
            "click_uv_30d": total_click_uv if total_click_uv else None,
            "pay_uv_30d": total_pay_uv if total_click_uv else None,
            "open_tasks": sum(row["status"] != "done" for row in task_rows),
            "due_tasks": sum(
                row["status"] != "done"
                and _is_overdue(row.get("due_at"), generated_at)
                for row in task_rows
            ),
        },
        "data_sources": _source_items(
            db.list_stores(),
            metrics,
            analytics,
            all_orders,
            generated_at,
        ),
        "alerts": alerts[:30],
        "stores": store_rows,
        "product_summary": {
            "potential": sum("potential" in row["opportunity_categories"] for row in product_rows),
            "hot": sum("hot" in row["opportunity_categories"] for row in product_rows),
            "risk": sum("risk" in row["opportunity_categories"] for row in product_rows),
            "sales_momentum": sum(
                "sales_momentum" in row["opportunity_categories"]
                for row in product_rows
            ),
            "missing_metrics": sum(row["metrics"]["updated_at"] is None for row in product_rows),
        },
    }


def products_payload(
    db: Database,
    payload: dict[str, Any],
) -> dict[str, Any]:
    rows, total, truncated = build_product_rows(
        db,
        selected_store_ids=_store_ids(payload),
        search=str(payload.get("search") or "").strip(),
        opportunity=str(payload.get("opportunity") or "").strip(),
    )
    return {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "products": rows,
        "total": total,
        "returned": len(rows),
        "truncated": truncated,
        "data_sources": [SOURCE_SQLITE, SOURCE_API_T1, SOURCE_CALC],
    }


def store_detail_payload(
    db: Database,
    payload: dict[str, Any],
) -> dict[str, Any]:
    store_id = int(payload.get("store_id") or 0)
    rows, _ = build_store_rows(db, selected_store_ids={store_id})
    if not rows:
        raise ValueError("店铺不存在")
    store = rows[0]
    after_sales = db.list_after_sales(store_id)
    reason_counts: dict[str, int] = {}
    for row in after_sales:
        reason = str(row.get("reason_text") or "未填写原因")
        reason_counts[reason] = reason_counts.get(reason, 0) + 1
    tasks = db.list_operation_tasks(store_id=store_id)
    orders = db.list_orders(store_id=store_id, limit=100_000)
    shipped_orders = [
        row
        for row in orders
        if str(row.get("waybill_id") or "").strip()
    ]
    quality_refunds = [
        row
        for row in after_sales
        if any(
            marker in str(row.get("reason_text") or "")
            for marker in ("品质", "质量", "破损", "瑕疵", "功能", "色差", "尺寸")
        )
    ]
    return {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "store": store,
        "trend": store["exposure_series"],
        "after_sale_reasons": [
            {"reason": reason, "count": count}
            for reason, count in sorted(
                reason_counts.items(),
                key=lambda item: item[1],
                reverse=True,
            )
        ],
        "tasks": tasks,
        "logistics": _build_logistics_diagnosis(
            store,
            after_sales,
            orders,
            now=datetime.now(),
        ),
        "calculators": {
            "quality_refund": {
                "current_count": len(quality_refunds),
                "denominator": len(shipped_orders),
                "rate": (
                    round(len(quality_refunds) / len(shipped_orders) * 100, 2)
                    if shipped_orders
                    else None
                ),
                "formula": "品质退款单 ÷ 已发货有效订单 × 100%",
                "source": SOURCE_CALC,
            },
            "service_reply": {
                "total_rounds": None,
                "unreplied_rounds": None,
                "unreplied_rate": None,
                "formula": "未回复轮次 ÷ 有效咨询轮次 × 100%",
                "source": "暂无客服消息事件数据",
            },
        },
        "data_sources": _source_items(
            db.list_stores(),
            db.store_metric_map(),
            {store_id: db.list_analytics_days(store_id, 30)},
            orders,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        ),
    }


def product_detail_payload(
    db: Database,
    payload: dict[str, Any],
) -> dict[str, Any]:
    store_id = int(payload.get("store_id") or 0)
    product_id = str(payload.get("product_id") or "").strip()
    product = db.get_product(store_id, product_id)
    if product is None:
        raise ValueError("商品不存在或不属于当前店铺")
    rows, _, _ = build_product_rows(db, selected_store_ids={store_id})
    selected = next(
        (row for row in rows if str(row["product_id"]) == product_id),
        None,
    )
    if selected is None:
        raise ValueError("商品不存在或不属于当前店铺")
    product_order_ids = {
        str(row.get("order_id") or "")
        for row in db.list_order_product_links(store_id)
        if str(row.get("product_id") or "") == product_id
    }
    orders = [
        row
        for row in db.list_orders(store_id=store_id, limit=100_000)
        if str(row.get("order_id") or "") in product_order_ids
    ]
    after_sales = [
        row
        for row in db.list_after_sales(store_id)
        if any(str(row.get("order_id") or "") == str(order.get("order_id")) for order in orders)
    ]
    after_sale_rows = [
        {
            "after_sale_order_id": str(row.get("after_sale_order_id") or ""),
            "order_id": str(row.get("order_id") or ""),
            "status": str(row.get("status") or ""),
            "status_text": str(row.get("status_text") or ""),
            "after_sale_type": str(row.get("after_sale_type") or ""),
            "reason_text": str(row.get("reason_text") or "").strip() or "未提供售后原因",
            "refund_amount": _integer(row.get("refund_amount")),
            "create_time": _integer(row.get("create_time")),
            "update_time": _integer(row.get("update_time")),
        }
        for row in after_sales
    ]
    reason_counts = Counter(row["reason_text"] for row in after_sale_rows)
    reason_text = " ".join(reason_counts)
    advice = [
        "先核对该商品的真实售后单、订单批次和当前处理状态，保留平台原始记录。"
    ]
    if any(marker in reason_text for marker in ("品质", "质量", "破损", "瑕疵", "功能")):
        advice.append("品质类问题先复核商品描述、实物批次、质检记录和包装防护，再决定改图、改文案或停售排查。")
    if any(marker in reason_text for marker in ("尺寸", "尺码", "大小")):
        advice.append("尺寸类问题先核对尺码表、SKU 属性和详情页中的测量口径。")
    if any(marker in reason_text for marker in ("色差", "颜色")):
        advice.append("色差类问题先核对实拍图、显示器色差提示和批次一致性。")
    advice.append("评价明细尚未接入，不能据好评率推断具体差评文字或预测平台最终分数。")
    store_metrics = db.store_metric_map().get(store_id) or {}
    return {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "store_id": store_id,
        "product": selected,
        "orders": orders[:50],
        "order_count": len(orders),
        "after_sales": after_sale_rows,
        "after_sale_reason_clusters": [
            {"reason": reason, "count": count}
            for reason, count in reason_counts.most_common()
        ],
        "after_sale_advice": advice,
        "evaluation_overview": {
            "good_eval_ratio": _number(store_metrics.get("good_eval_ratio")),
            "quality_refund_ratio": _number(store_metrics.get("quality_refund_ratio")),
            "stats_at": str(
                store_metrics.get("stats_at")
                or store_metrics.get("updated_at")
                or ""
            )
            or None,
            "scope": "store",
            "detail_status": "评价明细尚未接入，仅显示官方评价概览",
        },
        "tasks": db.list_operation_tasks(store_id=store_id, product_id=product_id),
        "funnel": {
            "views30": selected["metrics"]["views30"],
            "click_uv": selected["metrics"]["click_uv"],
            "pay_uv": selected["metrics"]["pay_uv"],
            "pay_cnt": selected["metrics"]["pay_cnt"],
            "click_rate": selected["click_rate"],
            "pay_conversion": selected["pay_conversion"],
        },
        "data_sources": [SOURCE_SQLITE, SOURCE_API_T1, SOURCE_CALC],
    }


def rules_payload(db: Database) -> dict[str, Any]:
    stored = db.get_operation_rules()
    return {
        "rules": _rules(db),
        "defaults": DEFAULT_OPERATION_RULES,
        "stored_keys": sorted(stored),
        "note": "这些阈值只用于本地运营提醒，不代表平台评分公式或处罚结论。",
    }


def validate_rules(payload: dict[str, Any]) -> dict[str, str]:
    values: dict[str, str] = {}
    for key, default in DEFAULT_OPERATION_RULES.items():
        raw = payload.get(key, default)
        number = _number(raw)
        if number is None or number < 0:
            raise ValueError(f"巡检规则 {key} 必须是非负数字")
        if key in {"no_reply_rate", "quality_refund_watch_rate"} and number > 100:
            raise ValueError(f"巡检规则 {key} 不能超过 100")
        if key == "score_drop_threshold" and number > 5:
            raise ValueError("体验分下降阈值不能超过 5")
        values[key] = str(number)
    return values


def export_csv(scope: str, rows: list[dict[str, Any]]) -> tuple[str, str]:
    output = io.StringIO()
    output.write("\ufeff")
    if scope == "tasks":
        columns = [
            ("id", "任务ID"),
            ("priority", "优先级"),
            ("status", "状态"),
            ("store_name", "店铺"),
            ("product_title", "商品"),
            ("category", "类别"),
            ("title", "任务"),
            ("detail", "详情"),
            ("due_at", "到期时间"),
            ("updated_at", "更新时间"),
        ]
    elif scope == "products":
        columns = [
            ("store_name", "店铺"),
            ("product_id", "商品ID"),
            ("title", "商品"),
            ("status_text", "状态"),
            ("metrics.views30", "30日浏览"),
            ("metrics.pay_uv", "支付人数"),
            ("click_rate", "点击率"),
            ("pay_conversion", "支付转化"),
            ("after_sale_count", "售后单"),
            ("tags", "机会/风险"),
            ("synced_at", "商品更新时间"),
        ]
    else:
        columns = [
            ("store_name", "店铺"),
            ("web_status", "登录状态"),
            ("scores.dsr", "体验分"),
            ("scores.score_product", "商品体验"),
            ("scores.score_deliver", "物流体验"),
            ("scores.score_service", "服务体验"),
            ("exposure_today", "今日推荐曝光"),
            ("visitors_30d", "30日访客"),
            ("orders_today", "今日订单"),
            ("gmv_today", "今日销售额"),
            ("quality_refund_ratio", "质量退款率"),
            ("alert_level", "本地风险级别"),
            ("alert_reasons", "证据"),
            ("stats_at", "数据更新时间"),
        ]
    writer = csv.writer(output)
    writer.writerow([label for _, label in columns])
    for row in rows:
        values: list[Any] = []
        for path, _ in columns:
            value: Any = row
            for part in path.split("."):
                value = value.get(part) if isinstance(value, dict) else None
            if isinstance(value, (list, dict)):
                value = json.dumps(value, ensure_ascii=False)
            values.append("" if value is None else value)
        writer.writerow(values)
    return output.getvalue(), f"langlang-operations-{scope}.csv"
