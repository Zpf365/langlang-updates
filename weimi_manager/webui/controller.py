from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict
from datetime import datetime, timedelta
import base64
import copy
import csv
import hashlib
import io
import json
import re
import tempfile
import threading
import time
import os
import shutil
import subprocess
import sys
from typing import Any, Callable, Iterator
import uuid
from pathlib import Path

from .. import __version__
from ..auth_client import AuthError
from ..db import Database, SHANGHAI_TZ, order_status_text
from ..models import (
    Store,
    is_unclaimed_gift_order,
    order_counts_as_transaction,
)
from ..procurement import (
    canonical_platform,
    match_store_skus_to_source,
    normalize_purchase_product_url,
    purchase_source_identity,
    validated_source_skus,
)
from ..operations import (
    _safe_error_message,
    dashboard_payload,
    export_csv,
    product_detail_payload,
    products_payload,
    rules_payload,
    store_detail_payload,
    validate_rules,
)
from ..runtime_logging import (
    get_runtime_log_manager,
    log_event,
)
from ..transit_shipping import (
    TransitShippingError,
    TransitShippingManager,
    normalize_rules,
)
from ..updater import (
    StagedUpdate,
    UpdateInfo,
    check_for_update,
    download_and_stage_update,
    launch_update_installer,
)
from ..wechat_api import WechatApiError
from ..activity_renewal import ActivityAutoRenewalManager
from ..address_parser import address_is_complete, parse_address_text
from ..analytics import AnalyticsRuntime
from ..analytics.builders import (
    business_date,
    product_daily_payloads,
    store_daily_payloads,
)
from .delivery_ledger import delivery_ledger_payload
from .logistics_monitor import logistics_monitor_payload
from .clipboard import write_clipboard_text
from .store_import import parse_store_import_source
from ..team_service import TeamService
from ..upload_violations import (
    build_cloud_counterfeit_rows,
    build_upload_violation_titles,
    filter_active_violation_records,
    is_real_logged_in_store,
    load_upload_violation_title_records,
    mandatory_upload_violation_issue,
    real_logged_in_stores,
)


PRIMARY_NAV = (
    ("stores", "店铺管理", "store", "/shops"),
    ("orders", "订单管理", "package", "/orders"),
    ("transit", "中转订单", "truck", "/transit"),
    ("logistics", "物流监控", "radar", "/logistics"),
    ("statistics", "利润统计", "chart", "/statistics"),
    ("products", "商品管理", "clipboard", "/managed-products"),
    ("upload", "上传商品", "upload", "/product-upload"),
    ("supply_market", "货源市场", "grid", "/supply-market"),
    ("hosted", "托管中心", "settings", "/hosted"),
    ("violations", "违规中心", "alert", "/violations"),
    ("procurement", "采购账号", "shopping-cart", "/procurement-accounts"),
    ("customer_service", "AI客服", "message", "/customer-service"),
)

MORE_NAV = (
    ("big_screen", "数据大屏", "chart", "/big-screen"),
    ("operations", "运营中心", "radar", "/operations"),
    ("runtime_logs", "运行日志", "clipboard", "/more"),
    ("store_groups", "店铺分组", "folder", "/shops"),
    ("activity", "活动管理", "calendar", "/activity"),
    ("automation", "自动化", "workflow", "/automation"),
    ("finance", "资金账户", "wallet", "/finance"),
    ("storefront", "店铺主页", "store", "/storefront"),
    ("after_sales", "售后管理", "return", "/orders?after_sales=1"),
    ("team", "团队管理", "users", "/more/team"),
    ("delivery_ledger", "未完成", "clipboard", "/more/delivery-ledger"),
)

PROCUREMENT_AUTO_VERIFY_MAX_ATTEMPTS = 4
PROCUREMENT_AUTO_VERIFY_RETRY_SECONDS = 2.0
PROCUREMENT_AUTO_VERIFY_POST_TRIGGER_SECONDS = 0.8
PROCUREMENT_AUTO_VERIFY_WAIT_SECONDS = 30 * 60

INTERNAL_MODULE_ENTITLEMENTS = {
    "procurement": "feature.procurement_association",
    "delivery_ledger": "module.delivery_ledger",
    "team": "module.team",
    "after_sales": "module.after_sales",
    "storefront": "module.storefront",
    "finance": "module.finance",
    "automation": "module.automation",
    "store_groups": "module.store_groups",
    "runtime_logs": "module.runtime_logs",
}
INTERNAL_CAPABILITIES = (
    *INTERNAL_MODULE_ENTITLEMENTS.values(),
    "feature.software_update",
    "account.view_others",
)
PROCUREMENT_ASSOCIATION_ENTITLEMENT = "feature.procurement_association"
LOCAL_PROCUREMENT_ASSOCIATION_SETTING = (
    "local_experiment.procurement_association_enabled"
)
LOCAL_PROCUREMENT_ASSOCIATION_USERNAMES = frozenset({"zpfly"})
PROCUREMENT_ASSOCIATION_ACTIONS = frozenset(
    {
        "orders.workbench.manual-purchase",
        "orders.workbench.purchase-focus",
        "orders.workbench.browse-procurement",
        "orders.workbench.sync-procurement",
        "orders.workbench.sync-procurement-note",
    }
)
PROCUREMENT_ASSOCIATION_ACTION_PREFIXES = (
    "orders.procurement.",
    "procurement.",
)
ACTION_ENTITLEMENTS = {
    "delivery.ledger": "module.delivery_ledger",
    "team.operations.members": "module.team",
    "team.operations.create": "module.team",
    "team.operations.add-existing": "module.team",
    "team.operations.save": "module.team",
    "team.operations.batch": "module.team",
    "team.operations.audit": "module.team",
    "storefront.load": "module.storefront",
    "storefront.action": "module.storefront",
    "finance.summary": "module.finance",
    "automation.tasks.list": "module.automation",
    "automation.tasks.create": "module.automation",
    "automation.tasks.update": "module.automation",
    "automation.tasks.delete": "module.automation",
    "automation.tasks.run-start": "module.automation",
    "automation.tasks.run-status": "module.automation",
    "automation.runs": "module.automation",
    "stores.groups.create": "module.store_groups",
    "stores.groups.delete": "module.store_groups",
    "stores.assign-group": "module.store_groups",
    "stores.batch-assign-group": "module.store_groups",
    "logs.status": "module.runtime_logs",
    "logs.export": "module.runtime_logs",
    "logs.open-dir": "module.runtime_logs",
    "logs.webui-events": "module.runtime_logs",
    "update.check": "feature.software_update",
    "update.install.start": "feature.software_update",
    "update.install.status": "feature.software_update",
}
AFTER_SALE_ACTION_PREFIX = "orders.after-sales."
INTERNAL_ACCOUNT_IDS = frozenset(
    {
        "220a9616-2346-441b-9e3f-f5f4f5b824fd",
        "8248567f-c855-435a-89ef-4175729155df",
    }
)

ISOLATED_MODULES: tuple[dict[str, str], ...] = ()

ORDER_MARKS_SETTING = "webui_marked_order_ids"
ORDER_HIDDEN_PRODUCTS_SETTING = "webui_hidden_order_product_ids"
ORDER_SOFTWARE_NOTES_SETTING = "webui_order_software_notes"
ORDER_FLAGS_SETTING = "webui_order_flags"
ORDER_PURCHASE_INFO_SETTING = "order_purchase_info"
TRANSIT_SHIPPING_RULES_SETTING = "transit_shipping_rules"
PRODUCT_TASK_JOBS_SETTING = "webui_product_task_jobs"
PRODUCT_TASK_JOB_LIMIT = 80
ANALYTICS_PUBLISH_KEY_LIMIT = 2048
ANALYTICS_PUBLISH_KEY_TTL_SECONDS = 24 * 3600
STORE_SCORE_FIELDS = (
    "dsr",
    "score_product",
    "score_deliver",
    "score_service",
)
STORE_SCORE_LABELS = {
    "dsr": "体验分",
    "score_product": "商品体验",
    "score_deliver": "物流体验",
    "score_service": "服务体验",
}
STORE_SCORE_STATE_PREFIX = "webui_store_score_state_"
STORE_SCORE_LEGACY_PREFIX = "webui_previous_store_metrics_"
STORE_SCORE_DAILY_KEEP_DAYS = 35
OFFICIAL_NOTE_COLOR_NAMES = {
    1: "红",
    2: "橙",
    3: "绿",
    4: "蓝",
}
OFFICIAL_NOTE_COLOR_VALUES = {
    name: value for value, name in OFFICIAL_NOTE_COLOR_NAMES.items()
}
OFFICIAL_NOTE_COLOR_VALUES[""] = 0
OPERATION_PERMISSION_KEYS = (
    "operations_view",
    "operations_export",
    "operations_task_manage",
    "operations_rule_manage",
    "operations_open_backend",
)
OPERATION_ROLE_DEFAULTS = {"owner", "admin"}
OPERATION_BACKEND_TARGETS = {
    "store": "/shop/home",
    "score": "/shop/setting/rate2",
    "review": "/shop/evaluate/home",
    "service": "/shop/customerservice/home",
    "traffic": "/shop/platformrecommend/recommendationFeed",
    "product": "/shop/goods/list",
    "product_stats": "/shop/statistics/product",
    "aftersale": "/shop/aftersale/home",
    "order": "/shop/order/list",
    "violation": "/shop/violation/home",
    "diagnosis": "/shop/shopdiagnosis/home",
    "content": "/shop/goods/content",
    "growth": "/shop/goodsSubFunction/qualificationScore",
    "coupon": "/shop/marketing/coupon",
    "snapup": "/shop/marketing/snapup",
    "gift": "/shop/marketingkol/gift",
    "repurchase": "/shop/useroperation/repurchaseTool",
    "member": "/shop/member/home",
}
UPLOAD_DEFAULT_TEMPLATE: dict[str, Any] = {
    "name": "默认上货模板",
    "title_prefix": "",
    "title_suffix": "",
    "price_multiplier": 1,
    "price_offset": 0,
    "fixed_price_enabled": False,
    "fixed_price": 0,
    "final_price_decimals": 2,
    "ladder_enabled": False,
    "ladder_rules": "",
    "stock": 0,
    "stock_mode": "source",
    "listing_mode": "immediate",
    "duplicate_filter_mode": "store",
    "save_as_draft": False,
    "skip_duplicate": True,
    "auto_open_category": True,
    "category_business_type": "individual",
    "category_mode": "auto",
    "fallback_category": "",
    "specified_category_id": "",
    "specified_category_ids": [],
    "specified_category_path": "",
    "specified_category_version": "",
    "specified_category_store_id": "",
    "force_fallback_category": False,
    "deposit_limit": 0,
    "category_brand_ids": "",
    "category_license_groups": "",
    "video_folder": "",
    "main_image_video": False,
    "use_package_tags": True,
    "read_packaging_labels": False,
    "clothing_mode": False,
    "seven_day_return": True,
    "shipping_insurance": False,
    "damage_return": False,
    "fake_one_three": False,
    "support_exchange": False,
    "distributor_name": "",
    "distributor_contact": "",
    "delivery_type": "现货",
    "delivery_time": "48小时内发货",
    "title_replace_rules": "",
    "low_price_enabled": False,
    "low_price_multiplier": 2,
    "low_price_action": "跳过商品",
    "trim_head": 0,
    "remove_head_index": 0,
    "remove_detail_index": 0,
    "title_rules": "",
    "sku_rules": "",
    "image_rules": "",
    "image_mode": "默认方式",
    "head_action": "删除违规图片",
    "detail_action": "删除违规图片",
    "attribute_rules": "",
    "trim_detail": 0,
    "trim_detail_tail": 0,
    "detail_keep_first": 0,
    "strip_title_brackets": False,
    "cloud_violation": True,
    "cloud_violation_types": "",
    "enable_custom_attributes": False,
    "is_default": False,
}
UPLOAD_KNOWN_FREIGHT_TEMPLATE_LABELS = {
    "1103871390004": "不包邮",
    "1103871380004": "江浙沪包邮",
    "1103871366004": "全国包邮（偏远地区除外）",
    "1103871356004": "全国包邮",
}
UPLOAD_PACKAGE_SUFFIXES = {".json", ".jsonl", ".csv", ".tsv", ".txt"}
UPLOAD_MAX_FILE_CHARS = 20_000_000
UPLOAD_JOB_TERMINAL_STATUSES = {
    "completed",
    "partial",
    "failed",
    "paused",
    "cancelled",
}
ACCOUNT_TASK_TERMINAL_STATUSES = {
    "completed",
    "partial",
    "failed",
    "verification_pending",
    "cancelled",
}
ALL_STORES_KEY = "__all__"
UPLOAD_SENSITIVE_KEY_MARKERS = (
    "appsecret",
    "app_secret",
    "access_token",
    "refresh_token",
    "authorization",
    "cookie",
    "password",
    "secret",
    "token",
    "address",
    "phone",
    "mobile",
    "tel",
)


def _upload_identity_key(row: dict[str, Any]) -> tuple[str, str]:
    return (
        str(row.get("platform") or "").strip(),
        str(row.get("source_product_id") or "").strip(),
    )


def _sanitize_upload_export(value: Any) -> Any:
    if isinstance(value, list):
        return [_sanitize_upload_export(item) for item in value]
    if not isinstance(value, dict):
        return value
    output: dict[str, Any] = {}
    for key, item in value.items():
        normalized = str(key).casefold().replace("-", "_")
        if any(marker in normalized for marker in UPLOAD_SENSITIVE_KEY_MARKERS):
            continue
        output[str(key)] = _sanitize_upload_export(item)
    return output


class _ScopedUploadProductService:
    def __init__(self, controller: "NextUiController", service: Any) -> None:
        self._controller = controller
        self._service = service

    def __getattr__(self, name: str) -> Any:
        return getattr(self._service, name)

    def product_write(
        self,
        store: Any,
        action: str,
        payload: dict[str, Any],
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        client_actions = {
            "add": (
                "product_add",
                "product_image_upload",
                "product_listing",
                "product_delisting",
            ),
            "update": (
                "product_update",
                "product_image_upload",
            ),
            "delete": (
                "product_delete",
                "product_web_batch_action",
            ),
            "cancel_audit": ("product_audit_cancel",),
        }.get(str(action), ())
        with self._controller._allow_platform_write(
            store,
            "product_write",
            *client_actions,
        ):
            return self._service.product_write(
                store,
                action,
                payload,
                *args,
                **kwargs,
            )


class NextUiController:
    """Small command interface shared by the web UI and its tests."""

    def __init__(
        self,
        db: Database,
        *,
        account_label: str = "",
        web_session: Any | None = None,
        product_service: Any | None = None,
        native_action: Any | None = None,
        auth_context: Any | None = None,
        auth_client: Any | None = None,
        auth_service: Any | None = None,
        team_service: TeamService | None = None,
        project_dir: Path | None = None,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        self.db = db
        prune_store_cache = getattr(
            db,
            "prune_orphaned_store_cache_settings",
            None,
        )
        if callable(prune_store_cache):
            prune_store_cache()
        self._now_provider = now_provider or datetime.now
        self.project_dir = (
            Path(project_dir).resolve()
            if project_dir is not None
            else Path(sys.executable).resolve().parent
        )
        self.account_label = account_label
        self.web_session = web_session
        self.product_service = product_service
        self.native_action = native_action
        self.auth_context = auth_context
        self.auth_client = auth_client
        self.auth_service = auth_service
        self.team_service = team_service
        self._server_capability_grants = dict(
            getattr(auth_context, "entitlements", {}) or {}
        )
        self._server_capability_authoritative = False
        self._server_capability_checked_at = 0.0
        analytics_default_base_url = ""
        auth_config = getattr(auth_client, "config", None)
        function_url = getattr(auth_config, "function_url", None)
        if (
            callable(function_url)
            and bool(getattr(auth_config, "configured", False))
        ):
            analytics_default_base_url = str(
                function_url("ops-report") or ""
            )
        self.analytics = AnalyticsRuntime(
            db,
            token_provider=self._cloud_violation_token,
            default_base_url=analytics_default_base_url,
        )
        self._analytics_publish_lock = threading.Lock()
        self._analytics_publish_keys: dict[str, float] = {}
        self._member_role = (
            "owner"
            if auth_context is None
            else str(getattr(auth_context, "member_role", "") or "")
        )
        raw_permissions = getattr(auth_context, "permissions", {}) or {}
        self._member_permissions = (
            dict(raw_permissions) if isinstance(raw_permissions, dict) else {}
        )
        self._product_jobs: dict[str, dict[str, Any]] = {}
        self._product_jobs_lock = threading.Lock()
        self._product_batch_jobs: dict[str, dict[str, Any]] = {}
        self._product_batch_jobs_lock = threading.Lock()
        self._product_task_registry_lock = threading.RLock()
        self._product_store_leases: dict[int, str] = {}
        self._product_store_leases_lock = threading.RLock()
        self._product_backend_jobs: dict[str, dict[str, Any]] = {}
        self._product_backend_jobs_lock = threading.Lock()
        self._store_jobs: dict[str, dict[str, Any]] = {}
        self._store_jobs_lock = threading.Lock()
        self._store_score_state_lock = threading.RLock()
        self._store_refresh_workers: dict[str, threading.Thread] = {}
        self._store_login_lock = threading.Lock()
        self._order_jobs: dict[str, dict[str, Any]] = {}
        self._order_jobs_lock = threading.Lock()
        self._order_backend_jobs: dict[str, dict[str, Any]] = {}
        self._order_backend_jobs_lock = threading.Lock()
        self._procurement_login_jobs: dict[str, dict[str, Any]] = {}
        self._procurement_login_jobs_lock = threading.Lock()
        self._procurement_login_lock = threading.Lock()
        self._procurement_monitor_lock = threading.Lock()
        self._procurement_monitor_last_at = 0.0
        self._procurement_link_lock = threading.Lock()
        self._procurement_verify_lock = threading.Lock()
        self._procurement_verify_in_progress: set[
            tuple[int, str, str, int]
        ] = set()
        self._procurement_purchase_baselines: dict[
            tuple[int, str, str, int],
            set[str],
        ] = {}
        self._procurement_auto_verify_jobs: dict[str, dict[str, Any]] = {}
        self._procurement_auto_verify_jobs_lock = threading.Lock()
        self._procurement_preview_cache_lock = threading.RLock()
        self._procurement_preview_cache: dict[
            tuple[int, str, str, str],
            tuple[float, dict[str, Any]],
        ] = {}
        self._procurement_preview_cache_index: dict[
            tuple[int, str, str],
            tuple[tuple[int, str, str, str], float],
        ] = {}
        self._upload_jobs: dict[str, dict[str, Any]] = {}
        self._upload_jobs_lock = threading.Lock()
        self._automation_jobs: dict[str, dict[str, Any]] = {}
        self._automation_jobs_lock = threading.Lock()
        self._automation_scheduler_stop = threading.Event()
        self._automation_scheduler_thread: threading.Thread | None = None
        self._automation_scheduler_lock = threading.Lock()
        self._activity_renewal_jobs: dict[str, dict[str, Any]] = {}
        self._activity_renewal_jobs_lock = threading.Lock()
        self._activity_cloud_lock = threading.Lock()
        self._activity_cloud_restore_thread: threading.Thread | None = None
        self._activity_cloud_push_thread: threading.Thread | None = None
        self._activity_cloud_restore_attempted = False
        self._activity_cloud_push_pending = False
        self.activity_renewal_manager = (
            ActivityAutoRenewalManager(
                db,
                product_service,
                web_session,
                now_provider=self._now_provider,
                write_scope=lambda store: self._allow_platform_write(
                    store,
                    "activity_create",
                ),
            )
            if product_service is not None and web_session is not None
            else None
        )
        self._upload_category_timer: threading.Timer | None = None
        self._upload_category_timer_lock = threading.Lock()
        self._update_jobs: dict[str, dict[str, Any]] = {}
        self._update_jobs_lock = threading.Lock()
        self._startup_refresh_lock = threading.Lock()
        self._startup_refresh_thread: threading.Thread | None = None
        self._new_store_followup_lock = threading.Lock()
        self._new_store_followup_thread: threading.Thread | None = None
        self._new_store_followup_pending: set[int] = set()
        self._big_screen_process: subprocess.Popen[Any] | None = None
        self._last_update_info: UpdateInfo | None = None
        self.account_action_handler: Callable[[str], None] | None = None
        self.requested_account_action = ""
        self._account_session_lock = threading.RLock()
        self._account_session_closing = False
        self._account_generation = 0
        self._platform_write_scope: ContextVar[
            frozenset[tuple[str, str]]
        ] = ContextVar(
            f"webui_platform_write_scope_{id(self)}",
            default=frozenset(),
        )
        if product_service is not None:
            setattr(
                product_service,
                "platform_write_authorizer",
                self._authorize_platform_write,
            )

    def _account_id(self) -> str:
        return str(
            getattr(self.auth_context, "account_id", "")
            or self.account_label
            or ""
        )

    def _current_generation(self) -> int:
        with self._account_session_lock:
            if self._account_session_closing:
                raise RuntimeError("当前账户会话正在关闭")
            return int(self._account_generation)

    def _job_update_allowed(self, generation: int) -> bool:
        with self._account_session_lock:
            return (
                not self._account_session_closing
                and int(generation) == int(self._account_generation)
            )

    def _job_owner_fields(self) -> dict[str, Any]:
        with self._account_session_lock:
            return {
                "account_id": self._account_id(),
                "generation": int(self._account_generation),
            }

    def _require_job_owner(
        self,
        job: dict[str, Any],
        label: str,
    ) -> None:
        with self._account_session_lock:
            if self._account_session_closing:
                raise PermissionError(
                    f"{label}所属账户会话正在关闭，不能读取或操作"
                )
            current_account = self._account_id()
            current_generation = int(self._account_generation)
        job_account = str(job.get("account_id") or "")
        if job_account != current_account:
            raise PermissionError(
                f"{label}不属于当前账户，已拒绝读取或操作"
            )
        try:
            job_generation = int(job.get("generation"))
        except (TypeError, ValueError) as exc:
            raise PermissionError(
                f"{label}缺少有效会话代际，已拒绝读取或操作"
            ) from exc
        if job_generation != current_generation:
            raise PermissionError(
                f"{label}会话代际不匹配，已拒绝读取或操作"
            )

    def _require_persisted_product_task_owner(
        self,
        job: dict[str, Any],
        label: str,
    ) -> None:
        with self._account_session_lock:
            if self._account_session_closing:
                raise PermissionError(
                    f"{label}所属账户会话正在关闭，不能读取"
                )
            current_account = self._account_id()
        if str(job.get("account_id") or "") != current_account:
            raise PermissionError(f"{label}不属于当前账户，已拒绝读取")

    def _claim_product_store_leases(
        self,
        store_ids: list[int],
        owner: str,
    ) -> list[int]:
        normalized = sorted(
            {
                int(store_id)
                for store_id in store_ids
                if int(store_id) > 0
            }
        )
        with self._product_store_leases_lock:
            conflicts = [
                store_id
                for store_id in normalized
                if store_id in self._product_store_leases
            ]
            if conflicts:
                raise ValueError(
                    "所选店铺商品正在执行另一任务，请等待或取消后再试："
                    + ", ".join(map(str, conflicts))
                )
            for store_id in normalized:
                self._product_store_leases[store_id] = owner
        return normalized

    def _release_product_store_leases(
        self,
        store_ids: list[int],
        owner: str,
    ) -> None:
        with self._product_store_leases_lock:
            for store_id in store_ids:
                if self._product_store_leases.get(int(store_id)) == owner:
                    self._product_store_leases.pop(int(store_id), None)

    @contextmanager
    def _product_store_lease(
        self,
        store_ids: list[int],
        owner: str,
    ) -> Iterator[list[int]]:
        normalized = self._claim_product_store_leases(store_ids, owner)
        try:
            yield normalized
        finally:
            self._release_product_store_leases(normalized, owner)

    @staticmethod
    def _public_product_task(
        job: dict[str, Any],
        *,
        include_products: bool = False,
    ) -> dict[str, Any]:
        public = {
            key: copy.deepcopy(value)
            for key, value in job.items()
            if not str(key).startswith("_")
            and (
                include_products
                or key != "products"
            )
        }
        public.pop("runner", None)
        return public

    def _load_product_task_registry(self) -> dict[str, dict[str, Any]]:
        try:
            payload = json.loads(
                self.db.get_setting(PRODUCT_TASK_JOBS_SETTING, "{}")
            )
        except (TypeError, ValueError, json.JSONDecodeError):
            payload = {}
        raw_tasks = payload.get("tasks") if isinstance(payload, dict) else {}
        if not isinstance(raw_tasks, dict):
            return {}
        return {
            str(job_id): dict(job)
            for job_id, job in raw_tasks.items()
            if isinstance(job, dict)
        }

    def _persist_product_task(self, job: dict[str, Any]) -> None:
        job_id = str(job.get("job_id") or "").strip()
        if not job_id:
            return
        now = datetime.now().isoformat(timespec="seconds")
        stored = self._public_product_task(job)
        stored["updated_at"] = now
        stored.setdefault("created_at", now)
        with self._product_task_registry_lock:
            tasks = self._load_product_task_registry()
            tasks[job_id] = stored
            if len(tasks) > PRODUCT_TASK_JOB_LIMIT:
                ordered = sorted(
                    tasks.items(),
                    key=lambda item: str(
                        item[1].get("updated_at")
                        or item[1].get("created_at")
                        or ""
                    ),
                    reverse=True,
                )
                tasks = dict(ordered[:PRODUCT_TASK_JOB_LIMIT])
            self.db.set_setting(
                PRODUCT_TASK_JOBS_SETTING,
                json.dumps(
                    {"tasks": tasks},
                    ensure_ascii=False,
                ),
            )

    def _load_product_task(self, job_id: str) -> dict[str, Any] | None:
        with self._product_task_registry_lock:
            job = self._load_product_task_registry().get(job_id)
        if job is None:
            return None
        if job.get("status") == "paused":
            return job
        if job.get("status") not in ACCOUNT_TASK_TERMINAL_STATUSES:
            if job.get("kind") == "refresh":
                job["message"] = (
                    "上次商品刷新随程序退出中断；当前结果待核实，请重新刷新"
                )
                job["status"] = "verification_pending"
                job["verification_pending"] = True
            else:
                job["message"] = (
                    "上次商品批量操作随程序退出中断；未完成项已恢复为待核实"
                )
                items = list(job.get("items") or [])
                for row in items:
                    if not isinstance(row, dict):
                        continue
                    if row.get("status") not in {
                        "success",
                        "failed",
                        "cancelled",
                        "pending",
                    }:
                        row["status"] = "pending"
                        row["message"] = "程序中断，未获得平台执行结论"
                job["items"] = items
                counts = {
                    status: sum(
                        1
                        for row in items
                        if isinstance(row, dict)
                        and row.get("status") == status
                    )
                    for status in ("success", "failed", "cancelled", "pending")
                }
                job["counts"] = counts
                job["success"] = counts["success"]
                job["failed"] = counts["failed"]
                job["cancelled_count"] = counts["cancelled"]
                job["pending"] = counts["pending"]
                job["total"] = len(items)
                job["current"] = len(items)
                job["status"] = "verification_pending"
                job["verification_pending"] = True
            self._persist_product_task(job)
        return job

    def _authorize_platform_write(self, app_id: str, action: str) -> None:
        with self._account_session_lock:
            if self._account_session_closing:
                raise PermissionError(
                    "账户正在切换，旧的平台写操作已冻结"
                )
        scope = self._platform_write_scope.get()
        expected = (str(app_id), str(action))
        if expected not in scope:
            raise PermissionError(
                "真实平台写操作缺少当前会话安全门，已拒绝执行："
                f"{action}"
            )

    @contextmanager
    def _allow_platform_write(
        self,
        store: Any,
        action: str,
        *additional_actions: str,
    ) -> Iterator[None]:
        token = self._platform_write_scope.set(
            frozenset(
                (str(getattr(store, "app_id", "") or ""), str(item))
                for item in (action, *additional_actions)
                if str(item or "").strip()
            )
        )
        try:
            yield
        finally:
            self._platform_write_scope.reset(token)

    def dispatch(
        self,
        action: str,
        payload: dict[str, Any] | None = None,
        *,
        origin: str = "manual",
    ) -> dict[str, Any]:
        data = payload or {}
        handlers = {
            "bootstrap": self._bootstrap,
            "capabilities.refresh": self._refresh_capabilities,
            "analytics.status": self._analytics_status,
            "analytics.config.get": self._analytics_config_get,
            "analytics.config.save": self._analytics_config_save,
            "analytics.flush": self._analytics_flush,
            "analytics.diagnostics": self._analytics_diagnostics,
            "account.task-status": self._account_task_status,
            "account.action": self._account_action,
            "update.check": self._check_update,
            "update.install.start": self._start_update_install,
            "update.install.status": self._update_install_status,
            "logs.status": self._logs_status,
            "logs.webui-events": self._record_webui_events,
            "logs.export": self._export_logs,
            "logs.open-dir": self._open_log_directory,
            "startup.refresh.start": self._start_startup_refresh,
            "startup.refresh.status": self._startup_refresh_status,
            "dashboard.big-screen.open": self._open_big_screen,
            "clipboard.write": self._write_clipboard,
            "delivery.ledger": self._delivery_ledger,
            "operations.dashboard": self._operations_dashboard,
            "operations.products": self._operations_products,
            "operations.store-detail": self._operations_store_detail,
            "operations.product-detail": self._operations_product_detail,
            "operations.tasks.list": self._operations_tasks_list,
            "operations.tasks.create": self._operations_tasks_create,
            "operations.tasks.update": self._operations_tasks_update,
            "operations.rules.get": self._operations_rules_get,
            "operations.rules.save": self._operations_rules_save,
            "operations.export": self._operations_export,
            "operations.inspect": self._operations_inspect,
            "operations.inspect.status": self._operations_inspect_status,
            "operations.open-backend": self._operations_open_backend,
            "team.operations.members": self._team_operations_members,
            "team.operations.create": self._team_operations_create,
            "team.operations.add-existing": self._team_add_existing,
            "team.operations.save": self._team_operations_save,
            "team.operations.batch": self._team_operations_batch,
            "team.operations.audit": self._team_operations_audit,
            "stores.list": self._list_stores,
            "stores.create": self._create_store,
            "stores.update": self._update_store,
            "stores.delete": self._delete_store,
            "stores.reveal-secret": self._reveal_store_secret,
            "stores.groups.create": self._create_store_group,
            "stores.groups.delete": self._delete_store_group,
            "stores.assign-group": self._assign_store_group,
            "stores.batch-assign-group": self._batch_assign_store_group,
            "stores.batch-delete": self._batch_delete_stores,
            "stores.probe": self._probe_store,
            "stores.login": self._login_store,
            "stores.login.batch": self._login_store,
            "stores.import-cookie": self._import_store_cookie,
            "stores.open-backend": self._open_store_backend,
            "stores.clear-cache": self._clear_web_cache,
            "stores.batch.preview": self._preview_store_batch,
            "stores.batch.commit": self._commit_store_batch,
            "stores.credentials.detail": self._store_credentials_detail,
            "stores.credentials.probe": self._probe_store_credential,
            "stores.refresh": self._refresh_stores,
            "stores.refresh.start": self._start_store_refresh,
            "stores.refresh.status": self._store_refresh_status,
            "storefront.load": self._load_storefront,
            "storefront.action": self._storefront_action,
            "finance.summary": self._finance_summary,
            "automation.tasks.list": self._list_automation_tasks,
            "automation.tasks.create": self._create_automation_task,
            "automation.tasks.update": self._update_automation_task,
            "automation.tasks.delete": self._delete_automation_task,
            "automation.tasks.run-start": self._start_automation_run,
            "automation.tasks.run-status": self._automation_run_status,
            "automation.runs": self._list_automation_runs,
            "orders.list": self._list_orders,
            "orders.sync": self._sync_orders,
            "orders.refresh-one": self._refresh_one_order,
            "orders.logistics.track": self._order_logistics_track,
            "logistics.monitor": self._logistics_monitor,
            "orders.quick-refresh.start": self._start_order_quick_refresh,
            "orders.quick-refresh.status": self._order_quick_refresh_status,
            "orders.mark": self._mark_order,
            "orders.update-note": self._update_order_note,
            "orders.update-note-state": self._update_order_note_state,
            "orders.workbench.detail": self._order_workbench_detail,
            "orders.workbench.local-update": self._update_order_workbench,
            "orders.workbench.manual-purchase": self._manual_link_procurement,
            "orders.workbench.purchase-focus": self._set_procurement_focus,
            "orders.workbench.browse-procurement": self._browse_procurement,
            "orders.workbench.sync-procurement": self._sync_procurement_order,
            "orders.workbench.sync-procurement-note": self._sync_procurement_order_note,
            "orders.procurement.link-upsert": self._procurement_link_upsert,
            "orders.procurement.link-delete": self._procurement_link_delete,
            "orders.procurement.link-open": self._procurement_link_open,
            "orders.procurement.purchase-confirm": self._procurement_purchase_confirm,
            "orders.procurement.purchase-auto-verify.status": (
                self._procurement_purchase_auto_verify_status
            ),
            "orders.procurement.product-preview": self._procurement_product_preview,
            "orders.procurement.link-save": self._procurement_link_save,
            "orders.procurement.purchase-open": self._procurement_link_open,
            "orders.procurement.backfill": self._procurement_backfill,
            "orders.workbench.address-update": self._request_order_address_change,
            "orders.address.parse": self._parse_order_address,
            "orders.address.save-software": self._save_order_address_software,
            "orders.workbench.price-update": self._update_order_price,
            "orders.workbench.delivery-update": self._update_order_delivery,
            "orders.workbench.shipping-options": self._refresh_order_shipping,
            "orders.workbench.shipment-create": self._create_order_shipment,
            "orders.workbench.shipment-complete": self._complete_order_shipment,
            "orders.workbench.open-official": self._open_official_order,
            "orders.workbench.open-official.status": self._open_official_order_status,
            "orders.after-sales.list": self._list_after_sales,
            "orders.after-sales.detail": self._after_sale_detail,
            "orders.after-sales.sync": self._sync_after_sales,
            "orders.after-sales.action": self._after_sale_action,
            "orders.after-sales.remove": self._remove_after_sales,
            "orders.after-sales.upload-media": self._upload_after_sale_media,
            "orders.after-sales.auto-config.get": self._get_after_sale_auto_config,
            "orders.after-sales.auto-config.save": self._save_after_sale_auto_config,
            "orders.after-sales.auto-start": self._start_auto_after_sale,
            "orders.transfer-cases.list": self._list_transfer_cases,
            "orders.transfer-cases.create": self._create_transfer_case,
            "orders.transfer-cases.update": self._update_transfer_case,
            "orders.workbench.messages": self._list_pickup_messages,
            "orders.workbench.mark-messages-sent": self._mark_pickup_messages_sent,
            "orders.workbench.logs": self._list_order_task_logs,
            "orders.workbench.import": self._import_order_workbench,
            "orders.import-file.parse": self._parse_order_import_file,
            "orders.profit.report": self._profit_report,
            "orders.profit.download-log": self._record_profit_download,
            "orders.decrypt-address": self._decrypt_order_address,
            "orders.filters.get": self._get_order_filters,
            "orders.filters.save": self._save_order_filters,
            "orders.settings.get": self._get_order_settings,
            "orders.settings.save": self._save_order_settings,
            "products.list": self._list_products,
            "products.locate": self._locate_product,
            "products.refresh": self._refresh_products,
            "products.refresh.start": self._start_product_refresh,
            "products.refresh.status": self._product_refresh_status,
            "products.refresh.cancel": self._cancel_product_refresh,
            "products.batch.start": self._start_product_batch,
            "products.batch.status": self._product_batch_status,
            "products.batch.cancel": self._cancel_product_batch,
            "products.tasks.list": self._list_product_tasks,
            "products.delete": self._delete_product,
            "products.cancel-audit": self._cancel_product_audit,
            "products.set-listing": self._set_products_listing,
            "products.detail": self._product_detail,
            "products.taglink": self._product_taglink,
            "products.update-stock": self._update_product_stock,
            "products.upload-images": self._upload_product_images,
            "products.open-official": self._open_official_product,
            "products.open-official.status": self._open_official_product_status,
            "products.operation-logs": self._list_product_operation_logs,
            "activity.catalog": self._activity_catalog,
            "activity.apply": self._activity_apply,
            "activity.preview": self._activity_preview,
            "activity.submit-items": self._activity_submit_items,
            "activity.remove-all": self._activity_remove_all,
            "activity.operations": self._activity_operations,
            "activity.coupons.list": self._list_coupons,
            "activity.coupons.sync": self._sync_coupons,
            "activity.coupons.create": self._create_coupon,
            "activity.coupons.status": self._update_coupon_status,
            "activity.coupons.renew": self._renew_coupon,
            "activity.snapup.list": self._list_snapup_activities,
            "activity.snapup.create": self._create_snapup_activity,
            "activity.snapup.delete": self._delete_snapup_activity,
            "activity.snapup.renew": self._renew_snapup_activity,
            "activity.double11.preview": self._double11_preview,
            "activity.double11.drafts.list": self._list_double11_drafts,
            "activity.double11.drafts.save": self._save_double11_drafts,
            "activity.double11.submit": self._double11_submit,
            "activity.double11.remove-all": self._double11_remove_all,
            "activity.renewal.status": self._activity_renewal_status,
            "activity.renewal.save": self._save_activity_renewal_config,
            "activity.renewal.scan.start": self._start_activity_renewal_scan,
            "activity.renewal.scan.status": self._activity_renewal_scan_status,
            "activity.smart-auto.rules.save": self._save_smart_auto_rules,
            "activity.smart-auto.stop": self._stop_smart_auto_run,
            "upload.list": self._list_upload,
            "upload.batch.import": self._import_upload_batch,
            "upload.batch.reparse": self._reparse_upload_batch,
            "upload.batch.preflight": self._preflight_upload_batch,
            "upload.batch.export": self._export_upload_batch,
            "upload.batch.clear": self._clear_upload_batch,
            "upload.templates.defaults": self._upload_template_defaults,
            "upload.templates.save": self._save_upload_template,
            "upload.templates.copy": self._copy_upload_template,
            "upload.templates.set-default": self._set_default_upload_template,
            "upload.templates.delete": self._delete_upload_template,
            "upload.templates.select-folder": self._select_upload_template_folder,
            "upload.categories": self._upload_categories,
            "upload.store-options": self._upload_store_options,
            "upload.tasks.create": self._create_upload_tasks,
            "upload.tasks.retry": self._retry_upload_tasks,
            "upload.tasks.clear": self._clear_upload_tasks,
            "upload.category-auto-resume.set": (
                self._set_upload_category_auto_resume
            ),
            "upload.waiting-category.cancel": (
                self._cancel_upload_waiting_category
            ),
            "upload.logs": self._list_upload_logs,
            "upload.run.start": self._start_upload_job,
            "upload.run.pause": self._pause_upload_job,
            "upload.run.cancel": self._cancel_upload_job,
            "upload.run.status": self._upload_job_status,
            "statistics.summary": self._statistics_summary,
            "hosted.list": self._list_hosted,
            "hosted.save-config": self._save_hosted_config,
            "hosted.get-schedule": self._get_hosted_schedule,
            "hosted.save-schedule": self._save_hosted_schedule,
            "hosted.execute": self._execute_hosted,
            "violations.list": self._list_violations,
            "violations.refresh": self._refresh_violations,
            "violations.sync-platform-blacklist": self._sync_platform_violation_blacklist,
            "customer-service.open": self._open_customer_console,
            "procurement.list": self._list_procurement,
            "procurement.create": self._create_procurement,
            "procurement.update": self._update_procurement,
            "procurement.delete": self._delete_procurement,
            "procurement.login": self._login_procurement,
            "procurement.login.start": self._start_procurement_login,
            "procurement.login.status": self._procurement_login_status,
            "procurement.open-backend.start": self._start_procurement_backend,
            "procurement.open-backend.status": self._procurement_login_status,
            "procurement.probe": self._probe_procurement,
            "procurement.sync": self._sync_procurement,
            "transit.shipping.overview": self._transit_shipping_overview,
            "transit.shipping.rules.save": self._save_transit_shipping_rules,
            "transit.shipping.apply": self._apply_transit_shipping,
            "workbench.open": self._open_workbench,
        }
        if not self._analytics_rpc_enabled():
            for key in tuple(handlers):
                if key.startswith("analytics."):
                    handlers.pop(key, None)
        with self._account_session_lock:
            account_closing = self._account_session_closing
        if account_closing and action not in {
            "account.action",
            "account.task-status",
        }:
            raise RuntimeError(
                "当前账户会话正在安全关闭，旧账户操作已冻结"
            )
        handler = handlers.get(action)
        if handler is None:
            raise ValueError(f"不支持的界面操作：{action}")
        entitlement = ACTION_ENTITLEMENTS.get(action)
        if action.startswith(AFTER_SALE_ACTION_PREFIX):
            entitlement = "module.after_sales"
        procurement_action = (
            action in PROCUREMENT_ASSOCIATION_ACTIONS
            or action.startswith(
                PROCUREMENT_ASSOCIATION_ACTION_PREFIXES
            )
        )
        if procurement_action:
            entitlement = PROCUREMENT_ASSOCIATION_ENTITLEMENT
        if entitlement:
            if procurement_action:
                self._require_procurement_association()
            else:
                self._require_entitlement(entitlement)
        started = time.perf_counter()
        manager = get_runtime_log_manager()
        summary = (
            {
                "batch_count": len(data.get("events") or [])
                if isinstance(data.get("events"), list)
                else 0
            }
            if action == "logs.webui-events"
            else data
        )
        try:
            result = handler(data)
        except Exception as exc:
            if manager is not None:
                manager.network(
                    action=action,
                    store_id=data.get("store_id", ""),
                    parameters=summary,
                    status="failed",
                    duration_ms=(time.perf_counter() - started) * 1000,
                    origin=origin,
                    error_code=str(
                        getattr(exc, "code", "")
                        or type(exc).__name__
                    ),
                    error=str(exc),
                )
            self._emit_analytics_error(action, exc)
            raise
        if manager is not None:
            manager.network(
                action=action,
                store_id=data.get("store_id", ""),
                parameters=summary,
                status="success",
                duration_ms=(time.perf_counter() - started) * 1000,
                origin=origin,
            )
        self._emit_dispatch_analytics(action, data, result)
        return result

    @staticmethod
    def _analytics_rpc_enabled() -> bool:
        return (
            not bool(getattr(sys, "frozen", False))
            and os.environ.get("LANGLANG_ANALYTICS_DEV_RPC") == "1"
        )

    def start_analytics_reporter(self) -> None:
        try:
            self.analytics.start()
        except Exception:
            return

    def stop_analytics_reporter(self) -> None:
        try:
            self.analytics.stop()
        except Exception:
            return

    def _analytics_status(self, _payload: dict[str, Any]) -> dict[str, Any]:
        return self.analytics.status()

    def _analytics_config_get(self, _payload: dict[str, Any]) -> dict[str, Any]:
        return self.analytics.status()["config"]

    def _analytics_config_save(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.analytics.configure(
            enabled=bool(payload.get("enabled")),
            base_url=str(payload.get("base_url") or ""),
        )

    def _analytics_flush(self, _payload: dict[str, Any]) -> dict[str, Any]:
        return self.analytics.flush_now()

    def _analytics_diagnostics(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "events": self.analytics.diagnostics(
                int(payload.get("limit") or 100)
            )
        }

    def _emit_analytics_error(
        self,
        action: str,
        exc: Exception,
    ) -> None:
        try:
            module = str(action).split(".", 1)[0] or "runtime_logs"
            payload = {
                "module_key": module,
                "error_category": type(exc).__name__,
                "count": 1,
                "last_seen_at": datetime.now().astimezone().isoformat(
                    timespec="seconds"
                ),
                "business_date": business_date(),
            }
            self.analytics.defer(
                lambda: self.analytics.emit(
                    module,
                    "error.summary",
                    payload,
                )
            )
        except Exception:
            return

    def _emit_dispatch_analytics(
        self,
        action: str,
        payload: dict[str, Any],
        result: dict[str, Any],
    ) -> None:
        try:
            self.analytics.defer(
                lambda: self._emit_dispatch_analytics_work(
                    action,
                    payload,
                    result,
                )
            )
        except Exception:
            return

    @staticmethod
    def _analytics_result_store_ids(
        domain: str,
        payload: dict[str, Any],
        result: dict[str, Any],
    ) -> set[int] | None:
        successful = result.get("successful_store_ids")
        if isinstance(successful, list):
            return {
                int(value)
                for value in successful
                if str(value).isdigit()
            }
        rows = result.get("stores")
        if domain == "products" and isinstance(rows, list):
            return {
                int(row.get("store_id"))
                for row in rows
                if isinstance(row, dict)
                and row.get("status") == "completed"
                and str(row.get("store_id") or "").isdigit()
            }
        requested_store = payload.get("store_id")
        if (
            domain in {"stores", "products"}
            and str(requested_store or "").isdigit()
            and str(result.get("status") or "completed")
            in {"completed", "partial"}
        ):
            return {int(requested_store)}
        return None

    def _publish_refresh_analytics(
        self,
        domain: str,
        payload: dict[str, Any],
        result: dict[str, Any],
        *,
        publish_key: str = "",
    ) -> bool:
        try:
            status = str(result.get("status") or "completed").casefold()
            if domain not in {"stores", "products", "orders"}:
                return False
            if status not in {"completed", "partial"}:
                return False
            if publish_key:
                now = time.monotonic()
                with self._analytics_publish_lock:
                    if publish_key in self._analytics_publish_keys:
                        return False
                    self._analytics_publish_keys[publish_key] = now
                    expired = [
                        key
                        for key, seen_at in self._analytics_publish_keys.items()
                        if now - seen_at >= ANALYTICS_PUBLISH_KEY_TTL_SECONDS
                    ]
                    for key in expired:
                        self._analytics_publish_keys.pop(key, None)
                    overflow = (
                        len(self._analytics_publish_keys)
                        - ANALYTICS_PUBLISH_KEY_LIMIT
                    )
                    if overflow > 0:
                        oldest = sorted(
                            self._analytics_publish_keys.items(),
                            key=lambda item: item[1],
                        )[:overflow]
                        for key, _seen_at in oldest:
                            self._analytics_publish_keys.pop(key, None)
            successful_store_ids = self._analytics_result_store_ids(
                domain,
                payload,
                result,
            )
            if domain == "stores":
                for store_id, raw_payload in store_daily_payloads(self.db):
                    if (
                        successful_store_ids is not None
                        and int(store_id) not in successful_store_ids
                    ):
                        continue
                    event_payload = dict(raw_payload)
                    observed = bool(event_payload.pop("_observed", False))
                    if not observed:
                        continue
                    self.analytics.emit(
                        "stores",
                        "store.daily_metrics",
                        event_payload,
                        store_ref=str(store_id),
                        observed=True,
                    )
            elif domain == "products":
                threshold = int(
                    self.db.get_setting(
                        "ops_analytics_product_exposure_threshold",
                        "1000",
                    )
                    or 1000
                )
                for store_id, event_payload in product_daily_payloads(
                    self.db,
                    exposure_threshold=threshold,
                ):
                    if (
                        successful_store_ids is not None
                        and int(store_id) not in successful_store_ids
                    ):
                        continue
                    self.analytics.emit(
                        "products",
                        "product.qualified_snapshot",
                        event_payload,
                        store_ref=str(store_id),
                    )
            else:
                self._emit_order_events(
                    result,
                    successful_store_ids=successful_store_ids,
                )
            return True
        except Exception:
            return False

    def _emit_dispatch_analytics_work(
        self,
        action: str,
        payload: dict[str, Any],
        result: dict[str, Any],
    ) -> None:
        try:
            module = str(action).split(".", 1)[0]
            if module == "analytics":
                return
            write_actions = {
                "stores.create",
                "stores.update",
                "stores.batch.commit",
                "stores.refresh.start",
                "stores.login",
                "orders.sync",
                "orders.quick-refresh.start",
                "orders.workbench.manual-purchase",
                "orders.procurement.link-upsert",
                "orders.procurement.link-delete",
                "orders.procurement.link-open",
                "orders.procurement.purchase-confirm",
                "orders.address.save-software",
                "orders.procurement.link-save",
                "orders.procurement.purchase-open",
                "orders.procurement.backfill",
                "orders.workbench.delivery-update",
                "orders.workbench.shipment-complete",
                "orders.after-sales.sync",
                "orders.after-sales.action",
                "products.refresh.start",
                "products.set-listing",
                "activity.apply",
                "activity.submit-items",
                "upload.tasks.create",
                "upload.run.status",
                "procurement.login",
                "procurement.sync",
                "hosted.execute",
                "automation.tasks.run-start",
                "storefront.action",
                "violations.refresh",
            }
            if action in write_actions:
                self.analytics.emit(
                    module,
                    "feature.usage_daily",
                    {
                        "feature_key": action,
                        "use_count": 1,
                        "task_count": int(
                            result.get("created")
                            or result.get("updated")
                            or result.get("succeeded")
                            or 0
                        ),
                        "error_count": int(
                            result.get("failed")
                            or len(result.get("errors") or [])
                        ),
                        "business_date": business_date(),
                    },
                )
            if action == "bootstrap":
                self.analytics.emit(
                    "runtime_logs",
                    "app.version_seen",
                    {
                        "app_version": __version__,
                        "first_seen_at": datetime.now().astimezone().isoformat(
                            timespec="seconds"
                        ),
                        "business_date": business_date(),
                    },
                )
                self.analytics.emit(
                    "runtime_logs",
                    "device.heartbeat",
                    {
                        "app_version": __version__,
                        "platform": sys.platform,
                        "os_family": os.name,
                        "business_date": business_date(),
                    },
                )
            if action in {"stores.refresh.status", "stores.refresh"}:
                job_ref = str(
                    result.get("job_id")
                    or payload.get("job_id")
                    or ""
                )
                self._publish_refresh_analytics(
                    "stores",
                    payload,
                    result,
                    publish_key=(
                        f"stores:{job_ref}"
                        if job_ref
                        else ""
                    ),
                )
            if action in {"products.refresh.status", "products.refresh"}:
                job_ref = str(
                    result.get("job_id")
                    or payload.get("job_id")
                    or ""
                )
                self._publish_refresh_analytics(
                    "products",
                    payload,
                    result,
                    publish_key=(
                        f"products:{job_ref}"
                        if job_ref
                        else ""
                    ),
                )
            if action == "orders.quick-refresh.status":
                job_ref = str(
                    result.get("job_id")
                    or payload.get("job_id")
                    or ""
                )
                self._publish_refresh_analytics(
                    "orders",
                    payload,
                    result,
                    publish_key=(
                        f"orders:{job_ref}"
                        if job_ref
                        else ""
                    ),
                )
            if action == "orders.sync":
                self._publish_refresh_analytics(
                    "orders",
                    payload,
                    result,
                )
            if action == "orders.refresh-one":
                order = result.get("order") or {}
                if order:
                    self._emit_order_event(
                        "order.status_changed",
                        order,
                        {
                            "from_status": "",
                            "to_status": order.get("status_text"),
                            "updated_at": datetime.now().astimezone().isoformat(
                                timespec="seconds"
                            ),
                        },
                    )
            if action == "upload.tasks.create":
                batch_ids = [
                    str(value)
                    for value in payload.get("batch_ids", [])
                    if str(value).strip()
                ]
                if int(result.get("created") or 0) > 0:
                    self.analytics.emit(
                        "upload",
                        "upload.batch_created",
                        {
                            "batch_ref": ",".join(batch_ids[:100]),
                            "total_count": len(batch_ids),
                            "selected_count": len(batch_ids),
                            "store_count": len(
                                payload.get("store_ids") or []
                            ),
                            "template_ref": str(
                                payload.get("template_id") or ""
                            ),
                            "created_at": datetime.now().astimezone().isoformat(
                                timespec="seconds"
                            ),
                        },
                        batch_id=",".join(batch_ids[:20]),
                    )
            if (
                action == "upload.run.status"
                and str(result.get("status") or "")
                in {"completed", "partial", "failed"}
            ):
                tasks = result.get("tasks") or []
                failed = sum(
                    1
                    for task in tasks
                    for item in task.get("items") or []
                    if item.get("status") == "failed"
                )
                success = sum(
                    1
                    for task in tasks
                    for item in task.get("items") or []
                    if item.get("status") == "success"
                )
                batch_ref = str(
                    payload.get("job_id")
                    or result.get("job_id")
                    or ""
                )
                event_type = (
                    "upload.batch_completed"
                    if result.get("status") == "completed"
                    else "upload.item_failed"
                )
                self.analytics.emit(
                    "upload",
                    event_type,
                    {
                        "batch_ref": batch_ref,
                        "task_ref": batch_ref,
                        "item_ref": batch_ref,
                        "task_count": len(tasks),
                        "success_count": success,
                        "failed_count": failed,
                        "error_category": (
                            "upload_partial"
                            if result.get("status") != "completed"
                            else ""
                        ),
                        "completed_at": datetime.now().astimezone().isoformat(
                            timespec="seconds"
                        ),
                        "failed_at": datetime.now().astimezone().isoformat(
                            timespec="seconds"
                        ),
                    },
                    batch_id=batch_ref,
                )
            if action == "orders.workbench.manual-purchase":
                purchase_order = self.db.get_order(
                    int(payload.get("store_id") or 0),
                    str(payload.get("order_id") or ""),
                )
                purchase_item = (
                    purchase_order.items[0]
                    if purchase_order is not None
                    and purchase_order.items
                    else None
                )
                self.analytics.emit(
                    "procurement",
                    "procurement.purchase_submitted",
                    {
                        "order_ref": str(
                            payload.get("order_id") or ""
                        ),
                        "product_id": str(
                            getattr(purchase_item, "product_id", "") or ""
                        ),
                        "sku_ref": str(
                            getattr(purchase_item, "sku_id", "") or ""
                        ),
                        "quantity": int(
                            getattr(purchase_item, "sku_count", 0) or 0
                        ),
                        "store_order_item_ref": (
                            f"{getattr(purchase_item, 'product_id', '')}:"
                            f"{getattr(purchase_item, 'sku_id', '')}"
                            if purchase_item is not None
                            else ""
                        ),
                        "procurement_ref": str(
                            result.get("purchase_order_no") or ""
                        ),
                        "platform": str(
                            payload.get("platform") or ""
                        ),
                        "account_ref": "",
                        "purchase_amount": int(
                            payload.get("purchase_amount_cents") or 0
                        ),
                        "submitted_at": datetime.now().astimezone().isoformat(
                            timespec="seconds"
                        ),
                        "verification_required": False,
                    },
                    store_ref=str(payload.get("store_id") or ""),
                )
            if action == "procurement.sync":
                account_id = payload.get("account_id")
                procurement_rows = (
                    self.db.list_procurement_orders(
                        account_id=int(account_id)
                    )
                    if str(account_id or "").isdigit()
                    else []
                )
                for procurement in procurement_rows[:100]:
                    procurement_ref = str(
                        procurement.get("external_order_id")
                        or procurement.get("trade_no")
                        or procurement.get("id")
                        or ""
                    )
                    related_tasks = [
                        task
                        for task in self.db.list_order_dispatch_tasks()
                        if int(task.get("procurement_order_id") or 0)
                        == int(procurement.get("id") or 0)
                    ] or [{}]
                    for task in related_tasks:
                        product_fields = {
                            "order_ref": str(
                                task.get("order_id") or ""
                            ),
                            "product_id": str(
                                task.get("product_id") or ""
                            ),
                            "sku_ref": str(
                                task.get("store_sku_id") or ""
                            ),
                            "quantity": int(
                                task.get("quantity") or 0
                            ),
                            "store_order_item_ref": (
                                f"{task.get('product_id') or ''}:"
                                f"{task.get('store_sku_id') or ''}"
                                if task.get("product_id")
                                else ""
                            ),
                        }
                        self.analytics.emit(
                            "procurement",
                            "procurement.supplier_status_changed",
                            {
                                "procurement_ref": procurement_ref,
                                **product_fields,
                                "from_status": "",
                                "to_status": str(
                                    procurement.get("status_text")
                                    or procurement.get("status")
                                    or ""
                                ),
                                "updated_at": str(
                                    procurement.get("updated_at") or ""
                                ),
                            },
                        )
                        if procurement.get("mail_no"):
                            self.analytics.emit(
                                "procurement",
                                "procurement.tracking_received",
                                {
                                    "procurement_ref": procurement_ref,
                                    **product_fields,
                                    "tracking_company": str(
                                        procurement.get("carrier") or ""
                                    ),
                                    "tracking_no": str(
                                        procurement.get("mail_no") or ""
                                    ),
                                    "received_at": str(
                                        procurement.get("updated_at") or ""
                                    ),
                                },
                            )
            if action == "orders.workbench.delivery-update":
                self.analytics.emit(
                    "logistics",
                    "logistics.shipment_changed",
                    {
                        "order_ref": str(payload.get("order_id") or ""),
                        "tracking_company": ",".join(
                            str(item.get("delivery_id") or "")
                            for item in payload.get("packages") or []
                            if isinstance(item, dict)
                        ),
                        "tracking_no": ",".join(
                            str(item.get("waybill_id") or "")
                            for item in payload.get("packages") or []
                            if isinstance(item, dict)
                        ),
                        "package_count": len(payload.get("packages") or []),
                        "logistics_status": "shipped",
                        "updated_at": datetime.now().astimezone().isoformat(
                            timespec="seconds"
                        ),
                    },
                    store_ref=str(payload.get("store_id") or ""),
                )
            if action == "products.set-listing" and bool(result):
                for product_id in list(payload.get("product_ids") or [])[:100]:
                    self.analytics.emit(
                        "products",
                        "product.listing_changed",
                        {
                            "product_id": str(product_id),
                            "from_status": "",
                            "to_status": (
                                "出售中"
                                if payload.get("listed")
                                else "已下架"
                            ),
                            "updated_at": datetime.now().astimezone().isoformat(
                                timespec="seconds"
                            ),
                        },
                        store_ref=str(payload.get("store_id") or ""),
                    )
            if action == "orders.after-sales.action":
                store_id = payload.get("store_id")
                after_sale_id = str(
                    payload.get("after_sale_id") or ""
                )
                row = (
                    self.db.get_after_sale(int(store_id), after_sale_id)
                    if str(store_id or "").isdigit() and after_sale_id
                    else None
                )
                if row:
                    self.analytics.emit(
                        "after_sales",
                        "order.after_sale_changed",
                        {
                            "order_ref": str(row.get("order_id") or ""),
                            "after_sale_ref": after_sale_id,
                            "after_sale_type": str(
                                row.get("after_sale_type") or ""
                            ),
                            "status": str(row.get("status_text") or ""),
                            "refund_amount": int(
                                row.get("refund_amount") or 0
                            ),
                            "updated_at": datetime.now().astimezone().isoformat(
                                timespec="seconds"
                            ),
                        },
                        store_ref=str(store_id),
                    )
        except Exception:
            return

    def _emit_order_events(
        self,
        result: dict[str, Any],
        *,
        successful_store_ids: set[int] | None = None,
    ) -> None:
        try:
            status_changes = result.get("status_changes") or []
            for change in status_changes[:100]:
                if not isinstance(change, dict):
                    continue
                if (
                    successful_store_ids is not None
                    and str(change.get("store_id") or "").isdigit()
                    and int(change["store_id"]) not in successful_store_ids
                ):
                    continue
                order = next(
                    (
                        row
                        for row in result.get("orders") or []
                        if str(row.get("store_id"))
                        == str(change.get("store_id"))
                        and str(row.get("order_id"))
                        == str(change.get("order_id"))
                    ),
                    {},
                )
                self._emit_order_event(
                    "order.status_changed",
                    order,
                    {
                        "from_status": change.get("before_status_text"),
                        "to_status": change.get("after_status_text"),
                        "updated_at": datetime.now().astimezone().isoformat(
                            timespec="seconds"
                        ),
                    },
                )
            recent_orders = [
                row
                for row in result.get("orders") or []
                if (
                    successful_store_ids is None
                    or str(row.get("store_id") or "").isdigit()
                    and int(row["store_id"]) in successful_store_ids
                )
                if int(row.get("create_time") or 0)
                >= int(
                    datetime.now()
                    .replace(hour=0, minute=0, second=0, microsecond=0)
                    .timestamp()
                )
            ][:100]
            for order in recent_orders:
                self._emit_order_event("order.created", order, {})
        except Exception:
            return

    def _emit_order_event(
        self,
        event_type: str,
        order: dict[str, Any],
        extra: dict[str, Any],
    ) -> None:
        if not order:
            return
        items = []
        store_id = int(order.get("store_id") or 0)
        order_id = str(order.get("order_id") or "")
        record = self.db.get_order(store_id, order_id)
        if record is not None:
            items = [
                {
                    "line_ref": (
                        f"{item.product_id}:{item.sku_id}"
                    ),
                    "product_id": str(item.product_id),
                    "sku_ref": str(item.sku_id),
                    "quantity": int(item.sku_count or 0),
                    "sales_amount": int(item.sale_price or 0)
                    * int(item.sku_count or 0),
                    "title": str(item.title or ""),
                }
                for item in record.items[:50]
            ]
        common = {
            "order_ref": str(order.get("order_id") or ""),
            "items": items,
            "status": str(order.get("status_text") or ""),
            "amount": int(order.get("total_amount") or 0),
            "item_count": sum(
                int(item.get("quantity") or 0)
                for item in items
            ),
            "created_at": str(order.get("create_time") or ""),
            "paid_at": str(order.get("pay_time") or ""),
            "after_sale": bool(order.get("after_sale_count")),
            "source_tab": "orders",
            "currency_code": "CNY",
            **extra,
        }
        self.analytics.emit(
            "orders",
            event_type,
            common,
            store_ref=str(order.get("store_id") or ""),
        )

    @staticmethod
    def _procurement_reference(
        procurement: dict[str, Any] | None,
    ) -> str:
        row = procurement or {}
        return str(
            row.get("external_order_id")
            or row.get("trade_no")
            or row.get("id")
            or ""
        )

    def _emit_procurement_task_event(
        self,
        event_type: str,
        task: dict[str, Any],
        *,
        procurement_ref: str = "",
        **extra: Any,
    ) -> None:
        if not task:
            return
        product_id = str(task.get("product_id") or "")
        sku_ref = str(task.get("store_sku_id") or "")
        self.analytics.emit(
            "procurement",
            event_type,
            {
                "order_ref": str(task.get("order_id") or ""),
                "product_id": product_id,
                "sku_ref": sku_ref,
                "quantity": int(task.get("quantity") or 0),
                "store_order_item_ref": (
                    f"{product_id}:{sku_ref}"
                    if product_id and sku_ref
                    else ""
                ),
                "procurement_ref": procurement_ref,
                **extra,
            },
            store_ref=str(task.get("store_id") or ""),
        )

    def _logs_status(self, _payload: dict[str, Any]) -> dict[str, Any]:
        manager = get_runtime_log_manager()
        if manager is None:
            raise RuntimeError("运行日志尚未初始化")
        return manager.stats()

    def _record_webui_events(self, payload: dict[str, Any]) -> dict[str, Any]:
        manager = get_runtime_log_manager()
        if manager is None:
            return {"accepted": 0}
        events = payload.get("events")
        if not isinstance(events, list):
            raise ValueError("WebUI 日志事件格式不正确")
        accepted = 0
        for event in events[:100]:
            if isinstance(event, dict):
                manager.webui_event(event)
                accepted += 1
        return {"accepted": accepted}

    def _export_logs(self, payload: dict[str, Any]) -> dict[str, Any]:
        manager = get_runtime_log_manager()
        if manager is None:
            raise RuntimeError("运行日志尚未初始化")
        days = 7 if int(payload.get("days") or 1) == 7 else 1
        path = manager.export(days)
        log_event(
            "logs.exported_by_user",
            days=days,
            path=str(path),
        )
        return {
            "path": str(path),
            "days": days,
            "size": path.stat().st_size,
        }

    def _open_log_directory(self, _payload: dict[str, Any]) -> dict[str, Any]:
        manager = get_runtime_log_manager()
        if manager is None:
            raise RuntimeError("运行日志尚未初始化")
        if os.name == "nt":
            os.startfile(str(manager.log_root))
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(manager.log_root)])
        else:
            subprocess.Popen(["xdg-open", str(manager.log_root)])
        return {"path": str(manager.log_root)}

    def _open_big_screen(
        self,
        _payload: dict[str, Any],
    ) -> dict[str, Any]:
        process = self._big_screen_process
        if process is not None and process.poll() is None:
            return {
                "opened": False,
                "already_running": True,
                "pid": process.pid,
                "message": "数据大屏已在运行，请切换到已打开的大屏窗口",
                "data_dir": str(self.db.path.parent),
            }
        data_dir = self.db.path.parent.resolve()
        frozen = bool(getattr(sys, "frozen", False))
        if frozen:
            command = [
                sys.executable,
                "--big-screen",
                "--parent-pid",
                str(os.getpid()),
                "--data-dir",
                str(data_dir),
            ]
        else:
            command = [
                sys.executable,
                "-m",
                "weimi_manager.big_screen",
                "--parent-pid",
                str(os.getpid()),
                "--data-dir",
                str(data_dir),
            ]
        environment = os.environ.copy()
        environment["WEIMI_DATA_DIR"] = str(data_dir)
        flags = (
            getattr(subprocess, "CREATE_NO_WINDOW", 0)
            if os.name == "nt"
            else 0
        )
        self._big_screen_process = subprocess.Popen(
            command,
            cwd=str(self.project_dir),
            env=environment,
            creationflags=flags,
        )
        log_event(
            "dashboard.big_screen.opened",
            pid=self._big_screen_process.pid,
            data_dir=str(data_dir),
            frozen=frozen,
        )
        return {
            "opened": True,
            "already_running": False,
            "pid": self._big_screen_process.pid,
            "message": "数据大屏正在打开",
            "data_dir": str(data_dir),
        }

    def _write_clipboard(self, payload: dict[str, Any]) -> dict[str, Any]:
        if "text" not in payload:
            raise ValueError("text 不能为空")
        return write_clipboard_text(str(payload.get("text") or ""))

    def _check_update(self, payload: dict[str, Any]) -> dict[str, Any]:
        manual = bool(payload.get("manual", False))
        log_event("update.webui.check_started", manual=manual)
        info = check_for_update(
            "",
            self.project_dir,
        )
        self._last_update_info = info
        log_event(
            "update.webui.checked",
            manual=manual,
            current_version=info.current_version,
            latest_version=info.latest_version,
            available=info.available,
            mandatory=info.mandatory,
        )
        return asdict(info)

    def _start_update_install(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        info = self._last_update_info
        requested_version = str(payload.get("latest_version") or "").strip()
        if (
            info is None
            or not info.available
            or (
                requested_version
                and requested_version != info.latest_version
            )
        ):
            info = check_for_update(
                "",
                self.project_dir,
            )
            self._last_update_info = info
        if not info.available or not info.download_url:
            raise ValueError("当前没有可安装的更新")
        with self._update_jobs_lock:
            if any(
                str(job.get("status") or "")
                in {"queued", "downloading", "verifying", "extracting", "installing"}
                for job in self._update_jobs.values()
            ):
                raise ValueError("更新任务已经开始")
            job_id = f"update-{uuid.uuid4().hex[:12]}"
            self._update_jobs[job_id] = {
                "job_id": job_id,
                "status": "queued",
                "message": "正在准备更新…",
                "current": 0,
                "total": 0,
                "latest_version": info.latest_version,
                "error": "",
                "started_at": time.time(),
            }
        log_event(
            "update.webui.install_clicked",
            latest_version=info.latest_version,
        )

        def update_job(**changes: Any) -> None:
            with self._update_jobs_lock:
                current = self._update_jobs.get(job_id)
                if current is not None:
                    current.update(changes)

        def progress(message: str, current: int, total: int) -> None:
            status = (
                "verifying"
                if "校验" in message
                else "extracting"
                if "解压" in message
                else "downloading"
            )
            with self._update_jobs_lock:
                previous_stage = str(
                    (self._update_jobs.get(job_id) or {}).get("message")
                    or ""
                )
            if previous_stage != message:
                log_event(
                    "update.webui.stage",
                    latest_version=info.latest_version,
                    stage=message,
                    current=current,
                    total=total,
                )
            update_job(
                status=status,
                message=message,
                current=current,
                total=total,
            )

        def worker() -> None:
            try:
                staged: StagedUpdate = download_and_stage_update(
                    info,
                    self.project_dir,
                    progress=progress,
                )
                update_job(
                    status="installing",
                    message="更新包已通过校验，正在自动安装并重启",
                    current=1,
                    total=1,
                )
                log_event(
                    "update.webui.install_ready",
                    latest_version=staged.version,
                )
                log_path = launch_update_installer(
                    staged,
                    self.project_dir,
                )
                log_event(
                    "update.webui.installer_started",
                    latest_version=staged.version,
                    log_path=str(log_path),
                )
                callback = self.account_action_handler
                if callback is not None:
                    threading.Timer(
                        1.5,
                        lambda: callback("update"),
                    ).start()
            except Exception as exc:
                update_job(
                    status="failed",
                    message="自动更新失败",
                    error=str(exc),
                )
                log_event(
                    "update.webui.install_failed",
                    latest_version=info.latest_version,
                    error=str(exc),
                )

        threading.Thread(
            target=worker,
            daemon=True,
            name=f"langlang-webui-update-{job_id}",
        ).start()
        return {"job_id": job_id}

    def _update_install_status(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        job_id = str(payload.get("job_id") or "")
        with self._update_jobs_lock:
            job = self._update_jobs.get(job_id)
            if job is None:
                raise ValueError("更新任务不存在")
            return dict(job)

    def update_install_in_progress(self) -> bool:
        with self._update_jobs_lock:
            return any(
                str(job.get("status") or "")
                in {
                    "queued",
                    "downloading",
                    "verifying",
                    "extracting",
                    "installing",
                }
                for job in self._update_jobs.values()
            )

    def _bootstrap(self, _payload: dict[str, Any]) -> dict[str, Any]:
        role, permissions = self._current_member_permissions(
            refresh=True,
            fail_closed=False,
        )
        self._pull_server_capabilities(force=True)
        operations_permissions = {
            key: bool(
                role in OPERATION_ROLE_DEFAULTS or permissions.get(key)
            )
            for key in OPERATION_PERMISSION_KEYS
        }
        entitlements = self._current_entitlements(role, permissions)
        account = self._account_public(role)
        self._schedule_upload_category_resume(self._upload_tasks())
        return {
            "brand": {"name": "澜琅", "version": __version__},
            "account_label": self.account_label,
            "account": account,
            "member_role": role,
            "permissions": permissions,
            "operations_permissions": operations_permissions,
            "entitlements": entitlements,
            "capabilities": entitlements,
            "accessible_modules": [
                key
                for key, _label, _icon, _path in PRIMARY_NAV
                if self._module_entitlement_allowed(key, entitlements)
            ] + [
                key
                for key, _label, _icon, _path in MORE_NAV
                if self._module_entitlement_allowed(key, entitlements)
                and (
                    key != "operations"
                    or bool(operations_permissions["operations_view"])
                )
            ],
            "primary_nav": [
                {
                    "key": key,
                    "label": label,
                    "icon": icon,
                    "path": path,
                    "enabled": bool(path),
                }
                for key, label, icon, path in PRIMARY_NAV
                if self._module_entitlement_allowed(key, entitlements)
            ],
            "more_nav": [
                {
                    "key": key,
                    "label": label,
                    "icon": icon,
                    "path": path,
                    "enabled": bool(path),
                }
                for key, label, icon, path in MORE_NAV
                if (
                    key != "operations"
                    or bool(operations_permissions["operations_view"])
                )
                and self._module_entitlement_allowed(
                    key,
                    entitlements,
                )
            ],
            "isolated_modules": list(ISOLATED_MODULES),
        }

    def _pull_server_capabilities(
        self,
        *,
        force: bool = False,
    ) -> dict[str, bool]:
        now = time.monotonic()
        if (
            not force
            and now - self._server_capability_checked_at < 30
        ):
            return dict(self._server_capability_grants)
        context = self.auth_context
        tokens = getattr(context, "tokens", None)
        access_token = str(
            getattr(tokens, "access_token", "") or ""
        )
        reader = getattr(
            self.auth_client,
            "my_ops_capabilities",
            None,
        )
        can_pull = bool(
            not getattr(context, "offline", False)
            and access_token
            and callable(reader)
        )
        grants: dict[str, bool] = {}
        if can_pull:
            try:
                result = reader(access_token)
            except Exception:
                result = {}
            if isinstance(result, dict):
                grants = {
                    str(key): bool(value)
                    for key, value in result.items()
                    if str(key).strip()
                }
        self._server_capability_grants = grants
        self._server_capability_authoritative = can_pull
        self._server_capability_checked_at = now
        return dict(grants)

    def _refresh_capabilities(
        self,
        _payload: dict[str, Any],
    ) -> dict[str, Any]:
        role, permissions = self._current_member_permissions(
            refresh=True,
            fail_closed=True,
        )
        self._pull_server_capabilities(force=True)
        entitlements = self._current_entitlements(role, permissions)
        return {
            "entitlements": entitlements,
            "capabilities": entitlements,
        }

    def _current_entitlements(
        self,
        role: str,
        permissions: dict[str, Any],
    ) -> dict[str, bool]:
        context = self.auth_context
        account_id = str(
            getattr(context, "account_id", "")
            or ""
        ).strip().casefold()
        offline = bool(getattr(context, "offline", False))
        explicit = getattr(context, "entitlements", {})
        if not isinstance(explicit, dict):
            explicit = {}
        server_grants = dict(self._server_capability_grants)
        trusted_role = str(role or "").strip().casefold() in {
            "owner",
            "admin",
        }
        internal = (
            account_id in INTERNAL_ACCOUNT_IDS
            and trusted_role
            and not offline
        )
        result: dict[str, bool] = {}
        for key in INTERNAL_CAPABILITIES:
            granted = bool(
                (
                    server_grants.get(key)
                    if self._server_capability_authoritative
                    else explicit.get(key)
                )
                or permissions.get(key)
            )
            result[key] = bool(
                not offline
                and trusted_role
                and (internal or granted)
            )
        if PROCUREMENT_ASSOCIATION_ENTITLEMENT in result:
            result[PROCUREMENT_ASSOCIATION_ENTITLEMENT] = bool(
                not offline
                and trusted_role
                and self._procurement_local_experiment_enabled()
            )
        return result

    def _procurement_local_experiment_enabled(self) -> bool:
        # Unknown accounts are closed by default; the named local experiment
        # account is enabled unless its local switch explicitly says off.
        context = self.auth_context
        username = str(
            getattr(context, "username", "") or self.account_label or ""
        ).strip().casefold()
        if username not in LOCAL_PROCUREMENT_ASSOCIATION_USERNAMES:
            return False
        setting = str(
            self.db.get_setting(
                LOCAL_PROCUREMENT_ASSOCIATION_SETTING,
                "1",
            )
            or ""
        ).strip().casefold()
        return setting not in {"", "0", "false", "off", "disabled"}

    def _procurement_association_enabled(self) -> bool:
        role, permissions = self._current_member_permissions(
            refresh=False,
            fail_closed=False,
        )
        self._pull_server_capabilities(force=False)
        entitlements = self._current_entitlements(role, permissions)
        return bool(
            entitlements.get(
                PROCUREMENT_ASSOCIATION_ENTITLEMENT,
                False,
            )
        )

    def _require_procurement_association(self) -> None:
        if not self._procurement_association_enabled():
            raise PermissionError("此功能正在加急开发优化中")
        self._require_entitlement(PROCUREMENT_ASSOCIATION_ENTITLEMENT)

    @staticmethod
    def _module_entitlement_allowed(
        module_key: str,
        entitlements: dict[str, bool],
    ) -> bool:
        entitlement = INTERNAL_MODULE_ENTITLEMENTS.get(module_key)
        return not entitlement or bool(entitlements.get(entitlement))

    def _require_entitlement(self, entitlement: str) -> None:
        role, permissions = self._current_member_permissions(
            refresh=True,
            fail_closed=False,
        )
        self._pull_server_capabilities(force=True)
        entitlements = self._current_entitlements(role, permissions)
        if not entitlements.get(entitlement):
            raise PermissionError("当前账号无权访问该内部功能")

    def _account_public(self, role: str) -> dict[str, Any]:
        context = self.auth_context
        license_value = getattr(context, "license", None)
        license_payload = (
            asdict(license_value)
            if license_value is not None
            and hasattr(license_value, "__dataclass_fields__")
            else {}
        )
        return {
            "account_id": str(getattr(context, "account_id", "") or ""),
            "username": str(
                getattr(context, "username", "") or self.account_label or ""
            ),
            "email": str(getattr(context, "email", "") or ""),
            "offline": bool(getattr(context, "offline", False)),
            "organization_name": str(
                getattr(context, "organization_name", "") or ""
            ),
            "member_role": role,
            "license": license_payload,
        }

    def _delivery_ledger(self, _payload: dict[str, Any]) -> dict[str, Any]:
        return delivery_ledger_payload(
            self.db,
            account=self._account_public(self._member_role),
        )

    @staticmethod
    def _job_is_active(job: dict[str, Any]) -> bool:
        return (
            bool(job)
            and str(job.get("status") or "")
            not in ACCOUNT_TASK_TERMINAL_STATUSES | {"paused"}
        )

    def _active_account_tasks(self) -> list[dict[str, Any]]:
        groups = (
            ("店铺刷新", self._store_jobs, self._store_jobs_lock),
            ("商品刷新", self._product_jobs, self._product_jobs_lock),
            ("商品批量操作", self._product_batch_jobs, self._product_batch_jobs_lock),
            ("订单刷新", self._order_jobs, self._order_jobs_lock),
            ("上传任务", self._upload_jobs, self._upload_jobs_lock),
            ("自动化", self._automation_jobs, self._automation_jobs_lock),
            (
                "活动续期",
                self._activity_renewal_jobs,
                self._activity_renewal_jobs_lock,
            ),
            ("软件更新", self._update_jobs, self._update_jobs_lock),
        )
        output: list[dict[str, Any]] = []
        for label, jobs, lock in groups:
            with lock:
                values = list(jobs.values())
            for job in values:
                if not self._job_is_active(job):
                    continue
                output.append(
                    {
                        "kind": label,
                        "job_id": str(job.get("job_id") or ""),
                        "status": str(job.get("status") or ""),
                        "message": str(job.get("message") or ""),
                        "account_id": str(
                            job.get("account_id")
                            or self._account_id()
                        ),
                        "generation": int(
                            job.get("generation")
                            or self._account_generation
                        ),
                    }
                )
        return output

    def _account_task_status(self, _payload: dict[str, Any]) -> dict[str, Any]:
        tasks = self._active_account_tasks()
        with self._account_session_lock:
            closing = self._account_session_closing
        return {
            "active": bool(tasks),
            "count": len(tasks),
            "tasks": tasks,
            "closing": closing,
            "policy": (
                "可以立即安全切换；旧账号后台任务会暂停并保留，晚到结果会丢弃。"
                if tasks
                else "当前没有活跃后台任务，可以安全切换账户。"
            ),
        }

    def _freeze_account_work(self, action: str) -> list[dict[str, Any]]:
        with self._account_session_lock:
            self._account_session_closing = True
            self._account_generation += 1
        interrupted = self._active_account_tasks()
        groups = (
            ("店铺刷新", self._store_jobs, self._store_jobs_lock),
            ("商品刷新", self._product_jobs, self._product_jobs_lock),
            ("商品批量操作", self._product_batch_jobs, self._product_batch_jobs_lock),
            ("订单刷新", self._order_jobs, self._order_jobs_lock),
            ("上传任务", self._upload_jobs, self._upload_jobs_lock),
            ("自动化", self._automation_jobs, self._automation_jobs_lock),
            ("活动续期", self._activity_renewal_jobs, self._activity_renewal_jobs_lock),
            ("软件更新", self._update_jobs, self._update_jobs_lock),
        )
        interrupted_at = datetime.now().astimezone().isoformat(
            timespec="seconds"
        )
        for _label, jobs, lock in groups:
            with lock:
                for job in jobs.values():
                    if not self._job_is_active(job):
                        continue
                    job.setdefault(
                        "previous_status",
                        str(job.get("status") or ""),
                    )
                    job.setdefault(
                        "account_id",
                        self._account_id(),
                    )
                    job.setdefault(
                        "generation",
                        max(0, self._account_generation - 1),
                    )
                    job["status"] = "paused"
                    job["paused_at"] = interrupted_at
                    job["paused_for_account_action"] = action
                    job["resume_requires_account"] = self._account_id()
                    job["message"] = (
                        "账户会话已切换，旧后台任务已暂停并保留；"
                        "晚到结果将丢弃"
                    )
                    runner = job.get("runner")
                    paused = getattr(runner, "paused", None)
                    if paused is not None and hasattr(paused, "set"):
                        paused.set()
                    cancel_event = job.get("_cancel_event")
                    if isinstance(cancel_event, threading.Event):
                        cancel_event.set()
                    if job.get("kind") in {"refresh", "batch"}:
                        self._persist_product_task(job)
        with self._upload_category_timer_lock:
            if (
                self._upload_category_timer is not None
                and self._upload_category_timer.is_alive()
            ):
                self._upload_category_timer.cancel()
            self._upload_category_timer = None
        try:
            self.db.set_setting(
                "account_handoff_state",
                json.dumps(
                    {
                        "action": action,
                        "account_id": self._account_id(),
                        "generation": self._account_generation,
                        "interrupted_at": interrupted_at,
                        "tasks": interrupted,
                    },
                    ensure_ascii=False,
                ),
            )
        except Exception:
            pass
        return interrupted

    def _account_action(self, payload: dict[str, Any]) -> dict[str, Any]:
        action = str(payload.get("action") or "").strip()
        if action not in {"switch", "logout"}:
            raise ValueError("账户操作只能是 switch 或 logout")
        if action == "switch":
            self._require_entitlement("account.view_others")
        if self.auth_context is None:
            raise ValueError("当前工作台没有可切换的登录账户")
        log_event(
            f"account.{action}_requested",
            account=getattr(self.auth_context, "username", ""),
        )
        active_tasks = self._freeze_account_work(action)
        logout_error = ""
        if action == "logout":
            service = getattr(self, "auth_service", None)
            if service is None:
                logout_error = "退出登录服务尚未配置；本机会话仍将关闭"
            else:
                try:
                    service.logout(
                        self.auth_context,
                        forget_local=True,
                    )
                except Exception as exc:
                    logout_error = str(exc)[:500]
            log_event(
                "account.logout_succeeded",
                account=getattr(self.auth_context, "username", ""),
                remote_error=logout_error,
            )
        self.requested_account_action = action
        callback = self.account_action_handler
        if callable(callback):
            callback(action)
        log_event(
            f"account.{action}_handoff",
            account=getattr(self.auth_context, "username", ""),
        )
        return {
            "accepted": True,
            "action": action,
            "forget_local": action == "logout",
            "active_tasks": active_tasks,
            "paused_task_count": len(active_tasks),
            "generation": self._account_generation,
            "logout_error": logout_error,
        }

    def _current_member_permissions(
        self,
        *,
        refresh: bool,
        fail_closed: bool = True,
    ) -> tuple[str, dict[str, bool]]:
        role = self._member_role
        permissions = dict(self._member_permissions)
        context = self.auth_context
        tokens = getattr(context, "tokens", None)
        access_token = str(getattr(tokens, "access_token", "") or "")
        organization_context = getattr(self.auth_client, "organization_context", None)
        if refresh and access_token and callable(organization_context):
            try:
                organization = organization_context(access_token)
            except Exception as exc:
                if fail_closed:
                    raise AuthError(
                        "暂时无法确认运营中心权限，请检查网络后重试。",
                        503,
                        "OPERATIONS_PERMISSION_UNAVAILABLE",
                    ) from exc
                role = ""
                permissions = {
                    **permissions,
                    **{key: False for key in OPERATION_PERMISSION_KEYS},
                }
            else:
                fresh_role = str(organization.get("role") or "").strip()
                fresh_permissions = organization.get("permissions")
                if not fresh_role or not isinstance(fresh_permissions, dict):
                    if fail_closed:
                        raise AuthError(
                            "运营中心权限数据不完整，已拒绝访问。",
                            403,
                            "OPERATIONS_PERMISSION_INVALID",
                        )
                else:
                    role = fresh_role
                    permissions = {
                        str(key): bool(value)
                        for key, value in fresh_permissions.items()
                    }
                    self._member_role = role
                    self._member_permissions = dict(permissions)
        if role in OPERATION_ROLE_DEFAULTS:
            permissions.update({key: True for key in OPERATION_PERMISSION_KEYS})
        else:
            permissions.update(
                {
                    key: bool(permissions.get(key))
                    for key in OPERATION_PERMISSION_KEYS
                }
            )
        return role, permissions

    def _require_operations(self, permission: str = "operations_view") -> None:
        _role, permissions = self._current_member_permissions(refresh=True)
        if not permissions.get(permission):
            raise AuthError(
                "运营中心访问权限已关闭，请联系主账号。",
                403,
                "OPERATIONS_FORBIDDEN",
            )

    def _require_team_admin(self) -> tuple[str, dict[str, bool]]:
        role, permissions = self._current_member_permissions(refresh=True)
        if role not in OPERATION_ROLE_DEFAULTS:
            raise AuthError(
                "只有主账号或共同主账号可以修改运营中心权限。",
                403,
                "TEAM_ADMIN_REQUIRED",
            )
        return role, permissions

    def _operations_dashboard(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._require_operations()
        return dashboard_payload(self.db, payload)

    def _operations_products(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._require_operations()
        return products_payload(self.db, payload)

    def _operations_store_detail(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._require_operations()
        return store_detail_payload(self.db, payload)

    def _operations_product_detail(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._require_operations()
        return product_detail_payload(self.db, payload)

    def _operations_tasks_list(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._require_operations()
        store_id = payload.get("store_id")
        tasks = self.db.list_operation_tasks(
            status=str(payload.get("status") or ""),
            store_id=int(store_id) if store_id not in (None, "") else None,
            product_id=str(payload.get("product_id") or ""),
            priority=str(payload.get("priority") or ""),
            category=str(payload.get("category") or ""),
            search=str(payload.get("search") or ""),
        )
        return {
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "tasks": tasks,
            "total": len(tasks),
        }

    def _operations_tasks_create(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._require_operations("operations_task_manage")
        title = str(payload.get("title") or "").strip()
        if not title:
            raise ValueError("任务标题不能为空")
        priority = str(payload.get("priority") or "P1").upper()
        if priority not in {"P0", "P1", "P2"}:
            raise ValueError("任务优先级必须是 P0、P1 或 P2")
        store_id = payload.get("store_id")
        normalized_store_id = (
            int(store_id) if store_id not in (None, "") else None
        )
        if normalized_store_id is not None and self.db.get_store(normalized_store_id) is None:
            raise ValueError("任务店铺不存在")
        product_id = str(payload.get("product_id") or "").strip()
        if product_id and normalized_store_id is None:
            raise ValueError("商品任务必须指定店铺")
        if (
            product_id
            and normalized_store_id is not None
            and self.db.get_product(normalized_store_id, product_id) is None
        ):
            raise ValueError("任务商品不存在或不属于当前店铺")
        evidence = payload.get("evidence_json", payload.get("evidence", {}))
        evidence_json = (
            json.dumps(evidence, ensure_ascii=False)
            if isinstance(evidence, (dict, list))
            else str(evidence or "{}")
        )
        task = self.db.create_operation_task(
            store_id=normalized_store_id,
            product_id=product_id,
            alert_key=str(payload.get("alert_key") or uuid.uuid4().hex),
            category=str(payload.get("category") or "运营动作"),
            title=title,
            detail=str(payload.get("detail") or ""),
            priority=priority,
            evidence_json=evidence_json,
            due_at=str(payload.get("due_at") or "") or None,
            created_by=self.account_label or self._member_role,
        )
        log_id = self.db.start_task_log(
            "operations",
            "create_task",
            task["title"],
            store_id=normalized_store_id,
            detail=json.dumps(task, ensure_ascii=False),
        )
        self.db.finish_task_log(log_id, "success", "运营任务已创建")
        return {"task": task}

    def _operations_tasks_update(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._require_operations("operations_task_manage")
        try:
            task_id = int(payload.get("task_id") or payload.get("id") or 0)
        except (TypeError, ValueError) as exc:
            raise ValueError("任务 ID 无效") from exc
        status = str(payload.get("status") or "").strip()
        if status not in {"open", "done", "delayed"}:
            raise ValueError("任务状态必须是 open、done 或 delayed")
        due_at = str(payload.get("due_at") or "") or None
        if status == "delayed" and not due_at:
            raise ValueError("延期任务必须提供新的到期时间")
        task = self.db.update_operation_task(
            task_id,
            status=status,
            due_at=due_at,
            completed_by=(
                self.account_label or self._member_role
                if status == "done"
                else ""
            ),
        )
        log_id = self.db.start_task_log(
            "operations",
            "update_task",
            task["title"],
            store_id=task.get("store_id"),
            detail=json.dumps(
                {
                    "task_id": task_id,
                    "status": status,
                    "due_at": due_at,
                },
                ensure_ascii=False,
            ),
        )
        self.db.finish_task_log(log_id, "success", f"任务状态已更新为 {status}")
        return {"task": task}

    def _operations_rules_get(self, _payload: dict[str, Any]) -> dict[str, Any]:
        self._require_operations()
        return rules_payload(self.db)

    def _operations_rules_save(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._require_operations("operations_rule_manage")
        values = validate_rules(payload)
        stored = self.db.save_operation_rules(values)
        log_id = self.db.start_task_log(
            "operations",
            "save_rules",
            "巡检规则已更新",
            detail=json.dumps(stored, ensure_ascii=False),
        )
        self.db.finish_task_log(log_id, "success", "巡检规则已保存")
        return rules_payload(self.db)

    def _operations_export(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._require_operations("operations_export")
        scope = str(payload.get("scope") or "stores")
        if scope == "tasks":
            store_id = payload.get("store_id")
            rows = self.db.list_operation_tasks(
                status=str(payload.get("status") or ""),
                store_id=int(store_id) if store_id not in (None, "") else None,
                priority=str(payload.get("priority") or ""),
                category=str(payload.get("category") or ""),
                search=str(payload.get("search") or ""),
            )
        elif scope == "products":
            rows = products_payload(self.db, payload)["products"]
        elif scope == "stores":
            dashboard = dashboard_payload(self.db, payload)
            rows = dashboard["stores"]
            status = str(payload.get("status") or "")
            if status in {"risk", "watch", "ok"}:
                rows = [
                    row
                    for row in rows
                    if (row.get("alert_level") or "ok") == status
                ]
        else:
            raise ValueError("不支持的运营导出范围")
        content, filename = export_csv(scope, rows)
        log_id = self.db.start_task_log(
            "operations",
            "export",
            f"{scope} 导出 {len(rows)} 条",
            detail=filename,
        )
        self.db.finish_task_log(log_id, "success", "运营数据已导出")
        return {
            "content": content,
            "filename": filename,
            "count": len(rows),
            "scope": scope,
        }

    def _operations_inspect(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._require_operations()
        started = time.monotonic()
        result = dashboard_payload(self.db, payload)
        job_id = ""
        refresh_error = ""
        if self.product_service is not None:
            requested = {
                str(value)
                for value in payload.get("store_ids") or []
                if str(value).isdigit()
            }
            refresh_ids = [
                int(row["store_id"])
                for row in result["stores"]
                if str(row.get("web_status") or "") == "已登录"
                and (
                    not requested
                    or str(row["store_id"]) in requested
                )
            ]
            if refresh_ids:
                try:
                    job_id = self._start_store_refresh(
                        {"store_ids": refresh_ids}
                    )["job_id"]
                except Exception as exc:
                    refresh_error = str(exc)
        log_id = self.db.start_task_log(
            "operations",
            "inspect",
            "本地运营巡检完成",
            detail=str(payload.get("search") or ""),
        )
        self.db.finish_task_log(
            log_id,
            "success",
            f"生成 {len(result['alerts'])} 项真实证据告警",
            detail=(
                f"refresh_job={job_id}; "
                f"refresh_error={refresh_error}; "
                f"stores={len(result['stores'])}"
            ),
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        return {
            "generated_at": result["generated_at"],
            "alert_count": len(result["alerts"]),
            "alerts": result["alerts"],
            "job_id": job_id,
            "refresh_error": refresh_error,
            "message": (
                "已启动真实店铺刷新并完成本地巡检"
                if job_id
                else "已完成本地巡检"
            ),
        }

    def _operations_inspect_status(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._require_operations()
        return self._store_refresh_status(payload)

    def _operations_open_backend(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._require_operations("operations_open_backend")
        session = self._require_web_session()
        store = self._require_store(self._store_id(payload))
        target = str(payload.get("target") or "store")
        path = OPERATION_BACKEND_TARGETS.get(target)
        if path is None:
            raise ValueError("不支持的官方后台目标")
        product_id = str(payload.get("product_id") or "").strip()
        if target in {"product", "product_stats", "content", "growth"} and not product_id:
            raise ValueError("商品级深链必须提供商品 ID")
        if product_id and self.db.get_product(store.id, product_id) is None:
            raise ValueError("商品不存在或不属于当前店铺")
        log_id = self.db.start_task_log(
            "operations",
            "open_backend",
            f"正在打开 {target}",
            store_id=store.id,
            detail=json.dumps(
                {"target": target, "product_id": product_id},
                ensure_ascii=False,
            ),
        )
        started = time.monotonic()

        def open_backend() -> None:
            try:
                if target in {"product", "product_stats", "content", "growth"}:
                    session.open_official_product(
                        store,
                        product_id,
                        target=target,
                    )
                else:
                    session.open_official_backend(
                        store,
                        f"https://store.weixin.qq.com{path}",
                    )
            except Exception as exc:
                self.db.finish_task_log(
                    log_id,
                    "failed",
                    str(exc),
                    duration_ms=int((time.monotonic() - started) * 1000),
                )
            else:
                self.db.finish_task_log(
                    log_id,
                    "success",
                    "官方后台页面已关闭",
                    duration_ms=int((time.monotonic() - started) * 1000),
                )

        threading.Thread(
            target=open_backend,
            name=f"operations-open-backend-{store.id}-{target}",
            daemon=True,
        ).start()
        return {
            "opening": True,
            "store_id": store.id,
            "target": target,
            "product_id": product_id,
        }

    def _team_members(self) -> list[dict[str, Any]]:
        if self.team_service is None:
            raise RuntimeError("团队权限服务尚未配置")
        members = self.team_service.list_members()
        latest_changes: dict[str, dict[str, Any]] = {}
        for log in self.db.list_task_logs(limit=500, scope="permissions"):
            try:
                detail = json.loads(log.get("detail") or "{}")
            except (TypeError, ValueError):
                continue
            member_id = str(detail.get("target_member_id") or "")
            if member_id and member_id not in latest_changes:
                latest_changes[member_id] = {
                    "operations_changed_at": (
                        log.get("finished_at")
                        or log.get("started_at")
                        or ""
                    ),
                    "operations_changed_by": str(detail.get("actor") or ""),
                }
        output: list[dict[str, Any]] = []
        for item in members:
            role = str(item.get("role") or "viewer")
            permissions = item.get("permissions")
            if isinstance(permissions, str):
                try:
                    permissions = json.loads(permissions)
                except (TypeError, ValueError):
                    permissions = {}
            normalized_permissions = (
                dict(permissions) if isinstance(permissions, dict) else {}
            )
            grants = item.get("grants")
            if isinstance(grants, str):
                try:
                    grants = json.loads(grants)
                except (TypeError, ValueError):
                    grants = []
            for key in OPERATION_PERMISSION_KEYS:
                if role in OPERATION_ROLE_DEFAULTS:
                    normalized_permissions[key] = True
                else:
                    normalized_permissions[key] = bool(
                        normalized_permissions.get(key)
                    )
            output.append(
                {
                    **item,
                    **latest_changes.get(
                        str(
                            item.get("member_id")
                            or item.get("id")
                            or item.get("user_id")
                            or ""
                        ),
                        {},
                    ),
                    "member_id": str(
                        item.get("member_id")
                        or item.get("id")
                        or item.get("user_id")
                        or ""
                    ),
                    "username": str(
                        item.get("username")
                        or item.get("user_name")
                        or item.get("display_name")
                        or ""
                    ),
                    "role": role,
                    "permissions": normalized_permissions,
                    "grants": grants if isinstance(grants, list) else [],
                }
            )
        return output

    def _team_operations_members(self, _payload: dict[str, Any]) -> dict[str, Any]:
        self._require_team_admin()
        members = self._team_members()
        return {
            "members": members,
            "total": len(members),
            "audit": self.db.list_task_logs(
                limit=100,
                scope="permissions",
            ),
        }

    def _team_operations_create(self, payload: dict[str, Any]) -> dict[str, Any]:
        role, _permissions = self._require_team_admin()
        if self.team_service is None:
            raise RuntimeError("团队权限服务尚未配置")
        username = str(payload.get("username") or "").strip()
        password = str(payload.get("password") or "")
        target_role = str(payload.get("role") or "operator")
        if target_role not in {"manager", "operator", "viewer"}:
            raise ValueError("新子账号角色必须是 manager、operator 或 viewer")
        if len(username) < 2:
            raise ValueError("用户名至少需要 2 个字符")
        if len(password) < 8:
            raise ValueError("密码至少需要 8 位")
        requested = dict(payload.get("permissions") or {})
        view_enabled = bool(requested.get("operations_view"))
        permissions = {
            key: bool(requested.get(key)) and view_enabled
            for key in OPERATION_PERMISSION_KEYS
        }
        result = self.team_service.create_subaccount(
            username=username,
            password=password,
            role=target_role,
            permissions=permissions,
            grants=list(payload.get("grants") or []),
        )
        member_id = str(
            result.get("member_id")
            or result.get("id")
            or result.get("user_id")
            or ""
        )
        self._audit_permission_change(
            actor=self.account_label or role,
            target_member_id=member_id,
            target_username=username,
            old_permissions={},
            new_permissions=permissions,
            role=target_role,
        )
        return {"created": True, "member": result}

    def _team_add_existing(self, payload: dict[str, Any]) -> dict[str, Any]:
        role, _permissions = self._require_team_admin()
        if self.team_service is None:
            raise RuntimeError("团队权限服务尚未配置")
        username = str(payload.get("username") or "").strip()
        target_role = str(payload.get("role") or "admin")
        if len(username) < 2:
            raise ValueError("已有账号用户名至少需要 2 个字符")
        if target_role not in {"admin", "manager", "operator", "viewer"}:
            raise ValueError("账号角色无效")
        result = self.team_service.add_existing_account(
            username,
            target_role,
        )
        member_id = str(
            result.get("member_id")
            or result.get("id")
            or result.get("user_id")
            or username
        )
        self._audit_permission_change(
            actor=self.account_label or role,
            target_member_id=member_id,
            target_username=username,
            old_permissions={},
            new_permissions={},
            role=target_role,
        )
        return {"added": True, "member": result}

    def _audit_permission_change(
        self,
        *,
        actor: str,
        target_member_id: str,
        target_username: str,
        old_permissions: dict[str, Any],
        new_permissions: dict[str, Any],
        role: str,
    ) -> None:
        detail = {
            "actor": actor,
            "target_member_id": target_member_id,
            "target_username": target_username,
            "role": role,
            "old": old_permissions,
            "new": new_permissions,
            "ip": "127.0.0.1",
            "changed_at": datetime.now().isoformat(timespec="seconds"),
        }
        log_id = self.db.start_task_log(
            "permissions",
            "operations_permission_update",
            f"更新 {target_username} 的运营中心权限",
            detail=json.dumps(detail, ensure_ascii=False),
        )
        self.db.finish_task_log(
            log_id,
            "success",
            "权限已保存并立即生效",
            detail=json.dumps(detail, ensure_ascii=False),
        )

    def _update_operation_member_permissions(
        self,
        member: dict[str, Any],
        requested: dict[str, Any],
        *,
        actor: str,
    ) -> dict[str, Any]:
        role = str(member.get("role") or "viewer")
        if role == "owner":
            raise ValueError("主账号固定拥有全部运营中心权限")
        existing = member.get("permissions")
        existing_permissions = (
            dict(existing) if isinstance(existing, dict) else {}
        )
        next_permissions = dict(existing_permissions)
        view_enabled = bool(requested.get("operations_view"))
        for key in OPERATION_PERMISSION_KEYS:
            next_permissions[key] = bool(requested.get(key)) and view_enabled
        if role in OPERATION_ROLE_DEFAULTS:
            next_permissions.update({key: True for key in OPERATION_PERMISSION_KEYS})
        if self.team_service is None:
            raise RuntimeError("团队权限服务尚未配置")
        grants = member.get("grants")
        self.team_service.update_member(
            str(member["member_id"]),
            role=role,
            permissions=next_permissions,
            grants=list(grants) if isinstance(grants, list) else [],
            is_active=bool(member.get("is_active", True)),
        )
        self._audit_permission_change(
            actor=actor,
            target_member_id=str(member["member_id"]),
            target_username=str(
                member.get("username")
                or member.get("display_name")
                or member["member_id"]
            ),
            old_permissions={
                key: bool(existing_permissions.get(key))
                for key in OPERATION_PERMISSION_KEYS
            },
            new_permissions={
                key: bool(next_permissions.get(key))
                for key in OPERATION_PERMISSION_KEYS
            },
            role=role,
        )
        return {
            **member,
            "permissions": next_permissions,
        }

    def _team_operations_save(self, payload: dict[str, Any]) -> dict[str, Any]:
        role, _permissions = self._require_team_admin()
        member_id = str(payload.get("member_id") or "")
        if not member_id:
            raise ValueError("member_id 不能为空")
        members = self._team_members()
        member = next(
            (item for item in members if str(item["member_id"]) == member_id),
            None,
        )
        if member is None:
            raise ValueError("团队成员不存在")
        updated = self._update_operation_member_permissions(
            member,
            dict(payload.get("permissions") or {}),
            actor=self.account_label or role,
        )
        return {"member": updated}

    def _team_operations_batch(self, payload: dict[str, Any]) -> dict[str, Any]:
        role, _permissions = self._require_team_admin()
        member_ids = {
            str(value)
            for value in payload.get("member_ids") or []
            if str(value).strip()
        }
        if not member_ids:
            raise ValueError("请选择至少一名成员")
        requested = dict(payload.get("permissions") or {})
        members = self._team_members()
        updated: list[dict[str, Any]] = []
        for member in members:
            if str(member["member_id"]) not in member_ids:
                continue
            updated.append(
                self._update_operation_member_permissions(
                    member,
                    requested,
                    actor=self.account_label or role,
                )
            )
        if not updated:
            raise ValueError("没有找到可更新的成员")
        return {"members": updated, "updated": len(updated)}

    def _team_operations_audit(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._require_team_admin()
        limit = int(payload.get("limit") or 100)
        return {
            "audit": self.db.list_task_logs(
                limit=max(1, min(500, limit)),
                scope="permissions",
            )
        }

    def _list_stores(self, payload: dict[str, Any]) -> dict[str, Any]:
        search = str(payload.get("search") or "")
        groups = self.db.list_store_groups()
        metrics = self.db.store_metric_map()
        legacy_settings = self.db.get_settings_with_prefix(
            STORE_SCORE_LEGACY_PREFIX
        )
        score_state_settings = self.db.get_settings_with_prefix(
            STORE_SCORE_STATE_PREFIX
        )
        credential_states = self.db.store_credential_state_map()
        store_rows = self.db.list_stores(search=search)
        store_page_now = self._now_provider()
        new_product_map = self.db.product_new_today_map(
            {store.id for store in store_rows},
            now=store_page_now,
        )
        stores = []
        for store in store_rows:
            item = asdict(store)
            current_metrics = dict(metrics.get(store.id, {}))
            legacy_previous = self._read_json_dict(
                legacy_settings.get(
                    f"{STORE_SCORE_LEGACY_PREFIX}{store.id}",
                    "{}",
                )
            )
            score_state = self._load_store_score_state(
                store.id,
                current_metrics,
                legacy_previous,
                raw_state=score_state_settings.get(
                    f"{STORE_SCORE_STATE_PREFIX}{store.id}"
                ),
            )
            for key in STORE_SCORE_FIELDS:
                value = self._score_number(
                    (score_state.get("values", {}).get(key) or {}).get(
                        "value"
                    )
                )
                if value is not None:
                    current_metrics[key] = value
            changes = self._daily_store_score_changes(
                score_state.get("daily", {})
            )
            item["score_state"] = {**score_state, "changes": changes}
            item["score_changes"] = changes
            item["score_refresh"] = score_state.get("refresh", {})
            item["score_trends"] = {
                key: float(change.get("delta"))
                for key, change in changes.items()
                if key in STORE_SCORE_FIELDS
                and isinstance(change, dict)
                and self._score_number(change.get("delta")) not in (None, 0.0)
            }
            item["metrics"] = current_metrics
            sales_sources = current_metrics.get(
                "_sales_period_sources"
            )
            if not isinstance(sales_sources, dict):
                sales_sources = {}
            sales_as_of_date = (
                str(current_metrics.get("_sales_snapshot_date") or "")
                or self._metric_date(
                    current_metrics.get("_sales_updated_at")
                )
            )
            sales_period_updated_at = current_metrics.get(
                "_sales_period_updated_at"
            )
            if not isinstance(sales_period_updated_at, dict):
                sales_period_updated_at = {}
            refund_period_updated_at = current_metrics.get(
                "_refund_period_updated_at"
            )
            if not isinstance(refund_period_updated_at, dict):
                refund_period_updated_at = {}
            refund_updated_at = str(
                current_metrics.get("_refund_updated_at") or ""
            )
            item["sales_meta"] = {
                "status": current_metrics.get(
                    "_sales_attempt_status"
                )
                or {},
                "sources": sales_sources,
                "source": str(
                    current_metrics.get("_sales_source")
                    or "本地订单缓存降级"
                ),
                "updated_at": str(
                    current_metrics.get("_sales_updated_at") or ""
                ),
                "stale": bool(
                    current_metrics.get("_sales_is_stale")
                ),
                "as_of_date": str(
                    current_metrics.get("_sales_snapshot_date") or ""
                ) or sales_as_of_date,
                "period_dates": {
                    period: (
                        self._metric_date(period_updated_at)
                        or sales_as_of_date
                    )
                    for period, period_updated_at in (
                        sales_period_updated_at.items()
                    )
                },
                "gift_exclusion": (
                    current_metrics.get("_gift_exclusion")
                    if isinstance(
                        current_metrics.get("_gift_exclusion"),
                        dict,
                    )
                    else {}
                ),
                "refund_amounts": {
                    "status": current_metrics.get(
                        "_refund_attempt_status"
                    )
                    or {},
                    "sources": current_metrics.get(
                        "_refund_period_sources"
                    )
                    or {},
                    "updated_at": str(
                        current_metrics.get("_refund_updated_at")
                        or ""
                    ),
                    "error": str(
                        current_metrics.get("_refund_error") or ""
                    ),
                    "as_of_date": self._metric_date(refund_updated_at),
                    "period_dates": {
                        period: self._metric_date(period_updated_at)
                        for period, period_updated_at in (
                            refund_period_updated_at.items()
                        )
                    },
                },
                "refund_order_counts": (
                    current_metrics.get("_refund_order_counts")
                    if isinstance(
                        current_metrics.get("_refund_order_counts"),
                        dict,
                    )
                    else {}
                ),
                "refund_order_cache": (
                    current_metrics.get("_refund_order_cache")
                    if isinstance(
                        current_metrics.get("_refund_order_cache"),
                        dict,
                    )
                    else {}
                ),
                "error": str(
                    current_metrics.get("_sales_error") or ""
                ),
            }
            exposure_today = self._score_number(
                current_metrics.get("product_exposure_today")
            )
            exposure_yesterday = self._score_number(
                current_metrics.get("product_exposure_yesterday")
            )
            exposure_seven_days = self._score_number(
                current_metrics.get("product_exposure_7d")
            )
            item["exposure_metrics"] = {
                "today": int(exposure_today) if exposure_today is not None else None,
                "yesterday": (
                    int(exposure_yesterday)
                    if exposure_yesterday is not None
                    else None
                ),
                "seven_days": (
                    int(exposure_seven_days)
                    if exposure_seven_days is not None
                    else None
                ),
            }
            exposure_count_today = self._score_number(
                current_metrics.get("product_exposure_count_today")
            )
            exposure_count_yesterday = self._score_number(
                current_metrics.get("product_exposure_count_yesterday")
            )
            exposure_count_seven_days = self._score_number(
                current_metrics.get("product_exposure_count_7d")
            )
            item["exposure_counts"] = {
                "today": (
                    int(exposure_count_today)
                    if exposure_count_today is not None
                    else None
                ),
                "yesterday": (
                    int(exposure_count_yesterday)
                    if exposure_count_yesterday is not None
                    else None
                ),
                "seven_days": (
                    int(exposure_count_seven_days)
                    if exposure_count_seven_days is not None
                    else None
                ),
            }
            exposure_people_today = self._score_number(
                current_metrics.get("product_exposure_today")
            )
            exposure_people_yesterday = self._score_number(
                current_metrics.get("product_exposure_yesterday")
            )
            exposure_people_seven_days = self._score_number(
                current_metrics.get("product_exposure_7d")
            )
            item["exposure_people"] = {
                "today": (
                    int(exposure_people_today)
                    if exposure_people_today is not None
                    else None
                ),
                "yesterday": (
                    int(exposure_people_yesterday)
                    if exposure_people_yesterday is not None
                    else None
                ),
                "seven_days": (
                    int(exposure_people_seven_days)
                    if exposure_people_seven_days is not None
                    else None
                ),
            }
            item["product_exposure_people"] = dict(
                item["exposure_people"]
            )
            item["product_exposure_counts"] = dict(
                item["exposure_counts"]
            )
            item["exposure_click_people"] = {
                "today": current_metrics.get("product_click_people_today"),
                "yesterday": current_metrics.get(
                    "product_click_people_yesterday"
                ),
                "seven_days": current_metrics.get("product_click_people_7d"),
            }
            item["exposure_click_counts"] = {
                "today": current_metrics.get("product_click_count_today"),
                "yesterday": current_metrics.get(
                    "product_click_count_yesterday"
                ),
                "seven_days": current_metrics.get("product_click_count_7d"),
            }
            exposure_period_updated_at = current_metrics.get(
                "_exposure_period_updated_at"
            )
            if not isinstance(exposure_period_updated_at, dict):
                exposure_period_updated_at = {}
            exposure_updated_at = str(
                current_metrics.get("_exposure_updated_at") or ""
            )
            exposure_as_of_date = self._metric_date(exposure_updated_at)
            item["exposure_meta"] = {
                "status": current_metrics.get("_exposure_attempt_status") or {},
                "count_status": (
                    current_metrics.get("_exposure_count_attempt_status") or {}
                ),
                "people_status": (
                    current_metrics.get("_exposure_people_attempt_status") or {}
                ),
                "updated_at": exposure_updated_at,
                "error": str(current_metrics.get("_exposure_error") or ""),
                "source": str(current_metrics.get("_exposure_source") or ""),
                "as_of_date": exposure_as_of_date,
                "period_dates": {
                    period: (
                        self._metric_date(period_updated_at)
                        or exposure_as_of_date
                    )
                    for period, period_updated_at in (
                        exposure_period_updated_at.items()
                    )
                },
            }
            online_source = str(
                current_metrics.get("_online_product_source") or ""
            )
            online_value = self._score_number(current_metrics.get("in_sale"))
            item["online_products"] = (
                int(online_value)
                if online_source and online_value is not None
                else None
            )
            online_updated_at = str(
                current_metrics.get("_online_product_updated_at") or ""
            )
            online_as_of_date = self._metric_date(online_updated_at)
            item["online_products_meta"] = {
                "status": str(
                    current_metrics.get("_online_product_attempt_status")
                    or "unavailable"
                ),
                "updated_at": online_updated_at,
                "error": str(
                    current_metrics.get("_online_product_error") or ""
                ),
                "source": online_source,
                "as_of_date": online_as_of_date,
            }
            new_product = new_product_map.get(store.id, {})
            item["new_products_today"] = new_product.get("count")
            new_products_as_of_date = self._metric_date(
                new_product.get("last_scan_completed_at")
            )
            item["new_products_today_meta"] = {
                "scan_completed_today": bool(
                    new_product.get("scan_completed_today")
                ),
                "last_scan_completed_at": str(
                    new_product.get("last_scan_completed_at") or ""
                ),
                "timezone": "Asia/Shanghai",
                "definition": "今日首次观测上新",
                "as_of_date": new_products_as_of_date,
            }
            fund_period_updated_at = current_metrics.get(
                "_fund_period_updated_at"
            )
            if not isinstance(fund_period_updated_at, dict):
                fund_period_updated_at = {}
            fund_updated_at = str(
                current_metrics.get("_fund_updated_at") or ""
            )
            fund_as_of_date = self._metric_date(fund_updated_at)
            fund_status = current_metrics.get("_fund_attempt_status")
            item["fund_meta"] = {
                "status": (
                    fund_status
                    if isinstance(fund_status, dict)
                    else {}
                ),
                "updated_at": fund_updated_at,
                "error": str(current_metrics.get("_fund_error") or ""),
                "source": str(current_metrics.get("_fund_source") or ""),
                "as_of_date": fund_as_of_date,
                "period_dates": {
                    period: (
                        self._metric_date(period_updated_at)
                        or fund_as_of_date
                    )
                    for period, period_updated_at in (
                        fund_period_updated_at.items()
                    )
                },
            }
            item["deposit_meta"] = {
                "status": str(
                    current_metrics.get("_deposit_attempt_status")
                    or (
                        "cached"
                        if current_metrics.get("_deposit_source")
                        else "unavailable"
                    )
                ),
                "updated_at": str(
                    current_metrics.get("_deposit_updated_at") or ""
                ),
                "error": str(current_metrics.get("_deposit_error") or ""),
                "source": str(current_metrics.get("_deposit_source") or ""),
            }
            item["credential_state"] = str(
                (credential_states.get(store.id) or {}).get("state")
                or "unknown"
            )
            item["as_of_date"] = (
                sales_as_of_date
                or exposure_as_of_date
                or online_as_of_date
                or new_products_as_of_date
                or fund_as_of_date
            )
            stores.append(item)
        return {"stores": stores, "groups": groups}

    @staticmethod
    def _read_json_dict(raw: Any) -> dict[str, Any]:
        try:
            value = json.loads(str(raw or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _score_number(value: Any) -> float | None:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return round(number, 2)

    @staticmethod
    def _metric_date(value: Any) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        try:
            parsed = datetime.fromisoformat(
                text[:-1] + "+00:00" if text.endswith("Z") else text
            )
        except ValueError:
            parsed = None
        if parsed is not None:
            if parsed.tzinfo is not None:
                parsed = parsed.astimezone(SHANGHAI_TZ)
            return parsed.date().isoformat()
        return (
            text[:10]
            if len(text) >= 10 and text[4] == "-" and text[7] == "-"
            else ""
        )

    def _local_now(self) -> datetime:
        now = self._now_provider()
        if now.tzinfo is not None:
            now = now.astimezone().replace(tzinfo=None)
        return now

    def _daily_store_score_changes(
        self,
        daily: Any,
    ) -> dict[str, dict[str, Any]]:
        if not isinstance(daily, dict):
            return {}
        changes: dict[str, dict[str, Any]] = {}
        for key in STORE_SCORE_FIELDS:
            rows: list[tuple[str, dict[str, Any]]] = []
            for date_key, values in daily.items():
                if not isinstance(values, dict):
                    continue
                row = values.get(key)
                if not isinstance(row, dict):
                    continue
                try:
                    normalized_date = datetime.fromisoformat(
                        str(date_key)
                    ).date().isoformat()
                except ValueError:
                    continue
                if self._score_number(row.get("value")) is None:
                    continue
                rows.append((normalized_date, row))
            official_rows = [
                (date_key, row)
                for date_key, row in rows
                if bool(row.get("official"))
            ]
            candidates = official_rows or rows
            if len(candidates) < 2:
                continue
            candidates.sort(key=lambda item: item[0], reverse=True)
            comparison_date, current = candidates[0]
            baseline_date, previous = candidates[1]
            current_value = self._score_number(current.get("value"))
            previous_value = self._score_number(previous.get("value"))
            if current_value is None or previous_value is None:
                continue
            delta = round(current_value - previous_value, 2)
            if not delta:
                continue
            changes[key] = {
                "from": previous_value,
                "to": current_value,
                "delta": delta,
                "updated_at": str(current.get("updated_at") or ""),
                "baseline_date": baseline_date,
                "comparison_date": comparison_date,
            }
        return changes

    def _prune_store_score_daily(
        self,
        daily: dict[str, Any],
    ) -> None:
        keep_from = (
            self._local_now().date()
            - timedelta(days=STORE_SCORE_DAILY_KEEP_DAYS)
        ).isoformat()
        for date_key in list(daily):
            if str(date_key) < keep_from:
                daily.pop(date_key, None)

    def _score_source_label(self, key: str) -> str:
        return f"微信小店统计接口 {STORE_SCORE_LABELS[key]}"

    def _migrate_store_score_state(
        self,
        current_metrics: dict[str, Any],
        legacy_previous: dict[str, Any],
    ) -> dict[str, Any]:
        now = self._local_now()
        now_text = now.strftime("%Y-%m-%d %H:%M:%S")
        updated_at = str(
            current_metrics.get("_score_updated_at")
            or current_metrics.get("stats_at")
            or current_metrics.get("updated_at")
            or now_text
        )
        state: dict[str, Any] = {
            "version": 3,
            "values": {},
            "changes": {},
            "daily": {},
            "refresh": {},
            "last_attempt_at": "",
        }
        try:
            current_date = datetime.fromisoformat(
                updated_at.replace("Z", "+00:00")
            ).date().isoformat()
        except ValueError:
            current_date = now.date().isoformat()
        for key in STORE_SCORE_FIELDS:
            current = self._score_number(current_metrics.get(key))
            previous = self._score_number(legacy_previous.get(key))
            value = current if current is not None else previous
            if value is None:
                continue
            state["values"][key] = {
                "value": value,
                "updated_at": (
                    updated_at if current is not None else ""
                ),
                "source": (
                    str(current_metrics.get("_score_source") or "")
                    or self._score_source_label(key)
                ),
                "migrated_from_legacy": current is None,
            }
            if current is not None and previous is not None:
                delta = round(current - previous, 2)
                if delta:
                    state["changes"][key] = {
                        "from": previous,
                        "to": current,
                        "delta": delta,
                        "updated_at": updated_at,
                    }
            if current is not None:
                state["daily"].setdefault(current_date, {})[key] = {
                    "value": current,
                    "updated_at": updated_at,
                    "source": (
                        str(current_metrics.get("_score_source") or "")
                        or self._score_source_label(key)
                    ),
                }
            state["refresh"][key] = {
                "status": "legacy",
                "attempted_at": "",
                "error": "",
            }
        self._prune_store_score_daily(state["daily"])
        state["changes"] = self._daily_store_score_changes(
            state["daily"]
        )
        return state

    def _load_store_score_state(
        self,
        store_id: int,
        current_metrics: dict[str, Any],
        legacy_previous: dict[str, Any],
        *,
        raw_state: str | None = None,
    ) -> dict[str, Any]:
        raw_state = (
            raw_state
            if raw_state is not None
            else self.db.get_setting(
                f"{STORE_SCORE_STATE_PREFIX}{store_id}",
                "",
            )
        )
        parsed = self._read_json_dict(raw_state)
        if parsed:
            owner_store_id = parsed.get("_owner_store_id")
            store = self.db.get_store(store_id)
            if owner_store_id is not None:
                try:
                    owner_matches = int(owner_store_id) == int(store_id)
                except (TypeError, ValueError):
                    owner_matches = False
                if not owner_matches:
                    parsed = {}
            owner_app_id = parsed.get("_owner_store_app_id")
            if (
                parsed
                and owner_app_id is not None
                and store is not None
                and str(owner_app_id) != str(store.app_id)
            ):
                parsed = {}
        migrated = self._migrate_store_score_state(
            current_metrics,
            legacy_previous,
        )
        if not parsed:
            self._save_store_score_state(store_id, migrated)
            return migrated
        daily = (
            parsed.get("daily")
            if isinstance(parsed.get("daily"), dict)
            else migrated["daily"]
        )
        state = {
            "version": 3,
            "values": (
                parsed.get("values")
                if isinstance(parsed.get("values"), dict)
                else {}
            ),
            "changes": {},
            "daily": daily,
            "refresh": (
                parsed.get("refresh")
                if isinstance(parsed.get("refresh"), dict)
                else {}
            ),
            "last_attempt_at": str(parsed.get("last_attempt_at") or ""),
        }
        daily_merged = False
        for date_key, values in migrated["daily"].items():
            if not isinstance(values, dict):
                continue
            target = state["daily"].setdefault(date_key, {})
            if not isinstance(target, dict):
                target = {}
                state["daily"][date_key] = target
            for key, row in values.items():
                if key not in target:
                    target[key] = row
                    daily_merged = True
        state_changes = self._daily_store_score_changes(daily)
        state["changes"] = state_changes
        changed = (
            parsed.get("version") != 3
            or not isinstance(parsed.get("daily"), dict)
            or parsed.get("changes") != state_changes
            or daily_merged
        )
        for key in STORE_SCORE_FIELDS:
            row = state["values"].get(key)
            value = self._score_number(
                row.get("value") if isinstance(row, dict) else None
            )
            if value is None:
                migrated_row = migrated["values"].get(key)
                if migrated_row is not None:
                    state["values"][key] = migrated_row
                    changed = True
                continue
            state["values"][key] = {
                "value": value,
                "updated_at": str(row.get("updated_at") or ""),
                "source": str(
                    row.get("source") or self._score_source_label(key)
                ),
                "migrated_from_legacy": bool(
                    row.get("migrated_from_legacy")
                ),
            }
            refresh = state["refresh"].get(key)
            if not isinstance(refresh, dict):
                state["refresh"][key] = {
                    "status": "unknown",
                    "attempted_at": "",
                    "error": "",
                }
                changed = True
        if changed:
            self._save_store_score_state(store_id, state)
        return state

    def _save_store_score_state(
        self,
        store_id: int,
        state: dict[str, Any],
    ) -> None:
        store = self.db.get_store(store_id)
        state["_owner_store_id"] = int(store_id)
        state["_owner_store_app_id"] = (
            str(store.app_id) if store is not None else ""
        )
        self.db.set_setting(
            f"{STORE_SCORE_STATE_PREFIX}{store_id}",
            json.dumps(state, ensure_ascii=False),
        )

    def _record_store_score_baseline(
        self,
        store_ids: set[int] | None = None,
    ) -> None:
        with self._store_score_state_lock:
            self._record_store_score_baseline_locked(store_ids)

    def _record_store_score_baseline_locked(
        self,
        store_ids: set[int] | None = None,
    ) -> None:
        metrics_map = self.db.store_metric_map()
        legacy_settings = self.db.get_settings_with_prefix(
            STORE_SCORE_LEGACY_PREFIX
        )
        now_text = self._local_now().strftime("%Y-%m-%d %H:%M:%S")
        for store_id, current_metrics in metrics_map.items():
            if store_ids and store_id not in store_ids:
                continue
            legacy_previous = self._read_json_dict(
                legacy_settings.get(
                    f"{STORE_SCORE_LEGACY_PREFIX}{store_id}",
                    "{}",
                )
            )
            state = self._load_store_score_state(
                store_id,
                current_metrics,
                legacy_previous,
            )
            state["last_attempt_at"] = now_text
            for key in STORE_SCORE_FIELDS:
                refresh = dict(state["refresh"].get(key) or {})
                refresh.update(
                    {
                        "status": "pending",
                        "attempted_at": now_text,
                        "error": "",
                    }
                )
                state["refresh"][key] = refresh
            self._save_store_score_state(store_id, state)

    def _record_store_score_refresh_result(
        self,
        store_id: int,
        current_metrics: dict[str, Any],
    ) -> None:
        with self._store_score_state_lock:
            self._record_store_score_refresh_result_locked(
                store_id,
                current_metrics,
            )

    def _record_store_score_refresh_result_locked(
        self,
        store_id: int,
        current_metrics: dict[str, Any],
    ) -> None:
        now_text = self._local_now().strftime("%Y-%m-%d %H:%M:%S")
        legacy_previous = self._read_json_dict(
            self.db.get_setting(
                f"{STORE_SCORE_LEGACY_PREFIX}{store_id}",
                "{}",
            )
        )
        state = self._load_store_score_state(
            store_id,
            current_metrics,
            legacy_previous,
        )
        attempt_status = current_metrics.get("_score_attempt_status")
        if not isinstance(attempt_status, dict):
            attempt_status = {}
        updated_at = str(
            current_metrics.get("_score_updated_at") or now_text
        )
        source = str(
            current_metrics.get("_score_source")
            or "微信小店统计接口"
        )
        daily_date = self._local_now().date().isoformat()
        daily = state.setdefault("daily", {})
        today_daily = daily.setdefault(daily_date, {})
        official_daily = current_metrics.get("_score_daily_values")
        if not isinstance(official_daily, dict):
            official_daily = {}
        for key in STORE_SCORE_FIELDS:
            new_value = self._score_number(current_metrics.get(key))
            status = str(attempt_status.get(key) or "")
            if not status:
                status = "updated" if new_value is not None else "missing"
            if status == "updated" and new_value is not None:
                state["values"][key] = {
                    "value": new_value,
                    "updated_at": updated_at,
                    "source": source,
                    "migrated_from_legacy": False,
                }
                official_rows = official_daily.get(key)
                wrote_official_row = False
                if isinstance(official_rows, list):
                    for row in official_rows:
                        if not isinstance(row, dict):
                            continue
                        try:
                            row_date = datetime.fromisoformat(
                                str(row.get("date") or "")
                            ).date().isoformat()
                        except ValueError:
                            continue
                        row_value = self._score_number(row.get("value"))
                        if row_value is None:
                            continue
                        daily.setdefault(row_date, {})[key] = {
                            "value": row_value,
                            "updated_at": updated_at,
                            "source": source,
                            "official": True,
                        }
                        wrote_official_row = True
                if not wrote_official_row:
                    today_daily[key] = {
                        "value": new_value,
                        "updated_at": updated_at,
                        "source": source,
                    }
            state["refresh"][key] = {
                "status": status,
                "attempted_at": now_text,
                "error": "",
            }
        self._prune_store_score_daily(daily)
        state["changes"] = self._daily_store_score_changes(daily)
        state["last_attempt_at"] = now_text
        self._save_store_score_state(store_id, state)

    def _record_store_score_refresh_failure(
        self,
        store_id: int,
        error: str,
    ) -> None:
        with self._store_score_state_lock:
            self._record_store_score_refresh_failure_locked(
                store_id,
                error,
            )

    def _record_store_score_refresh_failure_locked(
        self,
        store_id: int,
        error: str,
    ) -> None:
        current_metrics = dict(
            self.db.store_metric_map().get(store_id, {})
        )
        legacy_previous = self._read_json_dict(
            self.db.get_setting(
                f"{STORE_SCORE_LEGACY_PREFIX}{store_id}",
                "{}",
            )
        )
        state = self._load_store_score_state(
            store_id,
            current_metrics,
            legacy_previous,
        )
        now_text = self._local_now().strftime("%Y-%m-%d %H:%M:%S")
        for key in STORE_SCORE_FIELDS:
            state["refresh"][key] = {
                "status": "failed",
                "attempted_at": now_text,
                "error": str(error)[:500],
            }
        state["last_attempt_at"] = now_text
        self._save_store_score_state(store_id, state)

    def _create_store(self, payload: dict[str, Any]) -> dict[str, Any]:
        group_name = str(payload.get("group_name") or "").strip()
        if group_name:
            self._require_entitlement("module.store_groups")
        store_id = self.db.add_store(
            str(payload.get("name") or ""),
            str(payload.get("app_id") or ""),
            str(payload.get("secret") or ""),
            group_name,
        )
        store = self.db.get_store(store_id)
        if store is None:
            raise RuntimeError("店铺创建后无法读取")
        self._write_operation_log(
            "stores",
            "新增店铺",
            f"店铺已新增：{store.name}",
            store_id=store.id,
            detail={
                "store_id": store.id,
                "app_id": store.app_id,
                "group_name": store.group_name,
            },
        )
        return {
            "store": asdict(store),
            "profile_hydration": self._start_store_profile_hydration([store_id]),
            "background_followup": (
                self._schedule_new_store_followup(
                    [store_id],
                    reason="新增店铺",
                )
                if payload.get("background_refresh_enabled", True)
                else {"scheduled": False, "store_count": 0}
            ),
        }

    def _update_store(self, payload: dict[str, Any]) -> dict[str, Any]:
        store_id = self._store_id(payload)
        current = self._require_store(store_id)
        group_name = str(
            payload.get("group_name", current.group_name)
            or ""
        ).strip()
        if group_name != str(current.group_name or ""):
            self._require_entitlement("module.store_groups")
        secret = payload.get("secret")
        self.db.update_store(
            store_id,
            str(payload.get("name") or ""),
            group_name,
            str(secret) if secret not in (None, "") else None,
            str(payload.get("app_id") or "") or None,
        )
        store = self.db.get_store(store_id)
        if store is None:
            raise RuntimeError("店铺更新后无法读取")
        self._write_operation_log(
            "stores",
            "编辑店铺",
            f"店铺资料已更新：{store.name}",
            store_id=store.id,
            detail={
                "store_id": store.id,
                "app_id": store.app_id,
                "group_name": store.group_name,
                "secret_changed": secret not in (None, ""),
            },
        )
        return {"store": asdict(store)}

    def _delete_store(self, payload: dict[str, Any]) -> dict[str, Any]:
        store_id = self._store_id(payload)
        store = self._require_store(store_id)
        self.db.delete_store(store_id)
        cache_errors = self._remove_store_login_profiles([store])
        self._write_operation_log(
            "stores",
            "删除店铺",
            f"店铺已删除：{store.name}",
            detail={
                "store_id": store.id,
                "store_name": store.name,
                "app_id": store.app_id,
            },
        )
        return {
            "deleted": store_id,
            "cache_cleanup_errors": cache_errors,
        }

    def _reveal_store_secret(self, payload: dict[str, Any]) -> dict[str, Any]:
        store_id = self._store_id(payload)
        _store, secret = self.db.get_store_secret(store_id)
        return {"store_id": store_id, "secret": secret}

    def _create_store_group(self, payload: dict[str, Any]) -> dict[str, Any]:
        group_name = str(payload.get("group_name") or "").strip()
        if not group_name:
            raise ValueError("分组名称不能为空")
        self.db.create_store_group(group_name)
        self._write_operation_log(
            "stores",
            "新增店铺分组",
            f"店铺分组已新增：{group_name}",
            detail={"group_name": group_name},
        )
        return {"group_name": group_name, "groups": self.db.list_store_groups()}

    def _delete_store_group(self, payload: dict[str, Any]) -> dict[str, Any]:
        group_name = str(payload.get("group_name") or "").strip()
        if not group_name:
            raise ValueError("分组名称不能为空")
        self.db.delete_store_group(group_name)
        self._write_operation_log(
            "stores",
            "删除店铺分组",
            f"店铺分组已删除：{group_name}",
            detail={"group_name": group_name},
        )
        return {"group_name": group_name, "groups": self.db.list_store_groups()}

    def _assign_store_group(self, payload: dict[str, Any]) -> dict[str, Any]:
        store_id = self._store_id(payload)
        group_name = str(payload.get("group_name") or "").strip()
        self.db.set_store_groups([store_id], group_name)
        self._write_operation_log(
            "stores",
            "调整店铺分组",
            f"店铺分组已调整为：{group_name or '未分组'}",
            store_id=store_id,
            detail={"store_id": store_id, "group_name": group_name},
        )
        return {
            "store_id": store_id,
            "group_name": group_name,
            "groups": self.db.list_store_groups(),
        }

    def _batch_store_ids(
        self,
        payload: dict[str, Any],
    ) -> tuple[list[int], list[Any]]:
        raw_values = payload.get("store_ids")
        if not isinstance(raw_values, list) or not raw_values:
            raise ValueError("请至少选择一家店铺")
        ids: list[int] = []
        for raw in raw_values:
            try:
                store_id = int(raw)
            except (TypeError, ValueError) as exc:
                raise ValueError("店铺 ID 必须是整数") from exc
            if store_id <= 0:
                raise ValueError("店铺 ID 必须是正整数")
            ids.append(store_id)
        if len(ids) != len(set(ids)):
            raise ValueError("店铺 ID 不能重复")
        if len(ids) > 500:
            raise ValueError("单次批量操作不能超过 500 家店铺")
        stores = []
        missing = []
        for store_id in ids:
            store = self.db.get_store(store_id)
            if store is None:
                missing.append(store_id)
            else:
                stores.append(store)
        if missing:
            raise ValueError(
                "店铺不存在：" + ", ".join(map(str, missing))
            )
        return ids, stores

    def _batch_assign_store_group(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        ids, stores = self._batch_store_ids(payload)
        group_name = str(payload.get("group_name") or "").strip()
        self.db.set_store_groups(ids, group_name)
        self._write_operation_log(
            "stores",
            "批量调整店铺分组",
            f"{len(ids)} 家店铺分组已调整为：{group_name or '未分组'}",
            detail={
                "store_ids": ids,
                "store_names": [store.name for store in stores],
                "group_name": group_name,
            },
        )
        return {
            "updated": len(ids),
            "store_ids": ids,
            "group_name": group_name,
            "groups": self.db.list_store_groups(),
        }

    def _batch_delete_stores(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        ids, stores = self._batch_store_ids(payload)
        deleted_rows = self.db.delete_stores(ids)
        cache_errors = self._remove_store_login_profiles(stores)
        for store in stores:
            self._write_operation_log(
                "stores",
                "批量删除店铺",
                f"本地店铺已删除：{store.name}",
                store_id=None,
                detail={
                    "store_id": store.id,
                    "store_name": store.name,
                    "app_id": store.app_id,
                    "batch_store_ids": ids,
                    "official_store_deleted": False,
                },
            )
        return {
            "deleted": len(deleted_rows),
            "deleted_store_ids": [int(row["id"]) for row in deleted_rows],
            "deleted_stores": [
                {
                    "id": int(row["id"]),
                    "name": str(row["name"]),
                    "app_id": str(row["app_id"]),
                }
                for row in deleted_rows
            ],
            "cache_cleanup_errors": cache_errors,
            "official_store_deleted": False,
        }

    def _remove_store_login_profiles(
        self,
        stores: list[Any],
    ) -> list[str]:
        errors: list[str] = []
        web_session = self.web_session
        if web_session is None:
            return errors
        profile_root = (
            Path(web_session.data_dir) / "edge_profiles"
        ).resolve()
        for store in stores:
            profile = (profile_root / f"store_{store.id}").resolve()
            if profile.parent != profile_root:
                errors.append(f"{store.name}: 登录缓存路径非法")
                continue
            if not profile.exists():
                continue
            try:
                shutil.rmtree(profile)
            except OSError as exc:
                errors.append(f"{store.name}: {exc}")
        return errors

    def _parse_store_batch_payload(
        self,
        payload: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], str, str]:
        source_name = str(payload.get("source_name") or "").strip()
        rows, source_hash = parse_store_import_source(
            source_kind=str(payload.get("source_kind") or ""),
            source_name=source_name,
            text=str(payload.get("text") or ""),
            content_base64=str(payload.get("content_base64") or ""),
        )
        overrides = payload.get("name_overrides")
        if isinstance(overrides, dict):
            for row in rows:
                override = overrides.get(str(row.get("row_number")))
                if isinstance(override, str) and override.strip():
                    row["name"] = override.strip()
        return rows, source_hash, source_name

    def _store_batch_preview_rows(
        self,
        rows: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], dict[int, dict[str, Any]]]:
        existing_stores = list(self.db.list_stores())
        existing_by_app_id = {
            store.app_id: store
            for store in existing_stores
        }
        existing_names = {
            store.name.strip().casefold(): store
            for store in existing_stores
            if store.name.strip()
        }
        credential_states = self.db.store_credential_state_map()
        batch_names: dict[str, set[str]] = {}
        first_app_ids: set[str] = set()
        for row in rows:
            if row.get("parse_errors"):
                continue
            app_id = str(row.get("app_id") or "")
            if app_id in first_app_ids:
                continue
            first_app_ids.add(app_id)
            name_key = str(row.get("name") or "").strip().casefold()
            if name_key:
                batch_names.setdefault(name_key, set()).add(app_id)
        result: list[dict[str, Any]] = []
        internal = {int(row["row_number"]): row for row in rows}
        seen_app_ids: set[str] = set()
        for row in rows:
            app_id = str(row.get("app_id") or "")
            name = str(row.get("name") or "")
            name_key = name.strip().casefold()
            parse_errors = list(row.get("parse_errors") or [])
            existing = existing_by_app_id.get(app_id)
            reason = ""
            if parse_errors:
                status = "invalid"
                reason = "；".join(parse_errors)
            elif app_id in seen_app_ids:
                status = "duplicate"
                reason = "本批次重复 AppID，保留第一条"
            elif existing is not None:
                state = str(
                    (credential_states.get(existing.id) or {}).get("state")
                    or "unknown"
                )
                if state == "invalid":
                    status = "replace_candidate"
                    reason = "AppID 已存在，旧凭据已明确失效；仅保存新密钥候选"
                else:
                    status = "skip"
                    reason = (
                        "AppID 已存在，旧凭据状态为 "
                        + ("可用" if state == "valid" else "尚未判定失效")
                        + "；保留原密钥"
                    )
            elif (
                name_key in existing_names
                or len(batch_names.get(name_key, set())) > 1
            ):
                status = "name_conflict"
                reason = "同名店铺使用不同 AppID，请修改显示名称后重新预览"
            else:
                status = "new"
                reason = "本地不存在此 AppID"
            if not parse_errors:
                seen_app_ids.add(app_id)
            result.append(
                {
                    **{
                        key: value
                        for key, value in row.items()
                        if key != "secret"
                    },
                    "status": status,
                    "reason": reason,
                    "selectable": status in {"new", "replace_candidate"},
                    "existing_store_id": existing.id if existing else None,
                    "existing_store_name": existing.name if existing else "",
                }
            )
        return result, internal

    def _preview_store_batch(self, payload: dict[str, Any]) -> dict[str, Any]:
        rows, source_hash, source_name = self._parse_store_batch_payload(payload)
        preview_rows, _internal = self._store_batch_preview_rows(rows)
        summary = {
            status: sum(row["status"] == status for row in preview_rows)
            for status in (
                "new",
                "skip",
                "replace_candidate",
                "invalid",
                "duplicate",
                "name_conflict",
            )
        }
        log_id = self.db.start_task_log(
            "stores",
            "batch_import_preview",
            "店铺批量导入本地预检完成",
            detail=json.dumps(
                {
                    "source_name": Path(source_name).name,
                    "row_count": len(preview_rows),
                    **summary,
                },
                ensure_ascii=False,
            ),
        )
        self.db.finish_task_log(
            log_id,
            "success" if not summary["invalid"] else "partial",
            "本地预检完成，未访问平台",
        )
        return {
            "source_name": Path(source_name).name,
            "source_hash": source_hash,
            "rows": preview_rows,
            "summary": summary,
            "network_requests": 0,
        }

    def _commit_store_batch(self, payload: dict[str, Any]) -> dict[str, Any]:
        expected_hash = str(payload.get("source_hash") or "").strip()
        rows, source_hash, source_name = self._parse_store_batch_payload(payload)
        if expected_hash and expected_hash != source_hash:
            raise ValueError("导入来源已变化，请重新预检后再保存")
        preview_rows, internal = self._store_batch_preview_rows(rows)
        requested_rows = {
            int(value)
            for value in payload.get("row_numbers", [])
            if str(value).isdigit()
        }
        selected = [
            row
            for row in preview_rows
            if row["selectable"]
            and (
                not requested_rows
                or int(row["row_number"]) in requested_rows
            )
        ]
        if not selected:
            raise ValueError("没有可保存的新增店铺或替换候选")
        commit_rows = []
        for row in selected:
            raw = dict(internal[int(row["row_number"])])
            raw["expected_status"] = row["status"]
            commit_rows.append(raw)
        detail = self.db.commit_store_import_rows(
            commit_rows,
            source_name=source_name,
            source_hash=source_hash,
        )
        created_ids = [
            int(store_id)
            for store_id in detail.get("created_store_ids", [])
            if str(store_id).isdigit()
        ]
        if created_ids:
            detail["profile_hydration"] = self._start_store_profile_hydration(
                created_ids
            )
            detail["background_followup"] = (
                self._schedule_new_store_followup(
                    created_ids,
                    reason="批量导入店铺",
                )
                if payload.get("background_refresh_enabled", True)
                else {"scheduled": False, "store_count": 0}
            )
        return detail

    def _start_store_profile_hydration(
        self,
        store_ids: list[int],
    ) -> dict[str, Any]:
        service = self.product_service
        if service is None:
            return {"started": False, "reason": "产品服务尚未初始化"}
        if not callable(getattr(service, "sync_store_profile", None)):
            return {"started": False, "reason": "产品服务不支持店铺资料同步"}
        ids = sorted({int(store_id) for store_id in store_ids if int(store_id) > 0})
        if not ids:
            return {"started": False, "reason": "没有新增店铺"}

        def worker() -> None:
            for store_id in ids:
                store = self.db.get_store(store_id)
                if store is None:
                    continue
                try:
                    result = service.sync_store_profile(store)
                except Exception as exc:
                    state, code, message = self._credential_failure(exc)
                    if state == "invalid":
                        self.db.set_store_credential_state(
                            store_id,
                            "invalid",
                            error_code=code,
                            error_message=message,
                            mark_probe=True,
                        )
                    self._audit_store_credential(
                        store_id,
                        "profile_name_hydration",
                        f"店铺名称获取失败：{message}"[:500],
                        status="failed",
                        detail={"error_code": code},
                    )
                    continue
                self.db.set_store_credential_state(
                    store_id,
                    "valid",
                    mark_probe=True,
                )
                self.db.set_store_status(store_id, result.message)
                self._audit_store_credential(
                    store_id,
                    "profile_name_hydration",
                    result.message,
                )

        threading.Thread(
            target=worker,
            name=f"store-profile-hydration-{uuid.uuid4().hex[:8]}",
            daemon=True,
        ).start()
        return {"started": True, "store_count": len(ids)}

    def _store_credentials_detail(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        store = self._require_store(self._store_id(payload))
        state = self.db.get_store_credential_state(store.id)
        candidates = [
            {
                **{
                    key: value
                    for key, value in candidate.items()
                    if key != "secret_fingerprint"
                },
                "fingerprint_prefix": str(
                    candidate.get("secret_fingerprint") or ""
                )[:10],
            }
            for candidate in self.db.list_store_credential_candidates(
                store.id
            )
        ]
        audit = [
            row
            for row in self.db.list_task_logs(
                limit=5000,
                scope="stores",
            )
            if int(row.get("store_id") or 0) == store.id
        ][:100]
        return {
            "store_id": store.id,
            "store_name": store.name,
            "app_id": store.app_id,
            "state": state,
            "candidates": candidates,
            "audit": audit,
        }

    @staticmethod
    def _credential_failure(
        exc: Exception,
    ) -> tuple[str, str, str]:
        if isinstance(exc, ValueError):
            return "invalid", "invalid_input", _safe_error_message(exc)
        if isinstance(exc, WechatApiError):
            code = str(exc.code or "")
            if exc.category == "authentication":
                return "invalid", code or "authentication", _safe_error_message(exc)
            return "transient", code, _safe_error_message(exc)
        return "transient", "", _safe_error_message(exc)

    def _probe_store_credential(self, payload: dict[str, Any]) -> dict[str, Any]:
        service = self._require_product_service()
        store = self._require_store(self._store_id(payload))
        _current_store, secret = self.db.get_store_secret(store.id)
        if not secret:
            self.db.set_store_credential_state(
                store.id,
                "invalid",
                error_code="empty_secret",
                error_message="AppSecret 为空",
                mark_probe=True,
            )
            raise ValueError("该店铺没有 AppSecret")
        try:
            message = service.validate_store_credential(store, secret)
        except Exception as exc:
            state, code, safe_message = self._credential_failure(exc)
            self.db.set_store_credential_state(
                store.id,
                state,
                error_code=code,
                error_message=safe_message,
                mark_probe=True,
            )
            raise
        state = self.db.set_store_credential_state(
            store.id,
            "valid",
            mark_probe=True,
        )
        return {
            "store_id": store.id,
            "message": message,
            "state": state,
        }

    @staticmethod
    def _store_id(payload: dict[str, Any]) -> int:
        try:
            store_id = int(payload.get("store_id"))
        except (TypeError, ValueError) as exc:
            raise ValueError("store_id 必须是整数") from exc
        if store_id <= 0:
            raise ValueError("store_id 必须是正整数")
        return store_id

    @staticmethod
    def _order_state_key(store_id: int | str, order_id: str) -> str:
        return f"{int(store_id)}:{str(order_id)}"

    @classmethod
    def _order_state_value(
        cls,
        values: dict[str, Any],
        store_id: int | str,
        order_id: str,
    ) -> Any:
        return values.get(
            cls._order_state_key(store_id, order_id),
            values.get(str(order_id), ""),
        )

    @staticmethod
    def _order_unix_seconds(value: Any) -> int:
        try:
            seconds = int(float(value))
        except (TypeError, ValueError, OverflowError):
            return 0
        if seconds <= 0:
            return 0
        if seconds > 10_000_000_000:
            seconds //= 1000
        return seconds

    def _list_orders(self, payload: dict[str, Any]) -> dict[str, Any]:
        server_time = int(time.time())
        store_id = payload.get("store_id")
        status = payload.get("status")
        requested_limit = max(1, min(int(payload.get("limit") or 300), 100_000))
        rows = self.db.list_orders(
            store_id=int(store_id) if store_id not in (None, "") else None,
            status=int(status) if status not in (None, "") else None,
            search=str(payload.get("search") or ""),
            limit=requested_limit,
        )
        total = self.db.count_orders(
            store_id=int(store_id) if store_id not in (None, "") else None,
        )
        marked_ids = self._read_string_set(ORDER_MARKS_SETTING)
        software_notes = self._read_dict_setting(ORDER_SOFTWARE_NOTES_SETTING)
        flags = self._read_dict_setting(ORDER_FLAGS_SETTING)
        workbench_rows = self.db.get_order_workbench()
        procurement_enabled = self._procurement_association_enabled()
        procurement_orders = (
            {
                int(row.get("id") or 0): row
                for row in self.db.list_procurement_orders()
            }
            if procurement_enabled
            else {}
        )
        procurement_links: dict[tuple[int, str], list[dict[str, Any]]] = {}
        for link in (
            self.db.list_procurement_links()
            if procurement_enabled
            else ()
        ):
            key = (int(link.get("store_id") or 0), str(link.get("order_id") or ""))
            procurement_orders_row = procurement_orders.get(
                int(link.get("procurement_order_id") or 0),
                {},
            )
            procurement_links.setdefault(key, []).append(
                {
                    "id": int(link.get("id") or 0),
                    "procurement_order_id": int(
                        link.get("procurement_order_id") or 0
                    ),
                    "platform": str(
                        procurement_orders_row.get("platform")
                        or link.get("platform")
                        or ""
                    ),
                    "account_label": str(
                        procurement_orders_row.get("account_label") or ""
                    ),
                    "external_order_id": str(
                        procurement_orders_row.get("external_order_id")
                        or link.get("external_order_id")
                        or ""
                    ),
                    "mail_no": str(
                        procurement_orders_row.get("mail_no")
                        or link.get("mail_no")
                        or ""
                    ),
                    "carrier": str(
                        procurement_orders_row.get("carrier")
                        or link.get("carrier")
                        or ""
                    ),
                    "pickup_code": str(
                        procurement_orders_row.get("pickup_code")
                        or link.get("pickup_code")
                        or ""
                    ),
                    "goods_title": str(
                        procurement_orders_row.get("goods_title") or ""
                    ),
                    "pay_amount": int(
                        procurement_orders_row.get("pay_amount") or 0
                    ),
                    "status_text": str(
                        procurement_orders_row.get("status_text") or ""
                    ),
                    "match_type": str(link.get("match_type") or ""),
                    "local_note": str(link.get("local_note") or ""),
                }
            )
        after_sales_by_order: dict[tuple[int, str], list[dict[str, Any]]] = {}
        for after_sale in self.db.list_after_sales():
            key = (
                int(after_sale.get("store_id") or 0),
                str(after_sale.get("order_id") or ""),
            )
            after_sales_by_order.setdefault(key, []).append(
                {
                    "after_sale_order_id": str(
                        after_sale.get("after_sale_order_id") or ""
                    ),
                    "status_text": str(after_sale.get("status_text") or ""),
                    "reason_text": str(after_sale.get("reason_text") or ""),
                    "refund_amount": int(after_sale.get("refund_amount") or 0),
                    "update_time": int(after_sale.get("update_time") or 0),
                }
            )
        dispatch_by_order = (
            self.db.order_dispatch_map()
            if procurement_enabled
            else {}
        )
        product_link_map = (
            self.db.product_purchase_link_map()
            if procurement_enabled
            else {}
        )
        product_ids_by_order: dict[tuple[int, str], set[str]] = {}
        for item in self.db.list_order_product_links():
            key = (
                int(item.get("store_id") or 0),
                str(item.get("order_id") or ""),
            )
            product_ids_by_order.setdefault(key, set()).add(
                str(item.get("product_id") or "")
            )
        flags_changed = False
        orders = []
        for row in rows:
            try:
                raw_order = json.loads(str(row.get("raw_json") or "{}"))
            except (TypeError, ValueError, json.JSONDecodeError):
                raw_order = {}
            order_detail = raw_order.get("order_detail") if isinstance(raw_order, dict) else {}
            if not isinstance(order_detail, dict):
                order_detail = {}
            price_info = order_detail.get("price_info")
            if not isinstance(price_info, dict):
                price_info = {}
            settle_info = order_detail.get("settle_info")
            if not isinstance(settle_info, dict):
                settle_info = {}
            ext_info = order_detail.get("ext_info")
            if not isinstance(ext_info, dict):
                ext_info = {}
            delivery_info = order_detail.get("delivery_info")
            if not isinstance(delivery_info, dict):
                delivery_info = {}
            packages = []
            for package in delivery_info.get("delivery_product_info") or []:
                if not isinstance(package, dict):
                    continue
                package_waybill = str(
                    package.get("waybill_id") or ""
                ).strip()
                package_delivery = str(
                    package.get("delivery_id")
                    or package.get("delivery_name")
                    or ""
                ).strip()
                if not package_waybill and not package_delivery:
                    continue
                packages.append(
                    {
                        "waybill_id": package_waybill,
                        "delivery_id": package_delivery,
                        "delivery_name": str(
                            package.get("delivery_name")
                            or package_delivery
                            or ""
                        ),
                        "product_infos": list(
                            package.get("product_infos") or []
                        ),
                    }
                )
            if not packages and str(row.get("waybill_id") or "").strip():
                packages.append(
                    {
                        "waybill_id": str(row.get("waybill_id") or ""),
                        "delivery_id": str(row.get("carrier") or ""),
                        "delivery_name": str(row.get("carrier") or ""),
                        "product_infos": [],
                    }
                )
            order_id = str(row.get("order_id") or "")
            order_key = (int(row.get("store_id") or 0), order_id)
            workbench_row = workbench_rows.get(order_key, {})
            purchase_rows = procurement_links.get(order_key, [])
            after_sale_rows = after_sales_by_order.get(order_key, [])
            dispatch_tasks = dispatch_by_order.get(order_key, [])
            has_product_links = any(
                int(
                    (
                        product_link_map.get(
                            (order_key[0], product_id)
                        )
                        or {}
                    ).get("mapping_count")
                    or 0
                )
                > 0
                for product_id in product_ids_by_order.get(order_key, set())
            )
            merchant_notes = str(row.get("merchant_notes") or "")
            raw_color = ext_info.get("merchant_notes_tag_color")
            note_color_known = raw_color not in (None, "")
            try:
                note_color_code = int(raw_color or 0)
            except (TypeError, ValueError):
                note_color_code = 0
                note_color_known = False
            official_flag = OFFICIAL_NOTE_COLOR_NAMES.get(
                note_color_code,
                "",
            )
            official_flag_present = note_color_known and note_color_code != 0
            state_key = self._order_state_key(row.get("store_id"), order_id)
            if (
                official_flag_present
                and self._order_state_value(
                    flags,
                    row.get("store_id"),
                    order_id,
                )
                != official_flag
            ):
                flags[state_key] = official_flag
                flags_changed = True
            software_note = str(
                self._order_state_value(
                    software_notes,
                    row.get("store_id"),
                    order_id,
                )
                or workbench_row.get("note")
                or ""
            )
            local_flag = str(
                self._order_state_value(
                    flags,
                    row.get("store_id"),
                    order_id,
                )
                or workbench_row.get("flag_color")
                or ""
            )
            numeric_status = int(row.get("status") or 0)
            shipping_deadline_at = 0
            if numeric_status == 20:
                platform_deadlines = [
                    value
                    for value in (
                        self._order_unix_seconds(
                            item.get("delivery_deadline")
                        )
                        for item in order_detail.get("product_infos") or []
                        if isinstance(item, dict)
                    )
                    if value
                ]
                predict_deadline = self._order_unix_seconds(
                    delivery_info.get("predict_delivery_time")
                )
                if predict_deadline:
                    platform_deadlines.append(predict_deadline)
                if platform_deadlines:
                    shipping_deadline_at = min(platform_deadlines)
                else:
                    fallback_start = self._order_unix_seconds(
                        row.get("pay_time") or row.get("create_time")
                    )
                    if fallback_start:
                        shipping_deadline_at = fallback_start + 48 * 3600
            procurement_state = "not_required"
            if procurement_enabled:
                if after_sale_rows:
                    procurement_state = "aftersale"
                elif any(
                    int(task.get("result_unknown") or 0)
                    or str(task.get("status") or "")
                    in {"error", "verification_pending"}
                    for task in dispatch_tasks
                ):
                    procurement_state = "error"
                elif purchase_rows or any(
                    int(task.get("procurement_order_id") or 0)
                    for task in dispatch_tasks
                ):
                    if numeric_status in {21, 30, 100}:
                        procurement_state = "shipped"
                    elif any(
                        str(task.get("tracking_no") or "").strip()
                        for task in dispatch_tasks
                    ):
                        procurement_state = "auto_filling"
                    else:
                        procurement_state = "wait_supplier"
                elif numeric_status in {17, 20}:
                    procurement_state = (
                        "wait_purchase"
                        if has_product_links
                        else "unlinked"
                    )
            cached_address = self.db.get_order_address_cache(
                int(row.get("store_id") or 0),
                str(row.get("order_id") or ""),
            )
            display_address = (
                dict(cached_address["address"])
                if cached_address
                and address_is_complete(cached_address.get("address"))
                else {}
            )
            orders.append(
                {
                    "store_id": int(row.get("store_id") or 0),
                    "store_name": str(row.get("store_name") or ""),
                    "order_id": str(row.get("order_id") or ""),
                    "status": numeric_status,
                    "status_text": order_status_text(numeric_status),
                    "create_time": int(row.get("create_time") or 0),
                    "update_time": int(row.get("update_time") or 0),
                    "pay_time": int(row.get("pay_time") or 0),
                    "shipping_deadline_at": shipping_deadline_at,
                    "total_amount": int(row.get("total_amount") or 0),
                    "product_amount": int(row.get("product_amount") or 0),
                    "discount_amount": int(row.get("discount_amount") or 0),
                    "merchant_received": int(
                        price_info.get("merchant_receieve_price")
                        or price_info.get("merchant_received_price")
                        or row.get("total_amount")
                        or 0
                    ),
                    "service_fee": int(settle_info.get("predict_commission_fee") or 0),
                    "buyer_name": str(
                        display_address.get("user_name")
                        or row.get("buyer_name")
                        or ""
                    ),
                    "phone": str(
                        display_address.get("tel_number")
                        or row.get("phone")
                        or ""
                    ),
                    "address": str(
                        "".join(
                            dict.fromkeys(
                                value
                                for value in (
                                    display_address.get("province_name"),
                                    display_address.get("city_name"),
                                    display_address.get("county_name"),
                                    display_address.get("street_name"),
                                    display_address.get("detail_info"),
                                )
                                if value
                            )
                        )
                        or row.get("address")
                        or ""
                    ),
                    "waybill_id": str(row.get("waybill_id") or ""),
                    "carrier": str(row.get("carrier") or ""),
                    "packages": packages,
                    "merchant_notes": merchant_notes,
                    "software_note": software_note,
                    "flag": local_flag,
                    "official_note_color": official_flag,
                    "official_note_color_code": (
                        note_color_code if note_color_known else None
                    ),
                    "official_flag_present": official_flag_present,
                    "purchase_order_no": (
                        str(
                            workbench_row.get("purchase_order_no")
                            or (
                                purchase_rows[0]["external_order_id"]
                                if purchase_rows
                                else ""
                            )
                            or ""
                        )
                        if procurement_enabled
                        else ""
                    ),
                    "pickup_code": (
                        str(
                            workbench_row.get("pickup_code")
                            or next(
                                (
                                    item["pickup_code"]
                                    for item in purchase_rows
                                    if item["pickup_code"]
                                ),
                                "",
                            )
                        )
                        if procurement_enabled
                        else ""
                    ),
                    "purchase_orders": purchase_rows,
                    "procurement_count": len(purchase_rows),
                    "procurement_tasks": dispatch_tasks,
                    "procurement_state": procurement_state,
                    "needs_procurement": procurement_state in {
                        "unlinked",
                        "wait_purchase",
                        "error",
                    },
                    "cost": (
                        int(workbench_row.get("cost") or 0)
                        if procurement_enabled
                        else 0
                    ),
                    "profit": (
                        int(workbench_row.get("profit") or 0)
                        if procurement_enabled
                        else 0
                    ),
                    "fulfillment_stage": (
                        str(workbench_row.get("fulfillment_stage") or "")
                        if procurement_enabled
                        else ""
                    ),
                    "is_marked_sent": bool(workbench_row.get("is_marked_sent")),
                    "after_sales": after_sale_rows,
                    "after_sale_count": len(after_sale_rows),
                    "item_summary": str(row.get("item_summary") or ""),
                    "item_thumb_url": str(row.get("item_thumb_url") or ""),
                    "item_product_id": str(row.get("item_product_id") or ""),
                    "is_marked": str(row.get("order_id") or "") in marked_ids,
                }
            )
        if flags_changed:
            self.db.set_settings(
                {
                    ORDER_FLAGS_SETTING: json.dumps(flags, ensure_ascii=False),
                }
            )
        return {
            "orders": orders,
            "server_time": server_time,
            "total": total,
            "returned": len(orders),
            "truncated": total > len(orders),
        }

    def _order_workbench_detail(self, payload: dict[str, Any]) -> dict[str, Any]:
        store = self._require_store(self._store_id(payload))
        order_id = str(payload.get("order_id") or "").strip()
        if not order_id:
            raise ValueError("order_id 不能为空")
        order_payload = next(
            (
                row
                for row in self._list_orders(
                    {"store_id": store.id, "search": order_id, "limit": 100_000}
                )["orders"]
                if str(row.get("order_id")) == order_id
            ),
            None,
        )
        if order_payload is None:
            raise ValueError("订单不存在")
        procurement_enabled = self._procurement_association_enabled()
        order_record = self.db.get_order(store.id, order_id)
        raw_address = (
            order_record.raw.get("order_detail", {})
            .get("delivery_info", {})
            .get("address_info", {})
            if order_record is not None
            else {}
        )
        if not isinstance(raw_address, dict):
            raw_address = {}
        cached_address = self.db.get_order_address_cache(
            store.id,
            order_id,
        )
        procurement_address = {}
        procurement_address_source = ""
        if procurement_enabled and cached_address:
            procurement_address = dict(cached_address["address"])
            procurement_address_source = str(
                cached_address.get("source") or ""
            )
        if procurement_enabled and not procurement_address:
            (
                procurement_address,
                procurement_address_source,
            ) = self._order_procurement_address(
                store.id,
                order_id,
                order_record,
            )
        if procurement_enabled and address_is_complete(procurement_address):
            raw_address = procurement_address
        address_ready = bool(
            procurement_enabled
            and address_is_complete(procurement_address)
        )
        workbench = self.db.get_order_workbench_row(store.id, order_id) or {}
        procurement_links = []
        for link in (
            self.db.list_procurement_links(store.id, order_id)
            if procurement_enabled
            else ()
        ):
            procurement = self.db.get_procurement_order(
                int(link.get("procurement_order_id") or 0)
            ) or {}
            procurement_links.append(
                {
                    "id": int(link.get("id") or 0),
                    "procurement_order_id": int(
                        link.get("procurement_order_id") or 0
                    ),
                    "platform": str(procurement.get("platform") or ""),
                    "account_label": str(procurement.get("account_label") or ""),
                    "external_order_id": str(
                        procurement.get("external_order_id") or ""
                    ),
                    "mail_no": str(procurement.get("mail_no") or ""),
                    "carrier": str(procurement.get("carrier") or ""),
                    "pickup_code": str(procurement.get("pickup_code") or ""),
                    "goods_title": str(procurement.get("goods_title") or ""),
                    "goods_count": int(procurement.get("goods_count") or 0),
                    "pay_amount": int(procurement.get("pay_amount") or 0),
                    "status_text": str(procurement.get("status_text") or ""),
                    "match_type": str(link.get("match_type") or ""),
                    "local_note": str(link.get("local_note") or ""),
                    "updated_at": str(
                        procurement.get("updated_at")
                        or link.get("updated_at")
                        or ""
                    ),
                }
            )
        after_sales = [
            {
                "after_sale_order_id": str(row.get("after_sale_order_id") or ""),
                "status": str(row.get("status") or ""),
                "status_text": str(row.get("status_text") or ""),
                "after_sale_type": str(row.get("after_sale_type") or ""),
                "reason_text": str(row.get("reason_text") or ""),
                "refund_amount": int(row.get("refund_amount") or 0),
                "create_time": int(row.get("create_time") or 0),
                "update_time": int(row.get("update_time") or 0),
            }
            for row in self.db.list_after_sales(store.id)
            if str(row.get("order_id") or "") == order_id
        ]
        accounts = (
            [
                self._public_procurement_account(row)
                for row in self.db.list_procurement_accounts()
            ]
            if procurement_enabled
            else []
        )
        account_labels = {
            int(account["id"]): str(account.get("label") or "")
            for account in accounts
        }
        product_purchase_links: list[dict[str, Any]] = []
        product_sku_mappings: list[dict[str, Any]] = []
        product_ids = sorted(
            {
                str(item.product_id)
                for item in (
                    order_record.items
                    if order_record is not None
                    else []
                )
                if str(item.product_id or "").strip()
            }
        )
        for product_id in (product_ids if procurement_enabled else ()):
            purchase_records = self.db.list_procurement_purchase_records(
                store.id,
                order_id,
                product_id,
            )
            product_links = self.db.list_product_purchase_links(
                store.id,
                product_id,
            )
            for row in product_links:
                row_link_id = int(row.get("id") or 0)
                link_records = [
                    record
                    for record in purchase_records
                    if int(record.get("link_id") or 0) == row_link_id
                ]
                if not link_records and len(product_links) == 1:
                    link_records = [
                        record
                        for record in purchase_records
                        if int(record.get("link_id") or 0) == 0
                    ]
                try:
                    source_skus = json.loads(
                        str(row.get("source_skus_json") or "[]")
                    )
                except (TypeError, ValueError, json.JSONDecodeError):
                    source_skus = []
                account_id = int(row.get("account_id") or 0)
                attempt = self.db.get_procurement_purchase_attempt(
                    store.id,
                    order_id,
                    product_id,
                    int(row.get("id") or 0),
                )
                product_purchase_links.append(
                    {
                        "id": int(row.get("id") or 0),
                        "store_id": int(row.get("store_id") or 0),
                        "product_id": product_id,
                        "source_url": str(row.get("source_url") or ""),
                        "platform": str(row.get("platform") or ""),
                        "account_id": account_id,
                        "account_label": account_labels.get(
                            account_id,
                            "",
                        ),
                        "source_product_id": str(
                            row.get("source_product_id") or ""
                        ),
                        "source_title": str(
                            row.get("source_title") or ""
                        ),
                        "source_skus": (
                            source_skus
                            if isinstance(source_skus, list)
                            else []
                        ),
                        "purchase_attempt_status": str(
                            (attempt or {}).get("status") or ""
                        ),
                        "purchase_attempt_error": str(
                            (attempt or {}).get("last_error") or ""
                        ),
                        "purchase_opened_at": str(
                            (attempt or {}).get("opened_at") or ""
                        ),
                        "purchase_confirmed": bool(link_records),
                        "purchase_record": (
                            {
                                key: link_records[0].get(key)
                                for key in (
                                    "link_id",
                                    "upstream_order_id",
                                    "trade_no",
                                    "mail_no",
                                    "carrier",
                                    "upstream_shop",
                                    "goods_title",
                                    "upstream_sku",
                                    "quantity",
                                    "unit_amount",
                                    "paid_amount",
                                    "freight_amount",
                                    "status_text",
                                    "purchased_at",
                                )
                            }
                            if link_records
                            else None
                        ),
                        "use_transfer_warehouse": False,
                    }
                )
            product_sku_mappings.extend(
                self.db.list_product_sku_mappings(
                    store.id,
                    product_id,
                )
            )
        return {
            "order": {
                **order_payload,
                "address_info": {
                    key: str(raw_address.get(key) or "")
                    for key in (
                        "user_name",
                        "tel_number",
                        "province_name",
                        "city_name",
                        "county_name",
                        "street_name",
                        "detail_info",
                    )
                },
            },
            "items": [
                {
                    "product_id": item.product_id,
                    "sku_id": item.sku_id,
                    "title": item.title,
                    "sku_count": item.sku_count,
                    "sale_price": item.sale_price,
                    "sku_attributes": item.sku_attributes,
                    "thumb_url": item.thumb_url,
                }
                for item in (order_record.items if order_record is not None else [])
            ],
            "workbench": {
                "purchase_order_no": (
                    str(workbench.get("purchase_order_no") or "")
                    if procurement_enabled
                    else ""
                ),
                "pickup_code": (
                    str(workbench.get("pickup_code") or "")
                    if procurement_enabled
                    else ""
                ),
                "note": str(workbench.get("note") or ""),
                "profit": (
                    int(workbench.get("profit") or 0)
                    if procurement_enabled
                    else 0
                ),
                "cost": (
                    int(workbench.get("cost") or 0)
                    if procurement_enabled
                    else 0
                ),
                "is_marked": bool(workbench.get("is_marked")),
                "is_marked_sent": bool(workbench.get("is_marked_sent")),
                "fulfillment_stage": (
                    str(workbench.get("fulfillment_stage") or "")
                    if procurement_enabled
                    else ""
                ),
                "last_error": str(workbench.get("last_error") or ""),
                "flag_color": str(workbench.get("flag_color") or ""),
                "ignored": bool(workbench.get("ignored")),
            },
            "procurement_links": procurement_links,
            "product_purchase_links": product_purchase_links,
            "product_sku_mappings": product_sku_mappings,
            "procurement_accounts": accounts,
            "procurement_address": (
                procurement_address
            ),
            "procurement_address_ready": address_ready,
            "procurement_address_source": procurement_address_source,
            "after_sales": after_sales,
            "dispatch_tasks": (
                self.db.list_order_dispatch_tasks(store.id, order_id)
                if procurement_enabled
                else []
            ),
            "shipment": self.db.get_shipment(store.id, order_id) or {},
        }

    def _update_order_workbench(self, payload: dict[str, Any]) -> dict[str, Any]:
        store = self._require_store(self._store_id(payload))
        order_id = str(payload.get("order_id") or "").strip()
        if not order_id:
            raise ValueError("order_id 不能为空")
        allowed = {
            "purchase_order_no",
            "pickup_code",
            "note",
            "profit",
            "cost",
            "is_marked",
            "is_marked_sent",
            "fulfillment_stage",
            "flag_color",
            "ignored",
        }
        changes = {
            key: payload[key]
            for key in allowed
            if key in payload
        }
        if (
            {
                "purchase_order_no",
                "profit",
                "cost",
                "fulfillment_stage",
            }.intersection(changes)
            and not self._procurement_association_enabled()
        ):
            raise PermissionError("此功能正在加急开发优化中")
        row = self.db.update_order_workbench(store.id, order_id, **changes)
        return {"store_id": store.id, "order_id": order_id, "workbench": row}

    def _manual_link_procurement(self, payload: dict[str, Any]) -> dict[str, Any]:
        store = self._require_store(self._store_id(payload))
        order_id = str(payload.get("order_id") or "").strip()
        if not order_id:
            raise ValueError("order_id 不能为空")
        purchase_order_no = str(payload.get("purchase_order_no") or "").strip()
        if not purchase_order_no:
            raise ValueError("采购单号不能为空")
        platform = str(payload.get("platform") or "1688").strip() or "1688"
        amount_cents = int(payload.get("purchase_amount_cents") or 0)
        commission = float(payload.get("commission") or 0)
        transfer_warehouse = bool(payload.get("transfer_warehouse"))
        purchase_mode = str(payload.get("purchase_mode") or "手动下单")
        flag = str(payload.get("flag") or "")
        account = next(
            (
                row
                for row in self.db.list_procurement_accounts(platform)
                if "手动补录" in str(row.get("label") or "")
            ),
            None,
        )
        account_id = (
            int(account["id"])
            if account is not None
            else self.db.add_procurement_account(platform, "手动补录")
        )
        procurement_id = self.db.upsert_procurement_order(
            account_id,
            platform,
            {
                "external_order_id": purchase_order_no,
                "trade_no": purchase_order_no,
                "status": "manual",
                "status_text": "手动补录",
                "goods_title": str(payload.get("goods_title") or ""),
                "goods_count": int(payload.get("goods_count") or 1),
                "pay_amount": amount_cents,
                "raw": {
                    "purchase_mode": purchase_mode,
                    "transfer_warehouse": transfer_warehouse,
                    "commission_pct": commission,
                    "flag": flag,
                    "store_order_id": order_id,
                },
            },
        )
        self.db.upsert_procurement_link(
            store.id,
            order_id,
            procurement_id,
            match_type="manual",
            confidence=100,
            local_note=(
                f"{purchase_mode}；中转仓：{'是' if transfer_warehouse else '否'}；"
                f"佣金：{commission:g}%"
            ),
        )
        now = datetime.now().isoformat(timespec="seconds")
        workbench = self.db.update_order_workbench(
            store.id,
            order_id,
            purchase_order_no=purchase_order_no,
            flag_color=flag,
            is_marked=1,
            cost=amount_cents,
            fulfillment_stage="purchased",
            purchased_at=now,
            last_error="",
        )
        for task in self.db.list_order_dispatch_tasks(store.id, order_id):
            self.db.update_order_dispatch_task(
                int(task.get("id") or 0),
                status="purchased",
            )
        if bool(payload.get("remember")):
            self.db.set_setting(
                ORDER_PURCHASE_INFO_SETTING,
                json.dumps(
                    {
                        "purchase_mode": purchase_mode,
                        "flag": flag,
                        "transfer_warehouse": "是" if transfer_warehouse else "否",
                        "platform": platform,
                    },
                    ensure_ascii=False,
                ),
            )
        return {
            "store_id": store.id,
            "order_id": order_id,
            "purchase_order_no": purchase_order_no,
            "procurement_order_id": procurement_id,
            "workbench": workbench,
        }

    @staticmethod
    def _optional_procurement_account_id(
        payload: dict[str, Any],
    ) -> int:
        value = payload.get("account_id")
        if value in (None, ""):
            return 0
        try:
            account_id = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("采购账号 ID 无效") from exc
        if account_id <= 0:
            raise ValueError("采购账号 ID 无效")
        return account_id

    @staticmethod
    def _procurement_platform_label(platform: str) -> str:
        return "1688" if platform == "1688" else "淘宝"

    def _procurement_account_for_platform(
        self,
        platform: str,
        requested_account_id: int = 0,
        *,
        require_logged_in: bool = False,
    ) -> dict[str, Any] | None:
        accounts = [
            row
            for row in self.db.list_procurement_accounts()
            if canonical_platform(str(row.get("platform") or ""))
            == platform
        ]
        if requested_account_id:
            requested = next(
                (
                    row
                    for row in accounts
                    if int(row.get("id") or 0) == requested_account_id
                ),
                None,
            )
            if requested is None:
                raise ValueError(
                    f"采购链接平台与采购账号平台不一致"
                )
            if (
                require_logged_in
                and str(requested.get("status") or "") != "已登录"
            ):
                raise ValueError(
                    f"{self._procurement_platform_label(platform)}采购账号未登录"
                )
            return requested
        return next(
            (
                row
                for row in accounts
                if str(row.get("status") or "") == "已登录"
            ),
            next(
                (
                    row
                    for row in accounts
                    if not require_logged_in
                ),
                None,
            ),
        )

    def _procurement_link_upsert(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        store = self._require_store(self._store_id(payload))
        order_id = str(payload.get("order_id") or "").strip()
        product_id = str(payload.get("product_id") or "").strip()
        url = str(payload.get("url") or "").strip()
        if not order_id or not product_id or not url:
            raise ValueError("订单、商品和采购链接不能为空")
        order = self.db.get_order(store.id, order_id)
        if order is None:
            raise ValueError("订单不存在")
        if product_id not in {
            str(item.product_id) for item in order.items
        }:
            raise ValueError("商品不属于当前订单")
        platform, source_product_id, source_url = (
            purchase_source_identity(url)
        )
        requested_account_id = self._optional_procurement_account_id(
            payload
        )
        account = self._procurement_account_for_platform(
            platform,
            requested_account_id,
        )
        address = (
            order.raw.get("order_detail", {})
            .get("delivery_info", {})
            .get("address_info", {})
        )
        if not isinstance(address, dict):
            address = {}
        account_id = int(account["id"]) if account else 0
        with self._procurement_link_lock:
            existing = next(
                (
                    row
                    for row in self.db.list_product_purchase_links(
                        store.id,
                        product_id,
                    )
                    if (
                        canonical_platform(
                            str(row.get("platform") or "")
                        )
                        == platform
                        and normalize_purchase_product_url(
                            str(row.get("source_url") or "")
                        )
                        == source_url
                    )
                ),
                None,
            )
            link_id = self.db.upsert_product_purchase_link(
                store.id,
                product_id,
                {
                    "id": int(existing["id"]) if existing else 0,
                    "source_url": source_url,
                    "platform": self._procurement_platform_label(platform),
                    "account_id": account_id or None,
                    "address_info": address,
                    "source_product_id": source_product_id,
                    "source_title": str(
                        (existing or {}).get("source_title") or ""
                    ),
                    "source_skus": [],
                    "use_transfer_warehouse": False,
                    "note": str(
                        payload.get("note")
                        or (existing or {}).get("note")
                        or ""
                    ),
                },
            )
        if account_id:
            self.db.link_store_procurement_account(
                store.id,
                account_id,
            )
        link = next(
            (
                row
                for row in self.db.list_product_purchase_links(
                    store.id,
                    product_id,
                )
                if int(row.get("id") or 0) == link_id
            ),
            {},
        )
        return {
            "link": {
                **link,
                "id": int(link.get("id") or link_id),
                "store_id": store.id,
                "product_id": product_id,
                "source_url": source_url,
                "platform": self._procurement_platform_label(platform),
                "account_id": account_id,
                "account_label": str((account or {}).get("label") or ""),
                "source_product_id": source_product_id,
            },
            "duplicate": existing is not None,
        }

    def _procurement_link_delete(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        store = self._require_store(self._store_id(payload))
        order_id = str(payload.get("order_id") or "").strip()
        product_id = str(payload.get("product_id") or "").strip()
        try:
            link_id = int(payload.get("link_id"))
        except (TypeError, ValueError) as exc:
            raise ValueError("采购链接 ID 无效") from exc
        order = self.db.get_order(store.id, order_id)
        if order is None:
            raise ValueError("订单不存在")
        if product_id not in {
            str(item.product_id) for item in order.items
        }:
            raise ValueError("商品不属于当前订单")
        link = next(
            (
                row
                for row in self.db.list_product_purchase_links(
                    store.id,
                    product_id,
                )
                if int(row.get("id") or 0) == link_id
            ),
            None,
        )
        if link is None:
            raise ValueError("采购链接不存在或不属于当前商品")
        self.db.delete_product_purchase_link(
            link_id,
            store_id=store.id,
            product_id=product_id,
        )
        return {
            "deleted": link_id,
            "detail": self._order_workbench_detail(
                {"store_id": store.id, "order_id": order_id}
            ),
        }

    def _procurement_link_open(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        service = self._require_product_service()
        store = self._require_store(self._store_id(payload))
        order_id = str(payload.get("order_id") or "").strip()
        product_id = str(payload.get("product_id") or "").strip()
        if (
            not callable(
                getattr(service, "open_procurement_window", None)
            )
            and (
                not product_id
                or payload.get("link_id") in (None, "")
            )
        ):
            return self._procurement_purchase_open(payload)
        try:
            link_id = int(payload.get("link_id"))
        except (TypeError, ValueError) as exc:
            raise ValueError("采购链接 ID 无效") from exc
        order = self.db.get_order(store.id, order_id)
        if order is None:
            raise ValueError("订单不存在")
        procurement_address = self._require_procurement_address(
            store.id,
            order_id,
        )
        if product_id not in {
            str(item.product_id) for item in order.items
        }:
            raise ValueError("商品不属于当前订单")
        link = next(
            (
                row
                for row in self.db.list_product_purchase_links(
                    store.id,
                    product_id,
                )
                if int(row.get("id") or 0) == link_id
            ),
            None,
        )
        if link is None:
            raise ValueError("采购链接不存在或不属于当前商品")
        platform, _source_product_id, source_url = (
            purchase_source_identity(str(link.get("source_url") or ""))
        )
        requested_account_id = self._optional_procurement_account_id(
            payload
        )
        account = self._procurement_account_for_platform(
            platform,
            requested_account_id,
            require_logged_in=True,
        )
        if account is None:
            raise ValueError(
                f"没有可用的{self._procurement_platform_label(platform)}采购账号"
            )
        account_id = int(account["id"])
        if requested_account_id and requested_account_id != account_id:
            raise ValueError("采购链接平台与采购账号平台不一致")
        if int(link.get("account_id") or 0) != account_id:
            try:
                address_info = json.loads(
                    str(link.get("address_info_json") or "{}")
                )
            except (TypeError, ValueError, json.JSONDecodeError):
                address_info = {}
            self.db.upsert_product_purchase_link(
                store.id,
                product_id,
                {
                    **link,
                    "id": link_id,
                    "source_url": source_url,
                    "platform": self._procurement_platform_label(platform),
                    "account_id": account_id,
                    "address_info": (
                        address_info
                        if isinstance(address_info, dict)
                        else {}
                    ),
                    "source_product_id": _source_product_id,
                    "source_skus": [],
                },
            )
        self.db.link_store_procurement_account(
            store.id,
            account_id,
        )
        workbench = self.db.get_order_workbench_row(
            store.id,
            order_id,
        ) or {}
        order_detail = (
            order.raw.get("order_detail", {})
            if isinstance(order.raw, dict)
            else {}
        )
        ext_info = (
            order_detail.get("ext_info", {})
            if isinstance(order_detail, dict)
            else {}
        )
        product_infos = (
            order_detail.get("product_infos", [])
            if isinstance(order_detail, dict)
            else []
        )
        thumb_by_product = {
            str(item.get("product_id") or ""): str(
                item.get("thumb_img") or ""
            )
            for item in product_infos
            if isinstance(item, dict)
        }
        customer_message = str(
            ext_info.get("customer_notes")
            or ext_info.get("buyer_message")
            or ext_info.get("user_message")
            or order_detail.get("buyer_message")
            or order_detail.get("user_message")
            or ""
        ).strip()
        full_procurement_address = "".join(
            dict.fromkeys(
                str(value or "")
                for value in (
                    procurement_address.get("province_name"),
                    procurement_address.get("city_name"),
                    procurement_address.get("county_name"),
                    procurement_address.get("street_name"),
                    procurement_address.get("detail_info"),
                )
                if value
            )
        )
        address_phone = str(
            procurement_address.get("tel_number") or ""
        )
        extension_match = re.search(
            r"[-\s转](\d{1,8})$",
            address_phone,
        )
        address_extension = (
            extension_match.group(1)
            if extension_match
            else ""
        )
        display_phone = (
            re.split(r"[-\s转]", address_phone, maxsplit=1)[0]
            if address_phone
            else ""
        )
        display_name = str(
            procurement_address.get("user_name") or ""
        )
        display_address = full_procurement_address
        if address_extension:
            display_name = f"{display_name}[{address_extension}]"
            display_address = (
                f"{display_address}[{address_extension}]"
            )
        overlay_details = {
            "account": {
                "label": str(account.get("label") or ""),
                "platform": self._procurement_platform_label(platform),
            },
            "store": {"name": store.name},
            "order": {
                "id": order_id,
                "total_amount_text": (
                    f"¥{int(order.total_amount or 0) / 100:.2f}"
                ),
                "cost_text": (
                    f"¥{int(workbench.get('cost') or 0) / 100:.2f}"
                    if int(workbench.get("cost") or 0)
                    else "待填写"
                ),
                "profit_text": (
                    f"¥{int(workbench.get('profit') or 0) / 100:.2f}"
                    if int(workbench.get("profit") or 0)
                    else "—"
                ),
                "customer_message": customer_message,
            },
            "items": [
                {
                    "product_id": item.product_id,
                    "sku_id": item.sku_id,
                    "title": item.title,
                    "sku_attributes": item.sku_attributes,
                    "quantity": item.sku_count,
                    "sale_price_text": (
                        f"¥{int(item.sale_price or 0) / 100:.2f}"
                    ),
                    "thumb_url": (
                        item.thumb_url
                        or thumb_by_product.get(item.product_id, "")
                    ),
                }
                for item in order.items
            ],
            "recipient": {
                **procurement_address,
                "full_address": full_procurement_address,
                "display_name": display_name,
                "display_phone": display_phone,
                "display_address": display_address,
            },
        }
        verify_key = (store.id, order_id, product_id, link_id)
        before_order_ids = {
            str(row.get("external_order_id") or "")
            for row in self.db.list_procurement_orders(
                account_id=account_id
            )
        }
        with self._procurement_verify_lock:
            self._procurement_purchase_baselines[
                verify_key
            ] = before_order_ids
        open_window = getattr(
            service,
            "open_procurement_window",
            None,
        )
        if callable(open_window):
            try:
                result = open_window(
                    account_id,
                    source_url,
                    procurement_address,
                    overlay_details,
                )
            except TypeError as exc:
                if "positional argument" not in str(exc):
                    raise
                result = open_window(
                    account_id,
                    source_url,
                    procurement_address,
                )
        else:
            result = service.open_procurement_product(
                account_id,
                source_url,
                [],
            )
        opened_at = datetime.now().isoformat(timespec="seconds")
        self.db.upsert_procurement_purchase_attempt(
            store.id,
            order_id,
            {
                "product_id": product_id,
                "link_id": link_id,
                "account_id": account_id,
                "platform": self._procurement_platform_label(platform),
                "source_url": source_url,
                "status": "page_open_pending_payment",
                "opened_at": opened_at,
                "last_error": "",
            },
        )
        verification_job = self._start_procurement_purchase_auto_verify(
            {
                "store_id": store.id,
                "order_id": order_id,
                "product_id": product_id,
                "link_id": link_id,
                "account_id": account_id,
            }
        )
        return {
            "status": "page_opened_pending_payment",
            "link_id": link_id,
            "account_id": account_id,
            "account_label": str(account.get("label") or ""),
            "platform": self._procurement_platform_label(platform),
            "source_url": source_url,
            "opened_at": opened_at,
            "reused": bool(result.get("reused")),
            "opened": result,
            "verification_job_id": str(
                verification_job.get("job_id") or ""
            ),
        }

    def _start_procurement_purchase_auto_verify(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        service = self._require_product_service()
        verify = getattr(
            service,
            "verify_procurement_purchase",
            None,
        )
        confirm = getattr(
            service,
            "confirm_procurement_purchase",
            None,
        )
        wait_for_signal = getattr(
            service,
            "wait_for_procurement_purchase_signal",
            None,
        )
        if (
            not callable(wait_for_signal)
            or (not callable(verify) and not callable(confirm))
        ):
            return {}
        key = (
            int(payload.get("store_id") or 0),
            str(payload.get("order_id") or ""),
            str(payload.get("product_id") or ""),
            int(payload.get("link_id") or 0),
        )
        with self._procurement_auto_verify_jobs_lock:
            for existing in self._procurement_auto_verify_jobs.values():
                if (
                    tuple(existing.get("key") or ())
                    == key
                    and existing.get("status") in {"waiting", "checking"}
                ):
                    return dict(existing)
            job_id = uuid.uuid4().hex
            job = {
                "job_id": job_id,
                "key": key,
                "status": "waiting",
                "message": "采购页已打开，等待用户完成付款",
                "attempt": 0,
                "max_attempts": PROCUREMENT_AUTO_VERIFY_MAX_ATTEMPTS,
                "reason": "",
                "detail": None,
                "created_at": datetime.now().isoformat(
                    timespec="seconds"
                ),
                "updated_at": datetime.now().isoformat(
                    timespec="seconds"
                ),
            }
            self._procurement_auto_verify_jobs[job_id] = job

        def update(**changes: Any) -> None:
            with self._procurement_auto_verify_jobs_lock:
                current = self._procurement_auto_verify_jobs.get(job_id)
                if current is not None:
                    current.update(changes)
                    current["updated_at"] = datetime.now().isoformat(
                        timespec="seconds"
                    )

        def mark_pending(reason: str) -> dict[str, Any]:
            store_id = key[0]
            order_id = key[1]
            product_id = key[2]
            link_id = key[3]
            account_id = int(payload.get("account_id") or 0)
            attempt = self.db.get_procurement_purchase_attempt(
                store_id,
                order_id,
                product_id,
                link_id,
            ) or {}
            self.db.upsert_procurement_purchase_attempt(
                store_id,
                order_id,
                {
                    "product_id": product_id,
                    "link_id": link_id,
                    "account_id": account_id,
                    "platform": str(attempt.get("platform") or ""),
                    "source_url": str(attempt.get("source_url") or ""),
                    "status": "verification_pending",
                    "opened_at": str(attempt.get("opened_at") or ""),
                    "last_error": reason,
                },
            )
            self.db.update_order_workbench(
                store_id,
                order_id,
                last_error=f"采购结果待核实：{reason}",
            )
            for task in self.db.list_order_dispatch_tasks(
                store_id,
                order_id,
            ):
                if str(task.get("product_id") or "") != product_id:
                    continue
                self.db.update_order_dispatch_task(
                    int(task.get("id") or 0),
                    status="verification_pending",
                    result_unknown=1,
                    error=reason,
                    last_checked_at=datetime.now().isoformat(
                        timespec="seconds"
                    ),
                )
            return self._order_workbench_detail(
                {"store_id": store_id, "order_id": order_id}
            )

        def worker() -> None:
            try:
                signal = wait_for_signal(
                    int(payload.get("account_id") or 0),
                    timeout_seconds=PROCUREMENT_AUTO_VERIFY_WAIT_SECONDS,
                )
                if not isinstance(signal, dict):
                    signal = {
                        "status": "error",
                        "reason": "采购页面付款信号无效",
                    }
                signal_status = str(signal.get("status") or "")
                if signal_status in {"timeout", "closed"}:
                    reason = str(
                        signal.get("reason")
                        or "未获取到采购付款完成信号"
                    )
                    detail = mark_pending(reason)
                    update(
                        status="pending",
                        reason=reason,
                        message="自动核对未取得结果，已标记待核实",
                        detail=detail,
                    )
                    return
                if signal_status not in {
                    "payment_observed",
                    "returned_to_software",
                }:
                    reason = str(
                        signal.get("reason")
                        or "采购页面付款监听失败"
                    )
                    update(
                        status="error",
                        reason=reason,
                        message="自动核对失败，已标记待核实",
                    )
                    return
                upstream_order_id = str(
                    signal.get("upstream_order_id") or ""
                ).strip()
                close_window = getattr(
                    service,
                    "close_procurement_window",
                    None,
                )
                if callable(close_window):
                    try:
                        close_window(int(payload.get("account_id") or 0))
                    except Exception:
                        pass
                update(
                    status="checking",
                    upstream_order_id=upstream_order_id,
                    message=(
                        (
                            "已识别 1688 下单成功页，"
                            f"正在按订单号 {upstream_order_id} 精确回读"
                        )
                        if upstream_order_id
                        else "已检测到付款完成/返回软件，正在自动核对采购订单"
                    ),
                )
                if PROCUREMENT_AUTO_VERIFY_POST_TRIGGER_SECONDS > 0:
                    time.sleep(
                        PROCUREMENT_AUTO_VERIFY_POST_TRIGGER_SECONDS
                    )
                max_attempts = PROCUREMENT_AUTO_VERIFY_MAX_ATTEMPTS
                last_reason = ""
                last_detail = None
                for attempt_number in range(1, max_attempts + 1):
                    update(
                        status="checking",
                        attempt=attempt_number,
                        message=(
                            f"自动核对中 / 稍后自动重试"
                            f"（{attempt_number}/{max_attempts}）"
                        ),
                    )
                    result = self._procurement_purchase_confirm(
                        {
                            **payload,
                            **(
                                {"upstream_order_id": upstream_order_id}
                                if upstream_order_id
                                else {}
                            ),
                            "_auto_verify": True,
                            "_auto_attempt": attempt_number,
                            "_auto_max_attempts": max_attempts,
                        }
                    )
                    result_status = str(
                        result.get("status") or ""
                    )
                    last_reason = str(
                        result.get("reason") or ""
                    )
                    last_detail = result.get("detail")
                    if result_status == "confirmed":
                        update(
                            status="confirmed",
                            attempt=attempt_number,
                            message="已自动回读并保存唯一匹配的采购订单",
                            reason="",
                            detail=last_detail,
                        )
                        return
                    retryable = result_status in {
                        "not_found",
                        "in_progress",
                    }
                    if (
                        retryable
                        and attempt_number < max_attempts
                    ):
                        if PROCUREMENT_AUTO_VERIFY_RETRY_SECONDS > 0:
                            time.sleep(
                                PROCUREMENT_AUTO_VERIFY_RETRY_SECONDS
                            )
                        continue
                    if not retryable:
                        break
                reason = (
                    last_reason
                    or "自动核对达到次数上限，未发现唯一采购订单"
                )
                update(
                    status="pending",
                    reason=reason,
                    message="自动核对结束，采购结果待核实",
                    detail=last_detail,
                )
            except Exception as exc:
                reason = _safe_error_message(exc)
                try:
                    detail = mark_pending(reason)
                except Exception:
                    detail = None
                update(
                    status="error",
                    reason=reason,
                    message="自动核对异常，采购结果待核实",
                    detail=detail,
                )
            finally:
                with self._procurement_verify_lock:
                    self._procurement_purchase_baselines.pop(
                        key,
                        None,
                    )

        threading.Thread(
            target=worker,
            daemon=True,
            name=f"procurement-auto-verify-{job_id[:8]}",
        ).start()
        return dict(job)

    def _procurement_purchase_auto_verify_status(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        job_id = str(payload.get("job_id") or "").strip()
        if not job_id:
            raise ValueError("job_id 不能为空")
        with self._procurement_auto_verify_jobs_lock:
            job = self._procurement_auto_verify_jobs.get(job_id)
            if job is None:
                raise ValueError("采购自动核对任务不存在或已过期")
            return {
                key: copy.deepcopy(value)
                for key, value in job.items()
            }

    def _procurement_purchase_confirm(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        service = self._require_product_service()
        store = self._require_store(self._store_id(payload))
        order_id = str(payload.get("order_id") or "").strip()
        product_id = str(payload.get("product_id") or "").strip()
        upstream_order_id = str(
            payload.get("upstream_order_id") or ""
        ).strip()
        try:
            link_id = int(payload.get("link_id"))
        except (TypeError, ValueError) as exc:
            raise ValueError("采购链接 ID 无效") from exc
        order = self.db.get_order(store.id, order_id)
        if order is None:
            raise ValueError("订单不存在")
        self._require_procurement_address(store.id, order_id)
        items = [
            item
            for item in order.items
            if str(item.product_id) == product_id
        ]
        if not items:
            raise ValueError("商品不属于当前订单")
        link = next(
            (
                row
                for row in self.db.list_product_purchase_links(
                    store.id,
                    product_id,
                )
                if int(row.get("id") or 0) == link_id
            ),
            None,
        )
        if link is None:
            raise ValueError("采购链接不存在或不属于当前商品")
        existing_records = self.db.list_procurement_purchase_records(
            store.id,
            order_id,
            product_id,
            link_id=link_id,
        )
        matching_records = [
            record
            for record in existing_records
            if (
                not upstream_order_id
                or str(record.get("upstream_order_id") or "")
                == upstream_order_id
            )
        ]
        if matching_records:
            return {
                "status": "confirmed",
                "reused": True,
                "page_closed": False,
                "read_attempts": 0,
                "record": matching_records[0],
                "detail": self._order_workbench_detail(
                    {"store_id": store.id, "order_id": order_id}
                ),
            }
        if upstream_order_id and existing_records:
            return {
                "status": "conflict",
                "reused": False,
                "page_closed": False,
                "read_attempts": 0,
                "reason": (
                    "该采购链接已关联其它上游订单 "
                    f"{existing_records[0].get('upstream_order_id') or ''}"
                ),
                "detail": self._order_workbench_detail(
                    {"store_id": store.id, "order_id": order_id}
                ),
            }
        verify = getattr(
            service,
            "verify_procurement_purchase",
            None,
        )
        confirm = getattr(
            service,
            "confirm_procurement_purchase",
            None,
        )
        attempt = self.db.get_procurement_purchase_attempt(
            store.id,
            order_id,
            product_id,
            link_id,
        )
        verify_key = (store.id, order_id, product_id, link_id)
        if (
            callable(verify)
            and not upstream_order_id
            and (
                attempt is None
                or str(attempt.get("status") or "")
                not in {
                    "page_open_pending_payment",
                    "verification_pending",
                }
            )
        ):
            return {
                "status": "open_required",
                "page_closed": False,
                "read_attempts": 0,
                "reason": "请先打开采购商品页，完成付款后再核验",
                "detail": self._order_workbench_detail(
                    {"store_id": store.id, "order_id": order_id}
                ),
            }
        platform, source_product_id, source_url = (
            purchase_source_identity(str(link.get("source_url") or ""))
        )
        requested_account_id = self._optional_procurement_account_id(
            payload
        )
        account = self._procurement_account_for_platform(
            platform,
            requested_account_id or int(link.get("account_id") or 0),
            require_logged_in=True,
        )
        if account is None:
            reason = (
                f"没有可用的{self._procurement_platform_label(platform)}"
                "采购账号"
            )
            return {
                "status": "login_required",
                "page_closed": False,
                "read_attempts": 0,
                "reason": reason,
                "detail": self._order_workbench_detail(
                    {"store_id": store.id, "order_id": order_id}
                ),
            }
        account_id = int(account["id"])
        expected_quantity = sum(
            max(0, int(item.sku_count or 0))
            for item in items
        )
        sku_mappings = [
            row
            for row in self.db.list_product_sku_mappings(
                store.id,
                product_id,
            )
            if int(row.get("purchase_link_id") or 0) == link_id
        ]
        expected_sku = str(
            payload.get("expected_sku")
            or " ".join(
                str(row.get("purchase_sku_id") or "").strip()
                for row in sku_mappings
                if str(row.get("purchase_sku_id") or "").strip()
            )
            or " ".join(
                str(item.sku_attributes or "").strip()
                for item in items
                if str(item.sku_attributes or "").strip()
            )
        ).strip()
        expected_amount = int(payload.get("expected_amount_cents") or 0)
        if expected_amount <= 0:
            expected_amount = sum(
                int(row.get("purchase_price") or 0)
                * sum(
                    max(0, int(item.sku_count or 0))
                    for item in items
                    if str(item.sku_id or "")
                    == str(row.get("store_sku_id") or "")
                )
                for row in sku_mappings
            )
        with self._procurement_verify_lock:
            before_order_ids = set(
                self._procurement_purchase_baselines.get(
                    verify_key,
                    set(),
                )
            )
        opened_at_seconds = 0
        if attempt and attempt.get("opened_at"):
            try:
                opened_at_seconds = int(
                    datetime.fromisoformat(
                        str(attempt["opened_at"]).replace("Z", "+00:00")
                    ).timestamp()
                )
            except (TypeError, ValueError):
                opened_at_seconds = 0
        if not opened_at_seconds:
            opened_at_seconds = int(time.time())
        expected = {
            "source_product_id": source_product_id,
            "quantity": expected_quantity,
            "amount_cents": expected_amount,
            "sku_tokens": [expected_sku] if expected_sku else [],
            "started_after": opened_at_seconds,
            "before_order_ids": before_order_ids,
            "upstream_order_id": upstream_order_id,
        }
        if not callable(verify) and not callable(confirm):
            raise RuntimeError("当前服务不支持官方采购订单回读")
        with self._procurement_verify_lock:
            if verify_key in self._procurement_verify_in_progress:
                return {
                    "status": "in_progress",
                    "page_closed": False,
                    "read_attempts": 0,
                    "reason": "采购核验正在进行，请勿重复点击",
                    "detail": self._order_workbench_detail(
                        {"store_id": store.id, "order_id": order_id}
                    ),
                }
            self._procurement_verify_in_progress.add(verify_key)
        try:
            if callable(verify):
                result = verify(account_id, expected)
            else:
                result = confirm(account_id, source_url, expected)
        finally:
            with self._procurement_verify_lock:
                self._procurement_verify_in_progress.discard(
                    verify_key
                )
        if not isinstance(result, dict):
            raise RuntimeError("官方采购订单回读返回无效")
        status = str(result.get("status") or "not_found")
        reason = str(result.get("reason") or "").strip()
        page_closed = bool(result.get("page_closed"))
        read_attempts = int(result.get("read_attempts") or 0)
        if status != "confirmed":
            message = reason or "采购结果待核实"
            automated = bool(payload.get("_auto_verify"))
            auto_attempt = int(
                payload.get("_auto_attempt") or 0
            )
            auto_max_attempts = int(
                payload.get("_auto_max_attempts") or 0
            )
            auto_retryable = status in {
                "not_found",
                "in_progress",
            }
            auto_intermediate = (
                automated
                and auto_retryable
                and auto_max_attempts > 0
                and auto_attempt < auto_max_attempts
            )
            attempt_error = (
                (
                    "自动核对中，稍后自动重试"
                    f"（{auto_attempt}/{auto_max_attempts}）"
                )
                if auto_intermediate
                else message
            )
            self.db.upsert_procurement_purchase_attempt(
                store.id,
                order_id,
                {
                    "product_id": product_id,
                    "link_id": link_id,
                    "account_id": account_id,
                    "platform": self._procurement_platform_label(platform),
                    "source_url": source_url,
                    "status": "verification_pending",
                    "opened_at": str(
                        (attempt or {}).get("opened_at") or ""
                    ),
                    "last_error": attempt_error,
                },
            )
            if not auto_intermediate:
                for task in self.db.list_order_dispatch_tasks(
                    store.id,
                    order_id,
                ):
                    if str(task.get("product_id") or "") != product_id:
                        continue
                    self.db.update_order_dispatch_task(
                        int(task.get("id") or 0),
                        status="verification_pending",
                        result_unknown=1,
                        error=message,
                        last_checked_at=datetime.now().isoformat(
                            timespec="seconds"
                        ),
                    )
                self.db.update_order_workbench(
                    store.id,
                    order_id,
                    last_error=f"采购结果待核实：{message}",
                )
            return {
                "status": status,
                "page_closed": page_closed,
                "read_attempts": read_attempts,
                "reason": attempt_error,
                "auto_verification": (
                    "checking"
                    if auto_intermediate
                    else ("pending" if automated else "")
                ),
                "detail": self._order_workbench_detail(
                    {"store_id": store.id, "order_id": order_id}
                ),
            }

        matched = result.get("order")
        if not isinstance(matched, dict):
            raise RuntimeError("采购成功回读缺少官方订单记录")
        upstream_order_id = str(
            matched.get("external_order_id")
            or matched.get("trade_no")
            or ""
        ).strip()
        expected_upstream_order_id = str(
            payload.get("upstream_order_id") or ""
        ).strip()
        if not upstream_order_id:
            raise RuntimeError("官方采购订单缺少订单号")
        if (
            expected_upstream_order_id
            and upstream_order_id != expected_upstream_order_id
        ):
            raise RuntimeError("官方回读订单号与成功页订单号不一致")
        quantity = int(matched.get("goods_count") or expected_quantity or 0)
        paid_amount = int(matched.get("pay_amount") or 0)
        freight_amount = int(matched.get("freight_amount") or 0)
        unit_amount = int(matched.get("unit_price") or 0)
        if not unit_amount and quantity:
            unit_amount = max(0, paid_amount // quantity)
        sale_amount = sum(
            int(item.sale_price or 0) * int(item.sku_count or 0)
            for item in items
        )
        profit = sale_amount - paid_amount - freight_amount
        purchased_at = str(
            matched.get("purchase_time")
            or datetime.now().isoformat(timespec="seconds")
        )
        upstream_sku = str(matched.get("sku_text") or "")
        upstream_shop = str(matched.get("seller") or "")
        goods_title = str(matched.get("goods_title") or "")
        store_sku_id = str(items[0].sku_id or "")
        procurement_record = {
            "external_order_id": upstream_order_id,
            "trade_no": str(matched.get("trade_no") or upstream_order_id),
            "mail_no": str(matched.get("mail_no") or ""),
            "status": str(matched.get("status") or ""),
            "status_text": str(matched.get("status_text") or ""),
            "goods_title": goods_title,
            "goods_count": quantity,
            "pay_amount": paid_amount,
            "carrier": str(matched.get("carrier") or ""),
            "tracking_time": str(matched.get("tracking_time") or ""),
            "seller": upstream_shop,
            "purchase_time": purchased_at,
            "sku_text": upstream_sku,
            "source_product_ids": [source_product_id],
            "raw": matched.get("raw") if isinstance(matched.get("raw"), dict) else matched,
        }
        procurement_order_id = self.db.upsert_procurement_order(
            account_id,
            self._procurement_platform_label(platform),
            procurement_record,
        )
        structured = {
            "product_id": product_id,
            "link_id": link_id,
            "store_sku_id": store_sku_id,
            "account_id": account_id,
            "platform": self._procurement_platform_label(platform),
            "source_url": source_url,
            "source_product_id": source_product_id,
            "upstream_order_id": upstream_order_id,
            "trade_no": str(matched.get("trade_no") or upstream_order_id),
            "mail_no": str(matched.get("mail_no") or ""),
            "carrier": str(matched.get("carrier") or ""),
            "upstream_shop": upstream_shop,
            "seller": upstream_shop,
            "goods_title": goods_title,
            "upstream_sku": upstream_sku,
            "quantity": quantity,
            "unit_amount": unit_amount,
            "paid_amount": paid_amount,
            "freight_amount": freight_amount,
            "status": str(matched.get("status") or ""),
            "status_text": str(matched.get("status_text") or ""),
            "purchased_at": purchased_at,
            "estimated_profit": profit,
            "actual_profit": profit,
            "cost": paid_amount + freight_amount,
            "raw": procurement_record["raw"],
        }
        record_id = self.db.upsert_procurement_purchase_record(
            store.id,
            order_id,
            structured,
        )
        software_note = json.dumps(
            {
                **structured,
                "account_label": str(account.get("label") or ""),
                "read_attempts": read_attempts,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        self.db.upsert_procurement_link(
            store.id,
            order_id,
            procurement_order_id,
            match_type="purchase_readback",
            confidence=100,
            local_note=software_note,
            sync_wechat_note=False,
        )
        self.db.link_store_procurement_account(store.id, account_id)
        self.db.update_order_workbench(
            store.id,
            order_id,
            purchase_order_no=upstream_order_id,
            cost=paid_amount + freight_amount,
            profit=profit,
            fulfillment_stage="wait_supplier",
            purchased_at=purchased_at,
            last_error="",
        )
        for task in self.db.list_order_dispatch_tasks(store.id, order_id):
            if str(task.get("product_id") or "") != product_id:
                continue
            self.db.update_order_dispatch_task(
                int(task.get("id") or 0),
                status="wait_supplier",
                result_unknown=0,
                procurement_order_id=procurement_order_id,
                purchase_amount=paid_amount,
                purchased_at=purchased_at,
                error="",
                last_checked_at=datetime.now().isoformat(
                    timespec="seconds"
                ),
            )
        existing = self.db.list_procurement_purchase_records(
            store.id,
            order_id,
            product_id,
            link_id=link_id,
        )
        close_window = getattr(
            service,
            "close_procurement_window",
            None,
        )
        if callable(close_window) and not payload.get("_auto_verify"):
            close_window(account_id)
            page_closed = True
        self.db.upsert_procurement_purchase_attempt(
            store.id,
            order_id,
            {
                "product_id": product_id,
                "link_id": link_id,
                "account_id": account_id,
                "platform": self._procurement_platform_label(platform),
                "source_url": source_url,
                "status": "confirmed",
                "opened_at": str((attempt or {}).get("opened_at") or ""),
                "last_error": "",
            },
        )
        return {
            "status": "confirmed",
            "reused": False,
            "page_closed": page_closed,
            "read_attempts": read_attempts,
            "record_id": record_id,
            "record": existing[0] if existing else structured,
            "detail": self._order_workbench_detail(
                {"store_id": store.id, "order_id": order_id}
            ),
        }

    @staticmethod
    def _procurement_snapshot_fingerprint(
        snapshot: dict[str, Any],
    ) -> str:
        sku_snapshot = [
            {
                "sku_id": str(item.get("sku_id") or ""),
                "label": str(item.get("label") or ""),
                "price": int(item.get("price") or 0),
                "stock": int(item.get("stock") or 0),
                "attrs": item.get("attrs") or [],
            }
            for item in snapshot.get("skus") or []
            if isinstance(item, dict)
        ]
        encoded = json.dumps(
            sku_snapshot,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _cached_procurement_snapshot(
        self,
        account_id: int,
        platform: str,
        source_id: str,
    ) -> tuple[dict[str, Any] | None, str]:
        now = time.monotonic()
        index_key = (account_id, platform, source_id)
        with self._procurement_preview_cache_lock:
            index_entry = self._procurement_preview_cache_index.get(
                index_key
            )
            if index_entry is None:
                return None, ""
            cache_key, expires_at = index_entry
            if expires_at <= now:
                self._procurement_preview_cache_index.pop(index_key, None)
                self._procurement_preview_cache.pop(cache_key, None)
                return None, ""
            cached = self._procurement_preview_cache.get(cache_key)
            if cached is None:
                self._procurement_preview_cache_index.pop(
                    index_key,
                    None,
                )
                return None, ""
            return copy.deepcopy(cached[1]), cache_key[3]

    def _cache_procurement_snapshot(
        self,
        account_id: int,
        platform: str,
        source_id: str,
        snapshot: dict[str, Any],
    ) -> str:
        fingerprint = self._procurement_snapshot_fingerprint(snapshot)
        cache_key = (account_id, platform, source_id, fingerprint)
        expires_at = time.monotonic() + 600
        index_key = (account_id, platform, source_id)
        with self._procurement_preview_cache_lock:
            self._procurement_preview_cache[cache_key] = (
                expires_at,
                copy.deepcopy(snapshot),
            )
            self._procurement_preview_cache_index[index_key] = (
                cache_key,
                expires_at,
            )
            if len(self._procurement_preview_cache) > 128:
                oldest = sorted(
                    self._procurement_preview_cache.items(),
                    key=lambda item: item[1][0],
                )[: len(self._procurement_preview_cache) - 128]
                for expired_key, _value in oldest:
                    self._procurement_preview_cache.pop(
                        expired_key,
                        None,
                    )
                for key, (stored_key, _expiry) in list(
                    self._procurement_preview_cache_index.items()
                ):
                    if stored_key not in self._procurement_preview_cache:
                        self._procurement_preview_cache_index.pop(key, None)
        return fingerprint

    def _procurement_product_preview(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        service = self._require_product_service()
        store = self._require_store(self._store_id(payload))
        order_id = str(payload.get("order_id") or "").strip()
        product_id = str(payload.get("product_id") or "").strip()
        store_sku_id = str(payload.get("store_sku_id") or "").strip()
        account_id = self._procurement_id(payload)
        url = str(payload.get("url") or "").strip()
        if not order_id or not product_id or not url:
            raise ValueError("订单、商品和采购链接不能为空")
        order = self.db.get_order(store.id, order_id)
        if order is None:
            raise ValueError("订单不存在")
        items = [
            item
            for item in order.items
            if str(item.product_id) == product_id
            and (
                not store_sku_id
                or str(item.sku_id) == store_sku_id
            )
        ]
        if not items:
            raise ValueError("订单本地商品或 SKU 不存在")
        platform, source_id, normalized_url = purchase_source_identity(url)
        account = self.db.get_procurement_account(account_id)
        if account is None:
            raise ValueError("采购账号不存在")
        if canonical_platform(str(account.get("platform") or "")) != platform:
            raise ValueError("采购链接平台与采购账号平台不一致")

        force_refresh = bool(payload.get("force_refresh"))
        snapshot, snapshot_fingerprint = (
            (None, "")
            if force_refresh
            else self._cached_procurement_snapshot(
                account_id,
                platform,
                source_id,
            )
        )
        cache_hit = snapshot is not None
        if snapshot is None:
            snapshot = service.inspect_procurement_product(
                account_id,
                normalized_url,
            )
            if not isinstance(snapshot, dict):
                raise ValueError("采购平台未返回有效商品数据")
            snapshot_platform = canonical_platform(
                str(snapshot.get("platform") or "")
            )
            if snapshot_platform != platform:
                raise ValueError("读取结果平台与目标采购链接不一致")
            if str(snapshot.get("source_product_id") or "") != source_id:
                raise ValueError(
                    "读取结果与目标采购链接商品 ID 不一致"
                )
            snapshot = {
                **snapshot,
                "source_url": normalized_url,
                "skus": validated_source_skus(
                    snapshot.get("skus") or []
                ),
            }
            snapshot_fingerprint = self._cache_procurement_snapshot(
                account_id,
                platform,
                source_id,
                snapshot,
            )

        store_skus = [
            {
                "product_id": item.product_id,
                "sku_id": item.sku_id,
                "sku_attributes": item.sku_attributes,
                "label": item.sku_attributes or item.sku_id,
                "title": item.title,
                "sku_count": item.sku_count,
                "sale_price": item.sale_price,
            }
            for item in items
        ]
        mappings = match_store_skus_to_source(
            store_skus,
            snapshot.get("skus") or [],
        )
        return {
            "account_id": account_id,
            "snapshot": snapshot,
            "store_skus": store_skus,
            "mappings": mappings,
            "matched_count": sum(
                1 for row in mappings if row.get("matched")
            ),
            "cache_hit": cache_hit,
            "cache_key": snapshot_fingerprint,
        }

    def _procurement_link_save(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        store = self._require_store(self._store_id(payload))
        order_id = str(payload.get("order_id") or "").strip()
        raw_links = payload.get("links")
        if not order_id:
            raise ValueError("订单不能为空")
        if not isinstance(raw_links, list) or not raw_links:
            raise ValueError("请至少读取一个采购链接")
        order = self.db.get_order(store.id, order_id)
        if order is None:
            raise ValueError("订单不存在")
        order_product_ids = {
            str(item.product_id)
            for item in order.items
        }
        order_sku_keys = {
            (
                str(item.product_id),
                str(item.sku_id),
            )
            for item in order.items
        }
        address = (
            order.raw.get("order_detail", {})
            .get("delivery_info", {})
            .get("address_info", {})
        )
        if not isinstance(address, dict):
            address = {}
        prepared_links: list[dict[str, Any]] = []
        for raw in raw_links:
            if not isinstance(raw, dict):
                raise ValueError("采购链接数据无效")
            snapshot = raw.get("snapshot")
            if not isinstance(snapshot, dict):
                raise ValueError("请先读取采购链接并确认上家商品")
            product_id = str(raw.get("product_id") or "").strip()
            if product_id not in order_product_ids:
                raise ValueError("采购链接绑定了不属于当前订单的商品")
            account_value = str(raw.get("account_id") or "")
            if not account_value.isdigit():
                raise ValueError("采购链接缺少有效采购账号")
            account_id = int(account_value)
            account = self.db.get_procurement_account(account_id)
            if account is None:
                raise ValueError("采购账号不存在")
            source_url = str(
                snapshot.get("source_url")
                or raw.get("source_url")
                or ""
            ).strip()
            if not source_url:
                raise ValueError("采购链接缺少商品地址")
            platform, source_product_id, source_url = (
                purchase_source_identity(source_url)
            )
            if canonical_platform(
                str(account.get("platform") or "")
            ) != platform:
                raise ValueError("采购链接平台与采购账号平台不一致")
            snapshot_platform = canonical_platform(
                str(snapshot.get("platform") or "")
            )
            if snapshot_platform != platform:
                raise ValueError("采购链接快照平台与链接不一致")
            if str(snapshot.get("source_product_id") or "") != source_product_id:
                raise ValueError("采购链接快照商品 ID 与链接不一致")
            source_skus = validated_source_skus(
                snapshot.get("skus") or []
            )
            prepared_snapshot = {
                **snapshot,
                "platform": (
                    "1688" if platform == "1688" else "淘宝"
                ),
                "source_url": source_url,
                "source_product_id": source_product_id,
                "skus": source_skus,
            }
            prepared_links.append(
                {
                    "raw": raw,
                    "product_id": product_id,
                    "account_id": account_id,
                    "platform": platform,
                    "source_product_id": source_product_id,
                    "source_url": source_url,
                    "snapshot": prepared_snapshot,
                    "source_skus": {
                        str(item["sku_id"]): item
                        for item in source_skus
                    },
                }
            )

        links_by_key = {
            (item["product_id"], item["source_url"]): item
            for item in prepared_links
        }
        mappings = []
        for row in payload.get("mappings") or []:
            if not isinstance(row, dict):
                raise ValueError("SKU 对应关系数据无效")
            purchase_sku_id = str(
                row.get("purchase_sku_id") or ""
            ).strip()
            product_id = str(row.get("product_id") or "").strip()
            store_sku_id = str(row.get("store_sku_id") or "").strip()
            source_url = str(row.get("source_url") or "").strip()
            if not purchase_sku_id:
                continue
            if (product_id, store_sku_id) not in order_sku_keys:
                raise ValueError("关联映射包含不属于当前订单的本地 SKU")
            try:
                normalized_source_url = normalize_purchase_product_url(
                    source_url
                )
            except Exception as exc:
                raise ValueError("关联映射的采购链接无效") from exc
            prepared = links_by_key.get(
                (product_id, normalized_source_url)
            )
            if prepared is None:
                raise ValueError("关联映射对应的采购链接尚未读取")
            source_sku = prepared["source_skus"].get(
                purchase_sku_id
            )
            if source_sku is None:
                raise ValueError(
                    "关联的上家 SKU 不属于已读取快照"
                )
            mappings.append(
                {
                    **row,
                    "product_id": product_id,
                    "store_sku_id": store_sku_id,
                    "source_url": prepared["source_url"],
                    "account_id": prepared["account_id"],
                    "platform": (
                        "1688"
                        if prepared["platform"] == "1688"
                        else "淘宝"
                    ),
                    "source_product_id": prepared[
                        "source_product_id"
                    ],
                    "purchase_sku_id": purchase_sku_id,
                    "purchase_sku_label": str(
                        source_sku.get("label") or ""
                    ),
                    "purchase_attrs": (
                        source_sku.get("attrs") or []
                    ),
                    "purchase_price": int(
                        source_sku.get("price") or 0
                    ),
                }
            )
        if not mappings:
            raise ValueError("至少需要确认一个店铺 SKU 对应关系")

        saved_links: dict[
            tuple[str, str],
            dict[str, Any],
        ] = {}
        for prepared in prepared_links:
            raw = prepared["raw"]
            snapshot = prepared["snapshot"]
            product_id = prepared["product_id"]
            account_id = prepared["account_id"]
            source_url = prepared["source_url"]
            link_id = self.db.upsert_product_purchase_link(
                store.id,
                product_id,
                {
                    "source_url": source_url,
                    "platform": snapshot["platform"],
                    "account_id": account_id,
                    "address_info": address,
                    "source_product_id": prepared[
                        "source_product_id"
                    ],
                    "source_title": str(snapshot.get("title") or ""),
                    "source_skus": snapshot.get("skus") or [],
                    "use_transfer_warehouse": False,
                    "note": str(raw.get("note") or ""),
                },
            )
            saved_links[(product_id, source_url)] = {
                "id": link_id,
                "account_id": account_id,
                "platform": snapshot["platform"],
                "source_product_id": prepared[
                    "source_product_id"
                ],
            }
            self.db.link_store_procurement_account(
                store.id,
                account_id,
            )
        for mapping in mappings:
            saved_link = saved_links[
                (
                    str(mapping.get("product_id") or ""),
                    str(mapping.get("source_url") or ""),
                )
            ]
            mapping["purchase_link_id"] = saved_link["id"]
        mappings_by_product: dict[str, list[dict[str, Any]]] = {}
        for mapping in mappings:
            mappings_by_product.setdefault(
                str(mapping.get("product_id") or ""),
                [],
            ).append(mapping)
        for product_id, product_mappings in mappings_by_product.items():
            self.db.replace_product_sku_mappings(
                store.id,
                product_id,
                product_mappings,
            )
        self.db.sync_auto_dispatch_tasks()
        for task in self.db.list_order_dispatch_tasks(
            store.id,
            order_id,
        ):
            task_product_id = str(task.get("product_id") or "")
            mapping = next(
                (
                    row
                    for row in mappings
                    if str(row.get("product_id") or "") == task_product_id
                    if str(row.get("store_sku_id") or "")
                    == str(task.get("store_sku_id") or "")
                ),
                None,
            )
            if mapping:
                associated_at = datetime.now().astimezone().isoformat(
                    timespec="seconds"
                )
                self.db.update_order_dispatch_task(
                    int(task["id"]),
                    status="pending_purchase",
                    error="",
                    purchase_amount=int(
                        mapping.get("purchase_price") or 0
                    ),
                    purchase_sku_id=str(
                        mapping.get("purchase_sku_id") or ""
                    ),
                    purchase_attrs_json=json.dumps(
                        mapping.get("purchase_attrs") or [],
                        ensure_ascii=False,
                    ),
                    last_checked_at=datetime.now().isoformat(
                        timespec="seconds"
                    ),
                )
                self._emit_procurement_task_event(
                    "procurement.link_associated",
                    task,
                    platform=str(mapping.get("platform") or ""),
                    account_ref=str(mapping.get("account_id") or ""),
                    source_product_id=str(
                        mapping.get("source_product_id") or ""
                    ),
                    source_sku_id=str(
                        mapping.get("purchase_sku_id") or ""
                    ),
                    mapping_count=len(mappings),
                    associated_at=associated_at,
                )
        self.db.update_order_workbench(
            store.id,
            order_id,
            fulfillment_stage="pending_purchase",
            last_error="",
        )
        detail = self._order_workbench_detail(
            {
                "store_id": store.id,
                "order_id": order_id,
            }
        )
        return {
            "saved_links": [
                item["id"]
                for item in saved_links.values()
            ],
            "mapping_count": len(mappings),
            "detail": detail,
        }

    def _procurement_purchase_open(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        service = self._require_product_service()
        store = self._require_store(self._store_id(payload))
        order_id = str(payload.get("order_id") or "").strip()
        requested_account_id = self._procurement_id(payload)
        selected_product_id = str(
            payload.get("product_id") or ""
        ).strip()
        selected_store_sku_id = str(
            payload.get("store_sku_id") or ""
        ).strip()
        selected_source_url = str(
            payload.get("source_url") or ""
        ).strip()
        if not order_id:
            raise ValueError("order_id 不能为空")
        selected_platform = ""
        if selected_source_url:
            (
                selected_platform,
                _selected_source_id,
                selected_source_url,
            ) = purchase_source_identity(selected_source_url)
        tasks = [
            row
            for row in self.db.list_order_dispatch_tasks(
                store.id,
                order_id,
            )
            if str(row.get("purchase_sku_id") or "").strip()
            and (
                not selected_product_id
                or str(row.get("product_id") or "")
                == selected_product_id
            )
            and (
                not selected_store_sku_id
                or str(row.get("store_sku_id") or "")
                == selected_store_sku_id
            )
            and (
                not selected_source_url
                or normalize_purchase_product_url(
                    str(row.get("source_url") or "")
                )
                == selected_source_url
            )
        ]
        task_ids = {
            int(value)
            for value in payload.get("task_ids") or []
            if str(value).isdigit()
        }
        if task_ids:
            tasks = [
                row for row in tasks
                if int(row.get("id") or 0) in task_ids
            ]
        if not tasks:
            raise ValueError("订单尚未完成采购链接和 SKU 关联")
        if len(tasks) != 1:
            raise ValueError(
                "请按订单商品行逐笔拍单；当前一次调用只能处理一个商品任务"
            )
        task = tasks[0]
        task_account_id = int(task.get("purchase_account_id") or 0)
        if (
            task_account_id
            and requested_account_id
            and task_account_id != requested_account_id
        ):
            raise ValueError(
                "该商品关联的采购账号与当前选择账号不一致，请重新关联"
            )
        account_id = task_account_id or requested_account_id
        account = self.db.get_procurement_account(account_id)
        if account is None:
            raise ValueError("采购账号不存在")
        account_platform = canonical_platform(
            str(account.get("platform") or "")
        )
        if account_platform not in {"taobao", "1688"}:
            raise ValueError("首版仅支持淘宝和1688采购账号")
        if selected_platform and selected_platform != account_platform:
            raise ValueError("下单链接平台与采购账号平台不一致")
        if (
            str(task.get("purchase_account_platform") or "")
            and canonical_platform(
                str(task.get("purchase_account_platform"))
            )
            != account_platform
        ):
            raise ValueError("采购任务平台与采购账号平台不一致")
        if str(task.get("status") or "") not in {
            "pending",
            "pending_purchase",
        }:
            raise ValueError(
                "该商品任务当前不是待拍单状态，结果未知时请先核实并补录"
            )
        if not str(task.get("source_url") or "").strip():
            raise ValueError("采购任务缺少商品链接")
        before = {
            int(row["id"])
            for row in self.db.list_procurement_orders(
                account_id=account_id
            )
        }
        attrs = []
        try:
            attrs = json.loads(
                str(task.get("purchase_attrs_json") or "[]")
            )
        except (TypeError, ValueError, json.JSONDecodeError):
            attrs = []
        try:
            result = service.open_procurement_product(
                account_id,
                str(task.get("source_url") or ""),
                attrs if isinstance(attrs, list) else [],
            )
        except Exception as exc:
            message = str(exc)[:1000]
            self.db.update_order_dispatch_task(
                int(task["id"]),
                status="error",
                result_unknown=0,
                error=message,
                last_checked_at=datetime.now().isoformat(
                    timespec="seconds"
                ),
            )
            raise
        self.db.update_order_dispatch_task(
            int(task["id"]),
            status="verification_pending",
            result_unknown=1,
            error="官方页面已关闭，正在回读本次采购订单",
            last_checked_at=datetime.now().isoformat(
                timespec="seconds"
            ),
        )
        try:
            sync_result = service.sync_procurement_account(account_id)
        except Exception as exc:
            sync_result = None
            sync_error = str(exc)[:1000]
        else:
            sync_error = ""
        new_orders = [
            row
            for row in self.db.list_procurement_orders(
                account_id=account_id
            )
            if int(row["id"]) not in before
        ]
        if len(new_orders) == 1 and not sync_error:
            procurement = new_orders[0]
            self.db.upsert_procurement_link(
                store.id,
                order_id,
                int(procurement["id"]),
                match_type="purchase_readback",
                confidence=100,
            )
            self.db.update_order_dispatch_task(
                int(task["id"]),
                status="wait_supplier",
                error="",
                result_unknown=0,
                procurement_order_id=int(procurement["id"]),
                purchase_amount=int(
                    procurement.get("pay_amount")
                    or task.get("purchase_amount")
                    or 0
                ),
                purchased_at=datetime.now().isoformat(
                    timespec="seconds"
                ),
            )
            linked_procurement = []
            for link in self.db.list_procurement_links(
                store.id,
                order_id,
            ):
                row = self.db.get_procurement_order(
                    int(link.get("procurement_order_id") or 0)
                ) or {}
                if row:
                    linked_procurement.append(row)
            purchase_numbers = [
                str(row.get("external_order_id") or "")
                for row in linked_procurement
                if str(row.get("external_order_id") or "")
            ]
            self.db.update_order_workbench(
                store.id,
                order_id,
                purchase_order_no=" / ".join(purchase_numbers),
                cost=sum(
                    int(row.get("pay_amount") or 0)
                    for row in linked_procurement
                ),
                fulfillment_stage="purchased",
                purchased_at=datetime.now().isoformat(timespec="seconds"),
                last_error="",
            )
            submitted_at = datetime.now().astimezone().isoformat(
                timespec="seconds"
            )
            procurement_ref = self._procurement_reference(procurement)
            self._emit_procurement_task_event(
                "procurement.purchase_submitted",
                task,
                procurement_ref=procurement_ref,
                platform=str(procurement.get("platform") or ""),
                account_ref=str(account_id),
                purchase_amount=int(
                    procurement.get("pay_amount")
                    or task.get("purchase_amount")
                    or 0
                ),
                submitted_at=submitted_at,
                verification_required=False,
            )
        else:
            reason = (
                sync_error
                or (
                    "官方采购页已关闭，但回读到 "
                    f"{len(new_orders)} 个新采购订单；请核实后补录"
                )
            )
            self.db.update_order_dispatch_task(
                int(task["id"]),
                status="verification_pending",
                result_unknown=1,
                error=reason,
                last_checked_at=datetime.now().isoformat(
                    timespec="seconds"
                ),
            )
            self.db.update_order_workbench(
                store.id,
                order_id,
                fulfillment_stage="purchase_result_unknown",
                last_error=reason,
            )
            self._emit_procurement_task_event(
                "procurement.exception",
                task,
                error_category="purchase_result_verification_pending",
                exception_at=datetime.now().astimezone().isoformat(
                    timespec="seconds"
                ),
            )
        return {
            "opened": [result],
            "task_id": int(task["id"]),
            "new_procurement_orders": len(new_orders),
            "sync": self._public_service_result(sync_result)
            if sync_result is not None
            else {},
            "detail": self._order_workbench_detail(
                {"store_id": store.id, "order_id": order_id}
            ),
        }

    def _procurement_backfill(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        result = self._manual_link_procurement(payload)
        store = self._require_store(self._store_id(payload))
        order_id = str(payload.get("order_id") or "")
        procurement_id = int(result["procurement_order_id"])
        for task in self.db.list_order_dispatch_tasks(store.id, order_id):
            self.db.update_order_dispatch_task(
                int(task["id"]),
                status="wait_supplier",
                error="",
                result_unknown=0,
                procurement_order_id=procurement_id,
                purchased_at=datetime.now().isoformat(timespec="seconds"),
            )
            procurement = self.db.get_procurement_order(
                procurement_id
            ) or {}
            self._emit_procurement_task_event(
                "procurement.purchase_submitted",
                task,
                procurement_ref=self._procurement_reference(
                    procurement
                ),
                platform=str(procurement.get("platform") or ""),
                account_ref=str(
                    procurement.get("account_id") or ""
                ),
                purchase_amount=int(
                    procurement.get("pay_amount")
                    or task.get("purchase_amount")
                    or 0
                ),
                submitted_at=datetime.now().astimezone().isoformat(
                    timespec="seconds"
                ),
                verification_required=False,
            )
        return {
            **result,
            "detail": self._order_workbench_detail(
                {"store_id": store.id, "order_id": order_id}
            ),
        }

    def _monitor_procurement_automation(
        self,
        *,
        force: bool = False,
    ) -> dict[str, Any]:
        now_monotonic = time.monotonic()
        if (
            not force
            and now_monotonic - self._procurement_monitor_last_at < 300
        ):
            return {"skipped": True, "reason": "interval"}
        if not self._procurement_monitor_lock.acquire(blocking=False):
            return {"skipped": True, "reason": "running"}
        self._procurement_monitor_last_at = now_monotonic
        try:
            service = self._require_product_service()
            accounts = [
                account
                for account in self.db.list_procurement_accounts()
                if account.get("status") == "已登录"
            ]
            for account in accounts:
                try:
                    service.sync_procurement_account(
                        int(account["id"])
                    )
                except Exception:
                    continue
            stores = {
                int(store.id): store
                for store in self.db.list_stores()
            }
            links_by_order: dict[
                tuple[int, str],
                list[dict[str, Any]],
            ] = {}
            for link in self.db.list_procurement_links():
                key = (
                    int(link.get("store_id") or 0),
                    str(link.get("order_id") or ""),
                )
                procurement = self.db.get_procurement_order(
                    int(link.get("procurement_order_id") or 0)
                ) or {}
                links_by_order.setdefault(key, []).append(
                    {**link, **procurement}
                )
            completed = 0
            errors = 0
            for (store_id, order_id), links in links_by_order.items():
                store = stores.get(store_id)
                if store is None:
                    continue
                order = self.db.get_order(store_id, order_id)
                if order is None or int(order.status) not in {17, 20}:
                    continue
                tasks = self.db.list_order_dispatch_tasks(
                    store_id,
                    order_id,
                )
                links_by_id = {
                    int(link.get("id") or link.get("procurement_order_id") or 0):
                    link
                    for link in links
                }
                for link in links:
                    procurement_id = int(
                        link.get("procurement_order_id")
                        or link.get("id")
                        or 0
                    )
                    procurement_ref = self._procurement_reference(link)
                    related_tasks = [
                        task
                        for task in tasks
                        if int(task.get("procurement_order_id") or 0)
                        == procurement_id
                    ]
                    status_text = str(
                        link.get("status_text")
                        or link.get("status")
                        or ""
                    )
                    updated_at = str(link.get("updated_at") or "")
                    for task in related_tasks:
                        self._emit_procurement_task_event(
                            "procurement.supplier_status_changed",
                            task,
                            procurement_ref=procurement_ref,
                            from_status="",
                            to_status=status_text,
                            updated_at=updated_at,
                        )
                        tracking_no = str(
                            link.get("mail_no") or ""
                        ).strip()
                        if tracking_no:
                            self._emit_procurement_task_event(
                                "procurement.tracking_received",
                                task,
                                procurement_ref=procurement_ref,
                                tracking_company=str(
                                    link.get("carrier") or ""
                                ),
                                tracking_no=tracking_no,
                                received_at=updated_at,
                            )
                after_sales = [
                    row
                    for row in self.db.list_after_sales(store_id)
                    if str(row.get("order_id") or "") == order_id
                ]
                if after_sales:
                    stage = (
                        "store_shipped"
                        if order.status in {21, 30, 100}
                        else "supplier_refund_required"
                        if links
                        else "cancel_dispatch"
                    )
                    for task in tasks:
                        self.db.update_order_dispatch_task(
                            int(task["id"]),
                            status=(
                                "cancelled"
                                if stage == "cancel_dispatch"
                                else "aftersale"
                            ),
                            error="",
                            after_sale_stage=stage,
                            last_checked_at=datetime.now().isoformat(
                                timespec="seconds"
                            ),
                        )
                        procurement = links_by_id.get(
                            int(
                                task.get("procurement_order_id")
                                or 0
                            )
                        ) or {}
                        self._emit_procurement_task_event(
                            "procurement.after_sale_changed",
                            task,
                            procurement_ref=self._procurement_reference(
                                procurement
                            ),
                            stage=stage,
                            status="aftersale",
                            updated_at=datetime.now()
                            .astimezone()
                            .isoformat(timespec="seconds"),
                        )
                    self.db.update_order_workbench(
                        store_id,
                        order_id,
                        fulfillment_stage="aftersale",
                        last_error="",
                    )
                    continue
                if not tasks or any(
                    not str(task.get("purchase_sku_id") or "").strip()
                    for task in tasks
                ):
                    self.db.update_order_workbench(
                        store_id,
                        order_id,
                        fulfillment_stage="purchase_mapping_error",
                        last_error="存在未完成的商品SKU采购对应",
                    )
                    for task in tasks:
                        self._emit_procurement_task_event(
                            "procurement.exception",
                            task,
                            error_category="purchase_mapping_incomplete",
                            exception_at=datetime.now()
                            .astimezone()
                            .isoformat(timespec="seconds"),
                        )
                    errors += 1
                    continue
                packages = []
                for link in links:
                    tracking_no = str(link.get("mail_no") or "").strip()
                    if not tracking_no:
                        for task in tasks:
                            self.db.update_order_dispatch_task(
                                int(task["id"]),
                                status="wait_supplier",
                                last_checked_at=datetime.now().isoformat(
                                    timespec="seconds"
                                ),
                            )
                        self.db.update_order_workbench(
                            store_id,
                            order_id,
                            fulfillment_stage="wait_supplier",
                            last_error="",
                        )
                        packages = []
                        break
                    carrier = str(link.get("carrier") or "").strip()
                    if not carrier:
                        error = "上家已发货，但采购记录缺少快递公司编码"
                        for task in tasks:
                            self.db.update_order_dispatch_task(
                                int(task["id"]),
                                status="error",
                                error=error,
                                tracking_no=tracking_no,
                            )
                        self.db.update_order_workbench(
                            store_id,
                            order_id,
                            fulfillment_stage="procurement_error",
                            last_error=error,
                        )
                        errors += 1
                        packages = []
                        break
                    packages.append(
                        {
                            "delivery_id": carrier,
                            "waybill_id": tracking_no,
                        }
                    )
                if not packages:
                    continue
                address_changed = self._procurement_address_changed(
                    order,
                    links,
                )
                if address_changed:
                    error = "订单收货地址已变化，自动回填已暂停"
                    for task in tasks:
                        self.db.update_order_dispatch_task(
                            int(task["id"]),
                            status="error",
                            error=error,
                        )
                    self.db.update_order_workbench(
                        store_id,
                        order_id,
                        fulfillment_stage="procurement_error",
                        last_error=error,
                    )
                    errors += 1
                    continue
                product_infos = [
                    {
                        "product_id": item.product_id,
                        "sku_id": item.sku_id,
                        "product_cnt": item.sku_count,
                    }
                    for item in order.items
                ]
                try:
                    with self._allow_platform_write(
                        store,
                        "order_delivery_update",
                    ):
                        service.update_order_delivery_packages(
                            store,
                            order_id,
                            packages,
                            product_infos,
                        )
                    official = service.refresh_order_official(
                        store,
                        order_id,
                    )
                    refreshed = self.db.get_order(store.id, order_id)
                    if (
                        refreshed is None
                        or int(refreshed.status) not in {21, 30, 100}
                    ):
                        raise RuntimeError("店铺发货后官方回读未确认")
                    cost = sum(
                        int(link.get("pay_amount") or 0)
                        for link in links
                    )
                    commission = self._profit_fee_value(
                        order.raw,
                        (
                            "commission",
                            "commission_fee",
                            "commission_amount",
                            "predict_commission_fee",
                        ),
                    )
                    profit = int(order.total_amount or 0) - cost - commission
                    now_text = datetime.now().isoformat(timespec="seconds")
                    for task in tasks:
                        self.db.update_order_dispatch_task(
                            int(task["id"]),
                            status="shipped",
                            error="",
                            shipped_at=now_text,
                            last_checked_at=now_text,
                        )
                        procurement = links_by_id.get(
                            int(
                                task.get("procurement_order_id")
                                or 0
                            )
                        ) or {}
                        self._emit_procurement_task_event(
                            "procurement.store_shipped",
                            task,
                            procurement_ref=self._procurement_reference(
                                procurement
                            ),
                            package_count=len(packages),
                            shipped_at=now_text,
                            official_readback=True,
                        )
                    self.db.update_order_workbench(
                        store.id,
                        order_id,
                        cost=cost,
                        profit=profit,
                        fulfillment_stage="shipped",
                        shipped_at=now_text,
                        last_error="",
                    )
                    completed += 1
                except Exception as exc:
                    error = str(exc)[:1000]
                    for task in tasks:
                        self.db.update_order_dispatch_task(
                            int(task["id"]),
                            status=(
                                "verification_pending"
                                if "回读未确认" in error
                                else "error"
                            ),
                            error=error,
                        )
                    self.db.update_order_workbench(
                        store.id,
                        order_id,
                        fulfillment_stage=(
                            "shipment_verification_pending"
                            if "回读未确认" in error
                            else "procurement_error"
                        ),
                        last_error=error,
                    )
                    errors += 1
            return {
                "completed": completed,
                "errors": errors,
                "account_count": len(accounts),
            }
        finally:
            self._procurement_monitor_lock.release()

    @staticmethod
    def _procurement_address_changed(
        order: Any,
        links: list[dict[str, Any]],
    ) -> bool:
        current = (
            order.raw.get("order_detail", {})
            .get("delivery_info", {})
            .get("address_info", {})
        )
        if not isinstance(current, dict):
            return False
        for link in links:
            try:
                stored = json.loads(
                    str(link.get("address_info_json") or "{}")
                )
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if not isinstance(stored, dict) or not stored:
                continue
            keys = (
                "user_name",
                "tel_number",
                "province_name",
                "city_name",
                "county_name",
                "street_name",
                "detail_info",
            )
            if any(
                str(stored.get(key) or "")
                != str(current.get(key) or "")
                for key in keys
            ):
                return True
        return False

    def _set_procurement_focus(self, payload: dict[str, Any]) -> dict[str, Any]:
        store = self._require_store(self._store_id(payload))
        order_id = str(payload.get("order_id") or "").strip()
        if not order_id:
            raise ValueError("order_id 不能为空")
        product_ids = [
            str(value)
            for value in payload.get("product_ids", [])
            if str(value).strip()
        ]
        focus = {
            "store_id": store.id,
            "order_id": order_id,
            "product_ids": product_ids,
            "count": max(1, int(payload.get("count") or 1)),
            "mode": str(payload.get("mode") or "manual"),
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }
        self.db.set_setting(
            "procurement_focus",
            json.dumps(focus, ensure_ascii=False),
        )
        return {
            "focus": focus,
            "accounts": [
                self._public_procurement_account(row)
                for row in self.db.list_procurement_accounts()
            ],
        }

    def _browse_procurement(self, payload: dict[str, Any]) -> dict[str, Any]:
        service = self._require_product_service()
        account_id = self._procurement_id(payload)
        result = service.procurement_browse(account_id)
        return self._public_service_result(result)

    def _sync_procurement_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        service = self._require_product_service()
        store = self._require_store(self._store_id(payload))
        order_id = str(payload.get("order_id") or "").strip()
        account_id = self._procurement_id(payload)
        result = service.sync_procurement_account(account_id)
        matched = int(service.match_procurement_orders(store) or 0)
        links = self.db.list_procurement_links(store.id, order_id)
        if links:
            first = self.db.get_procurement_order(
                int(links[0].get("procurement_order_id") or 0)
            ) or {}
            self.db.update_order_workbench(
                store.id,
                order_id,
                purchase_order_no=str(first.get("external_order_id") or ""),
                pickup_code=str(first.get("pickup_code") or ""),
                cost=int(first.get("pay_amount") or 0),
                fulfillment_stage="purchased",
                purchased_at=datetime.now().isoformat(timespec="seconds"),
                last_error="",
            )
        return {
            **self._public_service_result(result),
            "matched": matched,
            "procurement_links": [
                {
                    "external_order_id": str(row.get("external_order_id") or ""),
                    "mail_no": str(row.get("mail_no") or ""),
                    "pickup_code": str(row.get("pickup_code") or ""),
                }
                for row in links
            ],
        }

    def _sync_procurement_order_note(self, payload: dict[str, Any]) -> dict[str, Any]:
        service = self._require_product_service()
        store = self._require_store(self._store_id(payload))
        order_id = str(payload.get("order_id") or "").strip()
        if not order_id:
            raise ValueError("order_id 不能为空")
        try:
            with self._allow_platform_write(store, "order_notes_update"):
                result = service.sync_procurement_note_to_wechat(store, order_id)
        except Exception as exc:
            self._write_operation_log(
                "orders",
                "同步采购备注",
                f"采购备注同步失败：{store.name} · {order_id}",
                store_id=store.id,
                status="failed",
                detail={"order_id": order_id, "error": str(exc)},
            )
            raise
        self._write_operation_log(
            "orders",
            "同步采购备注",
            f"采购备注同步成功：{store.name} · {order_id}",
            store_id=store.id,
            detail={"order_id": order_id},
        )
        return self._public_service_result(result)

    def _order_procurement_address(
        self,
        store_id: int,
        order_id: str,
        order: Any | None = None,
    ) -> tuple[dict[str, Any], str]:
        cached = self.db.get_order_address_cache(store_id, order_id)
        if cached and address_is_complete(cached.get("address")):
            return dict(cached["address"]), str(
                cached.get("source") or "software_cache"
            )
        record = order or self.db.get_order(store_id, order_id)
        if record is None:
            return {}, ""
        raw_info = (
            record.raw.get("order_detail", {})
            .get("delivery_info", {})
            .get("address_info", {})
        )
        if not isinstance(raw_info, dict):
            raw_info = {}
        address = {
            "user_name": str(
                raw_info.get("user_name") or record.buyer_name or ""
            ),
            "tel_number": str(
                raw_info.get("tel_number") or record.phone or ""
            ),
            "province_name": str(raw_info.get("province_name") or ""),
            "city_name": str(raw_info.get("city_name") or ""),
            "county_name": str(raw_info.get("county_name") or ""),
            "street_name": str(raw_info.get("street_name") or ""),
            "detail_info": str(
                raw_info.get("detail_info") or record.address or ""
            ),
        }
        return address, "official_order"

    def _require_procurement_address(
        self,
        store_id: int,
        order_id: str,
    ) -> dict[str, Any]:
        address, _source = self._order_procurement_address(
            store_id,
            order_id,
        )
        if not address_is_complete(address):
            raise ValueError("采购地址已加密/不完整，无法购买")
        return address

    def _parse_order_address(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        return parse_address_text(str(payload.get("text") or ""))

    def _save_order_address_software(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        store = self._require_store(self._store_id(payload))
        order_id = str(payload.get("order_id") or "").strip()
        order = self.db.get_order(store.id, order_id)
        if order is None:
            raise ValueError("订单不存在")
        raw_address = payload.get("address")
        if not isinstance(raw_address, dict):
            raise ValueError("地址格式无效")
        parsed = parse_address_text(
            " ".join(
                str(raw_address.get(key) or "")
                for key in (
                    "user_name",
                    "tel_number",
                    "province_name",
                    "city_name",
                    "county_name",
                    "detail_info",
                )
            )
        )
        address = {
            key: str(raw_address.get(key) or "").strip()
            for key in (
                "user_name",
                "tel_number",
                "province_name",
                "city_name",
                "county_name",
                "street_name",
                "detail_info",
            )
        }
        for key, value in parsed["address"].items():
            if not address.get(key) and value:
                address[key] = value
        if not address_is_complete(address):
            raise ValueError(
                parsed.get("reason")
                or "收货人、手机号、省、市、区县和详细地址必须完整"
            )
        cached = self.db.upsert_order_address_cache(
            store.id,
            order_id,
            address,
            source="software_manual",
            force=True,
        )
        return {
            "order_id": order_id,
            "address": cached["address"],
            "address_ready": True,
            "source": cached["source"],
            "updated_at": cached["updated_at"],
            "official_address_modified": False,
        }

    def _request_order_address_change(self, payload: dict[str, Any]) -> dict[str, Any]:
        service = self._require_product_service()
        store = self._require_store(self._store_id(payload))
        order_id = str(payload.get("order_id") or "").strip()
        address = payload.get("address")
        if not order_id:
            raise ValueError("order_id 不能为空")
        if not isinstance(address, dict):
            raise ValueError("收货地址格式无效")
        normalized = {
            key: str(address.get(key) or "")
            for key in (
                "user_name",
                "tel_number",
                "province_name",
                "city_name",
                "county_name",
                "street_name",
                "detail_info",
            )
        }
        if not normalized["user_name"] or not normalized["tel_number"]:
            raise ValueError("收货人和电话不能为空")
        try:
            with self._allow_platform_write(store, "order_address_update"):
                service.request_order_address_change(store, order_id, normalized)
        except Exception as exc:
            self._write_operation_log(
                "orders",
                "提交改址申请",
                f"改址申请提交失败：{store.name} · {order_id}",
                store_id=store.id,
                status="failed",
                detail={"order_id": order_id, "error": str(exc)},
            )
            raise
        self._write_operation_log(
            "orders",
            "提交改址申请",
            f"改址申请已提交：{store.name} · {order_id}",
            store_id=store.id,
            detail={"order_id": order_id},
        )
        return {"order_id": order_id, "submitted": True}

    def _update_order_delivery(self, payload: dict[str, Any]) -> dict[str, Any]:
        service = self._require_product_service()
        store = self._require_store(self._store_id(payload))
        order_id = str(payload.get("order_id") or "").strip()
        packages = payload.get("packages")
        if not isinstance(packages, list) or not packages:
            raise ValueError("至少需要一个店铺包裹")
        order = self.db.get_order(store.id, order_id)
        if order is None:
            raise ValueError("订单不存在")
        product_infos = [
            {
                "product_id": item.product_id,
                "sku_id": item.sku_id,
                "product_cnt": item.sku_count,
            }
            for item in order.items
        ]
        normalized = [
            {
                "delivery_id": str(item.get("delivery_id") or "").strip(),
                "waybill_id": str(item.get("waybill_id") or "").strip(),
            }
            for item in packages
            if isinstance(item, dict)
        ]
        normalized = [
            item
            for item in normalized
            if item["delivery_id"] and item["waybill_id"]
        ]
        if not normalized:
            raise ValueError("快递公司编码和运单号不能为空")
        try:
            with self._allow_platform_write(store, "order_delivery_update"):
                service.update_order_delivery_packages(
                    store,
                    order_id,
                    normalized,
                    product_infos,
                )
        except Exception as exc:
            self._write_operation_log(
                "orders",
                "提交订单发货",
                f"订单发货失败：{store.name} · {order_id}",
                store_id=store.id,
                status="failed",
                detail={
                    "order_id": order_id,
                    "package_count": len(normalized),
                    "error": str(exc),
                },
            )
            raise
        self._write_operation_log(
            "orders",
            "提交订单发货",
            f"订单发货成功：{store.name} · {order_id}",
            store_id=store.id,
            detail={
                "order_id": order_id,
                "package_count": len(normalized),
            },
        )
        self.db.update_order_workbench(
            store.id,
            order_id,
            fulfillment_stage="shipped",
            shipped_at=datetime.now().isoformat(timespec="seconds"),
            is_marked_sent=1,
        )
        return {
            "order_id": order_id,
            "shipped": True,
            "packages": normalized,
            "detail": self._order_workbench_detail(
                {"store_id": store.id, "order_id": order_id}
            ),
        }

    def _refresh_order_shipping(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        service = self._require_product_service()
        store = self._require_store(self._store_id(payload))
        try:
            result = service.refresh_shipping_options(store)
        except Exception as exc:
            self._write_operation_log(
                "orders",
                "刷新电子面单配置",
                f"电子面单配置刷新失败：{store.name}",
                store_id=store.id,
                status="failed",
                detail={"error": str(exc)},
            )
            raise
        public = self._public_service_result(result)
        self._write_operation_log(
            "orders",
            "刷新电子面单配置",
            f"电子面单配置刷新成功：{store.name}",
            store_id=store.id,
            detail={
                "accounts": len(self.db.list_shipping_accounts(store.id)),
                "templates": len(self.db.list_shipping_templates(store.id)),
            },
        )
        return {
            **public,
            "accounts": self.db.list_shipping_accounts(store.id),
            "templates": self.db.list_shipping_templates(store.id),
        }

    def _create_order_shipment(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        service = self._require_product_service()
        store = self._require_store(self._store_id(payload))
        order_id = str(payload.get("order_id") or "").strip()
        if not order_id:
            raise ValueError("order_id 不能为空")
        try:
            with self._allow_platform_write(store, "ewaybill_create"):
                result = service.create_shipment(store, order_id)
        except Exception as exc:
            self._write_operation_log(
                "orders",
                "生成电子面单",
                f"电子面单生成失败：{store.name} · {order_id}",
                store_id=store.id,
                status="failed",
                detail={"order_id": order_id, "error": str(exc)},
            )
            raise
        self._write_operation_log(
            "orders",
            "生成电子面单",
            f"电子面单生成成功：{store.name} · {order_id}",
            store_id=store.id,
            detail={
                "order_id": order_id,
                "waybill_id": str(result.get("waybill_id") or ""),
            },
        )
        return {
            "order_id": order_id,
            "shipment": result,
        }

    def _complete_order_shipment(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        service = self._require_product_service()
        store = self._require_store(self._store_id(payload))
        order_id = str(payload.get("order_id") or "").strip()
        if not order_id:
            raise ValueError("order_id 不能为空")
        try:
            with self._allow_platform_write(store, "order_delivery_send"):
                result = service.complete_shipment(store, order_id)
        except Exception as exc:
            self._write_operation_log(
                "orders",
                "完成电子面单发货",
                f"电子面单发货失败：{store.name} · {order_id}",
                store_id=store.id,
                status="failed",
                detail={"order_id": order_id, "error": str(exc)},
            )
            raise
        self._write_operation_log(
            "orders",
            "完成电子面单发货",
            f"电子面单发货成功：{store.name} · {order_id}",
            store_id=store.id,
            detail={
                "order_id": order_id,
                "waybill_id": str(result.get("waybill_id") or ""),
            },
        )
        return {
            "order_id": order_id,
            "shipment": result,
        }

    def _update_order_price(self, payload: dict[str, Any]) -> dict[str, Any]:
        service = self._require_product_service()
        store = self._require_store(self._store_id(payload))
        order_id = str(payload.get("order_id") or "").strip()
        changes = payload.get("changes")
        if not order_id:
            raise ValueError("order_id 不能为空")
        if not isinstance(changes, list) or not changes:
            raise ValueError("价格修改项不能为空")
        normalized = []
        for row in changes:
            if not isinstance(row, dict):
                continue
            product_id = str(row.get("product_id") or "").strip()
            sku_id = str(row.get("sku_id") or "").strip()
            change_price = int(row.get("change_price") or 0)
            if not product_id or not sku_id or change_price <= 0:
                continue
            normalized.append(
                {
                    "product_id": product_id,
                    "sku_id": sku_id,
                    "change_price": change_price,
                }
            )
        if not normalized:
            raise ValueError("没有有效的价格修改项")
        try:
            with self._allow_platform_write(store, "order_price_update"):
                service.update_order_price(
                    store,
                    order_id,
                    normalized,
                    bool(payload.get("change_express")),
                    int(payload.get("express_fee") or 0),
                )
        except Exception as exc:
            self._write_operation_log(
                "orders",
                "修改订单价格",
                f"订单改价失败：{store.name} · {order_id}",
                store_id=store.id,
                status="failed",
                detail={
                    "order_id": order_id,
                    "change_count": len(normalized),
                    "error": str(exc),
                },
            )
            raise
        self._write_operation_log(
            "orders",
            "修改订单价格",
            f"订单改价已提交：{store.name} · {order_id}",
            store_id=store.id,
            detail={
                "order_id": order_id,
                "change_count": len(normalized),
                "change_express": bool(payload.get("change_express")),
            },
        )
        return {"order_id": order_id, "updated": len(normalized)}

    def _open_official_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        session = self._require_web_session()
        store = self._require_store(self._store_id(payload))
        order_id = str(payload.get("order_id") or "").strip()
        if not order_id:
            raise ValueError("order_id 不能为空")
        with self._order_backend_jobs_lock:
            for existing in self._order_backend_jobs.values():
                if (
                    existing.get("store_id") == store.id
                    and existing.get("order_id") == order_id
                    and existing.get("status") in {"starting", "running"}
                ):
                    return {
                        "opening": True,
                        "already_running": True,
                        "job_id": existing["job_id"],
                        "order_id": order_id,
                    }
            job_id = uuid.uuid4().hex
            self._order_backend_jobs[job_id] = {
                "job_id": job_id,
                "store_id": store.id,
                "order_id": order_id,
                "status": "starting",
                "message": "正在定位微信官方订单…",
                "error": "",
            }
        log_id = self.db.start_task_log(
            "orders",
            "open_official_order",
            "正在打开微信官方订单详情",
            store_id=store.id,
            detail=order_id,
        )
        started = time.monotonic()

        def update(**changes: Any) -> None:
            with self._order_backend_jobs_lock:
                job = self._order_backend_jobs.get(job_id)
                if job is not None:
                    job.update(changes)

        def open_in_background() -> None:
            update(status="running", message="正在打开微信官方订单详情…")
            try:
                session.open_official_order(store, order_id)
            except Exception as exc:
                update(
                    status="failed",
                    message=f"打开官方订单失败：{exc}",
                    error=str(exc)[:1000],
                )
                self.db.finish_task_log(
                    log_id,
                    "failed",
                    str(exc),
                    duration_ms=int((time.monotonic() - started) * 1000),
                )
            else:
                update(
                    status="completed",
                    message="微信官方订单详情已关闭",
                )
                self.db.finish_task_log(
                    log_id,
                    "success",
                    "微信官方订单详情已关闭",
                    duration_ms=int((time.monotonic() - started) * 1000),
                )

        threading.Thread(
            target=open_in_background,
            name=f"open-official-order-{order_id}",
            daemon=True,
        ).start()
        return {
            "opening": True,
            "already_running": False,
            "job_id": job_id,
            "order_id": order_id,
        }

    def _open_official_order_status(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        job_id = str(payload.get("job_id") or "").strip()
        if not job_id:
            raise ValueError("job_id 不能为空")
        with self._order_backend_jobs_lock:
            job = self._order_backend_jobs.get(job_id)
            if job is None:
                raise ValueError("打开官方订单任务不存在")
            return dict(job)

    def _list_after_sales(self, payload: dict[str, Any]) -> dict[str, Any]:
        store_id = payload.get("store_id")
        order_id = str(payload.get("order_id") or "").strip()
        stores = {int(row.id): row.name for row in self.db.list_stores()}
        rows = self.db.list_after_sales(
            int(store_id) if store_id not in (None, "") else None
        )
        if order_id:
            rows = [
                row for row in rows
                if str(row.get("order_id") or "") == order_id
            ]
        return {
            "after_sales": [
                {
                    **row,
                    "store_name": stores.get(
                        int(row.get("store_id") or 0),
                        "",
                    ),
                    "raw_json": None,
                }
                for row in rows
            ]
        }

    def _after_sale_detail(self, payload: dict[str, Any]) -> dict[str, Any]:
        store = self._require_store(self._store_id(payload))
        after_sale_id = str(payload.get("after_sale_id") or "").strip()
        if not after_sale_id:
            raise ValueError("售后单号不能为空")
        row = self.db.get_after_sale(store.id, after_sale_id)
        if row is None:
            raise ValueError("售后记录不存在")
        try:
            raw = json.loads(str(row.get("raw_json") or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError):
            raw = {}
        return {
            "after_sale": {
                key: value
                for key, value in row.items()
                if key != "raw_json"
            },
            "raw": raw if isinstance(raw, dict) else {},
        }

    def _sync_after_sales(self, payload: dict[str, Any]) -> dict[str, Any]:
        service = self._require_product_service()
        store_id = payload.get("store_id")
        stores = (
            [self._require_store(int(store_id))]
            if store_id not in (None, "")
            else self.db.list_stores()
        )
        end_time = int(datetime.now().timestamp())
        start_time = end_time - 30 * 86400
        errors = []
        succeeded = 0
        for store in stores:
            try:
                service.sync_after_sales(
                    store,
                    start_time,
                    end_time,
                    time_field="create",
                )
            except Exception as exc:
                errors.append(f"{store.name}: {exc}")
            else:
                succeeded += 1
                self.db.set_setting(
                    f"after_sales_last_sync_{store.id}",
                    str(end_time),
                )
        return {
            "synced": len(stores),
            "succeeded": succeeded,
            "errors": errors,
            "after_sales": self._list_after_sales({})["after_sales"],
        }

    def _after_sale_action(self, payload: dict[str, Any]) -> dict[str, Any]:
        service = self._require_product_service()
        store = self._require_store(self._store_id(payload))
        after_sale_id = str(payload.get("after_sale_id") or "").strip()
        action = str(payload.get("operation") or "").strip()
        if not after_sale_id:
            raise ValueError("售后单号不能为空")
        if not action:
            raise ValueError("售后操作不能为空")
        try:
            with self._allow_platform_write(store, f"aftersale_{action}"):
                result = service.aftersale_action(
                    store,
                    after_sale_id,
                    action,
                    payload.get("data") if isinstance(payload.get("data"), dict) else None,
                )
        except Exception as exc:
            self._write_operation_log(
                "orders",
                "售后操作",
                f"售后操作失败：{store.name} · {after_sale_id} · {action}",
                store_id=store.id,
                status="failed",
                detail={
                    "after_sale_id": after_sale_id,
                    "operation": action,
                    "error": str(exc),
                },
            )
            raise
        self._write_operation_log(
            "orders",
            "售后操作",
            f"售后操作已提交：{store.name} · {after_sale_id} · {action}",
            store_id=store.id,
            detail={
                "after_sale_id": after_sale_id,
                "operation": action,
            },
        )
        return self._public_service_result(result)

    def _remove_after_sales(self, payload: dict[str, Any]) -> dict[str, Any]:
        rows = payload.get("rows")
        if not isinstance(rows, list):
            raise ValueError("售后记录格式无效")
        removed = 0
        for row in rows:
            if not isinstance(row, dict):
                continue
            try:
                store_id = int(row.get("store_id"))
            except (TypeError, ValueError):
                continue
            after_sale_id = str(row.get("after_sale_order_id") or "").strip()
            if not after_sale_id:
                continue
            self.db.delete_after_sale(store_id, after_sale_id)
            removed += 1
        return {"removed": removed}

    def _upload_after_sale_media(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        service = self._require_product_service()
        store = self._require_store(self._store_id(payload))
        after_sale_id = str(payload.get("after_sale_id") or "").strip()
        files = payload.get("files")
        if not after_sale_id:
            raise ValueError("售后单号不能为空")
        if not isinstance(files, list) or not files:
            raise ValueError("至少选择一张售后凭证图片")
        if len(files) > 9:
            raise ValueError("单次最多上传 9 张售后凭证图片")
        media_ids: list[str] = []
        with tempfile.TemporaryDirectory(prefix="langlang-aftersale-") as temp_dir:
            paths: list[str] = []
            for index, row in enumerate(files):
                if not isinstance(row, dict):
                    raise ValueError("售后凭证文件格式无效")
                name = Path(str(row.get("name") or f"proof-{index + 1}.jpg")).name
                suffix = Path(name).suffix.lower()
                if suffix not in {".jpg", ".jpeg", ".png", ".webp"}:
                    raise ValueError(f"不支持的售后凭证格式：{name}")
                try:
                    content = base64.b64decode(
                        str(row.get("content_base64") or ""),
                        validate=True,
                    )
                except (ValueError, TypeError) as exc:
                    raise ValueError(f"售后凭证内容无效：{name}") from exc
                if not content or len(content) > 10 * 1024 * 1024:
                    raise ValueError(f"售后凭证必须小于 10MB：{name}")
                path = Path(temp_dir) / f"{index:02d}-{name}"
                path.write_bytes(content)
                paths.append(str(path))
            try:
                with self._allow_platform_write(
                    store,
                    "aftersale_media_upload",
                ):
                    media_ids = list(
                        service.upload_after_sale_media(store, paths)
                    )
            except Exception as exc:
                self._write_operation_log(
                    "orders",
                    "上传售后凭证",
                    f"售后凭证上传失败：{store.name} · {after_sale_id}",
                    store_id=store.id,
                    status="failed",
                    detail={
                        "after_sale_id": after_sale_id,
                        "file_count": len(paths),
                        "error": str(exc),
                    },
                )
                raise
        self._write_operation_log(
            "orders",
            "上传售后凭证",
            f"售后凭证上传完成：{store.name} · {after_sale_id}",
            store_id=store.id,
            detail={
                "after_sale_id": after_sale_id,
                "file_count": len(media_ids),
            },
        )
        return {
            "store_id": store.id,
            "after_sale_id": after_sale_id,
            "media_ids": media_ids,
            "count": len(media_ids),
        }

    def _get_after_sale_auto_config(self, _payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "config": self._read_dict_setting("after_sale_auto_config"),
        }

    def _save_after_sale_auto_config(self, payload: dict[str, Any]) -> dict[str, Any]:
        config = payload.get("config")
        if not isinstance(config, dict):
            raise ValueError("自动售后配置格式无效")
        normalized = {
            "enabled": bool(config.get("enabled")),
            "action": str(config.get("action") or "accept"),
            "max_amount": max(0, int(config.get("max_amount") or 0)),
            "allowed_types": str(config.get("allowed_types") or "").strip(),
        }
        if normalized["action"] not in {"accept", "reject", "negotiate"}:
            raise ValueError("自动售后动作无效")
        self.db.set_setting(
            "after_sale_auto_config",
            json.dumps(normalized, ensure_ascii=False),
        )
        log_id = self.db.start_task_log(
            "after_sale",
            "自动售后设置",
            "配置已保存",
        )
        self.db.finish_task_log(log_id, "success", "配置已保存")
        return {"config": normalized}

    def _start_auto_after_sale(self, payload: dict[str, Any]) -> dict[str, Any]:
        service = self._require_product_service()
        config = self._read_dict_setting("after_sale_auto_config")
        if not config.get("enabled"):
            raise ValueError("自动售后未启用")
        allowed = {
            item.strip()
            for item in str(config.get("allowed_types") or "").split(",")
            if item.strip()
        }
        max_amount = int(config.get("max_amount") or 0)
        store_id = payload.get("store_id")
        rows = self.db.list_after_sales(
            int(store_id) if store_id not in (None, "") else None
        )
        eligible = []
        skipped = 0
        for row in rows:
            amount = int(row.get("refund_amount") or 0)
            text = f"{row.get('after_sale_type') or ''} {row.get('status_text') or ''}"
            pending = any(
                marker in f"{row.get('status') or ''} {row.get('status_text') or ''}"
                for marker in (
                    "MERCHANT_PROCESSING",
                    "WAIT_PACKAGE_INTERCEPT",
                    "商家受理中",
                    "平台处理中",
                    "待处理",
                )
            )
            if max_amount and amount > max_amount:
                skipped += 1
                continue
            if allowed and not any(item in text for item in allowed):
                skipped += 1
                continue
            if not pending:
                skipped += 1
                continue
            eligible.append(row)
        action = str(config.get("action") or "accept")
        errors = []
        succeeded = 0
        log_id = self.db.start_task_log(
            "after_sale",
            "自动售后",
            f"准备处理 {len(eligible)} 条售后单",
        )
        for row in eligible:
            store = self.db.get_store(int(row.get("store_id") or 0))
            if store is None:
                errors.append(f"{row.get('after_sale_order_id')}: 店铺不存在")
                continue
            try:
                with self._allow_platform_write(store, f"aftersale_{action}"):
                    service.aftersale_action(
                        store,
                        str(row.get("after_sale_order_id") or ""),
                        action,
                    )
            except Exception as exc:
                errors.append(
                    f"{store.name}/{row.get('after_sale_order_id')}: {exc}"
                )
            else:
                succeeded += 1
        message = (
            f"自动售后完成：成功 {succeeded}，失败 {len(errors)}，"
            f"规则跳过 {skipped}"
        )
        self.db.finish_task_log(
            log_id,
            "success" if not errors else "partial",
            message,
        )
        return {
            "eligible": len(eligible),
            "succeeded": succeeded,
            "failed": len(errors),
            "skipped": skipped,
            "errors": errors,
            "message": message,
        }

    def _list_transfer_cases(self, payload: dict[str, Any]) -> dict[str, Any]:
        case_type = str(payload.get("case_type") or "").strip() or None
        store_id = payload.get("store_id")
        rows = self.db.list_transfer_cases(
            case_type=case_type,
            store_id=int(store_id) if store_id not in (None, "") else None,
            search=str(payload.get("search") or ""),
        )
        return {"cases": rows}

    def _create_transfer_case(self, payload: dict[str, Any]) -> dict[str, Any]:
        case_id = self.db.create_transfer_case(
            {
                "case_type": str(payload.get("case_type") or "internal"),
                "store_id": payload.get("store_id"),
                "order_id": str(payload.get("order_id") or ""),
                "source_order_no": str(payload.get("source_order_no") or ""),
                "transfer_order_no": str(payload.get("transfer_order_no") or ""),
                "work_order_no": str(payload.get("work_order_no") or ""),
                "tracking_no": str(payload.get("tracking_no") or ""),
                "pickup_code": str(payload.get("pickup_code") or ""),
                "warehouse": str(payload.get("warehouse") or ""),
                "status": str(payload.get("status") or "待处理"),
                "status_text": str(
                    payload.get("status_text")
                    or payload.get("status")
                    or "待处理"
                ),
                "note": str(payload.get("note") or ""),
            }
        )
        return {"case_id": case_id}

    def _update_transfer_case(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            case_id = int(payload.get("case_id"))
        except (TypeError, ValueError) as exc:
            raise ValueError("case_id 必须是整数") from exc
        allowed = {
            "case_type",
            "store_id",
            "order_id",
            "source_order_no",
            "transfer_order_no",
            "work_order_no",
            "tracking_no",
            "pickup_code",
            "warehouse",
            "status",
            "status_text",
            "note",
        }
        changes = {
            key: value
            for key, value in payload.items()
            if key in allowed
        }
        self.db.update_transfer_case(case_id, **changes)
        return {"case_id": case_id, "updated": True}

    def _list_pickup_messages(self, _payload: dict[str, Any]) -> dict[str, Any]:
        links_by_purchase: dict[int, list[dict[str, Any]]] = {}
        for link in self.db.list_procurement_links():
            links_by_purchase.setdefault(
                int(link.get("procurement_order_id") or 0),
                [],
            ).append(link)
        workbench = self.db.get_order_workbench()
        stores = {
            int(row.id): row.name
            for row in self.db.list_stores()
        }
        rows = []
        for order in self.db.list_procurement_orders():
            pickup_code = str(order.get("pickup_code") or "").strip()
            if not pickup_code:
                continue
            links = links_by_purchase.get(int(order.get("id") or 0), [])
            sent = any(
                bool(
                    (workbench.get(
                        (int(link.get("store_id") or 0), str(link.get("order_id") or ""))
                    ) or {}).get("is_marked_sent")
                )
                for link in links
            )
            rows.append(
                {
                    "account_label": str(order.get("account_label") or ""),
                    "platform": str(order.get("platform") or ""),
                    "external_order_id": str(
                        order.get("external_order_id") or ""
                    ),
                    "pickup_code": pickup_code,
                    "links": [
                        {
                            "store_id": int(link.get("store_id") or 0),
                            "store_name": stores.get(
                                int(link.get("store_id") or 0),
                                "",
                            ),
                            "order_id": str(link.get("order_id") or ""),
                        }
                        for link in links
                    ],
                    "sent": sent,
                }
            )
        return {"messages": rows}

    def _mark_pickup_messages_sent(self, payload: dict[str, Any]) -> dict[str, Any]:
        links = payload.get("links")
        if not isinstance(links, list):
            raise ValueError("links 格式无效")
        updated = 0
        for link in links:
            if not isinstance(link, dict):
                continue
            try:
                store_id = int(link.get("store_id"))
            except (TypeError, ValueError):
                continue
            order_id = str(link.get("order_id") or "").strip()
            if not order_id:
                continue
            self.db.update_order_workbench(
                store_id,
                order_id,
                is_marked_sent=1,
            )
            updated += 1
        return {"updated": updated}

    def _list_order_task_logs(self, payload: dict[str, Any]) -> dict[str, Any]:
        limit = max(1, min(int(payload.get("limit") or 300), 1000))
        return {"logs": self.db.list_task_logs(limit=limit)}

    def _import_order_workbench(self, payload: dict[str, Any]) -> dict[str, Any]:
        records = payload.get("records")
        if not isinstance(records, list):
            raise ValueError("records 格式无效")
        if (
            any(
                isinstance(record, dict) and "purchase_order_no" in record
                for record in records
            )
            and not self._procurement_association_enabled()
        ):
            raise PermissionError("此功能正在加急开发优化中")
        selected_store = payload.get("store_id")
        imported = 0
        missing = []
        allowed = {
            "purchase_order_no",
            "pickup_code",
            "note",
            "is_marked_sent",
        }
        for record in records:
            if not isinstance(record, dict):
                continue
            order_id = str(record.get("order_id") or "").strip()
            if not order_id:
                continue
            candidates = self.db.list_orders(
                store_id=(
                    int(selected_store)
                    if selected_store not in (None, "")
                    else None
                ),
                search=order_id,
                limit=50,
            )
            matches = [
                row
                for row in candidates
                if str(row.get("order_id") or "") == order_id
            ]
            if not matches:
                missing.append(order_id)
                continue
            changes = {
                key: record[key]
                for key in allowed
                if key in record
            }
            for row in matches:
                self.db.update_order_workbench(
                    int(row.get("store_id") or 0),
                    order_id,
                    **changes,
                )
                imported += 1
        return {"imported": imported, "missing": missing}

    def _parse_order_import_file(self, payload: dict[str, Any]) -> dict[str, Any]:
        file_name = str(payload.get("file_name") or "").strip()
        content_base64 = str(payload.get("content_base64") or "")
        if not content_base64:
            raise ValueError("导入文件不能为空")
        try:
            content = base64.b64decode(content_base64, validate=True)
        except Exception as exc:
            raise ValueError("导入文件内容无效") from exc
        if len(content) > 1_500_000:
            raise ValueError("导入文件不能超过 1.5 MB")
        suffix = Path(file_name).suffix.lower()
        rows: list[dict[str, str]] = []
        if suffix == ".csv":
            text = content.decode("utf-8-sig", errors="replace")
            reader = csv.DictReader(io.StringIO(text))
            rows = [
                {
                    str(key or "").strip(): str(value or "").strip()
                    for key, value in row.items()
                }
                for row in reader
            ]
        elif suffix == ".xlsx":
            try:
                from openpyxl import load_workbook
            except ImportError as exc:
                raise ValueError("当前环境缺少 Excel 读取组件") from exc
            workbook = load_workbook(
                io.BytesIO(content),
                read_only=True,
                data_only=True,
            )
            try:
                sheet = workbook.active
                values = list(sheet.iter_rows(values_only=True))
            finally:
                workbook.close()
            if values:
                headers = [str(value or "").strip() for value in values[0]]
                rows = [
                    {
                        headers[index]: str(value or "").strip()
                        for index, value in enumerate(row)
                        if index < len(headers) and headers[index]
                    }
                    for row in values[1:]
                    if any(value not in (None, "") for value in row)
                ]
        else:
            raise ValueError("仅支持 .xlsx 和 .csv 文件")
        return {"file_name": file_name, "records": rows}

    @staticmethod
    def _profit_fee_value(raw: Any, names: tuple[str, ...]) -> int:
        if isinstance(raw, dict):
            for name in names:
                if name in raw and raw[name] not in (None, ""):
                    try:
                        return int(raw[name])
                    except (TypeError, ValueError):
                        pass
            for nested in raw.values():
                value = NextUiController._profit_fee_value(nested, names)
                if value:
                    return value
        elif isinstance(raw, list):
            for nested in raw:
                value = NextUiController._profit_fee_value(nested, names)
                if value:
                    return value
        return 0

    @staticmethod
    def _raw_first_value(raw: Any, names: tuple[str, ...]) -> str:
        if isinstance(raw, dict):
            for name in names:
                if name in raw and raw[name] not in (None, ""):
                    return str(raw[name])
            for nested in raw.values():
                value = NextUiController._raw_first_value(nested, names)
                if value:
                    return value
        elif isinstance(raw, list):
            for nested in raw:
                value = NextUiController._raw_first_value(nested, names)
                if value:
                    return value
        return ""

    @staticmethod
    def _profit_product_values(
        raw: dict[str, Any],
        order: dict[str, Any],
    ) -> tuple[list[str], int, str]:
        detail = raw.get("order_detail")
        products = detail.get("product_infos") if isinstance(detail, dict) else []
        values: list[str] = []
        count = 0
        title = ""
        if isinstance(products, list):
            for item in products:
                if not isinstance(item, dict):
                    continue
                product_id = str(item.get("product_id") or "").strip()
                if product_id and product_id not in values:
                    values.append(product_id)
                try:
                    count += int(item.get("sku_cnt") or 0)
                except (TypeError, ValueError):
                    pass
                if not title:
                    title = str(
                        item.get("title")
                        or item.get("product_name")
                        or item.get("sku_name")
                        or ""
                    )
        fallback_id = str(order.get("item_product_id") or "").strip()
        if not values and fallback_id:
            values.append(fallback_id)
        return values, count or 1, title or str(order.get("item_summary") or "")

    @staticmethod
    def _profit_period(timestamp: int, period: str) -> str:
        if not timestamp:
            return "暂无数据"
        value = datetime.fromtimestamp(timestamp)
        if period == "日报表":
            return value.strftime("%Y-%m-%d")
        if period == "周报表":
            year, week, _ = value.isocalendar()
            return f"{year}-W{week:02d}"
        if period == "月报表":
            return value.strftime("%Y-%m")
        if period == "年报表":
            return value.strftime("%Y")
        return "全部"

    def _profit_report(self, payload: dict[str, Any]) -> dict[str, Any]:
        now = datetime.now()
        start_text = str(payload.get("start_date") or (now.date() - timedelta(days=30)).isoformat())
        end_text = str(payload.get("end_date") or now.date().isoformat())
        try:
            start = datetime.strptime(start_text, "%Y-%m-%d")
            end = datetime.strptime(end_text, "%Y-%m-%d")
        except ValueError as exc:
            raise ValueError("成交时间格式必须为 YYYY-MM-DD") from exc
        start_time = int(start.timestamp())
        end_time = int(end.timestamp()) + 86399
        if end_time < start_time:
            raise ValueError("结束日期不能早于开始日期")

        view = str(payload.get("view") or "利润统计")
        period = str(payload.get("period") or "订单报表")
        selected_platform = str(payload.get("platform") or "全部平台")
        selected_store = int(payload["store_id"]) if payload.get("store_id") not in (None, "") else None
        selected_group = str(payload.get("group_name") or "全部分组")
        selected_account = str(payload.get("account_label") or "全部采购账号")
        service_term = str(payload.get("service_name") or "").strip().casefold()
        order_term = str(payload.get("order_no") or "").strip().casefold()
        product_term = str(payload.get("product_id") or "").strip().casefold()
        source = str(payload.get("source") or "全部数据")
        status_filter = str(payload.get("status") or "全部状态")

        stores = list(self.db.list_stores())
        store_lookup = {int(store.id): store for store in stores}
        procurement_orders = {
            int(row.get("id") or 0): row
            for row in self.db.list_procurement_orders()
        }
        links_by_order: dict[tuple[int, str], list[dict[str, Any]]] = {}
        platforms = {"微信小店"}
        accounts = set()
        for link in self.db.list_procurement_links():
            procurement = procurement_orders.get(
                int(link.get("procurement_order_id") or 0),
                {},
            )
            enriched = {**link, **procurement}
            key = (int(link.get("store_id") or 0), str(link.get("order_id") or ""))
            links_by_order.setdefault(key, []).append(enriched)
            if procurement.get("platform"):
                platforms.add(str(procurement["platform"]))
            if procurement.get("account_label"):
                accounts.add(str(procurement["account_label"]))
        workbench = self.db.get_order_workbench()
        orders: list[dict[str, Any]] = []
        for order in self.db.list_orders(limit=100_000):
            if is_unclaimed_gift_order(order) or not order_counts_as_transaction(order.get("status")):
                continue
            created = int(order.get("create_time") or 0)
            if created and not start_time <= created <= end_time:
                continue
            store_id = int(order.get("store_id") or 0)
            store = store_lookup.get(store_id)
            if selected_store is not None and store_id != selected_store:
                continue
            if selected_group != "全部分组" and (
                store is None or str(store.group_name or "") != selected_group
            ):
                continue
            try:
                raw = json.loads(str(order.get("raw_json") or "{}"))
            except (TypeError, ValueError, json.JSONDecodeError):
                raw = {}
            raw = raw if isinstance(raw, dict) else {}
            key = (store_id, str(order.get("order_id") or ""))
            purchase_rows = links_by_order.get(key, [])
            if source == "已采购" and not purchase_rows:
                continue
            if source == "未采购" and purchase_rows:
                continue
            order_accounts = {
                str(item.get("account_label") or "").strip()
                for item in purchase_rows
                if str(item.get("account_label") or "").strip()
            }
            if selected_account != "全部采购账号" and selected_account not in order_accounts:
                continue
            purchase_platforms = {
                str(item.get("platform") or "").strip()
                for item in purchase_rows
                if str(item.get("platform") or "").strip()
            }
            if selected_platform not in {"全部平台", "微信小店"} and selected_platform not in purchase_platforms:
                continue
            product_ids, item_count, product_title = self._profit_product_values(raw, order)
            if product_term and not any(product_term in value.casefold() for value in product_ids):
                continue
            status_text = {
                17: "待核销",
                20: "买家已付款",
                21: "卖家已发货",
                30: "待收货",
                100: "交易成功",
                200: "售后完成",
            }.get(int(order.get("status") or 0), str(order.get("status_text") or ""))
            if status_filter != "全部状态" and status_text != status_filter:
                continue
            if order_term and order_term not in str(order.get("order_id") or "").casefold():
                continue
            workbench_row = workbench.get(key, {})
            service_text = " ".join(
                (
                    str(workbench_row.get("note") or ""),
                    str(order.get("merchant_notes") or ""),
                )
            ).casefold()
            if service_term and service_term not in service_text:
                continue
            sale = int(order.get("total_amount") or 0)
            purchase_cost = sum(
                int(item.get("pay_amount") or 0)
                for item in purchase_rows
            )
            if purchase_cost and int(workbench_row.get("cost") or 0):
                purchase_cost = int(workbench_row.get("cost") or 0)
            commission = self._profit_fee_value(
                raw,
                ("commission", "commission_fee", "commission_amount"),
            )
            platform_fee = self._profit_fee_value(
                raw,
                (
                    "platform_fee",
                    "platform_service_fee",
                    "tech_service_fee",
                    "predict_commission_fee",
                ),
            )
            explicit_profit = int(workbench_row.get("profit") or 0)
            pending = (
                not explicit_profit
                and not int(workbench_row.get("cost") or 0)
                and not purchase_cost
            )
            profit = (
                explicit_profit
                if explicit_profit
                else sale - purchase_cost - commission - platform_fee
            )
            margin = profit / sale * 100 if sale and not pending else None
            purchase_ids = " / ".join(
                str(item.get("id") or "") for item in purchase_rows if item.get("id")
            )
            purchase_order_numbers = " / ".join(
                str(item.get("external_order_id") or "")
                for item in purchase_rows
                if item.get("external_order_id")
            )
            purchase_count = sum(
                int(item.get("goods_count") or 0)
                for item in purchase_rows
            )
            purchase_time = " / ".join(
                str(item.get("fetched_at") or item.get("updated_at") or "")
                for item in purchase_rows
                if item.get("fetched_at") or item.get("updated_at")
            )
            purchase_status = " / ".join(
                str(item.get("status_text") or item.get("status") or "")
                for item in purchase_rows
                if item.get("status_text") or item.get("status")
            )
            raw_sub_order = self._raw_first_value(
                raw,
                ("sub_order_id", "sub_order_no", "subOrderId", "subOrderNo"),
            )
            orders.append(
                {
                    "store_id": store_id,
                    "store_name": store.name if store is not None else str(order.get("store_name") or ""),
                    "order_id": str(order.get("order_id") or ""),
                    "create_time": created,
                    "product_ids": product_ids,
                    "product_title": product_title,
                    "item_count": item_count,
                    "sub_order_id": raw_sub_order,
                    "status_text": status_text,
                    "buyer_name": str(order.get("buyer_name") or ""),
                    "sale_amount": sale,
                    "commission": commission,
                    "platform_fee": platform_fee,
                    "purchase_order_no": purchase_order_numbers,
                    "purchase_time": purchase_time,
                    "purchase_id": purchase_ids,
                    "purchase_count": purchase_count,
                    "purchase_status": purchase_status,
                    "purchase_account": " / ".join(sorted(order_accounts)),
                    "purchase_price": purchase_cost,
                    "profit": profit,
                    "margin": margin,
                    "pending": pending,
                    "period": self._profit_period(created, period),
                }
            )

        if view in {"店铺统计", "商品统计"}:
            aggregate: dict[tuple[str, str], dict[str, Any]] = {}
            for row in orders:
                label = (
                    row["store_name"]
                    if view == "店铺统计"
                    else ", ".join(row["product_ids"]) or "未知商品"
                )
                key = (row["period"], label)
                group = aggregate.setdefault(
                    key,
                    {
                        "period": row["period"],
                        "label": label,
                        "orders": 0,
                        "item_count": 0,
                        "sale_amount": 0,
                        "cost": 0,
                        "commission": 0,
                        "platform_fee": 0,
                        "profit": 0,
                        "pending": 0,
                    },
                )
                group["orders"] += 1
                group["item_count"] += int(row["item_count"])
                group["sale_amount"] += int(row["sale_amount"])
                group["commission"] += int(row["commission"])
                group["platform_fee"] += int(row["platform_fee"])
                if row["pending"]:
                    group["pending"] += 1
                else:
                    group["cost"] += int(row["purchase_price"])
                    group["profit"] += int(row["profit"])
            aggregate_rows = sorted(aggregate.values(), key=lambda item: (item["period"], item["label"]))
            for row in aggregate_rows:
                row["margin"] = (
                    row["profit"] / row["sale_amount"] * 100
                    if row["sale_amount"] and not row["pending"]
                    else None
                )
        else:
            aggregate_rows = []
        verified = [row for row in orders if not row["pending"]]
        summary = {
            "orders": len(orders),
            "sale": sum(int(row["sale_amount"]) for row in orders),
            "cost": sum(int(row["purchase_price"]) for row in verified),
            "commission": sum(int(row["commission"]) for row in orders),
            "platform_fee": sum(int(row["platform_fee"]) for row in orders),
            "profit": sum(int(row["profit"]) for row in verified),
            "pending": len(orders) - len(verified),
        }
        summary["margin"] = (
            summary["profit"] / summary["sale"] * 100
            if summary["sale"] and summary["orders"] > summary["pending"]
            else None
        )
        groups = sorted({str(store.group_name) for store in stores if store.group_name})
        return {
            "view": view,
            "period": period,
            "start_date": start_text,
            "end_date": end_text,
            "orders": orders,
            "aggregate": aggregate_rows,
            "summary": summary,
            "choices": {
                "platforms": sorted(platforms),
                "stores": [
                    {"id": int(store.id), "name": store.name}
                    for store in stores
                ],
                "groups": groups,
                "accounts": sorted(accounts),
                "statuses": [
                    "全部状态",
                    "买家已付款",
                    "卖家已发货",
                    "待收货",
                    "交易成功",
                    "售后完成",
                    "待核销",
                ],
            },
        }

    def _record_profit_download(self, payload: dict[str, Any]) -> dict[str, Any]:
        view = str(payload.get("view") or "利润统计")
        period = str(payload.get("period") or "订单报表")
        count = max(0, int(payload.get("count") or 0))
        log_id = self.db.start_task_log(
            "profit",
            "利润统计下载记录",
            f"{view}/{period}，导出 {count} 条",
        )
        self.db.finish_task_log(
            log_id,
            "success" if count else "partial",
            f"{view}/{period} 导出 {count} 条" if count else "没有可导出的利润记录",
        )
        return {"logged": True, "count": count}

    @staticmethod
    def _public_service_result(result: Any) -> dict[str, Any]:
        if isinstance(result, dict):
            return dict(result)
        if hasattr(result, "__dataclass_fields__"):
            return asdict(result)
        if hasattr(result, "__dict__"):
            return dict(vars(result))
        return {"message": str(result)}

    @staticmethod
    def _current_order_ids_by_store(
        payload: dict[str, Any],
    ) -> dict[int, list[str]]:
        raw = payload.get("current_order_ids")
        result: dict[int, list[str]] = {}
        if isinstance(raw, dict):
            for store_id, values in raw.items():
                if not str(store_id).strip().isdigit():
                    continue
                if not isinstance(values, list):
                    continue
                ids = [
                    str(value).strip()
                    for value in values
                    if str(value).strip()
                ]
                if ids:
                    result[int(store_id)] = list(dict.fromkeys(ids))
            return result
        if isinstance(raw, list):
            for row in raw:
                if not isinstance(row, dict):
                    continue
                try:
                    store_id = int(row.get("store_id"))
                except (TypeError, ValueError):
                    continue
                order_id = str(row.get("order_id") or "").strip()
                if order_id:
                    result.setdefault(store_id, []).append(order_id)
            return {
                store_id: list(dict.fromkeys(ids))
                for store_id, ids in result.items()
            }
        return result

    def _sync_store_orders_recent(
        self,
        service: Any,
        store: Any,
        *,
        start_time: int,
        end_time: int,
        current_order_ids: list[str],
    ) -> tuple[Any, list[dict[str, Any]]]:
        before = {
            str(row.get("order_id") or ""): int(row.get("status") or 0)
            for row in self.db.list_orders(
                store_id=store.id,
                limit=100_000,
            )
        }
        refresh = getattr(service, "refresh_orders_recent", None)
        if callable(refresh):
            result = refresh(
                store,
                start_time,
                end_time,
                current_order_ids,
            )
        else:
            result = service.sync_orders(
                store,
                start_time,
                end_time,
            )
        after = {
            str(row.get("order_id") or ""): int(row.get("status") or 0)
            for row in self.db.list_orders(
                store_id=store.id,
                limit=100_000,
            )
        }
        changed_ids = [
            order_id
            for order_id in before
            if order_id in after and before[order_id] != after[order_id]
        ]
        changes = [
            {
                "store_id": store.id,
                "store_name": store.name,
                "order_id": order_id,
                "before_status": before[order_id],
                "after_status": after[order_id],
                "before_status_text": order_status_text(before[order_id]),
                "after_status_text": order_status_text(after[order_id]),
            }
            for order_id in changed_ids
        ]
        return result, changes

    def _logistics_monitor(self, payload: dict[str, Any]) -> dict[str, Any]:
        return logistics_monitor_payload(
            self.db,
            payload,
            now=self._local_now(),
        )

    def _sync_orders(self, payload: dict[str, Any]) -> dict[str, Any]:
        service = self._require_product_service()
        store_id = payload.get("store_id")
        stores = (
            [self._require_store(int(store_id))]
            if store_id not in (None, "")
            else self.db.list_stores()
        )
        now = datetime.now()
        end_time = int(now.timestamp())
        start_time = int(now.timestamp()) - 48 * 3600
        current_ids = self._current_order_ids_by_store(payload)

        def sync(store):
            return self._sync_store_orders_recent(
                service,
                store,
                start_time=start_time,
                end_time=end_time,
                current_order_ids=current_ids.get(int(store.id), []),
            )

        errors: list[str] = []
        succeeded = 0
        updated = 0
        status_changes: list[dict[str, Any]] = []
        with ThreadPoolExecutor(
            max_workers=max(1, min(4, len(stores))),
            thread_name_prefix="webui-order-sync",
        ) as executor:
            futures = {executor.submit(sync, store): store for store in stores}
            for future in as_completed(futures):
                store = futures[future]
                try:
                    result, changes = future.result()
                    updated += int(
                        result.get("count", 0)
                        if isinstance(result, dict)
                        else getattr(result, "count", 0)
                        or 0
                    )
                    status_changes.extend(changes)
                except Exception as exc:
                    errors.append(f"{store.name}: {exc}")
                    self._write_operation_log(
                        "orders",
                        "同步订单",
                        f"订单刷新失败：{store.name}",
                        store_id=store.id,
                        status="failed",
                        detail={"error": str(exc)},
                    )
                else:
                    succeeded += 1
                    self._write_operation_log(
                        "orders",
                        "同步订单",
                        f"订单刷新成功：{store.name}",
                        store_id=store.id,
                    )
        return {
            "synced": len(stores),
            "succeeded": succeeded,
            "updated": updated,
            "status_changes": status_changes,
            "errors": errors,
            "orders": self._list_orders({"limit": 1000})["orders"],
        }

    def _refresh_one_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        service = self._require_product_service()
        store = self._require_store(self._store_id(payload))
        order_id = str(payload.get("order_id") or "").strip()
        if not order_id:
            raise ValueError("order_id 不能为空")
        detail = service.refresh_order_official(store, order_id)
        order = next(
            (
                row
                for row in self._list_orders(
                    {
                        "store_id": store.id,
                        "search": order_id,
                        "limit": 1000,
                    }
                )["orders"]
                if str(row.get("order_id") or "") == order_id
            ),
            None,
        )
        self._write_operation_log(
            "orders",
            "刷新单个订单",
            f"订单官方详情已刷新：{store.name} · {order_id}",
            store_id=store.id,
            detail={
                "order_id": order_id,
                "merchant_notes_tag_color": detail.get(
                    "merchant_notes_tag_color"
                ),
                "package_count": len(detail.get("packages") or []),
            },
        )
        return {
            "store_id": store.id,
            "store_name": store.name,
            "order_id": order_id,
            "order": order,
            "platform_detail": detail,
        }

    def _order_logistics_track(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        service = self._require_product_service()
        store = self._require_store(self._store_id(payload))
        order_id = str(payload.get("order_id") or "").strip()
        waybill_id = str(payload.get("waybill_id") or "").strip()
        if not order_id or not waybill_id:
            raise ValueError("order_id 和 waybill_id 不能为空")
        detail = service.official_order_detail(store, order_id)
        packages = detail.get("packages") or []
        package = next(
            (
                row
                for row in packages
                if str(row.get("waybill_id") or "").strip()
                == waybill_id
            ),
            None,
        )
        if package is None:
            available = [
                str(row.get("waybill_id") or "")
                for row in packages
                if str(row.get("waybill_id") or "").strip()
            ]
            raise ValueError(
                f"官方订单中未找到运单 {waybill_id}"
                + (
                    "；官方包裹：" + "、".join(available)
                    if available
                    else "；官方未返回任何包裹"
                )
            )
        traces = package.get("traces") or []
        if package.get("error"):
            raise RuntimeError(
                f"官方物流查询失败：{package.get('error')}"
            )
        self._write_operation_log(
            "orders",
            "查询官方物流",
            (
                f"官方物流轨迹读取：{store.name} · "
                f"{order_id} · {waybill_id} · {len(traces)} 条"
            ),
            store_id=store.id,
            detail={
                "order_id": order_id,
                "waybill_id": waybill_id,
                "trace_count": len(traces),
                "source": detail.get("source"),
            },
        )
        return {
            "store_id": store.id,
            "store_name": store.name,
            "order_id": order_id,
            "waybill_id": waybill_id,
            "package": package,
            "packages": packages,
            "trace_count": len(traces),
            "source": "微信小店商家后台订单详情 expressData",
            "checked_at": detail.get("checked_at") or "",
        }

    def _start_order_quick_refresh(self, payload: dict[str, Any]) -> dict[str, Any]:
        service = self._require_product_service()
        store_id = payload.get("store_id")
        stores = (
            [self._require_store(int(store_id))]
            if store_id not in (None, "")
            else [store for store in self.db.list_stores() if store.app_id]
        )
        if not stores:
            raise ValueError("没有可联网刷新的店铺")
        now = datetime.now()
        end_time = int(now.timestamp())
        start_time = end_time - 48 * 3600
        current_ids = self._current_order_ids_by_store(payload)
        job_id = uuid.uuid4().hex
        generation = self._current_generation()
        owner = self._job_owner_fields()
        with self._order_jobs_lock:
            self._order_jobs[job_id] = {
                "job_id": job_id,
                **owner,
                "status": "starting",
                "current": 0,
                "total": len(stores),
                "succeeded": 0,
                "failed": 0,
                "successful_store_ids": [],
                "failed_store_ids": [],
                "updated": 0,
                "status_changes": [],
                "message": "开始快速刷新最近订单...",
                "errors": [],
                "orders": [],
            }

        def update(**changes: Any) -> None:
            with self._order_jobs_lock:
                if not self._job_update_allowed(generation):
                    return
                job = self._order_jobs.get(job_id)
                if job is not None:
                    job.update(changes)

        def worker() -> None:
            update(status="running", message=f"正在刷新最近订单（0/{len(stores)}）")
            completed = 0
            succeeded = 0
            successful_store_ids: list[int] = []
            failed_store_ids: list[int] = []
            updated = 0
            errors: list[str] = []
            status_changes: list[dict[str, Any]] = []
            workers = max(1, min(8, len(stores)))
            with ThreadPoolExecutor(
                max_workers=workers,
                thread_name_prefix="webui-order-quick-refresh",
            ) as executor:
                futures = {
                    executor.submit(
                        self._sync_store_orders_recent,
                        service,
                        store,
                        start_time=start_time,
                        end_time=end_time,
                        current_order_ids=current_ids.get(
                            int(store.id),
                            [],
                        ),
                    ): store
                    for store in stores
                }
                for future in as_completed(futures):
                    if not self._job_update_allowed(generation):
                        break
                    store = futures[future]
                    completed += 1
                    try:
                        result, changes = future.result()
                        count = int(
                            result.get("count", 0)
                            if isinstance(result, dict)
                            else getattr(result, "count", 0)
                            or 0
                        )
                    except Exception as exc:
                        errors.append(f"{store.name}: {exc}")
                        failed_store_ids.append(store.id)
                        update(
                            current=completed,
                            succeeded=succeeded,
                            failed=len(errors),
                            successful_store_ids=successful_store_ids,
                            failed_store_ids=failed_store_ids,
                            updated=updated,
                            message=(
                                f"正在刷新  {store.name}  "
                                f"({completed}/{len(stores)})"
                            ),
                        )
                    else:
                        succeeded += 1
                        successful_store_ids.append(store.id)
                        updated += count
                        status_changes.extend(changes)
                        update(
                            current=completed,
                            succeeded=succeeded,
                            failed=len(errors),
                            successful_store_ids=successful_store_ids,
                            failed_store_ids=failed_store_ids,
                            updated=updated,
                            status_changes=status_changes,
                            message=(
                                f"正在刷新  {store.name}  "
                                f"({completed}/{len(stores)})"
                            ),
                        )
            if not self._job_update_allowed(generation):
                return
            orders = self._list_orders({"limit": 2000})["orders"]
            update(
                status="completed" if not errors else "partial",
                current=len(stores),
                total=len(stores),
                succeeded=succeeded,
                failed=len(errors),
                successful_store_ids=successful_store_ids,
                failed_store_ids=failed_store_ids,
                updated=updated,
                status_changes=status_changes,
                message=(
                    f"快速刷新完成：成功 {succeeded}/{len(stores)}，"
                    f"读取 {updated} 条订单"
                    + (f"，失败 {len(errors)} 家" if errors else "")
                    + (f"；{errors[0]}" if errors else "")
                ),
                errors=errors,
                orders=orders,
            )

        threading.Thread(
            target=worker,
            name=f"webui-order-quick-refresh-{job_id[:8]}",
            daemon=True,
        ).start()
        return {"job_id": job_id}

    def _order_quick_refresh_status(self, payload: dict[str, Any]) -> dict[str, Any]:
        job_id = str(payload.get("job_id") or "")
        with self._order_jobs_lock:
            job = self._order_jobs.get(job_id)
            if job is None:
                raise ValueError("订单快速刷新任务不存在")
            return dict(job)

    def _read_string_set(self, key: str) -> set[str]:
        try:
            values = json.loads(self.db.get_setting(key, "[]"))
        except (TypeError, ValueError, json.JSONDecodeError):
            return set()
        if not isinstance(values, list):
            return set()
        return {str(value) for value in values if str(value)}

    def _read_dict_setting(self, key: str) -> dict[str, Any]:
        try:
            value = json.loads(self.db.get_setting(key, "{}"))
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    def _mark_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        order_id = str(payload.get("order_id") or "").strip()
        if not order_id:
            raise ValueError("order_id 不能为空")
        marked = self._read_string_set(ORDER_MARKS_SETTING)
        if bool(payload.get("marked")):
            marked.add(order_id)
        else:
            marked.discard(order_id)
        self.db.set_setting(
            ORDER_MARKS_SETTING,
            json.dumps(sorted(marked), ensure_ascii=False),
        )
        return {"order_id": order_id, "is_marked": order_id in marked}

    def _update_order_note(self, payload: dict[str, Any]) -> dict[str, Any]:
        service = self._require_product_service()
        store = self._require_store(self._store_id(payload))
        order_id = str(payload.get("order_id") or "").strip()
        if not order_id:
            raise ValueError("order_id 不能为空")
        notes = str(payload.get("notes") or "")
        with self._allow_platform_write(store, "order_notes_update"):
            result = service.update_order_notes(
                store,
                order_id,
                notes,
                int(payload.get("color") or 0),
            )
        return {"order_id": order_id, "notes": notes, "result": result}

    def _update_order_note_state(self, payload: dict[str, Any]) -> dict[str, Any]:
        store = self._require_store(self._store_id(payload))
        order_id = str(payload.get("order_id") or "").strip()
        if not order_id:
            raise ValueError("order_id 不能为空")
        scope = str(payload.get("scope") or "软件备注")
        if scope not in {"软件备注", "店铺备注"}:
            raise ValueError("备注类型无效")
        note = str(payload.get("note") or "")
        flag = str(payload.get("flag") or "").strip()
        if flag == "灰":
            # Old local data used 灰 for "no flag"; it is not an official code.
            flag = ""
        if flag not in {"", *OFFICIAL_NOTE_COLOR_VALUES}:
            raise ValueError("旗帜颜色不在微信小店允许范围内")
        sync_store = bool(payload.get("sync_store"))

        software_notes = self._read_dict_setting(ORDER_SOFTWARE_NOTES_SETTING)
        flags = self._read_dict_setting(ORDER_FLAGS_SETTING)
        state_key = self._order_state_key(store.id, order_id)
        write_store = scope == "店铺备注" or sync_store
        official: dict[str, Any] = {}
        if write_store:
            service = self._require_product_service()
            try:
                with self._allow_platform_write(store, "order_notes_update"):
                    official = service.update_order_notes(
                        store,
                        order_id,
                        note,
                        OFFICIAL_NOTE_COLOR_VALUES.get(flag, 0),
                    )
            except Exception as exc:
                self._write_operation_log(
                    "orders",
                    "保存店铺备注",
                    f"店铺备注保存失败：{store.name} · {order_id}",
                    store_id=store.id,
                    status="failed",
                    detail={"order_id": order_id, "error": str(exc)},
                )
                raise
            stored_note = str(official.get("merchant_notes") or "")
            try:
                stored_color = int(
                    official.get("merchant_notes_tag_color") or 0
                )
            except (TypeError, ValueError):
                stored_color = 0
            stored_flag = OFFICIAL_NOTE_COLOR_NAMES.get(stored_color, "")
            note = stored_note
            flag = stored_flag

        if scope == "软件备注":
            software_notes[state_key] = note
        elif sync_store:
            software_notes[state_key] = note
        software_notes.pop(order_id, None)
        if flag:
            flags[state_key] = flag
        else:
            flags.pop(state_key, None)
        flags.pop(order_id, None)
        self.db.set_settings(
            {
                ORDER_SOFTWARE_NOTES_SETTING: json.dumps(
                    software_notes,
                    ensure_ascii=False,
                ),
                ORDER_FLAGS_SETTING: json.dumps(flags, ensure_ascii=False),
            }
        )
        self._write_operation_log(
            "orders",
            "保存店铺备注" if write_store else "保存软件备注",
            (
                f"店铺备注保存成功：{store.name} · {order_id}"
                if write_store
                else f"软件备注保存成功：{store.name} · {order_id}"
            ),
            store_id=store.id,
            detail={
                "order_id": order_id,
                "scope": scope,
                "synced_to_store": write_store,
            },
        )
        return {
            "order_id": order_id,
            "scope": scope,
            "software_note": str(software_notes.get(state_key) or ""),
            "store_note": note if write_store else "",
            "merchant_notes": note if write_store else "",
            "merchant_notes_tag_color": (
                OFFICIAL_NOTE_COLOR_VALUES.get(flag, 0)
                if write_store
                else None
            ),
            "flag": str(flags.get(state_key) or ""),
            "synced_to_store": write_store,
            "verified": bool(write_store and official),
        }

    def _decrypt_order_address(self, payload: dict[str, Any]) -> dict[str, Any]:
        service = self._require_product_service()
        store = self._require_store(self._store_id(payload))
        order_id = str(payload.get("order_id") or "").strip()
        if not order_id:
            raise ValueError("order_id 不能为空")
        result = service.decode_order_sensitive(store, order_id)
        order = self.db.get_order(store.id, order_id)
        address, _source = self._order_procurement_address(
            store.id,
            order_id,
            order,
        )
        cached = None
        if address_is_complete(address):
            cached = self.db.upsert_order_address_cache(
                store.id,
                order_id,
                address,
                source="decrypted_official",
                source_updated_at=datetime.now().isoformat(
                    timespec="seconds"
                ),
            )
        return {
            "order_id": order_id,
            "result": result,
            "address": (
                cached.get("address") if cached else address
            ),
            "address_ready": bool(
                cached
                and address_is_complete(cached.get("address"))
            ),
        }

    def _get_order_filters(self, _payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "hidden_product_ids": sorted(
                self._read_string_set(ORDER_HIDDEN_PRODUCTS_SETTING)
            )
        }

    def _save_order_filters(self, payload: dict[str, Any]) -> dict[str, Any]:
        values = {
            str(value).strip()
            for value in payload.get("hidden_product_ids", [])
            if str(value).strip()
        }
        self.db.set_setting(
            ORDER_HIDDEN_PRODUCTS_SETTING,
            json.dumps(sorted(values), ensure_ascii=False),
        )
        return {"hidden_product_ids": sorted(values)}

    def _get_order_settings(self, _payload: dict[str, Any]) -> dict[str, Any]:
        try:
            settings = json.loads(self.db.get_setting("webui_order_settings", "{}"))
        except (TypeError, ValueError, json.JSONDecodeError):
            settings = {}
        return {"settings": settings if isinstance(settings, dict) else {}}

    def _save_order_settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        incoming = payload.get("settings")
        if not isinstance(incoming, dict):
            raise ValueError("settings 必须是对象")
        current = self._get_order_settings({})["settings"]
        current.update(incoming)
        self.db.set_setting(
            "webui_order_settings",
            json.dumps(current, ensure_ascii=False),
        )
        return {"settings": current}

    def _list_products(self, payload: dict[str, Any]) -> dict[str, Any]:
        store_id = payload.get("store_id")
        if store_id in (None, ""):
            raise ValueError("store_id 不能为空；全部店铺请显式传入 __all__")
        if store_id == ALL_STORES_KEY:
            store_filter = None
        else:
            store_filter = self._require_store(self._store_id(payload)).id
        status = payload.get("status")
        rows = self.db.list_products(
            store_id=store_filter,
            status=int(status) if status not in (None, "") else None,
            search=str(payload.get("search") or ""),
        )
        store_ids = {
            int(row.get("store_id") or 0)
            for row in rows
            if int(row.get("store_id") or 0) > 0
        }
        metrics_by_store = {
            store_id: self.db.get_product_metrics_map(store_id)
            for store_id in store_ids
        }
        products = []
        for row in rows:
            sales_count = None
            review_count = None
            try:
                raw_product = json.loads(str(row.get("raw_json") or "{}"))
                if isinstance(raw_product, dict):
                    raw_sales = raw_product.get("total_sold_num")
                    if raw_sales not in (None, ""):
                        sales_count = int(raw_sales)
            except (TypeError, ValueError, json.JSONDecodeError):
                pass
            store_id = int(row.get("store_id") or 0)
            metrics = metrics_by_store.get(store_id, {}).get(
                str(row.get("product_id") or ""),
                {},
            )
            products.append(
                {
                    "store_id": store_id,
                    "store_name": str(row.get("store_name") or ""),
                    "product_id": str(row.get("product_id") or ""),
                    "title": str(row.get("title") or ""),
                    "status": int(row.get("status") or 0),
                    "status_text": str(row.get("status_text") or ""),
                    "edit_status": int(row.get("edit_status") or 0),
                    "min_price": int(row.get("min_price") or 0),
                    "sku_count": int(row.get("sku_count") or 0),
                    "stock_total": int(row.get("stock_total") or 0),
                    "sales_count": sales_count,
                    "total_sales": sales_count,
                    "sold30": metrics.get("sold30"),
                    "views30": metrics.get("views30"),
                    "recommend_views30": metrics.get("recommend_views30"),
                    "review_count": review_count,
                    "metrics_updated_at": str(
                        metrics.get("updated_at") or ""
                    ),
                    "spu_code": str(row.get("spu_code") or ""),
                    "out_product_id": str(row.get("out_product_id") or ""),
                    "head_img": str(row.get("head_img") or ""),
                    "synced_at": str(row.get("synced_at") or ""),
                }
            )
        return {"products": products}

    def _refresh_products(self, payload: dict[str, Any]) -> dict[str, Any]:
        service = self._require_product_service()
        store = self._require_store(self._store_id(payload))
        tab_key = str(payload.get("tab_key") or "sales")
        with self._product_store_lease(
            [store.id],
            f"manual-refresh:{store.id}",
        ):
            result = service.refresh_product_tab(store, tab_key)
        errors = list(getattr(result, "errors", []) or [])
        if not isinstance(result, dict):
            result_data = asdict(result)
            result_data["store_ids"] = list(getattr(result, "store_ids", []) or [])
        else:
            result_data = dict(result)
        return {
            **self._list_products(
                {
                    "store_id": store.id,
                    "search": str(payload.get("search") or ""),
                }
            ),
            "sync": result_data,
            "succeeded": 0 if result_data.get("cancelled") else 1,
        }

    def _start_product_refresh(self, payload: dict[str, Any]) -> dict[str, Any]:
        service = self._require_product_service()
        store = self._require_store(self._store_id(payload))
        allowed_tabs = {
            "sales",
            "audit",
            "failed",
            "draft",
            "offline",
            "recycle",
            "strategy",
        }
        raw_tabs = payload.get("tab_keys")
        if isinstance(raw_tabs, list):
            tab_keys = [
                str(value)
                for value in raw_tabs
                if str(value) in allowed_tabs
            ]
        else:
            tab_keys = []
        fallback_tab = str(payload.get("tab_key") or "sales")
        if not tab_keys:
            tab_keys = [
                fallback_tab
                if fallback_tab in allowed_tabs
                else "sales"
            ]
        tab_keys = list(dict.fromkeys(tab_keys))
        generation = self._current_generation()
        owner = self._job_owner_fields()
        cancel_event = threading.Event()
        job_id = uuid.uuid4().hex
        lease_id = f"refresh:{job_id}"
        with self._product_jobs_lock:
            for existing in self._product_jobs.values():
                if (
                    existing.get("store_id") == store.id
                    and self._job_is_active(existing)
                ):
                    return {"job_id": existing["job_id"], "already_running": True}
            self._claim_product_store_leases([store.id], lease_id)
            try:
                self._product_jobs[job_id] = {
                    "job_id": job_id,
                    **owner,
                    "kind": "refresh",
                    "action": "refresh",
                    "store_id": store.id,
                    "tab_key": tab_keys[0],
                    "tab_keys": tab_keys,
                    "status": "starting",
                    "current": 0,
                    "total": 0,
                    "message": "正在连接微信小店…",
                    "errors": [],
                    "products": [],
                    "cancel_requested": False,
                    "_cancel_event": cancel_event,
                }
                self._persist_product_task(self._product_jobs[job_id])
            except Exception:
                self._release_product_store_leases([store.id], lease_id)
                raise

        def update(**changes: Any) -> None:
            with self._product_jobs_lock:
                if not self._job_update_allowed(generation):
                    return
                job = self._product_jobs.get(job_id)
                if job is not None:
                    if (
                        job.get("status") == "cancelling"
                        and changes.get("status") == "running"
                    ):
                        changes["status"] = "cancelling"
                    if changes.get("status") in ACCOUNT_TASK_TERMINAL_STATUSES:
                        changes.setdefault(
                            "finished_at",
                            datetime.now().isoformat(timespec="seconds"),
                        )
                    job.update(changes)
                    self._persist_product_task(job)

        def progress(message: str, current: int, total: int) -> None:
            update(
                status="running",
                message=message,
                current=max(0, int(current)),
                total=max(0, int(total)),
            )

        def worker_body() -> None:
            update(status="running", message="正在读取商品目录…")
            try:
                errors: list[str] = []
                tab_messages: list[str] = []
                reported_count = 0
                total_tabs = len(tab_keys)
                for index, tab_key in enumerate(tab_keys, start=1):
                    if not self._job_update_allowed(generation):
                        return
                    if cancel_event.is_set():
                        update(
                            status="cancelled",
                            message="商品刷新已取消，未启动后续页签读取",
                            cancel_requested=True,
                        )
                        return
                    def tab_progress(
                        message: str,
                        _current: int,
                        _total: int,
                        *,
                        tab_index: int = index,
                        tab: str = tab_key,
                    ) -> None:
                        progress(
                            f"{tab}：{message}",
                            tab_index - 1,
                            total_tabs,
                        )

                    result = service.refresh_product_tab(
                        store,
                        tab_key,
                        progress=tab_progress,
                        cancel_event=cancel_event,
                    )
                    if (
                        bool(getattr(result, "cancelled", False))
                        or cancel_event.is_set()
                    ):
                        update(
                            status="cancelled",
                            message="商品刷新已取消；当前页签请求结束后停止",
                            current=index - 1,
                            total=total_tabs,
                            errors=errors,
                            cancel_requested=True,
                        )
                        return
                    errors.extend(
                        str(value)
                        for value in (getattr(result, "errors", []) or [])
                        if str(value).strip()
                    )
                    reported_count += int(
                        getattr(result, "count", 0) or 0
                    )
                    tab_messages.append(
                        str(
                            getattr(result, "message", "")
                            or f"{tab_key} 商品读取完成"
                        )
                    )
                metric_product_ids = [
                    str(row.get("product_id") or "")
                    for row in self.db.list_products(store_id=store.id)
                    if str(row.get("product_id") or "")
                ]
                if not self._job_update_allowed(generation):
                    return
                if cancel_event.is_set():
                    update(
                        status="cancelled",
                        message="商品刷新已取消，未启动经营指标读取",
                        cancel_requested=True,
                    )
                    return
                metric_methods: list[tuple[Any, dict[str, Any]]] = []
                if self.db.get_web_cookies(store.id):
                    private_metrics = getattr(
                        service,
                        "sync_private_product_metrics",
                        None,
                    )
                    if callable(private_metrics):
                        metric_methods.append((private_metrics, {}))
                official_metrics = getattr(
                    service,
                    "sync_product_performance",
                    None,
                )
                if callable(official_metrics):
                    metric_methods.append((official_metrics, {"days": 30}))
                metric_failures: list[str] = []
                if metric_product_ids:
                    for metric_method, metric_kwargs in metric_methods:
                        if cancel_event.is_set():
                            update(
                                status="cancelled",
                                message="商品刷新已取消，未启动后续经营指标读取",
                                cancel_requested=True,
                            )
                            return
                        try:
                            metric_result = metric_method(
                                store,
                                metric_product_ids,
                                cancel_event=cancel_event,
                                **metric_kwargs,
                            )
                        except Exception as exc:
                            metric_failures.append(str(exc))
                            continue
                        if cancel_event.is_set():
                            update(
                                status="cancelled",
                                message="商品刷新已取消，未启动后续经营指标读取",
                                cancel_requested=True,
                            )
                            return
                        tab_messages.append(
                            str(
                                getattr(metric_result, "message", "")
                                or "商品经营指标已更新"
                            )
                        )
                        errors.extend(
                            str(value)
                            for value in (
                                getattr(metric_result, "errors", []) or []
                            )
                            if str(value).strip()
                        )
                        break
                    else:
                        if metric_failures:
                            errors.append(
                                "商品经营指标：" + "；".join(metric_failures)
                            )
                products = self._list_products(
                    {
                        "store_id": store.id,
                        "search": str(payload.get("search") or ""),
                    }
                )["products"]
                if cancel_event.is_set():
                    update(
                        status="cancelled",
                        message="商品刷新已取消，未提交后续读取结果",
                        errors=errors,
                        cancel_requested=True,
                    )
                    return
                total = max(len(products), reported_count)
                update(
                    status="partial" if errors else "completed",
                    current=total,
                    total=total,
                    message="；".join(tab_messages) or "商品读取完成",
                    errors=errors,
                    products=products,
                )
            except Exception as exc:
                update(
                    status="failed",
                    message=f"{type(exc).__name__}: {exc}",
                    errors=[str(exc)],
                )

        def worker() -> None:
            try:
                worker_body()
            finally:
                self._release_product_store_leases([store.id], lease_id)

        threading.Thread(
            target=worker,
            name=f"webui-product-refresh-{store.id}",
            daemon=True,
        ).start()
        return {"job_id": job_id, "already_running": False}

    def _product_refresh_status(self, payload: dict[str, Any]) -> dict[str, Any]:
        job_id = str(payload.get("job_id") or "")
        if not job_id:
            raise ValueError("job_id 不能为空")
        job: dict[str, Any] | None
        with self._product_jobs_lock:
            job = self._product_jobs.get(job_id)
        persisted = job is None
        if job is None:
            job = self._load_product_task(job_id)
            if job is None or job.get("kind") != "refresh":
                raise ValueError("商品刷新任务不存在")
        if persisted:
            self._require_persisted_product_task_owner(job, "商品刷新任务")
        else:
            self._require_job_owner(job, "商品刷新任务")
        terminal = job.get("status") in ACCOUNT_TASK_TERMINAL_STATUSES
        return self._public_product_task(
            job,
            include_products=terminal,
        )

    def _cancel_product_refresh(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        job_id = str(payload.get("job_id") or "").strip()
        if not job_id:
            raise ValueError("job_id 不能为空")
        with self._product_jobs_lock:
            job = self._product_jobs.get(job_id)
        if job is None:
            persisted = self._load_product_task(job_id)
            if persisted is None or persisted.get("kind") != "refresh":
                raise ValueError("商品刷新任务不存在")
            self._require_persisted_product_task_owner(
                persisted,
                "商品刷新任务",
            )
            return persisted
        self._require_job_owner(job, "商品刷新任务")
        if job.get("status") in ACCOUNT_TASK_TERMINAL_STATUSES:
            return self._public_product_task(job)
        cancel_event = job.get("_cancel_event")
        if isinstance(cancel_event, threading.Event):
            cancel_event.set()
        with self._product_jobs_lock:
            job["status"] = "cancelling"
            job["cancel_requested"] = True
            job["message"] = (
                "正在取消商品刷新；当前页签请求结束后停止，"
                "不会启动后续页签"
            )
            self._persist_product_task(job)
            return self._public_product_task(job)

    def _start_product_batch(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        service = self._require_product_service()
        action = str(payload.get("action") or "").strip()
        if action not in {
            "listing",
            "delisting",
            "recycle",
            "delete",
            "cancel_audit",
        }:
            raise ValueError("不支持的商品批量操作")
        raw_items = payload.get("items")
        if not isinstance(raw_items, list) or not raw_items:
            raise ValueError("至少选择一个商品")
        if len(raw_items) > 5000:
            raise ValueError("单次商品批量操作不能超过 5000 个商品")
        normalized: list[dict[str, Any]] = []
        seen: set[tuple[int, str]] = set()
        for raw in raw_items:
            if not isinstance(raw, dict):
                raise ValueError("商品批量操作项格式无效")
            try:
                store_id = int(raw.get("store_id"))
            except (TypeError, ValueError) as exc:
                raise ValueError("商品批量操作项缺少有效店铺") from exc
            product_id = str(raw.get("product_id") or "").strip()
            if store_id <= 0 or not product_id:
                raise ValueError("商品批量操作项缺少有效店铺或商品 ID")
            key = (store_id, product_id)
            if key in seen:
                continue
            seen.add(key)
            store = self._require_store(store_id)
            product = self.db.get_product(store_id, product_id) or {}
            normalized.append(
                {
                    "store_id": store_id,
                    "store_name": store.name,
                    "product_id": product_id,
                    "title": str(product.get("title") or ""),
                    "status": "queued",
                    "message": "等待执行",
                }
            )
        labels = {
            "listing": "批量上架",
            "delisting": "批量下架",
            "recycle": "批量移入回收站",
            "delete": "批量彻底删除",
            "cancel_audit": "批量撤回审核",
        }
        job_id = uuid.uuid4().hex
        generation = self._current_generation()
        owner = self._job_owner_fields()
        cancel_event = threading.Event()
        store_ids = list(dict.fromkeys(row["store_id"] for row in normalized))
        lease_id = f"delete:{job_id}"
        with self._product_batch_jobs_lock:
            if any(
                self._job_is_active(job)
                and int(job.get("store_id") or 0) in store_ids
                for job in self._product_batch_jobs.values()
            ):
                raise ValueError("所选店铺已有商品批量操作正在运行")
            if action in {"delete", "recycle"}:
                self._claim_product_store_leases(store_ids, lease_id)
            else:
                with self._product_jobs_lock:
                    if any(
                        self._job_is_active(job)
                        and int(job.get("store_id") or 0) in store_ids
                        for job in self._product_jobs.values()
                    ):
                        raise ValueError(
                            "所选店铺商品正在刷新，请先等待或取消刷新"
                        )
            try:
                self._product_batch_jobs[job_id] = {
                    "job_id": job_id,
                    **owner,
                    "kind": "batch",
                    "action": action,
                    "label": labels[action],
                    "store_id": store_ids[0] if len(store_ids) == 1 else 0,
                    "store_ids": store_ids,
                    "status": "starting",
                    "message": f"正在准备{labels[action]}…",
                    "current": 0,
                    "total": len(normalized),
                    "success": 0,
                    "failed": 0,
                    "pending": 0,
                    "cancelled_count": 0,
                    "counts": {
                        "success": 0,
                        "failed": 0,
                        "cancelled": 0,
                        "pending": 0,
                    },
                    "items": normalized,
                    "errors": [],
                    "cancel_requested": False,
                    "_cancel_event": cancel_event,
                }
                self._persist_product_task(self._product_batch_jobs[job_id])
                initial = self._public_product_task(
                    self._product_batch_jobs[job_id]
                )
            except Exception:
                if action in {"delete", "recycle"}:
                    self._release_product_store_leases(
                        store_ids,
                        lease_id,
                    )
                raise

        def counts_locked(job: dict[str, Any]) -> dict[str, int]:
            items = [
                row
                for row in job.get("items", [])
                if isinstance(row, dict)
            ]
            counts = {
                status: sum(
                    1 for row in items if row.get("status") == status
                )
                for status in ("success", "failed", "cancelled", "pending")
            }
            queued = sum(1 for row in items if row.get("status") == "queued")
            counts["queued"] = queued
            return counts

        def update(**changes: Any) -> dict[str, Any] | None:
            with self._product_batch_jobs_lock:
                if not self._job_update_allowed(generation):
                    return None
                job = self._product_batch_jobs.get(job_id)
                if job is None:
                    return None
                if (
                    job.get("status") == "cancelling"
                    and changes.get("status") == "running"
                ):
                    changes["status"] = "cancelling"
                prior_current = max(0, int(job.get("current") or 0))
                job.update(changes)
                counts = counts_locked(job)
                job["success"] = counts["success"]
                job["failed"] = counts["failed"]
                job["pending"] = counts["pending"]
                job["cancelled_count"] = counts["cancelled"]
                job["counts"] = {
                    key: counts[key]
                    for key in ("success", "failed", "cancelled", "pending")
                }
                completed_count = job["total"] - counts["queued"]
                requested_current = changes.get("current")
                job["current"] = (
                    (
                        completed_count
                        if changes.get("status") in ACCOUNT_TASK_TERMINAL_STATUSES
                        else max(completed_count, prior_current)
                    )
                    if requested_current is None
                    else max(
                        completed_count,
                        min(
                            job["total"],
                            max(0, int(requested_current)),
                        ),
                    )
                )
                if changes.get("status") in ACCOUNT_TASK_TERMINAL_STATUSES:
                    job.setdefault(
                        "finished_at",
                        datetime.now().isoformat(timespec="seconds"),
                    )
                self._persist_product_task(job)
                return self._public_product_task(job)

        def set_item(
            store_id: int,
            product_id: str,
            status: str,
            message: str = "",
        ) -> None:
            with self._product_batch_jobs_lock:
                if not self._job_update_allowed(generation):
                    return
                job = self._product_batch_jobs.get(job_id)
                if job is None:
                    return
                for row in job.get("items", []):
                    if (
                        int(row.get("store_id") or 0) == store_id
                        and str(row.get("product_id") or "") == product_id
                    ):
                        row["status"] = status
                        row["message"] = str(message or "")[:1000]
                        break
                counts = counts_locked(job)
                job["success"] = counts["success"]
                job["failed"] = counts["failed"]
                job["pending"] = counts["pending"]
                job["cancelled_count"] = counts["cancelled"]
                job["counts"] = {
                    key: counts[key]
                    for key in ("success", "failed", "cancelled", "pending")
                }
                job["current"] = job["total"] - counts["queued"]
                self._persist_product_task(job)

        def progress(
            message: str,
            current: int,
            total: int,
            *,
            store_id: int,
            group_offset: int,
        ) -> None:
            update(
                status="running",
                message=message,
                current=min(
                    len(normalized),
                    group_offset + max(0, int(current)),
                ),
                total=max(len(normalized), int(total)),
                _progress_store_id=store_id,
            )

        def worker_body() -> None:
            update(status="running", message=f"正在执行{labels[action]}…")
            grouped: dict[int, list[dict[str, Any]]] = {}
            for row in normalized:
                grouped.setdefault(row["store_id"], []).append(row)
            processed_before = 0
            cancel_stopped = False
            try:
                for store_id, group in grouped.items():
                    if cancel_event.is_set():
                        for row in group:
                            set_item(
                                store_id,
                                row["product_id"],
                                "cancelled",
                                "已取消，未执行平台写操作",
                            )
                        continue
                    store = self._require_store(store_id)
                    ids = [row["product_id"] for row in group]
                    if action in {"recycle", "delete"}:
                        writer = getattr(
                            service,
                            "batch_delete_products",
                            None,
                        )
                        try:
                            if not callable(writer):
                                raise RuntimeError(
                                    "当前商品服务不支持批量删除任务"
                                )
                            with self._allow_platform_write(
                                store,
                                "product_write",
                                "product_delete",
                                "product_web_batch_action",
                                "product_listing",
                                "product_delisting",
                            ):
                                result = writer(
                                    store,
                                    ids,
                                    progress=lambda message, current, total: progress(
                                        message,
                                        current,
                                        total,
                                        store_id=store_id,
                                        group_offset=processed_before,
                                    ),
                                    cancel_event=cancel_event,
                                    use_private_batch=bool(
                                        self.db.get_web_cookies(store.id)
                                    ),
                                    hard_delete=action == "delete",
                                )
                        except Exception as exc:
                            for product_id in ids:
                                set_item(
                                    store_id,
                                    product_id,
                                    "pending",
                                    "店铺批量删除未获得明确结果，待核实："
                                    + str(exc),
                                )
                            processed_before += len(ids)
                            continue
                        result_items = {
                            str(row.get("product_id") or ""): row
                            for row in result.get("items") or []
                            if isinstance(row, dict)
                        }
                        for product_id in ids:
                            row = result_items.get(product_id, {})
                            set_item(
                                store_id,
                                product_id,
                                str(row.get("status") or "pending"),
                                str(row.get("message") or ""),
                            )
                        processed_before += len(ids)
                        if bool(result.get("cancelled")):
                            cancel_stopped = True
                        if bool(result.get("cancelled")) or cancel_event.is_set():
                            cancel_event.set()
                        continue

                    for index, row in enumerate(group, start=1):
                        product_id = str(row["product_id"])
                        if cancel_event.is_set():
                            set_item(
                                store_id,
                                product_id,
                                "cancelled",
                                "已取消，未启动后续平台写操作",
                            )
                            continue
                        update(
                            status="running",
                            message=(
                                f"{labels[action]} "
                                f"{processed_before + index}/{len(normalized)}"
                            ),
                            current=processed_before + index - 1,
                            total=len(normalized),
                            active_product_id=product_id,
                        )
                        try:
                            if action in {"listing", "delisting"}:
                                with self._allow_platform_write(
                                    store,
                                    "product_write",
                                    "product_listing",
                                    "product_delisting",
                                ):
                                    result = service.set_products_listing(
                                        store,
                                        [product_id],
                                        action == "listing",
                                    )
                                public = self._public_service_result(result)
                            else:
                                with self._allow_platform_write(
                                    store,
                                    "product_write",
                                    "product_audit_cancel",
                                ):
                                    result = service.product_write(
                                        store,
                                        "cancel_audit",
                                        product_id=product_id,
                                    )
                                public = dict(result) if isinstance(result, dict) else {}
                                self._log_product_operation(
                                    store,
                                    product_id,
                                    str(row.get("title") or ""),
                                    "撤回审核",
                                    (
                                        "pending"
                                        if public.get("verification_pending")
                                        else "success"
                                    ),
                                    str(public.get("message") or ""),
                                )
                        except Exception as exc:
                            status = (
                                "pending"
                                if bool(
                                    getattr(exc, "verification_pending", False)
                                )
                                else "failed"
                            )
                            set_item(
                                store_id,
                                product_id,
                                status,
                                str(exc),
                            )
                        else:
                            if public.get("cancelled"):
                                status = "cancelled"
                                message = str(
                                    public.get("message")
                                    or "已取消，未确认平台写入"
                                )
                            elif (
                                public.get("verification_pending")
                                or int(public.get("pending") or 0) > 0
                            ):
                                status = "pending"
                                message = str(
                                    public.get("message")
                                    or "平台结果待核实"
                                )
                            elif int(public.get("failed") or 0) > 0:
                                status = "failed"
                                message = str(
                                    (public.get("errors") or [""])[0]
                                    or public.get("message")
                                    or "平台操作失败"
                                )
                            else:
                                status = "success"
                                message = str(
                                    public.get("message") or "平台操作完成"
                                )
                            set_item(
                                store_id,
                                product_id,
                                status,
                                message,
                            )
                        if cancel_event.is_set():
                            for remaining in group[index:]:
                                set_item(
                                    store_id,
                                    str(remaining["product_id"]),
                                    "cancelled",
                                    "已取消，未启动后续平台写操作",
                                )
                            break
                    processed_before += len(group)
                with self._product_batch_jobs_lock:
                    job = self._product_batch_jobs.get(job_id)
                    if job is None:
                        return
                    for row in job.get("items", []):
                        if row.get("status") == "queued":
                            row["status"] = "cancelled"
                            row["message"] = "已取消，未执行平台写操作"
                    counts = counts_locked(job)
                if counts["pending"]:
                    status = "verification_pending"
                    message = (
                        f"{labels[action]}结果待核实：成功 {counts['success']} 个，"
                        f"失败 {counts['failed']} 个，"
                        f"待核实 {counts['pending']} 个"
                    )
                elif cancel_stopped or counts["cancelled"] > 0:
                    status = "cancelled"
                    message = (
                        f"{labels[action]}已取消：成功 {counts['success']} 个，"
                        f"失败 {counts['failed']} 个，取消 {counts['cancelled']} 个，"
                        f"待核实 {counts['pending']} 个"
                    )
                elif counts["failed"] and counts["success"]:
                    status = "partial"
                    message = (
                        f"{labels[action]}部分完成：成功 {counts['success']} 个，"
                        f"失败 {counts['failed']} 个"
                    )
                elif counts["failed"]:
                    status = "failed"
                    message = (
                        f"{labels[action]}失败：{counts['failed']} 个商品未完成"
                    )
                else:
                    status = "completed"
                    message = (
                        f"{labels[action]}完成：成功 {counts['success']} 个"
                    )
                errors = [
                    f"{row.get('product_id')}: {row.get('message')}"
                    for row in (
                        self._product_batch_jobs.get(job_id, {}).get("items")
                        or []
                    )
                    if isinstance(row, dict)
                    and row.get("status") in {"failed", "pending"}
                ]
                update(
                    status=status,
                    message=message,
                    errors=errors[:100],
                )
            except Exception as exc:
                with self._product_batch_jobs_lock:
                    job = self._product_batch_jobs.get(job_id)
                    if job is not None:
                        for row in job.get("items", []):
                            if row.get("status") == "queued":
                                row["status"] = "pending"
                                row["message"] = (
                                    "前序任务失败，未获得平台执行结论，待核实"
                                )
                update(
                    status="failed",
                    message=f"{labels[action]}失败：{exc}",
                    errors=[str(exc)],
                )

        def worker() -> None:
            try:
                worker_body()
            finally:
                if action in {"delete", "recycle"}:
                    self._release_product_store_leases(
                        store_ids,
                        lease_id,
                    )

        threading.Thread(
            target=worker,
            name=f"webui-product-batch-{job_id[:8]}",
            daemon=True,
        ).start()
        return initial

    def _product_batch_status(self, payload: dict[str, Any]) -> dict[str, Any]:
        job_id = str(payload.get("job_id") or "").strip()
        if not job_id:
            raise ValueError("job_id 不能为空")
        with self._product_batch_jobs_lock:
            job = self._product_batch_jobs.get(job_id)
        persisted = job is None
        if job is None:
            job = self._load_product_task(job_id)
            if job is None or job.get("kind") != "batch":
                raise ValueError("商品批量任务不存在")
        if persisted:
            self._require_persisted_product_task_owner(job, "商品批量任务")
        else:
            self._require_job_owner(job, "商品批量任务")
        return self._public_product_task(job)

    def _cancel_product_batch(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        job_id = str(payload.get("job_id") or "").strip()
        if not job_id:
            raise ValueError("job_id 不能为空")
        with self._product_batch_jobs_lock:
            job = self._product_batch_jobs.get(job_id)
            if job is None:
                persisted = self._load_product_task(job_id)
                if persisted is None or persisted.get("kind") != "batch":
                    raise ValueError("商品批量任务不存在")
                self._require_persisted_product_task_owner(
                    persisted,
                    "商品批量任务",
                )
                return persisted
            self._require_job_owner(job, "商品批量任务")
            if job.get("status") in ACCOUNT_TASK_TERMINAL_STATUSES:
                return self._public_product_task(job)
            cancel_event = job.get("_cancel_event")
            if isinstance(cancel_event, threading.Event):
                cancel_event.set()
            job["status"] = "cancelling"
            job["cancel_requested"] = True
            job["message"] = (
                "正在取消；当前平台请求结束后停止，不会启动后续商品"
            )
            self._persist_product_task(job)
            return self._public_product_task(job)

    def _list_product_tasks(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        store_id = payload.get("store_id")
        try:
            store_filter = int(store_id) if store_id not in (None, "") else 0
        except (TypeError, ValueError) as exc:
            raise ValueError("store_id 必须是整数") from exc
        tasks: dict[str, dict[str, Any]] = {}
        for job_id in self._load_product_task_registry():
            job = self._load_product_task(job_id)
            if job is not None:
                tasks[job_id] = job
        with self._product_jobs_lock:
            for job_id, job in self._product_jobs.items():
                tasks[job_id] = self._public_product_task(job)
        with self._product_batch_jobs_lock:
            for job_id, job in self._product_batch_jobs.items():
                tasks[job_id] = self._public_product_task(job)
        output: list[dict[str, Any]] = []
        for job in tasks.values():
            try:
                self._require_persisted_product_task_owner(job, "商品任务")
            except PermissionError:
                continue
            job_store_ids = [
                int(value)
                for value in job.get("store_ids") or [job.get("store_id")]
                if str(value or "").isdigit()
            ]
            if store_filter and store_filter not in job_store_ids:
                continue
            output.append(job)
        output.sort(
            key=lambda row: str(
                row.get("updated_at")
                or row.get("created_at")
                or ""
            ),
            reverse=True,
        )
        return {"tasks": output[:30]}

    def _statistics_summary(self, payload: dict[str, Any]) -> dict[str, Any]:
        year = int(payload.get("year") or datetime.now().year)
        report = self._profit_report(
            {
                "view": "利润统计",
                "period": "日报表",
                "start_date": f"{year}-01-01",
                "end_date": f"{year}-12-31",
            }
        )
        daily: dict[str, dict[str, int]] = {}
        for row in report["orders"]:
            stamp = int(row.get("create_time") or 0)
            if stamp <= 0:
                continue
            key = datetime.fromtimestamp(stamp).date().isoformat()
            bucket = daily.setdefault(
                key,
                {
                    "date": key,
                    "orders": 0,
                    "sales": 0,
                    "cost": 0,
                    "commission": 0,
                    "platform_fee": 0,
                    "profit": 0,
                    "pending": 0,
                },
            )
            bucket["orders"] += 1
            bucket["sales"] += int(row.get("sale_amount") or 0)
            bucket["commission"] += int(row.get("commission") or 0)
            bucket["platform_fee"] += int(row.get("platform_fee") or 0)
            if row.get("pending"):
                bucket["pending"] += 1
            else:
                bucket["cost"] += int(row.get("purchase_price") or 0)
                bucket["profit"] += int(row.get("profit") or 0)
        for bucket in daily.values():
            bucket["margin"] = (
                round(bucket["profit"] / bucket["sales"] * 100, 1)
                if bucket["sales"] and bucket["orders"] > bucket["pending"]
                else 0
            )
        totals = report["summary"]
        return {
            "year": year,
            "totals": {
                "orders": totals["orders"],
                "sales": totals["sale"],
                "cost": totals["cost"],
                "commission": totals["commission"],
                "platform_fee": totals["platform_fee"],
                "profit": totals["profit"],
                "pending": totals["pending"],
            },
            "daily": sorted(daily.values(), key=lambda item: item["date"], reverse=True),
        }

    def _list_hosted(self, _payload: dict[str, Any]) -> dict[str, Any]:
        stores = []
        configured = 0
        for store in self.db.list_stores():
            config = self.db.get_hosted_config(store.id)
            if config:
                configured += 1
            stores.append(
                {
                    "id": store.id,
                    "name": store.name,
                    "web_status": store.web_status,
                    "config": config or {},
                }
            )
        schedule = self._get_hosted_schedule({})
        return {
            "stores": stores,
            "configured_count": configured,
            "runs": self.db.list_hosted_runs(limit=100),
            "schedule": schedule,
        }

    def _save_hosted_config(self, payload: dict[str, Any]) -> dict[str, Any]:
        store_id = self._store_id(payload)
        changes = {
            key: value
            for key, value in payload.items()
            if key != "store_id"
        }
        config = self.db.update_hosted_config(store_id, **changes)
        return {"config": config}

    def _get_hosted_schedule(self, _payload: dict[str, Any]) -> dict[str, Any]:
        try:
            selected = json.loads(self.db.get_setting("hosted_schedule_stores", "[]"))
        except (TypeError, ValueError):
            selected = []
        return {
            "enabled": self.db.get_setting("hosted_schedule_enabled", "0") == "1",
            "time": self.db.get_setting("hosted_schedule_time", "09:30"),
            "store_ids": [int(value) for value in selected if str(value).isdigit()],
        }

    def _save_hosted_schedule(self, payload: dict[str, Any]) -> dict[str, Any]:
        store_ids = sorted(
            {
                int(value)
                for value in payload.get("store_ids", [])
                if str(value).isdigit()
            }
        )
        time_value = str(payload.get("time") or "09:30")
        try:
            time_value = datetime.strptime(time_value, "%H:%M").strftime("%H:%M")
        except ValueError as exc:
            raise ValueError("定时执行时间必须为 HH:MM")
        self.db.set_setting(
            "hosted_schedule_enabled",
            "1" if payload.get("enabled") else "0",
        )
        self.db.set_setting("hosted_schedule_time", time_value)
        self.db.set_setting(
            "hosted_schedule_stores",
            json.dumps(store_ids),
        )
        return self._get_hosted_schedule({})

    def _execute_hosted(self, payload: dict[str, Any]) -> dict[str, Any]:
        service = self._require_product_service()
        selected_ids = {
            int(value)
            for value in payload.get("store_ids", [])
            if str(value).isdigit()
        }
        targets = []
        for store in self.db.list_stores():
            config = self.db.get_hosted_config(store.id) or {}
            if selected_ids and store.id not in selected_ids:
                continue
            if not config.get("enabled"):
                continue
            targets.append((store, config))
        if not targets:
            raise ValueError("没有已启用的托管店铺")

        from .hosted_runner import run_hosted_store

        errors: list[str] = []
        succeeded = 0
        for store, config in targets:
            error = run_hosted_store(self.db, service, store, config)
            if error:
                errors.append(error)
            else:
                succeeded += 1
        return {
            "executed": len(targets),
            "succeeded": succeeded,
            "errors": errors,
            "runs": self.db.list_hosted_runs(limit=100),
        }

    def run_due_hosted_schedule(
        self,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        current = now or datetime.now()
        schedule = self._get_hosted_schedule({})
        if not schedule["enabled"]:
            return {"started": False, "reason": "托管定时执行未启用"}
        if current.strftime("%H:%M") != schedule["time"]:
            return {"started": False, "reason": "未到托管定时执行时间"}
        run_date = current.date().isoformat()
        if self.db.get_setting("hosted_last_run_date", "") == run_date:
            return {"started": False, "reason": "今日托管定时执行已完成"}
        store_ids = list(schedule["store_ids"])
        if not store_ids:
            return {"started": False, "reason": "未选择托管定时执行店铺"}
        self.db.set_setting("hosted_last_run_date", run_date)
        try:
            result = self._execute_hosted({"store_ids": store_ids})
        except Exception:
            self.db.set_setting("hosted_last_run_date", "")
            raise
        return {
            "started": True,
            "run_date": run_date,
            "scheduled_store_count": len(store_ids),
            **result,
        }

    def _violation_rows(self) -> list[dict[str, Any]]:
        try:
            rows = json.loads(self.db.get_setting("violation_library", "[]"))
        except (TypeError, ValueError, json.JSONDecodeError):
            return []
        if not isinstance(rows, list):
            return []
        rows = filter_active_violation_records(
            rows,
            self.db.list_stores(),
        )
        result = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            head_img = str(row.get("head_img") or "")
            if not head_img:
                product_id = str(row.get("product_id") or "")
                store_id = int(row.get("store_id") or 0)
                if product_id and store_id:
                    product = self.db.get_product(store_id, product_id)
                    if isinstance(product, dict):
                        head_img = str(product.get("head_img") or "")
            result.append(
                {
                    "record_id": str(row.get("record_id") or ""),
                    "product_id": str(row.get("product_id") or ""),
                    "title": str(row.get("title") or ""),
                    "reason": str(row.get("reason") or ""),
                    "source": str(row.get("source") or ""),
                    "status": str(row.get("status") or ""),
                    "fix": str(row.get("fix") or ""),
                    "time": str(row.get("time") or ""),
                    "store_id": int(row.get("store_id") or 0),
                    "store_name": str(row.get("store_name") or ""),
                    "synced_at": str(row.get("synced_at") or ""),
                    "head_img": head_img,
                }
            )
        return result

    def _upload_violation_title_rows(self) -> list[dict[str, Any]]:
        return load_upload_violation_title_records(self.db)

    def _cloud_violation_token(self) -> str:
        auth_context = self.auth_context
        tokens = getattr(auth_context, "tokens", None)
        return str(getattr(tokens, "access_token", "") or "")

    def _push_counterfeit_violation_index(
        self,
        access_token: str,
        store_ids: set[int] | None = None,
    ) -> dict[str, Any]:
        client = self.auth_client
        push = getattr(
            client,
            "upsert_organization_counterfeit_violations",
            None,
        )
        if client is None or not access_token or not callable(push):
            return {"saved": 0, "skipped": "cloud_not_configured"}
        try:
            records = json.loads(
                self.db.get_setting("violation_library", "[]")
            )
        except (TypeError, ValueError, json.JSONDecodeError):
            records = []
        active_stores = self.db.list_stores()
        records = filter_active_violation_records(
            records if isinstance(records, list) else [],
            active_stores,
        )
        if store_ids is not None:
            records = [
                record
                for record in records
                if int(record.get("store_id") or 0) in store_ids
            ]
        app_id_by_store_id = {
            store.id: store.app_id
            for store in active_stores
            if store.id and store.app_id
        }
        rows: list[dict[str, Any]] = []
        for record in records:
            if not isinstance(record, dict):
                continue
            try:
                record_store_id = int(record.get("store_id") or 0)
            except (TypeError, ValueError):
                record_store_id = 0
            store_app_id = str(
                record.get("store_app_id")
                or app_id_by_store_id.get(record_store_id)
                or ""
            ).strip()
            rows.extend(
                build_cloud_counterfeit_rows(
                    [record],
                    store_app_id=store_app_id,
                )
            )
        deduped = {
            str(row["dedupe_key"]): row
            for row in rows
            if row.get("store_app_id")
        }
        pending_rows = list(deduped.values())
        result = push(access_token, pending_rows)
        if not isinstance(result, dict):
            return {
                "saved": 0,
                "error": "云端售假库返回不完整，未确认写入",
            }
        try:
            saved = int(result.get("saved"))
        except (TypeError, ValueError):
            return {
                **result,
                "saved": 0,
                "error": "云端售假库返回不完整，未确认写入",
            }
        if saved < 0 or saved > len(pending_rows):
            return {
                **result,
                "saved": max(0, saved),
                "error": "云端售假库返回的确认数量无效",
            }
        if saved != len(pending_rows):
            return {
                **result,
                "saved": saved,
                "error": (
                    "云端售假库未确认全部写入："
                    f"已确认 {saved}/{len(pending_rows)} 条"
                ),
            }
        return {**result, "saved": saved}

    def _pull_counterfeit_violation_index(
        self,
        access_token: str,
    ) -> list[dict[str, Any]]:
        client = self.auth_client
        pull = getattr(
            client,
            "pull_organization_counterfeit_violations",
            None,
        )
        if client is None or not access_token or not callable(pull):
            return []
        rows = pull(access_token)
        safe_rows = []
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            if str(row.get("reason_category") or "") not in {
                "出售假冒商品",
                "假冒商品",
                "售假",
            }:
                continue
            normalized = str(row.get("normalized_title") or "").strip()
            title = str(row.get("title") or "").strip()
            if not normalized or not title:
                continue
            safe_rows.append(
                {
                    "title": title,
                    "normalized": normalized,
                    "reason": str(row.get("reason_category") or ""),
                    "record_id": str(row.get("dedupe_key") or ""),
                    "product_id": str(row.get("product_id") or ""),
                    "store_app_id": str(row.get("store_app_id") or ""),
                    "source": "cloud_counterfeit",
                    "updated_at": str(row.get("updated_at") or ""),
                }
            )
        self.db.set_setting(
            "cloud_counterfeit_violation_titles",
            json.dumps(safe_rows, ensure_ascii=False),
        )
        return safe_rows

    def _list_violations(self, _payload: dict[str, Any]) -> dict[str, Any]:
        rows = self._violation_rows()
        return {"violations": rows, "count": len(rows)}

    def _refresh_violations(self, payload: dict[str, Any]) -> dict[str, Any]:
        service = self._require_product_service()
        store_id = payload.get("store_id")
        if store_id not in (None, ""):
            requested_store = self._require_store(int(store_id))
            stores = (
                [requested_store]
                if is_real_logged_in_store(requested_store)
                else []
            )
            if not stores:
                return {
                    "refreshed": 0,
                    "succeeded": 0,
                    "errors": [
                        f"{requested_store.name} 不是已登录真实店铺，"
                        "已拒绝同步违规记录"
                    ],
                    "violations": [],
                    "cloud_counterfeit": {},
                }
        else:
            stores = real_logged_in_stores(self.db.list_stores())
        if not stores:
            return {
                "refreshed": 0,
                "succeeded": 0,
                "errors": ["没有可同步的已登录真实店铺"],
                "violations": [],
                "cloud_counterfeit": {},
            }
        errors: list[str] = []
        succeeded = 0
        succeeded_store_ids: set[int] = set()
        with ThreadPoolExecutor(
            max_workers=max(1, min(4, len(stores))),
            thread_name_prefix="webui-violation-sync",
        ) as executor:
            futures = {
                executor.submit(service.sync_violation_library, store): store
                for store in stores
            }
            for future in as_completed(futures):
                store = futures[future]
                try:
                    future.result()
                except Exception as exc:
                    errors.append(f"{store.name}: {exc}")
                else:
                    succeeded += 1
                    succeeded_store_ids.add(store.id)
        cloud_result: dict[str, Any] = {}
        access_token = self._cloud_violation_token()
        if access_token and succeeded_store_ids:
            try:
                cloud_result = self._push_counterfeit_violation_index(
                    access_token,
                    succeeded_store_ids,
                )
                self._pull_counterfeit_violation_index(access_token)
            except Exception as exc:
                cloud_result = {
                    "error": str(exc)[:1000],
                    "saved": 0,
                }
                log_event(
                    "violations.cloud_counterfeit.sync_failed",
                    error=str(exc)[:1000],
                )
        elif access_token:
            cloud_result = {
                "saved": 0,
                "skipped": "no_successful_store_sync",
                "error": "没有成功同步的店铺，未推送云端售假库",
            }
        else:
            cloud_result = {
                "saved": 0,
                "skipped": "missing_access_token",
                "error": "当前登录缺少云端访问令牌",
            }
        return {
            "refreshed": len(stores),
            "succeeded": succeeded,
            "errors": errors,
            "violations": self._violation_rows(),
            "cloud_counterfeit": cloud_result,
        }

    def _transit_shipping_rules(self) -> dict[str, Any]:
        try:
            value = json.loads(
                self.db.get_setting(
                    TRANSIT_SHIPPING_RULES_SETTING,
                    "{}",
                )
            )
        except (TypeError, ValueError, json.JSONDecodeError):
            value = {}
        return normalize_rules(
            value if isinstance(value, dict) else {}
        )

    def _store_for_transit(
        self,
        payload: dict[str, Any],
    ) -> tuple[Store, TransitShippingManager]:
        store = self._require_store(self._store_id(payload))
        if store.web_status != "已登录":
            raise ValueError("店铺网页登录无效，请先在店铺管理登录")
        return store, TransitShippingManager(self.db)

    def _transit_shipping_overview(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        store, manager = self._store_for_transit(payload)
        try:
            overview = manager.overview(store)
        except TransitShippingError as exc:
            raise ValueError(str(exc)) from exc
        return {
            "store": {
                "id": store.id,
                "name": store.name,
                "app_id": store.app_id,
                "web_status": store.web_status,
            },
            "rules": self._transit_shipping_rules(),
            **overview,
        }

    def _save_transit_shipping_rules(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        rules = normalize_rules(
            payload.get("rules")
            if isinstance(payload.get("rules"), dict)
            else payload
        )
        self.db.set_setting(
            TRANSIT_SHIPPING_RULES_SETTING,
            json.dumps(rules, ensure_ascii=False),
        )
        return {"rules": rules}

    def _apply_transit_shipping(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        if payload.get("confirmed") is not True:
            raise ValueError(
                "执行全店中转属于真实平台写操作，"
                "请先完成一次执行确认"
            )
        store, manager = self._store_for_transit(payload)
        rules = normalize_rules(
            payload.get("rules")
            if isinstance(payload.get("rules"), dict)
            else self._transit_shipping_rules()
        )
        requested_ids = [
            str(value)
            for value in payload.get("template_ids") or []
            if str(value).strip()
        ]
        with self._allow_platform_write(
            store,
            "remote_transit_shipping_batch",
        ):
            result = manager.run_continuous_flow(
                store,
                template_ids=requested_ids,
                rules=rules,
            )
        if result.get("success"):
            self.db.set_setting(
                TRANSIT_SHIPPING_RULES_SETTING,
                json.dumps(rules, ensure_ascii=False),
            )
        self._write_operation_log(
            "transit",
            "全店中转连续流程",
            (
                f"{store.name}："
                f"{result.get('message') or result.get('failed_stage')}"
            ),
            store_id=store.id,
            status="success" if result.get("success") else "failed",
            detail={
                "template_ids": requested_ids,
                "failed_stage": result.get("failed_stage", ""),
                "product_failures": result.get(
                    "product_failures",
                    [],
                )[:20],
                "template_failures": result.get(
                    "template_failures",
                    [],
                )[:20],
                "errors": result.get("errors", [])[:20],
            },
        )
        return {**result, "rules": rules}

    def _list_procurement(self, payload: dict[str, Any]) -> dict[str, Any]:
        platform = str(payload.get("platform") or "").strip() or None
        rows = self.db.list_procurement_accounts(platform=platform)
        accounts = []
        for row in rows:
            accounts.append(
                {
                    "id": int(row.get("id") or 0),
                    "platform": str(row.get("platform") or ""),
                    "label": str(row.get("label") or ""),
                    "nickname": str(row.get("nickname") or ""),
                    "group_name": str(row.get("group_name") or ""),
                    "remark": str(row.get("remark") or ""),
                    "status": str(row.get("status") or ""),
                    "auto_login": bool(row.get("auto_login")),
                    "account_type": int(row.get("account_type") or 0),
                    "last_checked_at": str(row.get("last_checked_at") or ""),
                    "last_error": str(row.get("last_error") or ""),
                    "has_session": bool(
                        self.db.get_procurement_session(
                            int(row.get("id") or 0)
                        )
                    ),
                }
            )
        return {"accounts": accounts}

    def _create_procurement(self, payload: dict[str, Any]) -> dict[str, Any]:
        account_id = self.db.add_procurement_account(
            str(payload.get("platform") or "淘宝"),
            str(payload.get("label") or ""),
            str(payload.get("login_id") or ""),
            str(payload.get("profile_dir") or ""),
            nickname=str(payload.get("nickname") or ""),
            group_name=str(payload.get("group_name") or ""),
            remark=str(payload.get("remark") or ""),
            auto_login=bool(payload.get("auto_login")),
            account_type=int(payload.get("account_type") or 2),
        )
        password = str(payload.get("password") or "")
        alipay_password = str(payload.get("alipay_password") or "")
        if password or alipay_password:
            self.db.set_procurement_account_secrets(
                account_id,
                password=password or None,
                alipay_password=alipay_password or None,
            )
        account = self.db.get_procurement_account(account_id)
        if account is None:
            raise RuntimeError("采购账号创建后无法读取")
        return {"account": self._public_procurement_account(account)}

    def _update_procurement(self, payload: dict[str, Any]) -> dict[str, Any]:
        account_id = self._procurement_id(payload)
        allowed = {
            "label",
            "nickname",
            "group_name",
            "remark",
            "login_id",
            "profile_dir",
            "auto_login",
            "account_type",
        }
        changes = {key: payload[key] for key in allowed if key in payload}
        self.db.update_procurement_account(account_id, **changes)
        password = str(payload.get("password") or "")
        alipay_password = str(payload.get("alipay_password") or "")
        if password or alipay_password:
            self.db.set_procurement_account_secrets(
                account_id,
                password=password or None,
                alipay_password=alipay_password or None,
            )
        account = self.db.get_procurement_account(account_id)
        if account is None:
            raise ValueError("采购账号不存在")
        return {"account": self._public_procurement_account(account)}

    def _delete_procurement(self, payload: dict[str, Any]) -> dict[str, Any]:
        account_id = self._procurement_id(payload)
        cleanup_errors: list[str] = []
        delete_account = getattr(
            self.product_service,
            "procurement_delete_account",
            None,
        )
        if callable(delete_account):
            cleanup_errors = delete_account(account_id)
        else:
            self.db.delete_procurement_account(account_id)
        return {
            "deleted": account_id,
            "cleanup_errors": cleanup_errors,
        }

    def _login_procurement(self, payload: dict[str, Any]) -> dict[str, Any]:
        service = self._require_product_service()
        account_id = self._procurement_id(payload)
        try:
            result = service.procurement_login(
                account_id,
                auto_fill=not bool(payload.get("scan_only")),
            )
        except TypeError as exc:
            if "auto_fill" not in str(exc):
                raise
            result = service.procurement_login(account_id)
        return {"account_id": account_id, "result": result}

    def _start_procurement_login(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        service = self._require_product_service()
        account_id = self._procurement_id(payload)
        account = self.db.get_procurement_account(account_id)
        if account is None:
            raise ValueError("采购账号不存在")
        if not self._procurement_login_lock.acquire(blocking=False):
            raise RuntimeError(
                "采购账号登录窗口已在运行，请先完成或关闭现有窗口"
            )
        job_id = uuid.uuid4().hex
        with self._procurement_login_jobs_lock:
            self._procurement_login_jobs[job_id] = {
                "job_id": job_id,
                "account_id": account_id,
                "status": "starting",
                "message": "正在创建淘宝官方登录窗口…",
                "error": "",
                "account": self._public_procurement_account(account),
            }

        def update(**changes: Any) -> None:
            with self._procurement_login_jobs_lock:
                job = self._procurement_login_jobs.get(job_id)
                if job is not None:
                    job.update(changes)

        def worker() -> None:
            try:
                self._write_operation_log(
                    "procurement",
                    "采购账号扫码登录",
                    f"正在打开官方登录窗口：{account['label']}",
                    detail={
                        "account_id": account_id,
                        "platform": account.get("platform"),
                    },
                )
                update(
                    status="waiting_login",
                    message=(
                        "官方登录窗口已启动，请在窗口中扫码并完成验证；"
                        "登录成功并关闭窗口后会自动校验账号"
                    ),
                )
                login_result = service.procurement_login(
                    account_id,
                    auto_fill=False,
                )
                status = service.procurement_check_status(account_id)
                updated = self.db.get_procurement_account(account_id) or {}
                if status != "已登录":
                    raise RuntimeError(
                        f"官方会话校验未通过：{status}"
                        + (
                            f"（{updated.get('last_error')}）"
                            if updated.get("last_error")
                            else ""
                        )
                    )
                update(
                    status="completed",
                    message="采购账号登录成功，官方身份与登录会话已确认",
                    account=self._public_procurement_account(updated),
                    identity=str(
                        (
                            login_result.get("identity")
                            if isinstance(login_result, dict)
                            else ""
                        )
                        or (
                            self.db.get_procurement_session(account_id)
                            or {}
                        ).get("identity")
                        or ""
                    ),
                )
                self._write_operation_log(
                    "procurement",
                    "采购账号扫码登录",
                    (
                        f"采购账号登录成功：{account['label']} · "
                        f"身份 {(
                            login_result.get('identity')
                            if isinstance(login_result, dict)
                            else ''
                        ) or updated.get('nickname') or ''}"
                    ),
                    detail={"account_id": account_id},
                )
            except Exception as exc:
                message = str(exc)[:1000]
                update(
                    status="failed",
                    message=message,
                    error=message,
                )
                self.db.update_procurement_account(
                    account_id,
                    last_error=message,
                    last_checked_at=datetime.now().isoformat(
                        timespec="seconds"
                    ),
                )
                self._write_operation_log(
                    "procurement",
                    "采购账号扫码登录",
                    f"采购账号登录失败：{account['label']}",
                    status="failed",
                    detail={
                        "account_id": account_id,
                        "error": message,
                    },
                )
            finally:
                self._procurement_login_lock.release()

        threading.Thread(
            target=worker,
            daemon=True,
            name=f"procurement-login-{account_id}",
        ).start()
        return {"job_id": job_id, "account_id": account_id}

    def _start_procurement_backend(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        service = self._require_product_service()
        open_backend = getattr(
            service,
            "procurement_open_backend",
            None,
        )
        if not callable(open_backend):
            raise RuntimeError("采购账号官方后台服务尚未配置")
        account_id = self._procurement_id(payload)
        account = self.db.get_procurement_account(account_id)
        if account is None:
            raise ValueError("采购账号不存在")
        if account.get("status") != "已登录" or not self.db.get_procurement_session(
            account_id
        ):
            raise ValueError("采购账号尚未登录，请先扫码登录")
        if not self._procurement_login_lock.acquire(blocking=False):
            raise RuntimeError(
                "采购账号登录或后台窗口已在运行，请先关闭现有窗口"
            )
        job_id = uuid.uuid4().hex
        with self._procurement_login_jobs_lock:
            self._procurement_login_jobs[job_id] = {
                "job_id": job_id,
                "account_id": account_id,
                "action": "open_backend",
                "status": "starting",
                "message": "正在打开采购账号官方后台…",
                "error": "",
                "account": self._public_procurement_account(account),
            }

        def update(**changes: Any) -> None:
            with self._procurement_login_jobs_lock:
                job = self._procurement_login_jobs.get(job_id)
                if job is not None:
                    job.update(changes)

        def worker() -> None:
            try:
                update(
                    status="waiting_user",
                    message="官方后台窗口已打开，关闭窗口后本次操作结束",
                )
                result = open_backend(account_id)
                update(
                    status="completed",
                    message="采购账号官方后台窗口已关闭",
                    identity=str(result.get("identity") or ""),
                    opened_url=str(result.get("opened_url") or ""),
                    final_url=str(result.get("final_url") or ""),
                    account=self._public_procurement_account(
                        self.db.get_procurement_account(account_id)
                        or account
                    ),
                )
            except Exception as exc:
                message = str(exc)[:1000]
                update(
                    status="failed",
                    message=message,
                    error=message,
                )
                self.db.update_procurement_account(
                    account_id,
                    last_error=message,
                    last_checked_at=datetime.now().isoformat(
                        timespec="seconds"
                    ),
                )
            finally:
                self._procurement_login_lock.release()

        threading.Thread(
            target=worker,
            daemon=True,
            name=f"procurement-backend-{account_id}",
        ).start()
        return {
            "job_id": job_id,
            "account_id": account_id,
            "action": "open_backend",
        }

    def _procurement_login_status(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        job_id = str(payload.get("job_id") or "").strip()
        if not job_id:
            raise ValueError("job_id 不能为空")
        with self._procurement_login_jobs_lock:
            job = self._procurement_login_jobs.get(job_id)
            if job is None:
                raise ValueError("采购登录任务不存在或已过期")
            return dict(job)

    def _probe_procurement(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        service = self._require_product_service()
        account_id = self._procurement_id(payload)
        status = service.procurement_check_status(account_id)
        account = self.db.get_procurement_account(account_id)
        if account is None:
            raise ValueError("采购账号不存在")
        return {
            "account_id": account_id,
            "status": status,
            "account": self._public_procurement_account(account),
        }

    def _sync_procurement(self, payload: dict[str, Any]) -> dict[str, Any]:
        service = self._require_product_service()
        account_id = self._procurement_id(payload)
        result = service.sync_procurement_account(account_id)
        if isinstance(result, dict):
            result_data = dict(result)
        elif hasattr(result, "__dataclass_fields__"):
            result_data = asdict(result)
        elif hasattr(result, "__dict__"):
            result_data = dict(vars(result))
        else:
            result_data = {"message": str(result)}
        if hasattr(result, "store_ids"):
            result_data["store_ids"] = list(getattr(result, "store_ids", []) or [])
        return {"account_id": account_id, "result": result_data}

    @staticmethod
    def _procurement_id(payload: dict[str, Any]) -> int:
        try:
            account_id = int(payload.get("account_id"))
        except (TypeError, ValueError) as exc:
            raise ValueError("account_id 必须是整数") from exc
        if account_id <= 0:
            raise ValueError("account_id 必须是正整数")
        return account_id

    @staticmethod
    def _public_procurement_account(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": int(row.get("id") or 0),
            "platform": str(row.get("platform") or ""),
            "label": str(row.get("label") or ""),
            "nickname": str(row.get("nickname") or ""),
            "group_name": str(row.get("group_name") or ""),
            "remark": str(row.get("remark") or ""),
            "status": str(row.get("status") or ""),
            "auto_login": bool(row.get("auto_login")),
            "account_type": int(row.get("account_type") or 0),
            "last_error": str(row.get("last_error") or ""),
            "last_checked_at": str(row.get("last_checked_at") or ""),
        }

    def _probe_store(self, payload: dict[str, Any]) -> dict[str, Any]:
        session = self._require_web_session()
        store = self._require_store(self._store_id(payload))
        status, detail = session.probe(store)
        return {"status": status, "detail": detail}

    @staticmethod
    def _requested_store_ids(payload: dict[str, Any]) -> list[int]:
        raw_values = payload.get("store_ids")
        if raw_values is None:
            raw_values = [payload.get("store_id")]
        if not isinstance(raw_values, (list, tuple, set)):
            raise ValueError("store_ids 必须是数组")
        requested: list[int] = []
        for raw in raw_values:
            try:
                store_id = int(raw)
            except (TypeError, ValueError) as exc:
                raise ValueError("店铺 ID 必须是整数") from exc
            if store_id <= 0:
                raise ValueError("店铺 ID 必须是正整数")
            if store_id not in requested:
                requested.append(store_id)
        if not requested:
            raise ValueError("请至少选择一家店铺")
        if len(requested) > 500:
            raise ValueError("单次批量登录不能超过 500 家店铺")
        return requested

    def _login_store(self, payload: dict[str, Any]) -> dict[str, Any]:
        session = self._require_web_session()
        requested_ids = self._requested_store_ids(payload)
        if not self._store_login_lock.acquire(blocking=False):
            return {
                "success": False,
                "busy": True,
                "login_confirmed": False,
                "requested_store_ids": requested_ids,
                "message": "网页登录任务正在进行，请等待当前交互浏览器关闭",
            }
        try:
            stores = []
            missing_ids = []
            for store_id in requested_ids:
                store = self.db.get_store(store_id)
                if store is None:
                    missing_ids.append(store_id)
                else:
                    stores.append(store)
            if not stores:
                raise ValueError(
                    "所选店铺均不存在：" + ", ".join(map(str, missing_ids))
                )
            primary = stores[0]
            result = dict(
                session.interactive_login(
                    primary,
                    allowed_store_ids={store.id for store in stores},
                )
            )
        finally:
            self._store_login_lock.release()
        matched_ids = {
            int(store_id)
            for store_id in result.get("matched_store_ids", [])
            if str(store_id).isdigit()
        }
        primary_id = str(result.get("matched_store_id") or "").strip()
        if primary_id.isdigit():
            matched_ids.add(int(primary_id))
        requested_logged_in_ids = [
            store_id for store_id in requested_ids if store_id in matched_ids
        ]
        not_logged_in_ids = [
            store_id for store_id in requested_ids if store_id not in matched_ids
        ]
        result["requested_store_id"] = primary.id
        result["requested_store_ids"] = requested_ids
        result["requested_store_logged_in"] = (
            not_logged_in_ids == []
        )
        result["requested_logged_in_ids"] = requested_logged_in_ids
        result["not_logged_in_store_ids"] = not_logged_in_ids
        result["missing_store_ids"] = missing_ids
        result["requested_logged_in_count"] = len(requested_logged_in_ids)
        result["matched_store_ids"] = sorted(matched_ids)
        result["saved_store_count"] = len(matched_ids)
        result["background_followup"] = (
            self._schedule_new_store_followup(
                sorted(matched_ids),
                reason="扫码登录完成",
            )
            if payload.get("background_refresh_enabled", True)
            else {"scheduled": False, "store_count": 0}
        )
        return result

    def _import_store_cookie(self, payload: dict[str, Any]) -> dict[str, Any]:
        session = self._require_web_session()
        store = self._require_store(self._store_id(payload))
        header = str(payload.get("cookie_header") or "").strip()
        if not header:
            raise ValueError("Cookie Header 不能为空")
        if len(header) > 1_000_000:
            raise ValueError("Cookie Header 过长")
        try:
            result = session.import_cookie_header(store, header)
        except Exception as exc:
            self._audit_store_credential(
                store.id,
                "import_cookie",
                f"店铺网页 Cookie 导入失败：{store.name}",
                status="failed",
                detail={"error": str(exc)[:1000]},
            )
            raise
        public = dict(result) if isinstance(result, dict) else {}
        public.pop("cookies", None)
        public.pop("cookie_header", None)
        self._audit_store_credential(
            store.id,
            "import_cookie",
            f"店铺网页 Cookie 导入成功：{store.name}",
            detail={
                key: value
                for key, value in public.items()
                if key not in {"cookie", "cookies", "token", "secret"}
            },
        )
        refreshed = self.db.get_store(store.id)
        if payload.get("background_refresh_enabled", True):
            self._schedule_new_store_followup(
                [store.id],
                reason="网页 Cookie 导入完成",
            )
        return {
            "store_id": store.id,
            "status": str(getattr(refreshed, "web_status", "") or ""),
            "detail": str(getattr(refreshed, "web_status_detail", "") or ""),
            "result": public,
        }

    def _open_store_backend(self, payload: dict[str, Any]) -> dict[str, Any]:
        session = self._require_web_session()
        store = self._require_store(self._store_id(payload))
        session.open_official_backend(store)
        return {"opened": store.id}

    def _clear_web_cache(self, _payload: dict[str, Any]) -> dict[str, Any]:
        session = self._require_web_session()
        removed = int(session.cleanup_cache() or 0)
        return {"removed": removed}

    def _audit_store_credential(
        self,
        store_id: int,
        operation: str,
        message: str,
        *,
        status: str = "success",
        detail: dict[str, Any] | None = None,
    ) -> None:
        log_id = self.db.start_task_log(
            "stores",
            operation,
            message,
            store_id=store_id,
            detail=json.dumps(
                detail or {},
                ensure_ascii=False,
            ),
        )
        self.db.finish_task_log(log_id, status, message)

    def _recover_store_credential(
        self,
        store: Any,
        service: Any,
    ) -> tuple[Any, dict[str, Any]]:
        state = self.db.get_store_credential_state(store.id)
        if state.get("state") != "invalid":
            return store, {"action": "none"}
        pending = self.db.list_store_credential_candidates(
            store.id,
            states=("pending", "validating"),
        )
        if not pending:
            return store, {"action": "none"}

        _current_store, current_secret = self.db.get_store_secret(store.id)
        if current_secret:
            try:
                service.validate_store_credential(store, current_secret)
            except Exception:
                pass
            else:
                self.db.set_store_credential_state(
                    store.id,
                    "valid",
                    mark_probe=True,
                )
                superseded = self.db.supersede_store_credential_candidates(
                    store.id
                )
                self._audit_store_credential(
                    store.id,
                    "credential_candidate_superseded",
                    "原密钥已恢复可用，放弃候选替换",
                    detail={"superseded_count": superseded},
                )
                return self.db.get_store(store.id) or store, {
                    "action": "superseded",
                    "count": superseded,
                }

        candidate = self.db.claim_pending_credential_candidate(store.id)
        if candidate is None:
            return store, {"action": "deferred"}
        candidate_meta, candidate_secret = (
            self.db.get_store_credential_candidate_secret(
                int(candidate["id"])
            )
        )
        try:
            service.validate_store_credential(store, candidate_secret)
        except Exception as exc:
            state_name, code, safe_message = self._credential_failure(exc)
            if state_name == "invalid":
                self.db.release_credential_candidate(
                    int(candidate_meta["id"]),
                    state="invalid",
                    error_code=code,
                    error_message=safe_message,
                )
                self._audit_store_credential(
                    store.id,
                    "credential_replace_rejected",
                    "新密钥被平台明确拒绝，原记录和业务数据保持不动",
                    status="failed",
                    detail={
                        "candidate_id": int(candidate_meta["id"]),
                        "error_code": code,
                        "error_message": safe_message,
                    },
                )
                return store, {
                    "action": "rejected",
                    "error_code": code,
                    "error_message": safe_message,
                }
            self.db.release_credential_candidate(
                int(candidate_meta["id"]),
                state="pending",
                error_code=code,
                error_message=safe_message,
            )
            self._audit_store_credential(
                store.id,
                "credential_replace_deferred",
                "候选密钥验证遇到临时网络或平台错误，后续刷新再试",
                status="partial",
                detail={
                    "candidate_id": int(candidate_meta["id"]),
                    "error_code": code,
                    "error_message": safe_message,
                },
            )
            return store, {
                "action": "deferred",
                "error_code": code,
                "error_message": safe_message,
            }
        applied = self.db.apply_store_credential_candidate(
            int(candidate_meta["id"])
        )
        self._audit_store_credential(
            store.id,
            "credential_replace_success",
            "新密钥验证成功，已在同一店铺记录上原子替换",
            detail={
                "candidate_id": int(candidate_meta["id"]),
                "provider": "wechat_shop",
            },
        )
        return applied["store"], {
            "action": "recovered",
            "candidate_id": int(candidate_meta["id"]),
        }

    def _record_store_credential_probe(
        self,
        store: Any,
        metrics: dict[str, Any],
    ) -> None:
        code = str(metrics.get("_credential_error_code") or "").strip()
        if code:
            self.db.set_store_credential_state(
                store.id,
                "invalid",
                error_code=code,
                error_message=_safe_error_message(
                    metrics.get("_credential_error_message") or ""
                ),
                mark_probe=True,
            )
        elif metrics.get("_credential_probe_ok"):
            self.db.set_store_credential_state(
                store.id,
                "valid",
                mark_probe=True,
            )

    def _refresh_store_with_credential(
        self,
        store: Any,
        service: Any,
    ) -> tuple[Any, dict[str, Any]]:
        active_store, credential = self._recover_store_credential(
            store,
            service,
        )
        probe = getattr(self.web_session, "probe", None)
        if callable(probe):
            try:
                status, detail = probe(active_store)
            except Exception as exc:
                status = "检测异常"
                detail = f"登录状态检测失败：{str(exc)[:180]}"
                self.db.update_web_status(
                    active_store.id,
                    status,
                    detail,
                )
            credential["web_status"] = status
            credential["web_status_detail"] = detail
            active_store = self.db.get_store(active_store.id) or active_store
        try:
            metrics = service.refresh_store_metrics(active_store)
        except Exception as exc:
            self._record_store_score_refresh_failure(
                active_store.id,
                str(exc),
            )
            raise
        if isinstance(metrics, dict):
            self._record_store_credential_probe(active_store, metrics)
            self._record_store_score_refresh_result(
                active_store.id,
                metrics,
            )
        scan_catalog = getattr(service, "scan_product_catalog", None)
        if callable(scan_catalog):
            try:
                scan_catalog(active_store)
            except Exception as exc:
                credential["catalog_error"] = _safe_error_message(
                    str(exc)
                )
        return active_store, credential

    def _refresh_stores(self, payload: dict[str, Any]) -> dict[str, Any]:
        service = self._require_product_service()
        requested_ids = {
            int(value)
            for value in payload.get("store_ids", [])
            if str(value).isdigit()
        }
        stores = [
            store
            for store in self.db.list_stores()
            if not requested_ids or store.id in requested_ids
        ]
        if not stores:
            return {"refreshed": 0, "succeeded": 0, "errors": []}
        self._record_store_score_baseline({store.id for store in stores})

        def refresh_one(store):
            return self._refresh_store_with_credential(store, service)

        succeeded = 0
        errors: list[str] = []
        credential_actions: list[dict[str, Any]] = []
        workers = max(1, min(6, len(stores)))
        with ThreadPoolExecutor(
            max_workers=workers,
            thread_name_prefix="webui-store-refresh",
        ) as executor:
            futures = {
                executor.submit(refresh_one, store): store
                for store in stores
            }
            for future in as_completed(futures):
                store = futures[future]
                try:
                    _active_store, credential = future.result()
                    credential_actions.append(credential)
                except Exception as exc:
                    errors.append(f"{store.name}: {exc}")
                else:
                    succeeded += 1
        return {
            "refreshed": len(stores),
            "succeeded": succeeded,
            "errors": errors,
            "credential_actions": credential_actions,
        }

    def _start_store_refresh_monitor(
        self,
        job_id: str,
        monitored_job_ids: tuple[str, ...],
        store_ids: tuple[int, ...],
        generation: int,
    ) -> threading.Thread:
        requested_ids = set(store_ids)
        terminal_statuses = {
            "completed",
            "partial",
            "failed",
            "cancelled",
        }
        started_at = time.monotonic()

        def update(**changes: Any) -> None:
            with self._store_jobs_lock:
                if not self._job_update_allowed(generation):
                    return
                job = self._store_jobs.get(job_id)
                if job is not None:
                    job.update(changes)

        def worker() -> None:
            update(status="running", message="正在联合监控后台刷新任务…")
            while True:
                if not self._job_update_allowed(generation):
                    return
                with self._store_jobs_lock:
                    monitored = [
                        dict(self._store_jobs.get(monitored_job_id) or {})
                        for monitored_job_id in monitored_job_ids
                    ]
                missing_job_ids = [
                    monitored_job_id
                    for monitored_job_id, job in zip(
                        monitored_job_ids,
                        monitored,
                    )
                    if not job
                ]
                successful_store_ids = sorted({
                    int(store_id)
                    for job in monitored
                    for store_id in job.get("successful_store_ids") or ()
                    if int(store_id) in requested_ids
                })
                failed_store_ids = sorted({
                    int(store_id)
                    for job in monitored
                    for store_id in job.get("failed_store_ids") or ()
                    if int(store_id) in requested_ids
                })
                cancelled_store_ids = sorted({
                    int(store_id)
                    for job in monitored
                    if str(job.get("status") or "") == "cancelled"
                    for store_id in job.get("store_ids") or ()
                    if (
                        int(store_id) in requested_ids
                        and int(store_id) not in successful_store_ids
                        and int(store_id) not in failed_store_ids
                    )
                })
                current = len(
                    set(successful_store_ids)
                    | set(failed_store_ids)
                    | set(cancelled_store_ids)
                )
                errors = [
                    str(error)
                    for job in monitored
                    for error in job.get("errors") or ()
                ]
                credential_totals = {
                    key: sum(
                        int(job.get(key) or 0)
                        for job in monitored
                    )
                    for key in (
                        "credential_succeeded",
                        "credential_superseded",
                        "credential_failed",
                        "credential_deferred",
                    )
                }
                credential_actions = [
                    dict(action)
                    for job in monitored
                    for action in job.get("credential_actions") or ()
                    if int(action.get("store_id") or 0) in requested_ids
                ]
                settled = all(
                    str(job.get("status") or "") in terminal_statuses
                    for job in monitored
                    if job
                ) and not missing_job_ids
                update(
                    current=current,
                    total=len(store_ids),
                    succeeded=len(successful_store_ids),
                    failed=len(failed_store_ids),
                    cancelled=len(cancelled_store_ids),
                    successful_store_ids=successful_store_ids,
                    failed_store_ids=failed_store_ids,
                    cancelled_store_ids=cancelled_store_ids,
                    credential_actions=credential_actions,
                    **credential_totals,
                    message=(
                        f"联合刷新进行中（{current}/{len(store_ids)} 家）"
                        if not settled
                        else "正在汇总后台刷新结果…"
                    ),
                )
                if settled:
                    break
                time.sleep(0.1)

            statuses = [
                str(job.get("status") or "failed")
                for job in monitored
            ]
            if missing_job_ids:
                statuses.extend("failed" for _ in missing_job_ids)
                errors.extend(
                    f"后台刷新任务不存在：{monitored_job_id}"
                    for monitored_job_id in missing_job_ids
                )
            unaccounted_store_ids = sorted(
                requested_ids
                - set(successful_store_ids)
                - set(failed_store_ids)
                - set(cancelled_store_ids)
            )
            all_cancelled = bool(statuses) and all(
                status == "cancelled"
                for status in statuses
            )
            if unaccounted_store_ids and not all_cancelled:
                failed_store_ids = sorted({
                    *failed_store_ids,
                    *unaccounted_store_ids,
                })
                errors.extend(
                    f"店铺 {store_id} 未返回刷新结果"
                    for store_id in unaccounted_store_ids
                )
            if (
                statuses
                and all(status == "completed" for status in statuses)
                and not failed_store_ids
            ):
                status = "completed"
            elif (
                not successful_store_ids
                and all_cancelled
            ):
                status = "cancelled"
            elif (
                not successful_store_ids
                and (
                    failed_store_ids
                    or any(
                        status in {"failed", "cancelled"}
                        for status in statuses
                    )
                )
            ):
                status = "failed"
            else:
                status = "partial"
            update(
                status=status,
                current=len(
                    set(successful_store_ids)
                    | set(failed_store_ids)
                    | set(cancelled_store_ids)
                ),
                total=len(store_ids),
                succeeded=len(successful_store_ids),
                failed=len(failed_store_ids),
                cancelled=len(cancelled_store_ids),
                successful_store_ids=successful_store_ids,
                failed_store_ids=failed_store_ids,
                cancelled_store_ids=cancelled_store_ids,
                errors=errors,
                message=(
                    f"联合刷新结束：成功 {len(successful_store_ids)} 家，"
                    f"失败 {len(failed_store_ids)} 家；"
                    f"后台任务 {len(monitored_job_ids)} 个"
                ),
                elapsed_seconds=round(
                    time.monotonic() - started_at,
                    3,
                ),
            )

        monitor_thread = threading.Thread(
            target=worker,
            name=f"webui-store-monitor-{job_id[:8]}",
            daemon=True,
        )
        with self._store_jobs_lock:
            self._store_refresh_workers[job_id] = monitor_thread
        monitor_thread.start()
        return monitor_thread

    def _start_store_refresh(self, payload: dict[str, Any]) -> dict[str, Any]:
        service = self._require_product_service()
        requested_ids = {
            int(value)
            for value in payload.get("store_ids", [])
            if str(value).isdigit()
        }
        stores = [
            store
            for store in self.db.list_stores()
            if not requested_ids or store.id in requested_ids
        ]
        if not stores:
            raise ValueError("没有可刷新的店铺")
        store_ids = tuple(sorted(store.id for store in stores))
        job_id = uuid.uuid4().hex
        generation = self._current_generation()
        owner = self._job_owner_fields()
        account_id = str(owner.get("account_id") or "")
        monitor_job_id = ""
        monitored_job_ids: tuple[str, ...] = ()
        with self._store_jobs_lock:
            covered_store_ids: set[int] = set()
            overlapping_job_ids: list[str] = []
            for existing in self._store_jobs.values():
                existing_generation = existing.get("generation")
                if (
                    not self._job_is_active(existing)
                    or existing_generation is None
                    or int(existing_generation) != generation
                    or str(existing.get("account_id") or "") != account_id
                ):
                    continue
                existing_store_ids = {
                    int(value)
                    for value in existing.get("store_ids") or ()
                }
                if existing_store_ids & set(store_ids):
                    overlapping_job_ids.append(
                        str(existing["job_id"])
                    )
                covered_store_ids.update(existing_store_ids)
                if set(store_ids).issubset(existing_store_ids):
                    return {
                        "job_id": existing["job_id"],
                        "already_running": True,
                        "total": int(existing.get("total") or len(stores)),
                        "store_ids": list(existing_store_ids),
                    }
            stores = [
                store for store in stores
                if store.id not in covered_store_ids
            ]
            monitored_job_ids = tuple(
                dict.fromkeys(overlapping_job_ids)
            )
            if not stores:
                monitor_job_id = job_id
                self._store_jobs[job_id] = {
                    "job_id": job_id,
                    **owner,
                    "store_ids": store_ids,
                    "worker_count": len(monitored_job_ids),
                    "status": "starting",
                    "current": 0,
                    "total": len(store_ids),
                    "succeeded": 0,
                    "failed": 0,
                    "successful_store_ids": [],
                    "failed_store_ids": [],
                    "message": "正在准备联合监控后台刷新任务…",
                    "errors": [],
                    "credential_succeeded": 0,
                    "credential_superseded": 0,
                    "credential_failed": 0,
                    "credential_deferred": 0,
                    "credential_actions": [],
                    "aggregate": True,
                    "monitored_job_ids": list(monitored_job_ids),
                }
            else:
                if monitored_job_ids:
                    monitor_job_id = uuid.uuid4().hex
                    self._store_jobs[monitor_job_id] = {
                        "job_id": monitor_job_id,
                        **owner,
                        "store_ids": store_ids,
                        "worker_count": len(monitored_job_ids) + 1,
                        "status": "starting",
                        "current": 0,
                        "total": len(store_ids),
                        "succeeded": 0,
                        "failed": 0,
                        "successful_store_ids": [],
                        "failed_store_ids": [],
                        "message": "正在准备联合监控后台刷新任务…",
                        "errors": [],
                        "credential_succeeded": 0,
                        "credential_superseded": 0,
                        "credential_failed": 0,
                        "credential_deferred": 0,
                        "credential_actions": [],
                        "aggregate": True,
                        "monitored_job_ids": list(monitored_job_ids),
                    }
            if stores:
                refresh_store_ids = tuple(
                    sorted(store.id for store in stores)
                )
                workers = max(1, min(8, len(stores)))
                self._store_jobs[job_id] = {
                    "job_id": job_id,
                    **owner,
                    "store_ids": refresh_store_ids,
                    "worker_count": workers,
                    "status": "starting",
                    "current": 0,
                    "total": len(stores),
                    "succeeded": 0,
                    "failed": 0,
                    "successful_store_ids": [],
                    "failed_store_ids": [],
                    "message": "正在准备刷新店铺…",
                    "errors": [],
                    "credential_succeeded": 0,
                    "credential_superseded": 0,
                    "credential_failed": 0,
                    "credential_deferred": 0,
                    "credential_actions": [],
                }
                if monitor_job_id and monitor_job_id != job_id:
                    self._store_jobs[monitor_job_id][
                        "monitored_job_ids"
                    ].append(job_id)
        if not stores:
            self._start_store_refresh_monitor(
                monitor_job_id,
                monitored_job_ids,
                store_ids,
                generation,
            )
            return {
                "job_id": monitor_job_id,
                "already_running": True,
                "total": len(store_ids),
                "store_ids": list(store_ids),
                "monitored_job_ids": list(monitored_job_ids),
            }
        self._record_store_score_baseline({store.id for store in stores})
        started_at = time.monotonic()

        def update(**changes: Any) -> None:
            with self._store_jobs_lock:
                if not self._job_update_allowed(generation):
                    return
                job = self._store_jobs.get(job_id)
                if job is not None:
                    job.update(changes)

        def worker() -> None:
            update(status="running", message=f"正在刷新店铺数据（0/{len(stores)}）")
            completed = 0
            succeeded = 0
            errors: list[str] = []
            successful_store_ids: list[int] = []
            failed_store_ids: list[int] = []
            credential_succeeded = 0
            credential_superseded = 0
            credential_failed = 0
            credential_deferred = 0
            credential_actions: list[dict[str, Any]] = []
            with ThreadPoolExecutor(
                max_workers=workers,
                thread_name_prefix="webui-store-refresh-progress",
            ) as executor:
                futures = {
                    executor.submit(
                        self._refresh_store_with_credential,
                        store,
                        service,
                    ): store
                    for store in stores
                }
                for future in as_completed(futures):
                    if not self._job_update_allowed(generation):
                        break
                    store = futures[future]
                    completed += 1
                    try:
                        _active_store, credential = future.result()
                        if credential.get("catalog_error"):
                            raise ValueError(
                                "商品目录扫描失败："
                                + str(credential.get("catalog_error"))
                            )
                        action = str(credential.get("action") or "none")
                        if action == "recovered":
                            credential_succeeded += 1
                        elif action == "superseded":
                            credential_superseded += 1
                        elif action == "rejected":
                            credential_failed += 1
                        elif action == "deferred":
                            credential_deferred += 1
                        if action != "none":
                            credential_actions.append(
                                {
                                    "store_id": store.id,
                                    "store_name": store.name,
                                    **credential,
                                }
                            )
                    except Exception as exc:
                        errors.append(f"{store.name}: {exc}")
                        failed_store_ids.append(store.id)
                        update(
                            current=completed,
                            succeeded=succeeded,
                            failed=len(errors),
                            successful_store_ids=successful_store_ids,
                            failed_store_ids=failed_store_ids,
                            credential_succeeded=credential_succeeded,
                            credential_superseded=credential_superseded,
                            credential_failed=credential_failed,
                            credential_deferred=credential_deferred,
                            credential_actions=credential_actions,
                            message=(
                                f"正在刷新  {store.name}  "
                                f"({completed}/{len(stores)})"
                            ),
                        )
                    else:
                        succeeded += 1
                        successful_store_ids.append(store.id)
                        update(
                            current=completed,
                            succeeded=succeeded,
                            failed=len(errors),
                            successful_store_ids=successful_store_ids,
                            failed_store_ids=failed_store_ids,
                            credential_succeeded=credential_succeeded,
                            credential_superseded=credential_superseded,
                            credential_failed=credential_failed,
                            credential_deferred=credential_deferred,
                            credential_actions=credential_actions,
                            message=(
                                f"正在刷新  {store.name}  "
                                f"({completed}/{len(stores)})"
                            ),
                        )
            update(
                status="completed" if not errors else "partial",
                current=len(stores),
                total=len(stores),
                succeeded=succeeded,
                failed=len(errors),
                successful_store_ids=successful_store_ids,
                failed_store_ids=failed_store_ids,
                credential_succeeded=credential_succeeded,
                credential_superseded=credential_superseded,
                credential_failed=credential_failed,
                credential_deferred=credential_deferred,
                credential_actions=credential_actions,
                message=(
                    f"刷新完成：数据成功 {succeeded}，失败 {len(errors)}；"
                    f"凭据恢复 {credential_succeeded}，恢复放弃 "
                    f"{credential_superseded}，拒绝 {credential_failed}，"
                    f"延后 {credential_deferred}"
                    if errors
                    else (
                        f"刷新完成：成功刷新 {succeeded} 家店铺；"
                        f"凭据恢复 {credential_succeeded}，恢复放弃 "
                        f"{credential_superseded}，延后 {credential_deferred}"
                    )
                ),
                errors=errors,
                elapsed_seconds=round(time.monotonic() - started_at, 3),
            )

        refresh_thread = threading.Thread(
            target=worker,
            name=f"webui-store-refresh-{job_id[:8]}",
            daemon=True,
        )
        with self._store_jobs_lock:
            self._store_refresh_workers[job_id] = refresh_thread
        refresh_thread.start()
        if monitor_job_id:
            with self._store_jobs_lock:
                monitor_children = tuple(
                    self._store_jobs[monitor_job_id].get(
                        "monitored_job_ids"
                    )
                    or ()
                )
            self._start_store_refresh_monitor(
                monitor_job_id,
                monitor_children,
                store_ids,
                generation,
            )
            return {
                "job_id": monitor_job_id,
                "already_running": True,
                "total": len(store_ids),
                "store_ids": list(store_ids),
                "monitored_job_ids": list(monitor_children),
            }
        return {
            "job_id": job_id,
            "already_running": False,
            "total": len(stores),
            "store_ids": list(store_ids),
        }

    def wait_for_store_refresh(
        self,
        job_id: str,
        timeout: float = 10.0,
    ) -> bool:
        with self._store_jobs_lock:
            thread = self._store_refresh_workers.get(str(job_id))
        if thread is not None:
            thread.join(max(0.0, float(timeout)))
            return not thread.is_alive()
        return True

    def _store_refresh_status(self, payload: dict[str, Any]) -> dict[str, Any]:
        job_id = str(payload.get("job_id") or "")
        with self._store_jobs_lock:
            job = self._store_jobs.get(job_id)
            if job is None:
                raise ValueError("店铺刷新任务不存在")
            self._require_job_owner(job, "店铺刷新任务")
            return {
                key: value
                for key, value in job.items()
                if not key.startswith("_")
            }

    def _wait_refresh_job(
        self,
        jobs: dict[str, dict[str, Any]],
        lock: threading.Lock,
        job_id: str,
        *,
        timeout: float = 300.0,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + max(1.0, timeout)
        while time.monotonic() < deadline:
            with lock:
                job = dict(jobs.get(job_id) or {})
            if job.get("status") in {
                "completed",
                "partial",
                "failed",
                "cancelled",
            }:
                return job
            time.sleep(0.1)
        with lock:
            job = dict(jobs.get(job_id) or {})
        job["status"] = "timeout"
        job["message"] = "后台刷新等待超时"
        return job

    def _write_startup_refresh_status(self, **changes: Any) -> dict[str, Any]:
        try:
            current = json.loads(
                self.db.get_setting("startup_refresh_status", "{}")
            )
        except (TypeError, ValueError, json.JSONDecodeError):
            current = {}
        status = current if isinstance(current, dict) else {}
        status.update(changes)
        status["updated_at"] = datetime.now().isoformat(timespec="seconds")
        self.db.set_setting(
            "startup_refresh_status",
            json.dumps(status, ensure_ascii=False),
        )
        return status

    def _start_startup_refresh(self, payload: dict[str, Any]) -> dict[str, Any]:
        requested = payload.get("enabled")
        enabled = requested if isinstance(requested, dict) else {}
        enabled = {
            key: bool(enabled.get(key, True))
            for key in ("stores", "orders", "products", "violations")
        }
        with self._startup_refresh_lock:
            if (
                self._startup_refresh_thread is not None
                and self._startup_refresh_thread.is_alive()
            ):
                return {
                    "started": False,
                    "already_running": True,
                    "run_id": self._startup_refresh_status({}).get("run_id", ""),
                }
            run_id = uuid.uuid4().hex

            def worker() -> None:
                stages: dict[str, Any] = {}
                errors: list[str] = []
                all_stores = [
                    store for store in self.db.list_stores()
                    if store.app_id
                ]
                logged_in_stores = real_logged_in_stores(all_stores)
                self._write_startup_refresh_status(
                    run_id=run_id,
                    status="running",
                    stage="violations",
                    enabled=enabled,
                    started_at=datetime.now().isoformat(timespec="seconds"),
                    stages=stages,
                    errors=errors,
                )
                log_event(
                    "startup.refresh.started",
                    run_id=run_id,
                    enabled=enabled,
                    store_count=len(all_stores),
                )

                def run_stage(name: str, operation: Callable[[], Any]) -> None:
                    self._write_startup_refresh_status(
                        status="running",
                        stage=name,
                        stages=stages,
                        errors=errors,
                    )
                    started = time.monotonic()
                    try:
                        value = operation()
                    except Exception as exc:
                        stages[name] = {
                            "status": "failed",
                            "error": str(exc)[:1000],
                        }
                        errors.append(f"{name}: {exc}")
                    else:
                        if name in {"stores", "orders", "products"}:
                            job_ref = (
                                str(value.get("job_id") or "")
                                if isinstance(value, dict)
                                else ""
                            )
                            self._publish_refresh_analytics(
                                name,
                                {},
                                value if isinstance(value, dict) else {},
                                publish_key=(
                                    f"{name}:{job_ref}"
                                    if job_ref
                                    else f"startup:{run_id}:{name}"
                                ),
                            )
                        violation_stage_status = ""
                        if isinstance(value, dict):
                            if name == "orders":
                                status_changes = value.get("status_changes") or []
                                value = {
                                    key: value.get(key)
                                    for key in (
                                        "job_id",
                                        "status",
                                        "current",
                                        "total",
                                        "succeeded",
                                        "failed",
                                        "updated",
                                        "message",
                                        "errors",
                                    )
                                }
                                value["status_change_count"] = len(
                                    status_changes
                                )
                            elif name == "violations":
                                violation_stage_status = str(
                                    value.get("stage_status") or ""
                                )
                                value = {
                                    key: value.get(key)
                                    for key in (
                                        "refreshed",
                                        "succeeded",
                                        "errors",
                                        "cloud_counterfeit",
                                    )
                                }
                        if (
                            name == "violations"
                            and isinstance(value, dict)
                        ):
                            stage_status = str(
                                violation_stage_status
                                or "completed"
                            )
                            stage_errors = [
                                str(error)
                                for error in value.get("errors") or []
                                if str(error).strip()
                            ]
                            errors.extend(
                                f"{name}: {error}"
                                for error in stage_errors
                            )
                        else:
                            stage_status = (
                                "completed"
                                if not (
                                    isinstance(value, dict)
                                    and value.get("errors")
                                )
                                else "partial"
                            )
                        stages[name] = {
                            "status": stage_status,
                            "result": value,
                        }
                    stages[name]["duration_ms"] = int(
                        (time.monotonic() - started) * 1000
                    )

                if enabled["violations"]:
                    def sync_violations() -> dict[str, Any]:
                        if not logged_in_stores:
                            return {
                                "stage_status": "failed",
                                "refreshed": 0,
                                "succeeded": 0,
                                "errors": [
                                    "没有可同步的已登录真实店铺"
                                ],
                                "cloud_counterfeit": {},
                            }
                        result = self._refresh_violations({})
                        errors_value = [
                            str(error)
                            for error in result.get("errors") or []
                            if str(error).strip()
                        ]
                        cloud = result.get("cloud_counterfeit")
                        cloud_failed = (
                            not isinstance(cloud, dict)
                            or bool(cloud.get("error"))
                            or bool(cloud.get("skipped"))
                        )
                        if cloud_failed:
                            cloud_error = (
                                str(cloud.get("error") or "").strip()
                                if isinstance(cloud, dict)
                                else ""
                            )
                            errors_value.append(
                                "云端售假库同步失败"
                                + (f"：{cloud_error}" if cloud_error else "")
                            )
                        succeeded = int(
                            result.get("succeeded") or 0
                        )
                        if cloud_failed:
                            stage_status = "failed"
                        elif not errors_value:
                            stage_status = "completed"
                        elif succeeded > 0:
                            stage_status = "partial"
                        else:
                            stage_status = "failed"
                        return {
                            "stage_status": stage_status,
                            "refreshed": int(
                                result.get("refreshed") or 0
                            ),
                            "succeeded": succeeded,
                            "errors": errors_value,
                            "cloud_counterfeit": (
                                cloud if isinstance(cloud, dict) else {}
                            ),
                        }

                    run_stage("violations", sync_violations)

                store_ids = [store.id for store in all_stores]
                if enabled["stores"] and store_ids:
                    def refresh_stores() -> dict[str, Any]:
                        job = self._start_store_refresh(
                            {"store_ids": store_ids}
                        )
                        return self._wait_refresh_job(
                            self._store_jobs,
                            self._store_jobs_lock,
                            str(job["job_id"]),
                        )

                    run_stage("stores", refresh_stores)

                if enabled["orders"] and store_ids:
                    def refresh_orders() -> dict[str, Any]:
                        job = self._start_order_quick_refresh({})
                        return self._wait_refresh_job(
                            self._order_jobs,
                            self._order_jobs_lock,
                            str(job["job_id"]),
                        )

                    run_stage("orders", refresh_orders)

                if enabled["products"] and store_ids:
                    def refresh_products() -> dict[str, Any]:
                        results: list[dict[str, Any]] = []
                        job_ids: list[tuple[str, int]] = []
                        for store in all_stores:
                            try:
                                job = self._start_product_refresh(
                                    {
                                        "store_id": store.id,
                                        "tab_keys": [
                                            "sales",
                                            "offline",
                                            "recycle",
                                        ],
                                    }
                                )
                            except Exception as exc:
                                results.append(
                                    {
                                        "store_id": store.id,
                                        "status": "failed",
                                        "error": str(exc),
                                    }
                                )
                                continue
                            job_ids.append((str(job["job_id"]), store.id))
                        for job_id, store_id in job_ids:
                            result = self._wait_refresh_job(
                                self._product_jobs,
                                self._product_jobs_lock,
                                job_id,
                            )
                            results.append(
                                {
                                    "store_id": store_id,
                                    "status": result.get("status", ""),
                                    "message": result.get("message", ""),
                                    "errors": result.get("errors", []),
                                }
                            )
                        failures = [
                            row for row in results
                            if row.get("status") not in {"completed"}
                        ]
                        return {
                            "stores": results,
                            "errors": failures,
                        }

                    run_stage("products", refresh_products)

                enabled_stage_statuses = [
                    str(stage.get("status") or "")
                    for name, stage in stages.items()
                    if enabled.get(name)
                ]
                if not errors:
                    status = "completed"
                elif (
                    enabled_stage_statuses
                    and all(
                        value in {"failed", "timeout"}
                        for value in enabled_stage_statuses
                    )
                ):
                    status = "failed"
                else:
                    status = "partial"
                self._write_startup_refresh_status(
                    run_id=run_id,
                    status=status,
                    stage="finished",
                    finished_at=datetime.now().isoformat(timespec="seconds"),
                    stages=stages,
                    errors=errors,
                )
                log_event(
                    "startup.refresh.finished",
                    run_id=run_id,
                    status=status,
                    errors=errors[:20],
                )

            self._write_startup_refresh_status(
                run_id=run_id,
                status="starting",
                stage="queued",
                enabled=enabled,
                stages={},
                errors=[],
            )
            thread = threading.Thread(
                target=worker,
                name=f"webui-startup-refresh-{run_id[:8]}",
                daemon=True,
            )
            self._startup_refresh_thread = thread
            thread.start()
        return {
            "started": True,
            "already_running": False,
            "run_id": run_id,
        }

    def _startup_refresh_status(
        self,
        _payload: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            status = json.loads(
                self.db.get_setting("startup_refresh_status", "{}")
            )
        except (TypeError, ValueError, json.JSONDecodeError):
            status = {}
        result = dict(status) if isinstance(status, dict) else {}
        result["thread_alive"] = bool(
            self._startup_refresh_thread is not None
            and self._startup_refresh_thread.is_alive()
        )
        return result

    def _schedule_new_store_followup(
        self,
        store_ids: list[int],
        *,
        reason: str,
    ) -> dict[str, Any]:
        ids = {
            int(store_id)
            for store_id in store_ids
            if int(store_id) > 0
            and self.db.get_store(int(store_id)) is not None
        }
        if not ids:
            return {"scheduled": False, "store_count": 0}
        with self._new_store_followup_lock:
            self._new_store_followup_pending.update(ids)
            if (
                self._new_store_followup_thread is not None
                and self._new_store_followup_thread.is_alive()
            ):
                return {
                    "scheduled": True,
                    "already_running": True,
                    "store_count": len(ids),
                }

            def worker() -> None:
                while True:
                    with self._new_store_followup_lock:
                        pending = sorted(self._new_store_followup_pending)
                        self._new_store_followup_pending.clear()
                    if not pending:
                        break
                    results: list[dict[str, Any]] = []
                    for store_id in pending:
                        store = self.db.get_store(store_id)
                        if store is None:
                            continue
                        result: dict[str, Any] = {
                            "store_id": store_id,
                            "store_name": store.name,
                            "reason": reason,
                        }
                        try:
                            violation_result = self._refresh_violations(
                                {"store_id": store_id}
                            )
                            cloud = (
                                violation_result.get("cloud_counterfeit")
                                if isinstance(violation_result, dict)
                                else None
                            )
                            failures = (
                                list(violation_result.get("errors") or [])
                                if isinstance(violation_result, dict)
                                else []
                            )
                            if (
                                failures
                                or not isinstance(cloud, dict)
                                or cloud.get("error")
                                or cloud.get("skipped")
                            ):
                                cloud_error = (
                                    str(cloud.get("error") or "").strip()
                                    if isinstance(cloud, dict)
                                    else "缺少云端售假库状态"
                                )
                                result["violations"] = (
                                    "failed: "
                                    + "；".join(
                                        [
                                            str(value)
                                            for value in failures
                                            if str(value).strip()
                                        ]
                                        + [cloud_error]
                                    )
                                )[:500]
                            else:
                                result["violations"] = "completed"
                        except Exception as exc:
                            result["violations"] = f"failed: {exc}"[:500]
                        for domain, start, jobs, lock in (
                            (
                                "stores",
                                lambda: self._start_store_refresh(
                                    {"store_ids": [store_id]}
                                ),
                                self._store_jobs,
                                self._store_jobs_lock,
                            ),
                            (
                                "orders",
                                lambda: self._start_order_quick_refresh(
                                    {"store_id": store_id}
                                ),
                                self._order_jobs,
                                self._order_jobs_lock,
                            ),
                        ):
                            try:
                                job = start()
                                job_status = self._wait_refresh_job(
                                    jobs,
                                    lock,
                                    str(job["job_id"]),
                                )
                                result[domain] = {
                                    "status": job_status.get("status"),
                                    "message": job_status.get("message", ""),
                                }
                            except Exception as exc:
                                result[domain] = {
                                    "status": "failed",
                                    "error": str(exc)[:500],
                                }
                        try:
                            job = self._start_product_refresh(
                                {
                                    "store_id": store_id,
                                    "tab_keys": [
                                        "sales",
                                        "offline",
                                        "recycle",
                                    ],
                                }
                            )
                            product_status = self._wait_refresh_job(
                                self._product_jobs,
                                self._product_jobs_lock,
                                str(job["job_id"]),
                            )
                            result["products"] = {
                                "status": product_status.get("status"),
                                "message": product_status.get("message", ""),
                            }
                        except Exception as exc:
                            result["products"] = {
                                "status": "failed",
                                "error": str(exc)[:500],
                            }
                        results.append(result)
                    followup_failed = any(
                        str(result.get("violations") or "") != "completed"
                        or any(
                            not isinstance(result.get(domain), dict)
                            or str(
                                result[domain].get("status") or ""
                            ) != "completed"
                            for domain in ("stores", "orders", "products")
                        )
                        for result in results
                    )
                    self.db.set_setting(
                        "new_store_followup_status",
                        json.dumps(
                            {
                                "status": (
                                    "partial"
                                    if followup_failed
                                    else "completed"
                                ),
                                "reason": reason,
                                "stores": results,
                                "updated_at": datetime.now().isoformat(
                                    timespec="seconds"
                                ),
                            },
                            ensure_ascii=False,
                        ),
                    )
                    log_event(
                        "stores.new.background_refresh",
                        reason=reason,
                        stores=results,
                    )
                with self._new_store_followup_lock:
                    self._new_store_followup_thread = None

            thread = threading.Thread(
                target=worker,
                name="webui-new-store-followup",
                daemon=True,
            )
            self._new_store_followup_thread = thread
            thread.start()
        return {
            "scheduled": True,
            "already_running": False,
            "store_count": len(ids),
        }

    def _open_workbench(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self.native_action is None:
            raise RuntimeError("当前运行环境没有订单工作台适配器")
        result = self.native_action("workbench.open", dict(payload))
        return dict(result) if isinstance(result, dict) else {"opened": True}

    def _selected_stores(
        self,
        payload: dict[str, Any],
    ) -> list[Any]:
        raw_ids = payload.get("store_ids")
        if raw_ids in (None, [], ""):
            return list(self.db.list_stores())
        if not isinstance(raw_ids, list):
            raw_ids = [raw_ids]
        store_ids = {
            int(value)
            for value in raw_ids
            if str(value).strip().isdigit()
        }
        if not store_ids:
            raise ValueError("至少选择一个有效店铺")
        stores = [
            self._require_store(store_id)
            for store_id in sorted(store_ids)
        ]
        return stores

    def _write_operation_log(
        self,
        scope: str,
        operation: str,
        message: str,
        *,
        store_id: int | None = None,
        status: str = "success",
        detail: dict[str, Any] | None = None,
    ) -> None:
        payload = json.dumps(detail or {}, ensure_ascii=False)
        log_id = self.db.start_task_log(
            scope,
            operation,
            message,
            store_id=store_id,
            detail=payload,
        )
        self.db.finish_task_log(
            log_id,
            status,
            message,
            detail=payload,
        )

    def _finance_summary(self, payload: dict[str, Any]) -> dict[str, Any]:
        service = self._require_product_service()
        stores = self._selected_stores(payload)
        if not stores:
            raise ValueError("当前账户没有可查询的店铺")
        now = datetime.now()
        start_text = str(payload.get("start_date") or "").strip()
        end_text = str(payload.get("end_date") or "").strip()
        if start_text or end_text:
            if not start_text or not end_text:
                raise ValueError("资金查询必须同时提供开始日期和结束日期")
            try:
                start_dt = datetime.strptime(start_text, "%Y-%m-%d")
                end_dt = datetime.strptime(end_text, "%Y-%m-%d").replace(
                    hour=23,
                    minute=59,
                    second=59,
                )
            except ValueError as exc:
                raise ValueError("资金查询日期格式必须为 YYYY-MM-DD") from exc
            if end_dt < start_dt:
                raise ValueError("结束日期不能早于开始日期")
            if (end_dt.date() - start_dt.date()).days > 30:
                raise ValueError("资金流水单次最多查询 31 个自然日")
        else:
            end_dt = now
            start_dt = end_dt - timedelta(days=30)
            start_text = start_dt.date().isoformat()
            end_text = end_dt.date().isoformat()

        rows: list[dict[str, Any]] = []
        errors: list[str] = []
        succeeded = 0
        for store in stores:
            try:
                data = service.fetch_funds_summary(
                    store,
                    int(start_dt.timestamp()),
                    int(end_dt.timestamp()),
                )
            except Exception as exc:
                message = f"{store.name}: {exc}"
                errors.append(message)
                self._write_operation_log(
                    "finance",
                    "资金查询",
                    f"资金查询失败：{store.name}",
                    store_id=store.id,
                    status="failed",
                    detail={"error": str(exc)},
                )
                continue
            if not isinstance(data, dict):
                errors.append(f"{store.name}: 官方接口返回格式无效")
                continue
            succeeded += 1
            flows = [
                {
                    "flow_id": str(row.get("flow_id") or ""),
                    "type": str(row.get("type") or ""),
                    "amount": row.get("amount"),
                    "create_time": str(row.get("create_time") or ""),
                    "status": str(row.get("status") or ""),
                }
                for row in (data.get("flow_list") or [])
                if isinstance(row, dict)
            ]
            rows.append(
                {
                    "store_id": store.id,
                    "store_name": store.name,
                    "web_status": store.web_status,
                    "available_amount": data.get("available_amount"),
                    "pending_amount": data.get("pending_amount"),
                    "settle_amount": data.get("settle_amount"),
                    "estimated_amount": data.get("estimated_amount"),
                    "flow_list": flows,
                    "flow_count": len(flows),
                }
            )
            self._write_operation_log(
                "finance",
                "资金查询",
                f"资金查询成功：{store.name} · {len(flows)} 条流水",
                store_id=store.id,
                detail={
                    "available_amount": data.get("available_amount"),
                    "pending_amount": data.get("pending_amount"),
                    "flow_count": len(flows),
                },
            )
        return {
            "store_count": len(stores),
            "succeeded": succeeded,
            "errors": errors,
            "start_date": start_text,
            "end_date": end_text,
            "rows": rows,
            "fetched_at": now.isoformat(timespec="seconds"),
        }

    def _load_storefront(self, payload: dict[str, Any]) -> dict[str, Any]:
        service = self._require_product_service()
        store = self._require_store(self._store_id(payload))
        try:
            rows = service.sync_storefront(store)
        except Exception as exc:
            self._write_operation_log(
                "storefront",
                "读取店铺主页",
                f"读取店铺主页失败：{store.name}",
                store_id=store.id,
                status="failed",
                detail={"error": str(exc)},
            )
            raise
        products = []
        errors = []
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            detail_error = str(row.get("_detail_error") or "").strip()
            if detail_error:
                errors.append(
                    f"{row.get('product_id') or '未知商品'}: {detail_error}"
                )
            products.append(
                {
                    "product_id": str(row.get("product_id") or ""),
                    "title": str(row.get("title") or ""),
                    "status_text": str(row.get("status_text") or ""),
                }
            )
        self._write_operation_log(
            "storefront",
            "读取店铺主页",
            (
                f"读取店铺主页部分完成：{store.name} · {len(products)} 个商品，"
                f"详情失败 {len(errors)} 个"
                if errors
                else f"读取店铺主页成功：{store.name} · {len(products)} 个商品"
            ),
            store_id=store.id,
            status="partial" if errors else "success",
            detail={
                "product_count": len(products),
                "error_count": len(errors),
                "errors": errors[:20],
            },
        )
        return {
            "store_id": store.id,
            "store_name": store.name,
            "products": products,
            "count": len(products),
            "status": "partial" if errors else "success",
            "errors": errors,
        }

    def _storefront_action(self, payload: dict[str, Any]) -> dict[str, Any]:
        service = self._require_product_service()
        store = self._require_store(self._store_id(payload))
        action = str(payload.get("action") or "").strip()
        product_id = str(payload.get("product_id") or "").strip()
        if action not in {"reorder", "hide", "top"}:
            raise ValueError("店铺主页操作只能是 reorder、hide 或 top")
        if not product_id:
            raise ValueError("product_id 不能为空")
        try:
            value = int(payload.get("value"))
        except (TypeError, ValueError) as exc:
            raise ValueError("店铺主页操作 value 必须是整数") from exc
        labels = {
            "reorder": "调整排序",
            "hide": "设置隐藏" if value else "取消隐藏",
            "top": "设置置顶" if value else "取消置顶",
        }
        try:
            with self._allow_platform_write(
                store,
                f"storefront_{action}",
            ):
                service.storefront_action(
                    store,
                    action,
                    product_id,
                    value,
                )
        except Exception as exc:
            self._write_operation_log(
                "storefront",
                labels[action],
                f"店铺主页操作失败：{store.name} · {product_id}",
                store_id=store.id,
                status="failed",
                detail={
                    "action": action,
                    "product_id": product_id,
                    "value": value,
                    "error": str(exc),
                },
            )
            raise
        self._write_operation_log(
            "storefront",
            labels[action],
            f"店铺主页操作成功：{store.name} · {product_id}",
            store_id=store.id,
            detail={
                "action": action,
                "product_id": product_id,
                "value": value,
            },
        )
        return {
            "store_id": store.id,
            "product_id": product_id,
            "action": action,
            "value": value,
            "status": "success",
        }

    @staticmethod
    def _automation_task_id(payload: dict[str, Any]) -> int:
        try:
            task_id = int(payload.get("task_id"))
        except (TypeError, ValueError) as exc:
            raise ValueError("task_id 必须是整数") from exc
        if task_id <= 0:
            raise ValueError("task_id 必须是正整数")
        return task_id

    @staticmethod
    def _automation_task_type(value: Any) -> str:
        task_type = str(value or "").strip()
        aliases = {
            "orders": "order_sync",
            "recent_order_updates": "recent_orders",
            "products": "product_sync",
            "status": "product_status",
            "catalog": "product_catalog",
            "performance": "product_performance",
            "after_sales": "aftersale_sync",
            "analytics": "analytics_sync",
        }
        task_type = aliases.get(task_type, task_type)
        allowed = {
            "procurement_sync",
            "hosted_execute",
            "order_sync",
            "recent_orders",
            "product_sync",
            "product_status",
            "product_catalog",
            "product_performance",
            "aftersale_sync",
            "analytics_sync",
        }
        if task_type not in allowed:
            raise ValueError("不支持的自动化任务类型")
        return task_type

    def _list_automation_tasks(
        self,
        _payload: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "tasks": self.db.list_automation_tasks(),
            "runs": self.db.list_automation_runs(limit=100),
            "task_types": [
                {"value": "order_sync", "label": "同步最近订单"},
                {"value": "recent_orders", "label": "刷新最近订单更新"},
                {"value": "product_sync", "label": "同步商品目录与详情"},
                {"value": "product_status", "label": "刷新商品状态数量"},
                {"value": "product_catalog", "label": "扫描商品目录"},
                {"value": "product_performance", "label": "同步商品经营数据"},
                {"value": "aftersale_sync", "label": "同步售后"},
                {"value": "analytics_sync", "label": "同步经营数据"},
                {"value": "procurement_sync", "label": "同步采购账号数据"},
                {"value": "hosted_execute", "label": "执行已配置托管项目"},
            ],
        }

    def _create_automation_task(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        name = str(payload.get("name") or "").strip()
        if not name:
            raise ValueError("任务名称不能为空")
        task_type = self._automation_task_type(payload.get("task_type"))
        raw_store_id = payload.get("store_id")
        store_id = (
            None
            if raw_store_id in (None, "", 0, "0")
            else int(raw_store_id)
        )
        if store_id is not None:
            self._require_store(store_id)
        try:
            interval = int(payload.get("interval_minutes") or 60)
        except (TypeError, ValueError) as exc:
            raise ValueError("间隔分钟必须是整数") from exc
        interval = max(1, min(10080, interval))
        task_id = self.db.add_automation_task(
            name,
            task_type,
            store_id,
            interval,
        )
        if not bool(payload.get("enabled", True)):
            self.db.update_automation_task(task_id, enabled=0)
        return {
            "task_id": task_id,
            "tasks": self.db.list_automation_tasks(),
        }

    def _update_automation_task(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        task_id = self._automation_task_id(payload)
        task = next(
            (
                row
                for row in self.db.list_automation_tasks()
                if int(row.get("id") or 0) == task_id
            ),
            None,
        )
        if task is None:
            raise ValueError("自动化任务不存在")
        changes: dict[str, Any] = {}
        if "enabled" in payload:
            changes["enabled"] = 1 if payload.get("enabled") else 0
        if "name" in payload:
            name = str(payload.get("name") or "").strip()
            if not name:
                raise ValueError("任务名称不能为空")
            changes["name"] = name
        if "task_type" in payload:
            changes["task_type"] = self._automation_task_type(
                payload.get("task_type")
            )
        if "interval_minutes" in payload:
            try:
                interval = int(payload.get("interval_minutes"))
            except (TypeError, ValueError) as exc:
                raise ValueError("间隔分钟必须是整数") from exc
            changes["interval_minutes"] = max(1, min(10080, interval))
            changes["next_run"] = (
                datetime.now()
                + timedelta(minutes=changes["interval_minutes"])
            ).strftime("%Y-%m-%d %H:%M:%S")
        if "store_id" in payload:
            raw_store_id = payload.get("store_id")
            store_id = (
                None
                if raw_store_id in (None, "", 0, "0")
                else int(raw_store_id)
            )
            if store_id is not None:
                self._require_store(store_id)
            changes["store_id"] = store_id
        if (
            "enabled" in changes
            and changes["enabled"]
            and (
                not str(task.get("next_run") or "").strip()
                or str(task.get("next_run")) <= datetime.now().strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
            )
        ):
            changes["next_run"] = (
                datetime.now()
                + timedelta(
                    minutes=max(
                        1,
                        int(
                            changes.get(
                                "interval_minutes",
                                task.get("interval_minutes") or 60,
                            )
                        ),
                    )
                )
            ).strftime("%Y-%m-%d %H:%M:%S")
        self.db.update_automation_task(task_id, **changes)
        return {
            "task": next(
                row
                for row in self.db.list_automation_tasks()
                if int(row.get("id") or 0) == task_id
            )
        }

    def _delete_automation_task(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        task_id = self._automation_task_id(payload)
        self.db.delete_automation_task(task_id)
        return {"deleted": task_id}

    def _start_automation_run(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        raw_ids = payload.get("task_ids")
        if not isinstance(raw_ids, list):
            raw_ids = [raw_ids]
        task_ids = {
            int(value)
            for value in raw_ids
            if str(value).strip().isdigit() and int(value) > 0
        }
        if not task_ids:
            raise ValueError("至少选择一个自动化任务")
        tasks = [
            row
            for row in self.db.list_automation_tasks()
            if int(row.get("id") or 0) in task_ids
        ]
        if len(tasks) != len(task_ids):
            raise ValueError("部分自动化任务不存在")
        generation = self._current_generation()
        owner = self._job_owner_fields()
        with self._automation_jobs_lock:
            active = [
                row
                for row in self._automation_jobs.values()
                if self._job_is_active(row)
            ]
            if active:
                raise ValueError("已有自动化任务正在执行，请等待完成")
            job_id = uuid.uuid4().hex
            job = {
                "job_id": job_id,
                **owner,
                "status": "running",
                "message": "正在准备自动化任务…",
                "current": 0,
                "total": len(tasks),
                "succeeded": 0,
                "failed": 0,
                "errors": [],
                "started_at": datetime.now().isoformat(timespec="seconds"),
                "finished_at": "",
            }
            self._automation_jobs[job_id] = job
        thread = threading.Thread(
            target=self._run_automation_job,
            args=(job_id, tasks, generation),
            daemon=True,
            name=f"webui-automation-{job_id[:8]}",
        )
        thread.start()
        return {"job_id": job_id, "status": "running", "total": len(tasks)}

    def _automation_run_status(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        job_id = str(payload.get("job_id") or "").strip()
        with self._automation_jobs_lock:
            job = self._automation_jobs.get(job_id)
            if job is None:
                raise ValueError("自动化执行任务不存在")
            return dict(job)

    def run_due_automation_tasks(
        self,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        current = now or datetime.now()
        now_text = current.strftime("%Y-%m-%d %H:%M:%S")
        due = [
            task
            for task in self.db.list_automation_tasks()
            if task.get("enabled")
            and (
                not str(task.get("next_run") or "").strip()
                or str(task.get("next_run")) <= now_text
            )
        ]
        if not due:
            return {
                "due": 0,
                "started": False,
                "job_id": "",
                "message": "没有到期的自动化任务",
            }
        try:
            started = self._start_automation_run(
                {"task_ids": [int(task["id"]) for task in due]}
            )
        except ValueError as exc:
            if "已有自动化任务正在执行" in str(exc):
                return {
                    "due": len(due),
                    "started": False,
                    "job_id": "",
                    "message": str(exc),
                }
            raise
        return {
            "due": len(due),
            "started": True,
            "job_id": started["job_id"],
            "message": f"已启动 {len(due)} 个到期自动化任务",
        }

    def start_automation_scheduler(self) -> None:
        with self._automation_scheduler_lock:
            if (
                self._automation_scheduler_thread is not None
                and self._automation_scheduler_thread.is_alive()
            ):
                return
            self._automation_scheduler_stop.clear()

            def worker() -> None:
                while not self._automation_scheduler_stop.wait(1.0):
                    try:
                        self.run_due_automation_tasks()
                    except Exception:
                        # Due-task failures are persisted by _run_automation_job.
                        # A scheduler-level database or service error must not
                        # terminate future checks.
                        pass
                    try:
                        self.run_due_hosted_schedule()
                    except Exception:
                        # Hosted runs persist failures in hosted_runs. A
                        # transient scheduler error must not stop tomorrow.
                        pass
                    try:
                        self._monitor_procurement_automation()
                    except Exception:
                        pass
                    if self._automation_scheduler_stop.wait(29.0):
                        break

            self._automation_scheduler_thread = threading.Thread(
                target=worker,
                daemon=True,
                name="langlang-webui-automation-scheduler",
            )
            self._automation_scheduler_thread.start()

    def stop_automation_scheduler(self) -> None:
        self._automation_scheduler_stop.set()
        with self._automation_scheduler_lock:
            thread = self._automation_scheduler_thread
            self._automation_scheduler_thread = None
        if thread is not None and thread.is_alive():
            thread.join(timeout=2)

    def start_activity_renewal_scheduler(self) -> None:
        self._restore_activity_smart_cloud_settings()
        if self.activity_renewal_manager is not None:
            self.activity_renewal_manager.start()

    def stop_activity_renewal_scheduler(self) -> None:
        if self.activity_renewal_manager is not None:
            self.activity_renewal_manager.stop()

    def _list_automation_runs(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        limit = max(1, min(500, int(payload.get("limit") or 200)))
        return {
            "runs": self.db.list_automation_runs(limit=limit),
        }

    def _run_automation_job(
        self,
        job_id: str,
        tasks: list[dict[str, Any]],
        generation: int | None = None,
    ) -> None:
        generation = (
            self._current_generation()
            if generation is None
            else int(generation)
        )
        for index, task in enumerate(tasks, start=1):
            if not self._job_update_allowed(generation):
                return
            with self._automation_jobs_lock:
                if not self._job_update_allowed(generation):
                    return
                job = self._automation_jobs[job_id]
                job["message"] = f"正在执行：{task.get('name') or task.get('id')}"
                job["current"] = index - 1
            try:
                message = self._execute_automation_task(task)
            except Exception as exc:
                if not self._job_update_allowed(generation):
                    return
                message = str(exc)[:1000]
                now = datetime.now()
                self.db.update_automation_task(
                    int(task["id"]),
                    last_run=now.strftime("%Y-%m-%d %H:%M:%S"),
                    next_run=(
                        now
                        + timedelta(
                            minutes=max(
                                1,
                                int(task.get("interval_minutes") or 60),
                            )
                        )
                    ).strftime("%Y-%m-%d %H:%M:%S"),
                    last_status="失败",
                    last_message=message,
                )
                self.db.log_automation_run(
                    int(task["id"]),
                    "failed",
                    message,
                )
                with self._automation_jobs_lock:
                    if not self._job_update_allowed(generation):
                        return
                    job = self._automation_jobs[job_id]
                    job["failed"] += 1
                    job["errors"].append(
                        f"{task.get('name') or task.get('id')}: {message}"
                    )
            else:
                if not self._job_update_allowed(generation):
                    return
                now = datetime.now()
                self.db.update_automation_task(
                    int(task["id"]),
                    last_run=now.strftime("%Y-%m-%d %H:%M:%S"),
                    next_run=(
                        now
                        + timedelta(
                            minutes=max(
                                1,
                                int(task.get("interval_minutes") or 60),
                            )
                        )
                    ).strftime("%Y-%m-%d %H:%M:%S"),
                    last_status="成功",
                    last_message=message,
                )
                self.db.log_automation_run(
                    int(task["id"]),
                    "success",
                    message,
                )
                with self._automation_jobs_lock:
                    if not self._job_update_allowed(generation):
                        return
                    job = self._automation_jobs[job_id]
                    job["succeeded"] += 1
            with self._automation_jobs_lock:
                if not self._job_update_allowed(generation):
                    return
                job = self._automation_jobs[job_id]
                job["current"] = index
        with self._automation_jobs_lock:
            if not self._job_update_allowed(generation):
                return
            job = self._automation_jobs[job_id]
            job["status"] = (
                "failed"
                if job["succeeded"] == 0 and job["failed"]
                else "partial"
                if job["failed"]
                else "completed"
            )
            job["message"] = (
                f"自动化执行完成：成功 {job['succeeded']}，失败 {job['failed']}"
            )
            job["finished_at"] = datetime.now().isoformat(timespec="seconds")

    def _execute_automation_task(self, task: dict[str, Any]) -> str:
        service = self._require_product_service()
        task_type = self._automation_task_type(task.get("task_type"))
        raw_store_id = task.get("store_id")
        store_id = (
            int(raw_store_id)
            if raw_store_id not in (None, "", 0, "0")
            else None
        )
        stores = (
            [self._require_store(store_id)]
            if store_id is not None
            else list(self.db.list_stores())
        )
        now = datetime.now()
        messages: list[str] = []
        if task_type == "procurement_sync":
            count = 0
            for account in self.db.list_procurement_accounts():
                result = service.sync_procurement_account(int(account["id"]))
                count += int(getattr(result, "count", 0) or 0)
            return f"采购同步 {count} 条"
        if task_type == "hosted_execute":
            from .hosted_runner import run_hosted_store

            errors = []
            executed = 0
            for store in stores:
                config = self.db.get_hosted_config(store.id) or {}
                if not config.get("enabled"):
                    continue
                executed += 1
                error = run_hosted_store(
                    self.db,
                    service,
                    store,
                    config,
                )
                if error:
                    errors.append(error)
            if not executed:
                raise ValueError("没有已启用的托管店铺配置")
            if errors:
                raise RuntimeError("；".join(errors[:8]))
            return f"托管执行完成：{executed} 家店铺"
        for store in stores:
            if task_type == "order_sync":
                result = service.sync_orders(
                    store,
                    int((now - timedelta(days=7)).timestamp()),
                    int(now.timestamp()),
                )
                messages.append(str(getattr(result, "message", result)))
            elif task_type == "recent_orders":
                result = service.sync_recent_order_updates(
                    store,
                    int((now - timedelta(days=1)).timestamp()),
                    int(now.timestamp()),
                )
                messages.append(str(getattr(result, "message", result)))
            elif task_type == "product_sync":
                result = service.sync_products(store, max_items=300)
                service.sync_product_details(store, max_items=300)
                messages.append(str(getattr(result, "message", result)))
            elif task_type == "product_status":
                result = service.refresh_product_status_snapshot(store)
                messages.append(str(getattr(result, "message", result)))
            elif task_type == "product_catalog":
                result = service.scan_product_catalog(store)
                messages.append(str(getattr(result, "message", result)))
            elif task_type == "product_performance":
                product_ids = [
                    str(row.get("product_id") or "")
                    for row in self.db.list_products(store_id=store.id)
                    if str(row.get("product_id") or "").strip()
                ]
                result = service.sync_product_performance(
                    store,
                    product_ids,
                )
                messages.append(str(getattr(result, "message", result)))
            elif task_type == "aftersale_sync":
                start = now.replace(hour=0, minute=0, second=0, microsecond=0)
                result = service.sync_after_sales(
                    store,
                    int(start.timestamp()),
                    int(now.timestamp()),
                    time_field="update",
                )
                messages.append(str(getattr(result, "message", result)))
            elif task_type == "analytics_sync":
                result = service.sync_analytics_day(
                    store,
                    now.date().isoformat(),
                )
                messages.append(str(getattr(result, "message", result)))
        return "；".join(messages) or f"没有可执行对象（{len(stores)} 家店铺）"

    def _open_customer_console(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        session = self._require_web_session()
        store = self._require_store(self._store_id(payload))

        def worker() -> None:
            try:
                session.open_official_customer_console(store)
            except Exception as exc:
                self._write_operation_log(
                    "customer_service",
                    "打开官方客服控制台",
                    f"打开官方客服控制台失败：{store.name}",
                    store_id=store.id,
                    status="failed",
                    detail={"error": str(exc)},
                )
            else:
                self._write_operation_log(
                    "customer_service",
                    "打开官方客服控制台",
                    f"官方客服控制台已关闭：{store.name}",
                    store_id=store.id,
                )

        threading.Thread(
            target=worker,
            daemon=True,
            name=f"webui-customer-console-{store.id}",
        ).start()
        self._write_operation_log(
            "customer_service",
            "打开官方客服控制台",
            f"正在打开官方客服控制台：{store.name}",
            store_id=store.id,
            status="running",
        )
        return {
            "opening": True,
            "store_id": store.id,
            "store_name": store.name,
        }

    def _sync_platform_violation_blacklist(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        service = self._require_product_service()
        stores = real_logged_in_stores(
            self._selected_stores(payload)
        )
        if not stores:
            raise ValueError("当前账户没有已登录的真实店铺")
        errors: list[str] = []
        succeeded = 0
        counts = 0
        with ThreadPoolExecutor(
            max_workers=max(1, min(4, len(stores))),
            thread_name_prefix="webui-platform-violation",
        ) as executor:
            futures = {
                executor.submit(
                    service.sync_platform_violation_blacklist,
                    store,
                ): store
                for store in stores
            }
            for future in as_completed(futures):
                store = futures[future]
                try:
                    result = future.result()
                except Exception as exc:
                    message = f"{store.name}: {exc}"
                    errors.append(message)
                    self._write_operation_log(
                        "violations",
                        "同步平台违规黑名单",
                        f"平台违规黑名单同步失败：{store.name}",
                        store_id=store.id,
                        status="failed",
                        detail={"error": str(exc)},
                    )
                else:
                    succeeded += 1
                    counts += int(getattr(result, "count", 0) or 0)
                    self._write_operation_log(
                        "violations",
                        "同步平台违规黑名单",
                        f"平台违规黑名单同步成功：{store.name}",
                        store_id=store.id,
                        detail={
                            "count": int(getattr(result, "count", 0) or 0),
                            "message": str(getattr(result, "message", "")),
                        },
                    )
        records = self._read_json_list(
            self.db.get_setting("violation_library", "[]")
        )
        records.extend(
            self._read_json_list(
                self.db.get_setting("violation_blacklist", "[]")
            )
        )
        self.db.set_setting(
            "upload_violation_titles",
            json.dumps(
                build_upload_violation_titles(records),
                ensure_ascii=False,
            ),
        )
        return {
            "refreshed": len(stores),
            "succeeded": succeeded,
            "errors": errors,
            "active_rule_count": len(
                self._read_json_list(
                    self.db.get_setting("upload_violation_titles", "[]")
                )
            ),
            "platform_record_count": counts,
        }

    def _require_store(self, store_id: int) -> Any:
        store = self.db.get_store(store_id)
        if store is None:
            raise ValueError("店铺不存在")
        return store

    def _require_web_session(self) -> Any:
        if self.web_session is None:
            raise RuntimeError("当前运行环境未初始化网页登录会话")
        return self.web_session

    def _require_product_service(self) -> Any:
        if self.product_service is None:
            raise RuntimeError("当前运行环境未初始化商品服务")
        return self.product_service

    def _delete_product(self, payload: dict[str, Any]) -> dict[str, Any]:
        service = self._require_product_service()
        store = self._require_store(self._store_id(payload))
        product_id = str(payload.get("product_id") or "").strip()
        if not product_id:
            raise ValueError("product_id 不能为空")
        if product_id in self.db.violation_blacklist_ids():
            raise ValueError(
                f"商品 {product_id} 在违规黑名单中，已阻止删除操作"
            )
        product = self.db.get_product(store.id, product_id) or {}
        title = str(product.get("title") or "")
        try:
            with self._allow_platform_write(
                store,
                "product_write",
                "product_delete",
                "product_web_batch_action",
            ):
                result = dict(
                    service.product_write(
                        store,
                        "delete",
                        product_id=product_id,
                    )
                )
        except Exception as exc:
            pending = bool(
                getattr(exc, "verification_pending", False)
            )
            self._log_product_operation(
                store,
                product_id,
                title,
                (
                    "彻底删除"
                    if int(product.get("status") or -1) == 6
                    else "移入回收站"
                ),
                "pending" if pending else "failed",
                str(exc),
            )
            raise
        delete_mode = str(result.get("delete_mode") or "")
        self._log_product_operation(
            store,
            product_id,
            title,
            "彻底删除" if delete_mode == "real" else "移入回收站",
            "success",
        )
        return result

    def _cancel_product_audit(self, payload: dict[str, Any]) -> dict[str, Any]:
        service = self._require_product_service()
        store = self._require_store(self._store_id(payload))
        product_id = str(payload.get("product_id") or "").strip()
        if not product_id:
            raise ValueError("product_id 不能为空")
        title = str((self.db.get_product(store.id, product_id) or {}).get("title") or "")
        try:
            with self._allow_platform_write(
                store,
                "product_write",
                "product_audit_cancel",
            ):
                result = dict(
                    service.product_write(
                        store,
                        "cancel_audit",
                        product_id=product_id,
                    )
                )
        except Exception as exc:
            self._log_product_operation(
                store,
                product_id,
                title,
                "撤回审核",
                (
                    "pending"
                    if getattr(exc, "verification_pending", False)
                    else "failed"
                ),
                str(exc),
            )
            raise
        self._log_product_operation(
            store,
            product_id,
            title,
            "撤回审核",
            "success",
        )
        return result

    def _set_products_listing(self, payload: dict[str, Any]) -> dict[str, Any]:
        service = self._require_product_service()
        store = self._require_store(self._store_id(payload))
        product_ids = [
            str(value).strip()
            for value in payload.get("product_ids", [])
            if str(value).strip()
        ]
        if not product_ids:
            raise ValueError("至少选择一个商品")
        listed = bool(payload.get("listed"))
        action = "上架" if listed else "下架"
        try:
            with self._allow_platform_write(
                store,
                "product_listing",
                "product_delisting",
            ):
                result = service.set_products_listing(
                    store,
                    product_ids,
                    listed,
                )
        except Exception as exc:
            for product_id in product_ids:
                self._log_product_operation(
                    store,
                    product_id,
                    str((self.db.get_product(store.id, product_id) or {}).get("title") or ""),
                    action,
                    "failed",
                    str(exc),
                )
            raise
        public = self._public_service_result(result)
        errors = [
            str(value)
            for value in public.get("errors") or []
            if str(value).strip()
        ]
        pending_count = int(public.get("pending") or 0)
        failed_count = int(public.get("failed") or 0)
        for product_id in product_ids:
            error = next(
                (
                    value
                    for value in errors
                    if value.startswith(f"{product_id}:")
                ),
                "",
            )
            self._log_product_operation(
                store,
                product_id,
                str((self.db.get_product(store.id, product_id) or {}).get("title") or ""),
                action,
                (
                    "pending"
                    if error and pending_count and not failed_count
                    else "failed"
                    if error
                    else "success"
                ),
                error,
            )
        return public

    def _locate_product(self, payload: dict[str, Any]) -> dict[str, Any]:
        store = self._require_store(self._store_id(payload))
        product_id = str(payload.get("product_id") or "").strip()
        if not product_id:
            raise ValueError("product_id 不能为空")
        row = self.db.get_product(store.id, product_id)
        source = "本机已同步商品"
        if row is None:
            service = self._require_product_service()
            product = service.fetch_product_detail(store, product_id)
            row = (
                asdict(product)
                if hasattr(product, "__dataclass_fields__")
                else dict(product)
                if isinstance(product, dict)
                else None
            )
            source = "微信小店官方商品详情"
        if not isinstance(row, dict):
            raise RuntimeError(
                f"未找到店铺 {store.name}（{store.id}）的商品 {product_id}"
            )
        return {
            "store_id": store.id,
            "store_name": store.name,
            "product_id": product_id,
            "product": row,
            "source": source,
        }

    def _product_detail(self, payload: dict[str, Any]) -> dict[str, Any]:
        service = self._require_product_service()
        store = self._require_store(self._store_id(payload))
        product_id = str(payload.get("product_id") or "").strip()
        if not product_id:
            raise ValueError("product_id 不能为空")
        product = service.fetch_product_detail(store, product_id)
        if hasattr(product, "__dataclass_fields__"):
            data = asdict(product)
        elif isinstance(product, dict):
            data = dict(product)
        else:
            raise RuntimeError("商品服务没有返回有效详情")
        raw = data.get("raw")
        if not isinstance(raw, dict):
            raw = {}
        skus = []
        for row in raw.get("skus") or []:
            if not isinstance(row, dict):
                continue
            skus.append(
                {
                    "sku_id": str(row.get("sku_id") or ""),
                    "sku_name": str(row.get("sku_name") or row.get("title") or ""),
                    "sale_price": row.get("sale_price"),
                    "market_price": row.get("market_price"),
                    "stock_num": row.get("stock_num"),
                    "status": row.get("status"),
                    "status_text": str(row.get("status_text") or ""),
                }
            )
        return {
            "store_id": store.id,
            "store_name": store.name,
            "product": {
                "product_id": product_id,
                "title": str(data.get("title") or ""),
                "head_img": str(data.get("head_img") or ""),
                "status": data.get("status"),
                "status_text": str(data.get("status_text") or ""),
                "min_price": data.get("min_price"),
                "stock_total": data.get("stock_total"),
                "synced_at": str(data.get("synced_at") or ""),
                "skus": skus,
            },
        }

    def _product_taglink(self, payload: dict[str, Any]) -> dict[str, Any]:
        service = self._require_product_service()
        store = self._require_store(self._store_id(payload))
        product_id = str(payload.get("product_id") or "").strip()
        if not product_id:
            raise ValueError("product_id 不能为空")
        try:
            link = service.fetch_product_taglink(store, product_id)
        except Exception as exc:
            self._log_product_operation(
                store,
                product_id,
                "",
                "读取商品短链",
                "failed",
                str(exc),
            )
            raise
        self._log_product_operation(
            store,
            product_id,
            "",
            "读取商品短链",
            "success",
        )
        return {
            "store_id": store.id,
            "product_id": product_id,
            "link": str(link or ""),
        }

    def _update_product_stock(self, payload: dict[str, Any]) -> dict[str, Any]:
        service = self._require_product_service()
        store = self._require_store(self._store_id(payload))
        product_id = str(payload.get("product_id") or "").strip()
        sku_id = str(payload.get("sku_id") or "").strip()
        if not product_id or not sku_id:
            raise ValueError("product_id 和 sku_id 不能为空")
        try:
            diff_type = int(payload.get("diff_type"))
            amount = int(payload.get("amount"))
        except (TypeError, ValueError) as exc:
            raise ValueError("库存修改类型和数量必须是整数") from exc
        if diff_type not in {1, 2, 3}:
            raise ValueError("库存修改类型必须是 1、2 或 3")
        if amount < 0 or (diff_type != 3 and amount == 0):
            raise ValueError("库存调整数量不正确")
        title = str((self.db.get_product(store.id, product_id) or {}).get("title") or "")
        try:
            with self._allow_platform_write(store, "product_stock_update"):
                service.update_product_stock(
                    store,
                    product_id,
                    sku_id,
                    diff_type,
                    amount,
                )
        except Exception as exc:
            self._log_product_operation(
                store,
                product_id,
                title,
                "修改库存",
                "failed",
                str(exc),
            )
            raise
        self._log_product_operation(
            store,
            product_id,
            title,
            "修改库存",
            "success",
        )
        return {
            "store_id": store.id,
            "product_id": product_id,
            "sku_id": sku_id,
            "diff_type": diff_type,
            "amount": amount,
        }

    def _upload_product_images(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        service = self._require_product_service()
        store = self._require_store(self._store_id(payload))
        files = payload.get("files")
        if not isinstance(files, list) or not files:
            raise ValueError("至少选择一张商品图片")
        if len(files) > 20:
            raise ValueError("单次最多上传 20 张商品图片")
        images: list[str] = []
        with tempfile.TemporaryDirectory(prefix="langlang-product-images-") as temp_dir:
            paths: list[str] = []
            for index, row in enumerate(files):
                if not isinstance(row, dict):
                    raise ValueError("商品图片格式无效")
                name = Path(str(row.get("name") or f"image-{index + 1}.jpg")).name
                suffix = Path(name).suffix.lower()
                if suffix not in {".jpg", ".jpeg", ".png", ".webp"}:
                    raise ValueError(f"不支持的商品图片格式：{name}")
                try:
                    content = base64.b64decode(
                        str(row.get("content_base64") or ""),
                        validate=True,
                    )
                except (ValueError, TypeError) as exc:
                    raise ValueError(f"商品图片内容无效：{name}") from exc
                if not content or len(content) > 20 * 1024 * 1024:
                    raise ValueError(f"商品图片必须小于 20MB：{name}")
                path = Path(temp_dir) / f"{index:02d}-{name}"
                path.write_bytes(content)
                paths.append(str(path))
            try:
                with self._allow_platform_write(
                    store,
                    "product_image_upload",
                ):
                    images = list(
                        service.upload_product_images(store, paths)
                    )
            except Exception as exc:
                self._write_operation_log(
                    "products",
                    "上传商品图片",
                    f"商品图片上传失败：{store.name}",
                    store_id=store.id,
                    status="failed",
                    detail={"file_count": len(paths), "error": str(exc)},
                )
                raise
        self._write_operation_log(
            "products",
            "上传商品图片",
            f"商品图片上传完成：{store.name} · {len(images)} 张",
            store_id=store.id,
            detail={"file_count": len(images)},
        )
        return {
            "store_id": store.id,
            "images": images,
            "count": len(images),
        }

    def _open_official_product(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        session = self._require_web_session()
        store = self._require_store(self._store_id(payload))
        product_id = str(payload.get("product_id") or "").strip()
        if not product_id:
            raise ValueError("product_id 不能为空")

        with self._product_backend_jobs_lock:
            for existing in self._product_backend_jobs.values():
                if (
                    existing.get("store_id") == store.id
                    and existing.get("product_id") == product_id
                    and existing.get("status") in {"starting", "running"}
                ):
                    return {
                        "opening": True,
                        "already_running": True,
                        "job_id": existing["job_id"],
                        "store_id": store.id,
                        "product_id": product_id,
                    }
            job_id = uuid.uuid4().hex
            self._product_backend_jobs[job_id] = {
                "job_id": job_id,
                "store_id": store.id,
                "product_id": product_id,
                "status": "starting",
                "message": "正在定位商品官方后台…",
                "error": "",
            }

        def update(**changes: Any) -> None:
            with self._product_backend_jobs_lock:
                job = self._product_backend_jobs.get(job_id)
                if job is not None:
                    job.update(changes)

        def worker() -> None:
            update(status="running", message="正在打开微信小店商品后台…")
            try:
                session.open_official_product(store, product_id)
            except Exception as exc:
                update(
                    status="failed",
                    message=f"打开商品后台失败：{exc}",
                    error=str(exc)[:1000],
                )
                self._log_product_operation(
                    store,
                    product_id,
                    "",
                    "打开商品后台",
                    "failed",
                    str(exc),
                )
            else:
                update(
                    status="completed",
                    message=f"已打开商品 {product_id} 的官方后台",
                )
                self._log_product_operation(
                    store,
                    product_id,
                    "",
                    "打开商品后台",
                    "success",
                )

        threading.Thread(
            target=worker,
            daemon=True,
            name=f"webui-product-backend-{store.id}-{product_id[:12]}",
        ).start()
        return {
            "opening": True,
            "already_running": False,
            "job_id": job_id,
            "store_id": store.id,
            "product_id": product_id,
        }

    def _open_official_product_status(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        job_id = str(payload.get("job_id") or "").strip()
        if not job_id:
            raise ValueError("job_id 不能为空")
        with self._product_backend_jobs_lock:
            job = self._product_backend_jobs.get(job_id)
            if job is None:
                raise ValueError("打开商品后台任务不存在")
            return dict(job)

    def _log_product_operation(
        self,
        store: Any,
        product_id: str,
        title: str,
        action: str,
        status: str,
        error: str = "",
    ) -> None:
        message = (
            f"{store.name} · {title or product_id} · {action}"
            f"{'成功' if status == 'success' else ('待核实' if status == 'pending' else '失败')}"
        )
        log_id = self.db.start_task_log(
            "products",
            f"商品{action}",
            message,
            store_id=store.id,
            detail=json.dumps(
                {
                    "store_id": store.id,
                    "store_name": store.name,
                    "product_id": product_id,
                    "title": title,
                    "action": action,
                    "error": error[:1000],
                },
                ensure_ascii=False,
            ),
        )
        self.db.finish_task_log(
            log_id,
            status,
            message,
            detail=json.dumps(
                {
                    "store_id": store.id,
                    "store_name": store.name,
                    "product_id": product_id,
                    "title": title,
                    "action": action,
                    "error": error[:1000],
                },
                ensure_ascii=False,
            ),
        )

    def _list_product_operation_logs(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        raw_store_id = payload.get("store_id")
        if raw_store_id in (None, ""):
            raise ValueError("store_id 不能为空；全部店铺请显式传入 __all__")
        store = (
            None
            if raw_store_id == ALL_STORES_KEY
            else self._require_store(self._store_id(payload))
        )
        status = str(payload.get("status") or "").strip()
        rows = self.db.list_task_logs(
            limit=max(1, min(500, int(payload.get("limit") or 200))),
            scope="products",
            status=status,
        )
        logs: list[dict[str, Any]] = []
        for row in rows:
            row_store_id = int(row.get("store_id") or 0)
            if store is not None and row_store_id != store.id:
                continue
            row_store = (
                store
                if store is not None
                else self.db.get_store(row_store_id)
            )
            try:
                detail = json.loads(str(row.get("detail") or "{}"))
            except (TypeError, ValueError, json.JSONDecodeError):
                detail = {}
            logs.append(
                {
                    "id": int(row.get("id") or 0),
                    "store_id": row_store_id,
                    "store_name": str(
                        getattr(row_store, "name", "") or ""
                    ),
                    "product_id": str(detail.get("product_id") or ""),
                    "title": str(detail.get("title") or ""),
                    "action": str(detail.get("action") or row.get("operation") or ""),
                    "status": str(row.get("status") or ""),
                    "message": str(row.get("message") or ""),
                    "error": str(detail.get("error") or ""),
                    "started_at": str(row.get("started_at") or ""),
                    "finished_at": str(row.get("finished_at") or ""),
                }
            )
        return {"logs": logs, "count": len(logs)}

    def _activity_catalog(self, payload: dict[str, Any]) -> dict[str, Any]:
        service = self._require_product_service()
        store = self._require_store(self._store_id(payload))
        return service.platform_activity_catalog(store)

    def _activity_preview(self, payload: dict[str, Any]) -> dict[str, Any]:
        service = self._require_product_service()
        store = self._require_store(self._store_id(payload))
        activity_id = self._activity_id(payload)
        return service.activity_preview(
            store,
            activity_id,
            discount_basis_points=max(
                1,
                min(10000, int(payload.get("discount_basis_points") or 9000)),
            ),
            min_discount_cents=max(
                1,
                int(payload.get("min_discount_cents") or 100),
            ),
        )

    def _activity_apply(self, payload: dict[str, Any]) -> dict[str, Any]:
        service = self._require_product_service()
        store = self._require_store(self._store_id(payload))
        activity_id = self._activity_id(payload)
        with self._allow_platform_write(store, "platform_activity_apply"):
            return service.apply_platform_activity(store, activity_id)

    def _activity_submit_items(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        service = self._require_product_service()
        store = self._require_store(self._store_id(payload))
        activity_id = self._activity_id(payload)
        items = [
            dict(item)
            for item in payload.get("items", [])
            if isinstance(item, dict)
        ]
        with self._allow_platform_write(
            store,
            "platform_activity_items_submit",
        ):
            return service.activity_submit_items(
                store,
                activity_id,
                items,
            )

    def _activity_remove_all(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        service = self._require_product_service()
        store = self._require_store(self._store_id(payload))
        activity_id = self._activity_id(payload)
        with self._allow_platform_write(
            store,
            "platform_activity_items_remove",
        ):
            return service.double11_remove_all(store, activity_id)

    def _list_coupons(self, payload: dict[str, Any]) -> dict[str, Any]:
        store = self._require_store(self._store_id(payload))
        rows = []
        for row in self.db.list_coupons(store.id):
            try:
                raw = json.loads(str(row.get("raw_json") or "{}"))
            except (TypeError, ValueError, json.JSONDecodeError):
                raw = {}
            if not isinstance(raw, dict):
                raw = {}
            coupon_info = (
                raw.get("coupon_info")
                if isinstance(raw.get("coupon_info"), dict)
                else {}
            )
            promote_info = (
                coupon_info.get("promote_info")
                if isinstance(coupon_info.get("promote_info"), dict)
                else {}
            )
            rows.append(
                {
                    "store_id": store.id,
                    "store_name": store.name,
                    "coupon_id": str(row.get("coupon_id") or ""),
                    "name": str(row.get("name") or ""),
                    "coupon_type": int(row.get("coupon_type") or 0),
                    "status": int(row.get("status") or 0),
                    "discount_fee": int(row.get("discount_fee") or 0),
                    "discount_num": int(row.get("discount_num") or 0),
                    "total_num": int(row.get("total_num") or 0),
                    "receive_num": int(row.get("receive_num") or 0),
                    "used_num": int(row.get("used_num") or 0),
                    "start_time": int(row.get("start_time") or 0),
                    "end_time": int(row.get("end_time") or 0),
                    "promote_type": int(
                        promote_info.get("promote_type")
                        or promote_info.get("promoteType")
                        or 0
                    ),
                    "synced_at": str(row.get("synced_at") or ""),
                }
            )
        return {"coupons": rows, "count": len(rows)}

    def _sync_coupons(self, payload: dict[str, Any]) -> dict[str, Any]:
        service = self._require_product_service()
        store = self._require_store(self._store_id(payload))
        try:
            result = service.sync_coupons(store)
        except Exception as exc:
            self._write_operation_log(
                "activities",
                "同步优惠券",
                f"优惠券同步失败：{store.name}",
                store_id=store.id,
                status="failed",
                detail={"error": str(exc)},
            )
            raise
        self._write_operation_log(
            "activities",
            "同步优惠券",
            f"优惠券同步成功：{store.name}",
            store_id=store.id,
            detail=self._public_service_result(result),
        )
        return {
            "result": self._public_service_result(result),
            **self._list_coupons({"store_id": store.id}),
        }

    def _update_coupon_status(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        service = self._require_product_service()
        store = self._require_store(self._store_id(payload))
        coupon_id = str(payload.get("coupon_id") or "").strip()
        if not coupon_id:
            raise ValueError("coupon_id 不能为空")
        try:
            status = int(payload.get("status"))
        except (TypeError, ValueError) as exc:
            raise ValueError("优惠券状态必须是整数") from exc
        if status not in {4, 5}:
            raise ValueError("当前只允许停用或删除优惠券")
        try:
            with self._allow_platform_write(store, "coupon_status"):
                result = service.coupon_write(
                    store,
                    "status",
                    {
                        "coupon_id": coupon_id,
                        "status": status,
                    },
                )
        except Exception as exc:
            self._write_operation_log(
                "activities",
                "变更优惠券状态",
                f"优惠券状态变更失败：{store.name} · {coupon_id}",
                store_id=store.id,
                status="failed",
                detail={
                    "coupon_id": coupon_id,
                    "status": status,
                    "error": str(exc),
                },
            )
            raise
        self._write_operation_log(
            "activities",
            "变更优惠券状态",
            f"优惠券状态已变更：{store.name} · {coupon_id}",
            store_id=store.id,
            detail={"coupon_id": coupon_id, "status": status},
        )
        return {
            "coupon_id": coupon_id,
            "status": status,
            "result": result,
            **self._list_coupons({"store_id": store.id}),
        }

    def _create_coupon(self, payload: dict[str, Any]) -> dict[str, Any]:
        service = self._require_product_service()
        store = self._require_store(self._store_id(payload))
        coupon = payload.get("coupon")
        if not isinstance(coupon, dict):
            raise ValueError("优惠券配置格式无效")
        name = str(coupon.get("name") or "").strip()
        if not name:
            raise ValueError("优惠券名称不能为空")
        promote_info = (
            coupon.get("promote_info")
            if isinstance(coupon.get("promote_info"), dict)
            else {}
        )
        discount_info = (
            coupon.get("discount_info")
            if isinstance(coupon.get("discount_info"), dict)
            else {}
        )
        receive_info = (
            coupon.get("receive_info")
            if isinstance(coupon.get("receive_info"), dict)
            else {}
        )
        valid_info = (
            coupon.get("valid_info")
            if isinstance(coupon.get("valid_info"), dict)
            else {}
        )
        try:
            coupon_type = int(coupon.get("type") or 104)
            promote_type = int(promote_info.get("promote_type") or 1)
            discount_fee = int(discount_info.get("discount_fee") or 0)
            discount_num = int(discount_info.get("discount_num") or 0)
            total_num = int(receive_info.get("total_num") or 0)
            limit_num = int(
                receive_info.get("limit_num_one_person") or 1
            )
            start_time = int(receive_info.get("start_time") or 0)
            end_time = int(receive_info.get("end_time") or 0)
            valid_type = int(valid_info.get("valid_type") or 2)
            valid_day_num = int(valid_info.get("valid_day_num") or 7)
        except (TypeError, ValueError) as exc:
            raise ValueError("优惠券数值字段格式无效") from exc
        if discount_fee <= 0:
            raise ValueError("优惠金额必须大于 0")
        if total_num <= 0:
            raise ValueError("发放总量必须大于 0")
        if limit_num <= 0 or valid_day_num <= 0:
            raise ValueError("每人限领和有效天数必须大于 0")
        if end_time <= start_time:
            raise ValueError("优惠券结束时间必须晚于开始时间")
        normalized = {
            "type": coupon_type,
            "name": name,
            "promote_info": {"promote_type": promote_type},
            "discount_info": {
                "discount_fee": discount_fee,
                "discount_num": discount_num,
            },
            "receive_info": {
                "total_num": total_num,
                "limit_num_one_person": limit_num,
                "start_time": start_time,
                "end_time": end_time,
            },
            "valid_info": {
                "valid_type": valid_type,
                "valid_day_num": valid_day_num,
            },
        }
        try:
            with self._allow_platform_write(store, "coupon_create"):
                result = service.coupon_write(
                    store,
                    "create",
                    normalized,
                )
        except Exception as exc:
            self._write_operation_log(
                "activities",
                "新建优惠券",
                f"优惠券创建失败：{store.name} · {name}",
                store_id=store.id,
                status="failed",
                detail={"name": name, "error": str(exc)},
            )
            raise
        self._write_operation_log(
            "activities",
            "新建优惠券",
            f"优惠券创建成功：{store.name} · {name}",
            store_id=store.id,
            detail={
                "name": name,
                "coupon_type": coupon_type,
                "total_num": total_num,
            },
        )
        return {
            "result": result,
            **self._list_coupons({"store_id": store.id}),
        }

    def _renew_coupon(self, payload: dict[str, Any]) -> dict[str, Any]:
        session = self._require_web_session()
        store = self._require_store(self._store_id(payload))
        coupon_id = str(payload.get("coupon_id") or "").strip()
        if not coupon_id:
            raise ValueError("coupon_id 不能为空")
        try:
            coupon_type = int(payload.get("coupon_type") or 0)
            promote_type = int(payload.get("promote_type") or 0)
            start_time = int(
                payload.get("start_time")
                or int(time.time()) + 60
            )
            days = max(1, min(365, int(payload.get("days") or 7)))
        except (TypeError, ValueError) as exc:
            raise ValueError("优惠券续期参数无效") from exc
        try:
            with self._allow_platform_write(store, "coupon_renew"):
                result = session.renew_coupon(
                    store,
                    coupon_id,
                    coupon_type,
                    promote_type,
                    start_time,
                    days,
                )
        except Exception as exc:
            self._write_operation_log(
                "activities",
                "续期优惠券",
                f"优惠券续期失败：{store.name} · {coupon_id}",
                store_id=store.id,
                status="failed",
                detail={"coupon_id": coupon_id, "days": days, "error": str(exc)},
            )
            raise
        self._write_operation_log(
            "activities",
            "续期优惠券",
            f"优惠券续期成功：{store.name} · {coupon_id}",
            store_id=store.id,
            detail={"coupon_id": coupon_id, "days": days},
        )
        return result

    def _list_snapup_activities(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        service = self._require_product_service()
        store = self._require_store(self._store_id(payload))
        activities = service.snapup_activities(store)
        rows = []
        for row in activities or []:
            if not isinstance(row, dict):
                continue
            extra = row.get("extra") if isinstance(row.get("extra"), dict) else {}
            products = extra.get("snapupProducts") or row.get("products") or []
            rows.append(
                {
                    "activity_id": str(
                        row.get("activityId")
                        or row.get("activity_id")
                        or ""
                    ),
                    "title": str(row.get("title") or ""),
                    "status": row.get("status"),
                    "start_time": row.get("startTime") or row.get("start_time"),
                    "end_time": row.get("endTime") or row.get("end_time"),
                    "product_count": len(products) if isinstance(products, list) else 0,
                }
            )
        return {
            "store_id": store.id,
            "store_name": store.name,
            "activities": rows,
            "count": len(rows),
        }

    def _renew_snapup_activity(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        session = self._require_web_session()
        store = self._require_store(self._store_id(payload))
        activity_id = str(payload.get("activity_id") or "").strip()
        if not activity_id:
            raise ValueError("activity_id 不能为空")
        try:
            start_time = int(
                payload.get("start_time")
                or int(time.time()) + 60
            )
            days = max(1, min(7, int(payload.get("days") or 7)))
        except (TypeError, ValueError) as exc:
            raise ValueError("限时抢购续期参数无效") from exc
        try:
            with self._allow_platform_write(store, "activity_renew"):
                result = session.renew_snapup_activity(
                    store,
                    activity_id,
                    start_time,
                    days,
                )
        except Exception as exc:
            self._write_operation_log(
                "activities",
                "续期限时抢购",
                f"限时抢购续期失败：{store.name} · {activity_id}",
                store_id=store.id,
                status="failed",
                detail={"activity_id": activity_id, "days": days, "error": str(exc)},
            )
            raise
        self._write_operation_log(
            "activities",
            "续期限时抢购",
            f"限时抢购续期成功：{store.name} · {activity_id}",
            store_id=store.id,
            detail={"activity_id": activity_id, "days": days},
        )
        return result

    def _create_snapup_activity(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        service = self._require_product_service()
        store = self._require_store(self._store_id(payload))
        product_ids = [
            str(value).strip()
            for value in payload.get("product_ids") or []
            if str(value).strip()
        ]
        if not product_ids:
            raise ValueError("至少选择一个商品")
        try:
            discount = float(payload.get("discount") or 0.9)
            limit_purchase_num = max(
                0,
                int(payload.get("limit_purchase_num") or 0),
            )
            stock_limit_per_sku = max(
                0,
                int(payload.get("stock_limit_per_sku") or 0),
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("折扣、限购数量和库存上限必须是数字") from exc
        try:
            with self._allow_platform_write(store, "activity_create"):
                result = service.create_snapup_activity(
                    store,
                    product_ids,
                    discount,
                    create_all=bool(payload.get("create_all", True)),
                    start_time=(
                        int(payload["start_time"])
                        if payload.get("start_time") not in (None, "")
                        else None
                    ),
                    end_time=(
                        int(payload["end_time"])
                        if payload.get("end_time") not in (None, "")
                        else None
                    ),
                    min_discount_cents=max(
                        100,
                        int(payload.get("min_discount_cents") or 100),
                    ),
                    limit_purchase_num=limit_purchase_num,
                    stock_limit_per_sku=stock_limit_per_sku,
                )
        except Exception as exc:
            self._write_operation_log(
                "activities",
                "创建限时抢购",
                f"限时抢购创建失败：{store.name}",
                store_id=store.id,
                status="failed",
                detail={
                    "product_ids": product_ids,
                    "discount": discount,
                    "error": str(exc),
                },
            )
            raise
        success_count = int(result.get("activity_count") or 0)
        failed_count = int(result.get("failed_count") or 0)
        log_status = (
            "failed"
            if failed_count and not success_count
            else "partial"
            if failed_count
            else "success"
        )
        message = (
            f"限时抢购创建完成：{success_count} 场，"
            f"失败 {failed_count}，跳过 {int(result.get('skipped_count') or 0)}"
        )
        self._write_operation_log(
            "activities",
            "创建限时抢购",
            message,
            store_id=store.id,
            status=log_status,
            detail={
                "requested_count": int(result.get("requested_count") or 0),
                "success_count": success_count,
                "failed_count": failed_count,
                "skipped_count": int(result.get("skipped_count") or 0),
                "already_active_count": int(
                    result.get("already_active_count") or 0
                ),
            },
        )
        return result

    def _delete_snapup_activity(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        service = self._require_product_service()
        store = self._require_store(self._store_id(payload))
        activity_id = str(payload.get("activity_id") or "").strip()
        if not activity_id:
            raise ValueError("activity_id 不能为空")
        try:
            with self._allow_platform_write(store, "activity_delete"):
                result = service.delete_snapup_activity(
                    store,
                    activity_id,
                )
        except Exception as exc:
            self._write_operation_log(
                "activities",
                "删除限时抢购",
                f"限时抢购删除失败：{store.name} · {activity_id}",
                store_id=store.id,
                status="failed",
                detail={"activity_id": activity_id, "error": str(exc)},
            )
            raise
        self._write_operation_log(
            "activities",
            "删除限时抢购",
            f"限时抢购已删除：{store.name} · {activity_id}",
            store_id=store.id,
            detail={"activity_id": activity_id},
        )
        return {
            "activity_id": activity_id,
            "result": result,
        }

    def _double11_preview(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        service = self._require_product_service()
        store = self._require_store(self._store_id(payload))
        activity_id = self._activity_id(payload)
        return service.double11_activity_products(store, activity_id)

    def _double11_draft_setting_key(self, store_id: int) -> str:
        return f"double11_prefill_{store_id}"

    def _list_double11_drafts(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        store = self._require_store(self._store_id(payload))
        drafts = self._read_json_list(
            self.db.get_setting(
                self._double11_draft_setting_key(store.id),
                "[]",
            )
        )
        return {
            "store_id": store.id,
            "drafts": drafts,
            "count": len(drafts),
        }

    def _save_double11_drafts(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        store = self._require_store(self._store_id(payload))
        incoming = payload.get("drafts")
        if not isinstance(incoming, list):
            raise ValueError("预填报草稿格式无效")
        blacklist = self.db.violation_blacklist_ids()
        normalized: list[dict[str, Any]] = []
        seen: set[str] = set()
        for row in incoming:
            if not isinstance(row, dict):
                continue
            product_id = str(row.get("product_id") or "").strip()
            if (
                not product_id
                or product_id in seen
                or product_id in blacklist
            ):
                continue
            try:
                activity_price = int(row.get("activity_price") or 0)
                activity_stock = int(row.get("activity_stock") or 0)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"商品 {product_id} 的活动价或库存无效"
                ) from exc
            if activity_price <= 0 or activity_stock < 0:
                raise ValueError(
                    f"商品 {product_id} 的活动价必须大于 0，库存不能为负"
                )
            seen.add(product_id)
            normalized.append(
                {
                    "product_id": product_id,
                    "title": str(row.get("title") or ""),
                    "activity_price": activity_price,
                    "activity_stock": activity_stock,
                }
            )
        self.db.set_setting(
            self._double11_draft_setting_key(store.id),
            json.dumps(normalized, ensure_ascii=False),
        )
        self._write_operation_log(
            "activities",
            "保存活动预填草稿",
            f"活动预填草稿已保存：{store.name} · {len(normalized)} 个商品",
            store_id=store.id,
            detail={"count": len(normalized)},
        )
        return {
            "store_id": store.id,
            "drafts": normalized,
            "count": len(normalized),
        }

    def _double11_submit(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        service = self._require_product_service()
        store = self._require_store(self._store_id(payload))
        activity_id = self._activity_id(payload)
        drafts = payload.get("drafts")
        if not isinstance(drafts, list) or not drafts:
            drafts = self._read_json_list(
                self.db.get_setting(
                    f"double11_prefill_{store.id}",
                    "[]",
                )
            )
        with self._allow_platform_write(
            store,
            "platform_activity_double11_submit",
        ):
            return service.double11_submit(
                store,
                drafts,
                activity_id,
            )

    def _double11_remove_all(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        service = self._require_product_service()
        store = self._require_store(self._store_id(payload))
        activity_id = self._activity_id(payload)
        with self._allow_platform_write(
            store,
            "platform_activity_items_remove",
        ):
            return service.double11_remove_all(store, activity_id)

    def _activity_operations(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        raw_store_id = payload.get("store_id")
        if raw_store_id == ALL_STORES_KEY:
            store = None
        elif raw_store_id in (None, ""):
            store = None
        else:
            store = self._require_store(self._store_id(payload))
        rows = self.db.list_activity_operations(
            limit=max(1, min(500, int(payload.get("limit") or 100)))
        )
        return {
            "operations": [
                row
                for row in rows
                if store is None
                or int(row.get("store_id") or 0) == store.id
            ]
        }

    def _require_activity_renewal_manager(
        self,
    ) -> ActivityAutoRenewalManager:
        manager = self.activity_renewal_manager
        if manager is None:
            raise RuntimeError(
                "自动续期需要真实商品服务和网页登录会话"
            )
        return manager

    def _activity_renewal_status(
        self,
        _payload: dict[str, Any],
    ) -> dict[str, Any]:
        manager = self._require_activity_renewal_manager()
        with self._activity_renewal_jobs_lock:
            active_job = next(
                (
                    dict(job)
                    for job in self._activity_renewal_jobs.values()
                    if job.get("status") in {"starting", "running"}
                ),
                None,
            )
        self._restore_activity_smart_cloud_settings()
        return {
            **manager.snapshot(),
            "active_job": active_job,
        }

    def _save_smart_auto_rules(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        manager = self._require_activity_renewal_manager()
        rules = payload.get("rules")
        if not isinstance(rules, dict):
            raise ValueError("智能活动规则必须是对象")
        manager.save_auto_create_rules(rules)
        self._mark_activity_cloud_pending()
        self._push_activity_smart_cloud_settings()
        return manager.snapshot()

    def _stop_smart_auto_run(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        manager = self._require_activity_renewal_manager()
        try:
            run_id = int(payload.get("run_id"))
        except (TypeError, ValueError) as exc:
            raise ValueError("run_id 必须是整数") from exc
        row = manager.stop_auto_create(run_id)
        return {"run": row, **manager.snapshot()}

    @staticmethod
    def _activity_cloud_setting_value() -> str:
        return json.dumps(
            {
                "enabled": True,
                "schema_version": 1,
            },
            ensure_ascii=False,
        )

    def _activity_cloud_payload(self) -> dict[str, Any]:
        manager = self._require_activity_renewal_manager()
        snapshot = manager.snapshot()
        return {
            "schema_version": 1,
            "enabled": bool(snapshot.get("enabled")),
            "interval_minutes": int(snapshot.get("interval_minutes") or 10),
            "rules": snapshot.get("auto_create_rules") or {},
            "stores": [
                {
                    "store_id": int(row.get("store_id") or 0),
                    "enabled": bool(row.get("enabled")),
                    "hours_before": int(row.get("hours_before") or 6),
                    "duration_days": int(row.get("duration_days") or 7),
                }
                for row in snapshot.get("stores") or []
            ],
        }

    def _mark_activity_cloud_pending(self) -> None:
        self.db.set_setting(
            "activity_snapup_auto_cloud_status",
            json.dumps(
                {
                    "status": "pending",
                    "error_category": "",
                    "updated_at": datetime.now().astimezone().isoformat(
                        timespec="seconds"
                    ),
                },
                ensure_ascii=False,
            ),
        )

    def _push_activity_smart_cloud_settings(self) -> None:
        client = self.auth_client
        token = self._cloud_violation_token()
        upsert = getattr(client, "upsert_organization_settings", None)
        if not token or not callable(upsert):
            self.db.set_setting(
                "activity_snapup_auto_cloud_status",
                json.dumps(
                    {
                        "status": "local_only",
                        "error_category": "auth_unavailable",
                        "updated_at": datetime.now().astimezone().isoformat(
                            timespec="seconds"
                        ),
                    },
                    ensure_ascii=False,
                ),
            )
            return
        with self._activity_cloud_lock:
            self._activity_cloud_push_pending = True
            if (
                self._activity_cloud_push_thread is not None
                and self._activity_cloud_push_thread.is_alive()
            ):
                return

            def worker() -> None:
                while True:
                    with self._activity_cloud_lock:
                        self._activity_cloud_push_pending = False
                    try:
                        payload = self._activity_cloud_payload()
                        result = upsert(
                            token,
                            [
                                {
                                    "key": "activity_smart_automation",
                                    "value": json.dumps(
                                        payload,
                                        ensure_ascii=False,
                                    ),
                                },
                                {
                                    "key": "activity_auto_renewal_enabled",
                                    "value": (
                                        "1"
                                        if payload["enabled"]
                                        else "0"
                                    ),
                                },
                                {
                                    "key": (
                                        "activity_auto_renewal_"
                                        "interval_minutes"
                                    ),
                                    "value": str(
                                        payload["interval_minutes"]
                                    ),
                                },
                            ],
                        )
                        if int(result.get("saved") or 0) < 1:
                            raise RuntimeError("云端未确认设置保存")
                    except Exception as exc:
                        status = {
                            "status": "failed",
                            "error_category": type(exc).__name__,
                            "updated_at": (
                                datetime.now()
                                .astimezone()
                                .isoformat(timespec="seconds")
                            ),
                        }
                    else:
                        status = {
                            "status": "synced",
                            "error_category": "",
                            "updated_at": (
                                datetime.now()
                                .astimezone()
                                .isoformat(timespec="seconds")
                            ),
                        }
                    self.db.set_setting(
                        "activity_snapup_auto_cloud_status",
                        json.dumps(status, ensure_ascii=False),
                    )
                    with self._activity_cloud_lock:
                        if not self._activity_cloud_push_pending:
                            self._activity_cloud_push_thread = None
                            return

            thread = threading.Thread(
                target=worker,
                daemon=True,
                name="activity-smart-local-push",
            )
            self._activity_cloud_push_thread = thread
            thread.start()

    def _restore_activity_smart_cloud_settings(self) -> None:
        client = self.auth_client
        token = self._cloud_violation_token()
        pull = getattr(client, "pull_organization_settings", None)
        if not token or not callable(pull):
            return
        with self._activity_cloud_lock:
            if self._activity_cloud_restore_attempted:
                return
            self._activity_cloud_restore_attempted = True
            if (
                self._activity_cloud_restore_thread is not None
                and self._activity_cloud_restore_thread.is_alive()
            ):
                return

            def worker() -> None:
                try:
                    rows = pull(token)
                    raw = next(
                        (
                            str(row.get("value") or "")
                            for row in rows
                            if isinstance(row, dict)
                            and row.get("key")
                            == "activity_smart_automation"
                        ),
                        "",
                    )
                    if not raw:
                        return
                    payload = json.loads(raw)
                    if (
                        not isinstance(payload, dict)
                        or int(payload.get("schema_version") or 0) != 1
                    ):
                        raise ValueError("云端智能活动配置版本不支持")
                    manager = self._require_activity_renewal_manager()
                    manager.save_global_config(
                        enabled=bool(payload.get("enabled")),
                        interval_minutes=int(
                            payload.get("interval_minutes") or 10
                        ),
                    )
                    rules = payload.get("rules")
                    if isinstance(rules, dict):
                        manager.save_auto_create_rules(rules)
                    for row in payload.get("stores") or []:
                        if not isinstance(row, dict):
                            continue
                        store_id = int(row.get("store_id") or 0)
                        if self.db.get_store(store_id) is None:
                            continue
                        manager.save_store_config(
                            store_id,
                            enabled=bool(row.get("enabled")),
                            hours_before=int(
                                row.get("hours_before") or 6
                            ),
                            duration_days=int(
                                row.get("duration_days") or 7
                            ),
                        )
                except Exception as exc:
                    status = {
                        "status": "sync_failed",
                        "error_category": type(exc).__name__,
                        "updated_at": datetime.now().astimezone().isoformat(
                            timespec="seconds"
                        ),
                    }
                else:
                    if not raw:
                        return
                    status = {
                        "status": "synced",
                        "error_category": "",
                        "updated_at": datetime.now().astimezone().isoformat(
                            timespec="seconds"
                        ),
                    }
                self.db.set_setting(
                    "activity_snapup_auto_cloud_status",
                    json.dumps(status, ensure_ascii=False),
                )

            thread = threading.Thread(
                target=worker,
                daemon=True,
                name="activity-smart-cloud-restore",
            )
            self._activity_cloud_restore_thread = thread
            thread.start()

    def _save_activity_renewal_config(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        manager = self._require_activity_renewal_manager()
        if "enabled" in payload or "interval_minutes" in payload:
            current = manager.snapshot()
            manager.save_global_config(
                enabled=bool(
                    payload.get("enabled", current["enabled"])
                ),
                interval_minutes=int(
                    payload.get(
                        "interval_minutes",
                        current["interval_minutes"],
                    )
                ),
            )
        store_configs = payload.get("stores")
        if store_configs is not None:
            if not isinstance(store_configs, list):
                raise ValueError("店铺自动续期配置格式无效")
            for row in store_configs:
                if not isinstance(row, dict):
                    continue
                store = self._require_store(self._store_id(row))
                hours_before = row.get("hours_before")
                duration_days = row.get("duration_days")
                manager.save_store_config(
                    store.id,
                    enabled=bool(row.get("enabled")),
                    hours_before=(
                        None
                        if hours_before in (None, "")
                        else int(hours_before)
                    ),
                    duration_days=(
                        None
                        if duration_days in (None, "")
                        else int(duration_days)
                    ),
                )
        self._mark_activity_cloud_pending()
        self._push_activity_smart_cloud_settings()
        self._write_operation_log(
            "activities",
            "保存自动续期配置",
            "自动续期限时抢购配置已保存",
            detail={
                "enabled": payload.get("enabled"),
                "interval_minutes": payload.get("interval_minutes"),
                "store_count": len(store_configs or []),
            },
        )
        return manager.snapshot()

    def _start_activity_renewal_scan(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        manager = self._require_activity_renewal_manager()
        raw_store_ids = payload.get("store_ids")
        if raw_store_ids in (None, [], ""):
            store_ids = [
                int(store.id)
                for store in self.db.list_stores()
                if manager.db.get_activity_renewal_config(store.id).get(
                    "enabled"
                )
            ]
        elif not isinstance(raw_store_ids, list):
            raise ValueError("store_ids 必须是数组")
        else:
            store_ids = sorted(
                {
                    int(value)
                    for value in raw_store_ids
                    if str(value).strip().isdigit()
                }
            )
        if not store_ids:
            raise ValueError("没有已启用自动续期的店铺")
        with self._activity_renewal_jobs_lock:
            for existing in self._activity_renewal_jobs.values():
                if existing.get("status") in {"starting", "running"}:
                    return {
                        "job_id": existing["job_id"],
                        "already_running": True,
                    }
            job_id = uuid.uuid4().hex
            self._activity_renewal_jobs[job_id] = {
                "job_id": job_id,
                "status": "starting",
                "current": 0,
                "total": len(store_ids),
                "message": "正在准备自动续期检查…",
                "errors": [],
                "results": [],
            }

        def update(**changes: Any) -> None:
            with self._activity_renewal_jobs_lock:
                job = self._activity_renewal_jobs.get(job_id)
                if job is not None:
                    job.update(changes)

        def progress(message: str, current: int, total: int) -> None:
            update(
                status="running",
                message=message,
                current=max(0, current),
                total=max(0, total),
            )

        def worker() -> None:
            try:
                result = manager.run_due(
                    store_ids=store_ids,
                    force=True,
                    progress=progress,
                )
                store_results = list(result.get("stores") or [])
                errors = [
                    str(row.get("error") or "")
                    for row in store_results
                    if row.get("status") in {
                        "failed",
                        "verification_pending",
                    }
                ]
                successful = sum(
                    row.get("status") == "success"
                    for row in store_results
                )
                update(
                    status="completed",
                    current=len(store_results),
                    total=len(store_results),
                    message=(
                        f"自动续期检查完成：成功 {successful}，"
                        f"需处理 {len(errors)}"
                    ),
                    errors=errors,
                    results=store_results,
                )
            except Exception as exc:
                update(
                    status="failed",
                    message=str(exc),
                    errors=[str(exc)],
                )

        threading.Thread(
            target=worker,
            name=f"webui-activity-renewal-{job_id[:8]}",
            daemon=True,
        ).start()
        return {
            "job_id": job_id,
            "already_running": False,
        }

    def _activity_renewal_scan_status(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        job_id = str(payload.get("job_id") or "")
        if not job_id:
            raise ValueError("job_id 不能为空")
        with self._activity_renewal_jobs_lock:
            job = self._activity_renewal_jobs.get(job_id)
            if job is None:
                raise ValueError("自动续期检查任务不存在")
            return dict(job)

    @staticmethod
    def _activity_id(payload: dict[str, Any]) -> int:
        try:
            activity_id = int(payload.get("activity_id"))
        except (TypeError, ValueError) as exc:
            raise ValueError("activity_id 必须是整数") from exc
        if activity_id <= 0:
            raise ValueError("activity_id 必须大于 0")
        return activity_id

    @staticmethod
    def _read_json_list(value: str) -> list[dict[str, Any]]:
        try:
            rows = json.loads(value or "[]")
        except (TypeError, ValueError, json.JSONDecodeError):
            return []
        if not isinstance(rows, list):
            return []
        return [dict(row) for row in rows if isinstance(row, dict)]

    def _upload_default_template_id(self) -> str:
        return str(
            self.db.get_setting("upload_default_template_id", "") or ""
        ).strip()

    def _upload_templates(self) -> list[dict[str, Any]]:
        rows = self._read_json_list(
            self.db.get_setting("upload_templates", "[]")
        )
        default_id = self._upload_default_template_id()
        migrated = False
        for row in rows:
            if "cloud_violation" not in row:
                row["cloud_violation"] = True
                migrated = True
        if migrated:
            self._save_upload_templates(rows)
        for row in rows:
            row["is_default"] = bool(
                default_id and str(row.get("id") or "") == default_id
            )
        return rows

    def _save_upload_templates(self, rows: list[dict[str, Any]]) -> None:
        self.db.set_setting(
            "upload_templates",
            json.dumps(rows, ensure_ascii=False),
        )

    def _upload_cloud_violation_types(self) -> list[str]:
        types: set[str] = set()
        active_stores = self.db.list_stores()
        for setting_key in (
            "violation_library",
            "upload_violation_titles",
        ):
            rows = self._read_json_list(
                self.db.get_setting(setting_key, "[]")
            )
            rows = filter_active_violation_records(
                rows,
                active_stores,
            )
            for row in rows:
                reason = str(row.get("reason") or "").strip()
                if reason:
                    types.add(reason)
        for row in self._read_json_list(
            self.db.get_setting(
                "cloud_counterfeit_violation_titles",
                "[]",
            )
        ):
            reason = str(row.get("reason") or "").strip()
            if reason:
                types.add(reason)
        return sorted(types)

    def _normalize_upload_template(
        self,
        incoming: dict[str, Any],
        existing: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        template = json.loads(json.dumps(UPLOAD_DEFAULT_TEMPLATE))
        template.update(existing or {})
        template.update(incoming)
        template["name"] = str(template.get("name") or "").strip()
        if not template["name"]:
            raise ValueError("模板名称不能为空")
        try:
            template["price_multiplier"] = max(
                0.0,
                float(template.get("price_multiplier") or 0),
            )
            template["price_offset"] = int(template.get("price_offset") or 0)
            template["fixed_price"] = max(
                0,
                int(template.get("fixed_price") or 0),
            )
            template["final_price_decimals"] = max(
                0,
                min(
                    4,
                    int(template.get("final_price_decimals") or 0),
                ),
            )
            template["stock"] = max(0, int(template.get("stock") or 0))
            template["deposit_limit"] = max(
                0,
                int(template.get("deposit_limit") or 0),
            )
            template["low_price_multiplier"] = max(
                1.0,
                float(template.get("low_price_multiplier") or 2),
            )
            template["trim_head"] = max(
                0,
                int(template.get("trim_head") or 0),
            )
            template["trim_detail"] = max(
                0,
                int(template.get("trim_detail") or 0),
            )
            template["trim_detail_tail"] = max(
                0,
                int(template.get("trim_detail_tail") or 0),
            )
            template["detail_keep_first"] = max(
                0,
                int(template.get("detail_keep_first") or 0),
            )
            template["remove_head_index"] = max(
                0,
                int(template.get("remove_head_index") or 0),
            )
            template["remove_detail_index"] = max(
                0,
                int(template.get("remove_detail_index") or 0),
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("模板价格、库存、保证金或裁剪参数无效") from exc
        if template.get("listing_mode") not in {
            "immediate",
            "warehouse",
            "draft",
        }:
            raise ValueError("发布方式必须为 immediate、warehouse 或 draft")
        if template.get("duplicate_filter_mode") not in {
            "store",
            "computer",
            "off",
        }:
            raise ValueError("去重方式必须为 store、computer 或 off")
        if template.get("category_business_type") not in {
            "individual",
            "enterprise",
        }:
            raise ValueError("类目保证金业务类型必须为 individual 或 enterprise")
        if template.get("delivery_type") not in {"现货", "预售"}:
            raise ValueError("发货类型必须是现货或预售")
        if template.get("delivery_time") not in {
            "48小时内发货",
            "7天内发货",
            "15天内发货",
        }:
            raise ValueError("发货时效不在允许范围内")
        if template.get("image_mode") not in {"默认方式", "OCR 识图"}:
            raise ValueError("图片模式无效")
        if template.get("head_action") not in {
            "删除违规图片",
            "跳过商品",
        } or template.get("detail_action") not in {
            "删除违规图片",
            "跳过商品",
        }:
            raise ValueError("图片命中处理方式无效")
        if template.get("low_price_action") not in {
            "跳过商品",
            "删除最低价",
        }:
            raise ValueError("低价规格处理方式无效")
        raw_category_ids = template.get("specified_category_ids")
        if isinstance(raw_category_ids, str):
            try:
                raw_category_ids = json.loads(raw_category_ids or "[]")
            except (TypeError, ValueError, json.JSONDecodeError):
                raw_category_ids = []
        template["specified_category_ids"] = list(
            dict.fromkeys(
                str(value).strip()
                for value in raw_category_ids or []
                if str(value).strip().isdigit()
            )
        ) if isinstance(raw_category_ids, list) else []
        template["specified_category_id"] = str(
            template.get("specified_category_id") or ""
        ).strip()
        template["specified_category_path"] = str(
            template.get("specified_category_path") or ""
        ).strip()
        template["specified_category_version"] = str(
            template.get("specified_category_version") or ""
        ).strip()
        template["specified_category_store_id"] = str(
            template.get("specified_category_store_id") or ""
        ).strip()
        if template["specified_category_id"]:
            if (
                not template["specified_category_ids"]
                or template["specified_category_id"]
                != template["specified_category_ids"][-1]
                or not template["specified_category_path"]
                or not template["specified_category_store_id"].isdigit()
            ):
                raise ValueError(
                    "指定商品类目快照不完整，请重新选择叶子类目"
                )
            template["category_mode"] = "specified"
        else:
            template["specified_category_ids"] = []
            template["specified_category_path"] = ""
            template["specified_category_version"] = ""
            template["specified_category_store_id"] = ""
            template["category_mode"] = "auto"
        ladder_rules = str(template.get("ladder_rules") or "")
        for raw_line in ladder_rules.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            parts = [part.strip() for part in line.split("=")]
            if len(parts) != 4:
                raise ValueError(
                    "阶梯倍率每行必须填写：最低原价=最高原价=倍率=加价"
                )
            try:
                low, high, multiplier, _offset = map(float, parts)
            except ValueError as exc:
                raise ValueError("阶梯倍率参数必须是数字") from exc
            if low < 0 or high < low or multiplier <= 0:
                raise ValueError("阶梯倍率的价格区间或倍率无效")
        license_groups = str(
            template.get("category_license_groups") or ""
        ).strip()
        if license_groups:
            try:
                parsed_groups = json.loads(license_groups)
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                raise ValueError("申请证照组必须是 JSON 数组") from exc
            if not isinstance(parsed_groups, list):
                raise ValueError("申请证照组必须是 JSON 数组")
        template["category_license_groups"] = license_groups
        for key in (
            "fixed_price_enabled",
            "ladder_enabled",
            "main_image_video",
            "use_package_tags",
            "read_packaging_labels",
            "clothing_mode",
            "seven_day_return",
            "shipping_insurance",
            "damage_return",
            "fake_one_three",
            "support_exchange",
            "auto_open_category",
            "force_fallback_category",
            "cloud_violation",
            "strip_title_brackets",
            "enable_custom_attributes",
            "low_price_enabled",
            "skip_duplicate",
        ):
            template[key] = bool(template.get(key))
        template["skip_duplicate"] = (
            template.get("duplicate_filter_mode") != "off"
        )
        template["save_as_draft"] = template.get("listing_mode") == "draft"
        template["category_mode"] = (
            "specified"
            if template.get("specified_category_id")
            else "auto"
        )
        template["auto_open_category"] = True
        template["cloud_violation_types"] = str(
            template.get("cloud_violation_types") or ""
        ).strip()
        return template

    def _upload_batch_rows(self) -> list[dict[str, Any]]:
        return self._read_json_list(
            self.db.get_setting("upload_batch", "[]")
        )

    def _upload_tasks(self) -> list[dict[str, Any]]:
        from ..ui.upload_page import _recover_upload_tasks

        active_job = self._active_upload_job("run")
        active_runner = active_job.get("runner") if active_job else None
        if active_runner is not None:
            return active_runner.tasks
        tasks = self._read_json_list(
            self.db.get_setting("upload_tasks", "[]")
        )
        if _recover_upload_tasks(tasks):
            self.db.set_setting(
                "upload_tasks",
                json.dumps(tasks, ensure_ascii=False),
            )
        return tasks

    def _save_upload_batch(self, rows: list[dict[str, Any]]) -> None:
        self.db.set_setting(
            "upload_batch",
            json.dumps(rows, ensure_ascii=False),
        )

    def _save_upload_tasks(self, tasks: list[dict[str, Any]]) -> None:
        self.db.set_setting(
            "upload_tasks",
            json.dumps(tasks, ensure_ascii=False),
        )

    def _schedule_upload_category_resume(
        self,
        tasks: list[dict[str, Any]],
        *,
        minimum_delay: float = 0.0,
    ) -> None:
        if not self._upload_category_auto_resume_enabled():
            return
        waiting_items = [
            (task, item)
            for task in tasks
            for item in task.get("items") or []
            if item.get("status") == "waiting_category"
        ]
        if not waiting_items:
            return
        with self._upload_category_timer_lock:
            if (
                self._upload_category_timer is not None
                and self._upload_category_timer.is_alive()
            ):
                return
            try:
                fallback_delay = float(
                    os.environ.get(
                        "LANG_LANG_UPLOAD_CATEGORY_RETRY_SECONDS",
                        "600",
                    )
                )
            except ValueError:
                fallback_delay = 600.0
            try:
                active_delay = max(
                    30.0,
                    float(
                        os.environ.get(
                            "LANG_LANG_UPLOAD_CATEGORY_ACTIVE_RETRY_SECONDS",
                            "60",
                        )
                    ),
                )
            except ValueError:
                active_delay = 60.0
            now = datetime.now()
            next_times = []
            for _task, item in waiting_items:
                raw_next = str(item.get("category_next_check_at") or "")
                if not raw_next:
                    continue
                try:
                    next_times.append(datetime.fromisoformat(raw_next))
                except ValueError:
                    continue
            delay = (
                max(0.05, (min(next_times) - now).total_seconds())
                if next_times
                else max(0.05, fallback_delay)
            )
            delay = max(delay, minimum_delay)

            def resume() -> None:
                with self._upload_category_timer_lock:
                    self._upload_category_timer = None
                current_tasks = self._upload_tasks()
                due_by_task: dict[str, set[str]] = {}
                current_time = datetime.now()
                for task in current_tasks:
                    for item in task.get("items") or []:
                        if item.get("status") != "waiting_category":
                            continue
                        raw_next = str(
                            item.get("category_next_check_at") or ""
                        )
                        if raw_next:
                            try:
                                next_time = datetime.fromisoformat(raw_next)
                            except ValueError:
                                next_time = current_time
                            if next_time > current_time:
                                continue
                        due_by_task.setdefault(
                            str(task.get("id") or ""),
                            set(),
                        ).add(str(item.get("item_id") or ""))
                if not due_by_task:
                    self._schedule_upload_category_resume(current_tasks)
                    return
                if self._active_upload_job("run"):
                    self._schedule_upload_category_resume(
                        current_tasks,
                        minimum_delay=active_delay,
                    )
                    return
                try:
                    self._start_upload_job(
                        {
                            "task_ids": sorted(due_by_task),
                            "item_ids": sorted(
                                item_id
                                for values in due_by_task.values()
                                for item_id in values
                            ),
                        }
                    )
                except Exception:
                    self._schedule_upload_category_resume(
                        current_tasks,
                        minimum_delay=max(1.0, fallback_delay),
                    )

            timer = threading.Timer(max(0.05, delay), resume)
            timer.daemon = True
            self._upload_category_timer = timer
            timer.start()

    def _upload_category_auto_resume_enabled(self) -> bool:
        return (
            self.db.get_setting(
                "upload_category_auto_resume_enabled",
                "1",
            )
            != "0"
        )

    def _set_upload_category_auto_resume(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        enabled = bool(payload.get("enabled"))
        self.db.set_setting(
            "upload_category_auto_resume_enabled",
            "1" if enabled else "0",
        )
        if not enabled:
            with self._upload_category_timer_lock:
                timer = self._upload_category_timer
                self._upload_category_timer = None
            if timer is not None:
                timer.cancel()
        else:
            self._schedule_upload_category_resume(
                self._upload_tasks(),
                minimum_delay=0.05,
            )
        return {
            "enabled": enabled,
            "tasks": self._upload_tasks(),
        }

    def _cancel_upload_waiting_category(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        task_ids = {
            str(value)
            for value in payload.get("task_ids", [])
            if str(value).strip()
        }
        item_ids = {
            str(value)
            for value in payload.get("item_ids", [])
            if str(value).strip()
        }
        if not task_ids and not item_ids:
            raise ValueError("请选择要取消等待的商品")
        tasks = self._upload_tasks()
        cancelled = 0
        now = datetime.now().isoformat(timespec="seconds")
        for task in tasks:
            if (
                task_ids
                and str(task.get("id") or "") not in task_ids
            ):
                continue
            task_cancelled = 0
            for item in task.get("items") or []:
                if item.get("status") != "waiting_category":
                    continue
                if (
                    item_ids
                    and str(item.get("item_id") or "") not in item_ids
                ):
                    continue
                item["status"] = "cancelled"
                item["category_state"] = "cancelled"
                item["category_last_checked_at"] = now
                item["category_next_check_at"] = ""
                item["message"] = "已取消类目等待，不会自动续传"
                item["finished_at"] = now
                cancelled += 1
                task_cancelled += 1
            waiting = sum(
                item.get("status") == "waiting_category"
                for item in task.get("items") or []
            )
            if waiting:
                task["status"] = "needs_category"
                task["message"] = f"{waiting} 个商品继续等待类目"
            elif task_cancelled:
                task["status"] = "paused"
                task["message"] = "类目等待已取消"
        self._save_upload_tasks(tasks)
        self._schedule_upload_category_resume(tasks)
        return {"cancelled": cancelled, "tasks": tasks}

    def _merge_upload_rows(
        self,
        rows: list[dict[str, Any]],
        *,
        append: bool = True,
    ) -> dict[str, Any]:
        current = self._upload_batch_rows() if append else []
        known = {
            _upload_identity_key(row)
            for row in current
            if _upload_identity_key(row)[1]
        }
        existing_batch_ids = {
            str(row.get("batch_id") or "")
            for row in current
            if str(row.get("batch_id") or "")
        }
        added_rows: list[dict[str, Any]] = []
        skipped = 0
        for raw in rows:
            if not isinstance(raw, dict):
                continue
            row = dict(raw)
            identity = _upload_identity_key(row)
            if not identity[1] or identity in known:
                skipped += 1
                continue
            batch_id = str(row.get("batch_id") or "").strip()
            if not batch_id or batch_id in existing_batch_ids:
                batch_id = uuid.uuid4().hex
            row["batch_id"] = batch_id
            row["platform"] = identity[0] or "未识别"
            row["source_product_id"] = identity[1]
            row["title"] = str(row.get("title") or "").strip()
            row["price"] = max(0, int(row.get("price") or 0))
            if row.get("stock") is not None:
                row["stock"] = max(0, int(row.get("stock") or 0))
            current.append(row)
            added_rows.append(row)
            existing_batch_ids.add(batch_id)
            known.add(identity)
        self._save_upload_batch(current)
        return {
            "batch": current,
            "added": len(added_rows),
            "skipped": skipped,
            "added_rows": added_rows,
        }

    @staticmethod
    def _public_upload_job(job: dict[str, Any]) -> dict[str, Any]:
        return {
            key: value
            for key, value in job.items()
            if key not in {
                "runner",
                "pause_requested",
                "cancel_requested",
            }
            and not key.startswith("_")
        }

    def _active_upload_job(self, kind: str) -> dict[str, Any] | None:
        with self._upload_jobs_lock:
            job = next(
                (
                    job
                    for job in self._upload_jobs.values()
                    if job.get("kind") == kind
                    and job.get("status")
                    not in UPLOAD_JOB_TERMINAL_STATUSES
                ),
                None,
            )
            return dict(job) if job is not None else None

    def _list_upload(self, _payload: dict[str, Any]) -> dict[str, Any]:
        templates = self._upload_templates()
        batch = self._upload_batch_rows()
        tasks = self._upload_tasks()
        self._schedule_upload_category_resume(tasks)
        active_run_job = self._active_upload_job("run")
        stores = [
            {
                "id": store.id,
                "name": store.name,
                "group_name": store.group_name,
                "web_status": store.web_status,
            }
            for store in self.db.list_stores()
        ]
        accounts = self._upload_procurement_accounts()
        violation_titles = self._upload_violation_title_rows()
        return {
            "templates": templates,
            "default_template_id": self._upload_default_template_id(),
            "cloud_violation_types": self._upload_cloud_violation_types(),
            "batch": batch,
            "tasks": tasks,
            "stores": stores,
            "procurement_accounts": accounts,
            "template_defaults": UPLOAD_DEFAULT_TEMPLATE,
            "violation_titles": violation_titles,
            "violation_title_count": len(violation_titles),
            "category_auto_resume_enabled": (
                self._upload_category_auto_resume_enabled()
            ),
            "active_run_job": (
                self._public_upload_job(active_run_job)
                if active_run_job is not None
                else None
            ),
        }

    def _upload_procurement_accounts(self) -> list[dict[str, Any]]:
        return [
            {
                "id": row["id"],
                "label": row["label"],
                "nickname": row["nickname"],
                "status": row["status"],
            }
            for row in self._list_procurement({"platform": "1688"})["accounts"]
        ]

    def _import_upload_batch(self, payload: dict[str, Any]) -> dict[str, Any]:
        files = payload.get("files")
        if not isinstance(files, list) or not files:
            raise ValueError("请选择至少一个数据包文件")
        from ..ui.upload_page import UploadPage as LegacyUploadPage

        parsed_rows: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        accepted_names: list[str] = []
        detected_formats: set[str] = set()
        with tempfile.TemporaryDirectory(prefix="langlang-upload-") as temp_dir:
            root = Path(temp_dir)
            for index, item in enumerate(files):
                if not isinstance(item, dict):
                    continue
                display_name = Path(str(item.get("name") or "")).name
                suffix = Path(display_name).suffix.lower()
                content = item.get("content")
                if suffix not in UPLOAD_PACKAGE_SUFFIXES:
                    errors.append(
                        {
                            "file": display_name or f"文件 {index + 1}",
                            "error": "仅支持 JSON、JSONL、CSV、TSV、TXT 数据包",
                        }
                    )
                    continue
                if not isinstance(content, str) or not content.strip():
                    errors.append(
                        {
                            "file": display_name or f"文件 {index + 1}",
                            "error": "文件内容为空或无法读取为文本",
                        }
                    )
                    continue
                if len(content) > UPLOAD_MAX_FILE_CHARS:
                    errors.append(
                        {
                            "file": display_name,
                            "error": f"单个文件超过 {UPLOAD_MAX_FILE_CHARS // 1_000_000} MB 限制",
                        }
                    )
                    continue
                safe_name = f"upload-{index}{suffix}"
                path = root / safe_name
                path.write_text(content, encoding="utf-8")
                try:
                    rows = LegacyUploadPage._parse_package(None, path)
                except Exception as exc:
                    errors.append({"file": display_name, "error": str(exc)[:500]})
                    continue
                for row in rows:
                    row["source_file"] = display_name or path.name
                    parsed_rows.append(row)
                    payload = row.get("payload")
                    if (
                        isinstance(payload, dict)
                        and payload.get("_source_format")
                        == "wechat_json_package"
                    ):
                        detected_formats.add("微信 JSON 数据包")
                    elif suffix in {".csv", ".tsv", ".txt"}:
                        detected_formats.add("TXT/表格数据包")
                    elif suffix in {".json", ".jsonl"}:
                        detected_formats.add("通用 JSON 数据包")
                accepted_names.append(display_name)
        merged = self._merge_upload_rows(
            parsed_rows,
            append=bool(payload.get("append", True)),
        )
        if accepted_names:
            self.db.set_setting(
                "upload_last_source",
                accepted_names[-1],
            )
        return {
            "batch": merged["batch"],
            "imported": merged["added"],
            "skipped": merged["skipped"],
            "parsed": len(parsed_rows),
            "files": accepted_names,
            "formats": sorted(detected_formats),
            "errors": errors,
        }

    def _reparse_upload_batch(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        from ..ui.upload_page import _normalize_source_payload

        selected = {
            str(value)
            for value in payload.get("batch_ids", [])
            if str(value).strip()
        }
        rows = self._upload_batch_rows()
        previews: list[dict[str, Any]] = []
        changed = False
        for row in rows:
            if selected and str(row.get("batch_id") or "") not in selected:
                continue
            source = row.get("payload")
            if not isinstance(source, dict):
                source = row.get("raw")
            if not isinstance(source, dict):
                continue
            normalized = _normalize_source_payload(source)
            head_images = [
                str(value)
                for value in normalized.get("head_imgs") or []
                if str(value).strip()
            ]
            skus = [
                item
                for item in normalized.get("skus") or []
                if isinstance(item, dict)
            ]
            recoverable = bool(head_images and skus)
            if recoverable:
                row["payload"] = normalized
                row["title"] = str(
                    normalized.get("title") or row.get("title") or ""
                )
                source_id = str(
                    normalized.get("source_product_id")
                    or normalized.get("out_product_id")
                    or row.get("source_product_id")
                    or ""
                ).strip()
                if source_id:
                    row["source_product_id"] = source_id
                prices = [
                    int(item.get("sale_price") or 0)
                    for item in skus
                    if int(item.get("sale_price") or 0) > 0
                ]
                if prices:
                    row["price"] = min(prices)
                stocks = [
                    int(item.get("stock_num") or 0)
                    for item in skus
                    if item.get("stock_num") is not None
                ]
                if stocks:
                    row["stock"] = sum(stocks)
                    row["stock_known"] = True
                changed = True
            previews.append(
                {
                    "batch_id": row.get("batch_id"),
                    "source_product_id": (
                        normalized.get("source_product_id")
                        or row.get("source_product_id")
                    ),
                    "title": normalized.get("title")
                    or row.get("title"),
                    "recoverable": recoverable,
                    "sku_count": len(skus),
                    "head_image_count": len(head_images),
                    "adapter_sources": (
                        normalized.get("_adapter_sources") or []
                    ),
                    "adapter_errors": (
                        normalized.get("_adapter_errors") or []
                    ),
                    "adapter_conflicts": (
                        normalized.get("_adapter_conflicts") or []
                    ),
                }
            )
        if changed:
            self._save_upload_batch(rows)
        task_previews: list[dict[str, Any]] = []
        for task in self._upload_tasks():
            for item in task.get("items") or []:
                message = str(item.get("message") or "")
                if (
                    item.get("status") not in {
                        "failed",
                        "verification_pending",
                    }
                    and "head_imgs, skus" not in message
                ):
                    continue
                source = item.get("payload")
                if not isinstance(source, dict):
                    source = item.get("raw")
                if not isinstance(source, dict):
                    continue
                normalized = _normalize_source_payload(source)
                head_images = normalized.get("head_imgs") or []
                skus = normalized.get("skus") or []
                if not head_images and not skus:
                    continue
                task_previews.append(
                    {
                        "task_id": task.get("id"),
                        "item_id": item.get("item_id"),
                        "source_product_id": (
                            normalized.get("source_product_id")
                            or item.get("source_product_id")
                        ),
                        "title": normalized.get("title")
                        or item.get("title"),
                        "recoverable": bool(head_images and skus),
                        "sku_count": len(skus),
                        "head_image_count": len(head_images),
                        "adapter_conflicts": (
                            normalized.get("_adapter_conflicts") or []
                        ),
                    }
                )
        return {
            "batch_preview": previews,
            "batch_recoverable": sum(
                item["recoverable"] for item in previews
            ),
            "batch_blocked": sum(
                not item["recoverable"] for item in previews
            ),
            "task_preview": task_previews[:200],
            "task_recoverable": sum(
                item["recoverable"] for item in task_previews
            ),
            "task_preview_truncated": len(task_previews) > 200,
        }

    def _preflight_upload_batch(self, payload: dict[str, Any]) -> dict[str, Any]:
        from ..ui.upload_page import (
            TemplateRuleSkip,
            UploadPage as LegacyUploadPage,
            _normalize_source_payload,
        )

        selected = {
            str(value)
            for value in payload.get("batch_ids", [])
            if str(value).strip()
        }
        rows = [
            row
            for row in self._upload_batch_rows()
            if not selected or str(row.get("batch_id") or "") in selected
        ]
        template_id = str(
            payload.get("template_id")
            or self._upload_default_template_id()
        ).strip()
        template = next(
            (
                row
                for row in self._upload_templates()
                if str(row.get("id") or "") == template_id
            ),
            {},
        )
        if template:
            template = self._normalize_upload_template(
                template,
                template,
            )
        template_stock = max(0, int(template.get("stock") or 0))
        ready = 0
        results: list[dict[str, Any]] = []
        for row in rows:
            issues: list[str] = []
            warnings: list[str] = []
            mapping: dict[str, Any] = {}
            title = str(row.get("title") or "").strip()
            source_id = str(row.get("source_product_id") or "").strip()
            platform = str(row.get("platform") or "").strip()
            price = row.get("price")
            stock = row.get("stock")
            payload_data = row.get("payload")
            if not title:
                issues.append("缺少商品标题")
            if not source_id:
                issues.append("缺少稳定来源 ID")
            if platform not in {"1688", "淘宝", "微信小店"}:
                issues.append("来源平台未识别")
            if not isinstance(price, int) or price <= 0:
                issues.append("价格必须为正整数分")
            if stock is None and template_stock <= 0:
                issues.append("库存未知，需选择固定库存模板或补充来源库存")
            elif stock is not None and (not isinstance(stock, int) or stock < 0):
                issues.append("库存必须是非负整数")
            if not isinstance(payload_data, dict):
                issues.append("缺少标准化商品 payload")
            else:
                checked_payload = _normalize_source_payload(payload_data)
                images = (
                    checked_payload.get("head_imgs")
                    or checked_payload.get("headImg")
                    or checked_payload.get("itemImgs")
                    or checked_payload.get("images")
                    or checked_payload.get("imageList")
                )
                sku_list = checked_payload.get("skuList")
                skus = checked_payload.get("skus")
                if not skus and isinstance(sku_list, dict):
                    skus = sku_list.get("skuInfo")
                adapter_errors = checked_payload.get("_adapter_errors")
                if isinstance(adapter_errors, list):
                    issues.extend(
                        str(value)
                        for value in adapter_errors
                        if str(value).strip()
                    )
                if not images:
                    issues.append("缺少商品主图")
                if not skus:
                    issues.append("缺少 SKU")
                legacy_page = object.__new__(LegacyUploadPage)
                legacy_page.db = self.db
                try:
                    mapped = LegacyUploadPage._upload_payload(
                        legacy_page,
                        row,
                        template,
                    )
                except TemplateRuleSkip as exc:
                    issues.append(str(exc))
                except Exception as exc:
                    issues.append(f"老版字段匹配失败：{exc}")
                else:
                    mapped_skus = [
                        item
                        for item in mapped.get("skus") or []
                        if isinstance(item, dict)
                    ]
                    head_images = [
                        str(value)
                        for value in mapped.get("head_imgs") or []
                        if str(value).strip()
                    ]
                    detail_images = [
                        str(value)
                        for value in (mapped.get("desc_info") or {}).get("imgs") or []
                        if str(value).strip()
                    ]
                    source_head_count = int(
                        (
                            mapped.get("_source_image_counts")
                            or {}
                        ).get("head")
                        or 0
                    )
                    if not head_images and not source_head_count:
                        issues.append("老版映射后仍无商品主图")
                    elif not head_images:
                        warnings.append(
                            "主图处理规则已移除全部来源主图，上传时将使用店铺默认主图回退"
                        )
                    elif len(head_images) < 3:
                        warnings.append(
                            f"主图仅 {len(head_images)} 张，上传时将尝试使用店铺默认主图补足"
                        )
                    if not mapped_skus:
                        issues.append("老版映射后仍无有效 SKU")
                    invalid_price = sum(
                        int(item.get("sale_price") or 0) <= 0
                        for item in mapped_skus
                    )
                    invalid_stock = sum(
                        int(item.get("stock_num") or 0) < 0
                        for item in mapped_skus
                    )
                    missing_attrs = sum(
                        not item.get("sku_attrs")
                        for item in mapped_skus
                    )
                    if invalid_price:
                        issues.append(f"{invalid_price} 个 SKU 售价无效")
                    if invalid_stock:
                        issues.append(f"{invalid_stock} 个 SKU 库存为负数")
                    if missing_attrs:
                        issues.append(f"{missing_attrs} 个 SKU 缺少规格属性")
                    has_category = bool(
                        mapped.get("cats")
                        or mapped.get("cats_v2")
                        or mapped.get("category_path")
                    )
                    category_mode = "source" if has_category else "auto"
                    if not has_category:
                        warnings.append(
                            "来源数据没有微信类目，上传时将按店铺官方类目树自动匹配"
                        )
                    prices = [
                        int(item.get("sale_price") or 0)
                        for item in mapped_skus
                        if int(item.get("sale_price") or 0) > 0
                    ]
                    stocks = [
                        int(item.get("stock_num") or 0)
                        for item in mapped_skus
                    ]
                    mapping = {
                        "title": str(mapped.get("title") or title),
                        "sku_count": len(mapped_skus),
                        "head_image_count": len(head_images),
                        "detail_image_count": len(detail_images),
                        "attribute_count": len(mapped.get("attrs") or []),
                        "category_mode": category_mode,
                        "category_path": str(mapped.get("category_path") or ""),
                        "price_min": min(prices) if prices else None,
                        "price_max": max(prices) if prices else None,
                        "stock_total": sum(stocks),
                        "adapter_sources": (
                            checked_payload.get("_adapter_sources")
                            or []
                        ),
                        "adapter_conflicts": (
                            checked_payload.get("_adapter_conflicts")
                            or []
                        ),
                    }
            if title:
                violation_issue = mandatory_upload_violation_issue(
                    title,
                    template,
                    self._upload_violation_title_rows(),
                )
                if violation_issue:
                    issues.append(violation_issue)
            if not issues:
                ready += 1
            results.append(
                {
                    "batch_id": str(row.get("batch_id") or ""),
                    "title": title,
                    "platform": platform,
                    "source_product_id": source_id,
                    "price": price,
                    "stock": stock,
                    "ready": not issues,
                    "issues": issues,
                    "warnings": warnings,
                    "mapping": mapping,
                }
            )
        return {
            "checked": len(results),
            "ready": ready,
            "blocked": len(results) - ready,
            "rows": results,
        }

    def _export_upload_batch(self, payload: dict[str, Any]) -> dict[str, Any]:
        selected = {
            str(value)
            for value in payload.get("batch_ids", [])
            if str(value).strip()
        }
        rows = [
            row
            for row in self._upload_batch_rows()
            if not selected or str(row.get("batch_id") or "") in selected
        ]
        safe_rows = _sanitize_upload_export(rows)
        return {
            "count": len(safe_rows),
            "filename": f"langlang-upload-batch-{datetime.now():%Y%m%d-%H%M%S}.json",
            "content": json.dumps(safe_rows, ensure_ascii=False, indent=2),
        }

    def _clear_upload_batch(self, _payload: dict[str, Any]) -> dict[str, Any]:
        self._save_upload_batch([])
        return {"cleared": True, "batch": []}

    def _preview_upload_links(self, payload: dict[str, Any]) -> dict[str, Any]:
        from ..source_1688 import parse_1688_links

        links = parse_1688_links(
            str(payload.get("text") or ""),
            max_links=50,
        )
        accounts = self._upload_procurement_accounts()
        return {
            "links": [
                {"offer_id": link.offer_id, "url": link.url}
                for link in links
            ],
            "accounts": accounts,
        }

    def _start_upload_link_collection(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        from ..source_1688 import (
            Link1688Collector,
            Source1688LoginRequired,
            normalized_1688_row,
            parse_1688_links,
        )

        account_id = int(payload.get("account_id") or 0)
        account = self.db.get_procurement_account(account_id)
        if account is None or str(account.get("platform") or "") != "1688":
            raise ValueError("请选择已登录的 1688 采购账号")
        links = parse_1688_links(
            str(payload.get("text") or ""),
            max_links=50,
        )
        if not links:
            raise ValueError("没有有效的 1688 商品链接")
        if self._active_upload_job("links"):
            raise ValueError("已有 1688 链接采集任务正在运行")
        try:
            skip_detail_images = max(
                0,
                min(20, int(payload.get("skip_detail_images") or 0)),
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("详情图跳过数量必须是 0 到 20 的整数") from exc
        job_id = uuid.uuid4().hex
        with self._upload_jobs_lock:
            self._upload_jobs[job_id] = {
                "job_id": job_id,
                "kind": "links",
                "status": "starting",
                "message": "正在准备 1688 链接采集…",
                "current": 0,
                "total": len(links),
                "errors": [],
                "created_at": datetime.now().isoformat(timespec="seconds"),
            }

        def update(**changes: Any) -> None:
            with self._upload_jobs_lock:
                job = self._upload_jobs.get(job_id)
                if job is not None:
                    job.update(changes)

        def worker() -> None:
            try:
                update(status="running", message="正在采集 1688 商品…")
                collector = Link1688Collector(self.db, self.db.path.parent)

                def progress(message: str, current: int, total: int) -> None:
                    update(message=message, current=current, total=total)

                results = collector.collect(
                    account,
                    links,
                    skip_detail_images=skip_detail_images,
                    progress=progress,
                )
                rows = [
                    normalized_1688_row(result["data"])
                    for result in results
                    if result.get("ok") and isinstance(result.get("data"), dict)
                ]
                merged = self._merge_upload_rows(rows, append=True)
                sku_count = sum(
                    len(
                        (
                            row.get("payload", {}).get("skus")
                            or row.get("payload", {}).get("skuList", {}).get("skuInfo")
                            or []
                        )
                    )
                    for row in rows
                    if isinstance(row.get("payload"), dict)
                )
                image_count = sum(
                    len(
                        (
                            row.get("payload", {}).get("head_imgs")
                            or row.get("payload", {}).get("headImgs")
                            or []
                        )
                    )
                    + len(
                        (
                            row.get("payload", {}).get("detail_imgs")
                            or row.get("payload", {}).get("detailImgs")
                            or []
                        )
                    )
                    for row in rows
                    if isinstance(row.get("payload"), dict)
                )
                prices = [
                    int(row.get("price") or 0)
                    for row in rows
                    if int(row.get("price") or 0) > 0
                ]
                stocks = [
                    int(row.get("stock") or 0)
                    for row in rows
                ]
                failures = [
                    {
                        "offer_id": str(result.get("offer_id") or ""),
                        "error": str(result.get("error") or "")[:500],
                    }
                    for result in results
                    if not result.get("ok")
                ]
                update(
                    status="partial" if failures else "completed",
                    message=(
                        f"1688 采集完成，新增 {merged['added']} 个商品，"
                        f"失败 {len(failures)} 个"
                    ),
                    current=len(links),
                    total=len(links),
                    added=merged["added"],
                    skipped=merged["skipped"],
                    product_count=len(rows),
                    sku_count=sku_count,
                    image_count=image_count,
                    price_min=min(prices) if prices else None,
                    price_max=max(prices) if prices else None,
                    stock_min=min(stocks) if stocks else None,
                    stock_max=max(stocks) if stocks else None,
                    failures=failures,
                    errors=[item["error"] for item in failures],
                )
            except Source1688LoginRequired as exc:
                update(
                    status="failed",
                    message=str(exc),
                    login_required=True,
                    errors=[str(exc)],
                )
            except Exception as exc:
                update(
                    status="failed",
                    message=f"{type(exc).__name__}: {exc}",
                    errors=[str(exc)],
                )

        threading.Thread(
            target=worker,
            name=f"webui-upload-links-{job_id[:8]}",
            daemon=True,
        ).start()
        return {"job_id": job_id}

    def _upload_template_defaults(self, _payload: dict[str, Any]) -> dict[str, Any]:
        default_id = self._upload_default_template_id()
        default_template = next(
            (
                row
                for row in self._upload_templates()
                if str(row.get("id") or "") == default_id
            ),
            None,
        )
        return {
            "template": UPLOAD_DEFAULT_TEMPLATE,
            "default_template_id": default_id,
            "default_template": default_template,
            "cloud_violation_types": self._upload_cloud_violation_types(),
        }

    def _upload_store_options(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Read real per-store defaults without inventing missing values."""
        requested = {
            int(value)
            for value in payload.get("store_ids", [])
            if str(value).isdigit()
        }
        service = self._require_product_service()
        result: dict[str, Any] = {}
        for store in self.db.list_stores():
            if requested and store.id not in requested:
                continue
            errors: list[str] = []
            freight_templates: list[dict[str, str]] = []
            after_sale_addresses: list[dict[str, str]] = []
            default_freight_template_id = ""
            default_after_sale_address_id = ""
            brands: list[dict[str, str]] = []
            try:
                client = service.client_for(store)
            except Exception as exc:
                client = None
                errors.append(f"店铺接口不可用：{exc}")
            if client is not None:
                try:
                    defaults = service._product_payload_defaults(
                        store,
                        client,
                    )
                except Exception as exc:
                    defaults = {}
                    errors.append(f"读取商品默认配置失败：{exc}")
                default_freight_template_id = str(
                    (defaults.get("express_info") or {}).get(
                        "template_id"
                    )
                    or ""
                )
                default_after_sale_address_id = str(
                    (defaults.get("after_sale_info") or {}).get(
                        "after_sale_address_id"
                    )
                    or ""
                )
                fetch_ids = getattr(
                    client,
                    "fetch_freight_template_ids",
                    None,
                )
                fetch_detail = getattr(
                    client,
                    "fetch_freight_template_detail",
                    None,
                )
                if callable(fetch_ids):
                    try:
                        template_ids = list(fetch_ids())
                    except Exception as exc:
                        template_ids = []
                        errors.append(f"读取运费模板失败：{exc}")
                    for template_id in template_ids[:100]:
                        label = str(template_id)
                        if callable(fetch_detail):
                            try:
                                detail = fetch_detail(template_id)
                            except Exception:
                                detail = {}
                            label = str(
                                (detail or {}).get("name")
                                or (detail or {}).get("template_name")
                                or label
                            )
                        if (
                            label.strip() in {
                                str(template_id),
                                "",
                                "店铺当前默认运费模板",
                            }
                        ):
                            label = (
                                UPLOAD_KNOWN_FREIGHT_TEMPLATE_LABELS.get(
                                    str(template_id),
                                    label.strip() or "未读取到运费模板名称",
                                )
                            )
                        freight_templates.append(
                            {
                                "id": str(template_id),
                                "label": label,
                            }
                        )
                if default_freight_template_id and not any(
                    item["id"] == default_freight_template_id
                    for item in freight_templates
                ):
                    errors.append(
                        "缓存中的默认运费模板不属于当前店铺，"
                        "已忽略并请重新选择官方模板"
                    )
                    default_freight_template_id = ""
            if default_after_sale_address_id:
                after_sale_addresses.append(
                    {
                        "id": default_after_sale_address_id,
                        "label": "店铺当前默认售后地址",
                    }
                )
            brands.append(
                {
                    "id": "2100000000",
                    "label": "无品牌",
                }
            )
            if client is not None:
                valid_brands: list[dict[str, Any]] = []
                fetch_brands = getattr(
                    service,
                    "valid_store_brands",
                    None,
                )
                if callable(fetch_brands):
                    try:
                        valid_brands = fetch_brands(store, client)
                    except Exception as exc:
                        valid_brands = []
                        errors.append(f"读取店铺生效品牌失败：{exc}")
                else:
                    fallback_fetch = getattr(
                        client,
                        "fetch_valid_brands",
                        None,
                    )
                    if callable(fallback_fetch):
                        try:
                            valid_brands = fallback_fetch()
                        except Exception as exc:
                            errors.append(
                                f"读取店铺生效品牌失败：{exc}"
                            )
                for brand in valid_brands or []:
                    if not isinstance(brand, dict):
                        continue
                    brand_id = str(
                        brand.get("brand_id") or ""
                    ).strip()
                    if not brand_id or any(
                        existing["id"] == brand_id
                        for existing in brands
                    ):
                        continue
                    label = str(
                        brand.get("ch_name")
                        or brand.get("en_name")
                        or "未读取到品牌名称"
                    ).strip()
                    if brand.get("expiring"):
                        label = f"{label}（即将过期）"
                    brands.append(
                        {
                            "id": brand_id,
                            "label": label,
                        }
                    )
            if not brands:
                brands.append(
                    {
                        "id": "2100000000",
                        "label": "无品牌",
                    }
                )
            result[str(store.id)] = {
                "store_id": store.id,
                "store_name": store.name,
                "web_status": store.web_status,
                "freight_templates": freight_templates,
                "after_sale_addresses": after_sale_addresses,
                "brands": brands,
                "brand_mode": "none",
                "has_store_brands": len(brands) > 1,
                "brand_source": "微信小店生效品牌资质",
                "default_freight_template_id": (
                    default_freight_template_id
                ),
                "default_after_sale_address_id": (
                    default_after_sale_address_id
                ),
                "default_brand_id": "2100000000",
                "error": "；".join(errors)[:1000],
            }
        return {"stores": result}

    def _upload_categories(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Read each selected store's official, permission-filtered category tree."""
        requested = [
            int(value)
            for value in payload.get("store_ids", [])
            if str(value).isdigit()
        ]
        if not requested:
            return {"stores": {}}
        service = self._require_product_service()
        fetch_categories = getattr(
            service,
            "upload_category_options",
            None,
        )
        if not callable(fetch_categories):
            raise RuntimeError("当前商品服务不支持官方类目读取")
        refresh = bool(payload.get("refresh"))
        stores = {
            store.id: store
            for store in self.db.list_stores()
            if store.id in set(requested)
        }
        result: dict[str, Any] = {}
        for store_id in dict.fromkeys(requested):
            store = stores.get(store_id)
            if store is None:
                result[str(store_id)] = {
                    "store_id": store_id,
                    "roots": [],
                    "version": "",
                    "cache_key": "",
                    "leaf_count": 0,
                    "error": "店铺不存在",
                }
                continue
            try:
                options = fetch_categories(store, refresh=refresh)
            except Exception as exc:
                result[str(store_id)] = {
                    "store_id": store.id,
                    "store_name": store.name,
                    "roots": [],
                    "version": "",
                    "cache_key": "",
                    "leaf_count": 0,
                    "error": str(exc)[:1000],
                }
                continue
            result[str(store.id)] = {
                "store_id": store.id,
                "store_name": store.name,
                **(
                    dict(options)
                    if isinstance(options, dict)
                    else {}
                ),
            }
        return {"stores": result}

    def _save_upload_template(self, payload: dict[str, Any]) -> dict[str, Any]:
        incoming = payload.get("template")
        if not isinstance(incoming, dict):
            raise ValueError("template 必须是对象")
        templates = self._upload_templates()
        template_id = str(incoming.get("id") or "").strip()
        if not template_id:
            template_id = f"tpl_{uuid.uuid4().hex[:10]}"
        existing = next(
            (
                row
                for row in templates
                if str(row.get("id") or "") == template_id
            ),
            {},
        )
        template = self._normalize_upload_template(incoming, existing)
        if template.get("specified_category_id"):
            store = self.db.get_store(
                int(template["specified_category_store_id"])
            )
            if store is None:
                raise ValueError(
                    "指定类目的上下文店铺不存在，请重新选择类目"
                )
            service = self._require_product_service()
            validator = getattr(
                service,
                "validate_category_selection",
                None,
            )
            if not callable(validator):
                raise RuntimeError("当前商品服务不支持指定类目校验")
            validated = validator(
                store,
                template["specified_category_ids"],
                template["specified_category_id"],
            )
            template["specified_category_path"] = validated["path"]
            template["specified_category_version"] = validated["version"]
        template["id"] = template_id
        template.setdefault(
            "created_at",
            existing.get("created_at")
            or datetime.now().isoformat(timespec="seconds"),
        )
        template["updated_at"] = datetime.now().isoformat(timespec="seconds")
        template["is_default"] = (
            template_id == self._upload_default_template_id()
        )
        templates = [
            row
            for row in templates
            if str(row.get("id") or "") != template_id
        ]
        templates.append(template)
        self._save_upload_templates(templates)
        return {"template": template, "templates": templates}

    def _copy_upload_template(self, payload: dict[str, Any]) -> dict[str, Any]:
        template_id = str(payload.get("template_id") or "").strip()
        if not template_id:
            raise ValueError("template_id 不能为空")
        templates = self._upload_templates()
        source = next(
            (
                row
                for row in templates
                if str(row.get("id") or "") == template_id
            ),
            None,
        )
        if source is None:
            raise ValueError("上货模板不存在")
        now = datetime.now().isoformat(timespec="seconds")
        copy = json.loads(json.dumps(source))
        copy["id"] = f"tpl_{uuid.uuid4().hex[:10]}"
        copy["name"] = f"{source.get('name') or '上传模板'} 副本"
        copy["created_at"] = now
        copy["updated_at"] = now
        copy["is_default"] = False
        templates.append(copy)
        self._save_upload_templates(templates)
        return {"template": copy, "templates": templates}

    def _set_default_upload_template(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        template_id = str(payload.get("template_id") or "").strip()
        if not template_id:
            raise ValueError("template_id 不能为空")
        templates = self._upload_templates()
        if not any(
            str(row.get("id") or "") == template_id
            for row in templates
        ):
            raise ValueError("上货模板不存在")
        self.db.set_setting("upload_default_template_id", template_id)
        for row in templates:
            row["is_default"] = str(row.get("id") or "") == template_id
        self._save_upload_templates(templates)
        return {
            "default_template_id": template_id,
            "templates": templates,
        }

    def _delete_upload_template(self, payload: dict[str, Any]) -> dict[str, Any]:
        template_id = str(payload.get("template_id") or "").strip()
        if not template_id:
            raise ValueError("template_id 不能为空")
        templates = [
            row
            for row in self._upload_templates()
            if str(row.get("id") or "") != template_id
        ]
        self._save_upload_templates(templates)
        default_id = self._upload_default_template_id()
        if default_id == template_id:
            default_id = ""
            self.db.set_setting("upload_default_template_id", "")
        return {
            "deleted": template_id,
            "default_template_id": default_id,
            "templates": templates,
        }

    def _select_upload_template_folder(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        if os.name != "nt":
            raise RuntimeError("当前系统不支持 Windows 原生文件夹选择")
        initial = str(payload.get("initial_path") or "").strip()
        script = (
            "Add-Type -AssemblyName System.Windows.Forms;"
            "$d=New-Object System.Windows.Forms.FolderBrowserDialog;"
            "$d.Description='选择商品视频文件夹';"
            "$d.ShowNewFolderButton=$true;"
            "$p=$env:LANG_UPLOAD_FOLDER_INITIAL;"
            "if($p -and (Test-Path -LiteralPath $p)){"
            "$d.SelectedPath=$p};"
            "$r=$d.ShowDialog();"
            "if($r -eq [System.Windows.Forms.DialogResult]::OK){"
            "[Console]::Out.Write($d.SelectedPath)}"
        )
        environment = os.environ.copy()
        environment["LANG_UPLOAD_FOLDER_INITIAL"] = initial
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            result = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-STA",
                    "-Command",
                    script,
                ],
                capture_output=True,
                text=True,
                timeout=900,
                env=environment,
                creationflags=flags,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise RuntimeError(f"无法打开 Windows 文件夹选择框：{exc}") from exc
        if result.returncode != 0:
            error = (result.stderr or result.stdout or "未知错误").strip()
            raise RuntimeError(f"文件夹选择失败：{error[:500]}")
        return {"path": (result.stdout or "").strip()}

    def _create_upload_tasks(self, payload: dict[str, Any]) -> dict[str, Any]:
        from ..ui.upload_page import (
            _apply_upload_image_rules,
            _normalize_source_payload,
        )

        batch_ids = {
            str(value)
            for value in payload.get("batch_ids", [])
            if str(value).strip()
        }
        store_ids = {
            int(value)
            for value in payload.get("store_ids", [])
            if str(value).isdigit()
        }
        assignments = {
            str(batch_id): int(store_id)
            for batch_id, store_id in (
                payload.get("assignments") or {}
            ).items()
            if str(store_id).isdigit()
        }
        store_ids.update(assignments.values())
        template_id = str(
            payload.get("template_id")
            or self._upload_default_template_id()
        ).strip()
        if not batch_ids:
            raise ValueError("请选择要创建任务的商品")
        if not store_ids:
            raise ValueError("请选择至少一个真实店铺")
        if not template_id:
            raise ValueError("请选择完整的上货模板")
        rows = [
            row
            for row in self._upload_batch_rows()
            if str(row.get("batch_id") or "") in batch_ids
        ]
        if not rows:
            raise ValueError("所选商品不在当前导入批次中")
        if assignments and set(assignments).intersection(batch_ids) != batch_ids:
            raise ValueError("部分商品未分配目标店铺")
        stores = {
            store.id: store
            for store in self.db.list_stores()
            if store.id in store_ids
        }
        if len(stores) != len(store_ids):
            raise ValueError("部分目标店铺不存在，请刷新后重试")
        template = next(
            (
                row
                for row in self._upload_templates()
                if str(row.get("id") or "") == template_id
            ),
            None,
        )
        if template is None:
            raise ValueError("上货模板不存在")
        template = self._normalize_upload_template(
            template,
            template,
        )
        violation_titles = self._upload_violation_title_rows()
        for row in rows:
            violation_issue = mandatory_upload_violation_issue(
                str(row.get("title") or ""),
                template,
                violation_titles,
            )
            if violation_issue:
                raise ValueError(
                    f"商品标题已命中售假违规库，禁止创建上传任务："
                    f"{str(row.get('title') or row.get('source_product_id') or '')[:120]}"
                    f"；{violation_issue}"
                )
        store_options = payload.get("store_options")
        if not isinstance(store_options, dict):
            store_options = {}
        rows_by_store: dict[int, list[dict[str, Any]]] = {}
        if assignments:
            for row in rows:
                store_id = assignments.get(
                    str(row.get("batch_id") or "")
                )
                if store_id is None:
                    continue
                rows_by_store.setdefault(store_id, []).append(row)
        else:
            rows_by_store = {
                store_id: list(rows)
                for store_id in stores
            }
        tasks = self._upload_tasks()
        created_ids: list[str] = []
        blocked_stores: list[dict[str, Any]] = []
        now = datetime.now().isoformat(timespec="seconds")
        for store_id, store_rows in rows_by_store.items():
            store = stores.get(store_id)
            if store is None or not store_rows:
                continue
            task_id = f"up_{uuid.uuid4().hex[:12]}"
            task_template = json.loads(json.dumps(template))
            options = store_options.get(str(store.id))
            if isinstance(options, dict):
                for key in (
                    "freight_template_id",
                    "brand_id",
                    "after_sale_address_id",
                    "brand_mode",
                    "specified_category_id",
                    "specified_category_path",
                    "specified_category_version",
                    "specified_category_store_id",
                ):
                    value = str(options.get(key) or "").strip()
                    if value:
                        task_template[key] = value
                legacy_category_ids = []
                raw_category_ids = options.get("specified_category_ids")
                if isinstance(raw_category_ids, list):
                    legacy_category_ids = list(
                        dict.fromkeys(
                            str(value).strip()
                            for value in raw_category_ids
                            if str(value).strip().isdigit()
                        )
                    )
                if legacy_category_ids:
                    task_template["category_mode"] = "specified"
                    task_template["specified_category_ids"] = legacy_category_ids
                    task_template["specified_category_id"] = str(
                        task_template.get("specified_category_id")
                        or legacy_category_ids[-1]
                    )

                configured_brand_mode = (
                    str(options.get("brand_mode") or "").strip()
                    if isinstance(options, dict)
                    else ""
                )
                brand_mode = configured_brand_mode
                if not brand_mode:
                    # Preserve legacy task-creation callers that provided only
                    # brand_id before the explicit 无品牌/店铺品牌 switch existed.
                    brand_mode = "legacy"
                task_template["brand_mode"] = brand_mode
                if brand_mode not in {"none", "store"}:
                    if brand_mode != "legacy":
                        raise ValueError(
                            f"{store.name} 品牌设置无效，请选择无品牌或店铺品牌"
                        )
                if brand_mode == "none":
                    task_template["brand_id"] = "2100000000"
                elif brand_mode == "store":
                    service = self._require_product_service()
                    brand_id = str(
                        task_template.get("brand_id") or ""
                    ).strip()
                    try:
                        client = service.client_for(store)
                        brand_reader = getattr(
                            service,
                            "valid_store_brands",
                            None,
                        )
                        valid_brands = (
                            brand_reader(store, client)
                            if callable(brand_reader)
                            else client.fetch_valid_brands()
                        )
                    except Exception as exc:
                        raise ValueError(
                            f"{store.name} 无法校验店铺品牌：{exc}"
                        ) from exc
                    valid_ids = {
                        str(item.get("brand_id") or "").strip()
                        for item in valid_brands or []
                        if isinstance(item, dict)
                    }
                    if not brand_id or brand_id not in valid_ids:
                        raise ValueError(
                            f"{store.name} 选择的店铺品牌不存在、未生效或已过期"
                        )
            if task_template.get("specified_category_id"):
                service = self._require_product_service()
                validator = getattr(
                    service,
                    "validate_category_selection",
                    None,
                )
                if not callable(validator):
                    blocked_stores.append(
                        {
                            "store_id": store.id,
                            "store_name": store.name,
                            "error": (
                                f"{store.name} 当前商品服务不支持指定类目校验，"
                                "请修改模板"
                            ),
                        }
                    )
                    continue
                try:
                    validated_category = validator(
                        store,
                        list(task_template.get("specified_category_ids") or []),
                        str(task_template.get("specified_category_id") or ""),
                    )
                except Exception as exc:
                    blocked_stores.append(
                        {
                            "store_id": store.id,
                            "store_name": store.name,
                            "error": str(exc),
                        }
                    )
                    continue
                task_template["specified_category_ids"] = list(
                    validated_category["category_ids"]
                )
                task_template["specified_category_id"] = validated_category[
                    "category_id"
                ]
                task_template["specified_category_path"] = validated_category["path"]
                task_template["specified_category_version"] = validated_category[
                    "version"
                ]
                task_template["specified_category_validated_store_id"] = store.id
            else:
                task_template["category_mode"] = "auto"
            created_ids.append(task_id)
            items = []
            for row in store_rows:
                item = dict(row)
                item["item_id"] = uuid.uuid4().hex
                item["status"] = "pending"
                item["message"] = "等待上传"
                item["product_id"] = ""
                source_payload = row.get("payload")
                if not isinstance(source_payload, dict):
                    source_payload = row.get("raw")
                if isinstance(source_payload, dict):
                    normalized = _normalize_source_payload(
                        source_payload
                    )
                    if (
                        normalized.get("head_imgs")
                        or (normalized.get("desc_info") or {}).get("imgs")
                    ):
                        resolved = _apply_upload_image_rules(
                            normalized,
                            task_template,
                        )
                        item["image_snapshot"] = {
                            "head_imgs": list(
                                resolved.get("head_imgs") or []
                            ),
                            "detail_imgs": list(
                                (resolved.get("desc_info") or {}).get(
                                    "imgs"
                                )
                                or []
                            ),
                            "source_counts": dict(
                                resolved.get("_source_image_counts")
                                or {}
                            ),
                        }
                items.append(item)
            tasks.insert(
                0,
                {
                    "id": task_id,
                    "store_id": store.id,
                    "store_name": store.name,
                    "template": task_template,
                    "template_id": template_id,
                    "status": "pending",
                    "created_at": now,
                    "message": "等待开始",
                    "items": items,
                },
            )
        self._save_upload_tasks(tasks)
        return {
            "created": len(created_ids),
            "task_ids": created_ids,
            "tasks": tasks,
            "blocked_stores": blocked_stores,
        }

    def _retry_upload_tasks(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self._active_upload_job("run"):
            raise ValueError("上传任务正在运行，请先暂停并等待在途商品结束")
        task_ids = {
            str(value)
            for value in payload.get("task_ids", [])
            if str(value).strip()
        }
        item_ids = {
            str(value)
            for value in payload.get("item_ids", [])
            if str(value).strip()
        }
        tasks = self._upload_tasks()
        retried = 0
        for task in tasks:
            if task_ids and str(task.get("id") or "") not in task_ids:
                continue
            changed = False
            for item in task.get("items") or []:
                if item_ids and str(item.get("item_id") or "") not in item_ids:
                    continue
                if (
                    item.get("status") != "failed"
                    and not self._is_pre_submit_permission_rejection(item)
                ):
                    continue
                item["status"] = "pending"
                item["message"] = "等待重试"
                item["product_id"] = ""
                item.pop("submission_attempt_id", None)
                item.pop("submission_started_at", None)
                item.pop("finished_at", None)
                item["category_state"] = ""
                item["category_apply_submitted"] = False
                item["category_check_attempts"] = 0
                item["category_reprocess_available"] = False
                item.pop("category_next_check_at", None)
                item.pop("category_error", None)
                retried += 1
                changed = True
            if changed:
                task["status"] = "paused"
                task["message"] = "失败商品已放回队列"
        self._save_upload_tasks(tasks)
        return {"retried": retried, "tasks": tasks}

    @staticmethod
    def _is_pre_submit_permission_rejection(
        item: dict[str, Any],
    ) -> bool:
        if str(item.get("status") or "") != "verification_pending":
            return False
        if str(item.get("product_id") or "").strip():
            return False
        message = str(item.get("message") or "")
        return (
            "真实平台写操作缺少当前会话安全门，已拒绝执行" in message
            and any(
                f"已拒绝执行：{action}" in message
                for action in (
                    "product_write",
                    "product_add",
                    "product_image_upload",
                )
            )
        )

    def _clear_upload_tasks(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self._active_upload_job("run"):
            raise ValueError("上传任务正在运行，不能删除任务")
        scope = str(payload.get("scope") or "selected")
        task_ids = {
            str(value)
            for value in payload.get("task_ids", [])
            if str(value).strip()
        }
        tasks = self._upload_tasks()
        if scope == "all":
            remaining: list[dict[str, Any]] = []
        elif scope == "completed":
            remaining = [
                task
                for task in tasks
                if task.get("status") != "completed"
            ]
        else:
            if not task_ids:
                raise ValueError("请选择要删除的上传任务")
            remaining = [
                task
                for task in tasks
                if str(task.get("id") or "") not in task_ids
            ]
        self._save_upload_tasks(remaining)
        return {
            "deleted": len(tasks) - len(remaining),
            "tasks": remaining,
        }

    def _list_upload_logs(self, payload: dict[str, Any]) -> dict[str, Any]:
        task_id = str(payload.get("task_id") or "").strip()
        status = str(payload.get("status") or "").strip()
        keyword = str(payload.get("keyword") or "").strip().casefold()
        logs: list[dict[str, Any]] = []
        for task in self._upload_tasks():
            if task_id and str(task.get("id") or "") != task_id:
                continue
            for item in task.get("items") or []:
                item_status = str(item.get("status") or "pending")
                if status and item_status != status:
                    continue
                message = str(item.get("message") or "")
                values = (
                    str(task.get("store_name") or ""),
                    str(item.get("title") or ""),
                    str(item.get("source_product_id") or ""),
                    str(item.get("product_id") or ""),
                    message,
                )
                if keyword and not any(keyword in value.casefold() for value in values):
                    continue
                logs.append(
                    {
                        "id": f"{task.get('id')}:{item.get('item_id')}",
                        "task_id": str(task.get("id") or ""),
                        "store_id": int(task.get("store_id") or 0),
                        "store_name": str(task.get("store_name") or ""),
                        "item_id": str(item.get("item_id") or ""),
                        "title": str(item.get("title") or ""),
                        "source_product_id": str(item.get("source_product_id") or ""),
                        "product_id": str(item.get("product_id") or ""),
                        "status": item_status,
                        "message": message,
                        "category_state": str(
                            item.get("category_state") or ""
                        ),
                        "category_ids": list(
                            item.get("category_ids") or []
                        ),
                        "category_statuses": dict(
                            item.get("category_statuses") or {}
                        ),
                        "category_checks": dict(
                            item.get("category_checks") or {}
                        ),
                        "category_deposit_snapshot": dict(
                            item.get("category_deposit_snapshot") or {}
                        ),
                        "category_required_deposit": item.get(
                            "category_required_deposit"
                        ),
                        "category_amount_to_pay": item.get(
                            "category_amount_to_pay"
                        ),
                        "category_operation_required_deposit": item.get(
                            "category_operation_required_deposit"
                        ),
                        "category_deposit_balance": item.get(
                            "category_deposit_balance"
                        ),
                        "category_real_deposit_balance": item.get(
                            "category_real_deposit_balance"
                        ),
                        "category_deposit_shortfall": item.get(
                            "category_deposit_shortfall"
                        ),
                        "category_deposit_free_trial": item.get(
                            "category_deposit_free_trial"
                        ),
                        "category_business_type": str(
                            item.get("category_business_type") or ""
                        ),
                        "category_selection": dict(
                            item.get("category_selection") or {}
                        ),
                        "category_candidates": list(
                            item.get("category_candidates") or []
                        ),
                        "category_apply_submitted": bool(
                            item.get("category_apply_submitted")
                        ),
                        "category_application_at": str(
                            item.get("category_application_at") or ""
                        ),
                        "category_last_checked_at": str(
                            item.get("category_last_checked_at") or ""
                        ),
                        "category_check_attempts": int(
                            item.get("category_check_attempts") or 0
                        ),
                        "category_next_check_at": str(
                            item.get("category_next_check_at") or ""
                        ),
                        "category_error": str(
                            item.get("category_error") or ""
                        ),
                        "category_previous_failure_message": str(
                            item.get(
                                "category_previous_failure_message"
                            )
                            or ""
                        ),
                        "category_reprocess_available": bool(
                            item.get("category_reprocess_available")
                        ),
                        "attempt_id": str(item.get("submission_attempt_id") or ""),
                        "started_at": str(
                            item.get("submission_started_at")
                            or task.get("created_at")
                            or ""
                        ),
                        "finished_at": str(item.get("finished_at") or ""),
                    }
                )
        logs.sort(
            key=lambda row: row["finished_at"] or row["started_at"],
            reverse=True,
        )
        return {"logs": logs[:5000], "count": len(logs)}

    def _start_upload_job(self, payload: dict[str, Any]) -> dict[str, Any]:
        service = self._require_product_service()
        if self._active_upload_job("run"):
            raise ValueError("已有上传任务正在运行")
        selected_ids = {
            str(value)
            for value in payload.get("task_ids", [])
            if str(value).strip()
        }
        item_ids = {
            str(value)
            for value in payload.get("item_ids", [])
            if str(value).strip()
        }
        tasks = self._upload_tasks()
        selected = [
            task
            for task in tasks
            if selected_ids and str(task.get("id") or "") in selected_ids
        ]
        if not selected and not selected_ids:
            selected = [
                task
                for task in tasks
                if task.get("status")
                in {
                    "pending",
                    "queued",
                    "paused",
                    "partial",
                    "needs_verification",
                    "needs_category",
                }
            ]
        if not selected:
            raise ValueError("没有可执行的上传任务")
        requeued = 0
        for task in selected:
            for item in task.get("items") or []:
                if not self._is_pre_submit_permission_rejection(item):
                    continue
                item["status"] = "pending"
                item["message"] = "之前未进入平台，等待重新上传"
                item.pop("submission_attempt_id", None)
                item.pop("submission_started_at", None)
                item.pop("finished_at", None)
                requeued += 1
        if requeued:
            self._save_upload_tasks(tasks)
        blocked_statuses = {"success", "skipped", "verification_pending"}
        violation_titles = self._upload_violation_title_rows()
        runnable_items: list[dict[str, Any]] = []
        blocked_title_count = 0
        for task in selected:
            task_template = (
                task.get("template")
                if isinstance(task.get("template"), dict)
                else {}
            )
            for item in task.get("items") or []:
                if (
                    str(item.get("status") or "") in blocked_statuses
                    or (
                        item_ids
                        and str(item.get("item_id") or "") not in item_ids
                    )
                ):
                    continue
                violation_issue = mandatory_upload_violation_issue(
                    str(item.get("title") or ""),
                    task_template,
                    violation_titles,
                )
                if violation_issue:
                    item["status"] = "failed"
                    item["message"] = violation_issue
                    item["finished_at"] = datetime.now().isoformat(
                        timespec="seconds"
                    )
                    blocked_title_count += 1
                    continue
                runnable_items.append(item)
        if blocked_title_count:
            self._save_upload_tasks(tasks)
        if not runnable_items:
            raise ValueError(
                (
                    f"{blocked_title_count} 个商品命中售假违规库，已禁止提交"
                    if blocked_title_count
                    else "没有可执行商品；待核实商品必须先人工确认，不能自动重复提交"
                )
            )
        job_id = uuid.uuid4().hex
        generation = self._current_generation()
        owner = self._job_owner_fields()
        with self._upload_jobs_lock:
            self._upload_jobs[job_id] = {
                "job_id": job_id,
                **owner,
                "kind": "run",
                "status": "starting",
                "message": "正在准备上传任务…",
                "completed": 0,
                "total": len(runnable_items),
                "errors": [],
                "tasks": [],
                "created_at": datetime.now().isoformat(timespec="seconds"),
            }

        def update(**changes: Any) -> None:
            with self._upload_jobs_lock:
                if not self._job_update_allowed(generation):
                    return
                job = self._upload_jobs.get(job_id)
                if job is not None:
                    for key, value in changes.items():
                        if key in {"completed", "current"}:
                            job[key] = max(
                                int(job.get(key) or 0),
                                int(value or 0),
                            )
                        else:
                            job[key] = value

        def worker() -> None:
            try:
                from types import SimpleNamespace
                from ..pyside.pages.upload import UploadRunner

                def progress(message: str, completed: int, total: int) -> None:
                    update(
                        status="running",
                        message=message,
                        completed=completed,
                        total=total,
                    )

                runner = UploadRunner(
                    SimpleNamespace(
                        db=self.db,
                        service=_ScopedUploadProductService(self, service),
                    ),
                    tasks,
                    progress=progress,
                )
                with self._upload_jobs_lock:
                    job = self._upload_jobs.get(job_id)
                    if job is not None:
                        job["runner"] = runner
                        if job.get("cancel_requested"):
                            runner.cancelled.set()
                        elif job.get("pause_requested"):
                            runner.paused.set()
                if not self._job_update_allowed(generation):
                    runner.paused.set()
                    return
                update(status="running", message="正在执行上传任务…")
                errors = runner.run(
                    selected,
                    selected_item_ids=item_ids or None,
                )
                if not self._job_update_allowed(generation):
                    return
                refreshed = self._upload_tasks()
                waiting_category = any(
                    item.get("status") == "waiting_category"
                    for task in refreshed
                    if str(task.get("id") or "") in selected_ids
                    for item in task.get("items") or []
                )
                if runner.cancelled.is_set():
                    status = "cancelled"
                    message = "上传已取消，未提交商品保持未提交状态"
                elif runner.paused.is_set():
                    status = "paused"
                    message = "已暂停，当前商品处理完成；未处理商品等待继续"
                elif waiting_category:
                    status = "partial"
                    message = "部分商品等待类目生效，已安排后续自动恢复上传"
                elif errors:
                    status = "partial"
                    message = f"上传任务结束，问题 {len(errors)} 条"
                else:
                    status = "completed"
                    message = "上传任务完成"
                update(
                    status=status,
                    message=message,
                    errors=errors,
                    tasks=refreshed,
                    runner=None,
                )
                if waiting_category and self._job_update_allowed(generation):
                    self._schedule_upload_category_resume(refreshed)
            except Exception as exc:
                update(
                    status="failed",
                    message=f"{type(exc).__name__}: {exc}",
                    errors=[str(exc)],
                    runner=None,
                )

        threading.Thread(
            target=worker,
            name=f"webui-upload-{job_id[:8]}",
            daemon=True,
        ).start()
        return {"job_id": job_id}

    def _request_upload_job_control(
        self,
        payload: dict[str, Any],
        *,
        cancel: bool,
    ) -> dict[str, Any]:
        job_id = str(payload.get("job_id") or "")
        with self._upload_jobs_lock:
            job = self._upload_jobs.get(job_id)
            if job is None or job.get("kind") != "run":
                raise ValueError("上传任务不存在")
            if job.get("status") in UPLOAD_JOB_TERMINAL_STATUSES:
                raise ValueError("上传任务已经结束")
            runner = job.get("runner")
            if runner is None:
                if cancel:
                    job["cancel_requested"] = True
                    job["status"] = "cancelling"
                    job["message"] = "已请求取消，任务启动后将立即停止"
                else:
                    job["pause_requested"] = True
                    job["status"] = "pausing"
                    job["message"] = "已请求暂停，任务启动后将立即暂停"
                return self._public_upload_job(job)
            if cancel:
                runner.cancelled.set()
                job["status"] = "cancelling"
                job["message"] = "正在取消；当前在途商品处理结束前不会宣称已停止"
            else:
                runner.paused.set()
                job["status"] = "pausing"
                job["message"] = "正在暂停；当前商品处理结束后停止"
            return self._public_upload_job(job)

    def _pause_upload_job(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request_upload_job_control(payload, cancel=False)

    def _cancel_upload_job(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request_upload_job_control(payload, cancel=True)

    def _upload_job_status(self, payload: dict[str, Any]) -> dict[str, Any]:
        job_id = str(payload.get("job_id") or "")
        with self._upload_jobs_lock:
            job = self._upload_jobs.get(job_id)
            if job is None:
                raise ValueError("上传任务不存在")
            return self._public_upload_job(job)
