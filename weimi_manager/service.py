from __future__ import annotations

import hashlib
import json
import re
import threading
import time
import unicodedata
import uuid
from concurrent.futures import CancelledError, ThreadPoolExecutor, as_completed
from copy import deepcopy
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from .db import PRODUCT_STATUS, SHANGHAI_TZ, Database, order_status_text
from .mock import MockShopClient, ProgressCallback
from .models import (
    Order,
    Product,
    ShippingAccount,
    ShippingTemplate,
    Store,
    SyncResult,
    UploadPreflightError,
    is_unclaimed_gift_order,
    order_counts_as_transaction,
    product_matches_tab,
)
from .procurement import (
    ControlledPurchaseWindow,
    ProcurementAdapter,
    ProcurementWebSession,
)
from .shop_private import ShopPrivateClient, ShopPrivateError
from .source_1688 import rank_category_candidates
from .upload_violations import (
    build_upload_violation_titles,
    is_real_logged_in_store,
    load_upload_violation_title_records,
    mandatory_upload_violation_issue,
)
from .wechat_api import (
    WechatApiError,
    WechatShopClient,
    normalize_sku_sale_attrs,
    parse_shipping_account,
    parse_shipping_template,
)

_NON_BLOCKING_VIOLATION_MARKERS = (
    "价格错误",
    "价格设置错误",
    "价格异常",
    "价保异常",
    "库存错误",
)
_CLOSED_VIOLATION_STATUSES = {
    "已撤销",
    "申诉成功",
    "预警解除",
    "预警结束",
    "审核中",
}
GIFT_UNCLAIMED_MAX_AGE_SECONDS = 24 * 60 * 60
AFTER_SALE_CACHE_MAX_AGE_SECONDS = 24 * 60 * 60
AFTER_SALE_FULL_SYNC_MAX_AGE_SECONDS = 24 * 60 * 60
AFTER_SALE_FULL_SYNC_DAYS = 30
UPLOAD_CATEGORY_CACHE_SCHEMA = 1
UPLOAD_CATEGORY_CACHE_TTL_SECONDS = 24 * 60 * 60
UPLOAD_CATEGORY_REFRESH_COOLDOWN_SECONDS = 60 * 60
PRODUCT_BATCH_ACTION_SIZE = 20
EXTERNAL_MARKETING_ATTRIBUTE_TOKENS = frozenset(
    {
        "是否外贸",
        "是否为外贸",
        "外贸",
        "是否跨境出口",
        "是否跨境出口专供货源",
        "跨境出口",
        "跨境出口专供货源",
        "主要下游平台",
        "下游平台",
        "主要上下游平台",
        "上下游平台",
        "主要上下游品牌",
        "上下游品牌",
        "主要销售平台",
        "销售平台",
        "来源平台",
        "主要销售地区",
        "销售地区",
        "主要销售区域",
        "销售区域",
        "主要销售市场",
        "销售市场",
        "销售国家",
        "目标市场",
        "目标地区",
        "目标国家",
    }
)


def _gift_exclusion_for_period(
    orders: list[dict[str, Any]],
    *,
    now_ts: int,
    start_ts: int,
    end_ts: int,
) -> dict[str, Any]:
    """Build the unclaimed-gift deduction for one sale period."""
    pending_ids: list[str] = []
    deducted_amount = 0
    unconfirmed_ids: list[str] = []
    expired_ids: list[str] = []
    for row in orders:
        try:
            created_at = int(row.get("create_time") or 0)
        except (TypeError, ValueError):
            continue
        if not (start_ts <= created_at < end_ts):
            continue
        if not is_unclaimed_gift_order(row):
            continue
        order_id = str(row.get("order_id") or "").strip()
        if created_at <= now_ts - GIFT_UNCLAIMED_MAX_AGE_SECONDS:
            expired_ids.append(order_id)
            continue
        pending_ids.append(order_id)
        deducted_amount += max(0, int(row.get("total_amount") or 0))
        raw_value = row.get("raw_json")
        if isinstance(raw_value, dict):
            raw = raw_value
        else:
            try:
                raw = json.loads(raw_value or "{}")
            except (TypeError, ValueError):
                raw = {}
        if not isinstance(raw, dict) or raw.get("is_present") is not True:
            unconfirmed_ids.append(order_id)
    return {
        "deducted_amount": deducted_amount,
        "deducted_orders": len(pending_ids),
        "applied_amount": 0,
        "applied_orders": 0,
        "pending_order_ids": pending_ids,
        "pending_order_count": len(pending_ids),
        "gated_count": len(unconfirmed_ids),
        "gated_order_ids": unconfirmed_ids,
        "unconfirmed_count": len(unconfirmed_ids),
        "unconfirmed_order_ids": unconfirmed_ids,
        "expired_status12_count": len(expired_ids),
        "expired_status12_order_ids": expired_ids,
        "source": (
            "官方成交统计 - 未领取礼物"
            "（本地订单 status=12，创建时间 24 小时内）"
        ),
        "buyer_deduction": "未扣减买家数（官方 pay_uv 无订单明细重叠信息）",
    }


def _after_sale_counts_as_refund_request(row: dict[str, Any]) -> bool:
    after_sale_id = str(row.get("after_sale_order_id") or "").strip()
    if not after_sale_id:
        return False
    status = str(row.get("status") or "").strip().upper()
    status_text = str(row.get("status_text") or "").strip()
    if status in {"USER_CANCELD", "USER_CANCELED"}:
        return False
    if any(
        marker in status_text
        for marker in ("用户已取消", "未提交", "无效")
    ):
        return False
    return True


def _refund_order_counts_for_periods(
    order_rows: list[dict[str, Any]],
    after_sale_rows: list[dict[str, Any]],
    ranges: dict[str, tuple[int, int]],
) -> tuple[dict[str, int | None], dict[str, Any]]:
    """Count completed refunds from the same official after-sale timeline."""
    events: dict[str, dict[str, Any]] = {}

    def add_event(
        dedupe_key: str,
        event_time: int,
        source: str,
    ) -> None:
        key = str(dedupe_key or "").strip()
        if not key or event_time <= 0:
            return
        current = events.get(key)
        if current is None:
            events[key] = {
                "event_time": event_time,
                "sources": {source},
            }
            return
        current["sources"].add(source)
        if event_time < int(current["event_time"]):
            current["event_time"] = event_time

    completed_statuses = {
        "MERCHANT_REFUND_SUCCESS",
        "MERCHANT_RETURN_SUCCESS",
    }
    for row in after_sale_rows:
        if not _after_sale_counts_as_refund_request(row):
            continue
        status = str(row.get("status") or "").strip().upper()
        if status not in completed_statuses:
            continue
        raw = {}
        try:
            parsed_raw = json.loads(row.get("raw_json") or "{}")
            raw = parsed_raw if isinstance(parsed_raw, dict) else {}
        except (TypeError, ValueError):
            raw = {}
        candidates = [
            raw.get("complete_time"),
            (raw.get("refund_info") or {}).get("refund_time")
            if isinstance(raw.get("refund_info"), dict)
            else None,
            row.get("update_time"),
        ]
        event_time = 0
        for candidate in candidates:
            try:
                event_time = int(candidate or 0)
            except (TypeError, ValueError):
                continue
            if event_time > 0:
                break
        try:
            if event_time <= 0:
                continue
        except TypeError:
            continue
        order_id = str(row.get("order_id") or "").strip()
        after_sale_id = str(row.get("after_sale_order_id") or "").strip()
        dedupe_key = order_id or after_sale_id
        add_event(
            dedupe_key,
            event_time,
            "官方售后完成时间（complete_time/update_time）",
        )

    order_evidence = {
        period: set()
        for period in ranges
    }
    for row in order_rows:
        if str(row.get("status") or "").strip() != "200":
            continue
        try:
            event_time = int(row.get("update_time") or 0)
        except (TypeError, ValueError):
            continue
        order_id = str(row.get("order_id") or "").strip()
        if not order_id:
            continue
        for period, (start_ts, end_ts) in ranges.items():
            if start_ts <= event_time < end_ts:
                order_evidence[period].add(order_id)

    keys_by_period = {period: set() for period in ranges}
    sources_by_period: dict[str, set[str]] = {
        period: set() for period in ranges
    }
    for key, event in events.items():
        event_time = int(event["event_time"])
        for period, (start_ts, end_ts) in ranges.items():
            if start_ts <= event_time < end_ts:
                keys_by_period[period].add(key)
                sources_by_period[period].update(event["sources"])
    has_source_data = bool(after_sale_rows)
    counts = {
        period: (
            len(values)
            if has_source_data
            else None
        )
        for period, values in keys_by_period.items()
    }
    metadata = {
        "record_count": len(events),
        "sources": {
            period: sorted(values)
            for period, values in sources_by_period.items()
        },
        "source": (
            "仅使用已完成官方售后记录及 complete_time/update_time；"
            "按 order_id 去重；订单 status=200 仅作覆盖证据，不参与单量"
        ),
        "order_status_evidence": {
            period: len(values)
            for period, values in order_evidence.items()
        },
    }
    return counts, metadata


def _refund_amounts_for_periods(
    after_sale_rows: list[dict[str, Any]],
    ranges: dict[str, tuple[int, int]],
) -> dict[str, int | None]:
    """Sum completed official refund amounts by completion date.

    The account overview sometimes reports a zero refund amount for a day
    while its completed after-sale feed contains real refunds. The store page
    must prefer the dated official detail rather than present a false zero.
    """
    totals = {period: 0 for period in ranges}
    completed_statuses = {
        "MERCHANT_REFUND_SUCCESS",
        "MERCHANT_RETURN_SUCCESS",
    }
    for row in after_sale_rows:
        if not _after_sale_counts_as_refund_request(row):
            continue
        status_name = str(row.get("status") or "").strip().upper()
        if status_name not in completed_statuses:
            continue
        try:
            raw = json.loads(row.get("raw_json") or "{}")
        except (TypeError, ValueError):
            raw = {}
        if not isinstance(raw, dict):
            raw = {}
        refund_raw = raw.get("refund_info")
        refund_info = refund_raw if isinstance(refund_raw, dict) else {}
        event_time = 0
        for candidate in (
            raw.get("complete_time"),
            refund_info.get("refund_time"),
            row.get("update_time"),
        ):
            try:
                event_time = int(candidate or 0)
            except (TypeError, ValueError):
                event_time = 0
            if event_time > 0:
                break
        if event_time <= 0:
            continue
        try:
            amount = int(
                row.get("refund_amount")
                or refund_info.get("amount")
                or 0
            )
        except (TypeError, ValueError):
            amount = 0
        if amount <= 0:
            continue
        for period, (start_ts, end_ts) in ranges.items():
            if start_ts <= event_time < end_ts:
                totals[period] += amount
    return {
        period: value if after_sale_rows else None
        for period, value in totals.items()
    }


def _discount_price_cents(
    price_cents: int,
    multiplier: float | Decimal,
) -> int:
    """Apply a price multiplier with currency-safe half-up rounding."""
    ratio = (
        multiplier
        if isinstance(multiplier, Decimal)
        else Decimal(str(multiplier))
    )
    value = Decimal(int(price_cents)) * ratio
    return max(
        1,
        int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP)),
    )


def is_explicit_violation_record(record: dict[str, Any]) -> bool:
    """Return true only for actionable platform violation titles."""
    if not isinstance(record, dict):
        return False
    title = str(record.get("title") or "").strip()
    product_id = str(record.get("product_id") or "").strip()
    if not title and not product_id:
        return False
    status = str(record.get("status") or "").strip()
    if status in _CLOSED_VIOLATION_STATUSES:
        return False
    source = str(record.get("source") or "").strip()
    if source in {"预警", "本机黑名单", "导入清单"} and status != "已违规":
        return False
    text = f"{title} {record.get('reason') or ''} {record.get('fix') or ''}"
    return not any(
        marker in text
        for marker in _NON_BLOCKING_VIOLATION_MARKERS
    )


class ProductDeleteUncertainError(RuntimeError):
    """The remote delete request may have reached the platform."""

    verification_pending = True


