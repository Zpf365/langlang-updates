from __future__ import annotations

import hashlib
import json
import threading
import time
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from typing import Any, Callable

from .db import SHANGHAI_TZ


GLOBAL_ENABLED_SETTING = "activity_auto_renewal_enabled"
GLOBAL_INTERVAL_SETTING = "activity_auto_renewal_interval_minutes"
AUTO_CREATE_RULES_SETTING = "activity_snapup_auto_rules"
AUTO_CREATE_CLOUD_STATUS_SETTING = "activity_snapup_auto_cloud_status"
DEFAULT_INTERVAL_MINUTES = 10
EXPIRED_GRACE_SECONDS = 3600
RENEWAL_MATCH_TOLERANCE_SECONDS = 180
AUTO_CREATE_MAX_PER_SCAN = 3
AUTO_CREATE_DAILY_LIMIT = 20
AUTO_CREATE_RETRY_LIMIT = 3
DEFAULT_AUTO_CREATE_RULES = {
    "start_delay_minutes": 5,
    "duration_seconds": 7 * 86400,
    "discount": 0.85,
    "min_discount_cents": 100,
    "stock_limit_per_sku": 0,
    "limit_purchase_num": 0,
    "rule_version": "default-v1",
    "saved_at": "",
}


def _integer(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _seconds(value: Any) -> int:
    stamp = _integer(value)
    if abs(stamp) >= 100_000_000_000:
        return stamp // 1000
    return stamp


def _activity_start(row: dict[str, Any]) -> int:
    return _seconds(
        row.get("start_time")
        or row.get("startTime")
        or row.get("start_time_ms")
    )


def _activity_end(row: dict[str, Any]) -> int:
    return _seconds(
        row.get("end_time")
        or row.get("endTime")
        or row.get("end_time_ms")
    )


def _is_merchant_snapup(row: dict[str, Any]) -> bool:
    """Reject records explicitly linked to platform campaigns."""
    for key in (
        "platformActivityId",
        "platform_activity_id",
        "platformCampaignId",
        "platform_campaign_id",
    ):
        if row.get(key) not in (None, "", 0, "0"):
            return False
    extra = row.get("extra")
    if isinstance(extra, dict):
        for key in ("platformActivityId", "platform_activity_id"):
            if extra.get(key) not in (None, "", 0, "0"):
                return False
    return True


class ActivityAutoRenewalManager:
    """Persistent, testable scheduler for merchant-created snap-up tasks."""

    def __init__(
        self,
        db: Any,
        service: Any,
        web_session: Any,
        *,
        now_provider: Callable[[], datetime] | None = None,
        write_scope: Callable[[Any], Any] | None = None,
        before_step: Callable[[], bool] | None = None,
    ) -> None:
        self.db = db
        self.service = service
        self.web_session = web_session
        self._now_provider = now_provider or datetime.now
        self._lock = threading.RLock()
        self._store_locks: dict[int, threading.Lock] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_scan_at = 0
        self._next_scan_at = 0
        self._last_result: dict[str, Any] = {}
        self._write_scope = write_scope
        self._before_step = before_step

    def _can_continue(self) -> bool:
        callback = self._before_step
        return True if callback is None else bool(callback())

    def _now(self, now: datetime | None = None) -> datetime:
        value = now or self._now_provider()
        return (
            value.astimezone(SHANGHAI_TZ)
            if value.tzinfo is not None
            else value.replace(tzinfo=SHANGHAI_TZ)
        )

    @staticmethod
    def _timestamp(value: datetime) -> int:
        return int(value.timestamp())

    def _interval_minutes(self) -> int:
        try:
            value = int(
                self.db.get_setting(
                    GLOBAL_INTERVAL_SETTING,
                    str(DEFAULT_INTERVAL_MINUTES),
                )
            )
        except (TypeError, ValueError):
            value = DEFAULT_INTERVAL_MINUTES
        return max(1, min(1440, value))

    def global_enabled(self) -> bool:
        return (
            self.db.get_setting(GLOBAL_ENABLED_SETTING, "0") == "1"
        )

    def save_global_config(
        self,
        *,
        enabled: bool,
        interval_minutes: int,
    ) -> dict[str, Any]:
        interval = max(1, min(1440, int(interval_minutes)))
        self.db.set_setting(
            GLOBAL_ENABLED_SETTING,
            "1" if enabled else "0",
        )
        self.db.set_setting(
            GLOBAL_INTERVAL_SETTING,
            str(interval),
        )
        return self.snapshot()

    def auto_create_rules(self) -> dict[str, Any]:
        try:
            stored = json.loads(
                self.db.get_setting(
                    AUTO_CREATE_RULES_SETTING,
                    "{}",
                )
            )
        except (TypeError, ValueError, json.JSONDecodeError):
            stored = {}
        if not isinstance(stored, dict) or not stored:
            return dict(DEFAULT_AUTO_CREATE_RULES)
        rules = dict(DEFAULT_AUTO_CREATE_RULES)
        rules.update(stored)
        return self._validate_auto_create_rules(rules)

    @staticmethod
    def _validate_auto_create_rules(
        rules: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            start_delay = int(rules.get("start_delay_minutes") or 5)
            duration = int(rules.get("duration_seconds") or 7 * 86400)
            discount = float(rules.get("discount") or 0.85)
            minimum = int(rules.get("min_discount_cents") or 100)
            stock = int(rules.get("stock_limit_per_sku") or 0)
            purchase = int(rules.get("limit_purchase_num") or 0)
        except (TypeError, ValueError) as exc:
            raise ValueError("自动限时活动规则格式无效") from exc
        if not 5 <= start_delay <= 60:
            raise ValueError("自动活动开始延迟必须在 5–60 分钟之间")
        if not 60 <= duration <= 7 * 86400:
            raise ValueError("自动活动持续时间必须在 1 分钟–7 天之间")
        if not 0.1 <= discount <= 0.85:
            raise ValueError("自动活动折扣必须在 1–8.5 折之间")
        if minimum < 100:
            raise ValueError("自动活动最低优惠不能低于 1 元")
        if stock != 0:
            raise ValueError("官方限时活动接口要求抢购库存保持 0")
        if purchase < 0:
            raise ValueError("每人限购不能为负数")
        return {
            "start_delay_minutes": start_delay,
            "duration_seconds": duration,
            "discount": discount,
            "min_discount_cents": minimum,
            "stock_limit_per_sku": 0,
            "limit_purchase_num": purchase,
            "rule_version": str(rules.get("rule_version") or ""),
            "saved_at": str(rules.get("saved_at") or ""),
        }

    def save_auto_create_rules(
        self,
        rules: dict[str, Any],
    ) -> dict[str, Any]:
        normalized = self._validate_auto_create_rules(rules)
        content = {
            key: value
            for key, value in normalized.items()
            if key not in {"rule_version", "saved_at"}
        }
        version = hashlib.sha256(
            json.dumps(
                content,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()[:16]
        normalized["rule_version"] = version
        normalized["saved_at"] = datetime.now().astimezone().isoformat(
            timespec="seconds"
        )
        self.db.set_setting(
            AUTO_CREATE_RULES_SETTING,
            json.dumps(normalized, ensure_ascii=False),
        )
        return normalized

    @staticmethod
    def _activity_product_ids(row: dict[str, Any]) -> set[str]:
        extra = row.get("extra")
        if isinstance(extra, str):
            try:
                extra = json.loads(extra)
            except (TypeError, ValueError, json.JSONDecodeError):
                extra = {}
        if not isinstance(extra, dict):
            extra = {}
        products = (
            extra.get("snapupProducts")
            or row.get("snapupProducts")
            or row.get("products")
            or []
        )
        ids = set()
        for item in products:
            if not isinstance(item, dict):
                continue
            product_key = item.get("productKey")
            product_id = (
                item.get("productId")
                or item.get("product_id")
                or (
                    product_key.get("productId")
                    if isinstance(product_key, dict)
                    else ""
                )
            )
            if product_id:
                ids.add(str(product_id))
        return ids

    def _process_new_products(
        self,
        store: Any,
        *,
        current: datetime,
        budget: dict[str, int],
    ) -> dict[str, Any]:
        lock = self._store_lock(store.id)
        if not lock.acquire(blocking=False):
            return {
                "status": "running",
                "processed": 0,
                "reason": "上一轮店铺自动活动仍在执行",
            }
        try:
            return self._process_new_products_locked(
                store,
                current=current,
                budget=budget,
            )
        finally:
            lock.release()

    def _process_new_products_locked(
        self,
        store: Any,
        *,
        current: datetime,
        budget: dict[str, int],
    ) -> dict[str, Any]:
        config = self.db.get_activity_renewal_config(store.id)
        if not int(config.get("enabled") or 0):
            return {"status": "disabled", "processed": 0}
        if str(getattr(store, "web_status", "")) != "已登录":
            return {
                "status": "skipped",
                "processed": 0,
                "reason": "网页未登录",
            }
        raw_rules = self.db.get_setting(
            AUTO_CREATE_RULES_SETTING,
            "",
        ).strip()
        if not raw_rules:
            return {
                "status": "rules_missing",
                "processed": 0,
                "reason": "尚未保存智能活动规则快照",
            }
        rules = self.auto_create_rules()
        observed_date = current.date().isoformat()
        today_count = self.db.count_activity_auto_create_successes(
            observed_date
        )
        if today_count >= AUTO_CREATE_DAILY_LIMIT:
            return {
                "status": "daily_limit",
                "processed": 0,
                "daily_success": today_count,
                "daily_limit": AUTO_CREATE_DAILY_LIMIT,
            }
        local_start = current.replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )
        start_utc = local_start.astimezone(UTC).isoformat(
            timespec="seconds"
        )
        end_utc = (local_start + timedelta(days=1)).astimezone(
            UTC
        ).isoformat(timespec="seconds")
        candidates = self.db.list_new_product_candidates(
            int(store.id),
            start_at=start_utc,
            end_at=end_utc,
        )
        if not candidates:
            return {"status": "idle", "processed": 0}
        try:
            activities = self.service.snapup_activities(store)
        except Exception as exc:
            error = f"读取官方限时活动失败：{exc}"[:2000]
            return {
                "status": "failed",
                "processed": 0,
                "error": error,
            }
        existing_product_ids: set[str] = set()
        for row in activities or []:
            if not isinstance(row, dict):
                continue
            if _integer(row.get("status"), -1) in {0, 1}:
                existing_product_ids.update(
                    self._activity_product_ids(row)
                )
        today_runs = [
            row
            for row in self.db.list_activity_auto_create_runs(
                int(store.id),
                limit=1000,
            )
            if str(row.get("observed_date") or "") == observed_date
        ]
        outcomes: list[dict[str, Any]] = []
        timestamp = self._timestamp(current)
        for row in today_runs:
            if str(row.get("status") or "") not in {
                "running",
                "verification_pending",
            }:
                continue
            confirmed = self._match_official_activity(
                activities,
                product_id=str(row.get("product_id") or ""),
                expected_activity_id=str(
                    row.get("activity_id") or ""
                ),
                expected_start=int(row.get("start_time") or 0),
                expected_end=int(row.get("end_time") or 0),
            )
            if confirmed is None:
                continue
            activity_id, confirmed_start, confirmed_end = confirmed
            reconciled = self.db.save_activity_auto_create_run(
                int(store.id),
                str(row["product_id"]),
                observed_date,
                str(row["rule_version"]),
                status="success",
                attempts=int(row.get("attempts") or 0),
                activity_id=activity_id,
                start_time=confirmed_start,
                end_time=confirmed_end,
                last_checked_at=timestamp,
            )
            row.update(reconciled)
            outcomes.append(reconciled)
            today_count += 1
            self.db.record_activity_operation(
                "自动新商品限时活动",
                "success",
                store.id,
                store.name,
                _integer(activity_id, 0) or None,
                1,
                (
                    f"商品 {row['product_id']} 已在官方活动列表确认"
                    f"活动 {activity_id}，{confirmed_start}–{confirmed_end}"
                ),
                {
                    "product_id": str(row["product_id"]),
                    "rule_version": str(row["rule_version"]),
                    "activity_id": activity_id,
                    "start_time": confirmed_start,
                    "end_time": confirmed_end,
                    "reconciled": True,
                },
            )
        handled_products = {
            str(row.get("product_id") or "")
            for row in today_runs
            if str(row.get("product_id") or "")
            and (
                str(row.get("status") or "")
                in {
                    "success",
                    "manual_stopped",
                }
                or (
                    str(row.get("status") or "") == "failed"
                    and (
                        int(row.get("attempts") or 0)
                        >= AUTO_CREATE_RETRY_LIMIT
                        or int(row.get("next_retry_at") or 0)
                        > self._timestamp(current)
                    )
                )
                or (
                    str(row.get("status") or "") == "verification_pending"
                    and (
                        int(row.get("next_retry_at") or 0)
                        > timestamp
                        or int(row.get("attempts") or 0)
                        >= AUTO_CREATE_RETRY_LIMIT
                    )
                )
            )
        }
        for candidate in candidates:
            if budget["remaining"] <= 0:
                break
            product_id = str(candidate.get("product_id") or "")
            if not product_id:
                continue
            if product_id in handled_products:
                continue
            if today_count >= AUTO_CREATE_DAILY_LIMIT:
                outcomes.append(
                    {
                        "product_id": product_id,
                        "status": "waiting",
                        "reason": "已达到每日自动创建上限",
                    }
                )
                break
            try:
                raw = json.loads(
                    str(candidate.get("raw_json") or "{}")
                )
            except (TypeError, ValueError, json.JSONDecodeError):
                raw = {}
            raw = raw if isinstance(raw, dict) else {}
            skip_reason = ""
            if _integer(candidate.get("status"), 0) != 5:
                skip_reason = "商品已不在售"
            elif _integer(candidate.get("stock_total"), 0) <= 0:
                skip_reason = "库存为 0"
            elif _integer(candidate.get("min_price"), 0) <= 0:
                skip_reason = "商品价格缺失"
            elif _integer(candidate.get("sku_count"), 0) <= 0:
                skip_reason = "SKU 资料不完整"
            elif not str(candidate.get("title") or "").strip():
                skip_reason = "商品标题缺失"
            elif not str(candidate.get("head_img") or "").strip():
                skip_reason = "商品主图缺失"
            elif not isinstance(raw.get("skus"), list) or not raw.get("skus"):
                skip_reason = "商品 SKU 明细缺失"
            elif product_id in existing_product_ids:
                skip_reason = "已存在当前或未来限时活动"
            if skip_reason:
                row = self.db.save_activity_auto_create_run(
                    int(store.id),
                    product_id,
                    observed_date,
                    str(rules["rule_version"]),
                    status="skipped",
                    attempts=0,
                    error=skip_reason,
                    last_checked_at=self._timestamp(current),
                )
                handled_products.add(product_id)
                outcomes.append(row)
                continue
            row = self._auto_create_one(
                store,
                candidate,
                rules=rules,
                observed_date=observed_date,
                current=current,
            )
            handled_products.add(product_id)
            outcomes.append(row)
            budget["remaining"] -= 1
            if row.get("status") == "success":
                today_count += 1
        return {
            "status": (
                "completed"
                if outcomes
                else "idle"
            ),
            "processed": len(outcomes),
            "outcomes": outcomes,
            "daily_success": today_count,
            "daily_limit": AUTO_CREATE_DAILY_LIMIT,
        }

    def _auto_create_one(
        self,
        store: Any,
        candidate: dict[str, Any],
        *,
        rules: dict[str, Any],
        observed_date: str,
        current: datetime,
    ) -> dict[str, Any]:
        product_id = str(candidate.get("product_id") or "")
        rule_version = str(rules.get("rule_version") or "")
        timestamp = self._timestamp(current)
        existing = self.db.get_activity_auto_create_run(
            int(store.id),
            product_id,
            observed_date,
            rule_version,
        )
        attempts = int((existing or {}).get("attempts") or 0)
        if existing and existing.get("status") in {
            "success",
            "manual_stopped",
        }:
            return existing
        if (
            existing
            and existing.get("status") == "running"
        ):
            retry_at = timestamp + 30 * 60
            return self.db.save_activity_auto_create_run(
                int(store.id),
                product_id,
                observed_date,
                rule_version,
                status="verification_pending",
                attempts=attempts,
                next_retry_at=retry_at,
                activity_id=str(existing.get("activity_id") or ""),
                start_time=int(existing.get("start_time") or 0),
                end_time=int(existing.get("end_time") or 0),
                error=(
                    "上次自动活动在提交后中断，"
                    "等待官方状态稳定后再次核对或重试"
                ),
                last_checked_at=timestamp,
            )
        if (
            existing
            and existing.get("status") == "failed"
            and attempts >= AUTO_CREATE_RETRY_LIMIT
        ):
            return existing
        if (
            existing
            and existing.get("status") == "verification_pending"
            and attempts >= AUTO_CREATE_RETRY_LIMIT
            and int(existing.get("next_retry_at") or 0) <= timestamp
        ):
            return existing
        if (
            existing
            and int(existing.get("next_retry_at") or 0) > timestamp
        ):
            return existing
        attempts += 1
        if not self._can_continue():
            return {
                "product_id": product_id,
                "status": "paused",
                "message": "账户或任务代次已变化，未提交活动创建",
            }
        self.db.save_activity_auto_create_run(
            int(store.id),
            product_id,
            observed_date,
            rule_version,
            status="running",
            attempts=attempts,
            last_checked_at=timestamp,
        )
        start_time = timestamp + int(rules["start_delay_minutes"]) * 60
        end_time = start_time + int(rules["duration_seconds"])
        attempted_activity_id = ""
        write_accepted = False
        try:
            scope = (
                self._write_scope(store)
                if callable(self._write_scope)
                else nullcontext()
            )
            with scope:
                result = self.service.create_snapup_activity(
                    store,
                    [product_id],
                    float(rules["discount"]),
                    start_time=start_time,
                    end_time=end_time,
                    min_discount_cents=int(
                        rules["min_discount_cents"]
                    ),
                    limit_purchase_num=int(
                        rules["limit_purchase_num"]
                    ),
                    stock_limit_per_sku=0,
                    create_all=True,
                )
            write_accepted = True
            if int((result or {}).get("already_active_count") or 0):
                return self.db.save_activity_auto_create_run(
                    int(store.id),
                    product_id,
                    observed_date,
                    rule_version,
                    status="skipped",
                    attempts=attempts,
                    error="官方已存在当前或未来活动",
                    last_checked_at=timestamp,
                )
            expected_id = str(
                (result or {}).get("activity_id") or ""
            )
            if not expected_id:
                activities = (result or {}).get("activities") or []
                expected_id = next(
                    (
                        str(item.get("activity_id") or "")
                        for item in activities
                        if isinstance(item, dict)
                        and item.get("activity_id")
                    ),
                    "",
                )
            attempted_activity_id = expected_id
            expected_start = int(
                (result or {}).get("start_time") or start_time
            )
            expected_end = int(
                (result or {}).get("end_time") or end_time
            )
            confirmed = self._confirm_auto_create(
                store,
                product_id=product_id,
                expected_activity_id=expected_id,
                expected_start=expected_start,
                expected_end=expected_end,
            )
            if confirmed is None:
                raise RuntimeError(
                    "官方创建接口返回成功，但活动列表未确认新活动生效"
                )
            activity_id, confirmed_start, confirmed_end = confirmed
            row = self.db.save_activity_auto_create_run(
                int(store.id),
                product_id,
                observed_date,
                rule_version,
                status="success",
                attempts=attempts,
                activity_id=activity_id,
                start_time=confirmed_start,
                end_time=confirmed_end,
                last_checked_at=timestamp,
            )
            self.db.record_activity_operation(
                "自动新商品限时活动",
                "success",
                store.id,
                store.name,
                _integer(activity_id, 0) or None,
                1,
                (
                    f"商品 {product_id} 已自动创建活动 "
                    f"{activity_id}，{confirmed_start}–{confirmed_end}"
                ),
                {
                    "product_id": product_id,
                    "rule_version": rule_version,
                    "activity_id": activity_id,
                    "start_time": confirmed_start,
                    "end_time": confirmed_end,
                },
            )
            return row
        except Exception as exc:
            verification = (
                write_accepted
                or "未确认新活动生效" in str(exc)
            )
            status = (
                "verification_pending"
                if verification
                else "failed"
            )
            retry_at = (
                timestamp + min(
                    6 * 3600,
                    30 * 60 * (2 ** max(0, attempts - 1)),
                )
                if attempts < AUTO_CREATE_RETRY_LIMIT
                else 0
            )
            row = self.db.save_activity_auto_create_run(
                int(store.id),
                product_id,
                observed_date,
                rule_version,
                status=status,
                attempts=attempts,
                next_retry_at=retry_at,
                activity_id=attempted_activity_id,
                start_time=start_time,
                end_time=end_time,
                error=str(exc)[:4000],
                last_checked_at=timestamp,
            )
            self.db.record_activity_operation(
                "自动新商品限时活动",
                "failed" if status == "failed" else "verification_pending",
                store.id,
                store.name,
                None,
                0,
                str(exc)[:2000],
                {
                    "product_id": product_id,
                    "rule_version": rule_version,
                    "attempts": attempts,
                },
            )
            return row

    def _confirm_auto_create(
        self,
        store: Any,
        *,
        product_id: str,
        expected_activity_id: str,
        expected_start: int,
        expected_end: int,
    ) -> tuple[str, int, int] | None:
        activities = self.service.snapup_activities(store)
        return self._match_official_activity(
            activities,
            product_id=product_id,
            expected_activity_id=expected_activity_id,
            expected_start=expected_start,
            expected_end=expected_end,
        )

    def _match_official_activity(
        self,
        activities: Any,
        *,
        product_id: str,
        expected_activity_id: str,
        expected_start: int,
        expected_end: int,
    ) -> tuple[str, int, int] | None:
        matches = []
        for row in activities or []:
            if not isinstance(row, dict):
                continue
            activity_id = str(
                row.get("activity_id")
                or row.get("activityId")
                or ""
            )
            if not activity_id:
                continue
            if expected_activity_id and activity_id != expected_activity_id:
                continue
            if _integer(row.get("status"), -1) not in {0, 1}:
                continue
            if product_id not in self._activity_product_ids(row):
                continue
            start = _activity_start(row)
            end = _activity_end(row)
            if (
                int(expected_start) > 0
                and int(expected_end) > 0
                and abs(start - int(expected_start))
                <= RENEWAL_MATCH_TOLERANCE_SECONDS
                and abs(end - int(expected_end))
                <= RENEWAL_MATCH_TOLERANCE_SECONDS
            ):
                matches.append((end, activity_id, start, end))
            elif int(expected_start) <= 0 or int(expected_end) <= 0:
                matches.append((end, activity_id, start, end))
        if not matches:
            return None
        matches.sort()
        _ordering, activity_id, start, end = matches[0]
        return activity_id, start, end

    def stop_auto_create(self, run_id: int) -> dict[str, Any]:
        rows = self.db.list_activity_auto_create_runs(limit=1000)
        row = next(
            (
                item
                for item in rows
                if int(item.get("id") or 0) == int(run_id)
            ),
            None,
        )
        if row is None:
            raise ValueError("自动活动执行记录不存在")
        return self.db.save_activity_auto_create_run(
            int(row["store_id"]),
            str(row["product_id"]),
            str(row["observed_date"]),
            str(row["rule_version"]),
            status="manual_stopped",
            attempts=int(row.get("attempts") or 0),
            activity_id=str(row.get("activity_id") or ""),
            start_time=int(row.get("start_time") or 0),
            end_time=int(row.get("end_time") or 0),
            error="用户手动停止",
            last_checked_at=self._timestamp(self._now()),
        )

    def save_store_config(
        self,
        store_id: int,
        *,
        enabled: bool,
        hours_before: int | None = None,
        duration_days: int | None = None,
    ) -> dict[str, Any]:
        if hours_before is not None and not 1 <= int(hours_before) <= 168:
            raise ValueError("提前续期小时数必须在 1–168 之间")
        if duration_days is not None and not 1 <= int(duration_days) <= 7:
            raise ValueError("续期天数必须在 1–7 天之间")
        current = self.db.get_activity_renewal_config(store_id)
        return self.db.save_activity_renewal_config(
            store_id,
            enabled=1 if enabled else 0,
            hours_before=(
                int(current["hours_before"])
                if hours_before is None
                else int(hours_before)
            ),
            duration_days=(
                int(current["duration_days"])
                if duration_days is None
                else int(duration_days)
            ),
        )

    def snapshot(self) -> dict[str, Any]:
        configs: dict[int, dict[str, Any]] = {}
        for store in self.db.list_stores():
            row = self.db.get_activity_renewal_config(store.id)
            configs[store.id] = {
                **row,
                "store_id": int(store.id),
                "store_name": store.name,
                "web_status": store.web_status,
                "api_status": store.status,
            }
        auto_runs = self.db.list_activity_auto_create_runs(limit=500)
        auto_status: dict[str, int] = {}
        for row in auto_runs:
            status = str(row.get("status") or "pending")
            auto_status[status] = auto_status.get(status, 0) + 1
        rules = self.auto_create_rules()
        cloud_status_raw = self.db.get_setting(
            AUTO_CREATE_CLOUD_STATUS_SETTING,
            "local_only",
        )
        try:
            cloud_status = json.loads(cloud_status_raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            cloud_status = {"status": cloud_status_raw}
        if not isinstance(cloud_status, dict):
            cloud_status = {"status": "local_only"}
        return {
            "enabled": self.global_enabled(),
            "interval_minutes": self._interval_minutes(),
            "running": bool(self._thread and self._thread.is_alive()),
            "last_scan_at": self._last_scan_at,
            "next_scan_at": self._next_scan_at,
            "last_result": dict(self._last_result),
            "stores": list(configs.values()),
            "runs": self.db.list_activity_renewal_runs(limit=500),
            "auto_create_rules": rules,
            "auto_create_runs": auto_runs,
            "auto_create_summary": {
                "pending": (
                    auto_status.get("pending", 0)
                    + auto_status.get("running", 0)
                ),
                "success": auto_status.get("success", 0),
                "failed": auto_status.get("failed", 0),
                "skipped": auto_status.get("skipped", 0),
                "verification_pending": auto_status.get(
                    "verification_pending",
                    0,
                ),
                "daily_success": (
                    self.db.count_activity_auto_create_successes(
                        self._now().date().isoformat()
                    )
                ),
                "daily_limit": AUTO_CREATE_DAILY_LIMIT,
            },
            "cloud_status": cloud_status,
        }

    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._loop,
                name="langlang-activity-auto-renewal",
                daemon=True,
            )
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=3)
        self._thread = None

    def _loop(self) -> None:
        while not self._stop.wait(5):
            if not self._can_continue():
                continue
            try:
                self.run_due()
            except Exception as exc:
                self._last_result = {
                    "status": "failed",
                    "error": str(exc)[:2000],
                    "finished_at": self._timestamp(self._now()),
                }

    def run_due(
        self,
        *,
        store_ids: list[int] | None = None,
        force: bool = False,
        now: datetime | None = None,
        progress: Callable[[str, int, int], None] | None = None,
    ) -> dict[str, Any]:
        if not self._can_continue():
            return {
                "status": "paused",
                "message": "账户或任务代次已变化，未启动自动续期扫描",
                "stores": [],
                "finished_at": self._timestamp(self._now(now)),
            }
        current = self._now(now)
        timestamp = self._timestamp(current)
        interval_seconds = self._interval_minutes() * 60
        if not self.global_enabled() and not force:
            return {
                "status": "disabled",
                "message": "自动续期总开关未启用",
                "stores": [],
                "finished_at": timestamp,
            }
        requested_ids = (
            {int(value) for value in store_ids}
            if store_ids
            else None
        )
        stores = [
            store
            for store in self.db.list_stores()
            if requested_ids is None or int(store.id) in requested_ids
        ]
        results: list[dict[str, Any]] = []
        total = len(stores)
        auto_budget = {"remaining": AUTO_CREATE_MAX_PER_SCAN}
        interrupted = False
        for index, store in enumerate(stores, start=1):
            if not self._can_continue():
                interrupted = True
                break
            config = self.db.get_activity_renewal_config(store.id)
            if not int(config.get("enabled") or 0):
                results.append(
                    {
                        "store_id": store.id,
                        "store_name": store.name,
                        "status": "disabled",
                        "message": "店铺自动续期未启用",
                    }
                )
                continue
            if (
                not force
                and int(config.get("next_scan_at") or 0) > timestamp
            ):
                results.append(
                    {
                        "store_id": store.id,
                        "store_name": store.name,
                        "status": "waiting",
                        "message": "未到下次扫描时间",
                        "next_scan_at": int(config.get("next_scan_at") or 0),
                    }
                )
                continue
            if progress:
                progress(
                    f"正在检查 {store.name}",
                    index - 1,
                    total,
                )
            renewal_result = self.check_store(
                store,
                force=force,
                now=current,
            )
            if not self._can_continue():
                interrupted = True
                break
            auto_result = self._process_new_products(
                store,
                current=current,
                budget=auto_budget,
            )
            results.append(
                {
                    **renewal_result,
                    "auto_create": auto_result,
                }
            )
            if progress:
                progress(f"已检查 {store.name}", index, total)
        self._last_scan_at = timestamp
        self._next_scan_at = timestamp + interval_seconds
        self._last_result = {
            "status": "paused" if interrupted else "completed",
            "finished_at": timestamp,
            "stores": results,
        }
        return dict(self._last_result)

    def _store_lock(self, store_id: int) -> threading.Lock:
        with self._lock:
            return self._store_locks.setdefault(
                int(store_id),
                threading.Lock(),
            )

    def check_store(
        self,
        store: Any,
        *,
        force: bool = False,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        current = self._now(now)
        timestamp = self._timestamp(current)
        config = self.db.get_activity_renewal_config(store.id)
        next_scan_at = timestamp + self._interval_minutes() * 60
        if not force and store.web_status != "已登录":
            error = f"网页{store.web_status}，跳过自动续期"
            self.db.save_activity_renewal_config(
                store.id,
                next_scan_at=next_scan_at,
                last_checked_at=timestamp,
                last_error=error,
            )
            return {
                "store_id": store.id,
                "store_name": store.name,
                "status": "failed",
                "error": error,
            }
        lock = self._store_lock(store.id)
        if not lock.acquire(blocking=False):
            return {
                "store_id": store.id,
                "store_name": store.name,
                "status": "running",
                "message": "上一轮店铺检查仍在执行",
            }
        try:
            return self._check_store_locked(
                store,
                config=config,
                current=current,
                next_scan_at=next_scan_at,
                force=force,
            )
        finally:
            lock.release()

    def _check_store_locked(
        self,
        store: Any,
        *,
        config: dict[str, Any],
        current: datetime,
        next_scan_at: int,
        force: bool,
    ) -> dict[str, Any]:
        timestamp = self._timestamp(current)
        self.db.save_activity_renewal_config(
            store.id,
            next_scan_at=next_scan_at,
            last_checked_at=timestamp,
            last_error="",
        )
        try:
            activities = self.service.snapup_activities(store)
        except Exception as exc:
            error = f"读取官方限时活动失败：{exc}"
            self.db.save_activity_renewal_config(
                store.id,
                last_error=error,
            )
            return {
                "store_id": store.id,
                "store_name": store.name,
                "status": "failed",
                "error": error[:2000],
            }
        hours_before = max(1, int(config.get("hours_before") or 6))
        candidates = []
        for row in activities or []:
            if not isinstance(row, dict) or not _is_merchant_snapup(row):
                continue
            status = _integer(row.get("status"), -1)
            end_time = _activity_end(row)
            if end_time <= 0:
                continue
            in_advance_window = (
                status in {0, 1}
                and
                timestamp - EXPIRED_GRACE_SECONDS
                <= end_time
                <= timestamp + hours_before * 3600
            )
            in_just_ended_window = (
                status == 2
                and
                timestamp - EXPIRED_GRACE_SECONDS
                <= end_time
                <= timestamp
            )
            if in_advance_window or in_just_ended_window:
                candidates.append(row)
        candidates.sort(key=_activity_end)
        if not candidates:
            return {
                "store_id": store.id,
                "store_name": store.name,
                "status": "idle",
                "message": "没有进入续期窗口的商家自建限时活动",
                "activity_count": len(activities or []),
            }
        source = candidates[0]
        source_end = _activity_end(source)
        if source_end > timestamp:
            source_id = str(
                source.get("activity_id")
                or source.get("activityId")
                or ""
            )
            return {
                "store_id": store.id,
                "store_name": store.name,
                "status": "waiting_for_end",
                "source_activity_id": source_id,
                "source_end_time": source_end,
                "next_scan_at": next_scan_at,
                "message": (
                    "官方规则不允许同一商品在源活动仍生效时重复创建；"
                    "将在源活动结束后复查并续期"
                ),
            }
        result = self._renew_candidate(
            store,
            source,
            config=config,
            current=current,
        )
        return {
            "store_id": store.id,
            "store_name": store.name,
            **result,
        }

    def _renew_candidate(
        self,
        store: Any,
        source: dict[str, Any],
        *,
        config: dict[str, Any],
        current: datetime,
    ) -> dict[str, Any]:
        source_id = str(
            source.get("activity_id")
            or source.get("activityId")
            or ""
        )
        source_end = _activity_end(source)
        if not source_id or source_end <= 0:
            return {
                "status": "failed",
                "error": "官方活动缺少活动ID或结束时间",
            }
        existing = self.db.get_activity_renewal_run(
            store.id,
            source_id,
            source_end,
        )
        if existing and existing.get("status") == "success":
            return {
                "status": "success",
                "source_activity_id": source_id,
                "new_activity_id": existing.get("new_activity_id") or "",
                "message": "该源活动的同一结束时间已成功续期",
            }
        timestamp = self._timestamp(current)
        retry_limit = 3
        backoff_seconds = 30 * 60
        attempts = int((existing or {}).get("attempts") or 0)
        if (
            existing
            and existing.get("status") in {"failed", "verification_pending"}
            and attempts >= retry_limit
        ):
            return {
                "status": "failed",
                "source_activity_id": source_id,
                "error": existing.get("error") or "重试次数已达上限",
                "message": "需要人工核实，已停止自动重试",
            }
        if (
            existing
            and int(existing.get("next_retry_at") or 0) > timestamp
        ):
            return {
                "status": "retry_wait",
                "source_activity_id": source_id,
                "next_retry_at": int(existing.get("next_retry_at") or 0),
                "message": "失败退避等待中",
            }
        if existing and existing.get("status") == "running":
            wait_until = timestamp + backoff_seconds
            self.db.save_activity_renewal_run(
                store.id,
                source_id,
                source_end,
                status="verification_pending",
                attempts=attempts,
                last_checked_at=timestamp,
                next_retry_at=wait_until,
                error="上次续期在提交后中断，将先复查官方列表再决定是否重试",
            )
            return {
                "status": "verification_pending",
                "source_activity_id": source_id,
                "next_retry_at": wait_until,
                "message": "检测到中断任务，正在等待官方状态稳定",
            }
        if not self._can_continue():
            return {
                "status": "paused",
                "source_activity_id": source_id,
                "message": "账户或任务代次已变化，未提交活动续期",
            }
        attempts += 1
        self.db.save_activity_renewal_run(
            store.id,
            source_id,
            source_end,
            status="running",
            attempts=attempts,
            last_checked_at=timestamp,
            next_retry_at=0,
            error="",
        )
        interval = max(60, source_end - timestamp + 1)
        start_time = timestamp + interval
        duration_days = max(
            1,
            min(7, int(config.get("duration_days") or 7)),
        )
        expected_end = start_time + duration_days * 86400
        try:
            renewal = self.web_session.renew_snapup_activity(
                store,
                source_id,
                start_time,
                duration_days,
            )
            expected_start = int(renewal.get("start_time") or start_time)
            expected_end = int(renewal.get("end_time") or expected_end)
            expected_activity_id = str(
                renewal.get("new_activity_id") or ""
            ).strip()
            confirmed = self._refresh_and_confirm(
                store,
                source_id=source_id,
                expected_activity_id=expected_activity_id,
                expected_start=expected_start,
                expected_end=expected_end,
            )
            if confirmed is None:
                raise RuntimeError(
                    "续期接口返回成功，但官方活动列表未确认新活动生效"
                )
            new_id, confirmed_start, confirmed_end = confirmed
            self.db.save_activity_renewal_run(
                store.id,
                source_id,
                source_end,
                status="success",
                attempts=attempts,
                last_checked_at=timestamp,
                next_retry_at=0,
                new_activity_id=new_id,
                new_start_time=confirmed_start,
                new_end_time=confirmed_end,
                error="",
            )
            self.db.save_activity_renewal_config(
                store.id,
                next_scan_at=timestamp + self._interval_minutes() * 60,
                last_checked_at=timestamp,
                last_success_at=timestamp,
                last_error="",
            )
            self.db.record_activity_operation(
                "自动续期限时抢购",
                "success",
                store.id,
                store.name,
                _integer(source_id, 0) or None,
                1,
                (
                    f"源活动 {source_id} 已续期为新活动 {new_id}，"
                    f"{confirmed_start}–{confirmed_end}"
                ),
                {
                    "source_activity_id": source_id,
                    "source_end_time": source_end,
                    "new_activity_id": new_id,
                    "new_start_time": confirmed_start,
                    "new_end_time": confirmed_end,
                    "attempts": attempts,
                },
            )
            return {
                "status": "success",
                "source_activity_id": source_id,
                "source_end_time": source_end,
                "new_activity_id": new_id,
                "new_start_time": confirmed_start,
                "new_end_time": confirmed_end,
                "attempts": attempts,
            }
        except Exception as exc:
            is_verification = "官方活动列表未确认" in str(exc)
            status = (
                "verification_pending"
                if is_verification
                else "failed"
            )
            retry_at = timestamp + min(
                6 * 3600,
                backoff_seconds * (2 ** max(0, attempts - 1)),
            )
            error = str(exc)[:4000]
            self.db.save_activity_renewal_run(
                store.id,
                source_id,
                source_end,
                status=status,
                attempts=attempts,
                last_checked_at=timestamp,
                next_retry_at=(
                    retry_at if attempts < retry_limit else 0
                ),
                new_activity_id=str(
                    renewal.get("new_activity_id") or ""
                    if "renewal" in locals()
                    else ""
                ),
                new_start_time=start_time,
                new_end_time=expected_end,
                error=error,
            )
            self.db.save_activity_renewal_config(
                store.id,
                next_scan_at=timestamp + self._interval_minutes() * 60,
                last_checked_at=timestamp,
                last_error=error,
            )
            self.db.record_activity_operation(
                "自动续期限时抢购",
                "failed",
                store.id,
                store.name,
                _integer(source_id, 0) or None,
                0,
                error,
                {
                    "source_activity_id": source_id,
                    "source_end_time": source_end,
                    "attempts": attempts,
                },
            )
            return {
                "status": status,
                "source_activity_id": source_id,
                "source_end_time": source_end,
                "attempts": attempts,
                "next_retry_at": retry_at,
                "error": error,
            }

    def _refresh_and_confirm(
        self,
        store: Any,
        *,
        source_id: str,
        expected_activity_id: str,
        expected_start: int,
        expected_end: int,
    ) -> tuple[str, int, int] | None:
        activities = self.service.snapup_activities(store)
        matches: list[tuple[int, str, int, int]] = []
        for row in activities or []:
            if not isinstance(row, dict):
                continue
            activity_id = str(
                row.get("activity_id")
                or row.get("activityId")
                or ""
            )
            if not activity_id or activity_id == source_id:
                continue
            if (
                expected_activity_id
                and activity_id != expected_activity_id
            ):
                continue
            status = _integer(row.get("status"), -1)
            if status not in {0, 1}:
                continue
            start = _activity_start(row)
            end = _activity_end(row)
            if (
                abs(start - int(expected_start))
                <= RENEWAL_MATCH_TOLERANCE_SECONDS
                and abs(end - int(expected_end))
                <= RENEWAL_MATCH_TOLERANCE_SECONDS
            ):
                matches.append((end, activity_id, start, end))
        if not matches:
            return None
        matches.sort()
        _end_order, activity_id, start, end = matches[0]
        return activity_id, start, end