class SyncService:
    def __init__(
        self,
        db: Database,
        sleep: Callable[[float], None] | None = None,
        now_provider: Callable[[], datetime] | None = None,
    ):
        self.db = db
        self.sleep = sleep
        self.now_provider = now_provider or datetime.now
        self.platform_write_authorizer: (
            Callable[[str, str], None] | None
        ) = None
        self._product_payload_defaults_cache: dict[int, dict[str, Any]] = {}
        self._category_rule_cache: dict[tuple[int, str, str], dict[str, Any]] = {}
        self._category_catalog_cache: dict[int, list[dict[str, Any]]] = {}
        self._upload_category_tree_cache: dict[
            tuple[int, str],
            dict[str, Any],
        ] = {}
        self._upload_category_refresh_lock = threading.RLock()
        self._upload_category_refresh_inflight: set[int] = set()
        self._store_valid_brands_cache: dict[
            int,
            tuple[float, list[dict[str, Any]]],
        ] = {}
        self._store_freight_template_ids_cache: dict[int, set[str]] = {}
        self._store_merchant_address_ids_cache: dict[int, set[str]] = {}
        self._procurement_windows: dict[int, ControlledPurchaseWindow] = {}
        self._procurement_windows_lock = threading.RLock()

    def client_for(self, store: Store) -> WechatShopClient | MockShopClient:
        if store.app_id == "demo-store":
            return MockShopClient(store)
        _, secret = self.db.get_store_secret(store.id)
        if not secret:
            raise ValueError("该店铺仅使用网页登录，未配置 AppSecret，不能调用公开 API")
        kwargs: dict[str, Any] = (
            {"sleeper": self.sleep} if self.sleep else {}
        )
        observer = getattr(self, "call_observer", None)
        if callable(observer):
            kwargs["call_observer"] = observer
        authorizer = getattr(self, "platform_write_authorizer", None)
        if callable(authorizer):
            kwargs["write_authorizer"] = authorizer
        return WechatShopClient(store.app_id, secret, **kwargs)

    def _require_platform_write(self, store: Store, action: str) -> None:
        if str(store.app_id) == "demo-store":
            return
        authorizer = getattr(self, "platform_write_authorizer", None)
        if not callable(authorizer):
            raise PermissionError(
                "真实平台写操作缺少当前会话安全门，已拒绝执行："
                f"{str(action)}"
            )
        authorizer(str(store.app_id), str(action))

    def validate_store(self, store: Store) -> None:
        client = self.client_for(store)
        message = client.test_connection()
        self.db.set_store_status(store.id, message)

    def validate_store_credential(
        self,
        store: Store,
        secret: str,
    ) -> str:
        if store.app_id == "demo-store":
            return "连接成功"
        if not str(secret or "").strip():
            raise ValueError("AppSecret 为空")
        kwargs: dict[str, Any] = {"sleeper": self.sleep} if self.sleep else {}
        observer = getattr(self, "call_observer", None)
        if callable(observer):
            kwargs["call_observer"] = observer
        client = WechatShopClient(
            store.app_id,
            str(secret).strip(),
            **kwargs,
        )
        return client.test_connection()

    def sync_orders(
        self,
        store: Store,
        start_time: int,
        end_time: int,
        progress: ProgressCallback | None = None,
    ) -> SyncResult:
        if end_time - start_time > 7 * 86400:
            raise ValueError("订单同步时间范围不能超过 7 天")
        run_id = self.db.start_sync_run(store.id, "orders")
        try:
            client = self.client_for(store)
            if isinstance(client, MockShopClient):
                orders = client.fetch_orders(start_time, end_time, progress)
            else:
                order_ids = client.fetch_order_ids(start_time, end_time)
                orders = []
                total = len(order_ids)
                for index, order_id in enumerate(order_ids, 1):
                    if progress:
                        progress(f"正在拉取订单详情 {index}/{total}", index, total)
                    order = client.fetch_order(store, order_id)
                    self.db.upsert_order(order)
                    orders.append(order)
            if isinstance(client, MockShopClient):
                for index, order in enumerate(orders, 1):
                    self.db.upsert_order(order)
                    if progress:
                        progress(f"正在保存订单 {index}/{len(orders)}", index, len(orders))
            self.db.touch_store_sync(store.id)
            message = f"同步完成，共 {len(orders)} 个订单"
            self.db.finish_sync_run(run_id, "success", len(orders), message)
            return SyncResult("orders", len(orders), message)
        except Exception as exc:
            self.db.finish_sync_run(run_id, "failed", 0, str(exc))
            raise

    def refresh_orders_recent(
        self,
        store: Store,
        start_time: int,
        end_time: int,
        current_order_ids: list[str] | tuple[str, ...] | None = None,
        progress: ProgressCallback | None = None,
    ) -> SyncResult:
        """Refresh update-time changes and every currently displayed order."""
        if end_time <= start_time:
            raise ValueError("订单刷新结束时间必须晚于开始时间")
        if end_time - start_time > 7 * 86400:
            raise ValueError("订单刷新时间范围不能超过 7 天")
        run_id = self.db.start_sync_run(store.id, "orders")
        try:
            client = self.client_for(store)
            if isinstance(client, MockShopClient):
                orders = client.fetch_orders(start_time, end_time, progress)
                ids = [order.order_id for order in orders]
            else:
                fetch_updates = getattr(
                    client,
                    "fetch_order_ids_by_update",
                    None,
                )
                if not callable(fetch_updates):
                    raise RuntimeError(
                        "当前客户端不支持按 update_time 刷新订单"
                    )
                update_ids = fetch_updates(start_time, end_time)
                tracked_ids = [
                    str(value).strip()
                    for value in (current_order_ids or [])
                    if str(value).strip()
                ]
                ids = list(dict.fromkeys([*update_ids, *tracked_ids]))
                failures: list[str] = []
                fetched = 0
                for index, order_id in enumerate(ids, start=1):
                    if progress:
                        progress(
                            f"正在核验订单状态 {index}/{len(ids)}",
                            index,
                            len(ids),
                        )
                    try:
                        order = client.fetch_order(store, order_id)
                        self.db.upsert_order(order)
                        fetched += 1
                    except Exception as exc:
                        failures.append(f"{order_id}: {exc}")
                if failures and not fetched:
                    raise RuntimeError(
                        "订单刷新全部失败：" + "；".join(failures[:5])
                    )
                self.db.touch_store_sync(store.id)
                message = (
                    f"按更新时间核验 {len(ids)} 个订单，"
                    f"成功 {fetched}"
                )
                if failures:
                    message += f"，失败 {len(failures)}"
                self.db.finish_sync_run(
                    run_id,
                    "success" if not failures else "partial",
                    fetched,
                    message,
                )
                return SyncResult(
                    "orders",
                    fetched,
                    message,
                    success=fetched,
                    failed=len(failures),
                    errors=failures[:20],
                )
            for index, order in enumerate(orders, start=1):
                self.db.upsert_order(order)
                if progress:
                    progress(
                        f"正在保存订单 {index}/{len(orders)}",
                        index,
                        len(orders),
                    )
            self.db.touch_store_sync(store.id)
            message = f"订单刷新完成，共核验 {len(ids)} 个订单"
            self.db.finish_sync_run(run_id, "success", len(ids), message)
            return SyncResult("orders", len(ids), message, success=len(ids))
        except Exception as exc:
            self.db.finish_sync_run(run_id, "failed", 0, str(exc))
            raise

    def _ensure_today_orders_cached(
        self,
        store: Store,
        start_time: int,
        end_time: int,
    ) -> bool:
        """Backfill today's order states once so sales metrics use real status."""
        business_day = datetime.fromtimestamp(
            start_time,
            SHANGHAI_TZ,
        ).date().isoformat()
        setting_key = f"orders_day_backfill_{store.id}"
        if self.db.get_setting(setting_key, "") == business_day:
            return False
        try:
            client = self.client_for(store)
        except ValueError:
            return False
        if isinstance(client, MockShopClient):
            return False
        self.sync_orders(store, start_time, end_time)
        self.db.set_setting(setting_key, business_day)
        return True

    def update_order_notes(
        self,
        store: Store,
        order_id: str,
        notes: str,
        color: int = 0,
    ) -> dict[str, Any]:
        self._require_platform_write(store, "order_notes_update")
        if str(store.app_id) == "demo-store":
            self.db.update_order_note(store.id, order_id, notes)
            return {
                "merchant_notes": str(notes),
                "merchant_notes_tag_color": int(color),
                "verified": True,
            }

        private_error: Exception | None = None
        try:
            private = ShopPrivateClient(self.db, store)
            private.update_order_notes_private(order_id, notes, color)
            official = private.order_detail(order_id)
        except ShopPrivateError as exc:
            private_error = exc
            private = None
            official = {}
        else:
            private_error = None

        if private is None:
            client = self.client_for(store)
            if isinstance(client, MockShopClient):
                raise RuntimeError("演示店铺不支持真实备注回读")
            client.update_order_notes(order_id, notes, color)
            updated_order = client.fetch_order(store, order_id)
            raw_detail = updated_order.raw.get("order_detail")
            raw_detail = (
                raw_detail if isinstance(raw_detail, dict) else {}
            )
            ext_info = raw_detail.get("ext_info")
            ext_info = ext_info if isinstance(ext_info, dict) else {}
            official = {
                "order_id": order_id,
                "merchant_notes": str(
                    ext_info.get("merchant_notes")
                    or updated_order.merchant_notes
                    or ""
                ),
                "merchant_notes_tag_color": int(
                    ext_info.get("merchant_notes_tag_color") or 0
                ),
                "packages": [],
            }

        actual_notes = str(official.get("merchant_notes") or "")
        try:
            actual_color = int(
                official.get("merchant_notes_tag_color") or 0
            )
        except (TypeError, ValueError):
            actual_color = 0
        if actual_notes != str(notes) or actual_color != int(color):
            detail = (
                f"备注：{actual_notes!r}，旗帜：{actual_color}"
            )
            if private_error is not None:
                detail += f"；网页登录写入错误：{private_error}"
            raise RuntimeError(
                "官方订单备注回读不一致，未确认保存成功（"
                + detail
                + "）"
            )
        self.db.update_order_platform_detail(
            store.id,
            order_id,
            merchant_notes=actual_notes,
            merchant_notes_tag_color=actual_color,
            packages=official.get("packages") or None,
        )
        return official

    def official_order_detail(
        self,
        store: Store,
        order_id: str,
    ) -> dict[str, Any]:
        """Read order notes, flag and package traces from the official backend."""
        private = ShopPrivateClient(self.db, store)
        detail = private.order_detail(order_id)
        self.db.update_order_platform_detail(
            store.id,
            order_id,
            merchant_notes=str(detail.get("merchant_notes") or ""),
            merchant_notes_tag_color=int(
                detail.get("merchant_notes_tag_color") or 0
            ),
            packages=detail.get("packages") or None,
        )
        return detail

    def refresh_order_official(
        self,
        store: Store,
        order_id: str,
    ) -> dict[str, Any]:
        """Refresh one order and merge official notes/package state."""
        public_error: Exception | None = None
        try:
            client = self.client_for(store)
            if not isinstance(client, MockShopClient):
                self.db.upsert_order(client.fetch_order(store, order_id))
        except Exception as exc:
            public_error = exc
        try:
            detail = self.official_order_detail(store, order_id)
        except Exception:
            if public_error is not None:
                raise public_error
            raise
        detail["public_refresh_error"] = (
            str(public_error) if public_error is not None else ""
        )
        return detail

    def update_order_price(
        self,
        store: Store,
        order_id: str,
        change_infos: list[dict[str, Any]],
        change_express: bool,
        express_fee: int,
    ) -> None:
        self._require_platform_write(store, "order_price_update")
        client = self.client_for(store)
        if not isinstance(client, MockShopClient):
            client.update_order_price(
                {
                    "order_id": order_id,
                    "change_order_infos": change_infos,
                    "change_express": change_express,
                    "express_fee": express_fee if change_express else 0,
                }
            )
            try:
                order = client.fetch_order(store, order_id)
                self.db.upsert_order(order)
            except Exception:
                # The write already returned success. A follow-up read failure
                # must not be reported as a failed write and trigger a retry.
                pass

    def request_order_address_change(
        self,
        store: Store,
        order_id: str,
        address: dict[str, str],
    ) -> None:
        self._require_platform_write(store, "order_address_update")
        client = self.client_for(store)
        if isinstance(client, MockShopClient):
            return
        client.update_order_address(
            {
                "order_id": order_id,
                "user_address": address,
            }
        )

    def update_order_delivery_info(
        self,
        store: Store,
        order_id: str,
        waybill_id: str,
        delivery_id: str,
        product_infos: list[dict[str, Any]],
    ) -> None:
        self._require_platform_write(store, "order_delivery_update")
        client = self.client_for(store)
        if isinstance(client, MockShopClient):
            return
        client.update_order_delivery_info(
            {
                "order_id": order_id,
                "delivery_list": [
                    {
                        "deliver_type": 1,
                        "delivery_id": delivery_id,
                        "waybill_id": waybill_id,
                        "product_infos": product_infos,
                    }
                ],
            }
        )
        self.db.update_order_delivery(
            store.id,
            order_id,
            waybill_id,
            delivery_id,
            21,
            order_status_text(21),
        )

    def update_order_delivery_packages(
        self,
        store: Store,
        order_id: str,
        packages: list[dict[str, str]],
        product_infos: list[dict[str, Any]],
    ) -> None:
        self._require_platform_write(store, "order_delivery_update")
        if not packages:
            raise ValueError("至少需要一个店铺包裹")
        client = self.client_for(store)
        if isinstance(client, MockShopClient):
            return
        client.update_order_delivery_info(
            {
                "order_id": order_id,
                "delivery_list": [
                    {
                        "deliver_type": 1,
                        "delivery_id": str(package["delivery_id"]),
                        "waybill_id": str(package["waybill_id"]),
                        "product_infos": product_infos,
                    }
                    for package in packages
                ],
            }
        )
        first = packages[0]
        self.db.update_order_delivery(
            store.id,
            order_id,
            str(first["waybill_id"]),
            str(first["delivery_id"]),
            21,
            order_status_text(21),
        )

    def aftersale_action(
        self,
        store: Store,
        after_sale_id: str,
        action: str,
        payload: dict[str, Any] | None = None,
    ) -> SyncResult:
        self._require_platform_write(store, f"aftersale_{action}")
        client = self.client_for(store)
        payload = dict(payload or {})
        payload["after_sale_order_id"] = after_sale_id
        if isinstance(client, MockShopClient):
            return SyncResult(
                f"aftersale_{action}",
                1,
                "演示店铺操作成功",
                success=1,
            )
        methods = {
            "accept": client.accept_after_sale,
            "reject": client.reject_after_sale,
            "negotiate": client.negotiate_after_sale,
            "reship": client.reship_after_sale,
            "update_reship": client.update_after_sale_reship,
            "accept_exchange": client.accept_after_sale_exchange,
            "reject_exchange": client.reject_after_sale_exchange,
        }
        if action not in methods:
            raise ValueError("不支持的售后操作")
        methods[action](payload)
        try:
            detail = client.fetch_after_sale(after_sale_id)
            self.db.upsert_after_sale(store.id, detail)
        except Exception:
            pass
        return SyncResult(
            f"aftersale_{action}",
            1,
            "售后操作已提交",
            success=1,
        )

    def upload_after_sale_media(
        self,
        store: Store,
        paths: list[str],
    ) -> list[str]:
        self._require_platform_write(store, "aftersale_media_upload")
        client = self.client_for(store)
        if isinstance(client, MockShopClient):
            return [f"demo://after-sale-proof/{Path(path).name}" for path in paths]
        return [client.upload_product_image(path) for path in paths]

    @staticmethod
    def _uses_hosted_product_image(url: str) -> bool:
        host = (urlparse(str(url or "")).hostname or "").lower()
        return (
            host == "mmecimage.cn"
            or host.endswith(".mmecimage.cn")
            or host.endswith(".wxqcloud.qq.com.cn")
            or host.endswith(".wxapp.tc.qq.com")
        )

    @staticmethod
    def _valid_express_info(value: Any) -> bool:
        if not isinstance(value, dict):
            return False
        template_id = str(value.get("template_id") or "").strip()
        return bool(template_id and template_id != "0")

    @staticmethod
    def _valid_after_sale_info(value: Any) -> bool:
        if not isinstance(value, dict):
            return False
        address_id = str(value.get("after_sale_address_id") or "").strip()
        return bool(address_id and address_id != "0")

    @staticmethod
    def _category_leaf_index(
        options: dict[str, Any],
    ) -> dict[str, dict[str, Any]]:
        leaves: dict[str, dict[str, Any]] = {}

        def walk(nodes: list[dict[str, Any]]) -> None:
            for node in nodes:
                if not isinstance(node, dict):
                    continue
                if node.get("leaf"):
                    category_id = str(
                        node.get("category_id") or ""
                    ).strip()
                    if category_id:
                        leaves[category_id] = node
                walk(node.get("children") or [])

        walk(options.get("roots") or [])
        return leaves

    @staticmethod
    def _upload_category_cache_key(store: Store) -> str:
        return f"upload_category_cache_{store.id}"

    def _read_upload_category_cache(
        self,
        store: Store,
    ) -> dict[str, Any] | None:
        raw = self.db.get_setting(
            self._upload_category_cache_key(store),
            "",
        )
        if not raw:
            return None
        try:
            cached = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
        if not isinstance(cached, dict):
            return None
        if (
            str(cached.get("store_id") or "") != str(store.id)
            or str(cached.get("store_app_id") or "") != store.app_id
            or not isinstance(cached.get("roots"), list)
            or not str(cached.get("version") or "")
        ):
            return None
        return cached

    def _write_upload_category_cache(
        self,
        store: Store,
        payload: dict[str, Any],
        *,
        touch_saved_at: bool = True,
    ) -> dict[str, Any]:
        saved = deepcopy(payload)
        saved.update(
            {
                "schema": UPLOAD_CATEGORY_CACHE_SCHEMA,
                "store_id": store.id,
                "store_app_id": store.app_id,
                "saved_at": (
                    float(time.time())
                    if touch_saved_at
                    else float(saved.get("saved_at") or 0)
                ),
                "cache_key": (
                    str(saved.get("cache_key") or "")
                    or f"{store.id}:{saved.get('version') or ''}"
                ),
            }
        )
        self.db.set_setting(
            self._upload_category_cache_key(store),
            json.dumps(saved, ensure_ascii=False),
        )
        if saved.get("version"):
            self._upload_category_tree_cache[
                (store.id, str(saved["version"]))
            ] = deepcopy(saved)
        return saved

    @staticmethod
    def _decorate_upload_category_cache(
        cached: dict[str, Any],
        *,
        now_ts: float,
        refresh_pending: bool = False,
    ) -> dict[str, Any]:
        result = deepcopy(cached)
        saved_at = float(result.get("saved_at") or 0)
        age = max(0, int(now_ts - saved_at)) if saved_at else 0
        result.update(
            {
                "cached": True,
                "stale": (
                    not saved_at
                    or age >= UPLOAD_CATEGORY_CACHE_TTL_SECONDS
                ),
                "age_seconds": age,
                "refresh_pending": bool(refresh_pending),
                "expires_at": (
                    datetime.fromtimestamp(
                        saved_at + UPLOAD_CATEGORY_CACHE_TTL_SECONDS
                    ).isoformat(timespec="seconds")
                    if saved_at
                    else ""
                ),
            }
        )
        return result

    def _record_upload_category_refresh_failure(
        self,
        store: Store,
        cached: dict[str, Any],
        error: Exception | str,
        *,
        attempted_at: float | None = None,
    ) -> dict[str, Any]:
        failed = deepcopy(cached)
        failed.update(
            {
                "last_refresh_attempt_at": float(
                    attempted_at or time.time()
                ),
                "refresh_error": str(error)[:1000],
            }
        )
        return self._write_upload_category_cache(
            store,
            failed,
            touch_saved_at=False,
        )

    def _schedule_upload_category_refresh(
        self,
        store: Store,
        client: WechatShopClient | MockShopClient | None,
        cached: dict[str, Any],
    ) -> bool:
        now_ts = time.time()
        try:
            last_attempt = float(
                cached.get("last_refresh_attempt_at") or 0
            )
        except (TypeError, ValueError):
            last_attempt = 0.0
        if (
            last_attempt
            and now_ts - last_attempt
            < UPLOAD_CATEGORY_REFRESH_COOLDOWN_SECONDS
        ):
            return False
        with self._upload_category_refresh_lock:
            if store.id in self._upload_category_refresh_inflight:
                return False
            self._upload_category_refresh_inflight.add(store.id)

        def worker() -> None:
            try:
                fresh = self._fetch_upload_category_options(
                    store,
                    client,
                )
                fresh["last_refresh_attempt_at"] = time.time()
                fresh["refresh_error"] = ""
                self._write_upload_category_cache(store, fresh)
            except Exception as exc:
                latest = self._read_upload_category_cache(store) or cached
                self._record_upload_category_refresh_failure(
                    store,
                    latest,
                    exc,
                )
            finally:
                with self._upload_category_refresh_lock:
                    self._upload_category_refresh_inflight.discard(
                        store.id
                    )

        refresh_thread = threading.Thread(
            target=worker,
            name=f"upload-category-refresh-{store.id}",
            daemon=True,
        )
        refresh_thread.start()
        return True

    def upload_category_options(
        self,
        store: Store,
        client: WechatShopClient | MockShopClient | None = None,
        *,
        refresh: bool = False,
    ) -> dict[str, Any]:
        """Return a persistent per-store category snapshot.

        Fresh cache is returned without network access. Expired cache is
        returned immediately while one background refresh is started.
        Manual refresh is synchronous and falls back to the previous snapshot
        if the official read fails.
        """
        client = client or self.client_for(store)
        cached = self._read_upload_category_cache(store)
        if cached is not None and not refresh:
            result = self._decorate_upload_category_cache(
                cached,
                now_ts=time.time(),
            )
            if result["stale"]:
                scheduled = self._schedule_upload_category_refresh(
                    store,
                    client,
                    cached,
                )
                result["refresh_pending"] = scheduled
                if not scheduled and cached.get("refresh_error"):
                    result["error"] = (
                        "类目缓存已过期，后台刷新失败："
                        + str(cached.get("refresh_error") or "")
                    )
            return result
        try:
            fresh = self._fetch_upload_category_options(store, client)
        except Exception as exc:
            if cached is None:
                raise
            failed = self._record_upload_category_refresh_failure(
                store,
                cached,
                exc,
            )
            result = self._decorate_upload_category_cache(
                failed,
                now_ts=time.time(),
            )
            result.update(
                {
                    "manual_refresh_failed": bool(refresh),
                    "refresh_error": str(exc)[:1000],
                    "error": f"类目刷新失败，继续使用旧缓存：{str(exc)[:500]}",
                }
            )
            return result
        fresh["last_refresh_attempt_at"] = time.time()
        fresh["refresh_error"] = ""
        saved = self._write_upload_category_cache(store, fresh)
        result = self._decorate_upload_category_cache(
            saved,
            now_ts=time.time(),
        )
        result["cached"] = False
        result["stale"] = False
        result["refresh_pending"] = False
        return result

    def _fetch_upload_category_options(
        self,
        store: Store,
        client: WechatShopClient | MockShopClient | None = None,
        *,
        refresh: bool = False,
    ) -> dict[str, Any]:
        """Read an official category tree filtered by this store's permissions."""
        client = client or self.client_for(store)
        if isinstance(client, MockShopClient):
            result = {
                "roots": [],
                "leaf_count": 0,
                "version": "",
                "cache_key": "",
                "source": "演示店铺无官方类目",
                "refreshed_at": datetime.now().isoformat(
                    timespec="seconds"
                ),
            }
            return result
        fetch_all = getattr(client, "fetch_all_categories", None)
        fetch_relations = getattr(
            client,
            "fetch_effective_category_relations",
            None,
        )
        if not callable(fetch_all) or not callable(fetch_relations):
            raise RuntimeError("当前店铺接口不支持官方类目读取")
        raw = fetch_all()
        raw_v2 = raw.get("cats_v2") if isinstance(raw, dict) else None
        if not isinstance(raw_v2, list) or not raw_v2:
            raise ValueError("官方类目树没有返回 cats_v2")
        version = hashlib.sha256(
            json.dumps(
                raw_v2,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()[:20]
        cache_key = (store.id, version)
        relation_ids = {
            str(value).strip()
            for value in fetch_relations()
            if str(value).strip()
        }
        permission_version = hashlib.sha256(
            json.dumps(
                sorted(relation_ids),
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()[:8]
        version = f"{version}-{permission_version}"
        cache_key = (store.id, version)
        tree_payload = None
        if tree_payload is None:
            roots_by_id: dict[str, dict[str, Any]] = {}
            paths: list[list[dict[str, Any]]] = []
            for group in raw_v2:
                if not isinstance(group, dict):
                    continue
                entries = [
                    item
                    for item in group.get("cat_and_qua") or []
                    if isinstance(item, dict)
                    and isinstance(item.get("cat"), dict)
                ]
                leaf_entry = next(
                    (
                        item
                        for item in entries
                        if bool(item["cat"].get("leaf"))
                    ),
                    None,
                )
                if leaf_entry is None:
                    continue
                leaf_id = str(
                    leaf_entry["cat"].get("cat_id") or ""
                ).strip()
                if not leaf_id:
                    continue
                path = sorted(
                    entries,
                    key=lambda item: int(
                        item["cat"].get("level") or 0
                    ),
                )
                path_nodes = [item["cat"] for item in path]
                if not path_nodes:
                    continue
                paths.append(path_nodes)

            for path_nodes in paths:
                parent_id = ""
                target = roots_by_id
                for index, source in enumerate(path_nodes):
                    category_id = str(
                        source.get("cat_id") or ""
                    ).strip()
                    if not category_id:
                        break
                    if (
                        index
                        and str(source.get("f_cat_id") or "")
                        != parent_id
                    ):
                        break
                    node = target.get(category_id)
                    if node is None:
                        node = {
                            "category_id": category_id,
                            "name": str(source.get("name") or ""),
                            "level": int(source.get("level") or index + 1),
                            "leaf": bool(source.get("leaf")),
                            "available": (
                                category_id in relation_ids
                                if bool(source.get("leaf"))
                                else None
                            ),
                            "_children": {},
                        }
                        target[category_id] = node
                    else:
                        node["leaf"] = bool(source.get("leaf"))
                        if node["leaf"]:
                            node["available"] = category_id in relation_ids
                    parent_id = category_id
                    target = node["_children"]

            def materialize(
                nodes: dict[str, dict[str, Any]],
                parents: list[str] | None = None,
                parent_names: list[str] | None = None,
            ) -> list[dict[str, Any]]:
                output: list[dict[str, Any]] = []
                parents = parents or []
                parent_names = parent_names or []
                for node in nodes.values():
                    id_path = [
                        *parents,
                        str(node.get("category_id") or ""),
                    ]
                    names = [
                        *parent_names,
                        str(node.get("name") or ""),
                    ]
                    children = materialize(
                        node.get("_children") or {},
                        id_path,
                        names,
                    )
                    output.append(
                        {
                            key: value
                            for key, value in node.items()
                            if key != "_children"
                        }
                        | {
                            "id_path": id_path,
                            "path": " / ".join(
                                value for value in names if value
                            ),
                            "children": children,
                        }
                    )
                return output

            roots = materialize(roots_by_id)
            leaf_count = len(self._category_leaf_index({"roots": roots}))
            available_leaf_count = sum(
                bool(node.get("available"))
                for node in self._category_leaf_index({"roots": roots}).values()
            )
            tree_payload = {
                "roots": roots,
                "leaf_count": leaf_count,
                "available_leaf_count": available_leaf_count,
                "version": version,
                "cache_key": f"{store.id}:{version}",
                "source": "微信小店官方类目树 + 本店生效类目权限",
                "refreshed_at": datetime.now().isoformat(
                    timespec="seconds"
                ),
                "cached": False,
            }
            if not roots:
                tree_payload["error"] = "店铺暂无生效类目权限"
            elif not available_leaf_count:
                tree_payload["warning"] = "店铺暂无生效类目权限，请先申请类目"
            self._upload_category_tree_cache[cache_key] = deepcopy(tree_payload)
        return deepcopy(tree_payload)

    def valid_store_brands(
        self,
        store: Store,
        client: WechatShopClient | MockShopClient | None = None,
        *,
        refresh: bool = False,
    ) -> list[dict[str, Any]]:
        now = time.monotonic()
        cached = self._store_valid_brands_cache.get(store.id)
        if (
            cached is not None
            and not refresh
            and now - float(cached[0]) < 600
        ):
            return deepcopy(cached[1])
        client = client or self.client_for(store)
        if isinstance(client, MockShopClient):
            return []
        fetch_brands = getattr(client, "fetch_valid_brands", None)
        if not callable(fetch_brands):
            raise RuntimeError("当前店铺接口不支持官方品牌读取")
        rows: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in fetch_brands() or []:
            if not isinstance(item, dict):
                continue
            brand_id = str(item.get("brand_id") or "").strip()
            if not brand_id or brand_id in seen:
                continue
            seen.add(brand_id)
            rows.append({**item, "brand_id": brand_id})
        self._store_valid_brands_cache[store.id] = (
            now,
            deepcopy(rows),
        )
        return rows

    def validate_category_selection(
        self,
        store: Store,
        category_ids: list[str],
        category_id: str = "",
        client: WechatShopClient | MockShopClient | None = None,
    ) -> dict[str, Any]:
        normalized_ids = [
            str(value).strip()
            for value in category_ids
            if str(value).strip().isdigit()
        ]
        leaf_id = str(
            category_id
            or (normalized_ids[-1] if normalized_ids else "")
        ).strip()
        if not normalized_ids or not leaf_id.isdigit():
            raise ValueError("指定类目缺少有效叶子 ID")
        if normalized_ids[-1] != leaf_id:
            raise ValueError("指定类目的叶子 ID 与类目路径末级不一致")
        options = self.upload_category_options(
            store,
            client,
            refresh=False,
        )
        leaf = self._category_leaf_index(options).get(leaf_id)
        if leaf is None or leaf.get("available") is not True:
            options = self.upload_category_options(
                store,
                client,
                refresh=True,
            )
            leaf = self._category_leaf_index(options).get(leaf_id)
        if leaf is None or leaf.get("available") is not True:
            detail = str(
                options.get("refresh_error")
                or options.get("error")
                or ""
            ).strip()
            suffix = f"；刷新结果：{detail}" if detail else ""
            raise ValueError(
                f"{store.name} 指定类目 {leaf_id} 已失效、被冻结或不存在，"
                f"请修改模板{suffix}"
            )
        current_ids = [
            str(value)
            for value in leaf.get("id_path") or []
            if str(value).strip()
        ]
        if current_ids != normalized_ids:
            raise ValueError(
                f"{store.name} 指定类目路径已变化，请重新选择模板类目"
            )
        return {
            "category_id": leaf_id,
            "category_ids": current_ids,
            "path": str(leaf.get("path") or ""),
            "version": str(options.get("version") or ""),
            "cache_key": str(options.get("cache_key") or ""),
        }

    def _validate_specified_product_category(
        self,
        store: Store,
        client: WechatShopClient,
        payload: dict[str, Any],
    ) -> None:
        category_id = str(
            payload.get("_specified_category_id") or ""
        ).strip()
        if not category_id:
            return
        category_ids = [
            str(item.get("cat_id") or "").strip()
            for item in payload.get("cats_v2") or []
            if isinstance(item, dict)
            and str(item.get("cat_id") or "").strip().isdigit()
        ]
        validated = self.validate_category_selection(
            store,
            category_ids,
            category_id,
            client,
        )
        payload["category_path"] = validated["path"]
        brand_mode = str(payload.get("_brand_mode") or "").strip()
        brand_id = str(payload.get("brand_id") or "").strip()
        if brand_mode == "none":
            brand_id = "2100000000"
            payload["brand_id"] = brand_id
        elif brand_mode == "store":
            valid_ids = {
                str(item.get("brand_id") or "").strip()
                for item in self.valid_store_brands(store, client)
            }
            if not brand_id or brand_id not in valid_ids:
                raise ValueError(
                    f"{store.name} 店铺品牌 {brand_id or '未选择'} "
                    "不存在、未生效或已过期"
                )
        category_rule_key = (
            store.id,
            category_id,
            brand_id or "2100000000",
        )
        rule = self._category_rule_cache.get(category_rule_key)
        if not isinstance(rule, dict):
            rule = client.fetch_category_product_rule(
                category_id,
                brand_id=brand_id or "2100000000",
            )
            self._category_rule_cache[category_rule_key] = rule
        brand_info = rule.get("brand_info")
        brand_info = brand_info if isinstance(brand_info, dict) else {}
        if (
            brand_mode == "store"
            and brand_info.get("is_brand_authorized") is False
        ):
            raise ValueError(
                f"{store.name} 所选品牌未获得指定类目的品牌授权"
            )
        if (
            bool(rule.get("is_directed_category"))
            or bool(rule.get("restricted_brand_list"))
        ) and brand_mode == "none":
            raise ValueError(
                f"{store.name} 指定类目为品牌定向类目，"
                "必须选择当前店铺生效品牌"
            )
        restricted_brand_ids = {
            str(item.get("brand_id") or "").strip()
            for item in rule.get("restricted_brand_list") or []
            if isinstance(item, dict)
        }
        if (
            brand_mode == "store"
            and restricted_brand_ids
            and brand_id not in restricted_brand_ids
        ):
            raise ValueError(
                f"{store.name} 所选品牌不在指定类目的可售品牌范围内"
            )
        provided_qualifications = {
            str(item.get("qua_id") or "").strip()
            for item in payload.get("product_qua_infos") or []
            if isinstance(item, dict)
        }
        for item in rule.get("product_qua_list") or []:
            if not isinstance(item, dict):
                continue
            required = item.get("required_rule") or {}
            if int(required.get("rule_type") or 0) != 1:
                continue
            qualification_id = str(
                item.get("id") or item.get("qua_id") or ""
            ).strip()
            if (
                qualification_id
                and qualification_id not in provided_qualifications
            ):
                raise ValueError(
                    f"{store.name} 指定类目缺少必填商品资质："
                    f"{item.get('name') or qualification_id}"
                )
        provided_attrs = {
            str(item.get("attr_key") or "").strip(): str(
                item.get("attr_value") or ""
            ).strip()
            for item in payload.get("attrs") or []
            if isinstance(item, dict)
        }
        for item in rule.get("product_attr_list") or []:
            if not isinstance(item, dict):
                continue
            required_rule = item.get("required_rule") or {}
            is_required = bool(
                item.get("is_required")
                or int(required_rule.get("rule_type") or 0) == 1
            )
            if not is_required:
                continue
            name = str(item.get("name") or "").strip()
            if name and not provided_attrs.get(name):
                raise ValueError(
                    f"{store.name} 指定类目缺少必填商品属性：{name}"
                )
        deposit_rule = client.fetch_category_rule(
            category_id,
            rule_id=1,
        )
        required_deposit = max(
            0,
            int(deposit_rule.get("deposit") or 0),
        )
        try:
            deposit_limit = max(
                0,
                int(payload.get("_deposit_limit_cents") or 0),
            )
        except (TypeError, ValueError):
            deposit_limit = 0
        if deposit_limit and required_deposit > deposit_limit:
            raise ValueError(
                f"{store.name} 指定类目需要保证金 "
                f"{required_deposit} 分，超过模板上限 {deposit_limit} 分"
            )

    def _product_payload_defaults(
        self,
        store: Store,
        client: WechatShopClient | None = None,
    ) -> dict[str, Any]:
        cached = self._product_payload_defaults_cache.get(store.id)
        if cached is not None:
            return cached
        defaults: dict[str, Any] = {}
        with self.db.connect() as connection:
            rows = connection.execute(
                "SELECT raw_json FROM products "
                "WHERE store_id=? AND status=5 "
                "ORDER BY synced_at DESC LIMIT 20",
                (store.id,),
            ).fetchall()
        for row in rows:
            try:
                payload = json.loads(row["raw_json"] or "{}")
            except (TypeError, ValueError):
                continue
            if not isinstance(payload, dict):
                continue
            for key in (
                "express_info",
                "after_sale_info",
                "deliver_acct_type",
                "limited_info",
                "size_chart",
                "hide_in_window",
                "release_mode",
                "head_videos",
            ):
                if defaults.get(key) in (None, {}, []) and payload.get(key):
                    defaults[key] = deepcopy(payload[key])
            if len(defaults.get("head_imgs") or []) < 3:
                defaults["head_imgs"] = deepcopy(payload.get("head_imgs") or [])
            if not (defaults.get("desc_info") or {}).get("imgs"):
                defaults["desc_info"] = deepcopy(payload.get("desc_info") or {})
            if (
                self._valid_express_info(defaults.get("express_info"))
                and self._valid_after_sale_info(defaults.get("after_sale_info"))
                and len(defaults.get("head_imgs") or []) >= 3
            ):
                self._product_payload_defaults_cache[store.id] = defaults
                return defaults
        if client is not None and (
            not self._valid_express_info(defaults.get("express_info"))
            or not self._valid_after_sale_info(defaults.get("after_sale_info"))
            or len(defaults.get("head_imgs") or []) < 3
        ):
            fetch_ids = getattr(client, "fetch_product_ids", None)
            fetch_product = getattr(client, "fetch_product", None)
            if callable(fetch_ids) and callable(fetch_product):
                try:
                    for product_id in list(fetch_ids(5))[:3]:
                        product = fetch_product(store, product_id)
                        remote = getattr(product, "raw", {})
                        if not isinstance(remote, dict):
                            continue
                        for key in (
                            "express_info",
                            "after_sale_info",
                            "deliver_acct_type",
                            "limited_info",
                            "size_chart",
                            "hide_in_window",
                            "release_mode",
                            "head_videos",
                        ):
                            if not defaults.get(key) and remote.get(key):
                                defaults[key] = deepcopy(remote[key])
                        if len(defaults.get("head_imgs") or []) < 3:
                            defaults["head_imgs"] = deepcopy(remote.get("head_imgs") or [])
                        if len(defaults.get("desc_info", {}).get("imgs") or []) == 0:
                            defaults["desc_info"] = deepcopy(
                                remote.get("desc_info") or defaults.get("desc_info")
                            )
                        if (
                            self._valid_express_info(defaults.get("express_info"))
                            and self._valid_after_sale_info(defaults.get("after_sale_info"))
                            and len(defaults.get("head_imgs") or []) >= 3
                        ):
                            break
                except Exception:
                    pass
        if client is not None:
            if not self._valid_express_info(defaults.get("express_info")):
                fetch_freight = getattr(
                    client,
                    "fetch_default_freight_template",
                    None,
                )
                if callable(fetch_freight):
                    try:
                        template = fetch_freight()
                    except Exception:
                        template = {}
                    template_id = str((template or {}).get("template_id") or "").strip()
                    if template_id and template_id != "0":
                        defaults["express_info"] = {
                            "template_id": template_id,
                            "weight": 1000,
                            "express_type": 0,
                        }
            if not self._valid_after_sale_info(defaults.get("after_sale_info")):
                fetch_address = getattr(
                    client,
                    "fetch_default_after_sale_address",
                    None,
                )
                if callable(fetch_address):
                    try:
                        address = fetch_address()
                    except Exception:
                        address = {}
                    address_id = str((address or {}).get("address_id") or "").strip()
                    if address_id and address_id != "0":
                        defaults["after_sale_info"] = {
                            "after_sale_address_id": address_id,
                        }
        if defaults.get("deliver_method") is None:
            defaults["deliver_method"] = 0
        if defaults.get("deliver_acct_type") is None:
            defaults["deliver_acct_type"] = []
        self._product_payload_defaults_cache[store.id] = defaults
        return defaults

    def _validate_store_delivery_config(
        self,
        store: Store,
        client: WechatShopClient,
        payload: dict[str, Any],
    ) -> None:
        template_id = str(
            (payload.get("express_info") or {}).get("template_id") or ""
        ).strip()
        fetch_template_ids = getattr(
            client,
            "fetch_freight_template_ids",
            None,
        )
        if template_id and callable(fetch_template_ids):
            template_ids = self._store_freight_template_ids_cache.get(store.id)
            if template_ids is None:
                try:
                    template_ids = {
                        str(value).strip()
                        for value in fetch_template_ids()
                        if str(value).strip()
                    }
                except Exception as exc:
                    raise ValueError(
                        f"{store.name}无法从官方读取运费模板，"
                        "已停止提交以避免模板失效："
                        f"{exc}"
                    ) from exc
                self._store_freight_template_ids_cache[store.id] = template_ids
            if template_id not in template_ids:
                raise ValueError(
                    f"{store.name}所选运费模板 {template_id} "
                    "不属于当前店铺或已失效，请重新选择官方模板"
                )

        address_id = str(
            (payload.get("after_sale_info") or {}).get(
                "after_sale_address_id"
            )
            or ""
        ).strip()
        fetch_address_ids = getattr(
            client,
            "fetch_merchant_address_ids",
            None,
        )
        if address_id and callable(fetch_address_ids):
            address_ids = self._store_merchant_address_ids_cache.get(store.id)
            if address_ids is None:
                try:
                    address_ids = {
                        str(value).strip()
                        for value in fetch_address_ids()
                        if str(value).strip()
                    }
                except Exception as exc:
                    raise ValueError(
                        f"{store.name}无法从官方读取售后地址，"
                        "已停止提交以避免地址失效："
                        f"{exc}"
                    ) from exc
                self._store_merchant_address_ids_cache[store.id] = address_ids
            if address_id not in address_ids:
                raise ValueError(
                    f"{store.name}所选售后地址 {address_id} "
                    "不属于当前店铺或已失效，请重新选择官方地址"
                )

    @staticmethod
    def _category_leaf_id(payload: dict[str, Any]) -> str:
        for key in ("cats_v2", "cats"):
            values = payload.get(key)
            if not isinstance(values, list):
                continue
            for value in reversed(values):
                category_id = value.get("cat_id") if isinstance(value, dict) else value
                category_id = str(category_id or "").strip()
                if category_id.isdigit():
                    return category_id
        return ""

    @staticmethod
    def _attribute_options(attribute: dict[str, Any]) -> list[str]:
        return [
            value.strip() for value in str(attribute.get("value") or "").split(";") if value.strip()
        ]

    @staticmethod
    def _infer_material(
        payload: dict[str, Any],
        options: list[str],
        category_name: str = "",
    ) -> str:
        text = " ".join(
            str(value or "")
            for value in (
                payload.get("title"),
                payload.get("short_title"),
                (payload.get("desc_info") or {}).get("desc"),
                category_name,
            )
        )
        synonyms = (
            ("聚酯纤维", ("聚酯纤维",)),
            ("聚酯", ("聚酯纤维",)),
            ("锦纶", ("尼龙", "聚酰胺纤维")),
            ("聚酰胺", ("尼龙", "聚酰胺纤维")),
            ("尼龙", ("尼龙",)),
            ("涤纶", ("涤纶",)),
            ("冰丝", ("冰丝",)),
            ("氨纶", ("氨纶",)),
            ("腈纶", ("腈纶",)),
            ("莱赛尔", ("莱赛尔纤维",)),
            ("粘胶", ("粘胶纤维",)),
            ("真丝", ("真丝", "桑蚕丝")),
            ("羊毛", ("羊毛",)),
            ("羊绒", ("羊绒",)),
            ("棉", ("棉",)),
            ("塑料", ("塑料",)),
        )
        for keyword, preferred in synonyms:
            if keyword not in text:
                continue
            for option in options:
                if any(value in option for value in preferred):
                    return option
            return preferred[0]
        fallbacks = (
            "其他",
            "涤纶",
            "聚酯纤维",
            "尼龙",
            "棉",
        )
        for preferred in fallbacks:
            match = next(
                (option for option in options if preferred in option),
                "",
            )
            if match:
                return match
        return options[0] if options else ""

    @classmethod
    def _required_attribute_value(
        cls,
        attribute: dict[str, Any],
        payload: dict[str, Any],
        category_name: str = "",
    ) -> str:
        name = str(attribute.get("name") or "").strip()
        attribute_type = str(attribute.get("type_v2") or attribute.get("type") or "string")
        options = cls._attribute_options(attribute)
        text = " ".join(
            str(value or "")
            for value in (
                payload.get("title"),
                payload.get("short_title"),
                (payload.get("desc_info") or {}).get("desc"),
                category_name,
            )
        )
        material = cls._infer_material(
            payload,
            options,
            category_name,
        )
        if "unit" in attribute_type:
            units = cls._attribute_options(attribute)
            unit = units[0] if units else ""
            return cls._normalize_attribute_value(
                attribute,
                cls._measure_value(payload, name, unit),
                payload,
                category_name,
            )
        if "成分" in name or "含量" in name:
            if material:
                return f"100%{material}"
            return "100%其他"
        if any(word in name for word in ("材质", "面料")):
            return material or (options[0] if options else "其他")
        exact_option = next(
            (
                option
                for option in sorted(options, key=len, reverse=True)
                if len(option) > 1 and option in text
            ),
            "",
        )
        if exact_option:
            return exact_option
        if "适用对象" in name:
            for preferred in ("成年人", "老少皆宜", "成人"):
                match = next(
                    (option for option in options if preferred in option),
                    "",
                )
                if match:
                    return match
        if "尺码" in name and attribute_type == "string":
            return "均码"
        if "尺码" in name and options:
            match = next(
                (option for option in options if option == "均码"),
                "",
            )
            if match:
                return match
        if "产地" in name:
            match = next(
                (option for option in options if "中国大陆" in option),
                "",
            )
            if match:
                return match
            return "中国大陆"
        if "保质期" in name:
            return "12个月"
        if any(word in name for word in ("颜色", "色系")):
            return "多色"
        if any(word in name for word in ("风格", "款式")):
            return "简约"
        if any(word in name for word in ("型号", "编号", "货号")):
            return str(payload.get("out_product_id") or payload.get("spu_code") or "无")
        if options:
            for preferred in ("其他", "无", "常规", "均码"):
                match = next(
                    (option for option in options if option == preferred),
                    "",
                )
                if match:
                    return match
            return options[0]
        if attribute_type == "boolean":
            return "否"
        if attribute_type == "date":
            return datetime.now().date().isoformat()
        if attribute_type == "datetime":
            return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if attribute_type.startswith("integer"):
            return "1"
        if attribute_type.startswith("decimal"):
            return "1"
        return str(attribute.get("hint") or "无")

    @classmethod
    def _normalize_attribute_value(
        cls,
        attribute: dict[str, Any],
        value: Any,
        payload: dict[str, Any],
        category_name: str = "",
    ) -> str:
        """Normalize numeric category attributes to the official wire format."""
        attribute_type = str(attribute.get("type_v2") or attribute.get("type") or "string")
        raw = str(value or "").strip()
        name = str(attribute.get("name") or "").strip()
        if "unit" in attribute_type:
            units = cls._attribute_options(attribute)
            lowered = raw.casefold()
            unit = next(
                (candidate for candidate in units if candidate and candidate.casefold() in lowered),
                units[0] if units else "",
            )
            if not unit:
                return raw
            if not raw:
                raw = cls._measure_value(payload, name, unit)
            number = re.search(r"-?\d+(?:\.\d+)?", raw)
            if number is None:
                generated = cls._measure_value(payload, name, unit)
                number = re.search(
                    r"-?\d+(?:\.\d+)?",
                    generated,
                )
            numeric = number.group(0) if number else "1"
            try:
                if float(numeric) <= 0:
                    numeric = "1"
                if attribute_type.startswith("integer"):
                    numeric = str(int(float(numeric)))
            except ValueError:
                numeric = "1"
            return f"{numeric} {unit}".strip()
        if attribute_type.startswith("integer") or attribute_type.startswith("decimal"):
            number = re.search(r"-?\d+(?:\.\d+)?", raw)
            numeric = number.group(0) if number else "1"
            try:
                if float(numeric) <= 0:
                    numeric = "1"
                if attribute_type.startswith("integer"):
                    numeric = str(int(float(numeric)))
            except ValueError:
                numeric = "1"
            return numeric
        return raw

    @staticmethod
    def _measure_value(
        payload: dict[str, Any],
        name: str,
        unit: str,
    ) -> str:
        text = " ".join(
            str(value or "")
            for value in (
                payload.get("title"),
                payload.get("short_title"),
                (payload.get("desc_info") or {}).get("desc"),
            )
        )
        aliases: dict[str, tuple[str, ...]] = {
            "%": ("%", "％"),
            "cm": ("cm", "厘米"),
            "mm": ("mm", "毫米"),
            "m": ("m", "米"),
            "kg": ("kg", "千克", "公斤"),
            "g": ("g", "克"),
            "l": ("l", "L", "升"),
            "ml": ("ml", "mL", "毫升"),
            "w": ("w", "W", "瓦"),
            "v": ("v", "V", "伏"),
        }
        lookup = aliases.get(unit.lower(), (unit,))
        for alias in lookup:
            match = re.search(
                rf"(\d+(?:\.\d+)?)\s*{re.escape(alias)}",
                text,
            )
            if match:
                number = match.group(1)
                return f"{number} {unit}".strip()
        defaults = {
            "%": "100",
            "cm": "1",
            "mm": "1",
            "m": "1",
            "kg": "1",
            "g": "1",
            "l": "1",
            "ml": "1",
            "w": "1",
            "v": "1",
        }
        return f"{defaults.get(unit.lower(), '1')} {unit}".strip()

    @staticmethod
    def _attribute_key_token(value: Any) -> str:
        normalized = unicodedata.normalize(
            "NFKC",
            str(value or ""),
        ).casefold()
        return re.sub(
            r"[\s\-_—:：/\\|（）()\[\]【】.,，。·]+",
            "",
            normalized,
        )

    @classmethod
    def _is_external_marketing_attribute(cls, key: Any) -> bool:
        token = cls._attribute_key_token(key)
        if not token:
            return False
        if token in EXTERNAL_MARKETING_ATTRIBUTE_TOKENS:
            return True
        if "外贸" in token or "跨境出口" in token:
            return True
        if (
            ("下游" in token or "上下游" in token)
            and ("平台" in token or "品牌" in token)
        ):
            return True
        if "第三方" in token and "平台" in token:
            return True
        return any(
            marker in token
            for marker in (
                "销售地区",
                "销售区域",
                "销售市场",
                "销售国家",
            )
        )

    @classmethod
    def _filter_external_marketing_attributes(
        cls,
        attrs: list[dict[str, Any]],
        *,
        required_keys: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        required = required_keys or set()
        return [
            item
            for item in attrs
            if (
                cls._attribute_key_token(item.get("attr_key"))
                in required
                or not cls._is_external_marketing_attribute(
                    item.get("attr_key")
                )
            )
        ]

    @classmethod
    def _sanitize_attributes_without_rule(
        cls,
        attrs: Any,
        brand_id: str,
    ) -> list[dict[str, Any]]:
        output: list[dict[str, Any]] = []
        existing: dict[str, dict[str, Any]] = {}
        brand_token = cls._attribute_key_token("品牌")
        for item in attrs or []:
            if not isinstance(item, dict):
                continue
            key = str(item.get("attr_key") or "").strip()
            token = cls._attribute_key_token(key)
            if not token:
                continue
            if brand_id and token == brand_token:
                continue
            if cls._is_external_marketing_attribute(key):
                continue
            current = existing.get(token)
            if current is not None:
                current["attr_value"] = item.get("attr_value")
                continue
            normalized = {
                "attr_key": key,
                "attr_value": item.get("attr_value"),
            }
            output.append(normalized)
            existing[token] = normalized
        return output

    def _apply_required_category_attributes(
        self,
        store: Store,
        client: WechatShopClient,
        payload: dict[str, Any],
    ) -> None:
        brand_id = str(
            payload.get("brand_id") or "2100000000"
        ).strip()
        payload["brand_id"] = brand_id
        category_id = self._category_leaf_id(payload)
        if not category_id:
            payload["attrs"] = self._sanitize_attributes_without_rule(
                payload.get("attrs"),
                brand_id,
            )
            return
        cache_key = (store.id, category_id, brand_id)
        rule = self._category_rule_cache.get(cache_key)
        if rule is None:
            fetch_rule = getattr(
                client,
                "fetch_category_product_rule",
                None,
            )
            if not callable(fetch_rule):
                payload["attrs"] = self._sanitize_attributes_without_rule(
                    payload.get("attrs"),
                    brand_id,
                )
                return
            try:
                rule = fetch_rule(category_id, brand_id=brand_id)
            except Exception:
                payload["attrs"] = self._sanitize_attributes_without_rule(
                    payload.get("attrs"),
                    brand_id,
                )
                return
            self._category_rule_cache[cache_key] = rule
        attrs = payload.get("attrs")
        attrs = list(attrs) if isinstance(attrs, list) else []
        normalized_attrs: list[dict[str, Any]] = []
        existing: dict[str, dict[str, Any]] = {}
        brand_token = self._attribute_key_token("品牌")
        for item in attrs:
            if not isinstance(item, dict):
                continue
            key = str(item.get("attr_key") or "").strip()
            if not key:
                continue
            key_token = self._attribute_key_token(key)
            if not key_token:
                continue
            # brand_id is the official brand field. Keeping a second "品牌"
            # attribute makes WeChat reject product/add with 6600044.
            if key_token == brand_token and brand_id:
                continue
            normalized = {
                "attr_key": key,
                "attr_value": item.get("attr_value"),
            }
            current = existing.get(key_token)
            if current is not None:
                current["attr_value"] = normalized["attr_value"]
                continue
            normalized_attrs.append(normalized)
            existing[key_token] = normalized
        attrs = normalized_attrs
        category_name = str((rule.get("info") or {}).get("name") or "")
        allowed_attributes: dict[str, dict[str, Any]] = {}
        required_attribute_keys: set[str] = set()
        for attribute in rule.get("product_attr_list") or []:
            if not isinstance(attribute, dict):
                continue
            name = str(attribute.get("name") or "").strip()
            required_rule = attribute.get("required_rule") or {}
            is_required = bool(
                attribute.get("is_required")
                or (
                    isinstance(required_rule, dict)
                    and int(required_rule.get("rule_type") or 0) == 1
                )
            )
            if not name:
                continue
            name_token = self._attribute_key_token(name)
            if not name_token:
                continue
            allowed_attributes[name_token] = attribute
            if is_required:
                required_attribute_keys.add(name_token)
            if name_token == brand_token and brand_id:
                continue
            current = existing.get(name_token)
            if current is not None:
                current["attr_key"] = name
                normalized_value = self._normalize_attribute_value(
                    attribute,
                    current.get("attr_value"),
                    payload,
                    category_name,
                )
                if is_required and not normalized_value:
                    normalized_value = self._required_attribute_value(
                        attribute,
                        payload,
                        category_name,
                    )
                if is_required and not normalized_value:
                    raise UploadPreflightError(
                        f"{store.name} 指定类目必填属性无法自动补全："
                        f"{name}"
                    )
                current["attr_value"] = normalized_value
                continue
            if not is_required:
                continue
            value = self._required_attribute_value(
                attribute,
                payload,
                category_name,
            )
            if not value:
                raise UploadPreflightError(
                    f"{store.name} 指定类目必填属性无法自动补全："
                    f"{name}"
                )
            normalized = {"attr_key": name, "attr_value": value}
            attrs.append(normalized)
            existing[name_token] = normalized
        if allowed_attributes:
            attrs = [
                item
                for item in attrs
                if self._attribute_key_token(item.get("attr_key"))
                in allowed_attributes
            ]
            existing = {
                self._attribute_key_token(item.get("attr_key")): item
                for item in attrs
            }
        attrs = self._filter_external_marketing_attributes(
            attrs,
            required_keys=required_attribute_keys,
        )
        payload["attrs"] = attrs

    def _prepare_product_payload(
        self,
        store: Store,
        client: WechatShopClient,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        prepared = normalize_sku_sale_attrs(payload)
        defaults = self._product_payload_defaults(store, client)
        if not self._valid_express_info(prepared.get("express_info")):
            prepared["express_info"] = deepcopy(defaults.get("express_info") or {})
        if not self._valid_after_sale_info(prepared.get("after_sale_info")):
            prepared["after_sale_info"] = deepcopy(defaults.get("after_sale_info") or {})
        for key in (
            "deliver_acct_type",
            "limited_info",
            "after_sale_info",
            "size_chart",
            "hide_in_window",
            "release_mode",
            "head_videos",
        ):
            if key not in prepared and key in defaults:
                prepared[key] = deepcopy(defaults[key])
        if prepared.get("deliver_method") is None:
            prepared["deliver_method"] = defaults.get("deliver_method", 0)
        if prepared.get("deliver_acct_type") is None:
            prepared["deliver_acct_type"] = deepcopy(defaults.get("deliver_acct_type") or [])
        if prepared.get("listing") is None:
            prepared["listing"] = 1
        if not self._valid_express_info(prepared.get("express_info")):
            raise ValueError(f"{store.name}未获取到运费模板ID，请先在微信小店后台配置默认运费模板")
        if not self._valid_after_sale_info(prepared.get("after_sale_info")):
            raise ValueError(f"{store.name}未获取到售后地址ID，请先在微信小店后台配置默认收货地址")
        self._validate_store_delivery_config(
            store,
            client,
            prepared,
        )
        self._apply_required_category_attributes(
            store,
            client,
            prepared,
        )
        if prepared.get("_specified_category_id"):
            self._validate_specified_product_category(
                store,
                client,
                prepared,
            )

        image_cache: dict[str, str] = {}

        def prepare_url(value: Any) -> str:
            url = str(value or "").strip()
            if not url:
                return ""
            if self._uses_hosted_product_image(url):
                return url
            if url not in image_cache:
                image_cache[url] = client.upload_product_image_url(url)
            return image_cache[url]

        head_images = [
            upload
            for upload in (prepare_url(url) for url in prepared.get("head_imgs") or [])
            if upload
        ]
        fallback_heads = [
            str(url)
            for url in defaults.get("head_imgs") or []
            if self._uses_hosted_product_image(str(url))
        ]
        for url in fallback_heads:
            if url not in head_images:
                head_images.append(url)
            if len(head_images) >= 3:
                break
        prepared["head_imgs"] = list(dict.fromkeys(head_images))[:9]
        if len(prepared["head_imgs"]) < 3:
            raise ValueError(f"{store.name}商品主图少于3张：当前仅 {len(prepared['head_imgs'])} 张")

        desc_info = dict(prepared.get("desc_info") or {})
        detail_images = [
            upload for upload in (prepare_url(url) for url in desc_info.get("imgs") or []) if upload
        ]
        if not detail_images:
            fallback_details = [
                str(url)
                for url in (defaults.get("desc_info") or {}).get("imgs") or []
                if self._uses_hosted_product_image(str(url))
            ]
            detail_images = fallback_details[:1]
        if not detail_images and prepared["head_imgs"]:
            detail_images = prepared["head_imgs"][:1]
        desc_info["imgs"] = list(dict.fromkeys(detail_images))[:50]
        prepared["desc_info"] = desc_info
        for key in (
            "_template_image_processing",
            "_source_image_counts",
            "_specified_category_id",
            "_specified_category_path",
            "_specified_category_version",
            "_brand_mode",
            "_deposit_limit_cents",
        ):
            prepared.pop(key, None)
        return prepared

    def _ensure_product_category(
        self,
        store: Store,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        prepared = deepcopy(payload)
        if any(
            prepared.get(key)
            for key in ("cats", "cats_v2", "category_path")
        ):
            return prepared
        catalog = self._category_catalog_cache.get(store.id)
        private: ShopPrivateClient | Any | None = None
        if not catalog:
            private = ShopPrivateClient(self.db, store)
            catalog = private.category_catalog()
            self._category_catalog_cache[store.id] = catalog
        source_text = " ".join(
            str(prepared.get(key) or "")
            for key in ("category_hint", "category_name", "title")
        )
        ranked = rank_category_candidates(
            source_text,
            catalog,
            limit=25,
        )
        if not ranked or float(ranked[0].get("match_score") or 0) < 2:
            raise ValueError(
                f"{store.name} 未匹配到可信的微信小店商品类目；"
                "请检查商品标题或配置模板兜底类目"
            )
        candidates = [
            item
            for item in ranked
            if float(item.get("match_score") or 0) >= 2
        ]
        best = candidates[0]
        checker = (
            getattr(private, "category_application_checks", None)
            if private is not None
            else None
        )
        if callable(checker):
            top_candidates = candidates[:12]
            checks = checker(
                [
                    str(item.get("category_id") or "")
                    for item in top_candidates
                    if str(item.get("category_id") or "")
                ]
            )
            compliant: list[dict[str, Any]] = []
            recommended: list[dict[str, Any]] = []
            for item in top_candidates:
                category_id = str(
                    item.get("category_id") or ""
                )
                check = checks.get(category_id)
                if not isinstance(check, dict):
                    continue
                candidate = {
                    **item,
                    "application_state": str(
                        check.get("state") or "unknown"
                    ),
                    "application_label": str(
                        check.get("label") or ""
                    ),
                    "application_reason": str(
                        check.get("reason") or ""
                    ),
                    "deposit_amount": check.get("deposit_amount"),
                    "required_deposit": check.get(
                        "required_deposit"
                    ),
                    "amount_to_pay": check.get("amount_to_pay"),
                    "operation_required_deposit": check.get(
                        "operation_required_deposit"
                    ),
                    "deposit_balance": check.get(
                        "deposit_balance"
                    ),
                    "real_deposit_balance": check.get(
                        "real_deposit_balance"
                    ),
                    "deposit_shortfall": check.get(
                        "deposit_shortfall"
                    ),
                    "deposit_free_trial": check.get(
                        "deposit_free_trial"
                    ),
                    "business_type": check.get("business_type"),
                }
                recommended.append(candidate)
                state = str(check.get("state") or "")
                if state in {
                    "effective",
                    "not_applied",
                    "pending",
                }:
                    compliant.append(candidate)
            if compliant:
                # Semantic confidence remains the primary key. Deposit is
                # only used to choose among candidates with comparable,
                # compliant category meaning.
                top_score = float(
                    compliant[0].get("match_score") or 0
                )
                best = max(
                    (
                        item
                        for item in compliant
                        if float(item.get("match_score") or 0)
                        >= max(2.0, top_score * 0.85)
                    ),
                    key=lambda item: (
                        float(item.get("match_score") or 0),
                        -int(item.get("deposit_amount") or 0),
                    ),
                )
            elif recommended:
                best = recommended[0]
            prepared["_category_candidates"] = recommended[:8]
            prepared["_category_selected"] = {
                "category_id": str(best.get("category_id") or ""),
                "path": str(best.get("path") or ""),
                "match_score": float(
                    best.get("match_score") or 0
                ),
                "application_state": str(
                    best.get("application_state") or "unknown"
                ),
                "deposit_amount": best.get("deposit_amount"),
                "required_deposit": best.get(
                    "required_deposit"
                ),
                "amount_to_pay": best.get("amount_to_pay"),
                "operation_required_deposit": best.get(
                    "operation_required_deposit"
                ),
                "deposit_balance": best.get("deposit_balance"),
                "real_deposit_balance": best.get(
                    "real_deposit_balance"
                ),
                "deposit_shortfall": best.get(
                    "deposit_shortfall"
                ),
                "deposit_free_trial": best.get(
                    "deposit_free_trial"
                ),
                "business_type": best.get("business_type"),
            }
        path_ids = [
            str(value)
            for value in best.get("id_path") or []
            if str(value)
        ]
        if not path_ids:
            path_ids = [str(best.get("category_id") or "")]
        prepared["cats_v2"] = [
            {"cat_id": category_id}
            for category_id in path_ids
            if category_id
        ]
        prepared.pop("cats", None)
        prepared["category_path"] = str(best.get("path") or "")
        prepared["category_apply_ids"] = [
            str(best.get("category_id") or "")
        ]
        prepared["_auto_category_required"] = False
        return prepared

    def _remote_recycle_presence(
        self,
        store: Store,
        product_id: str,
        official_client: WechatShopClient | MockShopClient | None,
    ) -> tuple[bool | None, str]:
        errors: list[str] = []
        try:
            private = ShopPrivateClient(self.db, store)
            products = private.scan_products(
                "recycle",
                max_pages=None,
                page_size=50,
            )
            return (
                str(product_id)
                in {
                    str(product.product_id)
                    for product in products
                    if product.product_id
                },
                "微信小店回收站完整列表",
            )
        except Exception as exc:
            errors.append(f"网页回收站：{exc}")
        if (
            official_client is not None
            and not isinstance(official_client, MockShopClient)
        ):
            fetch_ids = getattr(
                official_client,
                "fetch_product_ids",
                None,
            )
            if callable(fetch_ids):
                try:
                    return (
                        str(product_id) in {
                            str(value)
                            for value in fetch_ids(6)
                        },
                        "微信商品接口 status=6",
                    )
                except Exception as exc:
                    errors.append(f"官方回收站：{exc}")
        return None, "；".join(errors)

    def _delete_product_remote(
        self,
        store: Store,
        product_id: str,
        official_client: WechatShopClient | MockShopClient | None,
    ) -> dict[str, Any]:
        row = self.db.get_product(store.id, product_id)
        current_status = int(row["status"]) if row else -1
        delete_mode = "real" if current_status == 6 else "recycle"
        if (
            store.app_id == "demo-store"
            or isinstance(official_client, MockShopClient)
        ):
            self.db.remove_product_cache(store.id, product_id)
            return {
                "errcode": 0,
                "demo": True,
                "delete_mode": delete_mode,
                "remote_verified": True,
            }

        result: dict[str, Any]
        if delete_mode == "real":
            try:
                result = self.private_batch_product_action(
                    store,
                    [product_id],
                    "real",
                )
            except Exception as exc:
                raise ProductDeleteUncertainError(
                    "彻底删除请求结果未确认：" + str(exc)
                ) from exc
        else:
            client = official_client or (
                self.client_for(store)
                if self.db.get_store_secret(store.id)[1]
                else None
            )
            if client is not None and not isinstance(client, MockShopClient):
                try:
                    result = client.delete_product(product_id)
                except Exception as exc:
                    raise ProductDeleteUncertainError(
                        "移入回收站请求结果未确认：" + str(exc)
                    ) from exc
            else:
                try:
                    result = self.private_batch_product_action(
                        store,
                        [product_id],
                        "recycle",
                    )
                except Exception as exc:
                    raise ProductDeleteUncertainError(
                        "移入回收站请求结果未确认：" + str(exc)
                    ) from exc
        if int(result.get("errcode") or 0) != 0:
            raise RuntimeError(
                str(result.get("errmsg") or "微信商品删除接口返回失败")
            )

        still_in_recycle, source = self._remote_recycle_presence(
            store,
            product_id,
            official_client,
        )
        if still_in_recycle is None:
            raise ProductDeleteUncertainError(
                "删除请求已返回，但无法重新读取平台回收站确认结果："
                + source
            )
        if delete_mode == "real" and still_in_recycle:
            raise RuntimeError(
                f"平台回收站仍存在商品 {product_id}，彻底删除未生效"
            )
        if delete_mode == "recycle" and not still_in_recycle:
            raise RuntimeError(
                f"平台回收站未找到商品 {product_id}，移入回收站未确认成功"
            )
        self.db.remove_product_cache(store.id, product_id)
        result = dict(result)
        result.update(
            {
                "delete_mode": delete_mode,
                "remote_verified": True,
                "verification_source": source,
            }
        )
        return result

    def product_write(
        self,
        store: Store,
        action: str,
        payload: dict[str, Any] | None = None,
        product_id: str = "",
        client: WechatShopClient | MockShopClient | None = None,
        listing_mode: str = "",
    ) -> dict[str, Any]:
        self._require_platform_write(store, "product_write")
        if action == "delete":
            return self._delete_product_remote(
                store,
                product_id,
                client,
            )
        if action in {"add", "update"}:
            payload_data = payload or {}
            title_records = load_upload_violation_title_records(self.db)
            for title in (
                payload_data.get("title"),
                (
                    payload_data.get("product")
                    if isinstance(payload_data.get("product"), dict)
                    else {}
                ).get("title"),
            ):
                violation_issue = mandatory_upload_violation_issue(
                    title,
                    {},
                    title_records,
                )
                if violation_issue:
                    raise ValueError(
                        "商品标题命中售假违规库，禁止提交微信平台："
                        f"{str(title or '')[:120]}；{violation_issue}"
                    )
        client = client or self.client_for(store)
        if isinstance(client, MockShopClient):
            return {"errcode": 0, "demo": True}
        if action == "add":
            payload = self._ensure_product_category(
                store,
                payload or {},
            )
            prepared = self._prepare_product_payload(
                store,
                client,
                payload,
            )
            if listing_mode == "draft":
                prepared["listing"] = 0
            elif listing_mode:
                prepared["listing"] = 1
            result = client.add_product(prepared)
        elif action == "update":
            result = client.update_product(payload or {})
        elif action == "cancel_audit":
            result = client.cancel_product_audit(product_id)
        else:
            raise ValueError("不支持的商品操作")
        if int(result.get("errcode") or 0) != 0:
            raise RuntimeError(str(result.get("errmsg") or "微信商品接口返回失败"))
        if action == "add" and int(result.get("errcode") or 0) == 0:
            self.db.adjust_upload_metrics(
                store.id,
                product_delta=1,
                quota_delta=(0 if listing_mode == "draft" else -1),
            )
        if action in {"add", "update"}:
            result_data = result.get("data")
            result_data = result_data if isinstance(result_data, dict) else {}
            result_product_id = str(
                result.get("product_id")
                or result_data.get("product_id")
                or (payload or {}).get("product_id")
                or ""
            )
            if result_product_id and not result.get("product_id"):
                result["product_id"] = result_product_id
            if result_product_id:
                if action == "add" and listing_mode == "warehouse":
                    try:
                        result["warehouse_result"] = client.set_product_listing(
                            result_product_id,
                            False,
                        )
                        self.db.mark_product_status(
                            store.id,
                            result_product_id,
                            11,
                            PRODUCT_STATUS[11],
                        )
                    except Exception as exc:
                        result["warehouse_error"] = str(exc)
                try:
                    product = client.fetch_product(store, result_product_id)
                    self.db.upsert_product(product)
                    self.db.mark_product_catalog_done(store.id, result_product_id)
                except Exception:
                    pass
        return result

    def upload_product_images(
        self,
        store: Store,
        paths: list[str],
    ) -> list[str]:
        self._require_platform_write(store, "product_image_upload")
        client = self.client_for(store)
        if isinstance(client, MockShopClient):
            return [f"https://example.invalid/demo-{index}.png" for index, _ in enumerate(paths, 1)]
        return [client.upload_product_image(path) for path in paths]

    def sync_recent_order_updates(
        self,
        store: Store,
        start_time: int,
        end_time: int,
    ) -> SyncResult:
        client = self.client_for(store)
        if isinstance(client, MockShopClient):
            return SyncResult("order_updates", 0, "演示店铺无订单更新")
        ids = client.fetch_order_ids_by_update(start_time, end_time)
        failures = []
        for order_id in ids:
            try:
                self.db.upsert_order(client.fetch_order(store, order_id))
            except Exception as exc:
                failures.append(f"{order_id}: {exc}")
        return SyncResult(
            "order_updates",
            len(ids),
            f"订单增量：{len(ids) - len(failures)} 条更新",
            success=len(ids) - len(failures),
            failed=len(failures),
            errors=failures[:20],
        )

    def sync_order(
        self,
        store: Store,
        order_id: str,
    ) -> SyncResult:
        client = self.client_for(store)
        if isinstance(client, MockShopClient):
            return SyncResult(
                "order",
                0,
                "演示店铺不刷新单个订单",
                success=0,
            )
        order = client.fetch_order(store, str(order_id))
        self.db.upsert_order(order)
        self.db.sync_auto_dispatch_tasks()
        return SyncResult(
            "order",
            1,
            f"订单 {order_id} 已刷新",
            success=1,
        )

    def sync_products(
        self,
        store: Store,
        progress: ProgressCallback | None = None,
        max_items: int | None = None,
        workers: int = 4,
        refresh_catalog: bool = False,
        cancel_event: threading.Event | None = None,
    ) -> SyncResult:
        scan_state = self.db.get_product_scan_state(store.id)
        if refresh_catalog or not scan_state or scan_state["status"] != "completed":
            catalog_result = self.scan_product_catalog(
                store,
                progress,
                cancel_event=cancel_event,
                reset=refresh_catalog,
            )
            if catalog_result.cancelled:
                return catalog_result
            if store.app_id == "demo-store":
                return catalog_result
        return self.sync_product_details(
            store,
            progress,
            max_items=max_items,
            workers=workers,
            cancel_event=cancel_event,
        )

    def scan_product_catalog(
        self,
        store: Store,
        progress: ProgressCallback | None = None,
        cancel_event: threading.Event | None = None,
        reset: bool = False,
    ) -> SyncResult:
        run_id = self.db.start_sync_run(store.id, "product_catalog")
        state = self.db.begin_product_scan(
            store.id,
            uuid.uuid4().hex,
            reset=reset,
        )
        scan_id = str(state["scan_id"])
        next_key = str(state["next_key"] or "")
        discovered = int(state["discovered"] or 0)
        try:
            client = self.client_for(store)
            if isinstance(client, MockShopClient):
                products = client.fetch_products(progress)
                product_ids = [product.product_id for product in products]
                discovered = self.db.save_product_scan_page(
                    store.id,
                    scan_id,
                    product_ids,
                    "",
                )
                for product in products:
                    self.db.upsert_product(product)
                    self.db.mark_product_catalog_done(store.id, product.product_id)
            else:
                page_number = 0
                while True:
                    if cancel_event and cancel_event.is_set():
                        raise _TaskCancelled
                    page_number += 1
                    page = client.fetch_product_id_page(next_key)
                    discovered = self.db.save_product_scan_page(
                        store.id,
                        scan_id,
                        page.ids,
                        page.next_key,
                    )
                    if progress:
                        progress(
                            f"正在刷新商品目录：第 {page_number} 页，已发现 {discovered} 个商品",
                            0,
                            0,
                        )
                    next_key = page.next_key
                    if not next_key:
                        break
            self.db.complete_product_scan(store.id, scan_id)
            self.db.touch_store_sync(store.id)
            message = f"商品目录刷新完成，共发现 {discovered} 个商品"
            self.db.finish_sync_run(run_id, "success", discovered, message)
            return SyncResult(
                "product_catalog",
                discovered,
                message,
                success=discovered,
            )
        except _TaskCancelled:
            message = f"商品目录刷新已暂停，已保存 {discovered} 个商品，可继续"
            self.db.finish_sync_run(run_id, "cancelled", discovered, message)
            return SyncResult(
                "product_catalog",
                discovered,
                message,
                success=discovered,
                cancelled=True,
            )
        except Exception as exc:
            self.db.finish_sync_run(run_id, "failed", 0, str(exc))
            raise

    def sync_product_details(
        self,
        store: Store,
        progress: ProgressCallback | None = None,
        max_items: int | None = None,
        workers: int = 4,
        cancel_event: threading.Event | None = None,
        include_failed: bool = True,
    ) -> SyncResult:
        run_id = self.db.start_sync_run(store.id, "product_details")
        product_ids = self.db.list_pending_product_ids(
            store.id,
            limit=max_items,
            include_failed=include_failed,
        )
        total = len(product_ids)
        if not product_ids:
            message = "没有待同步的商品详情"
            self.db.finish_sync_run(run_id, "success", 0, message)
            return SyncResult("product_details", 0, message)

        client = self.client_for(store)
        completed_count = 0
        success_count = 0
        failures: list[str] = []
        cancelled = False

        def fetch_one(product_id: str) -> tuple[str, str | None]:
            if cancel_event and cancel_event.is_set():
                return product_id, "cancelled"
            try:
                product = client.fetch_product(store, product_id)
                self.db.upsert_product(product)
                self.db.mark_product_catalog_done(store.id, product_id)
                return product_id, None
            except Exception as exc:
                self.db.mark_product_catalog_failed(store.id, product_id, str(exc))
                return product_id, str(exc)

        safe_workers = max(1, min(int(workers), 8))
        executor = ThreadPoolExecutor(
            max_workers=safe_workers,
            thread_name_prefix="product-sync",
        )
        futures = {executor.submit(fetch_one, product_id): product_id for product_id in product_ids}
        try:
            for future in as_completed(futures):
                if cancel_event and cancel_event.is_set():
                    cancelled = True
                    for pending in futures:
                        pending.cancel()
                if future.cancelled():
                    continue
                try:
                    product_id, error = future.result()
                except CancelledError:
                    continue
                completed_count += 1
                if error is None:
                    success_count += 1
                elif error != "cancelled":
                    failures.append(f"{product_id}: {error}")
                if progress:
                    progress(
                        f"正在同步商品详情 {completed_count}/{total}",
                        completed_count,
                        total,
                    )
        finally:
            executor.shutdown(wait=True, cancel_futures=True)

        cancelled_count = sum(1 for future in futures if future.cancelled())
        failed_count = max(0, total - success_count - cancelled_count)
        if cancelled:
            message = f"商品详情同步已暂停：成功 {success_count}，失败 {failed_count}，可继续"
            self.db.finish_sync_run(run_id, "cancelled", success_count, message)
            return SyncResult(
                "product_details",
                total,
                message,
                success=success_count,
                failed=failed_count,
                cancelled=True,
                errors=failures[:20],
            )

        message = f"商品详情同步完成：成功 {success_count}，失败 {len(failures)}"
        status = "success" if not failures else "partial"
        self.db.finish_sync_run(run_id, status, success_count, message)
        self.db.touch_store_sync(store.id)
        return SyncResult(
            "product_details",
            total,
            message,
            success=success_count,
            failed=len(failures),
            errors=failures[:20],
        )

    def refresh_product_status_snapshot(
        self,
        store: Store,
        progress: ProgressCallback | None = None,
        cancel_event: threading.Event | None = None,
    ) -> SyncResult:
        try:
            private = ShopPrivateClient(self.db, store)
            if progress:
                progress("正在读取微信商家后台商品状态", 0, 1)
            snapshot = private.status_counts()
        except ShopPrivateError:
            client = self.client_for(store)
            if isinstance(client, MockShopClient):
                products = client.fetch_products(progress)
                snapshot = {
                    "sales": sum(product.status == 5 for product in products),
                    "offline": sum(
                        product.status in {11, 12, 13, 14, 15, 20} for product in products
                    ),
                    "recycle": sum(product.status == 6 for product in products),
                }
            else:
                snapshot = {}
                groups = (
                    ("sales", (5,)),
                    ("offline", (11, 12, 13, 14, 15, 20)),
                    ("recycle", (6,)),
                )
                for group_index, (tab_key, statuses) in enumerate(groups, 1):
                    if cancel_event and cancel_event.is_set():
                        raise _TaskCancelled
                    if progress:
                        progress(
                            f"正在读取官方商品状态：{tab_key}",
                            group_index - 1,
                            len(groups),
                        )
                    snapshot[tab_key] = sum(
                        len(client.fetch_product_ids(status)) for status in statuses
                    )
        self.db.upsert_product_status_snapshot(store.id, snapshot)
        try:
            quota = self.fetch_product_audit_quota(store)
            self.db.set_setting(
                f"product_quota_{store.id}",
                json.dumps(quota, ensure_ascii=False),
            )
        except Exception:
            pass
        return SyncResult(
            "product_status_snapshot",
            sum(snapshot.values()),
            "服务端商品状态数量已刷新",
            success=sum(snapshot.values()),
        )

    def refresh_store_metrics(self, store: Store) -> dict[str, Any]:
        now = self.now_provider()
        try:
            existing_metrics = json.loads(
                self.db.get_setting(f"store_metrics_{store.id}", "{}")
            )
        except (TypeError, ValueError, json.JSONDecodeError):
            existing_metrics = {}
        if not isinstance(existing_metrics, dict):
            existing_metrics = {}
        cached_store_id = existing_metrics.get("_cache_store_id")
        cached_app_id = existing_metrics.get("_cache_store_app_id")
        cache_owner_mismatch = False
        if cached_store_id is not None:
            try:
                cache_owner_mismatch = int(cached_store_id) != int(store.id)
            except (TypeError, ValueError):
                cache_owner_mismatch = True
        if (
            cached_app_id is not None
            and str(cached_app_id) != str(store.app_id)
        ):
            cache_owner_mismatch = True
        if cache_owner_mismatch:
            existing_metrics = {}
        today = now.date()
        today_start = int(
            now.replace(
                hour=0,
                minute=0,
                second=0,
                microsecond=0,
            ).timestamp()
        )
        month_start = int(
            (now - timedelta(days=30))
            .replace(hour=0, minute=0, second=0, microsecond=0)
            .timestamp()
        )
        quota_error = ""
        errors: list[str] = []
        credential_checked = False
        credential_probe_ok = False
        credential_error_code = ""
        credential_error_message = ""
        after_sale_incremental = {
            "status": "skipped",
            "updated_at": "",
            "error": "",
        }
        try:
            _current_store, current_secret = self.db.get_store_secret(store.id)
            if current_secret:
                credential_checked = True
                self.validate_store_credential(store, current_secret)
                credential_probe_ok = True
        except WechatApiError as exc:
            if exc.category == "authentication":
                credential_error_code = str(exc.code or "authentication")
                credential_error_message = str(exc)[:500]
                errors.append(
                    f"凭据验证失败（{credential_error_code}）："
                    f"{credential_error_message}"
                )
        except Exception as exc:
            errors.append(f"凭据验证：{exc}")
        if credential_probe_ok and str(store.name).strip() == str(store.app_id).strip():
            try:
                self.sync_store_profile(store)
            except Exception as exc:
                errors.append(f"店铺名称获取：{exc}")
        try:
            self._ensure_today_orders_cached(
                store,
                today_start,
                int(now.timestamp()),
            )
        except Exception as exc:
            errors.append(f"今日订单同步：{exc}")
        if credential_probe_ok:
            after_sale_incremental = (
                self._sync_recent_after_sales_for_metrics(
                    store,
                    int(now.timestamp()),
                )
            )
        try:
            quota = self.fetch_product_audit_quota(store)
            self.db.set_setting(
                f"product_quota_{store.id}",
                json.dumps(quota, ensure_ascii=False),
            )
        except Exception as exc:
            quota_error = str(exc)
            try:
                quota = json.loads(
                    self.db.get_setting(
                        f"product_quota_{store.id}",
                        "{}",
                    )
                )
            except (TypeError, ValueError):
                quota = {}
        orders = self.db.list_orders(
            store_id=store.id,
            start_time=month_start,
            limit=100000,
        )
        valid = [
            row
            for row in orders
            if order_counts_as_transaction(row.get("status"))
        ]

        def buyer_identity(row: dict[str, Any]) -> str:
            try:
                raw = json.loads(row.get("raw_json") or "{}")
            except (TypeError, ValueError):
                raw = {}
            identity = (
                (raw.get("openid") if isinstance(raw, dict) else None)
                or row.get("buyer_name")
                or row.get("order_id")
            )
            return str(identity or "")

        buyer_ids = {identity for row in valid if (identity := buyer_identity(row))}
        today_orders = [
            row
            for row in valid
            if int(row.get("create_time") or 0) >= today_start
        ]
        analytics = self._analytics_days_for_metrics(store)
        analytics_by_date = {
            str(row.get("business_date")): row for row in analytics if row.get("business_date")
        }
        history = [
            row
            for business_date, row in analytics_by_date.items()
            if business_date < today.isoformat()
        ]
        week_start_date = today - timedelta(days=7)
        week_start_ts = int(
            datetime.combine(
                week_start_date,
                datetime.min.time(),
            ).timestamp()
        )
        yesterday_start_ts = int(
            datetime.combine(
                today - timedelta(days=1),
                datetime.min.time(),
            ).timestamp()
        )
        now_ts = int(now.timestamp())
        period_ranges = {
            "today": (today_start, now_ts + 1),
            "yesterday": (yesterday_start_ts, today_start),
            "seven_days": (week_start_ts, today_start),
            "thirty_days": (month_start, today_start),
        }
        gift_exclusions = {
            "today": _gift_exclusion_for_period(
                orders,
                now_ts=now_ts,
                start_ts=period_ranges["today"][0],
                end_ts=period_ranges["today"][1],
            ),
            "yesterday": _gift_exclusion_for_period(
                orders,
                now_ts=now_ts,
                start_ts=period_ranges["yesterday"][0],
                end_ts=period_ranges["yesterday"][1],
            ),
            "seven_days": _gift_exclusion_for_period(
                orders,
                now_ts=now_ts,
                start_ts=period_ranges["seven_days"][0],
                end_ts=period_ranges["seven_days"][1],
            ),
            "thirty_days": _gift_exclusion_for_period(
                orders,
                now_ts=now_ts,
                start_ts=period_ranges["thirty_days"][0],
                end_ts=period_ranges["thirty_days"][1],
            ),
        }
        after_sale_rows = self.db.list_after_sales(store.id)
        refund_order_rows = self.db.list_refund_orders_by_update_time(
            store.id,
            period_ranges["thirty_days"][0],
            period_ranges["thirty_days"][1],
        )
        refund_order_counts, refund_order_meta = (
            _refund_order_counts_for_periods(
                refund_order_rows,
                after_sale_rows,
                period_ranges,
            )
        )
        after_sale_sync_raw = str(
            self.db.get_setting(
                f"after_sales_last_sync_{store.id}",
                "",
            )
            or ""
        )
        after_sale_sync_ts = 0
        if after_sale_sync_raw.isdigit():
            after_sale_sync_ts = int(after_sale_sync_raw)
        else:
            for row in after_sale_rows:
                try:
                    synced_at = datetime.fromisoformat(
                        str(row.get("synced_at") or "")
                    ).timestamp()
                except (TypeError, ValueError):
                    continue
                after_sale_sync_ts = max(after_sale_sync_ts, int(synced_at))
        after_sale_has_cache = bool(after_sale_rows or after_sale_sync_ts)
        after_sale_stale = bool(
            after_sale_has_cache
            and (
                not after_sale_sync_ts
                or now_ts - after_sale_sync_ts
                > AFTER_SALE_CACHE_MAX_AGE_SECONDS
            )
        )
        after_sale_updated_at = (
            datetime.fromtimestamp(
                after_sale_sync_ts,
                SHANGHAI_TZ,
            ).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
            if after_sale_sync_ts
            else ""
        )
        refund_order_counts = {
            period: (
                int(value) if value is not None else None
            )
            for period, value in refund_order_counts.items()
        }

        def history_sum(field: str) -> int:
            total = 0
            for row in history:
                total += int(row.get(field) or 0)
            return total

        summary = self.db.store_summary(store.id)
        history_clicks_30d = history_sum("product_click_uv")
        history_refund_30d = history_sum("pay_refund_gmv")
        yesterday_orders = [
            row
            for row in valid
            if yesterday_start_ts
            <= int(row.get("create_time") or 0)
            < today_start
        ]
        week_orders = [
            row
            for row in valid
            if int(row.get("create_time") or 0) >= week_start_ts
        ]
        transaction_gmv_today = sum(
            int(row.get("total_amount") or 0) for row in today_orders
        )
        transaction_gmv_week = sum(
            int(row.get("total_amount") or 0) for row in week_orders
        )
        transaction_gmv_month = sum(
            int(row.get("total_amount") or 0) for row in valid
        )
        metrics: dict[str, Any] = {
            "in_sale": None,
            "warehouse": summary["warehouse"],
            "product_total": None,
            "buyers_today": len(
                {buyer_identity(row) for row in today_orders if buyer_identity(row)}
            ),
            "buyer_30d": len(buyer_ids),
            "buyers_yesterday": len(
                {
                    buyer_identity(row)
                    for row in yesterday_orders
                    if buyer_identity(row)
                }
            ),
            "buyers_7d": len(
                {
                    buyer_identity(row)
                    for row in week_orders
                    if buyer_identity(row)
                }
            ),
            "today_orders": len(today_orders),
            "orders_yesterday": len(yesterday_orders),
            "orders_7d": len(week_orders),
            "orders_30d": len(valid),
            "product_click_today": sum(
                int(row.get("product_click_uv") or 0)
                for row in analytics_by_date.values()
                if row.get("business_date") == today.isoformat()
            ),
            "visitors_30d": history_clicks_30d,
            "today_refund": sum(
                int(row.get("pay_refund_gmv") or 0)
                for row in analytics_by_date.values()
                if row.get("business_date") == today.isoformat()
            ),
            "refund_30d": history_refund_30d,
            "gmv_today": transaction_gmv_today,
            "gmv_yesterday": sum(
                int(row.get("total_amount") or 0)
                for row in yesterday_orders
            ),
            "gmv_7d": transaction_gmv_week,
            "gmv_30d": transaction_gmv_month,
            "refund_gmv_today": None,
            "refund_gmv_yesterday": None,
            "refund_gmv_7d": None,
            "refund_gmv_30d": None,
            "refund_orders_today": refund_order_counts["today"],
            "refund_orders_yesterday": refund_order_counts["yesterday"],
            "refund_orders_7d": refund_order_counts["seven_days"],
            "refund_orders_30d": refund_order_counts["thirty_days"],
            "aftersale": sum(
                "完成" not in str(row.get("status_text") or "")
                for row in self.db.list_after_sales(store.id)
            ),
            "warn": 0,
            "violation": 0,
            "cash": None,
            "frozen": None,
            "settle": None,
            "deposit": None,
            "dsr": None,
            "score_product": None,
            "score_deliver": None,
            "score_service": None,
            "good_eval_ratio": None,
            "quality_refund_ratio": None,
            "wait_send": 0,
            "wait_receive": sum(int(row.get("status") or 0) == 30 for row in valid),
            "recommend_exposure": None,
            "recommend_exposure_yesterday": None,
            "recommend_exposure_7d": None,
            "recommend_exposure_count": None,
            "recommend_exposure_yesterday_count": None,
            "recommend_exposure_7d_count": None,
            "recommend_exposure_people": None,
            "recommend_exposure_yesterday_people": None,
            "recommend_exposure_7d_people": None,
            "product_exposure_today": None,
            "product_exposure_yesterday": None,
            "product_exposure_7d": None,
            "product_exposure_count_today": None,
            "product_exposure_count_yesterday": None,
            "product_exposure_count_7d": None,
            "product_click_people_today": None,
            "product_click_people_yesterday": None,
            "product_click_people_7d": None,
            "product_click_count_today": None,
            "product_click_count_yesterday": None,
            "product_click_count_7d": None,
            "search_exposure_yesterday": None,
            "today_success": 0,
            "stats_at": now.strftime("%Y-%m-%d %H:%M:%S"),
            "updated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        }
        score_fields = (
            "dsr",
            "score_product",
            "score_deliver",
            "score_service",
        )
        score_attempt_status = {
            key: "failed"
            for key in score_fields
        }
        score_updated_at = ""
        score_daily_values = (
            existing_metrics.get("_score_daily_values")
            if isinstance(
                existing_metrics.get("_score_daily_values"),
                dict,
            )
            else {}
        )
        deposit_error = ""
        deposit_status = "failed"
        deposit_updated_at = ""
        private_client: ShopPrivateClient | None = None
        exposure_attempt_status = {
            "today": "failed",
            "yesterday": "failed",
            "seven_days": "failed",
        }
        exposure_count_attempt_status = {
            "today": "failed",
            "yesterday": "failed",
            "seven_days": "failed",
        }
        exposure_people_attempt_status = {
            "today": "failed",
            "yesterday": "failed",
            "seven_days": "failed",
        }
        exposure_period_sources: dict[str, str] = {}
        exposure_period_updated_at: dict[str, str] = {}
        exposure_updated_at = ""
        exposure_error = ""
        online_product_status = "failed"
        online_product_source = ""
        online_product_updated_at = ""
        online_product_error = ""
        sales_attempt_status = {
            "today": "failed",
            "yesterday": "failed",
            "seven_days": "failed",
            "thirty_days": "failed",
        }
        sales_period_sources: dict[str, str] = {}
        sales_updated_at = ""
        sales_period_updated_at: dict[str, str] = {}
        sales_error = ""
        sales_is_stale = False
        refund_attempt_status = {
            "today": "missing",
            "yesterday": "missing",
            "seven_days": "missing",
            "thirty_days": "missing",
        }
        refund_period_sources: dict[str, str] = {}
        official_refund_order_counts: dict[str, int] = {}
        official_refund_order_sources: dict[str, str] = {}
        refund_updated_at = ""
        refund_period_updated_at: dict[str, str] = {}
        refund_error = ""
        fund_attempt_status = {
            "cash": "failed",
            "frozen": "failed",
            "settle": "failed",
        }
        fund_period_updated_at: dict[str, str] = {}
        fund_updated_at = ""
        fund_source = ""
        fund_error = ""
        sales_period_fields = {
            "today": (
                ("gmv_today", "pay_gmv"),
                ("today_orders", "pay_order_cnt"),
                ("buyers_today", "pay_uv"),
            ),
            "yesterday": (
                ("gmv_yesterday", "pay_gmv"),
                ("orders_yesterday", "pay_order_cnt"),
                ("buyers_yesterday", "pay_uv"),
            ),
            "seven_days": (
                ("gmv_7d", "pay_gmv"),
                ("orders_7d", "pay_order_cnt"),
                ("buyers_7d", "pay_uv"),
            ),
            "thirty_days": (
                ("gmv_30d", "pay_gmv"),
                ("orders_30d", "pay_order_cnt"),
                ("buyers_30d", "pay_uv"),
            ),
        }
        refund_period_fields = {
            "today": ("refund_gmv_today", "pay_refund_gmv"),
            "yesterday": (
                "refund_gmv_yesterday",
                "pay_refund_gmv",
            ),
            "seven_days": ("refund_gmv_7d", "pay_refund_gmv"),
            "thirty_days": ("refund_gmv_30d", "pay_refund_gmv"),
        }
        previous_sales_sources = existing_metrics.get(
            "_sales_period_sources"
        )
        if not isinstance(previous_sales_sources, dict):
            previous_sales_sources = {}
        previous_refund_sources = existing_metrics.get(
            "_refund_period_sources"
        )
        if not isinstance(previous_refund_sources, dict):
            previous_refund_sources = {}
        previous_exposure_sources = existing_metrics.get(
            "_exposure_period_sources"
        )
        if not isinstance(previous_exposure_sources, dict):
            previous_exposure_sources = {}
        previous_exposure_period_updated_at = existing_metrics.get(
            "_exposure_period_updated_at"
        )
        if not isinstance(previous_exposure_period_updated_at, dict):
            previous_exposure_period_updated_at = {}
        previous_fund_updated_at = str(
            existing_metrics.get("_fund_updated_at") or ""
        )
        previous_fund_period_updated_at = existing_metrics.get(
            "_fund_period_updated_at"
        )
        if not isinstance(previous_fund_period_updated_at, dict):
            previous_fund_period_updated_at = {}
        sales_cache_snapshot_date = str(
            existing_metrics.get("_sales_snapshot_date") or ""
        )
        sales_cache_is_current_day = (
            sales_cache_snapshot_date == today.isoformat()
        )

        def optional_sales_int(value: Any) -> int | None:
            if value is None or value == "":
                return None
            try:
                return int(float(value))
            except (TypeError, ValueError):
                return None

        def metric_date(value: Any) -> str:
            text = str(value or "").strip()
            if (
                len(text) >= 10
                and text[4] == "-"
                and text[7] == "-"
            ):
                return text[:10]
            return ""

        def apply_fund_values(
            values: dict[str, Any],
            source: str,
        ) -> int:
            nonlocal fund_updated_at
            nonlocal fund_source
            found = 0
            for field in ("cash", "frozen", "settle"):
                value = optional_sales_int(values.get(field))
                if value is None:
                    continue
                metrics[field] = value
                fund_attempt_status[field] = "updated"
                fund_period_updated_at[field] = now.strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
                found += 1
            if found:
                fund_source = source
                fund_updated_at = now.strftime("%Y-%m-%d %H:%M:%S")
            return found

        def preserve_exposure_period(
            period: str,
            failure_message: str,
        ) -> bool:
            nonlocal exposure_error
            source = str(previous_exposure_sources.get(period) or "")
            metric_keys = {
                "today": (
                    "product_exposure_today",
                    "product_exposure_count_today",
                    "product_click_people_today",
                    "product_click_count_today",
                ),
                "yesterday": (
                    "product_exposure_yesterday",
                    "product_exposure_count_yesterday",
                    "product_click_people_yesterday",
                    "product_click_count_yesterday",
                ),
                "seven_days": (
                    "product_exposure_7d",
                    "product_exposure_count_7d",
                    "product_click_people_7d",
                    "product_click_count_7d",
                ),
            }[period]
            previous_values = {
                key: optional_sales_int(existing_metrics.get(key))
                for key in metric_keys
            }
            if not source or not any(
                value is not None for value in previous_values.values()
            ):
                return False
            for key, value in previous_values.items():
                metrics[key] = value
            exposure_attempt_status[period] = "stale"
            exposure_count_attempt_status[period] = (
                "stale"
                if previous_values[metric_keys[1]] is not None
                else "missing"
            )
            exposure_people_attempt_status[period] = (
                "stale"
                if previous_values[metric_keys[0]] is not None
                else "missing"
            )
            exposure_period_sources[period] = source
            prior_time = str(
                previous_exposure_period_updated_at.get(period)
                or existing_metrics.get("_exposure_updated_at")
                or ""
            )
            if prior_time:
                exposure_period_updated_at[period] = prior_time
            if failure_message:
                exposure_error = (
                    f"{exposure_error}；{failure_message}"[:500]
                    if exposure_error
                    else failure_message[:500]
                )
            return True

        def apply_official_sales(
            period: str,
            values: dict[str, Any],
            source: str,
            *,
            error: str = "",
        ) -> None:
            nonlocal sales_updated_at
            nonlocal sales_is_stale
            nonlocal sales_error
            nonlocal refund_updated_at
            nonlocal refund_error
            found = 0
            for metric_key, source_key in sales_period_fields[period]:
                value = optional_sales_int(values.get(source_key))
                if value is not None:
                    metrics[metric_key] = value
                    found += 1
                    continue
                if (
                    previous_sales_sources.get(period)
                    and sales_cache_is_current_day
                ):
                    previous = optional_sales_int(
                        existing_metrics.get(metric_key)
                    )
                    if previous is not None:
                        metrics[metric_key] = previous
                        sales_is_stale = True
            if found == len(sales_period_fields[period]):
                sales_attempt_status[period] = "updated"
            elif found:
                sales_attempt_status[period] = "partial"
                sales_is_stale = True
            else:
                sales_attempt_status[period] = (
                    "failed" if error else "missing"
                )
                sales_is_stale = True
            if found:
                sales_period_sources[period] = source
                sales_updated_at = now.strftime("%Y-%m-%d %H:%M:%S")
                sales_period_updated_at[period] = sales_updated_at
            elif (
                previous_sales_sources.get(period)
                and sales_cache_is_current_day
            ):
                sales_period_sources[period] = str(
                    previous_sales_sources[period]
                )
                previous_period_updated_at = existing_metrics.get(
                    "_sales_period_updated_at"
                )
                if isinstance(previous_period_updated_at, dict):
                    prior_time = str(
                        previous_period_updated_at.get(period) or ""
                    )
                    if prior_time:
                        sales_period_updated_at[period] = prior_time
            refund_metric_key, refund_source_key = refund_period_fields[
                period
            ]
            refund_value = optional_sales_int(
                values.get(refund_source_key)
            )
            if refund_value is not None:
                metrics[refund_metric_key] = max(0, refund_value)
                refund_attempt_status[period] = "updated"
                refund_period_sources[period] = source
                refund_updated_at = now.strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
                refund_period_updated_at[period] = refund_updated_at
            elif (
                previous_refund_sources.get(period)
                and sales_cache_is_current_day
            ):
                previous_refund = optional_sales_int(
                    existing_metrics.get(refund_metric_key)
                )
                if previous_refund is not None:
                    metrics[refund_metric_key] = previous_refund
                    refund_period_sources[period] = str(
                        previous_refund_sources[period]
                    )
                    refund_attempt_status[period] = "stale"
                    previous_period_updated_at = existing_metrics.get(
                        "_refund_period_updated_at"
                    )
                    if isinstance(previous_period_updated_at, dict):
                        prior_time = str(
                            previous_period_updated_at.get(period) or ""
                        )
                    else:
                        prior_time = str(
                            existing_metrics.get("_refund_updated_at") or ""
                        )
                    if prior_time:
                        refund_period_updated_at[period] = prior_time
            else:
                refund_attempt_status[period] = (
                    "failed" if error else "missing"
                )
            for count_source_key in (
                "pay_refund_order_cnt",
                "refund_order_cnt",
                "pay_refund_cnt",
                "refund_success_order_cnt",
            ):
                count_value = optional_sales_int(
                    values.get(count_source_key)
                )
                if count_value is None:
                    continue
                official_refund_order_counts[period] = max(
                    0,
                    count_value,
                )
                official_refund_order_sources[period] = (
                    f"{source}（官方字段 {count_source_key}）"
                )
                break
            if error:
                sales_error = (
                    f"{sales_error}；{period}: {error}"[:1000]
                    if sales_error
                    else f"{period}: {error}"[:1000]
                )
                refund_error = (
                    f"{refund_error}；{period}: {error}"[:1000]
                    if refund_error
                    else f"{period}: {error}"[:1000]
                )
        try:
            private = ShopPrivateClient(self.db, store)
            private_client = private
            realtime = private.realtime_overview()
            apply_official_sales(
                "today",
                realtime,
                "微信小店实时成交统计",
            )
            for (
                period,
                days,
                offset_days,
                source,
            ) in (
                (
                    "yesterday",
                    1,
                    1,
                    "微信小店后台昨日成交统计",
                ),
                (
                    "seven_days",
                    7,
                    1,
                    "微信小店后台近7天成交统计",
                ),
                (
                    "thirty_days",
                    30,
                    1,
                    "微信小店后台近30天成交统计",
                ),
            ):
                try:
                    period_values = private.period_overview(
                        days,
                        offset_days=offset_days,
                    )
                except Exception as exc:
                    errors.append(f"成交周期（{period}）：{exc}")
                    apply_official_sales(
                        period,
                        {},
                        source,
                        error=str(exc),
                    )
                else:
                    apply_official_sales(
                        period,
                        period_values,
                        source,
                    )
            for gift in gift_exclusions.values():
                gift["applied_amount"] = 0
                gift["applied_orders"] = 0
            today_click = int(float(realtime.get("product_click_uv") or 0))
            today_refund = int(float(realtime.get("pay_refund_gmv") or 0))
            metrics.update(
                {
                    "today_refund": today_refund,
                    "product_click_today": today_click,
                    "visitors_30d": int(metrics.get("visitors_30d") or 0) + today_click,
                    "refund_30d": int(metrics.get("refund_30d") or 0) + today_refund,
                }
            )
            metrics.update(private.todo_overview())
            score_values = private.score_overview()
            official_score_daily = score_values.get("_score_daily_values")
            if isinstance(official_score_daily, dict) and official_score_daily:
                score_daily_values = official_score_daily
            for key in score_fields:
                try:
                    value = float(score_values.get(key))
                except (TypeError, ValueError):
                    score_attempt_status[key] = "missing"
                    continue
                metrics[key] = value
                score_attempt_status[key] = "updated"
                score_updated_at = now.strftime("%Y-%m-%d %H:%M:%S")
            metrics.update(private.evaluation_overview())
            try:
                balance_values = private.account_balance()
            except Exception as exc:
                fund_error = f"资金账户：{exc}"[:500]
                errors.append(fund_error)
                for field in fund_attempt_status:
                    fund_attempt_status[field] = "failed"
            else:
                if not isinstance(balance_values, dict):
                    balance_values = {}
                found = apply_fund_values(
                    balance_values,
                    "微信小店资金账户 getGeneralAccountBalance",
                )
                if not found:
                    fund_error = (
                        "资金账户未返回可提现、冻结或待结算余额"
                    )
                    for field in fund_attempt_status:
                        fund_attempt_status[field] = "missing"
            try:
                deposit_value = private.deposit_balance()
            except Exception as exc:
                deposit_status = "failed"
                deposit_error = str(exc)
                errors.append(f"保证金：{exc}")
            else:
                if deposit_value is None:
                    deposit_status = "missing"
                else:
                    metrics["deposit"] = int(deposit_value)
                    deposit_status = "updated"
                    deposit_updated_at = now.strftime("%Y-%m-%d %H:%M:%S")
            exposure_jobs = (
                (
                    "today",
                    "recommend_exposure",
                    "recommend_exposure_count",
                    "recommend_exposure_people",
                    lambda: private.product_overview(realtime=True),
                ),
                (
                    "yesterday",
                    "recommend_exposure_yesterday",
                    "recommend_exposure_yesterday_count",
                    "recommend_exposure_yesterday_people",
                    lambda: private.product_overview(days=1, offset_days=1),
                ),
                (
                    "seven_days",
                    "recommend_exposure_7d",
                    "recommend_exposure_7d_count",
                    "recommend_exposure_7d_people",
                    lambda: private.product_overview(days=7, offset_days=1),
                ),
            )
            for (
                exposure_key,
                metric_key,
                count_metric_key,
                people_metric_key,
                loader,
            ) in exposure_jobs:
                try:
                    overview = loader()
                except Exception as exc:
                    errors.append(f"商品曝光（{exposure_key}）：{exc}")
                    if not preserve_exposure_period(
                        exposure_key,
                        f"商品曝光（{exposure_key}）：{exc}",
                    ):
                        exposure_error = str(exc)
                        exposure_count_attempt_status[exposure_key] = "failed"
                        exposure_people_attempt_status[exposure_key] = "failed"
                    continue
                exposure = overview.get("exposure")
                exposure_count = overview.get("exposure_count")
                exposure_people = overview.get("exposure_people")
                product_count = optional_sales_int(
                    overview.get("product_view_cnt")
                )
                product_people = optional_sales_int(
                    overview.get("product_view_uv")
                )
                product_exposure_key = {
                    "today": "product_exposure_today",
                    "yesterday": "product_exposure_yesterday",
                    "seven_days": "product_exposure_7d",
                }[exposure_key]
                product_exposure_count_key = {
                    "today": "product_exposure_count_today",
                    "yesterday": "product_exposure_count_yesterday",
                    "seven_days": "product_exposure_count_7d",
                }[exposure_key]
                metrics[product_exposure_key] = product_people
                metrics[product_exposure_count_key] = product_count
                click_people = optional_sales_int(
                    overview.get("product_click_uv")
                )
                click_count = optional_sales_int(
                    overview.get("product_click_cnt")
                )
                click_people_key = {
                    "today": "product_click_people_today",
                    "yesterday": "product_click_people_yesterday",
                    "seven_days": "product_click_people_7d",
                }[exposure_key]
                click_count_key = {
                    "today": "product_click_count_today",
                    "yesterday": "product_click_count_yesterday",
                    "seven_days": "product_click_count_7d",
                }[exposure_key]
                metrics[click_people_key] = click_people
                metrics[click_count_key] = click_count
                if product_count is not None or product_people is not None:
                    exposure_count = product_count
                    exposure_people = product_people
                    exposure = (
                        product_people
                        if product_people is not None
                        else product_count
                    )
                if (
                    exposure_count is None
                    and exposure_people is None
                    and exposure is not None
                ):
                    # Legacy private clients only expose the people count as
                    # "exposure"; never relabel it as view count.
                    exposure_people = exposure
                if exposure_count is not None:
                    metrics[count_metric_key] = int(exposure_count)
                    exposure_count_attempt_status[exposure_key] = "updated"
                else:
                    exposure_count_attempt_status[exposure_key] = "missing"
                if exposure_people is not None:
                    metrics[people_metric_key] = int(exposure_people)
                    exposure_people_attempt_status[exposure_key] = "updated"
                else:
                    exposure_people_attempt_status[exposure_key] = "missing"
                if exposure is None:
                    exposure = (
                        exposure_people
                        if exposure_people is not None
                        else exposure_count
                    )
                if exposure is not None:
                    metrics[metric_key] = int(exposure)
                if (
                    exposure_count is not None
                    or exposure_people is not None
                ):
                    exposure_attempt_status[exposure_key] = (
                        "updated"
                        if exposure_count is not None
                        and exposure_people is not None
                        else "partial"
                    )
                    exposure_updated_at = now.strftime("%Y-%m-%d %H:%M:%S")
                    exposure_period_updated_at[exposure_key] = (
                        exposure_updated_at
                    )
                    exposure_period_sources[exposure_key] = (
                        "微信小店店铺数据·商品数据·商品转化"
                    )
                else:
                    if not preserve_exposure_period(
                        exposure_key,
                        f"商品曝光（{exposure_key}）：Compass 未返回商品曝光人数",
                    ):
                        exposure_attempt_status[exposure_key] = "missing"
            search = private.search_overview(30)
            metrics["search_exposure_yesterday"] = search["search_exposure_yesterday"]
            try:
                metrics["today_success"] = private.audit_success_today()
            except Exception as exc:
                errors.append(f"今日审核：{exc}")
        except ShopPrivateError as exc:
            deposit_error = str(exc) if not deposit_error else deposit_error
            sales_error = str(exc) if not sales_error else sales_error
            errors.append(f"商家后台：{exc}")
            if any(marker in str(exc) for marker in ("登录已失效", "请重新扫码", "拒绝请求")):
                self.db.update_web_status(store.id, "已掉线", str(exc))
        try:
            client = self.client_for(store)
            if not isinstance(client, MockShopClient):
                count_errors: list[str] = []
                with ThreadPoolExecutor(max_workers=3) as executor:
                    count_jobs = {
                        "product_total": executor.submit(client.fetch_product_total, None),
                        "in_sale": executor.submit(client.fetch_product_total, 5),
                        "warehouse": executor.submit(client.fetch_product_total, 11),
                    }
                    for key, future in count_jobs.items():
                        try:
                            metrics[key] = int(future.result())
                        except Exception as exc:
                            count_errors.append(f"{key}: {exc}")
                if metrics.get("in_sale") is not None:
                    online_product_status = "updated"
                    online_product_source = (
                        "微信开放平台商品列表 total_num（status=5）"
                    )
                    online_product_updated_at = now.strftime(
                        "%Y-%m-%d %H:%M:%S"
                    )
                elif count_errors:
                    online_product_error = "；".join(count_errors)
                if any(
                    status != "updated"
                    for status in fund_attempt_status.values()
                ):
                    balance = client.fetch_fund_balance()
                    if not isinstance(balance, dict):
                        balance = {}
                    raw_data = balance.get("data")
                    if isinstance(raw_data, dict):
                        balance = raw_data
                    found = apply_fund_values(
                        {
                            "cash": balance.get(
                                "available_amount",
                                balance.get("cash"),
                            ),
                            "frozen": balance.get(
                                "frozen_amount",
                                balance.get("frozen"),
                            ),
                            "settle": balance.get(
                                "pending_amount",
                                balance.get("settle"),
                            ),
                        },
                        "微信开放平台资金余额接口",
                    )
                    if not found and not fund_error:
                        fund_error = "资金余额接口未返回真实余额"
        except Exception as exc:
            errors.append(f"商品与资金余额：{exc}")
            online_product_error = str(exc)
            if not fund_error:
                fund_error = f"资金余额：{exc}"[:500]
            for field, status in fund_attempt_status.items():
                if status != "updated":
                    fund_attempt_status[field] = "failed"
        if online_product_status != "updated" and private_client is not None:
            try:
                private_counts = private_client.status_counts()
            except Exception as exc:
                online_product_error = (
                    f"{online_product_error}；{exc}".strip("；")
                )
            else:
                private_sales = private_counts.get("sales")
                if private_sales is None:
                    online_product_status = "missing"
                else:
                    metrics["in_sale"] = int(private_sales)
                    online_product_status = "updated"
                    online_product_source = (
                        "微信小店商家后台商品状态 totalNum（销售中）"
                    )
                    online_product_updated_at = now.strftime(
                        "%Y-%m-%d %H:%M:%S"
                    )
        if online_product_status != "updated":
            previous_online_source = str(
                existing_metrics.get("_online_product_source") or ""
            )
            previous_online = optional_sales_int(
                existing_metrics.get("in_sale")
            )
            if previous_online_source and previous_online is not None:
                metrics["in_sale"] = previous_online
                online_product_status = "stale"
                online_product_source = previous_online_source
                online_product_updated_at = str(
                    existing_metrics.get("_online_product_updated_at") or ""
                )
            elif not online_product_error:
                online_product_error = "在线商品数未返回真实值"
        for field in ("cash", "frozen", "settle"):
            if fund_attempt_status[field] == "updated":
                continue
            previous_fund = optional_sales_int(
                existing_metrics.get(field)
            )
            previous_fund_source = str(
                existing_metrics.get("_fund_source") or ""
            )
            if previous_fund is not None and previous_fund_source:
                metrics[field] = previous_fund
                fund_attempt_status[field] = "stale"
                prior_time = str(
                    previous_fund_period_updated_at.get(field)
                    or previous_fund_updated_at
                    or ""
                )
                if prior_time:
                    fund_period_updated_at[field] = prior_time
                fund_source = fund_source or previous_fund_source
                fund_updated_at = fund_updated_at or prior_time
        if not fund_error and any(
            status != "updated"
            for status in fund_attempt_status.values()
        ):
            fund_error = "资金账户部分余额未知，已保留最后可信值"
        if quota_error:
            errors.append(f"提审额度：{quota_error}")
        for key in score_fields:
            if score_attempt_status[key] == "updated":
                continue
            try:
                previous_value = float(existing_metrics.get(key))
            except (TypeError, ValueError):
                previous_value = None
            if previous_value is not None:
                metrics[key] = previous_value
        if deposit_status != "updated":
            existing_deposit_source = str(
                existing_metrics.get("_deposit_source") or ""
            )
            try:
                previous_deposit = int(existing_metrics.get("deposit"))
            except (TypeError, ValueError):
                previous_deposit = None
            if existing_deposit_source and previous_deposit is not None:
                metrics["deposit"] = previous_deposit
                deposit_updated_at = str(
                    existing_metrics.get("_deposit_updated_at") or ""
                )
        metrics["quota_remaining"] = int(quota.get("remaining") or 0)
        metrics["quota_total"] = int(quota.get("total") or 0)
        metrics["quota_error"] = quota_error
        if metrics.get("product_total") is not None:
            metrics["product_total"] = max(0, int(metrics["product_total"]))
        elif (
            metrics.get("in_sale") is not None
            and metrics.get("warehouse") is not None
        ):
            metrics["product_total"] = (
                int(metrics["in_sale"]) + int(metrics["warehouse"])
            )
        metrics["orders_today"] = int(metrics.get("today_orders") or 0)
        if metrics.get("refund_gmv_today") is not None:
            metrics["today_refund"] = int(metrics["refund_gmv_today"])
        else:
            metrics["today_refund"] = int(
                metrics.get("today_refund") or 0
            )
        metrics["refund_today"] = int(metrics.get("today_refund") or 0)
        if metrics.get("refund_gmv_30d") is not None:
            metrics["refund_30d"] = int(metrics["refund_gmv_30d"])
        metrics["_errors"] = errors
        metrics["_credential_checked"] = credential_checked
        metrics["_credential_probe_ok"] = credential_probe_ok
        metrics["_credential_error_code"] = credential_error_code
        metrics["_credential_error_message"] = credential_error_message
        metrics["_score_attempt_status"] = score_attempt_status
        metrics["_score_updated_at"] = score_updated_at or str(
            existing_metrics.get("_score_updated_at") or ""
        )
        metrics["_score_source"] = "微信小店统计接口 scoreTypeList 11/12/13/14"
        metrics["_score_daily_values"] = score_daily_values
        metrics["_exposure_attempt_status"] = exposure_attempt_status
        metrics["_exposure_count_attempt_status"] = (
            exposure_count_attempt_status
        )
        metrics["_exposure_people_attempt_status"] = (
            exposure_people_attempt_status
        )
        metrics["_exposure_updated_at"] = (
            exposure_updated_at
            or str(existing_metrics.get("_exposure_updated_at") or "")
        )
        metrics["_exposure_error"] = exposure_error[:500]
        metrics["_exposure_period_sources"] = exposure_period_sources
        metrics["_exposure_period_updated_at"] = (
            exposure_period_updated_at
        )
        metrics["_exposure_source"] = (
            "；".join(dict.fromkeys(exposure_period_sources.values()))
            or str(existing_metrics.get("_exposure_source") or "")
        )
        metrics["_online_product_attempt_status"] = online_product_status
        metrics["_online_product_updated_at"] = online_product_updated_at
        metrics["_online_product_error"] = online_product_error[:500]
        metrics["_online_product_source"] = online_product_source
        metrics["_fund_attempt_status"] = fund_attempt_status
        metrics["_fund_period_updated_at"] = fund_period_updated_at
        metrics["_fund_updated_at"] = (
            fund_updated_at
            or str(existing_metrics.get("_fund_updated_at") or "")
        )
        metrics["_fund_error"] = fund_error[:500]
        metrics["_fund_source"] = fund_source
        metrics["_cache_store_id"] = int(store.id)
        metrics["_cache_store_app_id"] = str(store.app_id)
        metrics["_cache_schema_version"] = 2
        refund_count_fields = {
            "today": "refund_gmv_today",
            "yesterday": "refund_gmv_yesterday",
            "seven_days": "refund_gmv_7d",
            "thirty_days": "refund_gmv_30d",
        }
        refund_count_keys = {
            "today": "refund_orders_today",
            "yesterday": "refund_orders_yesterday",
            "seven_days": "refund_orders_7d",
            "thirty_days": "refund_orders_30d",
        }
        gated_refund_periods: list[str] = []
        for period, amount_key in refund_count_fields.items():
            amount = metrics.get(amount_key)
            count_key = refund_count_keys[period]
            if (
                amount is not None
                and int(amount) > 0
                and metrics.get(count_key) in {None, 0}
            ):
                metrics[count_key] = None
                refund_order_counts[period] = None
                gated_refund_periods.append(period)
        for period, fields in sales_period_fields.items():
            if (
                sales_attempt_status[period] == "updated"
                or not previous_sales_sources.get(period)
                or not sales_cache_is_current_day
            ):
                continue
            for metric_key, _source_key in fields:
                previous = optional_sales_int(
                    existing_metrics.get(metric_key)
                )
                if previous is not None:
                    metrics[metric_key] = previous
            sales_period_sources.setdefault(
                period,
                str(previous_sales_sources[period]),
            )
            previous_period_updated_at = existing_metrics.get(
                "_sales_period_updated_at"
            )
            if isinstance(previous_period_updated_at, dict):
                prior_time = str(
                    previous_period_updated_at.get(period) or ""
                )
                if prior_time:
                    sales_period_updated_at.setdefault(period, prior_time)
            sales_is_stale = True
        if metrics.get("buyers_30d") is not None:
            metrics["buyer_30d"] = int(metrics["buyers_30d"])
        source_values = list(
            dict.fromkeys(
                value
                for value in sales_period_sources.values()
                if value
            )
        )
        metrics["_sales_attempt_status"] = sales_attempt_status
        metrics["_sales_period_sources"] = sales_period_sources
        metrics["_sales_period_updated_at"] = sales_period_updated_at
        metrics["_sales_source"] = (
            "；".join(source_values)
            if source_values
            else "本地订单缓存降级"
        )
        metrics["_sales_updated_at"] = (
            sales_updated_at
            or str(existing_metrics.get("_sales_updated_at") or "")
        )
        metrics["_sales_error"] = sales_error[:1000]
        metrics["_sales_is_stale"] = bool(
            sales_is_stale or not source_values
        )
        if sales_period_updated_at or sales_cache_is_current_day:
            sales_snapshot_date = today.isoformat()
        else:
            sales_snapshot_date = (
                sales_cache_snapshot_date
                or metric_date(existing_metrics.get("_sales_updated_at"))
            )
        metrics["_sales_snapshot_date"] = sales_snapshot_date
        metrics["_refund_attempt_status"] = refund_attempt_status
        metrics["_refund_period_sources"] = refund_period_sources
        metrics["_refund_period_updated_at"] = refund_period_updated_at
        metrics["_refund_updated_at"] = (
            refund_updated_at
            or str(existing_metrics.get("_refund_updated_at") or "")
        )
        metrics["_refund_error"] = refund_error[:1000]
        metrics["_after_sale_incremental"] = after_sale_incremental
        try:
            after_sale_coverage_start_ts = int(
                self.db.get_setting(
                    f"after_sales_coverage_start_{store.id}",
                    "0",
                )
                or 0
            )
        except (TypeError, ValueError):
            after_sale_coverage_start_ts = 0
        try:
            after_sale_coverage_end_ts = int(
                self.db.get_setting(
                    f"after_sales_coverage_end_{store.id}",
                    "0",
                )
                or 0
            )
        except (TypeError, ValueError):
            after_sale_coverage_end_ts = 0

        refund_coverage_complete: dict[str, bool] = {}
        refund_conflict_periods: list[str] = []
        for period, (start_ts, end_ts) in period_ranges.items():
            required_end_ts = min(now_ts, end_ts - 1)
            refund_coverage_complete[period] = bool(
                after_sale_coverage_start_ts
                and after_sale_coverage_end_ts
                and after_sale_coverage_start_ts <= start_ts
                and after_sale_coverage_end_ts >= required_end_ts
            )
        refund_amounts_from_details = _refund_amounts_for_periods(
            after_sale_rows,
            period_ranges,
        )

        for period in period_ranges:
            amount = metrics.get(refund_count_fields[period])
            detail_amount = refund_amounts_from_details.get(period)
            if (
                refund_coverage_complete[period]
                and detail_amount is not None
                and int(detail_amount) > 0
                and (
                    amount is None
                    or int(amount) == 0
                )
            ):
                amount = int(detail_amount)
                metrics[refund_count_fields[period]] = amount
                refund_attempt_status[period] = "updated"
                refund_period_sources[period] = (
                    "微信小店官方售后完成明细"
                )
                refund_updated_at = (
                    after_sale_updated_at
                    or now.strftime("%Y-%m-%d %H:%M:%S")
                )
                refund_period_updated_at[period] = refund_updated_at
            official_count_available = (
                period in official_refund_order_counts
            )
            candidate = (
                official_refund_order_counts[period]
                if official_count_available
                else refund_order_counts.get(period)
            )
            if (
                not official_count_available
                and not refund_coverage_complete[period]
            ):
                refund_order_counts[period] = None
                continue
            if (
                not official_count_available
                and candidate is None
            ):
                candidate = 0
            if amount is None:
                refund_order_counts[period] = candidate
                continue
            amount_value = max(0, int(amount))
            count_value = (
                int(candidate)
                if candidate is not None
                else None
            )
            if amount_value == 0 and (count_value or 0) > 0:
                refund_order_counts[period] = None
                refund_conflict_periods.append(period)
            elif amount_value > 0 and not count_value:
                refund_order_counts[period] = None
                refund_conflict_periods.append(period)
            else:
                refund_order_counts[period] = count_value

        metrics["_refund_order_counts"] = refund_order_counts
        for period, count_key in refund_count_keys.items():
            metrics[count_key] = refund_order_counts.get(period)
        refund_order_available = bool(
            official_refund_order_counts
            or (
                after_sale_has_cache
                and all(refund_coverage_complete.values())
            )
        )
        refund_order_status = "missing"
        if official_refund_order_counts:
            refund_order_status = "fresh"
        elif after_sale_has_cache:
            if not all(refund_coverage_complete.values()):
                refund_order_status = "partial"
            elif after_sale_stale:
                refund_order_status = "stale"
            else:
                refund_order_status = "fresh"
        metrics["_refund_order_cache"] = {
            "available": refund_order_available,
            "stale": bool(
                after_sale_stale
                and not official_refund_order_counts
            ),
            "updated_at": after_sale_updated_at or now.strftime(
                "%Y-%m-%d %H:%M:%S"
            ),
            "status": refund_order_status,
            "source": refund_order_meta["source"],
            "sources": {
                period: (
                    [official_refund_order_sources[period]]
                    if period in official_refund_order_sources
                    else refund_order_meta["sources"].get(period, [])
                )
                for period in period_ranges
            },
            "record_count": refund_order_meta["record_count"],
            "official_count_fields": official_refund_order_sources,
            "order_status_evidence": refund_order_meta[
                "order_status_evidence"
            ],
            "coverage": {
                period: {
                    "start": datetime.fromtimestamp(
                        start_ts,
                        SHANGHAI_TZ,
                    ).strftime(
                        "%Y-%m-%d %H:%M:%S"
                    ),
                    "end": datetime.fromtimestamp(
                        end_ts,
                        SHANGHAI_TZ,
                    ).strftime(
                        "%Y-%m-%d %H:%M:%S"
                    ),
                }
                for period, (start_ts, end_ts) in period_ranges.items()
            },
            "covered": refund_coverage_complete,
            "gated_periods": gated_refund_periods,
            "conflict_periods": refund_conflict_periods,
            "hint": (
                "退款金额与退款完成明细口径冲突，单量显示待同步"
                if refund_conflict_periods
                else (
                    "官方退款金额大于 0，但本地售后明细覆盖不足，单量显示待同步"
                    if gated_refund_periods
                    else (
                        "售后明细未完整覆盖近30天，单量显示待同步"
                        if not all(refund_coverage_complete.values())
                        else (
                            "售后单量缓存待刷新"
                            if after_sale_stale
                            else ""
                        )
                    )
                )
            ),
        }
        metrics["_gift_exclusion"] = gift_exclusions
        metrics["_deposit_attempt_status"] = deposit_status
        metrics["_deposit_updated_at"] = deposit_updated_at
        metrics["_deposit_error"] = deposit_error[:500]
        metrics["_deposit_source"] = (
            "微信小店 getDepositMPayState"
            if deposit_status == "updated"
            or (
                metrics.get("deposit") is not None
                and existing_metrics.get("_deposit_source")
            )
            else ""
        )
        self.db.set_setting(
            f"store_metrics_{store.id}",
            json.dumps(metrics, ensure_ascii=False),
        )
        return metrics

    def _sync_recent_after_sales_for_metrics(
        self,
        store: Store,
        now_ts: int,
    ) -> dict[str, Any]:
        """Refresh a full refund window once, then keep the last two days current."""
        now_ts = int(now_ts)
        local_today = datetime.fromtimestamp(
            now_ts,
            SHANGHAI_TZ,
        ).date()
        full_start = int(
            datetime.combine(
                local_today - timedelta(days=AFTER_SALE_FULL_SYNC_DAYS),
                datetime.min.time(),
                tzinfo=SHANGHAI_TZ,
            ).timestamp()
        )
        try:
            full_sync_at = int(
                self.db.get_setting(
                    f"after_sales_full_sync_at_{store.id}",
                    "0",
                )
                or 0
            )
        except (TypeError, ValueError):
            full_sync_at = 0
        errors: list[str] = []
        full_checked = False
        full_complete = False
        incremental_complete = False

        if (
            not full_sync_at
            or now_ts - full_sync_at
            >= AFTER_SALE_FULL_SYNC_MAX_AGE_SECONDS
        ):
            full_checked = True
            try:
                full_result = self.sync_after_sales(
                    store,
                    full_start,
                    now_ts,
                    time_field="update",
                )
            except Exception as exc:
                errors.append(f"近30天售后同步：{exc}")
            else:
                if getattr(full_result, "failed", 0):
                    errors.extend(
                        str(value)
                        for value in (
                            getattr(full_result, "errors", None)
                            or []
                        )
                    )
                else:
                    full_complete = True
                    incremental_complete = True
                    self.db.set_setting(
                        f"after_sales_full_sync_at_{store.id}",
                        str(now_ts),
                    )
                    self.db.set_setting(
                        f"after_sales_coverage_start_{store.id}",
                        str(full_start),
                    )
                    self.db.set_setting(
                        f"after_sales_coverage_end_{store.id}",
                        str(now_ts),
                    )

        if not incremental_complete:
            try:
                incremental_result = self.sync_after_sales(
                    store,
                    now_ts - 2 * 86400,
                    now_ts,
                    time_field="update",
                )
            except Exception as exc:
                errors.append(f"近2天售后同步：{exc}")
            else:
                if getattr(incremental_result, "failed", 0):
                    errors.extend(
                        str(value)
                        for value in (
                            getattr(
                                incremental_result,
                                "errors",
                                None,
                            )
                            or []
                        )
                    )
                else:
                    incremental_complete = True
                    self.db.set_setting(
                        f"after_sales_coverage_end_{store.id}",
                        str(now_ts),
                    )

        updated_at = datetime.fromtimestamp(
            now_ts,
            SHANGHAI_TZ,
        ).strftime("%Y-%m-%d %H:%M:%S")
        if incremental_complete:
            self.db.set_setting(
                f"after_sales_last_sync_{store.id}",
                str(now_ts),
            )
        return {
            "status": (
                "updated"
                if incremental_complete and not errors
                else ("partial" if incremental_complete else "failed")
            ),
            "updated_at": updated_at if incremental_complete else "",
            "error": "；".join(errors)[:500],
            "full_checked": full_checked,
            "full_complete": full_complete,
            "incremental_complete": incremental_complete,
            "coverage_start": (
                datetime.fromtimestamp(full_start).strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
                if full_complete
                else ""
            ),
            "coverage_end": updated_at if incremental_complete else "",
        }

    def _analytics_days_for_metrics(self, store: Store) -> list[dict[str, Any]]:
        rows = self.db.list_analytics_days(store.id, 30)
        now_ts = int(datetime.now().timestamp())
        try:
            last_attempt = int(
                self.db.get_setting(f"analytics_product_backfill_at_{store.id}", "0")
            )
        except ValueError:
            last_attempt = 0
        if now_ts - last_attempt < 21600:
            return rows
        client = self.client_for(store)
        if isinstance(client, MockShopClient):
            return rows
        today = datetime.now().date()
        day_pairs = [
            (
                (today - timedelta(days=offset)).isoformat(),
                (today - timedelta(days=offset)).strftime("%Y%m%d"),
            )
            for offset in range(1, 30)
        ]

        def fetch_day(pair: tuple[str, str]) -> tuple[str, dict[str, int]]:
            business_date, api_day = pair
            return business_date, client.fetch_product_day_totals(api_day)

        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = {executor.submit(fetch_day, pair): pair[0] for pair in day_pairs}
            for future in as_completed(futures):
                business_date = futures[future]
                try:
                    _business_date, data = future.result()
                    self.db.upsert_analytics_day(
                        store.id,
                        business_date,
                        {
                            **data,
                            "_source": "product_metrics",
                        },
                    )
                except Exception:
                    continue
        self.db.set_setting(f"analytics_product_backfill_at_{store.id}", str(now_ts))
        return self.db.list_analytics_days(store.id, 30)

    def fetch_product_taglink(self, store: Store, product_id: str) -> str:
        client = self.client_for(store)
        if isinstance(client, MockShopClient):
            return f"demo://product-tag/{product_id}"
        result = client.fetch_product_taglink(product_id)
        for key in (
            "product_taglink",
            "product_tag_link",
            "taglink",
            "link",
            "url",
        ):
            value = result.get(key)
            if value:
                return str(value)
        data = result.get("data")
        if isinstance(data, dict):
            for key in (
                "product_taglink",
                "product_tag_link",
                "taglink",
                "link",
                "url",
            ):
                value = data.get(key)
                if value:
                    return str(value)
        return json.dumps(result, ensure_ascii=False)

    def fetch_product_detail(
        self,
        store: Store,
        product_id: str,
    ) -> Product:
        """Read and cache the current upstream product detail.

        The web UI must not open the stock editor from the local SQLite
        snapshot because SKU IDs and stock can change after the last list
        refresh.
        """
        client = self.client_for(store)
        if isinstance(client, MockShopClient):
            product = next(
                (
                    row
                    for row in client.fetch_products()
                    if str(row.product_id) == str(product_id)
                ),
                None,
            )
            if product is None:
                raise ValueError(f"演示店铺没有商品 {product_id}")
        else:
            product = client.fetch_product(store, str(product_id))
        self.db.upsert_product(product)
        self.db.mark_product_catalog_done(store.id, str(product_id))
        return product

    def fetch_product_audit_quota(self, store: Store) -> dict[str, int]:
        client = self.client_for(store)
        if isinstance(client, MockShopClient):
            return {"remaining": 500, "total": 500}
        result = client.fetch_product_audit_quota()
        data = result.get("data")
        source = result.get("audit_quota") or (data if isinstance(data, dict) else result)
        if isinstance(source, dict) and isinstance(source.get("audit_quota"), dict):
            source = source["audit_quota"]
        remaining = (
            source.get("audit_total_remaining")
            or source.get("avail_quota")
            or source.get("remaining")
            or source.get("publish_quota_remaining")
            or 0
        )
        total = (
            source.get("audit_total_quota")
            or source.get("total_quota")
            or source.get("total")
            or source.get("publish_quota_total")
            or 0
        )
        return {
            "remaining": int(remaining or 0),
            "total": int(total or 0),
        }

    def refresh_product_tab(
        self,
        store: Store,
        tab_key: str,
        progress: ProgressCallback | None = None,
        cancel_event: threading.Event | None = None,
    ) -> SyncResult:
        if self.db.get_web_cookies(store.id):
            try:
                return self.sync_private_product_tab(
                    store, tab_key, max_pages=None, progress=progress, cancel_event=cancel_event
                )
            except ShopPrivateError:
                if not self.db.get_store_secret(store.id)[1]:
                    raise
        client = self.client_for(store)
        products = []
        if isinstance(client, MockShopClient):
            products = client.fetch_products(progress)
        else:
            product_ids = client.fetch_product_ids()
            if progress:
                progress(
                    f"已获取商品目录，准备读取 0/{len(product_ids)}",
                    0,
                    len(product_ids),
                )
            errors: list[str] = []
            with ThreadPoolExecutor(
                max_workers=max(1, min(6, len(product_ids) or 1)),
                thread_name_prefix="product-refresh",
            ) as executor:
                futures = {
                    executor.submit(client.fetch_product, store, product_id): product_id
                    for product_id in product_ids
                }
                completed = 0
                for future in as_completed(futures):
                    if cancel_event is not None and cancel_event.is_set():
                        for pending in futures:
                            pending.cancel()
                        return SyncResult(
                            "product_query",
                            len(products),
                            "已取消商品查询",
                            cancelled=True,
                        )
                    product_id = futures[future]
                    completed += 1
                    try:
                        products.append(future.result())
                    except Exception as exc:
                        errors.append(f"{product_id}: {exc}")
                    if progress:
                        progress(
                            f"正在读取商品详情 {completed}/{len(product_ids)}",
                            completed,
                            len(product_ids),
                        )
            products.sort(
                key=lambda product: product_ids.index(product.product_id)
                if product.product_id in product_ids
                else len(product_ids)
            )
        selected = [
            product
            for product in products
            if product_matches_tab(
                {
                    "status": product.status,
                    "edit_status": product.edit_status,
                    "raw_json": json.dumps(product.raw),
                },
                tab_key,
            )
        ]
        if cancel_event is not None and cancel_event.is_set():
            return SyncResult("product_query", 0, "已取消商品查询", cancelled=True)
        if errors:
            self.db.upsert_products(selected)
            return SyncResult(
                "product_query",
                len(selected),
                f"商品已部分更新，{len(errors)} 件读取失败",
                success=len(selected),
                failed=len(errors),
                errors=errors,
            )
        self.db.replace_product_tab(store.id, tab_key, selected)
        return SyncResult("product_query", len(selected), "商品已从微信读取", success=len(selected))

    def sync_private_product_tab(
        self,
        store: Store,
        tab_key: str,
        max_pages: int | None = 5,
        progress: ProgressCallback | None = None,
        cancel_event: threading.Event | None = None,
    ) -> SyncResult:
        private = ShopPrivateClient(self.db, store)
        products = private.scan_products(
            tab_key,
            max_pages=max_pages,
            stop=(cancel_event.is_set if cancel_event else None),
            on_progress=(
                (
                    lambda _key, page, pages: progress(
                        f"正在读取商品页第 {page}/{pages} 页",
                        page,
                        pages,
                    )
                )
                if progress
                else None
            ),
        )
        if max_pages is None and cancel_event is not None and cancel_event.is_set():
            return SyncResult("private_product_tab", 0, "已取消商品查询", cancelled=True)
        for product in products:
            product.store_id = store.id
            product.store_name = store.name
        if max_pages is None:
            self.db.replace_product_tab(store.id, tab_key, products)
        elif products:
            self.db.upsert_products(products)
        return SyncResult(
            "private_product_tab",
            len(products),
            f"已更新商品页本地缓存，共 {len(products)} 个",
            success=len(products),
        )

    def sync_private_product_metrics(
        self,
        store: Store,
        product_ids: list[str],
        progress: ProgressCallback | None = None,
        cancel_event: threading.Event | None = None,
    ) -> SyncResult:
        private = ShopPrivateClient(self.db, store)
        metrics = private.product_metrics(
            product_ids,
            stop=(cancel_event.is_set if cancel_event else None),
        )
        for product_id, values in metrics.items():
            self.db.upsert_product_metrics(
                store.id,
                product_id,
                views30=values.get("views30"),
                sold30=values.get("sold30"),
                recommend_views30=values.get("recommend_views30"),
            )
        return SyncResult(
            "private_product_metrics",
            len(metrics),
            f"已更新 {len(metrics)} 个商品的30天经营指标",
            success=len(metrics),
        )

    def sync_product_performance(
        self,
        store: Store,
        product_ids: list[str],
        days: int = 30,
        progress: ProgressCallback | None = None,
        cancel_event: threading.Event | None = None,
    ) -> SyncResult:
        ids = [str(value) for value in dict.fromkeys(product_ids) if value]
        if not ids:
            return SyncResult(
                "product_performance",
                0,
                "没有需要更新经营数据的商品",
            )
        updated = 0
        try:
            private = ShopPrivateClient(self.db, store)
            metrics = private.product_metrics(
                ids,
                stop=(cancel_event.is_set if cancel_event else None),
            )
            for product_id, values in metrics.items():
                self.db.upsert_product_metrics(
                    store.id,
                    product_id,
                    views30=values.get("views30"),
                    sold30=values.get("sold30"),
                    recommend_views30=values.get("recommend_views30"),
                )
                updated += 1
        except ShopPrivateError:
            pass
        client = self.client_for(store)
        if isinstance(client, MockShopClient):
            return SyncResult(
                "product_performance",
                updated,
                f"商品经营数据已更新：{updated} 个",
                success=updated,
            )
        span = max(1, min(int(days), 30))
        for day_index in range(span):
            if cancel_event and cancel_event.is_set():
                break
            business_day = (datetime.now() - timedelta(days=day_index + 1)).strftime("%Y%m%d")
            offset = 0
            while True:
                if cancel_event and cancel_event.is_set():
                    break
                result = client.fetch_shop_product_metrics(
                    business_day,
                    offset=offset,
                    limit=200,
                )
                rows = result.get("product_list") or []
                if not isinstance(rows, list):
                    rows = []
                for item in rows:
                    if not isinstance(item, dict):
                        continue
                    product_id = str(item.get("product_id") or "")
                    if product_id not in ids:
                        continue
                    data = item.get("data") or {}
                    if not isinstance(data, dict):
                        continue
                    self.db.upsert_product_metrics(
                        store.id,
                        product_id,
                        click_cnt=int(data.get("product_click_cnt") or 0),
                        click_uv=int(data.get("product_click_uv") or 0),
                        pay_cnt=int(data.get("pay_cnt") or 0),
                        pay_uv=int(data.get("pay_uv") or 0),
                        pay_gmv=int(data.get("pay_gmv") or 0),
                    )
                    updated = max(updated, 1)
                offset += len(rows)
                if len(rows) < 200:
                    break
            if progress:
                progress(
                    f"正在同步商品经营数据 {day_index + 1}/{span} 天",
                    day_index + 1,
                    span,
                )
        message = f"商品经营数据已更新：{len(ids)} 个商品，{span} 天"
        return SyncResult(
            "product_performance",
            len(ids),
            message,
            success=updated,
        )

    def private_batch_product_action(
        self,
        store: Store,
        product_ids: list[str],
        way: str,
    ) -> dict[str, Any]:
        self._require_platform_write(store, "product_web_batch_action")
        private = ShopPrivateClient(self.db, store)
        return private.batch_product_action(product_ids, way)

    def batch_delete_products(
        self,
        store: Store,
        product_ids: list[str],
        *,
        batch_size: int = PRODUCT_BATCH_ACTION_SIZE,
        progress: ProgressCallback | None = None,
        cancel_event: threading.Event | None = None,
        use_private_batch: bool = True,
        hard_delete: bool = False,
    ) -> dict[str, Any]:
        """Recycle or permanently delete products through bounded batches.

        When ``hard_delete`` is false, active products stop in the recycle bin.
        When true, they are moved to the recycle bin and then permanently
        deleted in the same task. Each platform write batch has one bounded
        recycle-list read; the previous per-product full scan is intentionally
        avoided.
        """
        self._require_platform_write(store, "product_web_batch_action")
        ids = list(
            dict.fromkeys(
                str(product_id).strip()
                for product_id in product_ids
                if str(product_id or "").strip()
            )
        )
        if not ids:
            raise ValueError("至少选择一个商品")
        chunk_size = max(1, min(int(batch_size or PRODUCT_BATCH_ACTION_SIZE), 100))
        run_id = self.db.start_sync_run(store.id, "product_delete")
        item_states: dict[str, dict[str, Any]] = {}
        errors: list[str] = []
        cancelled = False

        def record(
            product_id: str,
            status: str,
            message: str,
            *,
            action: str,
            fallback_action: str = "",
        ) -> None:
            row = {
                "store_id": store.id,
                "product_id": product_id,
                "status": status,
                "message": message,
                "action": action,
            }
            if fallback_action:
                row["fallback_action"] = fallback_action
            item_states[product_id] = row
            if status in {"failed", "pending"}:
                errors.append(f"{product_id}: {message}")
            self.db.log_product_operation(
                store.id,
                product_id,
                action,
                status,
                message,
            )

        if (
            store.app_id == "demo-store"
            or not use_private_batch
        ):
            active_ids = [
                product_id
                for product_id in ids
                if int(
                    (
                        self.db.get_product(store.id, product_id)
                        or {}
                    ).get("status")
                    or -1
                )
                != 6
            ]
            recycled_ids = [
                product_id for product_id in ids if product_id not in active_ids
            ]
            if store.app_id == "demo-store":
                for product_id in ids:
                    self.db.remove_product_cache(store.id, product_id)
                    record(
                        product_id,
                        "success",
                        "演示店铺已从本地商品缓存移除",
                        action="彻底删除" if product_id in recycled_ids else "移入回收站",
                    )
            else:
                if active_ids and not (
                    cancel_event is not None and cancel_event.is_set()
                ):
                    try:
                        listing_result = self.set_products_listing(
                            store,
                            active_ids,
                            False,
                            progress=progress,
                            cancel_event=cancel_event,
                        )
                        failed_prefixes = {
                            str(value).split(":", 1)[0]
                            for value in listing_result.errors
                            if ":" in str(value)
                        }
                        fallback_success_remaining = int(
                            listing_result.success or 0
                        )
                        for product_id in active_ids:
                            if product_id in failed_prefixes:
                                record(
                                    product_id,
                                    "failed",
                                    next(
                                        (
                                            value
                                            for value in listing_result.errors
                                            if str(value).startswith(
                                                f"{product_id}:"
                                            )
                                        ),
                                        "安全降级下架失败",
                                    ),
                                    action="移入回收站",
                                    fallback_action="商品下架",
                                )
                            elif listing_result.cancelled:
                                if fallback_success_remaining > 0:
                                    fallback_success_remaining -= 1
                                    record(
                                        product_id,
                                        "failed",
                                        "平台批量删除能力不可用；已安全降级为下架，"
                                        "商品未删除",
                                        action="移入回收站",
                                        fallback_action="商品下架",
                                    )
                                else:
                                    record(
                                        product_id,
                                        "cancelled",
                                        "已取消，安全降级下架未确认",
                                        action="移入回收站",
                                        fallback_action="商品下架",
                                    )
                            else:
                                record(
                                    product_id,
                                    "failed",
                                    "平台批量删除能力不可用；已安全降级为下架，"
                                    "商品未删除",
                                    action="移入回收站",
                                    fallback_action="商品下架",
                                )
                    except Exception as exc:
                        for product_id in active_ids:
                            record(
                                product_id,
                                "pending",
                                "批量删除能力不可用，安全降级下架结果待核实：" + str(exc),
                                action="移入回收站",
                                fallback_action="商品下架",
                            )
                cancelled = bool(
                    cancel_event is not None and cancel_event.is_set()
                )
                for product_id in active_ids:
                    if cancelled and product_id not in item_states:
                        record(
                            product_id,
                            "cancelled",
                            "已取消，未执行平台写操作",
                            action="移入回收站",
                        )
                for product_id in recycled_ids:
                    if cancelled:
                        record(
                            product_id,
                            "cancelled",
                            "已取消，未执行彻底删除",
                            action="彻底删除",
                        )
                    else:
                        record(
                            product_id,
                            "failed",
                            "当前店铺无网页批量删除能力，未执行彻底删除",
                            action="彻底删除",
                        )
        else:
            private = ShopPrivateClient(self.db, store)

            def recycle_presence() -> set[str]:
                products = private.scan_products(
                    "recycle",
                    max_pages=None,
                    page_size=100,
                )
                return {
                    str(product.product_id)
                    for product in products
                    if getattr(product, "product_id", "")
                }

            for offset in range(0, len(ids), chunk_size):
                chunk = ids[offset : offset + chunk_size]
                if cancel_event is not None and cancel_event.is_set():
                    cancelled = True
                    for product_id in ids[offset:]:
                        record(
                            product_id,
                            "cancelled",
                            "已取消，未执行平台写操作",
                            action=(
                                "彻底删除"
                                if int(
                                    (
                                        self.db.get_product(
                                            store.id,
                                            product_id,
                                        )
                                        or {}
                                    ).get("status")
                                    or -1
                                )
                                == 6
                                else "移入回收站"
                            ),
                        )
                    break

                to_recycle: list[str] = []
                to_delete: list[str] = []
                for product_id in chunk:
                    product = self.db.get_product(store.id, product_id) or {}
                    if int(product.get("status") or -1) == 6:
                        to_delete.append(product_id)
                    else:
                        to_recycle.append(product_id)

                recycled_confirmed: list[str] = []
                if to_recycle:
                    try:
                        result = private.batch_product_action(
                            to_recycle,
                            "recycle",
                        )
                        if int(result.get("errcode") or 0) != 0:
                            raise RuntimeError(
                                str(
                                    result.get("errmsg")
                                    or "批量移入回收站接口返回失败"
                                )
                            )
                    except Exception as exc:
                        for product_id in to_recycle:
                            record(
                                product_id,
                                "pending",
                                "移入回收站请求结果未知，缓存已保留，待核实："
                                + str(exc),
                                action="移入回收站",
                            )
                    else:
                        try:
                            present = recycle_presence()
                        except Exception as exc:
                            for product_id in to_recycle:
                                record(
                                    product_id,
                                    "pending",
                                    "移入回收站请求已返回，但回收站回读失败，"
                                    "缓存已保留，待核实：" + str(exc),
                                    action=(
                                        "彻底删除"
                                        if hard_delete
                                        else "移入回收站"
                                    ),
                                )
                        else:
                            recycled_confirmed = [
                                product_id
                                for product_id in to_recycle
                                if product_id in present
                            ]
                            for product_id in to_recycle:
                                if product_id not in present:
                                    record(
                                        product_id,
                                        "failed",
                                        "平台批量请求成功，但回收站未找到商品，"
                                        "不标记删除成功",
                                        action="移入回收站",
                                    )
                            if not hard_delete:
                                for product_id in recycled_confirmed:
                                    self.db.remove_product_cache(
                                        store.id,
                                        product_id,
                                    )
                                    record(
                                        product_id,
                                        "success",
                                        "平台回收站已确认商品",
                                        action="移入回收站",
                                    )
                                recycled_confirmed = []

                real_candidates = [
                    product_id
                    for product_id in recycled_confirmed + to_delete
                    if product_id not in item_states
                ]
                if (
                    cancel_event is not None
                    and cancel_event.is_set()
                    and hard_delete
                ):
                    cancelled = True
                    for product_id in recycled_confirmed:
                        record(
                            product_id,
                            "pending",
                            "已移入回收站、未彻底删除，缓存已保留，待核实",
                            action="彻底删除",
                        )
                    for product_id in to_delete:
                        if product_id not in item_states:
                            record(
                                product_id,
                                "cancelled",
                                "已取消，未执行彻底删除",
                                action="彻底删除",
                            )
                elif hard_delete and real_candidates:
                    try:
                        result = private.batch_product_action(
                            real_candidates,
                            "real",
                        )
                        if int(result.get("errcode") or 0) != 0:
                            raise RuntimeError(
                                str(
                                    result.get("errmsg")
                                    or "批量彻底删除接口返回失败"
                                )
                            )
                    except Exception as exc:
                        for product_id in real_candidates:
                            record(
                                product_id,
                                "pending",
                                "彻底删除请求结果未知，缓存已保留，待核实："
                                + str(exc),
                                action="彻底删除",
                            )
                    else:
                        try:
                            remaining = recycle_presence()
                        except Exception as exc:
                            for product_id in real_candidates:
                                record(
                                    product_id,
                                    "pending",
                                    "彻底删除请求已返回，但回收站回读失败，"
                                    "缓存已保留，待核实：" + str(exc),
                                    action="彻底删除",
                                )
                        else:
                            for product_id in real_candidates:
                                if product_id not in remaining:
                                    self.db.remove_product_cache(
                                        store.id,
                                        product_id,
                                    )
                                    record(
                                        product_id,
                                        "success",
                                        "平台回收站已确认不存在该商品",
                                        action="彻底删除",
                                    )
                                else:
                                    record(
                                        product_id,
                                        "failed",
                                        "平台回收站仍存在商品，彻底删除未生效",
                                        action="彻底删除",
                                    )

                if progress:
                    progress(
                        f"正在批量删除 {min(offset + len(chunk), len(ids))}/{len(ids)}",
                        min(offset + len(chunk), len(ids)),
                        len(ids),
                    )
                if cancel_event is not None and cancel_event.is_set():
                    cancelled = True
                    for product_id in ids[offset + len(chunk):]:
                        record(
                            product_id,
                            "cancelled",
                            "已取消，未执行平台写操作",
                            action=(
                                "彻底删除"
                                if int(
                                    (
                                        self.db.get_product(
                                            store.id,
                                            product_id,
                                        )
                                        or {}
                                    ).get("status")
                                    or -1
                                )
                                == 6
                                else "移入回收站"
                            ),
                        )
                    break

        for product_id in ids:
            if product_id not in item_states:
                record(
                    product_id,
                    "pending",
                    "没有获得明确的平台写入结论，缓存已保留，待核实",
                    action=(
                        "彻底删除"
                        if int(
                            (
                                self.db.get_product(store.id, product_id)
                                or {}
                            ).get("status")
                            or -1
                        )
                        == 6
                        else "移入回收站"
                    ),
                )
        items = [item_states[product_id] for product_id in ids]
        counts = {
            status: sum(
                1 for row in items if row["status"] == status
            )
            for status in ("success", "failed", "cancelled", "pending")
        }
        cancelled = bool(
            cancelled
            and (
                counts["cancelled"] > 0
                or counts["pending"] > 0
            )
        )
        if cancelled:
            status = "cancelled"
        elif counts["pending"]:
            status = "verification_pending"
        elif counts["failed"] and counts["success"]:
            status = "partial"
        elif counts["failed"]:
            status = "failed"
        else:
            status = "completed"
        message = (
            f"批量删除已取消：成功 {counts['success']} 个，"
            f"失败 {counts['failed']} 个，取消 {counts['cancelled']} 个，"
            f"待核实 {counts['pending']} 个"
            if cancelled
            else (
                f"批量删除：成功 {counts['success']} 个，"
                f"失败 {counts['failed']} 个，待核实 {counts['pending']} 个"
            )
        )
        self.db.finish_sync_run(
            run_id,
            status,
            counts["success"],
            message,
        )
        return {
            "cancelled": cancelled,
            "items": items,
            "counts": counts,
            "errors": errors[:100],
            "message": message,
        }

    def promote_products_to_league(
        self,
        store: Store,
        product_ids: list[str],
        ratio_percent: float,
    ) -> dict[str, Any]:
        self._require_platform_write(store, "league_items_add")
        try:
            ratio_percent = float(ratio_percent)
        except (TypeError, ValueError) as exc:
            raise ValueError("联盟佣金比例必须是 0–90 之间的数字") from exc
        if not 0 <= ratio_percent <= 90:
            raise ValueError("联盟佣金比例必须在 0–90% 之间")
        normalized_ids = list(
            dict.fromkeys(
                str(product_id).strip()
                for product_id in product_ids
                if str(product_id or "").strip()
            )
        )
        if not normalized_ids:
            raise ValueError("店铺没有可开通联盟佣金的商品")
        client = self.client_for(store)
        method = getattr(client, "batch_add_league_items", None)
        if not callable(method):
            raise RuntimeError("当前客户端不支持联盟商品推广")
        result = method(
            [
                {
                    "product_id": str(product_id),
                    "ratio": ratio_percent,
                }
                for product_id in normalized_ids
            ]
        )
        result = dict(result)
        result_info_list = result.get("result_info_list") or []
        failed_count = sum(
            1
            for row in result_info_list
            if isinstance(row, dict) and int(row.get("errcode") or 0) != 0
        )
        result.setdefault("requested_count", len(normalized_ids))
        result.setdefault(
            "success_count",
            max(0, len(normalized_ids) - failed_count),
        )
        result.setdefault("failed_count", failed_count)
        self.db.log_product_operation(
            store.id,
            "",
            "league_promote",
            "success" if int(result.get("errcode") or 0) == 0 else "failed",
        )
        return result

    def fetch_active_product_ids(self, store: Store) -> list[str]:
        """Read the complete official in-sale product ID list for one store."""
        client = self.client_for(store)
        method = getattr(client, "fetch_product_ids", None)
        if not callable(method):
            raise RuntimeError("当前客户端不支持读取全店在售商品")
        return list(
            dict.fromkeys(
                str(product_id)
                for product_id in method(status=5)
                if str(product_id or "").strip()
            )
        )

    def fetch_league_products(self, store: Store) -> dict[str, Any]:
        client = self.client_for(store)
        method = getattr(client, "fetch_league_items", None)
        if not callable(method):
            raise RuntimeError("当前客户端不支持读取联盟商品")
        page_size = 100
        page_index = 1
        items: list[dict[str, Any]] = []
        first_result: dict[str, Any] | None = None
        seen: set[str] = set()
        while True:
            result = method(page_size=page_size, page_index=page_index)
            if first_result is None:
                first_result = dict(result)
            page_items = result.get("items") or []
            if not isinstance(page_items, list):
                page_items = []
            added = 0
            for item in page_items:
                if not isinstance(item, dict):
                    continue
                product_id = str(item.get("product_id") or "")
                if product_id and product_id in seen:
                    continue
                if product_id:
                    seen.add(product_id)
                items.append(item)
                added += 1
            total = int(
                result.get("total_num")
                or result.get("total")
                or result.get("total_count")
                or 0
            )
            if (
                not page_items
                or len(page_items) < page_size
                or (total > 0 and len(items) >= total)
                or (page_index > 1 and added == 0)
            ):
                break
            page_index += 1
        merged = dict(first_result or {})
        merged["items"] = items
        merged.setdefault("total_num", len(items))
        return merged

    def update_league_ratio(
        self,
        store: Store,
        product_ids: list[str],
        ratio_percent: float,
    ) -> list[str]:
        self._require_platform_write(store, "league_item_update")
        client = self.client_for(store)
        fetch_detail = getattr(client, "fetch_league_item", None)
        update_item = getattr(client, "update_league_item", None)
        if not callable(fetch_detail) or not callable(update_item):
            raise RuntimeError("当前客户端不支持更新联盟佣金")
        errors: list[str] = []
        for product_id in product_ids:
            try:
                detail = fetch_detail(str(product_id))
                plan_id = str(detail.get("promotion_plan_id") or detail.get("plan_id") or "")
                if not plan_id:
                    errors.append(f"{product_id}: 没有推广计划 ID")
                    continue
                result = update_item(
                    str(product_id),
                    plan_id,
                    float(ratio_percent),
                )
                if int(result.get("errcode") or 0) != 0:
                    errors.append(f"{product_id}: {result.get('errmsg') or '更新失败'}")
            except Exception as exc:
                errors.append(f"{product_id}: {exc}")
        return errors

    def update_product_prices(
        self,
        store: Store,
        product_ids: list[str],
        multiplier: float,
    ) -> list[str]:
        self._require_platform_write(store, "product_price_update")
        client = self.client_for(store)
        method = getattr(client, "update_product_prices", None)
        if not callable(method):
            raise RuntimeError("当前客户端不支持商品改价")
        errors: list[str] = []
        for product_id in product_ids:
            try:
                result = method(str(product_id), float(multiplier))
                if int(result.get("errcode") or 0) != 0:
                    errors.append(f"{product_id}: {result.get('errmsg') or '改价失败'}")
            except Exception as exc:
                errors.append(f"{product_id}: {exc}")
        return errors

    def create_snapup_activity(
        self,
        store: Store,
        product_ids: list[str],
        discount: float,
        *,
        start_time: int | None = None,
        end_time: int | None = None,
        min_discount_cents: int = 100,
        limit_purchase_num: int = 0,
        stock_limit_per_sku: int = 0,
        create_all: bool = False,
    ) -> dict[str, Any]:
        self._require_platform_write(store, "activity_create")
        try:
            discount = float(discount)
        except (TypeError, ValueError) as exc:
            raise ValueError("限时抢购折扣必须是 0.1–0.85 之间的数字") from exc
        if not 0.1 <= discount <= 0.85:
            raise ValueError("平台规则要求限时抢购折扣必须在 1–8.5 折之间")
        min_discount_cents = int(min_discount_cents)
        if min_discount_cents < 100:
            raise ValueError("平台规则要求最低优惠不能低于 1 元")
        limit_purchase_num = max(0, int(limit_purchase_num))
        stock_limit_per_sku = max(0, int(stock_limit_per_sku))
        if stock_limit_per_sku > 0:
            raise ValueError(
                "当前官方限时抢购接口禁止设置抢购库存，"
                "请将商品库存上限设为 0，沿用 SKU 当前库存"
            )
        now = int(time.time())
        start = int(start_time or (now + 300))
        end = int(end_time or (start + 7 * 86400))
        if end <= start:
            raise ValueError("限时抢购结束时间必须晚于开始时间")
        if end - start > 7 * 86400:
            raise ValueError("限时抢购持续时间不能超过 7 天")

        normalized_ids = list(
            dict.fromkeys(str(product_id) for product_id in product_ids if product_id)
        )
        if not normalized_ids:
            raise RuntimeError("没有可参与活动的在售商品")

        private = ShopPrivateClient(self.db, store)
        already_active_ids: set[str] = set()
        list_existing = getattr(private, "snapup_activities", None)
        if create_all and callable(list_existing):
            for activity in list_existing():
                try:
                    activity_status = int(activity.get("status") or 0)
                except (TypeError, ValueError):
                    activity_status = 0
                if activity_status not in {0, 1}:
                    continue
                extra = activity.get("extra") or {}
                products = (
                    extra.get("snapupProducts") or []
                    if isinstance(extra, dict)
                    else []
                )
                for product in products:
                    if not isinstance(product, dict):
                        continue
                    product_id = str(product.get("productId") or "")
                    if product_id:
                        already_active_ids.add(product_id)
        candidate_ids = [
            product_id
            for product_id in normalized_ids
            if product_id not in already_active_ids
        ]
        if not candidate_ids:
            return {
                "activity_id": "",
                "activity_count": 0,
                "activities": [],
                "product_count": 0,
                "sku_count": 0,
                "requested_count": len(normalized_ids),
                "skipped_count": 0,
                "invalid_skipped_count": 0,
                "capacity_skipped_count": 0,
                "already_active_count": len(already_active_ids),
                "failed_count": 0,
                "truncated": False,
                "sku_limit_reached": False,
                "batch_count": 0,
                "snapup_type": 1,
                "start_time": start,
                "end_time": end,
            }
        products = private.fetch_snapup_products(candidate_ids)
        products_by_id = {
            str(
                (product.get("productKey") or {}).get("productId")
                or ""
            ): product
            for product in products
            if isinstance(product, dict)
        }
        eligible_products: list[dict[str, Any]] = []
        invalid_skipped_count = 0
        for order, product_id in enumerate(candidate_ids):
            product = products_by_id.get(product_id)
            if not product:
                invalid_skipped_count += 1
                continue
            try:
                product_status = int(product.get("status") or 0)
            except (TypeError, ValueError):
                product_status = 0
            if product_status != 5:
                invalid_skipped_count += 1
                continue
            snapup_skus: list[dict[str, str]] = []
            for sku in product.get("productSkus") or []:
                if not isinstance(sku, dict):
                    continue
                try:
                    sku_status = int(sku.get("status") or 5)
                except (TypeError, ValueError):
                    sku_status = 5
                sku_info = sku.get("productSkuInfo") or {}
                stock_info = (
                    sku_info.get("stockInfo")
                    if isinstance(sku_info, dict)
                    else {}
                )
                try:
                    sale_price = int(sku_info.get("salePrice") or 0)
                    sale_stock = int((stock_info or {}).get("stockNum") or 0)
                except (TypeError, ValueError):
                    continue
                sku_id = str((sku.get("skuKey") or {}).get("skuId") or "")
                if (
                    sku_status != 5
                    or not sku_id
                    or sale_price <= 0
                    or sale_stock <= 0
                ):
                    continue
                activity_price = _discount_price_cents(sale_price, discount)
                maximum_price = sale_price - min_discount_cents
                minimum_price = _discount_price_cents(sale_price, Decimal("0.1"))
                activity_price = max(
                    minimum_price,
                    min(activity_price, maximum_price),
                )
                if (
                    activity_price >= sale_price
                    or sale_price - activity_price < min_discount_cents
                ):
                    continue
                snapup_skus.append(
                    {
                        "skuId": sku_id,
                        "snapupPrice": str(activity_price),
                        "snapupStock": "0",
                    }
                )
            if snapup_skus:
                eligible_products.append(
                    {
                        "productId": product_id,
                        "snapupSkus": snapup_skus,
                        "_order": order,
                    }
                )
            else:
                invalid_skipped_count += 1
        if not eligible_products and already_active_ids:
            return {
                "activity_id": "",
                "activity_count": 0,
                "activities": [],
                "product_count": 0,
                "sku_count": 0,
                "requested_count": len(normalized_ids),
                "skipped_count": invalid_skipped_count,
                "invalid_skipped_count": invalid_skipped_count,
                "capacity_skipped_count": 0,
                "already_active_count": len(already_active_ids),
                "failed_count": 0,
                "truncated": False,
                "sku_limit_reached": False,
                "batch_count": 0,
                "snapup_type": 1,
                "start_time": start,
                "end_time": end,
            }
        if not eligible_products:
            raise RuntimeError("没有可参与活动的在售且库存大于 0 的商品")

        eligible_products.sort(
            key=lambda item: (len(item["snapupSkus"]), item["_order"])
        )
        batches: list[list[dict[str, Any]]] = []
        remaining = eligible_products
        capacity_skipped_count = 0
        while remaining:
            batch: list[dict[str, Any]] = []
            batch_sku_count = 0
            next_remaining: list[dict[str, Any]] = []
            for item in remaining:
                item_sku_count = len(item["snapupSkus"])
                if item_sku_count > 2000:
                    capacity_skipped_count += 1
                    continue
                if (
                    len(batch) < 200
                    and batch_sku_count + item_sku_count <= 2000
                ):
                    batch.append(item)
                    batch_sku_count += item_sku_count
                else:
                    next_remaining.append(item)
            if not batch:
                capacity_skipped_count += len(next_remaining)
                break
            batches.append(batch)
            remaining = next_remaining

        batches_to_create = batches if create_all else batches[:1]
        activities: list[dict[str, Any]] = []
        start_dt = datetime.fromtimestamp(start, SHANGHAI_TZ)
        for batch_index, batch in enumerate(batches_to_create, start=1):
            snapup_products = [
                {
                    "productId": item["productId"],
                    "snapupSkus": item["snapupSkus"],
                }
                for item in batch
            ]
            title_suffix = (
                f" {batch_index}"
                if len(batches_to_create) > 1
                else ""
            )
            payload = {
                "title": f"澜琅限时7天 {start_dt:%m/%d}{title_suffix}",
                "snapupProducts": snapup_products,
                "startTime": start,
                "endTime": end,
                "snapupType": 1,
                "limitPurchaseNum": limit_purchase_num,
            }
            try:
                response = private.create_snapup_activity(payload)
            except ShopPrivateError as exc:
                activities.append(
                    {
                        "ok": False,
                        "batch_index": batch_index,
                        "product_count": len(snapup_products),
                        "sku_count": sum(
                            len(item["snapupSkus"])
                            for item in batch
                        ),
                        "error": str(exc),
                    }
                )
                if not create_all:
                    raise
                continue
            data = (
                response.get("data")
                if isinstance(response.get("data"), dict)
                else {}
            )
            activity_id = str(
                response.get("activityId")
                or data.get("activityId")
                or ""
            )
            activities.append(
                {
                    "ok": True,
                    "batch_index": batch_index,
                    "activity_id": activity_id,
                    "product_count": len(snapup_products),
                    "sku_count": sum(
                        len(item["snapupSkus"])
                        for item in batch
                    ),
                    "response": response,
                }
            )
        successful = [item for item in activities if item["ok"]]
        failed = [item for item in activities if not item["ok"]]
        skipped_count = (
            invalid_skipped_count
            + capacity_skipped_count
            + sum(
                len(batch)
                for batch in batches[len(batches_to_create) :]
            )
        )
        return {
            "activity_id": (
                successful[0]["activity_id"] if successful else ""
            ),
            "activity_count": len(successful),
            "activities": activities,
            "product_count": sum(
                item["product_count"] for item in successful
            ),
            "sku_count": sum(item["sku_count"] for item in successful),
            "requested_count": len(normalized_ids),
            "skipped_count": skipped_count,
            "invalid_skipped_count": invalid_skipped_count,
            "capacity_skipped_count": capacity_skipped_count,
            "already_active_count": len(already_active_ids),
            "failed_count": len(failed),
            "truncated": len(batches) > len(batches_to_create),
            "sku_limit_reached": len(batches) > len(batches_to_create),
            "batch_count": len(batches),
            "snapup_type": 1,
            "start_time": start,
            "end_time": end,
        }

    def delete_snapup_activity(
        self,
        store: Store,
        activity_id: str,
        *,
        stop_if_active: bool = True,
    ) -> dict[str, Any]:
        self._require_platform_write(store, "activity_delete")
        private = ShopPrivateClient(self.db, store)
        if stop_if_active:
            task = private.snapup_activity(activity_id)
            try:
                status = int(task.get("status") or 0)
            except (TypeError, ValueError):
                status = 0
            if status == 1:
                private.stop_snapup_activity(activity_id)
        return private.delete_snapup_activity(activity_id)

    def create_limited_discount_tasks(
        self,
        store: Store,
        product_ids: list[str],
        discount: float,
        *,
        start_time: int | None = None,
        end_time: int | None = None,
        min_discount_cents: int = 100,
    ) -> list[str]:
        self._require_platform_write(store, "limited_discount_create")
        client = self.client_for(store)
        fetch_product = getattr(client, "fetch_product_payload", None)
        create_task = getattr(client, "create_limited_discount_task", None)
        if not callable(fetch_product) or not callable(create_task):
            raise RuntimeError("当前客户端不支持限时抢购")
        try:
            discount = float(discount)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "限时抢购折扣倍率必须是数字，0.85表示8.5折"
            ) from exc
        if not 0.1 <= discount <= 0.85:
            raise ValueError(
                "平台规则要求限时抢购折扣必须在 1–8.5 折之间，"
                "0.85表示8.5折"
            )
        min_discount_cents = int(min_discount_cents)
        if min_discount_cents < 100:
            raise ValueError("平台规则要求最低优惠不能低于 1 元")
        now = int(time.time())
        start = int(start_time or (now + 300))
        end = int(end_time or (start + 7 * 86400))
        if end <= start:
            raise ValueError("限时抢购结束时间必须晚于开始时间")
        if end - start > 7 * 86400:
            raise ValueError("限时抢购持续时间不能超过 7 天")
        errors: list[str] = []
        for product_id in product_ids:
            try:
                detail = fetch_product(str(product_id), data_type=3)
                product = detail.get("edit_product") or detail.get("product")
                if not isinstance(product, dict):
                    product = {}
                skus = []
                for sku in product.get("skus") or []:
                    if not isinstance(sku, dict):
                        continue
                    try:
                        sale_price = int(sku.get("sale_price") or 0)
                        sale_stock = int(sku.get("stock_num") or 0)
                    except (TypeError, ValueError):
                        continue
                    sku_id = str(sku.get("sku_id") or "")
                    if not sku_id or sale_price <= 0 or sale_stock <= 0:
                        continue
                    activity_price = _discount_price_cents(
                        sale_price,
                        discount,
                    )
                    if min_discount_cents:
                        activity_price = min(
                            activity_price,
                            max(1, sale_price - min_discount_cents),
                        )
                    skus.append(
                        {
                            "sku_id": sku_id,
                            "sale_price": activity_price,
                            "sale_stock": sale_stock,
                        }
                    )
                if not skus:
                    errors.append(f"{product_id}: 没有可参与抢购的 SKU")
                    continue
                result = create_task(
                    {
                        "product_id": str(product_id),
                        "start_time": start,
                        "end_time": end,
                        "limited_discount_skus": skus,
                        "title": "澜琅托管限时抢购",
                    }
                )
                if int(result.get("errcode") or 0) != 0:
                    errors.append(f"{product_id}: {result.get('errmsg') or '创建失败'}")
            except Exception as exc:
                errors.append(f"{product_id}: {exc}")
        return errors

    def stop_active_limited_discounts(
        self,
        store: Store,
    ) -> list[str]:
        self._require_platform_write(store, "limited_discount_stop")
        client = self.client_for(store)
        fetch_tasks = getattr(client, "fetch_limited_discount_tasks", None)
        stop_task = getattr(client, "stop_limited_discount_task", None)
        if not callable(fetch_tasks) or not callable(stop_task):
            raise RuntimeError("当前客户端不支持停止限时抢购")
        result = fetch_tasks(status=1, page_size=50)
        tasks = result.get("limited_discount_tasks") or []
        errors: list[str] = []
        for task in tasks:
            if not isinstance(task, dict):
                continue
            task_id = str(task.get("task_id") or "")
            if not task_id:
                continue
            try:
                stopped = stop_task(task_id)
                if int(stopped.get("errcode") or 0) != 0:
                    errors.append(f"{task_id}: {stopped.get('errmsg') or '停止失败'}")
            except Exception as exc:
                errors.append(f"{task_id}: {exc}")
        return errors

    def set_products_listing(
        self,
        store: Store,
        product_ids: list[str],
        listed: bool,
        progress: ProgressCallback | None = None,
        workers: int = 4,
        cancel_event: threading.Event | None = None,
    ) -> SyncResult:
        self._require_platform_write(store, "product_listing")
        action = "listing" if listed else "delisting"
        private = None
        if (
            not listed
            and store.app_id != "demo-store"
            and not self.db.get_store_secret(store.id)[1]
        ):
            private = ShopPrivateClient(self.db, store)
            client = None
            workers = 1
        else:
            client = self.client_for(store)
        run_id = self.db.start_sync_run(store.id, action)
        total = len(product_ids)
        success_count = 0
        failures: list[str] = []
        cancelled = False
        status_code = 5 if listed else 11
        status_text = PRODUCT_STATUS[status_code]

        def apply_one(product_id: str) -> tuple[str, str | None]:
            if cancel_event and cancel_event.is_set():
                return product_id, "cancelled"
            try:
                if private is not None:
                    result = private.batch_product_action([product_id], "offshelf")
                elif isinstance(client, MockShopClient):
                    result = {"errcode": 0}
                else:
                    result = client.set_product_listing(product_id, listed)
                if int(result.get("errcode") or 0) != 0:
                    raise RuntimeError(str(result.get("errmsg") or "接口返回失败"))
                self.db.mark_product_status(
                    store.id,
                    product_id,
                    status_code,
                    status_text,
                )
                self.db.log_product_operation(
                    store.id,
                    product_id,
                    action,
                    "success",
                )
                return product_id, None
            except Exception as exc:
                message = str(exc)
                self.db.log_product_operation(
                    store.id,
                    product_id,
                    action,
                    "failed",
                    message,
                )
                return product_id, message

        executor = ThreadPoolExecutor(max_workers=max(1, min(workers, 8)))
        futures = {executor.submit(apply_one, product_id): product_id for product_id in product_ids}
        try:
            for index, future in enumerate(as_completed(futures), 1):
                if cancel_event and cancel_event.is_set():
                    cancelled = True
                    for pending in futures:
                        pending.cancel()
                if future.cancelled():
                    continue
                try:
                    product_id, error = future.result()
                except CancelledError:
                    continue
                if error is None:
                    success_count += 1
                elif error != "cancelled":
                    failures.append(f"{product_id}: {error}")
                if progress:
                    progress(
                        f"正在批量{'上架' if listed else '下架'} {index}/{total}",
                        index,
                        total,
                    )
        finally:
            executor.shutdown(wait=True, cancel_futures=True)

        message = f"批量{'上架' if listed else '下架'}：成功 {success_count}，失败 {len(failures)}"
        status = "cancelled" if cancelled else ("success" if not failures else "partial")
        self.db.finish_sync_run(run_id, status, success_count, message)
        return SyncResult(
            action,
            total,
            message,
            success=success_count,
            failed=len(failures),
            cancelled=cancelled,
            errors=failures[:20],
        )

    def update_product_stock(
        self,
        store: Store,
        product_id: str,
        sku_id: str,
        diff_type: int,
        amount: int,
    ) -> Product | None:
        self._require_platform_write(store, "product_stock_update")
        if diff_type not in {1, 2, 3}:
            raise ValueError("库存修改类型必须是 1、2 或 3")
        if amount < 0 or (diff_type != 3 and amount == 0):
            raise ValueError("库存调整数量不正确")
        client = self.client_for(store)
        if isinstance(client, MockShopClient):
            product_row = self.db.get_product(store.id, product_id)
            if product_row:
                import json

                payload = json.loads(product_row["raw_json"] or "{}")
                skus = payload.get("skus") or []
                sku = next(
                    (item for item in skus if str(item.get("sku_id")) == str(sku_id)),
                    None,
                )
                if sku is not None:
                    current = int(sku.get("stock_num") or 0)
                    new_stock = (
                        current + amount
                        if diff_type == 1
                        else current - amount
                        if diff_type == 2
                        else amount
                    )
                    self.db.set_product_stock_cache(
                        store.id,
                        product_id,
                        sku_id,
                        max(0, new_stock),
                    )
            self.db.log_product_operation(store.id, product_id, "stock", "success")
            return None
        try:
            result = client.update_product_stock(
                product_id,
                sku_id,
                diff_type,
                amount,
            )
            if int(result.get("errcode") or 0) != 0:
                raise RuntimeError(str(result.get("errmsg") or "接口返回失败"))
        except Exception as exc:
            self.db.log_product_operation(
                store.id,
                product_id,
                "stock",
                "failed",
                str(exc),
            )
            raise
        self.db.log_product_operation(store.id, product_id, "stock", "success")
        try:
            product = client.fetch_product(store, product_id)
            self.db.upsert_product(product)
            return product
        except Exception:
            # Stock write succeeded; keep the accepted result even if the
            # best-effort cache refresh fails.
            return None

    def refresh_shipping_options(
        self,
        store: Store,
        progress: ProgressCallback | None = None,
    ) -> SyncResult:
        run_id = self.db.start_sync_run(store.id, "shipping_options")
        try:
            client = self.client_for(store)
            if isinstance(client, MockShopClient):
                account = ShippingAccount(
                    store_id=store.id,
                    delivery_id="SF",
                    delivery_name="顺丰速运",
                    acct_id="demo-acct",
                    shop_id="demo-shop",
                    site_code="DEMO",
                    site_name="演示网点",
                    status=3,
                    available=100,
                    monthly_card="",
                    sender_name="演示寄件人",
                    sender_mobile="13800000000",
                    sender_address={
                        "province": "湖北省",
                        "city": "武汉市",
                        "county": "青山区",
                        "street": "示范街道",
                        "address": "演示仓",
                    },
                    raw={"demo": True},
                )
                template = ShippingTemplate(
                    store_id=store.id,
                    delivery_id="SF",
                    template_id="demo-template",
                    template_name="演示一联单",
                    template_type="single",
                    is_default=True,
                    raw={"demo": True},
                )
                accounts = [account]
                templates = [template]
            else:
                delivery_rows = client.fetch_ewaybill_delivery_companies(status=3)
                delivery_names = {
                    str(item.get("delivery_id") or ""): str(item.get("delivery_name") or "")
                    for item in delivery_rows
                }
                if progress:
                    progress("正在获取电子面单账号库存…", 0, 0)
                account_rows = client.fetch_ewaybill_accounts(
                    need_balance=True,
                    page_size=10,
                )
                accounts = [
                    parse_shipping_account(
                        store,
                        item,
                        delivery_names.get(
                            str(item.get("delivery_id") or ""),
                            str(item.get("delivery_id") or ""),
                        ),
                    )
                    for item in account_rows
                ]
                if progress:
                    progress("正在获取电子面单模板…", 0, 0)
                template_rows = client.fetch_ewaybill_templates()
                templates = [parse_shipping_template(store, item) for item in template_rows]

            for account in accounts:
                self.db.upsert_shipping_account(account)
            for template in templates:
                self.db.upsert_shipping_template(template)

            config = self.db.get_shipping_config(store.id)
            if config is None and accounts:
                account = next((item for item in accounts if item.status == 3), accounts[0])
                selected_template = next(
                    (
                        item
                        for item in templates
                        if item.delivery_id == account.delivery_id and item.is_default
                    ),
                    next(
                        (item for item in templates if item.delivery_id == account.delivery_id),
                        None,
                    ),
                )
                self.db.save_shipping_config(
                    store.id,
                    {
                        "name": account.sender_name,
                        "mobile": account.sender_mobile,
                        **account.sender_address,
                    },
                    account.acct_id,
                    account.delivery_id,
                    selected_template.template_id if selected_template else "",
                )
            message = f"物流配置已刷新：{len(accounts)} 个面单账号，{len(templates)} 个模板"
            self.db.finish_sync_run(
                run_id,
                "success",
                len(accounts) + len(templates),
                message,
            )
            return SyncResult(
                "shipping_options",
                len(accounts),
                message,
                success=len(accounts),
            )
        except Exception as exc:
            self.db.finish_sync_run(run_id, "failed", 0, str(exc))
            raise

    def create_shipment(self, store: Store, order_id: str) -> dict:
        self._require_platform_write(store, "ewaybill_create")
        order = self.db.get_order(store.id, order_id)
        if order is None:
            raise ValueError("订单不存在")
        config = self.db.get_shipping_config(store.id)
        if config is None:
            raise ValueError("请先保存发货人和面单账号设置")
        accounts = self.db.list_shipping_accounts(store.id)
        account = next(
            (item for item in accounts if item["acct_id"] == config["default_acct_id"]),
            None,
        )
        if account is None:
            raise ValueError("默认面单账号不存在，请刷新物流配置")
        self._validate_receiver(order)
        sender = {
            "name": str(config["sender_name"]),
            "mobile": str(config["sender_mobile"]),
            "province": str(config["sender_province"]),
            "city": str(config["sender_city"]),
            "county": str(config["sender_county"]),
            "street": str(config["sender_street"]),
            "address": str(config["sender_address"]),
        }
        missing = [key for key, value in sender.items() if not value]
        if missing:
            raise ValueError("发货地址未填写完整")
        raw = order.raw
        address_info = raw.get("order_detail", {}).get("delivery_info", {}).get("address_info", {})
        receiver = {
            "name": order.buyer_name,
            "mobile": order.phone.split("-", 1)[0],
            "province": str(address_info.get("province_name") or ""),
            "city": str(address_info.get("city_name") or ""),
            "county": str(address_info.get("county_name") or ""),
            "street": str(address_info.get("street_name") or ""),
            "address": str(address_info.get("detail_info") or ""),
        }
        goods_list = [
            {
                "good_name": item.title,
                "good_count": item.sku_count,
                "product_id": int(item.product_id)
                if item.product_id.isdigit()
                else item.product_id,
                "sku_id": int(item.sku_id) if item.sku_id.isdigit() else item.sku_id,
            }
            for item in order.items
        ]
        payload: dict = {
            "delivery_id": account["delivery_id"],
            "ewaybill_acct_id": account["acct_id"],
            "sender": sender,
            "receiver": receiver,
            "ec_order_list": [
                {
                    "ec_order_id": int(order.order_id)
                    if order.order_id.isdigit()
                    else order.order_id,
                    "goods_list": goods_list,
                }
            ],
            "shop_id": account["shop_id"],
        }
        if account["site_code"]:
            payload["site_code"] = account["site_code"]
        template_id = str(config["default_template_id"] or "")
        if template_id:
            payload["template_id"] = template_id
        else:
            payload["template_type"] = "single"

        client = self.client_for(store)
        if isinstance(client, MockShopClient):
            ewaybill_order_id = f"DEMO{order_id}"
            create_result = {
                "ewaybill_order_id": ewaybill_order_id,
                "waybill_id": f"SFDEMO{order_id[-8:]}",
                "print_info": json.dumps(
                    {"demo": True, "order_id": order_id},
                    ensure_ascii=False,
                ),
            }
        else:
            precreate = client.precreate_ewaybill(payload)
            ewaybill_order_id = str(precreate.get("ewaybill_order_id") or "")
            if not ewaybill_order_id:
                raise RuntimeError("预取号接口没有返回 ewaybill_order_id")
            self.db.upsert_shipment(
                store.id,
                order_id,
                ewaybill_order_id,
                account["delivery_id"],
                account["acct_id"],
                template_id,
                "",
                "precreated",
            )
            create_payload = dict(payload)
            create_payload["ewaybill_order_id"] = ewaybill_order_id
            try:
                create_result = client.create_ewaybill(create_payload)
            except Exception as exc:
                self.db.upsert_shipment(
                    store.id,
                    order_id,
                    ewaybill_order_id,
                    account["delivery_id"],
                    account["acct_id"],
                    template_id,
                    "",
                    "create_failed",
                    error=str(exc),
                )
                raise

        order_info = create_result.get("order_info") or {}
        waybill_id = str(create_result.get("waybill_id") or order_info.get("waybill_id") or "")
        if not waybill_id:
            raise RuntimeError("取号接口没有返回运单号")
        print_info = create_result.get("print_info") or order_info.get("print_info") or ""
        if not isinstance(print_info, str):
            print_info = json.dumps(print_info, ensure_ascii=False)
        self.db.upsert_shipment(
            store.id,
            order_id,
            ewaybill_order_id,
            account["delivery_id"],
            account["acct_id"],
            template_id,
            waybill_id,
            "created",
            print_info=print_info,
        )
        self.db.update_order_delivery(
            store.id,
            order_id,
            waybill_id,
            account["delivery_name"],
            order.status,
            order.status_text,
        )
        return self.db.get_shipment(store.id, order_id) or {}

    def complete_shipment(self, store: Store, order_id: str) -> dict:
        self._require_platform_write(store, "order_delivery_send")
        shipment = self.db.get_shipment(store.id, order_id)
        if shipment is None or not shipment["waybill_id"]:
            raise ValueError("请先生成电子面单")
        order = self.db.get_order(store.id, order_id)
        if order is None:
            raise ValueError("订单不存在")
        client = self.client_for(store)
        if not isinstance(client, MockShopClient):
            client.confirm_ewaybill_printed(
                shipment["ewaybill_order_id"],
                shipment["delivery_id"],
                shipment["waybill_id"],
            )
            self.db.upsert_shipment(
                store.id,
                order_id,
                shipment["ewaybill_order_id"],
                shipment["delivery_id"],
                shipment["acct_id"],
                shipment["template_id"],
                shipment["waybill_id"],
                "printed",
                shipment["print_info"],
            )
            delivery_list = [
                {
                    "waybill_id": shipment["waybill_id"],
                    "delivery_id": shipment["delivery_id"],
                    "product_infos": [
                        {
                            "product_id": item.product_id,
                            "sku_id": item.sku_id,
                            "product_cnt": item.sku_count,
                        }
                        for item in order.items
                    ],
                    "deliver_type": 1,
                }
            ]
            client.send_order_delivery(order_id, delivery_list)
        self.db.update_order_delivery(
            store.id,
            order_id,
            shipment["waybill_id"],
            shipment["delivery_id"],
            21,
            order_status_text(21),
        )
        self.db.upsert_shipment(
            store.id,
            order_id,
            shipment["ewaybill_order_id"],
            shipment["delivery_id"],
            shipment["acct_id"],
            shipment["template_id"],
            shipment["waybill_id"],
            "shipped",
            shipment["print_info"],
        )
        return self.db.get_shipment(store.id, order_id) or {}

    @staticmethod
    def _validate_receiver(order: Order) -> None:
        values = (order.buyer_name, order.phone, order.address)
        if not all(values) or "*" in "".join(values):
            raise ValueError("订单收货信息仍被隐藏，请先在订单页解密收货信息")

    def decode_order_sensitive(self, store: Store, order_id: str) -> None:
        client = self.client_for(store)
        result = client.decode_sensitive_info(order_id)
        address_info = result.get("address_info") or {}
        virtual_info = result.get("virtual_number_info") or {}
        phone = str(virtual_info.get("virtual_number") or address_info.get("tel_number") or "")
        extension = str(virtual_info.get("extension") or "")
        if phone and extension:
            phone = f"{phone}-{extension}"
        province = str(address_info.get("province_name") or "")
        city = str(address_info.get("city_name") or "")
        county = str(address_info.get("county_name") or "")
        detail = str(address_info.get("detail_info") or "")
        address = "".join(
            dict.fromkeys(value for value in (province, city, county, detail) if value)
        )
        self.db.update_order_sensitive(
            store.id,
            order_id,
            str(address_info.get("user_name") or ""),
            phone,
            address,
            {
                "province": province,
                "city": city,
                "county": county,
                "street": str(address_info.get("street_name") or ""),
                "address": detail,
            },
        )

    def sync_store_profile(self, store: Store) -> SyncResult:
        client = self.client_for(store)
        info = client.fetch_store_info()
        self.db.upsert_store_profile(store.id, info)
        profile_name = str(info.get("nickname") or "").strip()
        if profile_name:
            self.db.replace_placeholder_store_name(
                store.id,
                store.app_id,
                profile_name,
            )
        return SyncResult(
            "store_profile",
            1,
            f"店铺资料已更新：{profile_name or store.name}",
            success=1,
        )

    def sync_after_sales(
        self,
        store: Store,
        start_time: int,
        end_time: int,
        progress: ProgressCallback | None = None,
        time_field: str = "create",
    ) -> SyncResult:
        if end_time - start_time > 86400:
            total = 0
            success = 0
            failed = 0
            errors: list[str] = []
            chunk_start = int(start_time)
            while chunk_start < int(end_time):
                chunk_end = min(int(end_time), chunk_start + 86400)
                result = self.sync_after_sales(
                    store,
                    chunk_start,
                    chunk_end,
                    progress=progress,
                    time_field=time_field,
                )
                total += result.count
                success += result.success
                failed += result.failed
                errors.extend(result.errors)
                chunk_start = chunk_end
            return SyncResult(
                "after_sales",
                total,
                f"售后同步完成：成功 {success}，失败 {failed}",
                success=success,
                failed=failed,
                errors=errors[:20],
            )
        client = self.client_for(store)
        ids = client.fetch_after_sale_ids(
            start_time,
            end_time,
            time_field=time_field,
        )
        failures = []
        for index, after_sale_id in enumerate(ids, 1):
            if progress:
                progress(f"正在拉取售后单 {index}/{len(ids)}", index, len(ids))
            try:
                payload = client.fetch_after_sale(after_sale_id)
                self.db.upsert_after_sale(store.id, payload)
            except Exception as exc:
                failures.append(f"{after_sale_id}: {exc}")
        message = f"售后同步完成：成功 {len(ids) - len(failures)}，失败 {len(failures)}"
        return SyncResult(
            "after_sales",
            len(ids),
            message,
            success=len(ids) - len(failures),
            failed=len(failures),
            errors=failures[:20],
        )

    def sync_analytics_day(
        self,
        store: Store,
        business_date: str,
    ) -> SyncResult:
        client = self.client_for(store)
        data = client.fetch_shop_overview(business_date.replace("-", ""))
        self.db.upsert_analytics_day(store.id, business_date, data)
        return SyncResult(
            "analytics",
            1,
            f"{business_date} 经营数据已更新",
            success=1,
        )

    def sync_analytics_range(
        self,
        store: Store,
        start_date: str,
        end_date: str,
        progress: ProgressCallback | None = None,
    ) -> SyncResult:
        start = datetime.strptime(start_date, "%Y-%m-%d").date()
        end = datetime.strptime(end_date, "%Y-%m-%d").date()
        if end < start:
            raise ValueError("结束日期不能早于开始日期")
        days = (end - start).days + 1
        failures = 0
        for index in range(days):
            if progress:
                progress(
                    f"正在同步经营数据 {index + 1}/{days}",
                    index,
                    days,
                )
            day = (start + timedelta(days=index)).isoformat()
            try:
                self.sync_analytics_day(store, day)
            except Exception:
                failures += 1
        return SyncResult(
            "analytics_range",
            days,
            f"经营数据同步完成：成功 {days - failures}，失败 {failures}",
            success=days - failures,
            failed=failures,
        )

    def sync_coupons(
        self,
        store: Store,
        progress: ProgressCallback | None = None,
    ) -> SyncResult:
        client = self.client_for(store)
        if isinstance(client, MockShopClient):
            return SyncResult("coupons", 0, "演示店铺暂无优惠券")
        ids = []
        for status in (1, 2, 3, 4):
            ids.extend(client.fetch_coupon_ids(status))
        ids = list(dict.fromkeys(ids))
        failures = []
        for index, coupon_id in enumerate(ids, 1):
            if progress:
                progress(f"正在拉取优惠券 {index}/{len(ids)}", index, len(ids))
            try:
                self.db.upsert_coupon(store.id, client.fetch_coupon(coupon_id))
            except Exception as exc:
                failures.append(f"{coupon_id}: {exc}")
        return SyncResult(
            "coupons",
            len(ids),
            f"优惠券同步完成：成功 {len(ids) - len(failures)}，失败 {len(failures)}",
            success=len(ids) - len(failures),
            failed=len(failures),
            errors=failures[:20],
        )

    def coupon_write(
        self,
        store: Store,
        action: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        client = self.client_for(store)
        if isinstance(client, MockShopClient):
            return {"errcode": 0, "demo": True}
        self._require_platform_write(store, f"coupon_{action}")
        if action == "create":
            result = client.create_coupon(payload)
        elif action == "update":
            result = client.update_coupon(payload)
        elif action == "status":
            result = client.update_coupon_status(payload)
        else:
            raise ValueError("不支持的优惠券操作")
        coupon_id = str(result.get("coupon_id") or payload.get("coupon_id") or "")
        if coupon_id:
            try:
                self.db.upsert_coupon(store.id, client.fetch_coupon(coupon_id))
            except Exception:
                pass
        return result

    def sync_storefront(self, store: Store) -> list[dict[str, Any]]:
        client = self.client_for(store)
        if isinstance(client, MockShopClient):
            return [
                {
                    "product_id": f"P{index:03d}",
                    "title": f"演示商品 {index}",
                    "status_text": "上架",
                }
                for index in range(1, 5)
            ]
        ids = client.fetch_storefront_product_ids()
        result = []
        for product_id in ids:
            try:
                product = client.fetch_product(store, product_id)
                result.append(
                    {
                        "product_id": product_id,
                        "title": product.title,
                        "status_text": product.status_text,
                        "raw": product,
                    }
                )
            except Exception as exc:
                result.append(
                    {
                        "product_id": product_id,
                        "title": "(详情读取失败)",
                        "status_text": "—",
                        "raw": None,
                        "_detail_error": str(exc),
                    }
                )
        return result

    def storefront_action(
        self,
        store: Store,
        action: str,
        product_id: str,
        value: int,
    ) -> None:
        client = self.client_for(store)
        if isinstance(client, MockShopClient):
            return
        self._require_platform_write(store, f"storefront_{action}")
        if action == "reorder":
            client.reorder_storefront_product(product_id, value)
        elif action == "hide":
            client.hide_storefront_product(product_id, bool(value))
        elif action == "top":
            client.top_storefront_product(product_id, bool(value))
        else:
            raise ValueError("不支持的主页操作")

    def procurement_login(
        self,
        account_id: int,
        *,
        auto_fill: bool = True,
    ) -> dict[str, Any]:
        account = self.db.get_procurement_account(account_id)
        if account is None:
            raise ValueError("采购账号不存在")
        return ProcurementWebSession(
            self.db,
            self.db.path.parent,
        ).login(account, auto_fill=auto_fill)

    def procurement_check_status(self, account_id: int) -> str:
        account = self.db.get_procurement_account(account_id)
        if account is None:
            raise ValueError("采购账号不存在")
        return ProcurementWebSession(
            self.db,
            self.db.path.parent,
        ).check_status(account)

    def procurement_delete_account(self, account_id: int) -> list[str]:
        account = self.db.get_procurement_account(account_id)
        if account is None:
            raise ValueError("采购账号不存在")
        errors = ProcurementWebSession(
            self.db,
            self.db.path.parent,
        ).cleanup_profile(account)
        self.db.delete_procurement_account(account_id)
        return errors

    def procurement_bind_alipay(self, account_id: int) -> dict[str, Any]:
        account = self.db.get_procurement_account(account_id)
        if account is None:
            raise ValueError("采购账号不存在")
        return ProcurementWebSession(
            self.db,
            self.db.path.parent,
        ).bind_alipay(account)

    def sync_procurement_account(
        self,
        account_id: int,
        progress: ProgressCallback | None = None,
    ) -> SyncResult:
        account = self.db.get_procurement_account(account_id)
        if account is None:
            raise ValueError("采购账号不存在")
        adapter = ProcurementAdapter(self.db, self.db.path.parent)
        try:
            count = adapter.sync(account, progress)
        except Exception as exc:
            message = str(exc)[:1000]
            current = self.db.get_procurement_account(account_id) or account
            has_session = bool(
                self.db.get_procurement_session(account_id)
            )
            identity_mismatch = any(
                marker in message
                for marker in ("账号不匹配", "不是目标账号")
            )
            # Order sync can fail because of an expired MTOP token, a
            # temporary challenge, or a page timeout.  None of those is proof
            # that the browser profile itself is logged out.  Only an
            # explicit identity mismatch invalidates the saved account here;
            # explicit login redirects are handled by check_status/open_backend.
            status = (
                "账号不匹配"
                if identity_mismatch
                else str(current.get("status") or "")
                or ("已登录" if has_session else "异常")
            )
            self.db.update_procurement_account(
                account_id,
                status=status,
                last_error=message,
            )
            raise
        return SyncResult(
            f"procurement_{account['platform']}",
            count,
            f"{account['platform']}采购数据已同步：{count} 条",
            success=count,
        )

    def procurement_browse(self, account_id: int) -> SyncResult:
        account = self.db.get_procurement_account(account_id)
        if account is None:
            raise ValueError("采购账号不存在")
        from .procurement import normalize_procurement_records

        session = ProcurementWebSession(self.db, self.db.path.parent)
        captured = session.browse(account)
        records = normalize_procurement_records(
            str(account["platform"]),
            [item.get("data") for item in captured],
        )
        for record in records:
            self.db.upsert_procurement_order(
                int(account["id"]),
                str(account["platform"]),
                record,
            )
        self.db.update_procurement_account(
            int(account["id"]),
            last_sync=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            status="已登录",
            last_error="",
        )
        return SyncResult(
            f"procurement_browse_{account['platform']}",
            len(records),
            f"采购网页已关闭，保存 {len(records)} 条采购记录",
            success=len(records),
        )

    def procurement_open_backend(
        self,
        account_id: int,
    ) -> dict[str, Any]:
        account = self.db.get_procurement_account(account_id)
        if account is None:
            raise ValueError("采购账号不存在")
        return ProcurementWebSession(
            self.db,
            self.db.path.parent,
        ).open_backend(account)

    def inspect_procurement_product(
        self,
        account_id: int,
        url: str,
    ) -> dict[str, Any]:
        account = self.db.get_procurement_account(account_id)
        if account is None:
            raise ValueError("采购账号不存在")
        return ProcurementWebSession(
            self.db,
            self.db.path.parent,
        ).inspect_product(account, url)

    def open_procurement_product(
        self,
        account_id: int,
        url: str,
        attrs: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        account = self.db.get_procurement_account(account_id)
        if account is None:
            raise ValueError("采购账号不存在")
        return ProcurementWebSession(
            self.db,
            self.db.path.parent,
        ).open_product(account, url, attrs)

    def open_procurement_window(
        self,
        account_id: int,
        url: str,
        address: dict[str, Any] | None = None,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        account = self.db.get_procurement_account(account_id)
        if account is None:
            raise ValueError("采购账号不存在")
        from .procurement import normalize_purchase_product_url

        target_url = normalize_purchase_product_url(url)
        with self._procurement_windows_lock:
            current = self._procurement_windows.get(account_id)
            if (
                current is not None
                and not current.closed.is_set()
                and current.url == target_url
            ):
                return {
                    "status": "opened",
                    "reused": True,
                    "page_url": target_url,
                }
            if current is not None:
                current.close()
                self._procurement_windows.pop(account_id, None)
            window = ControlledPurchaseWindow(
                self.db,
                self.db.path.parent,
                account,
                target_url,
                address,
                details,
            )
            window.start()
            self._procurement_windows[account_id] = window
        return {
            "status": "opened",
            "reused": False,
            "page_url": target_url,
        }

    def verify_procurement_purchase(
        self,
        account_id: int,
        expected: dict[str, Any],
    ) -> dict[str, Any]:
        upstream_order_id = str(
            expected.get("upstream_order_id") or ""
        ).strip()
        if upstream_order_id:
            account = self.db.get_procurement_account(account_id)
            if account is None:
                return {
                    "status": "error",
                    "reason": "采购账号不存在",
                    "read_attempts": 0,
                }
            return ProcurementWebSession(
                self.db,
                self.db.path.parent,
            ).read_order_by_id(account, upstream_order_id)
        with self._procurement_windows_lock:
            window = self._procurement_windows.get(account_id)
        if window is None or window.closed.is_set():
            return {
                "status": "open_required",
                "reason": "请先打开对应的官方采购商品页",
                "read_attempts": 0,
            }
        result = window.verify_once(expected)
        result["read_attempts"] = 1
        return result

    def wait_for_procurement_purchase_signal(
        self,
        account_id: int,
        *,
        timeout_seconds: int = 30 * 60,
    ) -> dict[str, Any]:
        """Wait for a payment/return signal from the controlled purchase page."""

        with self._procurement_windows_lock:
            window = self._procurement_windows.get(account_id)
        if window is None:
            return {
                "status": "closed",
                "reason": "采购页面已关闭，无法读取付款结果",
            }
        deadline = time.monotonic() + max(1, int(timeout_seconds))
        while time.monotonic() < deadline:
            if window.purchase_observed.is_set():
                result = {
                    "status": "payment_observed",
                    "reason": window.purchase_signal_reason,
                }
                purchase_order_id = str(
                    getattr(window, "purchase_order_id", "") or ""
                )
                if purchase_order_id:
                    result["upstream_order_id"] = (
                        purchase_order_id
                    )
                try:
                    self.close_procurement_window(account_id)
                except Exception:
                    pass
                return result
            if window.returned_to_software.is_set():
                return {
                    "status": "returned_to_software",
                    "reason": "采购窗口已关闭，开始自动核对",
                }
            if window.closed.is_set():
                return {
                    "status": "closed",
                    "reason": "采购页面已关闭，未捕获付款完成信号",
                }
            time.sleep(0.25)
        return {
            "status": "timeout",
            "reason": "采购页面仍打开，尚未检测到付款完成",
        }

    def close_procurement_window(self, account_id: int) -> bool:
        with self._procurement_windows_lock:
            window = self._procurement_windows.pop(account_id, None)
        if window is None:
            return False
        window.close()
        return True

    def match_procurement_orders(self, store: Store) -> int:
        return ProcurementAdapter(
            self.db,
            self.db.path.parent,
        ).match_orders(store.id)

    def procurement_local_note(self, store: Store, order_id: str) -> str:
        links = self.db.list_procurement_links(store.id, order_id)
        parts = []
        for link in links:
            parts.append(
                "采购[{platform}] {external_order_id} 物流:{mail_no} "
                "取件码:{pickup_code} {goods_title} ¥{amount:.2f}".format(
                    platform=link.get("platform") or "",
                    external_order_id=link.get("external_order_id") or "",
                    mail_no=link.get("mail_no") or "",
                    pickup_code=link.get("pickup_code") or "",
                    goods_title=link.get("goods_title") or "",
                    amount=int(link.get("pay_amount") or 0) / 100,
                )
            )
            if link.get("local_note"):
                parts.append(str(link["local_note"]))
        return "\n".join(parts)

    def sync_procurement_note_to_wechat(
        self,
        store: Store,
        order_id: str,
    ) -> SyncResult:
        links = self.db.list_procurement_links(store.id, order_id)
        if not links:
            raise ValueError("该订单没有采购关联")
        note = self.procurement_local_note(store, order_id)
        existing = self.db.get_order(store.id, order_id)
        if existing and existing.merchant_notes:
            note = f"{existing.merchant_notes}\n{note}".strip()
        self.update_order_notes(store, order_id, note, 0)
        for link in links:
            self.db.mark_procurement_note_synced(int(link["id"]))
        return SyncResult(
            "procurement_note_sync",
            1,
            "采购备注已同步到微信订单备注",
            success=1,
        )

    def double11_activity_products(
        self,
        store: Store,
        activity_id: int = 1323,
    ) -> dict[str, Any]:
        return ShopPrivateClient(self.db, store).activity_products(activity_id)

    def snapup_activities(self, store: Store) -> list[dict[str, Any]]:
        if store.web_status != "已登录":
            raise ValueError(f"网页{store.web_status}，不能读取限时抢购")
        return ShopPrivateClient(self.db, store).snapup_activities()

    def platform_activity_catalog(self, store: Store) -> dict[str, Any]:
        activities = ShopPrivateClient(self.db, store).platform_activities()
        summaries = []
        for item in activities:
            summaries.append(
                {
                    "activity_id": str(item.get("activity_id") or ""),
                    "name": str(item.get("name") or ""),
                    "activity_state": int(item.get("activityState") or 0),
                    "apply_state": int(item.get("applyState") or 0),
                    "publish_time": int(item.get("publishTime") or 0),
                    "apply_end_time": int(item.get("applyEndTime") or 0),
                    "activity_end_time": int(item.get("activityEndTime") or 0),
                    "description": str(
                        item.get("activityIntroduction") or item.get("incentiveDescription") or ""
                    ),
                }
            )
        payload = {
            "source_store_id": store.id,
            "source_store_name": store.name,
            "fetched_at": int(time.time()),
            "activities": summaries,
        }
        self.db.set_setting(
            "platform_activity_catalog",
            json.dumps(payload, ensure_ascii=False),
        )
        return payload

    def apply_platform_activity(
        self,
        store: Store,
        activity_id: int,
    ) -> dict[str, Any]:
        if store.web_status != "已登录":
            raise ValueError(f"网页{store.web_status}，不能申报平台活动")
        self._require_platform_write(store, "platform_activity_apply")
        try:
            result = ShopPrivateClient(
                self.db,
                store,
            ).apply_platform_activity(activity_id)
        except Exception as exc:
            self.db.record_activity_operation(
                "活动申报",
                "失败",
                store.id,
                store.name,
                int(activity_id),
                0,
                str(exc),
            )
            raise
        self.db.record_activity_operation(
            "活动申报",
            "成功",
            store.id,
            store.name,
            int(activity_id),
            1,
            "店铺活动申报成功",
            result,
        )
        return {
            "store_id": store.id,
            "store_name": store.name,
            "activity_id": int(activity_id),
            "result": result,
        }

    def activity_preview(
        self,
        store: Store,
        activity_id: int,
        discount_basis_points: int = 9000,
        min_discount_cents: int = 100,
    ) -> dict[str, Any]:
        base = {
            "store_id": store.id,
            "store_name": store.name,
            "status": "跳过",
            "reason": "",
            "rows": [],
            "valid_sku_count": 0,
            "conflict_count": 0,
        }
        if store.web_status != "已登录":
            base["reason"] = f"网页{store.web_status}"
            return base
        try:
            client = ShopPrivateClient(self.db, store)
        except Exception as exc:
            base["reason"] = str(exc)
            return base
        close_status = client.account_close_status()
        if close_status is not None and close_status != 100:
            base["reason"] = f"店铺状态受限（状态码 {close_status}）"
            return base
        try:
            eligible = client.activity_eligible_products(activity_id)
        except Exception as exc:
            base["reason"] = str(exc)
            return base
        if not eligible:
            base["reason"] = "平台没有返回当前可报名商品"
            return base
        try:
            submitted = client.activity_submitted_products(activity_id)
            submitted_keys = {
                (
                    str(row.get("productId") or ""),
                    str(row.get("skuId") or ""),
                )
                for row in submitted
                if row.get("productId") and row.get("skuId")
            }
        except Exception:
            submitted_keys = set()
        if submitted_keys:
            eligible = [
                item
                for item in eligible
                if (
                    str(
                        (item.get("product") or {}).get("productId") or item.get("productId") or ""
                    ),
                    str((item.get("product") or {}).get("skuId") or item.get("skuId") or ""),
                )
                not in submitted_keys
            ]
        if not eligible:
            base["reason"] = (
                "可提报商品均已提交" if submitted_keys else "平台没有返回当前可报名商品"
            )
            return base

        validation_rows: dict[tuple[str, str], dict[str, Any]] = {}
        for start in range(0, len(eligible), 200):
            batch = eligible[start : start + 200]
            validation_items = []
            for item in batch:
                product = item.get("product") or {}
                product_id = str(item.get("productId") or product.get("productId") or "")
                sku_id = str(item.get("skuId") or product.get("skuId") or "")
                if product_id and sku_id:
                    validation_items.append(
                        {
                            "productId": product_id,
                            "skuId": sku_id,
                            "activityStockNum": 0,
                            "activityPrice": 1,
                            "sellingPointWording": "",
                            "stockMode": 1,
                            "applyType": 0,
                        }
                    )
            if not validation_items:
                continue
            try:
                validation = client.activity_validate_products(
                    activity_id,
                    validation_items,
                )
            except Exception:
                validation = {}
            for row in validation.get("resultList") or []:
                if not isinstance(row, dict):
                    continue
                product = row.get("product") or {}
                key = (
                    str(product.get("productId") or ""),
                    str(product.get("skuId") or ""),
                )
                if all(key):
                    validation_rows[key] = row

        base["status"] = "可预览"
        discount_basis_points = max(100, min(10000, int(discount_basis_points)))
        min_discount_cents = max(0, int(min_discount_cents))
        for item in eligible:
            product = item.get("product") or {}
            product_id = str(item.get("productId") or product.get("productId") or "")
            sku_id = str(item.get("skuId") or product.get("skuId") or "")
            try:
                current_price = int(
                    item.get("salePrice")
                    or (item.get("activityPriceCalInfo") or {}).get("salePrice")
                    or 0
                )
            except (TypeError, ValueError):
                current_price = 0
            try:
                stock = int(item.get("commStockNum") or 0)
            except (TypeError, ValueError):
                stock = 0
            title = str(item.get("productName") or item.get("title") or product_id)
            validation = validation_rows.get((product_id, sku_id), {})
            invalid = str(
                item.get("invalidString")
                or item.get("invalidStringPrecheck")
                or validation.get("invalidString")
                or validation.get("invalidStringPrecheck")
                or ""
            ).strip()
            regular_price = _discount_price_cents(
                current_price,
                Decimal(discount_basis_points) / Decimal(10000),
            )
            floor_price = current_price - min_discount_cents
            configured_price = min(regular_price, floor_price)
            try:
                platform_limit = int(validation.get("validPrice") or 0)
            except (TypeError, ValueError):
                platform_limit = 0
            activity_price = (
                min(configured_price, platform_limit) if platform_limit > 0 else configured_price
            )
            discount_cents = current_price - activity_price
            rate = round(activity_price / current_price * 10, 2) if current_price else 0.0
            status_text = "可报名"
            if invalid:
                status_text = invalid
            elif not product_id or not sku_id or current_price <= 0:
                status_text = "价格或SKU异常"
            elif activity_price < 1:
                status_text = "活动价低于0.01元"
            elif current_price <= min_discount_cents:
                status_text = "原价不足最低优惠"
            if status_text == "可报名":
                base["valid_sku_count"] += 1
            else:
                base["conflict_count"] += 1
            base["rows"].append(
                {
                    "store_id": store.id,
                    "store_name": store.name,
                    "product_id": product_id,
                    "title": title,
                    "sku_id": sku_id,
                    "current_price": current_price,
                    "activity_price": activity_price,
                    "discount_cents": discount_cents,
                    "actual_discount": rate,
                    "stock": stock,
                    "status": status_text,
                }
            )
        if base["valid_sku_count"] == 0:
            base["reason"] = "没有符合条件的在售商品" if base["conflict_count"] else "没有在售商品"
        return base

    def activity_submit_items(
        self,
        store: Store,
        activity_id: int,
        items: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if not items:
            raise ValueError("没有可提交的SKU")
        self._require_platform_write(store, "platform_activity_items_submit")
        client = ShopPrivateClient(self.db, store)
        normalized = []
        for item in items:
            product_id = str(item.get("productId") or "").strip()
            sku_id = str(item.get("skuId") or "").strip()
            try:
                activity_price = int(item.get("activityPrice") or item.get("newActivityPrice") or 0)
            except (TypeError, ValueError):
                activity_price = 0
            if not product_id or not sku_id or activity_price <= 0:
                continue
            normalized.append(
                {
                    "productId": product_id,
                    "skuId": sku_id,
                    "activityStockNum": 0,
                    "activityPrice": activity_price,
                    "sellingPointWording": "",
                    "stockMode": 1,
                    "applyType": 0,
                }
            )
        if not normalized:
            raise ValueError("没有字段完整的可提交SKU")
        # Platform activity imports silently confirm at most 200 rows.
        batches = [normalized[index : index + 200] for index in range(0, len(normalized), 200)]
        try:
            completed_batches = []
            for batch in batches:
                validation = client.activity_validate_products(
                    activity_id,
                    batch,
                )
                invalid_keys = {
                    (
                        str(row.get("product", {}).get("productId") or ""),
                        str(row.get("product", {}).get("skuId") or ""),
                    )
                    for row in validation.get("resultList") or []
                    if isinstance(row, dict)
                    and (
                        str(row.get("invalidString") or "").strip()
                        or str(row.get("invalidStringPrecheck") or "").strip()
                    )
                }
                valid_items = [
                    item for item in batch if (item["productId"], item["skuId"]) not in invalid_keys
                ]
                if not valid_items:
                    raise ValueError("平台校验未通过，本批次没有可提交SKU")
                added = client.activity_update_products(
                    activity_id,
                    valid_items,
                    action_type=2,
                    add_source=3,
                )
                completed_batches.append(
                    {
                        "count": len(valid_items),
                        "invalid_count": len(invalid_keys),
                        "validation": validation,
                        "added": added,
                    }
                )
        except Exception as exc:
            self.db.record_activity_operation(
                "活动商品提交",
                "失败",
                store.id,
                store.name,
                int(activity_id),
                len(normalized),
                str(exc),
            )
            raise
        result = {
            "store_id": store.id,
            "store_name": store.name,
            "activity_id": int(activity_id),
            "count": len(normalized),
            "batch_count": len(completed_batches),
            "batches": completed_batches,
        }
        self.db.set_setting(
            f"activity_last_result_{store.id}_{activity_id}",
            json.dumps(result, ensure_ascii=False),
        )
        self.db.record_activity_operation(
            "活动商品提交",
            "成功",
            store.id,
            store.name,
            int(activity_id),
            len(normalized),
            "活动商品价格提交成功",
            result,
        )
        return result

    def double11_remove_all(
        self,
        store: Store,
        activity_id: int = 1323,
    ) -> dict[str, Any]:
        self._require_platform_write(store, "platform_activity_items_remove")
        client = ShopPrivateClient(self.db, store)
        rows: list[dict[str, Any]] = []
        for page in range(1, 101):
            result = client.activity_products(
                activity_id,
                page=page,
                page_size=100,
            )
            page_rows = [row for row in (result.get("productList") or []) if isinstance(row, dict)]
            rows.extend(page_rows)
            if len(page_rows) < 100:
                break
        items = []
        for row in rows:
            product_id = str(row.get("productId") or "")
            sku_id = str(row.get("skuId") or "")
            if not product_id or not sku_id:
                continue
            items.append(
                {
                    "productId": product_id,
                    "skuId": sku_id,
                    "applyType": 0,
                }
            )
        if not items:
            return {
                "activity_id": int(activity_id),
                "count": 0,
                "message": "当前活动没有报名商品",
            }
        result = client.activity_update_products(
            activity_id,
            items,
            action_type=1,
            del_source=0,
        )
        return {
            "activity_id": int(activity_id),
            "count": len(items),
            "result": result,
        }

    def double11_submit(
        self,
        store: Store,
        drafts: list[dict[str, Any]],
        activity_id: int = 1323,
    ) -> dict[str, Any]:
        if not drafts:
            raise ValueError("双十一草稿为空")
        self._require_platform_write(store, "platform_activity_double11_submit")
        items = []
        missing = []
        for draft in drafts:
            product_id = str(draft.get("product_id") or "")
            cached = self.db.get_product(store.id, product_id)
            if cached is None:
                missing.append(product_id)
                continue
            try:
                raw = json.loads(cached.get("raw_json") or "{}")
            except json.JSONDecodeError:
                raw = {}
            skus = raw.get("skus") or []
            price = int(draft.get("activity_price") or 0)
            for sku in skus:
                if not isinstance(sku, dict):
                    continue
                sku_id = str(sku.get("sku_id") or "")
                if not sku_id:
                    continue
                items.append(
                    {
                        "productId": product_id,
                        "skuId": sku_id,
                        "newActivityPrice": price,
                        "applyType": 1,
                    }
                )
        if missing:
            raise ValueError("以下商品缺少本地详情，无法提交：" + "、".join(missing[:10]))
        if not items:
            raise ValueError("草稿商品没有可提交的SKU")
        client = ShopPrivateClient(self.db, store)
        added = client.activity_update_products(
            activity_id,
            items,
            action_type=2,
            add_source=1,
        )
        checked = client.activity_check_prices(activity_id, items)
        submitted = client.activity_submit_prices(
            activity_id,
            items,
            apply_type=1,
        )
        result = {
            "activity_id": int(activity_id),
            "count": len(items),
            "added": added,
            "checked": checked,
            "submitted": submitted,
        }
        self.db.set_setting(
            f"double11_last_result_{store.id}",
            json.dumps(result, ensure_ascii=False),
        )
        return result

    def sync_platform_violation_blacklist(
        self,
        store: Store,
    ) -> SyncResult:
        if not is_real_logged_in_store(store):
            raise ValueError(
                "违规同步只允许已登录真实店铺，"
                "不允许演示店或未登录店铺"
            )
        products = ShopPrivateClient(
            self.db,
            store,
        ).platform_violation_products()
        try:
            existing = json.loads(self.db.get_setting("violation_blacklist", "[]"))
        except json.JSONDecodeError:
            existing = []
        rows = [dict(row) for row in existing if isinstance(row, dict)]
        by_id = {str(row.get("product_id")): row for row in rows if row.get("product_id")}
        added = 0
        for product in products:
            product_id = str(product["product_id"])
            row = by_id.get(product_id)
            if row is None:
                row = {
                    "product_id": product_id,
                    "title": str(product.get("title") or ""),
                    "reason": "",
                    "added": datetime.now().strftime("%Y-%m-%d %H:%M"),
                }
                rows.append(row)
                by_id[product_id] = row
                added += 1
            if not row.get("title") and product.get("title"):
                row["title"] = product["title"]
            reason = f"平台同步：{product.get('reason') or '违规/预警'}"
            if reason not in str(row.get("reason") or ""):
                row["reason"] = f"{row.get('reason')}\n{reason}".strip()
        self.db.set_setting(
            "violation_blacklist",
            json.dumps(rows, ensure_ascii=False),
        )
        return SyncResult(
            "platform_violation_blacklist",
            len(products),
            f"平台违规同步完成：发现 {len(products)}，新增 {added}",
            success=len(products),
        )

    def sync_violation_library(self, store: Store) -> SyncResult:
        if not is_real_logged_in_store(store):
            raise ValueError(
                "违规同步只允许已登录真实店铺，"
                "不允许演示店或未登录店铺"
            )
        records = [
            record
            for record in ShopPrivateClient(
                self.db,
                store,
            ).platform_violation_records()
            if is_explicit_violation_record(record)
        ]
        try:
            existing = json.loads(self.db.get_setting("violation_library", "[]"))
        except (TypeError, ValueError):
            existing = []
        rows = [
            dict(row)
            for row in existing
            if isinstance(row, dict)
            and str(row.get("store_app_id") or "") != store.app_id
            and (
                row.get("store_app_id")
                or int(row.get("store_id") or 0) != store.id
            )
        ]
        for record in records:
            enriched = dict(record)
            if not enriched.get("head_img"):
                product_id = str(enriched.get("product_id") or "")
                get_product = getattr(self.db, "get_product", None)
                if product_id and callable(get_product):
                    product = get_product(store.id, product_id)
                    if isinstance(product, dict):
                        enriched["head_img"] = str(
                            product.get("head_img") or ""
                        )
            rows.append(
                {
                    **enriched,
                    "store_id": store.id,
                    "store_app_id": store.app_id,
                    "store_name": store.name,
                    "synced_at": datetime.now().isoformat(timespec="seconds"),
                }
            )
        self.db.set_setting(
            "violation_library",
            json.dumps(rows, ensure_ascii=False),
        )
        self.db.set_setting(
            "upload_violation_titles",
            json.dumps(
                build_upload_violation_titles(rows),
                ensure_ascii=False,
            ),
        )
        try:
            blacklist = json.loads(self.db.get_setting("violation_blacklist", "[]"))
        except (TypeError, ValueError):
            blacklist = []
        blocked = [dict(row) for row in blacklist if isinstance(row, dict)]
        by_product = {str(row.get("product_id")): row for row in blocked if row.get("product_id")}
        for record in records:
            product_id = str(record.get("product_id") or "")
            if not product_id:
                continue
            item = by_product.get(product_id)
            if item is None:
                item = {
                    "product_id": product_id,
                    "title": record.get("title", ""),
                    "reason": "",
                    "added": datetime.now().strftime("%Y-%m-%d %H:%M"),
                }
                blocked.append(item)
                by_product[product_id] = item
            reason = f"平台同步：{record.get('reason') or '违规/预警'}"
            if reason not in str(item.get("reason") or ""):
                item["reason"] = (f"{item.get('reason') or ''}\n{reason}").strip()
        self.db.set_setting(
            "violation_blacklist",
            json.dumps(blocked, ensure_ascii=False),
        )
        return SyncResult(
            "violation_library",
            len(records),
            f"明确违规商品已同步：{store.name} {len(records)} 条",
            success=len(records),
        )

    def fetch_funds_summary(self, store: Store, start_time: int, end_time: int) -> dict[str, Any]:
        client = self.client_for(store)
        if isinstance(client, MockShopClient):
            return {
                "available_amount": 10000,
                "pending_amount": 2000,
                "flow_list": [],
            }
        balance = client.fetch_fund_balance()
        flows = client.fetch_funds_flows(start_time, end_time)
        balance["flow_list"] = [
            item for item in flows.get("flow_list", []) if isinstance(item, dict)
        ]
        return balance


def date_range(start_text: str, end_text: str) -> tuple[int, int]:
    start = datetime.strptime(start_text.strip(), "%Y-%m-%d")
    end = datetime.strptime(end_text.strip(), "%Y-%m-%d")
    if end < start:
        raise ValueError("结束日期不能早于开始日期")
    if (end - start).days > 6:
        raise ValueError("单次同步最多选择 7 个自然日")
    start_time = int(start.timestamp())
    end_time = int(end.timestamp()) + 86399
    return start_time, end_time


class _TaskCancelled(Exception):
    pass
