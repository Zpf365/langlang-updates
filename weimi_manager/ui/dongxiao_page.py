from __future__ import annotations

import json
import tkinter as tk
import uuid
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Any

from ..dongxiao import DONGXIAO_SECTIONS
from ..dongxiao.application.aftersale_service import DongxiaoAftersaleService
from ..dongxiao.application.catalog_service import ProductCatalogService
from ..dongxiao.application.import_service import ProductImportService
from ..dongxiao.application.order_service import DongxiaoOrderService
from ..dongxiao.application.pool_service import ProductPoolService
from ..dongxiao.application.preflight_service import (
    PLATFORM_READ_AUTHORIZATION,
    DongxiaoPreflightService,
)
from ..dongxiao.application.task_service import DongxiaoTaskService
from ..dongxiao.buyer_flow.assistant_client import BuyerAssistantClient
from ..dongxiao.buyer_flow.domain import BuyerFlowMode
from ..dongxiao.buyer_flow.repository import BuyerFlowRepository
from ..dongxiao.buyer_flow.service import DongxiaoBuyerFlowService
from ..dongxiao.buyer_flow.session_vault import BuyerSessionVault
from ..dongxiao.desktop_cart.domain import (
    CartAdapterMode,
    CartBatchStatus,
    CartResultStatus,
    CartSkuMode,
    CartSpeedMode,
    OutOfStockPolicy,
    PendingOrderStatus,
)
from ..dongxiao.desktop_cart.repository import DesktopCartRepository
from ..dongxiao.desktop_cart.service import DesktopCartService
from ..dongxiao.domain.catalog import (
    CatalogFreshness,
    classify_catalog_freshness,
)
from ..dongxiao.domain.enums import (
    AftersaleStatus,
    ApprovalStatus,
    DataSource,
    OrderStatus,
    ProductImportStatus,
    ProductState,
    TaskStatus,
)
from ..dongxiao.domain.operations import PreflightState
from ..dongxiao.domain.queries import ProductFilter, ProductPage
from ..dongxiao.domain.rules import (
    SkuSelection,
    SkuSelectionMode,
    parse_yuan_to_cents,
    select_sku,
)
from ..dongxiao.infrastructure.catalog import (
    CatalogAdapterMode,
    build_catalog_adapter,
)
from ..dongxiao.infrastructure.platform import (
    OfficialDongxiaoPlatformAdapter,
)
from ..dongxiao.infrastructure.repository import DongxiaoRepository
from .pages import BasePage
from .theme import BLUE, BORDER, MUTED, PANEL, SOFT_TEAL, TEXT, button

SOURCE_LABELS = {
    DataSource.OFFICIAL_API: "官方 API",
    DataSource.WEB_BACKEND: "网页后台（未确认官方授权）",
    DataSource.LOCAL_CACHE: "本地缓存（非实时）",
    DataSource.MOCK: "模拟数据",
    DataSource.UNKNOWN: "来源未知",
}
FRESHNESS_LABELS = {
    CatalogFreshness.LIVE_OFFICIAL: "官方实时",
    CatalogFreshness.LOCAL_CACHE: "本地缓存（非实时）",
    CatalogFreshness.STALE_CACHE: "缓存可能失效",
    CatalogFreshness.MOCK: "模拟数据",
    CatalogFreshness.UNKNOWN: "来源/时间未知",
}
STATE_LABELS = {
    ProductState.ACTIVE: "上架",
    ProductState.INACTIVE: "下架",
    ProductState.UNKNOWN: "未知",
}
AUDIT_ACTION_LABELS = {
    "task_created": "创建任务",
    "task_status_changed": "任务状态变更",
    "aftersale_status_changed": "售后状态变更",
    "aftersale_amount_changed": "售后金额变化",
    "aftersale_platform_status_unknown": "售后平台状态未知",
    "approval_decided": "审批决定",
    "order_synced": "同步真实订单",
    "aftersale_synced": "同步真实售后",
    "platform_write_startup_reset": "启动时关闭平台写操作",
    "platform_write_guard_changed": "平台写操作开关变更",
    "store_platform_write_startup_reset": "启动时关闭店铺写操作",
    "store_platform_write_changed": "店铺真实写操作变更",
    "buyer_account_created": "添加买家账号",
    "buyer_mock_account_created": "添加 Mock 买家账号",
    "buyer_current_account_changed": "切换当前买家账号",
    "buyer_account_checked": "检查买家账号",
    "buyer_account_deleted": "删除买家账号",
    "buyer_flow_prepared": "准备买家流程",
    "buyer_manual_handoff_started": "开始官方客户端交接",
    "buyer_handoff_user_confirmed": "用户确认完成官方操作",
    "buyer_order_verified": "核验官方待付款订单",
    "buyer_mock_flow_executed": "执行 Mock 买家流程",
}
AUDIT_ENTITY_LABELS = {
    "task": "任务",
    "order": "订单",
    "aftersale": "售后",
    "approval": "审批",
    "store": "店铺",
    "buyer_account": "买家账号",
    "buyer_flow_run": "买家流程",
    "buyer_order": "买家订单",
}


def _money(cents: int | None) -> str:
    if cents is None:
        return "—"
    value = int(cents)
    return f"¥{value // 100}.{value % 100:02d}"


def _date_time(value: datetime | None) -> str:
    return value.strftime("%Y-%m-%d %H:%M") if value else "—"


def _store_name_for_id(stores, store_id: int | None) -> str:
    if store_id is None:
        return "店铺已删除（历史记录）"
    return next(
        (
            str(getattr(store, "name", store_id))
            for store in stores
            if int(getattr(store, "id", 0)) == int(store_id)
        ),
        f"店铺 #{store_id}",
    )


def _day_start(value: date) -> datetime:
    return datetime.combine(value, time.min, tzinfo=UTC)


def _day_end(value: date) -> datetime:
    return datetime.combine(value, time.max, tzinfo=UTC)


def _audit_detail_text(value: str) -> str:
    try:
        payload = json.loads(value or "{}")
    except (TypeError, ValueError):
        return str(value)
    if not isinstance(payload, dict):
        return str(payload)
    labels = {
        "from": "原状态",
        "to": "新状态",
        "event": "事件",
        "reason": "原因",
        "status": "状态",
        "platform_status": "平台状态",
        "item_count": "商品项",
        "total_amount_cents": "实付金额（分）",
        "requested_amount_cents": "申请金额（分）",
        "idempotency_key": "幂等键",
    }
    return "；".join(
        f"{labels.get(str(key), str(key))}：{item}"
        for key, item in payload.items()
    )[:500]


class DongxiaoPage(BasePage):
    title = "动销中心"
    subtitle = "商品、任务、订单、售后与审计一体化工作台"

    def __init__(self, parent: tk.Widget, window) -> None:
        super().__init__(parent, window)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        self.section_buttons: dict[str, tk.Button] = {}
        self._active_section = "overview"
        self._stores: list[Any] = []
        self._products: list[Any] = []
        self._pools: list[Any] = []
        self._pool_items: list[dict[str, Any]] = []
        self._cart_accounts: list[Any] = []
        self._cart_batches: list[Any] = []
        self._cart_pending_drafts: list[Any] = []
        self._buyer_accounts: list[Any] = []
        self._buyer_runs: list[Any] = []
        self._buyer_run_items: list[Any] = []
        self._cart_preview = None
        self._verified_product_id = ""
        self._verified_sku_id = ""
        self._page = 1
        self._page_count = 1

        database_path = Path(getattr(self.db, "path"))
        self.runtime = getattr(window, "dongxiao_runtime", None)
        self.repository = (
            self.runtime.repository
            if self.runtime is not None
            else DongxiaoRepository(database_path)
        )
        self.pool_service = ProductPoolService(self.repository)
        self.import_service = ProductImportService(self.repository)
        if self.runtime is not None:
            self.task_service = self.runtime.task_service
            self.order_service = self.runtime.order_service
            self.aftersale_service = self.runtime.aftersale_service
            self.task_scheduler = self.runtime.scheduler
            self.preflight_service = self.runtime.preflight_service
            self.cart_service = self.runtime.cart_service
            self.buyer_flow_service = self.runtime.buyer_flow_service
        else:
            platform = OfficialDongxiaoPlatformAdapter(self.window.service)
            self.task_service = DongxiaoTaskService(
                self.repository,
                platform_write_gate=lambda _store_app_id: False,
            )
            self.order_service = DongxiaoOrderService(
                self.repository,
                platform,
            )
            self.aftersale_service = DongxiaoAftersaleService(
                self.repository,
                platform,
                task_service=self.task_service,
            )
            self.task_scheduler = None
            self.preflight_service = DongxiaoPreflightService(
                self.repository,
                runtime=None,
                sync_service=self.window.service,
                database_path=database_path,
                backup_dir=database_path.parent / "backups",
            )
            cart_repository = DesktopCartRepository(database_path)
            self.cart_service = DesktopCartService(
                cart_repository,
                self.repository,
                self.task_service,
                scheduler=None,
            )
            self.buyer_flow_service = DongxiaoBuyerFlowService(
                BuyerFlowRepository(database_path),
                self.repository,
                BuyerSessionVault(database_path.parent),
                order_service=self.order_service,
            )
        auth_client = getattr(window, "auth_client", None)
        auth_config = getattr(auth_client, "config", None)
        auth_context = getattr(window, "auth_context", None)
        access_token = str(
            getattr(getattr(auth_context, "tokens", None), "access_token", "")
            or ""
        )
        self.buyer_assistant_client = (
            BuyerAssistantClient(
                base_url=auth_config.base_url,
                access_token=access_token,
                timeout=float(getattr(auth_config, "request_timeout", 20.0)),
            )
            if auth_config is not None and access_token
            else None
        )
        self._last_pairing_id = ""
        self._last_pairing_identity_id = ""
        self._last_assistant_task_id = ""
        self.official_catalog_service = ProductCatalogService(
            self.repository,
            build_catalog_adapter(
                CatalogAdapterMode.OFFICIAL,
                sync_service=self.window.service,
                repository=self.repository,
            ),
        )
        self.cached_catalog_service = ProductCatalogService(
            self.repository,
            build_catalog_adapter(
                CatalogAdapterMode.CACHE,
                repository=self.repository,
            ),
        )

        self.store_count_var = tk.StringVar(value="店铺待读取")
        self.active_store_var = tk.StringVar(value="当前店铺：尚未选择")
        self.section_title_var = tk.StringVar()
        self.section_description_var = tk.StringVar()
        self.section_stage_var = tk.StringVar()
        self.store_var = tk.StringVar()
        self.source_status_var = tk.StringVar(value="数据来源：尚未同步")
        runtime_error = str(
            getattr(window, "dongxiao_runtime_error", "") or ""
        )
        if runtime_error:
            self.source_status_var.set(
                f"动销中心初始化异常：{runtime_error}"
            )
        self.product_page_var = tk.StringVar(value="商品 0 条")
        self.pool_stats_var = tk.StringVar(value="商品池尚未选择")
        self.sku_result_var = tk.StringVar(value="请选择商品后核验 SKU")
        self.task_stats_var = tk.StringVar(value="任务统计待读取")
        self.order_status_var = tk.StringVar(value="真实订单尚未同步")
        self.aftersale_status_var = tk.StringVar(value="售后工单尚未同步")
        self.cart_status_var = tk.StringVar(value="批量加购尚未配置")
        self.cart_preview_var = tk.StringVar(value="请先选择商品池和执行账号")
        self.pending_order_status_var = tk.StringVar(value="待付款草案尚未读取")
        self.cart_account_mode_var = tk.StringVar(value="模拟验证")
        self.cart_products_per_batch_var = tk.StringVar(value="10")
        self.cart_quantity_var = tk.StringVar(value="1")
        self.cart_sku_mode_var = tk.StringVar(value="首个可售 SKU")
        self.cart_specified_sku_var = tk.StringVar()
        self.cart_stock_policy_var = tk.StringVar(value="缺货跳过")
        self.cart_speed_var = tk.StringVar(value="保守")
        self.cart_interval_var = tk.StringVar(value="2500")
        self.cart_concurrency_var = tk.StringVar(value="1")
        self.cart_batch_name_var = tk.StringVar(value="桌面批量加购")
        self.buyer_account_status_var = tk.StringVar(value="买家账号尚未读取")
        self.buyer_flow_status_var = tk.StringVar(value="买家完整流程尚未准备")
        self.buyer_count_var = tk.StringVar(value="5")
        self.buyer_selection_mode_var = tk.StringVar(value="自动挑选")
        self.buyer_manual_ids_var = tk.StringVar()
        self.buyer_order_id_var = tk.StringVar()
        self.buyer_assistant_status_var = tk.StringVar(
            value="买家助手：等待扫码绑定"
        )
        self.audit_status_var = tk.StringVar(value="审计日志尚未读取")
        self.preflight_status_var = tk.StringVar(value="实盘检查尚未运行")
        self.platform_write_status_var = tk.StringVar(
            value="真实平台写操作：检查中"
        )
        self.overview_stats_vars = {
            "stores": tk.StringVar(value="—"),
            "products": tk.StringVar(value="—"),
            "pools": tk.StringVar(value="—"),
            "tasks": tk.StringVar(value="—"),
        }

        self.filter_keyword_var = tk.StringVar()
        self.filter_title_exact_var = tk.BooleanVar(value=False)
        self.filter_new_7d_var = tk.BooleanVar(value=False)
        self.filter_low_sales_var = tk.BooleanVar(value=False)
        self.filter_state_var = tk.StringVar(value="全部状态")
        self.filter_min_price_var = tk.StringVar()
        self.filter_max_price_var = tk.StringVar()
        self.filter_zero_sales_var = tk.BooleanVar(value=False)
        self.filter_sales_min_var = tk.StringVar()
        self.filter_sales_max_var = tk.StringVar()
        self.filter_listed_from_var = tk.StringVar()
        self.filter_listed_to_var = tk.StringVar()
        self.filter_product_ids_var = tk.StringVar()
        self.page_size_var = tk.StringVar(value="50")
        self.sku_mode_var = tk.StringVar(value="任意可售 SKU")
        self.sku_quantity_var = tk.StringVar(value="1")
        self.sku_id_var = tk.StringVar()
        self.pool_name_var = tk.StringVar()
        self.pool_group_var = tk.StringVar()
        self.pool_choice_var = tk.StringVar()
        today = date.today()
        self.order_start_var = tk.StringVar(
            value=(today - timedelta(days=6)).isoformat()
        )
        self.order_end_var = tk.StringVar(value=today.isoformat())
        self.order_id_var = tk.StringVar()
        self.aftersale_start_var = tk.StringVar(
            value=(today - timedelta(days=1)).isoformat()
        )
        self.aftersale_end_var = tk.StringVar(value=today.isoformat())
        self.aftersale_amount_var = tk.StringVar()

        self._build_header()
        self._build_workspace()
        self.show_section("overview")
        self.after(2000, self._task_activity_tick)

    def _build_header(self) -> None:
        header = ttk.Frame(self, style="Panel.TFrame", padding=(20, 15))
        header.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        header.columnconfigure(0, weight=1)

        title_box = ttk.Frame(header, style="Panel.TFrame")
        title_box.grid(row=0, column=0, sticky="w")
        ttk.Label(
            title_box,
            text="动销中心",
            font=("Microsoft YaHei UI", 16, "bold"),
        ).pack(anchor="w")
        ttk.Label(
            title_box,
            text="商品目录、筛选、SKU 规则与商品池均复用现有澜琅店铺与授权",
            style="Subtitle.TLabel",
        ).pack(anchor="w", pady=(5, 0))

        controls = ttk.Frame(header, style="Panel.TFrame")
        controls.grid(row=0, column=1, sticky="e")
        ttk.Label(controls, text="当前店铺").pack(side="left", padx=(0, 5))
        self.store_combo = ttk.Combobox(
            controls,
            textvariable=self.store_var,
            state="readonly",
            width=22,
        )
        self.store_combo.pack(side="left", padx=(0, 8))
        self.store_combo.bind(
            "<<ComboboxSelected>>",
            lambda _event: self._store_changed(),
        )
        ttk.Label(
            controls,
            textvariable=self.source_status_var,
            style="Subtitle.TLabel",
        ).pack(side="left", padx=(4, 0))

    def _build_workspace(self) -> None:
        workspace = ttk.Frame(self, style="TFrame")
        workspace.grid(row=1, column=0, sticky="nsew")
        workspace.columnconfigure(1, weight=1)
        workspace.rowconfigure(0, weight=1)

        navigation = tk.Frame(
            workspace,
            bg=PANEL,
            width=176,
            highlightbackground=BORDER,
            highlightthickness=1,
        )
        navigation.grid(row=0, column=0, sticky="ns", padx=(0, 12))
        navigation.grid_propagate(False)
        tk.Label(
            navigation,
            text="功能导航",
            bg=PANEL,
            fg=MUTED,
            anchor="w",
            padx=16,
            pady=12,
            font=("Microsoft YaHei UI", 9, "bold"),
        ).pack(fill="x")
        for section in DONGXIAO_SECTIONS:
            nav_button = tk.Button(
                navigation,
                text=section.label,
                command=lambda key=section.key: self.show_section(key),
                bg=PANEL,
                fg=TEXT,
                activebackground=SOFT_TEAL,
                activeforeground=BLUE,
                relief="flat",
                bd=0,
                anchor="w",
                justify="left",
                padx=16,
                pady=9,
                cursor="hand2",
                font=("Microsoft YaHei UI", 9),
            )
            nav_button.pack(fill="x", padx=7, pady=1)
            self.section_buttons[section.key] = nav_button
        self.platform_write_button = tk.Button(
            navigation,
            textvariable=self.platform_write_status_var,
            command=self._disable_platform_writes_now,
            bg="#FBE9E7",
            fg="#A33A2B",
            activebackground="#F5D4CF",
            activeforeground="#7C271C",
            relief="flat",
            bd=0,
            anchor="w",
            justify="left",
            wraplength=145,
            padx=16,
            pady=9,
            cursor="hand2",
            font=("Microsoft YaHei UI", 8, "bold"),
        )
        self.platform_write_button.pack(side="bottom", fill="x")
        tk.Label(
            navigation,
            text=(
                "买卖双方真实数据严格隔离。\n"
                "真实平台写操作默认关闭。"
            ),
            bg=PANEL,
            fg=MUTED,
            justify="left",
            anchor="w",
            padx=16,
            pady=14,
            font=("Microsoft YaHei UI", 8),
        ).pack(side="bottom", fill="x")

        self.section_host = ttk.Frame(
            workspace,
            style="Panel.TFrame",
            padding=(16, 14),
        )
        self.section_host.grid(row=0, column=1, sticky="nsew")
        self.section_host.columnconfigure(0, weight=1)
        self.section_host.rowconfigure(0, weight=1)
        self.section_views: dict[str, ttk.Frame] = {}
        self.section_views["overview"] = self._build_overview()
        self.section_views["stores"] = self._build_store_view()
        self.section_views["products"] = self._build_product_view()
        self.section_views["pools"] = self._build_pool_view()
        self.section_views["sku_rules"] = self._build_sku_view()
        self.section_views["cart_batch"] = self._build_cart_batch_view()
        self.section_views["pending_orders"] = self._build_pending_orders_view()
        self.section_views["accounts"] = self._build_cart_accounts_view()
        self.section_views["buyer_flow"] = self._build_buyer_flow_view()
        self.section_views["tasks"] = self._build_task_view()
        self.section_views["orders"] = self._build_order_view()
        self.section_views["aftersales"] = self._build_aftersale_view()
        self.section_views["audit"] = self._build_audit_view()
        self.section_views["real_ops"] = self._build_real_ops_view()
        self.section_views["preflight"] = self._build_preflight_view()
        for view in self.section_views.values():
            view.grid(row=0, column=0, sticky="nsew")
            view.grid_remove()

    def _build_overview(self) -> ttk.Frame:
        view = ttk.Frame(self.section_host, style="Panel.TFrame")
        view.columnconfigure(0, weight=1)
        ttk.Label(
            view,
            text="商品运营总览",
            font=("Microsoft YaHei UI", 15, "bold"),
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(
            view,
            textvariable=self.active_store_var,
            style="Subtitle.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(5, 16))

        cards = ttk.Frame(view, style="Panel.TFrame")
        cards.grid(row=2, column=0, sticky="ew")
        for index, (label, variable_key) in enumerate(
            (
                ("店铺", "stores"),
                ("已缓存商品", "products"),
                ("商品池", "pools"),
                ("任务/异常", "tasks"),
            )
        ):
            cards.columnconfigure(index, weight=1)
            card = ttk.Frame(
                cards,
                style="Card.TFrame",
                padding=(14, 12),
            )
            card.grid(
                row=0,
                column=index,
                sticky="nsew",
                padx=(0, 8) if index < 3 else 0,
            )
            ttk.Label(card, text=label, style="Subtitle.TLabel").pack(anchor="w")
            ttk.Label(
                card,
                textvariable=self.overview_stats_vars[variable_key],
                style="Section.TLabel",
                font=("Microsoft YaHei UI", 16, "bold"),
            ).pack(anchor="w", pady=(6, 0))
        ttk.Label(
            view,
            text=(
                "销量与上架时间只有来自官方接口且已同步时才可用于筛选；"
                "缺失数据不会被当作零销量。"
            ),
            style="Subtitle.TLabel",
            wraplength=760,
            justify="left",
        ).grid(row=3, column=0, sticky="w", pady=(18, 0))
        return view

    def _build_store_view(self) -> ttk.Frame:
        view = ttk.Frame(self.section_host, style="Panel.TFrame")
        view.columnconfigure(0, weight=1)
        ttk.Label(
            view,
            text="店铺连接",
            font=("Microsoft YaHei UI", 15, "bold"),
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(
            view,
            text=(
                "商品目录直接复用现有店铺、AppSecret 和扫码登录状态，"
                "不创建第二套店铺或登录体系。"
            ),
            style="Subtitle.TLabel",
            wraplength=760,
            justify="left",
        ).grid(row=1, column=0, sticky="w", pady=(6, 16))
        self.store_tree = ttk.Treeview(
            view,
            columns=("name", "app_id", "status"),
            show="headings",
            height=12,
        )
        for key, label, width in (
            ("name", "店铺名称", 240),
            ("app_id", "AppID", 260),
            ("status", "连接状态", 140),
        ):
            self.store_tree.heading(key, text=label)
            self.store_tree.column(key, width=width, anchor="w")
        self.store_tree.grid(row=2, column=0, sticky="nsew")
        view.rowconfigure(2, weight=1)
        return view

    def _build_product_view(self) -> ttk.Frame:
        view = ttk.Frame(self.section_host, style="Panel.TFrame")
        view.columnconfigure(0, weight=1)
        view.rowconfigure(3, weight=1)

        heading = ttk.Frame(view, style="Panel.TFrame")
        heading.grid(row=0, column=0, sticky="ew")
        heading.columnconfigure(0, weight=1)
        ttk.Label(
            heading,
            text="商品筛选",
            font=("Microsoft YaHei UI", 15, "bold"),
        ).grid(row=0, column=0, sticky="w")
        actions = ttk.Frame(heading, style="Panel.TFrame")
        actions.grid(row=0, column=1, sticky="e")
        self.sync_products_button = button(
            actions,
            "同步商品",
            self._sync_products,
            "Primary",
        )
        self.sync_products_button.pack(side="left", padx=(0, 6))
        self.import_products_button = button(
            actions,
            "批量导入",
            self._open_import_dialog,
        )
        self.import_products_button.pack(side="left", padx=(0, 6))
        button(
            actions,
            "查看缓存",
            self._show_cached_products,
        ).pack(side="left")

        filters = ttk.Frame(view, style="Panel.TFrame")
        filters.grid(row=1, column=0, sticky="ew", pady=(12, 8))
        for column in range(8):
            filters.columnconfigure(column, weight=1)
        self._entry_field(filters, 0, 0, "标题关键词", self.filter_keyword_var)
        ttk.Label(filters, text="状态").grid(row=0, column=2, sticky="w", padx=(6, 3))
        ttk.Combobox(
            filters,
            textvariable=self.filter_state_var,
            values=("全部状态", "上架", "下架", "未知"),
            state="readonly",
            width=10,
        ).grid(row=0, column=3, sticky="ew", padx=(0, 6))
        self._entry_field(filters, 0, 4, "最低价", self.filter_min_price_var)
        self._entry_field(filters, 0, 6, "最高价", self.filter_max_price_var)
        self._entry_field(filters, 1, 0, "最低销量", self.filter_sales_min_var)
        self._entry_field(filters, 1, 2, "最高销量", self.filter_sales_max_var)
        self._entry_field(filters, 1, 4, "上架起", self.filter_listed_from_var)
        self._entry_field(filters, 1, 6, "上架止", self.filter_listed_to_var)
        self._entry_field(filters, 2, 0, "商品 ID", self.filter_product_ids_var)
        tk.Checkbutton(
            filters,
            text="标题精准匹配",
            variable=self.filter_title_exact_var,
            bg=PANEL,
            fg=TEXT,
            activebackground=PANEL,
            selectcolor=PANEL,
            anchor="w",
        ).grid(row=3, column=0, columnspan=2, sticky="w")
        tk.Checkbutton(
            filters,
            text="近 7 天新品（需官方可信时间）",
            variable=self.filter_new_7d_var,
            bg=PANEL,
            fg=TEXT,
            activebackground=PANEL,
            selectcolor=PANEL,
            anchor="w",
        ).grid(row=3, column=2, columnspan=3, sticky="w")
        tk.Checkbutton(
            filters,
            text="低销量（0–10，需官方可信数据）",
            variable=self.filter_low_sales_var,
            bg=PANEL,
            fg=TEXT,
            activebackground=PANEL,
            selectcolor=PANEL,
            anchor="w",
        ).grid(row=3, column=5, columnspan=3, sticky="w")
        tk.Checkbutton(
            filters,
            text="仅零销量（需官方可信数据）",
            variable=self.filter_zero_sales_var,
            bg=PANEL,
            fg=TEXT,
            activebackground=PANEL,
            selectcolor=PANEL,
            anchor="w",
        ).grid(row=2, column=2, columnspan=3, sticky="w", padx=(6, 0))
        self.apply_filter_button = button(
            filters,
            "应用筛选",
            self._apply_filters,
            "Primary",
        )
        self.apply_filter_button.grid(row=2, column=5, sticky="e", padx=(4, 0))
        button(filters, "重置", self._reset_filters).grid(
            row=2,
            column=6,
            sticky="e",
            padx=(4, 0),
        )

        table_frame = ttk.Frame(view, style="Panel.TFrame")
        table_frame.grid(row=3, column=0, sticky="nsew")
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)
        self.product_tree = ttk.Treeview(
            table_frame,
            columns=(
                "product_id",
                "title",
                "state",
                "price",
                "stock",
                "sales",
                "listed",
                "source",
                "synced_at",
            ),
            show="headings",
            selectmode="extended",
        )
        columns = (
            ("product_id", "商品 ID", 130),
            ("title", "标题", 230),
            ("state", "状态", 64),
            ("price", "最低价", 82),
            ("stock", "库存", 64),
            ("sales", "销量", 72),
            ("listed", "上架时间", 130),
            ("source", "来源", 120),
            ("synced_at", "最近同步", 135),
        )
        for key, label, width in columns:
            self.product_tree.heading(key, text=label)
            self.product_tree.column(key, width=width, anchor="w")
        self.product_tree.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(
            table_frame,
            orient="vertical",
            command=self.product_tree.yview,
        )
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.product_tree.configure(yscrollcommand=scrollbar.set)

        footer = ttk.Frame(view, style="Panel.TFrame")
        footer.grid(row=4, column=0, sticky="ew", pady=(8, 0))
        footer.columnconfigure(2, weight=1)
        button(footer, "上一页", self._previous_page).grid(row=0, column=0)
        button(footer, "下一页", self._next_page).grid(
            row=0,
            column=1,
            padx=(5, 8),
        )
        ttk.Label(
            footer,
            textvariable=self.product_page_var,
            style="Subtitle.TLabel",
        ).grid(row=0, column=2, sticky="w")
        button(footer, "全选当前页", self._select_all_products).grid(
            row=0,
            column=3,
            padx=5,
        )
        button(footer, "加入商品池", self._add_selected_to_pool).grid(
            row=0,
            column=4,
            padx=(5, 0),
        )
        ttk.Label(footer, text="每页").grid(row=0, column=5, padx=(8, 3))
        ttk.Combobox(
            footer,
            textvariable=self.page_size_var,
            values=("50", "100", "200", "500"),
            state="readonly",
            width=6,
        ).grid(row=0, column=6)
        return view

    def _entry_field(
        self,
        parent: ttk.Frame,
        row: int,
        column: int,
        label: str,
        variable: tk.StringVar,
    ) -> None:
        ttk.Label(parent, text=label).grid(
            row=row,
            column=column,
            sticky="w",
            padx=(6, 3),
            pady=3,
        )
        ttk.Entry(parent, textvariable=variable, width=14).grid(
            row=row,
            column=column + 1,
            sticky="ew",
            padx=(0, 6),
            pady=3,
        )

    def _build_pool_view(self) -> ttk.Frame:
        view = ttk.Frame(self.section_host, style="Panel.TFrame")
        view.columnconfigure(0, weight=1)
        view.rowconfigure(3, weight=1)
        ttk.Label(
            view,
            text="商品池",
            font=("Microsoft YaHei UI", 15, "bold"),
        ).grid(row=0, column=0, sticky="w")

        toolbar = ttk.Frame(view, style="Panel.TFrame")
        toolbar.grid(row=1, column=0, sticky="ew", pady=(10, 8))
        ttk.Label(toolbar, text="商品池").pack(side="left")
        self.pool_combo = ttk.Combobox(
            toolbar,
            textvariable=self.pool_choice_var,
            state="readonly",
            width=24,
        )
        self.pool_combo.pack(side="left", padx=(5, 8))
        self.pool_combo.bind(
            "<<ComboboxSelected>>",
            lambda _event: self._load_pool_details(),
        )
        button(toolbar, "新建", self._create_pool, "Primary").pack(side="left")
        button(toolbar, "重命名", self._rename_pool).pack(side="left", padx=5)
        button(toolbar, "复制", self._copy_pool).pack(side="left")
        button(toolbar, "移除明细", self._remove_pool_item).pack(
            side="left",
            padx=5,
        )
        button(toolbar, "上移", lambda: self._move_pool_item(-1)).pack(
            side="left"
        )
        button(toolbar, "下移", lambda: self._move_pool_item(1)).pack(
            side="left",
            padx=5,
        )
        button(toolbar, "导出", self._export_pool).pack(side="left")
        button(toolbar, "导入 CSV", self._import_pool).pack(
            side="left",
            padx=(5, 0),
        )
        button(toolbar, "历史", self._show_pool_history).pack(
            side="left",
            padx=5,
        )

        ttk.Label(
            view,
            textvariable=self.pool_stats_var,
            style="Subtitle.TLabel",
        ).grid(row=2, column=0, sticky="w", pady=(0, 8))
        self.pool_tree = ttk.Treeview(
            view,
            columns=("product_id", "sku_id", "position", "created_at"),
            show="headings",
            selectmode="browse",
        )
        for key, label, width in (
            ("product_id", "商品 ID", 180),
            ("sku_id", "SKU ID（空表示商品级）", 210),
            ("position", "排序", 70),
            ("created_at", "加入时间", 170),
        ):
            self.pool_tree.heading(key, text=label)
            self.pool_tree.column(key, width=width, anchor="w")
        self.pool_tree.grid(row=3, column=0, sticky="nsew")
        return view

    def _build_sku_view(self) -> ttk.Frame:
        view = ttk.Frame(self.section_host, style="Panel.TFrame")
        view.columnconfigure(0, weight=1)
        ttk.Label(
            view,
            text="SKU 规则",
            font=("Microsoft YaHei UI", 15, "bold"),
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(
            view,
            text=(
                "规则只读取已缓存官方商品数据，并执行库存、起购、限购、"
                "价格变化和商品状态检查。不会调用平台写接口。"
            ),
            style="Subtitle.TLabel",
            wraplength=760,
            justify="left",
        ).grid(row=1, column=0, sticky="w", pady=(6, 16))
        controls = ttk.Frame(view, style="Panel.TFrame")
        controls.grid(row=2, column=0, sticky="w")
        ttk.Label(controls, text="模式").pack(side="left")
        ttk.Combobox(
            controls,
            textvariable=self.sku_mode_var,
            values=(
                "任意可售 SKU",
                "最低价 SKU",
                "最高价 SKU",
                "指定 SKU",
                "默认 SKU",
            ),
            state="readonly",
            width=18,
        ).pack(side="left", padx=(5, 12))
        ttk.Label(controls, text="数量").pack(side="left")
        ttk.Entry(
            controls,
            textvariable=self.sku_quantity_var,
            width=7,
        ).pack(side="left", padx=(5, 12))
        ttk.Label(controls, text="指定 SKU").pack(side="left")
        ttk.Entry(
            controls,
            textvariable=self.sku_id_var,
            width=22,
        ).pack(side="left", padx=(5, 12))
        button(
            controls,
            "核验选中商品",
            self._check_selected_sku,
            "Primary",
        ).pack(side="left")
        button(
            controls,
            "加入商品池",
            self._add_verified_sku_to_pool,
        ).pack(side="left", padx=(6, 0))
        ttk.Label(
            view,
            textvariable=self.sku_result_var,
            style="Subtitle.TLabel",
            wraplength=780,
            justify="left",
        ).grid(row=3, column=0, sticky="w", pady=(18, 0))
        return view

    def _build_cart_accounts_view(self) -> ttk.Frame:
        view = ttk.Frame(self.section_host, style="Panel.TFrame")
        view.columnconfigure(0, weight=1)
        view.rowconfigure(3, weight=1)
        ttk.Label(
            view,
            text="多账号任务",
            font=("Microsoft YaHei UI", 15, "bold"),
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(
            view,
            text=(
                "执行账号只绑定当前澜琅店铺，不创建第二套账号系统，"
                "也不在普通数据库中保存密码或 Token。"
                "Mock 账号只用于隔离流程验证；真实买家购物车接口待官方授权能力。"
            ),
            style="Subtitle.TLabel",
            wraplength=820,
            justify="left",
        ).grid(row=1, column=0, sticky="w", pady=(6, 12))
        toolbar = ttk.Frame(view, style="Panel.TFrame")
        toolbar.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        ttk.Label(toolbar, text="新账号类型").pack(side="left")
        ttk.Combobox(
            toolbar,
            textvariable=self.cart_account_mode_var,
            values=("模拟验证", "真实账号（待授权）"),
            state="readonly",
            width=18,
        ).pack(side="left", padx=(5, 10))
        button(
            toolbar,
            "添加执行账号",
            self._add_cart_account,
            "Primary",
        ).pack(side="left")
        button(
            toolbar,
            "检查连接",
            self._check_selected_cart_account,
        ).pack(side="left", padx=(6, 0))
        button(
            toolbar,
            "刷新",
            self._load_cart_accounts,
        ).pack(side="left", padx=(6, 0))
        self.cart_account_tree = ttk.Treeview(
            view,
            columns=("name", "mode", "status", "store", "checked", "error"),
            show="headings",
            selectmode="browse",
        )
        for key, label, width in (
            ("name", "账号显示名", 140),
            ("mode", "来源", 100),
            ("status", "连接状态", 120),
            ("store", "绑定店铺", 190),
            ("checked", "最近检查", 145),
            ("error", "失败原因", 260),
        ):
            self.cart_account_tree.heading(key, text=label)
            self.cart_account_tree.column(key, width=width, anchor="w")
        self.cart_account_tree.grid(row=3, column=0, sticky="nsew")
        self.cart_account_tree.bind(
            "<Double-1>",
            lambda _event: self._check_selected_cart_account(),
        )
        ttk.Label(
            view,
            text=(
                "真实账号只有在官方买家购物车授权、购物车服务端读取能力"
                "得到确认后才可进入执行；当前不会用网页私有接口代替。"
            ),
            style="Subtitle.TLabel",
        ).grid(row=4, column=0, sticky="w", pady=(8, 0))
        ttk.Label(
            view,
            text="独立买家微信账号",
            font=("Microsoft YaHei UI", 12, "bold"),
        ).grid(row=5, column=0, sticky="w", pady=(14, 4))
        buyer_toolbar = ttk.Frame(view, style="Panel.TFrame")
        buyer_toolbar.grid(row=6, column=0, sticky="ew", pady=(0, 6))
        button(
            buyer_toolbar,
            "添加真实买家账号",
            self._add_official_buyer_account,
            "Primary",
        ).pack(side="left")
        button(
            buyer_toolbar,
            "添加 Mock 测试账号",
            self._add_mock_buyer_account,
        ).pack(side="left", padx=(6, 0))
        button(
            buyer_toolbar,
            "打开官方微信",
            self._open_official_wechat_client,
        ).pack(side="left", padx=(6, 0))
        button(
            buyer_toolbar,
            "检查身份",
            self._check_selected_buyer_account,
        ).pack(side="left", padx=(6, 0))
        button(
            buyer_toolbar,
            "设为当前",
            self._set_selected_buyer_account_current,
        ).pack(side="left", padx=(6, 0))
        button(
            buyer_toolbar,
            "删除",
            self._delete_selected_buyer_account,
        ).pack(side="left", padx=(6, 0))
        self.buyer_account_tree = ttk.Treeview(
            view,
            columns=("current", "name", "provider", "identity", "status", "auth", "verified"),
            show="headings",
            selectmode="browse",
            height=5,
        )
        for key, label, width in (
            ("current", "当前", 50),
            ("name", "买家显示名", 130),
            ("provider", "授权来源", 115),
            ("identity", "真实身份", 125),
            ("status", "会话状态", 125),
            ("auth", "授权时间", 145),
            ("verified", "最近验证", 145),
        ):
            self.buyer_account_tree.heading(key, text=label)
            self.buyer_account_tree.column(key, width=width, anchor="w")
        self.buyer_account_tree.grid(row=7, column=0, sticky="nsew")
        ttk.Label(
            view,
            textvariable=self.buyer_account_status_var,
            style="Subtitle.TLabel",
            wraplength=850,
            justify="left",
        ).grid(row=8, column=0, sticky="w", pady=(6, 0))
        return view

    def _load_cart_accounts(self) -> None:
        store = self._current_store()
        if store is None:
            return
        scope = f"dongxiao:cart-accounts:{store.id}"
        if self.window.has_running_task(scope):
            return

        def work(_progress):
            return self.cart_service.list_accounts(
                store,
                permission_checker=self._permission_checker,
            )

        self.window.run_task(
            work,
            self._cart_accounts_loaded,
            lambda error: self.window.set_status(
                f"执行账号读取失败：{error}",
                busy=False,
            ),
            log_title="读取多账号任务执行账号",
            task_scope=scope,
            store_app_id=str(store.app_id),
        )

    def _cart_accounts_loaded(self, accounts) -> None:
        self._cart_accounts = list(accounts)
        listbox = getattr(self, "cart_account_listbox", None)
        if listbox is not None:
            listbox.delete(0, "end")
            for account in accounts:
                listbox.insert(
                    "end",
                    f"{account.display_name} · {account.adapter_mode.value} · "
                    f"{account.status.value}",
                )
        tree = getattr(self, "cart_account_tree", None)
        if tree is not None:
            for item_id in tree.get_children():
                tree.delete(item_id)
            mode_labels = {
                CartAdapterMode.MOCK: "Mock 模拟",
                CartAdapterMode.OFFICIAL: "真实账号待授权",
                CartAdapterMode.MANUAL: "人工",
                CartAdapterMode.UNKNOWN: "未知",
            }
            status_labels = {
                "mock_ready": "模拟环境可用",
                "pending_authorization": "等待平台授权",
                "connected": "已连接",
                "expired": "授权已失效",
                "disconnected": "未连接",
            }
            for account in accounts:
                tree.insert(
                    "",
                    "end",
                    iid=account.account_id,
                    values=(
                        account.display_name,
                        mode_labels.get(account.adapter_mode, account.adapter_mode.value),
                        status_labels.get(account.status.value, account.status.value),
                        f"{_store_name_for_id(self._stores, account.store_id)}",
                        _date_time(account.last_checked_at),
                        account.last_error,
                    ),
                )
        self.cart_status_var.set(f"已读取 {len(accounts)} 个执行账号")

    def _add_cart_account(self) -> None:
        store = self._current_store()
        if store is None:
            messagebox.showwarning("执行账号", "请先选择店铺。", parent=self)
            return
        display_name = simpledialog.askstring(
            "添加执行账号",
            "请输入账号显示名（不要输入密码、Cookie 或 Token）：",
            parent=self,
        )
        if not display_name:
            return
        mode = (
            CartAdapterMode.MOCK
            if self.cart_account_mode_var.get() == "模拟验证"
            else CartAdapterMode.OFFICIAL
        )
        if mode is CartAdapterMode.OFFICIAL:
            if not messagebox.askyesno(
                "真实账号授权待确认",
                (
                    "当前可确认的官方接口没有买家购物车能力。\n"
                    "该账号将保存为“等待平台授权”，不会执行真实加购。\n\n"
                    "是否继续创建这个本地授权档案？"
                ),
                parent=self,
            ):
                return

        def work(_progress):
            return self.cart_service.create_account(
                store,
                display_name=display_name,
                adapter_mode=mode,
                permission_checker=self._permission_checker,
                actor_id=self._actor_id(),
            )

        self.window.run_task(
            work,
            lambda _account: (
                self._load_cart_accounts(),
                self.window.set_status("执行账号已保存", busy=False),
            ),
            lambda error: messagebox.showerror(
                "添加执行账号失败",
                error,
                parent=self,
            ),
            log_title="添加动销执行账号",
            task_scope=f"dongxiao:cart-account-add:{store.id}",
            store_app_id=str(store.app_id),
        )

    def _selected_cart_account(self):
        tree = getattr(self, "cart_account_tree", None)
        if tree is None:
            return None
        selection = tree.selection()
        if not selection:
            return None
        selected_id = str(selection[0])
        return next(
            (
                account
                for account in self._cart_accounts
                if account.account_id == selected_id
            ),
            None,
        )

    def _check_selected_cart_account(self) -> None:
        account = self._selected_cart_account()
        if account is None:
            messagebox.showwarning(
                "检查执行账号",
                "请先选择一个执行账号。",
                parent=self,
            )
            return

        def work(_progress):
            return self.cart_service.check_account(
                account,
                permission_checker=self._permission_checker,
                actor_id=self._actor_id(),
            )

        self.window.run_task(
            work,
            lambda saved: (
                self._load_cart_accounts(),
                self.window.set_status(
                    f"账号检查完成：{saved.status.value}",
                    busy=False,
                ),
            ),
            lambda error: messagebox.showerror(
                "检查执行账号失败",
                error,
                parent=self,
            ),
            log_title="检查动销执行账号",
            task_scope=f"dongxiao:cart-account-check:{account.account_id}",
        )

    def _load_buyer_accounts(self) -> None:
        scope = "dongxiao:buyer-accounts"
        if self.window.has_running_task(scope):
            return
        service = self.buyer_flow_service
        permission = self._permission_checker

        self.window.run_task(
            lambda _progress: service.list_accounts(
                permission_checker=permission
            ),
            self._buyer_accounts_loaded,
            lambda error: self.buyer_account_status_var.set(
                f"买家账号读取失败：{error}"
            ),
            log_title="读取独立买家账号",
            task_scope=scope,
        )

    def _buyer_accounts_loaded(self, accounts) -> None:
        self._buyer_accounts = list(accounts)
        tree = getattr(self, "buyer_account_tree", None)
        if tree is not None:
            for item_id in tree.get_children():
                tree.delete(item_id)
            provider_labels = {
                "official_wechat": "官方微信",
                "official_partner": "官方合作方",
                "manual_handoff": "人工交接",
                "mock": "Mock 测试",
                "unknown": "未知",
            }
            identity_labels = {
                "unverified": "未验证",
                "user_asserted": "用户自述·未验证",
                "verified_official": "官方已验证",
                "verified_mock": "Mock 已验证",
                "expired": "身份已失效",
                "revoked": "身份已撤销",
            }
            for account in accounts:
                tree.insert(
                    "",
                    "end",
                    iid=account.buyer_account_id,
                    values=(
                        "是" if account.is_current else "",
                        account.display_name,
                        provider_labels.get(account.provider.value, account.provider.value),
                        identity_labels.get(
                            account.identity_status.value,
                            account.identity_status.value,
                        ),
                        account.status.value,
                        _date_time(account.authorization_at),
                        _date_time(account.last_verified_at),
                    ),
                )
        current = next(
            (account for account in accounts if account.is_current),
            None,
        )
        capability = self.buyer_flow_service.capability()
        if current is None:
            self.buyer_account_status_var.set(
                f"未选择已验证买家账号；{capability.reason}"
            )
        else:
            self.buyer_account_status_var.set(
                f"当前买家：{current.display_name}；"
                f"身份：{current.identity_status.value}；"
                f"{capability.reason}"
            )

    def _add_official_buyer_account(self) -> None:
        display_name = simpledialog.askstring(
            "添加真实买家账号",
            "请输入用于识别的买家显示名。\n"
            "不要输入密码、Cookie、Token 或验证码：",
            parent=self,
        )
        if not display_name:
            return

        def work(_progress):
            return self.buyer_flow_service.create_official_account(
                display_name=display_name,
                permission_checker=self._permission_checker,
                actor_id=self._actor_id(),
            )

        def done(result) -> None:
            self._load_buyer_accounts()
            messagebox.showinfo(
                "官方买家授权能力",
                (
                    f"账号“{result.account.display_name}”已保存为待授权记录。\n\n"
                    f"{result.instructions or result.capability.reason}\n\n"
                    "在官方能力未确认前，该账号不会产生真实登录或购买成功。"
                ),
                parent=self,
            )

        self.window.run_task(
            work,
            done,
            lambda error: messagebox.showerror(
                "添加买家账号失败",
                error,
                parent=self,
            ),
            log_title="添加官方买家账号",
            task_scope="dongxiao:buyer-account-add",
        )

    def _add_mock_buyer_account(self) -> None:
        display_name = simpledialog.askstring(
            "添加 Mock 买家账号",
            "这是本地流程测试账号，不代表真实微信身份。请输入显示名：",
            parent=self,
        )
        if not display_name:
            return
        self.window.run_task(
            lambda _progress: self.buyer_flow_service.create_mock_account(
                display_name=display_name,
                permission_checker=self._permission_checker,
                actor_id=self._actor_id(),
            ),
            lambda _account: self._load_buyer_accounts(),
            lambda error: messagebox.showerror(
                "添加 Mock 买家失败",
                error,
                parent=self,
            ),
            log_title="添加 Mock 买家账号",
            task_scope="dongxiao:buyer-account-add-mock",
        )

    def _selected_buyer_account(self):
        tree = getattr(self, "buyer_account_tree", None)
        if tree is None or not tree.selection():
            return None
        selected_id = str(tree.selection()[0])
        return next(
            (
                account
                for account in self._buyer_accounts
                if account.buyer_account_id == selected_id
            ),
            None,
        )

    def _check_selected_buyer_account(self) -> None:
        account = self._selected_buyer_account()
        if account is None:
            messagebox.showwarning(
                "检查买家账号",
                "请先选择一个买家账号。",
                parent=self,
            )
            return
        self.window.run_task(
            lambda _progress: self.buyer_flow_service.check_account(
                account.buyer_account_id,
                permission_checker=self._permission_checker,
                actor_id=self._actor_id(),
            ),
            lambda _account: self._load_buyer_accounts(),
            lambda error: messagebox.showerror(
                "检查买家账号失败",
                error,
                parent=self,
            ),
            log_title="检查买家账号",
            task_scope=f"dongxiao:buyer-check:{account.buyer_account_id}",
        )

    def _set_selected_buyer_account_current(self) -> None:
        account = self._selected_buyer_account()
        if account is None:
            messagebox.showwarning(
                "选择当前买家",
                "请先选择一个买家账号。",
                parent=self,
            )
            return
        self.window.run_task(
            lambda _progress: self.buyer_flow_service.set_current_account(
                account.buyer_account_id,
                permission_checker=self._permission_checker,
                actor_id=self._actor_id(),
            ),
            lambda _account: self._load_buyer_accounts(),
            lambda error: messagebox.showerror(
                "切换买家账号失败",
                error,
                parent=self,
            ),
            log_title="切换当前买家账号",
            task_scope=f"dongxiao:buyer-current:{account.buyer_account_id}",
        )

    def _delete_selected_buyer_account(self) -> None:
        account = self._selected_buyer_account()
        if account is None:
            messagebox.showwarning(
                "删除买家账号",
                "请先选择一个买家账号。",
                parent=self,
            )
            return
        if not messagebox.askyesno(
            "删除买家账号",
            f"确认删除买家账号“{account.display_name}”及其加密会话吗？",
            parent=self,
        ):
            return
        self.window.run_task(
            lambda _progress: self.buyer_flow_service.delete_account(
                account.buyer_account_id,
                permission_checker=self._permission_checker,
                actor_id=self._actor_id(),
            ),
            lambda _result: self._load_buyer_accounts(),
            lambda error: messagebox.showerror(
                "删除买家账号失败",
                error,
                parent=self,
            ),
            log_title="删除买家账号",
            task_scope=f"dongxiao:buyer-delete:{account.buyer_account_id}",
        )

    def _open_official_wechat_client(self) -> None:
        try:
            self.buyer_flow_service.open_official_wechat_client()
        except Exception as exc:
            messagebox.showinfo(
                "打开官方微信",
                f"{exc}\n\n请用户本人手动打开官方微信客户端。",
                parent=self,
            )
            return
        self.buyer_account_status_var.set(
            "已请求打开官方微信客户端；登录、扫码和验证码均由用户本人完成。"
        )

    def _require_buyer_assistant_client(self):
        client = getattr(self, "buyer_assistant_client", None)
        if client is None:
            raise ValueError(
                "买家助手后端需要已配置的澜琅登录会话和 Supabase 地址"
            )
        return client

    def _create_buyer_pairing(self) -> None:
        store = self._current_store()
        if store is None:
            messagebox.showwarning(
                "微信扫码绑定",
                "请先选择目标店铺。",
                parent=self,
            )
            return
        try:
            client = self._require_buyer_assistant_client()
        except Exception as exc:
            messagebox.showerror("微信扫码绑定", str(exc), parent=self)
            return
        self.window.run_task(
            lambda _progress: client.create_pairing(str(store.app_id)),
            self._buyer_pairing_created,
            lambda error: messagebox.showerror(
                "生成扫码绑定失败",
                error,
                parent=self,
            ),
            log_title="生成买家助手微信绑定",
            task_scope=f"buyer-assistant:pairing:{store.id}",
        )

    def _buyer_pairing_created(self, result: dict) -> None:
        self._last_pairing_id = str(result.get("pairing_id") or "")
        self.buyer_assistant_status_var.set(
            str(result.get("message") or "绑定票据已生成")
        )
        dialog = tk.Toplevel(self)
        dialog.title("微信扫码绑定买家助手")
        dialog.geometry("460x520")
        dialog.transient(self.window)
        ttk.Label(
            dialog,
            text="使用微信扫描绑定码",
            font=("Microsoft YaHei UI", 14, "bold"),
        ).pack(pady=(20, 8))
        qr = str(result.get("qr_image_base64") or "")
        if qr:
            try:
                image = tk.PhotoImage(data=qr, format="png")
                label = ttk.Label(dialog, image=image)
                label.image = image
                label.pack(pady=10)
            except tk.TclError:
                ttk.Label(dialog, text="小程序码图片无法显示").pack(pady=10)
        else:
            ttk.Label(
                dialog,
                text="小程序码尚未生成，请完成小程序 AppID/AppSecret 配置。",
                wraplength=400,
                justify="center",
            ).pack(pady=10)
        ttk.Label(
            dialog,
            text=f"绑定票据：{result.get('ticket', '')}",
            wraplength=410,
        ).pack(pady=8)
        ttk.Label(
            dialog,
            text=(
                "票据 5 分钟有效且只能使用一次。\n"
                "手机上需由用户本人 wx.login 并确认绑定。"
            ),
            justify="center",
        ).pack(pady=8)
        button(dialog, "关闭", dialog.destroy).pack(pady=8)

    def _poll_buyer_pairing(self) -> None:
        if not self._last_pairing_id:
            messagebox.showwarning(
                "绑定状态",
                "请先生成扫码绑定。",
                parent=self,
            )
            return
        try:
            client = self._require_buyer_assistant_client()
        except Exception as exc:
            messagebox.showerror("绑定状态", str(exc), parent=self)
            return

        def done(result: dict) -> None:
            identity = result.get("identity") or {}
            if identity.get("buyer_identity_id"):
                self._last_pairing_identity_id = str(
                    identity["buyer_identity_id"]
                )
            self.buyer_assistant_status_var.set(
                f"绑定状态：{result.get('status', 'unknown')}"
                + (
                    f"；微信买家：{identity.get('display_name', '')}"
                    if identity
                    else ""
                )
            )

        self.window.run_task(
            lambda _progress: client.pairing_status(self._last_pairing_id),
            done,
            lambda error: messagebox.showerror("绑定状态", error, parent=self),
            log_title="检查微信扫码绑定状态",
            task_scope=f"buyer-assistant:pairing-status:{self._last_pairing_id}",
        )

    def _send_buyer_task_to_phone(self) -> None:
        run = self._selected_buyer_run()
        if run is None:
            messagebox.showwarning(
                "发送手机任务",
                "请先选择电脑端买家流程。",
                parent=self,
            )
            return
        if not self._last_pairing_identity_id:
            messagebox.showwarning(
                "发送手机任务",
                "请先生成扫码绑定并刷新到“已绑定”状态。",
                parent=self,
            )
            return
        try:
            client = self._require_buyer_assistant_client()
        except Exception as exc:
            messagebox.showerror("发送手机任务", str(exc), parent=self)
            return
        items = self.buyer_flow_service.repository.list_items(run.run_id)
        task = {
            "buyer_identity_id": self._last_pairing_identity_id,
            "store_app_id": run.store_app_id,
            "store_name": run.store_name,
            "local_run_id": run.run_id,
            "idempotency_key": f"assistant:{run.idempotency_key}",
            "requested_count": run.requested_count,
            "items": [
                {
                    "product_id": item.product_id,
                    "sku_id": item.sku_id,
                    "title": item.title,
                    "specs": dict(item.specs),
                    "unit_price_cents": item.unit_price_cents,
                    "quantity": item.quantity,
                    "stock": item.stock,
                }
                for item in items[:10]
            ],
        }

        def done(result: dict) -> None:
            self._last_assistant_task_id = str(
                result.get("assistant_task_id") or ""
            )
            self.buyer_assistant_status_var.set(
                "商品任务已发送到手机；用户确认仅代表用户操作，"
                "不代表购物车服务端核验。"
            )

        self.window.run_task(
            lambda _progress: client.create_task(task),
            done,
            lambda error: messagebox.showerror("发送手机任务失败", error, parent=self),
            log_title="发送商品任务到微信买家助手",
            task_scope=f"buyer-assistant:send:{run.run_id}",
        )

    def _refresh_buyer_phone_progress(self) -> None:
        if not self._last_assistant_task_id:
            messagebox.showwarning(
                "手机进度",
                "当前没有已发送的手机任务。",
                parent=self,
            )
            return
        try:
            client = self._require_buyer_assistant_client()
        except Exception as exc:
            messagebox.showerror("手机进度", str(exc), parent=self)
            return

        def done(result: dict) -> None:
            items = result.get("buyer_assistant_task_items") or []
            confirmed = sum(
                item.get("status") == "user_confirmed"
                for item in items
            )
            self.buyer_assistant_status_var.set(
                f"手机操作进度：用户确认 {confirmed}/{len(items)}；"
                f"任务状态 {result.get('status', '')}；"
                "该进度不是平台购物车核验。"
            )

        self.window.run_task(
            lambda _progress: client.task_status(self._last_assistant_task_id),
            done,
            lambda error: messagebox.showerror("手机进度", error, parent=self),
            log_title="读取微信买家助手任务进度",
            task_scope=f"buyer-assistant:status:{self._last_assistant_task_id}",
        )

    def _build_buyer_flow_view(self) -> ttk.Frame:
        view = ttk.Frame(self.section_host, style="Panel.TFrame")
        view.columnconfigure(0, weight=1)
        view.rowconfigure(6, weight=1)
        ttk.Label(
            view,
            text="买家完整流程",
            font=("Microsoft YaHei UI", 15, "bold"),
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(
            view,
            text=(
                "商家店铺授权与买家微信身份严格分离。"
                "只有官方可验证的买家身份才允许进入真实加购和订单创建；"
                "当前不存在的官方能力会明确阻塞，不会用网页私有接口或 Mock 冒充。"
            ),
            style="Subtitle.TLabel",
            wraplength=850,
            justify="left",
        ).grid(row=1, column=0, sticky="w", pady=(6, 8))
        form = ttk.Frame(view, style="Panel.TFrame")
        form.grid(row=2, column=0, sticky="ew", pady=(0, 6))
        ttk.Label(form, text="选择方式").pack(side="left")
        ttk.Combobox(
            form,
            textvariable=self.buyer_selection_mode_var,
            values=("自动挑选", "手动选择"),
            state="readonly",
            width=12,
        ).pack(side="left", padx=(5, 12))
        ttk.Label(form, text="商品数量").pack(side="left")
        ttk.Combobox(
            form,
            textvariable=self.buyer_count_var,
            values=tuple(str(index) for index in range(5, 11)),
            state="readonly",
            width=6,
        ).pack(side="left", padx=(5, 12))
        ttk.Label(form, text="手动商品 ID").pack(side="left")
        ttk.Entry(
            form,
            textvariable=self.buyer_manual_ids_var,
            width=36,
        ).pack(side="left", padx=(5, 12))
        ttk.Label(form, text="订单号").pack(side="left")
        ttk.Entry(
            form,
            textvariable=self.buyer_order_id_var,
            width=24,
        ).pack(side="left", padx=(5, 0))
        actions = ttk.Frame(view, style="Panel.TFrame")
        actions.grid(row=3, column=0, sticky="ew", pady=(0, 6))
        button(
            actions,
            "准备并预览",
            self._prepare_buyer_flow,
            "Primary",
        ).pack(side="left")
        button(
            actions,
            "执行 Mock 流程",
            lambda: self._buyer_flow_action("mock"),
        ).pack(side="left", padx=(6, 0))
        button(
            actions,
            "开始官方交接",
            lambda: self._buyer_flow_action("handoff"),
        ).pack(side="left", padx=(6, 0))
        button(
            actions,
            "已在官方客户端完成",
            lambda: self._buyer_flow_action("handoff_done"),
        ).pack(side="left", padx=(6, 0))
        button(
            actions,
            "暂停",
            lambda: self._buyer_flow_action("pause"),
        ).pack(side="left", padx=(6, 0))
        button(
            actions,
            "继续",
            lambda: self._buyer_flow_action("resume"),
        ).pack(side="left", padx=(6, 0))
        button(
            actions,
            "取消",
            lambda: self._buyer_flow_action("cancel"),
        ).pack(side="left", padx=(6, 0))
        button(
            actions,
            "核验官方待付款订单",
            self._verify_buyer_order,
        ).pack(side="left", padx=(6, 0))
        assistant_actions = ttk.Frame(view, style="Panel.TFrame")
        assistant_actions.grid(row=4, column=0, sticky="ew", pady=(0, 6))
        button(
            assistant_actions,
            "生成微信扫码绑定",
            self._create_buyer_pairing,
            "Primary",
        ).pack(side="left")
        button(
            assistant_actions,
            "刷新绑定状态",
            self._poll_buyer_pairing,
        ).pack(side="left", padx=(6, 0))
        button(
            assistant_actions,
            "发送任务到手机",
            self._send_buyer_task_to_phone,
        ).pack(side="left", padx=(6, 0))
        button(
            assistant_actions,
            "刷新手机进度",
            self._refresh_buyer_phone_progress,
        ).pack(side="left", padx=(6, 0))
        ttk.Label(
            assistant_actions,
            textvariable=self.buyer_assistant_status_var,
            style="Subtitle.TLabel",
        ).pack(side="left", padx=(12, 0))
        ttk.Label(
            view,
            textvariable=self.buyer_flow_status_var,
            style="Subtitle.TLabel",
            wraplength=850,
            justify="left",
        ).grid(row=5, column=0, sticky="w", pady=(0, 6))
        panes = ttk.Panedwindow(view, orient="vertical")
        panes.grid(row=6, column=0, sticky="nsew")
        runs_box = ttk.Frame(panes, style="Panel.TFrame")
        runs_box.columnconfigure(0, weight=1)
        runs_box.rowconfigure(1, weight=1)
        ttk.Label(
            runs_box,
            text="流程历史与状态",
            style="Subtitle.TLabel",
        ).grid(row=0, column=0, sticky="w")
        self.buyer_run_tree = ttk.Treeview(
            runs_box,
            columns=("created", "store", "items", "mode", "status", "order", "reason"),
            show="headings",
            selectmode="browse",
            height=8,
        )
        for key, label, width in (
            ("created", "创建时间", 145),
            ("store", "店铺", 145),
            ("items", "商品数", 65),
            ("mode", "选择方式", 105),
            ("status", "状态", 140),
            ("order", "真实订单号", 190),
            ("reason", "说明", 420),
        ):
            self.buyer_run_tree.heading(key, text=label)
            self.buyer_run_tree.column(key, width=width, anchor="w")
        self.buyer_run_tree.grid(row=1, column=0, sticky="nsew")
        self.buyer_run_tree.bind(
            "<<TreeviewSelect>>",
            lambda _event: self._render_selected_buyer_run(),
        )
        panes.add(runs_box, weight=2)
        items_box = ttk.Frame(panes, style="Panel.TFrame")
        items_box.columnconfigure(0, weight=1)
        items_box.rowconfigure(1, weight=1)
        ttk.Label(
            items_box,
            text="商品、SKU、价格与订单预览",
            style="Subtitle.TLabel",
        ).grid(row=0, column=0, sticky="w")
        self.buyer_item_tree = ttk.Treeview(
            items_box,
            columns=("product", "title", "sku", "specs", "price", "qty", "stock", "subtotal"),
            show="headings",
        )
        for key, label, width in (
            ("product", "商品 ID", 130),
            ("title", "商品名称", 210),
            ("sku", "SKU", 130),
            ("specs", "规格", 170),
            ("price", "单价", 80),
            ("qty", "数量", 60),
            ("stock", "库存", 65),
            ("subtotal", "商品总价", 95),
        ):
            self.buyer_item_tree.heading(key, text=label)
            self.buyer_item_tree.column(key, width=width, anchor="w")
        self.buyer_item_tree.grid(row=1, column=0, sticky="nsew")
        panes.add(items_box, weight=1)
        return view

    def _load_buyer_runs(self) -> None:
        store = self._current_store()
        if store is None:
            return
        scope = f"dongxiao:buyer-runs:{store.id}"
        if self.window.has_running_task(scope):
            return
        repository = self.buyer_flow_service.repository
        self.window.run_task(
            lambda _progress: repository.list_runs(store_id=int(store.id)),
            self._buyer_runs_loaded,
            lambda error: self.buyer_flow_status_var.set(
                f"买家流程读取失败：{error}"
            ),
            log_title="读取买家完整流程",
            task_scope=scope,
            store_app_id=str(store.app_id),
        )

    def _buyer_runs_loaded(self, runs) -> None:
        self._buyer_runs = list(runs)
        tree = getattr(self, "buyer_run_tree", None)
        if tree is None:
            return
        for item_id in tree.get_children():
            tree.delete(item_id)
        labels = {
            "draft": "草稿",
            "prepared": "已准备",
            "blocked_official_unsupported": "官方能力不支持",
            "waiting_user_handoff": "等待官方客户端操作",
            "waiting_order_id": "等待真实订单号",
            "paused": "已暂停",
            "cancelled": "已取消",
            "order_verified": "订单已核验",
            "failed": "失败",
            "unknown": "结果未知",
        }
        for run in runs:
            tree.insert(
                "",
                "end",
                iid=run.run_id,
                values=(
                    _date_time(run.created_at),
                    run.store_name,
                    run.item_count,
                    run.mode.value,
                    labels.get(run.status.value, run.status.value),
                    run.order_id,
                    run.status_reason,
                ),
            )
        self.buyer_flow_status_var.set(f"已读取 {len(runs)} 个买家流程")

    def _selected_buyer_run(self):
        tree = getattr(self, "buyer_run_tree", None)
        if tree is None or not tree.selection():
            return None
        run_id = str(tree.selection()[0])
        return next((run for run in self._buyer_runs if run.run_id == run_id), None)

    def _prepare_buyer_flow(self) -> None:
        store = self._current_store()
        account = self.buyer_flow_service.current_account()
        if store is None:
            messagebox.showwarning(
                "买家完整流程",
                "请先选择目标商家店铺。",
                parent=self,
            )
            return
        if account is None:
            messagebox.showwarning(
                "买家完整流程",
                "没有已验证的当前买家账号。真实买家登录能力未确认，"
                "请查看官方接口审计；可先添加 Mock 账号验证流程。",
                parent=self,
            )
            return
        try:
            count = int(self.buyer_count_var.get())
        except ValueError:
            count = 5
        manual_ids = [
            item
            for item in self.buyer_manual_ids_var.get().replace(
                ",",
                " ",
            ).replace(
                "\n",
                " ",
            ).split()
            if item
        ]
        mode = (
            BuyerFlowMode.MANUAL_SELECT
            if self.buyer_selection_mode_var.get() == "手动选择"
            else BuyerFlowMode.AUTO_SELECT
        )
        quantities = {product_id: 1 for product_id in manual_ids}
        self.window.run_task(
            lambda _progress: self.buyer_flow_service.prepare_run(
                store,
                buyer_account_id=account.buyer_account_id,
                mode=mode,
                requested_count=count,
                manual_product_ids=manual_ids,
                quantities=quantities,
                idempotency_key=f"buyer-flow-{uuid.uuid4().hex}",
                permission_checker=self._permission_checker,
                actor_id=self._actor_id(),
            ),
            self._buyer_flow_prepared,
            lambda error: messagebox.showerror(
                "准备买家流程失败",
                error,
                parent=self,
            ),
            log_title="准备买家完整流程",
            task_scope=f"dongxiao:buyer-prepare:{store.id}",
            store_app_id=str(store.app_id),
        )

    def _buyer_flow_prepared(self, payload) -> None:
        run, _items = payload
        self._load_buyer_runs()
        self.buyer_order_id_var.set(run.order_id)
        self.buyer_flow_status_var.set(
            f"流程已保存：{run.status.value}；{run.status_reason}"
        )

    def _render_selected_buyer_run(self) -> None:
        run = self._selected_buyer_run()
        if run is None:
            return
        items = self.buyer_flow_service.repository.list_items(run.run_id)
        self._buyer_run_items = list(items)
        tree = getattr(self, "buyer_item_tree", None)
        if tree is None:
            return
        for item_id in tree.get_children():
            tree.delete(item_id)
        for item in items:
            tree.insert(
                "",
                "end",
                iid=item.item_id,
                values=(
                    item.product_id,
                    item.title,
                    item.sku_id,
                    "；".join(f"{key}:{value}" for key, value in item.specs),
                    _money(item.unit_price_cents),
                    item.quantity,
                    item.stock,
                    _money(item.subtotal_cents),
                ),
            )
        self.buyer_order_id_var.set(run.order_id)
        preview = self.buyer_flow_service.preview(
            run.run_id,
            buyer_display_name=run.buyer_account_id,
        )
        self.buyer_flow_status_var.set(
            f"{run.status.value}；商品总额 {_money(preview.merchandise_total_cents)}；"
            f"运费/最终应付：{preview.notes[0] if preview.notes else '官方结算页确认'}；"
            f"{run.status_reason}"
        )

    def _buyer_flow_action(self, action: str) -> None:
        run = self._selected_buyer_run()
        if run is None:
            messagebox.showwarning(
                "买家完整流程",
                "请先选择一个买家流程。",
                parent=self,
            )
            return

        def work(_progress):
            if action == "mock":
                return self.buyer_flow_service.execute_mock_flow(
                    run.run_id,
                    permission_checker=self._permission_checker,
                    actor_id=self._actor_id(),
                )
            if action == "handoff":
                return self.buyer_flow_service.start_manual_handoff(
                    run.run_id,
                    permission_checker=self._permission_checker,
                    actor_id=self._actor_id(),
                )
            if action == "handoff_done":
                return self.buyer_flow_service.mark_handoff_complete(
                    run.run_id,
                    permission_checker=self._permission_checker,
                    actor_id=self._actor_id(),
                )
            return self.buyer_flow_service.set_run_status(
                run.run_id,
                action,
                permission_checker=self._permission_checker,
                actor_id=self._actor_id(),
            )

        self.window.run_task(
            work,
            lambda saved: (
                self._load_buyer_runs(),
                self.buyer_flow_status_var.set(
                    f"流程状态已更新：{saved.status.value}"
                ),
            ),
            lambda error: messagebox.showerror(
                "买家流程操作失败",
                error,
                parent=self,
            ),
            log_title=f"买家流程操作：{action}",
            task_scope=f"dongxiao:buyer-action:{action}:{run.run_id}",
            store_app_id=run.store_app_id,
        )

    def _verify_buyer_order(self) -> None:
        store = self._current_store()
        run = self._selected_buyer_run()
        order_id = self.buyer_order_id_var.get().strip()
        if store is None or run is None or not order_id:
            messagebox.showwarning(
                "核验待付款订单",
                "请选择流程并输入官方客户端生成的真实订单号。",
                parent=self,
            )
            return
        if not messagebox.askyesno(
            "核验官方订单",
            (
                "将通过当前店铺的官方商家订单接口读取该订单。\n"
                "只验证订单是否存在及是否待付款，不会付款或修改订单。\n\n"
                f"订单号：{order_id}\n是否继续？"
            ),
            parent=self,
        ):
            return
        self.window.run_task(
            lambda _progress: self.buyer_flow_service.verify_merchant_order(
                run.run_id,
                order_id=order_id,
                store=store,
                permission_checker=self._permission_checker,
                actor_id=self._actor_id(),
            ),
            lambda link: (
                self._load_buyer_runs(),
                self._load_orders(),
                self.buyer_flow_status_var.set(
                    f"官方订单核验：{link.status.value}；"
                    "买家手机端仍需用户本人确认。"
                ),
            ),
            lambda error: messagebox.showerror(
                "官方订单核验失败",
                error,
                parent=self,
            ),
            log_title="核验真实买家待付款订单",
            task_scope=f"dongxiao:buyer-order-verify:{run.run_id}",
            store_app_id=str(store.app_id),
        )

    def _build_cart_batch_view(self) -> ttk.Frame:
        view = ttk.Frame(self.section_host, style="Panel.TFrame")
        view.columnconfigure(0, weight=1)
        view.rowconfigure(5, weight=1)
        ttk.Label(
            view,
            text="批量加购",
            font=("Microsoft YaHei UI", 15, "bold"),
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(
            view,
            text=(
                "预检会重新核验商品状态、SKU、库存、起购/限购和价格。"
                "执行成功只以购物车服务端快照为依据。"
                "Mock 结果只在隔离数据库中有效，真实接口未开放时保持禁止。"
            ),
            style="Subtitle.TLabel",
            wraplength=850,
            justify="left",
        ).grid(row=1, column=0, sticky="w", pady=(6, 10))
        form = ttk.Frame(view, style="Panel.TFrame")
        form.grid(row=2, column=0, sticky="ew")
        ttk.Label(form, text="批次名称").grid(row=0, column=0, sticky="w")
        ttk.Entry(
            form,
            textvariable=self.cart_batch_name_var,
            width=22,
        ).grid(row=0, column=1, sticky="w", padx=(5, 14), pady=3)
        ttk.Label(form, text="商品池").grid(row=0, column=2, sticky="w")
        self.cart_pool_combo = ttk.Combobox(
            form,
            textvariable=self.pool_choice_var,
            state="readonly",
            width=28,
        )
        self.cart_pool_combo.grid(
            row=0,
            column=3,
            sticky="w",
            padx=(5, 14),
            pady=3,
        )
        ttk.Label(form, text="每批商品数").grid(row=0, column=4, sticky="w")
        ttk.Entry(
            form,
            textvariable=self.cart_products_per_batch_var,
            width=8,
        ).grid(row=0, column=5, sticky="w", padx=(5, 14), pady=3)
        ttk.Label(form, text="每个商品数量").grid(row=1, column=0, sticky="w")
        ttk.Entry(
            form,
            textvariable=self.cart_quantity_var,
            width=8,
        ).grid(row=1, column=1, sticky="w", padx=(5, 14), pady=3)
        ttk.Label(form, text="SKU 选择").grid(row=1, column=2, sticky="w")
        ttk.Combobox(
            form,
            textvariable=self.cart_sku_mode_var,
            values=("首个可售 SKU", "最低价 SKU", "指定 SKU"),
            state="readonly",
            width=18,
        ).grid(row=1, column=3, sticky="w", padx=(5, 14), pady=3)
        ttk.Label(form, text="指定 SKU").grid(row=1, column=4, sticky="w")
        ttk.Entry(
            form,
            textvariable=self.cart_specified_sku_var,
            width=18,
        ).grid(row=1, column=5, sticky="w", padx=(5, 14), pady=3)
        ttk.Label(form, text="缺货处理").grid(row=2, column=0, sticky="w")
        ttk.Combobox(
            form,
            textvariable=self.cart_stock_policy_var,
            values=("缺货跳过", "缺货失败"),
            state="readonly",
            width=12,
        ).grid(row=2, column=1, sticky="w", padx=(5, 14), pady=3)
        ttk.Label(form, text="速度").grid(row=2, column=2, sticky="w")
        ttk.Combobox(
            form,
            textvariable=self.cart_speed_var,
            values=("保守", "自适应", "较快", "固定配置"),
            state="readonly",
            width=12,
        ).grid(row=2, column=3, sticky="w", padx=(5, 14), pady=3)
        ttk.Label(form, text="间隔 ms").grid(row=2, column=4, sticky="w")
        ttk.Entry(
            form,
            textvariable=self.cart_interval_var,
            width=9,
        ).grid(row=2, column=5, sticky="w", padx=(5, 14), pady=3)
        ttk.Label(form, text="并发").grid(row=2, column=6, sticky="w")
        ttk.Entry(
            form,
            textvariable=self.cart_concurrency_var,
            width=6,
        ).grid(row=2, column=7, sticky="w", padx=(5, 0), pady=3)
        account_box = ttk.LabelFrame(
            view,
            text="分配执行账号（可多选）",
            padding=8,
        )
        account_box.grid(row=3, column=0, sticky="ew", pady=(8, 5))
        self.cart_account_listbox = tk.Listbox(
            account_box,
            selectmode="extended",
            height=4,
            exportselection=False,
            font=("Microsoft YaHei UI", 9),
        )
        self.cart_account_listbox.pack(fill="x")
        tools = ttk.Frame(view, style="Panel.TFrame")
        tools.grid(row=4, column=0, sticky="ew", pady=(4, 7))
        button(
            tools,
            "执行前预览",
            self._preview_cart_batch,
            "Primary",
        ).pack(side="left")
        button(
            tools,
            "创建批次",
            self._create_cart_batch,
        ).pack(side="left", padx=(6, 0))
        button(
            tools,
            "确认并执行",
            self._confirm_cart_batch,
        ).pack(side="left", padx=(6, 0))
        button(
            tools,
            "暂停",
            lambda: self._cart_batch_action("pause"),
        ).pack(side="left", padx=(6, 0))
        button(
            tools,
            "恢复",
            lambda: self._cart_batch_action("resume"),
        ).pack(side="left", padx=(6, 0))
        button(
            tools,
            "取消",
            lambda: self._cart_batch_action("cancel"),
        ).pack(side="left", padx=(6, 0))
        button(
            tools,
            "生成待付款草案",
            self._create_pending_order_drafts,
        ).pack(side="left", padx=(6, 0))
        ttk.Label(
            view,
            textvariable=self.cart_preview_var,
            style="Subtitle.TLabel",
        ).grid(row=5, column=0, sticky="nw")
        bottom = ttk.Frame(view, style="Panel.TFrame")
        bottom.grid(row=6, column=0, sticky="nsew", pady=(8, 0))
        bottom.columnconfigure(0, weight=1)
        bottom.columnconfigure(1, weight=1)
        bottom.columnconfigure(2, weight=1)
        bottom.rowconfigure(0, weight=1)
        self.cart_preview_tree = ttk.Treeview(
            bottom,
            columns=("product", "title", "sku", "price", "qty", "status", "reason"),
            show="headings",
        )
        for key, label, width in (
            ("product", "商品 ID", 125),
            ("title", "标题", 150),
            ("sku", "SKU", 110),
            ("price", "单价", 75),
            ("qty", "数量", 55),
            ("status", "预检", 70),
            ("reason", "原因", 200),
        ):
            self.cart_preview_tree.heading(key, text=label)
            self.cart_preview_tree.column(key, width=width, anchor="w")
        self.cart_preview_tree.grid(row=0, column=0, sticky="nsew")
        self.cart_batch_tree = ttk.Treeview(
            bottom,
            columns=("name", "status", "products", "accounts", "speed", "created"),
            show="headings",
            selectmode="browse",
        )
        for key, label, width in (
            ("name", "批次", 155),
            ("status", "状态", 90),
            ("products", "商品", 60),
            ("accounts", "账号", 55),
            ("speed", "速度", 80),
            ("created", "创建时间", 145),
        ):
            self.cart_batch_tree.heading(key, text=label)
            self.cart_batch_tree.column(key, width=width, anchor="w")
        self.cart_batch_tree.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        self.cart_batch_tree.bind(
            "<<TreeviewSelect>>",
            lambda _event: self._show_cart_assignments(),
        )
        self.cart_assignment_tree = ttk.Treeview(
            bottom,
            columns=("account", "status", "progress", "success", "failed", "unknown"),
            show="headings",
        )
        for key, label, width in (
            ("account", "执行账号", 135),
            ("status", "状态", 80),
            ("progress", "进度", 70),
            ("success", "成功", 55),
            ("failed", "失败", 55),
            ("unknown", "待核验", 65),
        ):
            self.cart_assignment_tree.heading(key, text=label)
            self.cart_assignment_tree.column(key, width=width, anchor="w")
        self.cart_assignment_tree.grid(
            row=0,
            column=2,
            sticky="nsew",
            padx=(8, 0),
        )
        return view

    def _build_pending_orders_view(self) -> ttk.Frame:
        view = ttk.Frame(self.section_host, style="Panel.TFrame")
        view.columnconfigure(0, weight=1)
        view.rowconfigure(2, weight=1)
        ttk.Label(
            view,
            text="待付款订单",
            font=("Microsoft YaHei UI", 15, "bold"),
        ).grid(row=0, column=0, sticky="w")
        toolbar = ttk.Frame(view, style="Panel.TFrame")
        toolbar.grid(row=1, column=0, sticky="ew", pady=(8, 8))
        button(toolbar, "刷新", self._load_pending_orders).pack(side="left")
        button(
            toolbar,
            "批准草案",
            lambda: self._decide_pending_order(True),
        ).pack(side="left", padx=(6, 0))
        button(
            toolbar,
            "取消草案",
            lambda: self._decide_pending_order(False),
        ).pack(side="left", padx=(6, 0))
        button(
            toolbar,
            "尝试创建官方订单",
            self._request_official_order_creation,
        ).pack(side="left", padx=(6, 0))
        ttk.Label(
            view,
            textvariable=self.pending_order_status_var,
            style="Subtitle.TLabel",
        ).grid(row=1, column=0, sticky="e", padx=(250, 0))
        panes = ttk.Panedwindow(view, orient="vertical")
        panes.grid(row=2, column=0, sticky="nsew")
        draft_box = ttk.Frame(panes, style="Panel.TFrame")
        draft_box.columnconfigure(0, weight=1)
        draft_box.rowconfigure(1, weight=1)
        ttk.Label(
            draft_box,
            text="本地待付款草案（人工审批，不代表平台真实订单）",
            style="Subtitle.TLabel",
        ).grid(row=0, column=0, sticky="w")
        self.pending_order_tree = ttk.Treeview(
            draft_box,
            columns=("store", "account", "status", "items", "amount", "reason", "created"),
            show="headings",
            selectmode="browse",
        )
        for key, label, width in (
            ("store", "店铺", 150),
            ("account", "执行账号", 145),
            ("status", "状态", 105),
            ("items", "商品数", 65),
            ("amount", "预计金额", 95),
            ("reason", "来源/原因", 270),
            ("created", "创建时间", 150),
        ):
            self.pending_order_tree.heading(key, text=label)
            self.pending_order_tree.column(key, width=width, anchor="w")
        self.pending_order_tree.grid(row=1, column=0, sticky="nsew")
        panes.add(draft_box, weight=2)
        result_box = ttk.Frame(panes, style="Panel.TFrame")
        result_box.columnconfigure(0, weight=1)
        result_box.rowconfigure(1, weight=1)
        ttk.Label(
            result_box,
            text="购物车核验明细",
            style="Subtitle.TLabel",
        ).grid(row=0, column=0, sticky="w")
        self.cart_result_tree = ttk.Treeview(
            result_box,
            columns=("product", "title", "sku", "qty", "price", "status", "source", "request"),
            show="headings",
        )
        for key, label, width in (
            ("product", "商品 ID", 135),
            ("title", "商品", 180),
            ("sku", "SKU", 120),
            ("qty", "数量", 60),
            ("price", "单价", 80),
            ("status", "结果", 90),
            ("source", "核验来源", 120),
            ("request", "请求编号", 220),
        ):
            self.cart_result_tree.heading(key, text=label)
            self.cart_result_tree.column(key, width=width, anchor="w")
        self.cart_result_tree.grid(row=1, column=0, sticky="nsew")
        panes.add(result_box, weight=1)
        return view

    def _load_cart_batches(self) -> None:
        store = self._current_store()
        if store is None:
            return
        scope = f"dongxiao:cart-batches:{store.id}"
        if self.window.has_running_task(scope):
            return
        repository = self.cart_service.repository

        def work(_progress):
            batches = repository.list_batches(store_id=int(store.id))
            for batch in list(batches):
                if batch.status in {
                    CartBatchStatus.QUEUED,
                    CartBatchStatus.RUNNING,
                    CartBatchStatus.PAUSED,
                    CartBatchStatus.UNKNOWN,
                }:
                    try:
                        self.cart_service.sync_progress(batch.batch_id)
                    except Exception:
                        continue
            return repository.list_batches(store_id=int(store.id))

        self.window.run_task(
            work,
            self._cart_batches_loaded,
            lambda error: self.window.set_status(
                f"批量加购批次读取失败：{error}",
                busy=False,
            ),
            log_title="读取批量加购批次",
            task_scope=scope,
            store_app_id=str(store.app_id),
        )

    def _cart_batches_loaded(self, batches) -> None:
        self._cart_batches = list(batches)
        tree = getattr(self, "cart_batch_tree", None)
        if tree is not None:
            for item_id in tree.get_children():
                tree.delete(item_id)
            labels = {
                CartBatchStatus.DRAFT: "草稿",
                CartBatchStatus.AWAITING_CONFIRMATION: "待确认",
                CartBatchStatus.QUEUED: "已排队",
                CartBatchStatus.RUNNING: "执行中",
                CartBatchStatus.PAUSED: "已暂停",
                CartBatchStatus.COMPLETED: "已完成",
                CartBatchStatus.PARTIALLY_FAILED: "部分失败",
                CartBatchStatus.FAILED: "全部失败",
                CartBatchStatus.CANCELLED: "已取消",
                CartBatchStatus.UNKNOWN: "待人工核验",
            }
            for batch in batches:
                tree.insert(
                    "",
                    "end",
                    iid=batch.batch_id,
                    values=(
                        batch.name,
                        labels.get(batch.status, batch.status.value),
                        batch.total_products,
                        len(
                            self.cart_service.repository.list_assignments(
                                batch.batch_id
                            )
                        ),
                        f"{batch.speed_mode.value} / {batch.interval_ms}ms",
                        _date_time(batch.created_at),
                    ),
                )
        self.cart_status_var.set(f"已读取 {len(batches)} 个批量加购批次")

    def _selected_cart_batch(self):
        tree = getattr(self, "cart_batch_tree", None)
        if tree is None:
            return None
        selection = tree.selection()
        if not selection:
            return None
        batch_id = str(selection[0])
        return next(
            (batch for batch in self._cart_batches if batch.batch_id == batch_id),
            None,
        )

    def _show_cart_assignments(self) -> None:
        batch = self._selected_cart_batch()
        tree = getattr(self, "cart_assignment_tree", None)
        if tree is None:
            return
        for item_id in tree.get_children():
            tree.delete(item_id)
        if batch is None:
            return
        labels = {
            "pending": "等待",
            "running": "执行中",
            "completed": "已完成",
            "failed": "失败",
            "cancelled": "已取消",
            "unknown": "待核验",
        }
        for assignment in self.cart_service.repository.list_assignments(
            batch.batch_id
        ):
            total = int(assignment["total_items"] or 0)
            succeeded = int(assignment["succeeded_items"] or 0)
            failed = int(assignment["failed_items"] or 0)
            skipped = int(assignment["skipped_items"] or 0)
            unknown = int(assignment["unknown_items"] or 0)
            tree.insert(
                "",
                "end",
                values=(
                    str(assignment["display_name"]),
                    labels.get(str(assignment["status"]), str(assignment["status"])),
                    f"{succeeded + failed + skipped + unknown}/{total}",
                    succeeded,
                    failed,
                    unknown,
                ),
            )

    def _selected_cart_account_ids(self) -> list[str]:
        listbox = getattr(self, "cart_account_listbox", None)
        if listbox is None:
            return []
        return [
            self._cart_accounts[int(index)].account_id
            for index in listbox.curselection()
            if 0 <= int(index) < len(self._cart_accounts)
        ]

    def _cart_sku_mode(self) -> CartSkuMode:
        return {
            "首个可售 SKU": CartSkuMode.FIRST_AVAILABLE,
            "最低价 SKU": CartSkuMode.LOWEST_PRICE,
            "指定 SKU": CartSkuMode.SPECIFIED,
        }[self.cart_sku_mode_var.get()]

    def _cart_stock_policy(self) -> OutOfStockPolicy:
        return (
            OutOfStockPolicy.SKIP
            if self.cart_stock_policy_var.get() == "缺货跳过"
            else OutOfStockPolicy.FAIL
        )

    def _cart_speed_mode(self) -> CartSpeedMode:
        return {
            "保守": CartSpeedMode.CONSERVATIVE,
            "自适应": CartSpeedMode.ADAPTIVE,
            "较快": CartSpeedMode.FAST,
            "固定配置": CartSpeedMode.CUSTOM,
        }[self.cart_speed_var.get()]

    def _preview_cart_batch(self) -> None:
        store = self._current_store()
        pool = self._current_pool()
        account_ids = self._selected_cart_account_ids()
        if store is None or pool is None:
            messagebox.showwarning(
                "批量加购预览",
                "请先选择店铺和商品池。",
                parent=self,
            )
            return
        if not account_ids:
            messagebox.showwarning(
                "批量加购预览",
                "请至少选择一个执行账号。",
                parent=self,
            )
            return
        accounts = [
            account
            for account in self._cart_accounts
            if account.account_id in set(account_ids)
        ]
        modes = {account.adapter_mode for account in accounts}
        if len(modes) != 1:
            messagebox.showwarning(
                "批量加购预览",
                "同一批次只能使用同一种授权来源的账号。",
                parent=self,
            )
            return
        adapter_mode = next(iter(modes))
        try:
            products_per_batch = int(self.cart_products_per_batch_var.get())
            quantity = int(self.cart_quantity_var.get())
            int(self.cart_interval_var.get())
            int(self.cart_concurrency_var.get())
        except ValueError:
            messagebox.showwarning(
                "批量加购预览",
                "每批数量、商品数量、间隔和并发必须为整数。",
                parent=self,
            )
            return

        def work(_progress):
            return self.cart_service.preview_batch(
                store,
                pool_id=pool.pool_id,
                quantity_per_product=quantity,
                sku_mode=self._cart_sku_mode(),
                specified_sku_id=self.cart_specified_sku_var.get(),
                out_of_stock_policy=self._cart_stock_policy(),
                products_per_batch=products_per_batch,
                account_ids=account_ids,
                adapter_mode=adapter_mode,
                permission_checker=self._permission_checker,
            )

        self.window.run_task(
            work,
            self._cart_preview_loaded,
            lambda error: messagebox.showerror(
                "批量加购预览失败",
                error,
                parent=self,
            ),
            log_title="预览桌面批量加购",
            task_scope=f"dongxiao:cart-preview:{store.id}",
            store_app_id=str(store.app_id),
        )

    def _cart_preview_loaded(self, preview) -> None:
        self._cart_preview = preview
        tree = getattr(self, "cart_preview_tree", None)
        if tree is not None:
            for item_id in tree.get_children():
                tree.delete(item_id)
            status_labels = {
                "ready": "可执行",
                "skipped": "将跳过",
                "failed": "预检失败",
            }
            for item in preview.items:
                tree.insert(
                    "",
                    "end",
                    values=(
                        item.product_id,
                        item.title,
                        item.sku_id,
                        _money(item.unit_price_cents),
                        item.quantity,
                        status_labels.get(item.status, item.status),
                        item.reason,
                    ),
                )
        capability = preview.capability
        self.cart_preview_var.set(
            f"可执行 {preview.ready_count}，跳过 {preview.skipped_count}，"
            f"失败 {preview.failed_count}，预计金额 {_money(preview.total_amount_cents)}；"
            f"接口状态：{capability.state.value}；{capability.reason}"
        )
        if capability.can_add_to_cart and capability.can_verify_cart:
            message = (
                f"预检完成：可执行 {preview.ready_count} 个商品。\n"
                f"跳过 {preview.skipped_count}，失败 {preview.failed_count}。\n"
                f"数据来源：{preview.adapter_mode.value}。\n\n"
                "确认创建这个批次吗？创建后仍需再次确认执行。"
            )
            if tree is not None and messagebox.askyesno(
                "批量加购预览",
                message,
                parent=self,
            ):
                self._create_cart_batch()
            return
        messagebox.showinfo(
            "官方购物车能力未开放",
            capability.reason,
            parent=self,
        )

    def _create_cart_batch(self) -> None:
        store = self._current_store()
        preview = self._cart_preview
        account_ids = self._selected_cart_account_ids()
        if store is None or preview is None or not account_ids:
            messagebox.showwarning(
                "创建加购批次",
                "请先完成预览并选择执行账号。",
                parent=self,
            )
            return
        if not preview.capability.can_add_to_cart:
            messagebox.showwarning(
                "创建加购批次",
                preview.capability.reason,
                parent=self,
            )
            return
        try:
            products_per_batch = int(self.cart_products_per_batch_var.get())
            quantity = int(self.cart_quantity_var.get())
            interval_ms = int(self.cart_interval_var.get())
            concurrency = int(self.cart_concurrency_var.get())
        except ValueError:
            messagebox.showwarning(
                "创建加购批次",
                "数字输入格式不正确。",
                parent=self,
            )
            return

        def work(_progress):
            return self.cart_service.create_batch(
                store,
                name=self.cart_batch_name_var.get(),
                idempotency_key=f"desktop-cart-{uuid.uuid4().hex}",
                preview=preview,
                quantity_per_product=quantity,
                products_per_batch=products_per_batch,
                sku_mode=self._cart_sku_mode(),
                specified_sku_id=self.cart_specified_sku_var.get(),
                out_of_stock_policy=self._cart_stock_policy(),
                speed_mode=self._cart_speed_mode(),
                interval_ms=interval_ms,
                concurrency=concurrency,
                account_ids=account_ids,
                permission_checker=self._permission_checker,
                actor_id=self._actor_id(),
            )

        self.window.run_task(
            work,
            self._cart_batch_created,
            lambda error: messagebox.showerror(
                "创建加购批次失败",
                error,
                parent=self,
            ),
            log_title="创建桌面批量加购批次",
            task_scope=f"dongxiao:cart-batch-create:{store.id}",
            store_app_id=str(store.app_id),
        )

    def _cart_batch_created(self, result) -> None:
        batch, created = result
        self.cart_preview_var.set(
            f"批次 {batch.name} 已{'创建' if created else '读取'}，"
            "请检查商品和账号后点击“确认并执行”。"
        )
        self._load_cart_batches()
        self.window.set_status("批量加购批次已保存", busy=False)

    def _confirm_cart_batch(self) -> None:
        store = self._current_store()
        batch = self._selected_cart_batch()
        if store is None or batch is None:
            messagebox.showwarning(
                "确认加购批次",
                "请先在右侧批次列表选择一个批次。",
                parent=self,
            )
            return
        if not messagebox.askyesno(
            "确认执行批量加购",
            (
                f"批次：{batch.name}\n"
                f"商品数量：{batch.total_products}\n"
                f"数据来源：{batch.adapter_mode.value}\n\n"
                "执行后，只有购物车服务端状态核验成功才记为成功。\n"
                "Mock 模式不会访问真实平台。\n\n是否继续？"
            ),
            parent=self,
        ):
            return

        def work(_progress):
            return self.cart_service.confirm_batch(
                store,
                batch.batch_id,
                permission_checker=self._permission_checker,
                actor_id=self._actor_id(),
            )

        self.window.run_task(
            work,
            lambda result: self._cart_batch_command_done(
                "批次已确认并加入后台任务"
            ),
            lambda error: messagebox.showerror(
                "确认加购批次失败",
                error,
                parent=self,
            ),
            log_title="确认并执行桌面批量加购",
            task_scope=f"dongxiao:cart-batch-confirm:{batch.batch_id}",
            store_app_id=str(store.app_id),
        )

    def _cart_batch_action(self, action: str) -> None:
        store = self._current_store()
        batch = self._selected_cart_batch()
        if store is None or batch is None:
            messagebox.showwarning(
                "批量加购",
                "请先选择一个批次。",
                parent=self,
            )
            return
        if action == "cancel" and not messagebox.askyesno(
            "取消加购批次",
            "取消只停止尚未完成的明细，不删除历史记录。是否继续？",
            parent=self,
        ):
            return
        method = {
            "pause": self.cart_service.pause_batch,
            "resume": self.cart_service.resume_batch,
            "cancel": self.cart_service.cancel_batch,
        }[action]

        def work(_progress):
            return method(
                store,
                batch.batch_id,
                actor_id=self._actor_id(),
            )

        self.window.run_task(
            work,
            lambda _result: self._cart_batch_command_done(
                {
                    "pause": "批次已暂停",
                    "resume": "批次已恢复",
                    "cancel": "批次已取消",
                }[action]
            ),
            lambda error: messagebox.showerror(
                "批量加购操作失败",
                error,
                parent=self,
            ),
            log_title=f"批量加购操作：{action}",
            task_scope=f"dongxiao:cart-batch-{action}:{batch.batch_id}",
            store_app_id=str(store.app_id),
        )

    def _cart_batch_command_done(self, message: str) -> None:
        self.cart_status_var.set(message)
        self._load_cart_batches()
        self.window.set_status(message, busy=False)

    def _create_pending_order_drafts(self) -> None:
        store = self._current_store()
        batch = self._selected_cart_batch()
        if store is None or batch is None:
            messagebox.showwarning(
                "待付款草案",
                "请先选择一个已完成的加购批次。",
                parent=self,
            )
            return
        self.window.run_task(
            lambda _progress: self.cart_service.create_pending_drafts(
                store,
                batch.batch_id,
                actor_id=self._actor_id(),
            ),
            lambda drafts: (
                self.pending_order_status_var.set(
                    f"已生成 {len(drafts)} 个待付款草案"
                ),
                self._load_pending_orders(),
            ),
            lambda error: messagebox.showerror(
                "生成待付款草案失败",
                error,
                parent=self,
            ),
            log_title="生成待付款订单草案",
            task_scope=f"dongxiao:pending-draft:{batch.batch_id}",
            store_app_id=str(store.app_id),
        )

    def _load_pending_orders(self) -> None:
        store = self._current_store()
        if store is None:
            return
        scope = f"dongxiao:pending-orders:{store.id}"
        if self.window.has_running_task(scope):
            return
        repository = self.cart_service.repository

        def work(_progress):
            return (
                repository.list_pending_drafts(store_id=int(store.id)),
                repository.list_results(store_id=int(store.id), limit=5000),
            )

        self.window.run_task(
            work,
            self._pending_orders_loaded,
            lambda error: self.window.set_status(
                f"待付款草案读取失败：{error}",
                busy=False,
            ),
            log_title="读取待付款订单草案",
            task_scope=scope,
            store_app_id=str(store.app_id),
        )

    def _pending_orders_loaded(self, payload) -> None:
        drafts, results = payload
        self._cart_pending_drafts = list(drafts)
        labels = {
            PendingOrderStatus.DRAFT: "草稿",
            PendingOrderStatus.AWAITING_APPROVAL: "待审批",
            PendingOrderStatus.APPROVED: "已批准（未创建订单）",
            PendingOrderStatus.CANCELLED: "已取消",
            PendingOrderStatus.VERIFIED: "真实订单已核验",
            PendingOrderStatus.OFFICIAL_CREATE_UNSUPPORTED: "官方创建不支持",
        }
        tree = getattr(self, "pending_order_tree", None)
        if tree is not None:
            for item_id in tree.get_children():
                tree.delete(item_id)
            for draft in drafts:
                tree.insert(
                    "",
                    "end",
                    iid=draft.draft_id,
                    values=(
                        draft.store_name,
                        draft.account_id,
                        labels.get(draft.status, draft.status.value),
                        draft.item_count,
                        _money(draft.total_amount_cents),
                        draft.reason,
                        _date_time(draft.created_at),
                    ),
                )
        result_tree = getattr(self, "cart_result_tree", None)
        if result_tree is not None:
            for item_id in result_tree.get_children():
                result_tree.delete(item_id)
            status_labels = {
                CartResultStatus.SUCCEEDED: "成功",
                CartResultStatus.FAILED: "失败",
                CartResultStatus.SKIPPED: "跳过",
                CartResultStatus.UNKNOWN: "待核验",
                CartResultStatus.PENDING: "等待",
            }
            for result in results:
                result_tree.insert(
                    "",
                    "end",
                    iid=result.result_id,
                    values=(
                        result.product_id,
                        result.product_title,
                        result.sku_id,
                        result.quantity,
                        _money(result.unit_price_cents),
                        status_labels.get(result.status, result.status.value),
                        result.verification_source.value,
                        result.request_id,
                    ),
                )
        self.pending_order_status_var.set(
            "待审批 "
            + str(
                sum(
                    item.status is PendingOrderStatus.AWAITING_APPROVAL
                    for item in drafts
                )
            )
            + "，购物车成功明细 "
            + str(
                sum(
                    item.status is CartResultStatus.SUCCEEDED
                    for item in results
                )
            )
        )

    def _selected_pending_order_draft(self):
        tree = getattr(self, "pending_order_tree", None)
        if tree is None:
            return None
        selection = tree.selection()
        if not selection:
            return None
        draft_id = str(selection[0])
        return next(
            (
                draft
                for draft in self._cart_pending_drafts
                if draft.draft_id == draft_id
            ),
            None,
        )

    def _decide_pending_order(self, approve: bool) -> None:
        store = self._current_store()
        draft = self._selected_pending_order_draft()
        if store is None or draft is None:
            messagebox.showwarning(
                "待付款草案",
                "请先选择一个待付款草案。",
                parent=self,
            )
            return
        action = "批准" if approve else "取消"
        if not messagebox.askyesno(
            f"{action}待付款草案",
            (
                f"店铺：{draft.store_name}\n"
                f"商品数：{draft.item_count}\n"
                f"预计金额：{_money(draft.total_amount_cents)}\n\n"
                "这是本地草案审批，不会创建平台真实订单，也不会付款。\n"
                f"确认{action}吗？"
            ),
            parent=self,
        ):
            return

        def work(_progress):
            return self.cart_service.decide_pending_draft(
                store,
                draft.draft_id,
                approve=approve,
                actor_id=self._actor_id(),
                permission_checker=self._permission_checker,
                reason=f"用户人工{action}本地待付款草案",
            )

        self.window.run_task(
            work,
            lambda _saved: (
                self.pending_order_status_var.set(f"草案已{action}"),
                self._load_pending_orders(),
            ),
            lambda error: messagebox.showerror(
                "待付款草案审批失败",
                error,
                parent=self,
            ),
            log_title=f"{action}待付款草案",
            task_scope=f"dongxiao:pending-decision:{draft.draft_id}",
            store_app_id=str(store.app_id),
        )

    def _request_official_order_creation(self) -> None:
        draft = self._selected_pending_order_draft()
        if draft is None:
            messagebox.showwarning(
                "创建官方订单",
                "请先选择一个待付款草案。",
                parent=self,
            )
            return
        messagebox.showinfo(
            "官方订单创建未开放",
            (
                "当前可确认的微信小店商家开放接口不支持由商家端创建"
                "买家待付款订单。\n\n"
                "本次不会向平台发送任何请求，也不会自动付款。\n"
                "请继续使用微信官方下单和支付流程。"
            ),
            parent=self,
        )

    def _build_task_view(self) -> ttk.Frame:
        view = ttk.Frame(self.section_host, style="Panel.TFrame")
        view.columnconfigure(0, weight=1)
        view.rowconfigure(3, weight=1)
        ttk.Label(
            view,
            text="任务中心",
            font=("Microsoft YaHei UI", 15, "bold"),
        ).grid(row=0, column=0, sticky="w")
        toolbar = ttk.Frame(view, style="Panel.TFrame")
        toolbar.grid(row=1, column=0, sticky="ew", pady=(10, 6))
        button(
            toolbar,
            "刷新",
            self._load_tasks,
        ).pack(side="left")
        button(
            toolbar,
            "从当前商品池建任务",
            self._create_pool_task,
            "Primary",
        ).pack(side="left", padx=5)
        button(toolbar, "暂停", self._pause_task).pack(side="left")
        button(toolbar, "恢复", self._resume_task).pack(
            side="left",
            padx=5,
        )
        button(toolbar, "取消", self._cancel_task).pack(side="left")
        button(toolbar, "重试失败项", self._retry_task).pack(
            side="left",
            padx=5,
        )
        button(toolbar, "任务详情", self._show_task_details).pack(
            side="left"
        )
        button(
            toolbar,
            "核验未知结果",
            self._verify_unknown_task,
        ).pack(side="left", padx=(5, 0))
        ttk.Label(
            view,
            textvariable=self.task_stats_var,
            style="Subtitle.TLabel",
        ).grid(row=2, column=0, sticky="w", pady=(0, 6))
        self.task_tree = ttk.Treeview(
            view,
            columns=(
                "created",
                "task_id",
                "type",
                "status",
                "progress",
                "error",
            ),
            show="headings",
            selectmode="browse",
        )
        for key, label, width in (
            ("created", "创建时间", 150),
            ("task_id", "任务 ID", 190),
            ("type", "类型", 180),
            ("status", "状态", 105),
            ("progress", "进度", 100),
            ("error", "最后错误", 260),
        ):
            self.task_tree.heading(key, text=label)
            self.task_tree.column(key, width=width, anchor="w")
        self.task_tree.grid(row=3, column=0, sticky="nsew")
        return view

    def _build_order_view(self) -> ttk.Frame:
        view = ttk.Frame(self.section_host, style="Panel.TFrame")
        view.columnconfigure(0, weight=1)
        view.rowconfigure(3, weight=1)
        ttk.Label(
            view,
            text="真实订单",
            font=("Microsoft YaHei UI", 15, "bold"),
        ).grid(row=0, column=0, sticky="w")
        toolbar = ttk.Frame(view, style="Panel.TFrame")
        toolbar.grid(row=1, column=0, sticky="ew", pady=(10, 6))
        ttk.Label(toolbar, text="开始").pack(side="left")
        ttk.Entry(
            toolbar,
            textvariable=self.order_start_var,
            width=12,
        ).pack(side="left", padx=(4, 10))
        ttk.Label(toolbar, text="结束").pack(side="left")
        ttk.Entry(
            toolbar,
            textvariable=self.order_end_var,
            width=12,
        ).pack(side="left", padx=(4, 10))
        button(
            toolbar,
            "同步真实订单",
            self._sync_orders,
            "Primary",
        ).pack(side="left")
        button(toolbar, "刷新本地", self._load_orders).pack(
            side="left",
            padx=5,
        )
        button(
            toolbar,
            "选中订单核验任务",
            self._create_order_task,
        ).pack(side="left")
        ttk.Label(toolbar, text="订单号").pack(side="left", padx=(8, 2))
        ttk.Entry(
            toolbar,
            textvariable=self.order_id_var,
            width=22,
        ).pack(side="left")
        button(
            toolbar,
            "按号核验",
            self._sync_order_by_id,
        ).pack(side="left", padx=(4, 0))
        button(
            toolbar,
            "状态历史",
            self._show_order_history,
        ).pack(side="left", padx=(5, 0))
        ttk.Label(
            view,
            textvariable=self.order_status_var,
            style="Subtitle.TLabel",
        ).grid(row=2, column=0, sticky="w", pady=(0, 6))
        self.order_tree = ttk.Treeview(
            view,
            columns=(
                "order_id",
                "status",
                "total",
                "items",
                "created",
                "updated",
                "synced",
            ),
            show="headings",
            selectmode="extended",
        )
        for key, label, width in (
            ("order_id", "订单号", 185),
            ("status", "状态", 95),
            ("total", "实付", 90),
            ("items", "商品项", 70),
            ("created", "创建时间", 135),
            ("updated", "更新时间", 135),
            ("synced", "最近核验", 135),
        ):
            self.order_tree.heading(key, text=label)
            self.order_tree.column(key, width=width, anchor="w")
        self.order_tree.grid(row=3, column=0, sticky="nsew")
        return view

    def _build_aftersale_view(self) -> ttk.Frame:
        view = ttk.Frame(self.section_host, style="Panel.TFrame")
        view.columnconfigure(0, weight=1)
        view.rowconfigure(4, weight=1)
        ttk.Label(
            view,
            text="售后工单",
            font=("Microsoft YaHei UI", 15, "bold"),
        ).grid(row=0, column=0, sticky="w")
        sync_bar = ttk.Frame(view, style="Panel.TFrame")
        sync_bar.grid(row=1, column=0, sticky="ew", pady=(10, 5))
        ttk.Label(sync_bar, text="开始").pack(side="left")
        ttk.Entry(
            sync_bar,
            textvariable=self.aftersale_start_var,
            width=12,
        ).pack(side="left", padx=(4, 10))
        ttk.Label(sync_bar, text="结束").pack(side="left")
        ttk.Entry(
            sync_bar,
            textvariable=self.aftersale_end_var,
            width=12,
        ).pack(side="left", padx=(4, 10))
        button(
            sync_bar,
            "同步真实售后",
            self._sync_aftersales,
            "Primary",
        ).pack(side="left")
        button(sync_bar, "刷新本地", self._load_aftersales).pack(
            side="left",
            padx=5,
        )
        button(
            sync_bar,
            "选中售后核验任务",
            self._create_aftersale_task,
        ).pack(side="left")
        workflow = ttk.Frame(view, style="Panel.TFrame")
        workflow.grid(row=2, column=0, sticky="ew", pady=5)
        ttk.Label(workflow, text="本地审批金额").pack(side="left")
        ttk.Entry(
            workflow,
            textvariable=self.aftersale_amount_var,
            width=12,
        ).pack(side="left", padx=(4, 8))
        ttk.Label(
            workflow,
            text=(
                "平台实际执行金额以官方售后申请金额为准；"
                "当前同意接口不支持部分金额"
            ),
            style="Subtitle.TLabel",
        ).pack(side="left", padx=(0, 8))
        button(
            workflow,
            "申请审批",
            self._request_aftersale_approval,
        ).pack(side="left")
        button(
            workflow,
            "审批通过",
            lambda: self._decide_aftersale_approval(True),
        ).pack(side="left", padx=5)
        button(
            workflow,
            "审批驳回",
            lambda: self._decide_aftersale_approval(False),
        ).pack(side="left")
        button(
            workflow,
            "审批记录",
            self._show_aftersale_approvals,
        ).pack(side="left", padx=5)
        button(
            workflow,
            "提交同意售后",
            lambda: self._submit_aftersale_decision("accept"),
        ).pack(side="left", padx=5)
        button(
            workflow,
            "提交拒绝售后",
            lambda: self._submit_aftersale_decision("reject"),
        ).pack(side="left")
        ttk.Label(
            view,
            textvariable=self.aftersale_status_var,
            style="Subtitle.TLabel",
        ).grid(row=3, column=0, sticky="w", pady=(0, 6))
        self.aftersale_tree = ttk.Treeview(
            view,
            columns=(
                "case_id",
                "order_id",
                "status",
                "platform_status",
                "requested",
                "approved",
                "paid",
                "remaining",
                "deadline",
                "synced",
            ),
            show="headings",
            selectmode="browse",
        )
        for key, label, width in (
            ("case_id", "售后单号", 150),
            ("order_id", "订单号", 150),
            ("status", "状态", 95),
            ("platform_status", "平台原始状态", 170),
            ("requested", "申请金额", 90),
            ("approved", "审批金额", 90),
            ("paid", "订单实付", 90),
            ("remaining", "剩余可退", 90),
            ("deadline", "处理截止", 130),
            ("synced", "最近同步", 130),
        ):
            self.aftersale_tree.heading(key, text=label)
            self.aftersale_tree.column(key, width=width, anchor="w")
        self.aftersale_tree.grid(row=4, column=0, sticky="nsew")
        return view

    def _build_real_ops_view(self) -> ttk.Frame:
        view = ttk.Frame(self.section_host, style="Panel.TFrame")
        view.columnconfigure(0, weight=1)
        view.rowconfigure(4, weight=1)
        ttk.Label(
            view,
            text="真实操作管理",
            font=("Microsoft YaHei UI", 15, "bold"),
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(
            view,
            textvariable=self.active_store_var,
            style="Subtitle.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(5, 0))
        ttk.Label(
            view,
            textvariable=self.platform_write_status_var,
            style="Subtitle.TLabel",
        ).grid(row=2, column=0, sticky="w", pady=(4, 8))

        actions = ttk.Frame(view, style="Panel.TFrame")
        actions.grid(row=3, column=0, sticky="ew", pady=(0, 8))
        self.real_ops_enable_button = button(
            actions,
            "开启当前店铺真实写操作",
            self._enable_platform_writes,
            "Primary",
        )
        self.real_ops_enable_button.pack(side="left", padx=(0, 6))
        button(
            actions,
            "关闭当前店铺真实写操作",
            self._disable_current_store_writes,
        ).pack(side="left", padx=(0, 6))
        button(
            actions,
            "紧急关闭全部",
            self._disable_platform_writes_now,
        ).pack(side="left", padx=(0, 12))
        button(
            actions,
            "进入商品管理",
            lambda: self._open_business_page("products"),
        ).pack(side="left", padx=(0, 6))
        button(
            actions,
            "进入上传商品",
            lambda: self._open_business_page("upload"),
        ).pack(side="left", padx=(0, 6))
        button(
            actions,
            "进入订单管理",
            lambda: self._open_business_page("order_management"),
        ).pack(side="left", padx=(0, 6))
        button(
            actions,
            "进入售后管理",
            lambda: self._open_business_page("operations"),
        ).pack(side="left")

        table = ttk.Treeview(
            view,
            columns=("feature", "source", "entry", "note"),
            show="headings",
            height=11,
        )
        for key, label, width in (
            ("feature", "真实业务能力", 190),
            ("source", "接口依据", 170),
            ("entry", "执行入口", 150),
            ("note", "当前说明", 540),
        ):
            table.heading(key, text=label)
            table.column(key, width=width, anchor="w")
        rows = (
            (
                "桌面批量加购（业务编排）",
                "澜琅本地服务 + 购物车服务端核验协议",
                "批量加购 / 多账号任务",
                "商品池、SKU、库存、限购、价格、幂等、暂停恢复、"
                "逐账号进度与购物车回查已接入统一任务引擎",
            ),
            (
                "真实买家加购与购物车读取",
                "未确认可授权的官方买家接口",
                "批量加购",
                "未发现可确认的微信小店官方开放接口；"
                "真实账号保持等待授权，禁止以私有网页接口冒充成功",
            ),
            (
                "买家待付款订单创建",
                "未确认可授权的官方买家下单接口",
                "待付款订单",
                "支持本地草案、人工审批和真实订单核验；"
                "不会由商家端绕过微信官方下单与支付流程",
            ),
            (
                "商品新增与编辑",
                "微信小店官方商品 API",
                "商品管理 / 上传商品",
                "由现有业务页面执行；受当前店铺写开关和人工确认保护",
            ),
            (
                "价格、库存、上下架",
                "微信小店官方商品 API",
                "商品管理",
                "批量操作沿用现有逐项结果和操作日志",
            ),
            (
                "订单备注、改价、地址、物流",
                "微信小店官方订单 API",
                "订单管理",
                "订单操作在现有订单管理页面执行；不得修改普通客户测试订单",
            ),
            (
                "售后同意与拒绝",
                "微信小店官方售后 API",
                "售后工单",
                "必须有通过审批、金额一致、人工确认和写后回查",
            ),
            (
                "退款",
                "官方同意售后触发",
                "售后工单",
                "官方当前接口不支持指定部分退款金额，以申请金额为准",
            ),
            (
                "售后协商、补发、换货",
                "客户端方法存在，动销统一工作流未完成",
                "—",
                "不在统一动销任务中开放，避免伪装为已完成能力",
            ),
        )
        for row in rows:
            table.insert("", "end", values=row)
        table.grid(row=4, column=0, sticky="nsew")
        return view

    def _open_business_page(self, page_key: str) -> None:
        show_page = getattr(self.window, "show_page", None)
        if not callable(show_page):
            messagebox.showerror(
                "真实操作管理",
                "主窗口页面导航不可用。",
                parent=self,
            )
            return
        show_page(page_key)

    def _build_audit_view(self) -> ttk.Frame:
        view = ttk.Frame(self.section_host, style="Panel.TFrame")
        view.columnconfigure(0, weight=1)
        view.rowconfigure(3, weight=1)
        ttk.Label(
            view,
            text="执行日志与审计",
            font=("Microsoft YaHei UI", 15, "bold"),
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(
            view,
            text=(
                "展示任务、审批、售后和订单操作审计。"
                "敏感字段及凭证不会写入普通日志。"
            ),
            style="Subtitle.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(6, 6))
        self.audit_tree = ttk.Treeview(
            view,
            columns=("time", "action", "entity", "result", "detail"),
            show="headings",
            selectmode="browse",
        )
        for key, label, width in (
            ("time", "时间", 145),
            ("action", "动作", 150),
            ("entity", "对象", 210),
            ("result", "结果", 80),
            ("detail", "说明", 320),
        ):
            self.audit_tree.heading(key, text=label)
            self.audit_tree.column(key, width=width, anchor="w")
        self.audit_tree.grid(row=3, column=0, sticky="nsew")
        ttk.Label(
            view,
            textvariable=self.audit_status_var,
            style="Subtitle.TLabel",
        ).grid(row=2, column=0, sticky="w", pady=(0, 6))
        return view

    def _build_preflight_view(self) -> ttk.Frame:
        view = ttk.Frame(self.section_host, style="Panel.TFrame")
        view.columnconfigure(0, weight=1)
        view.rowconfigure(5, weight=1)
        view.rowconfigure(8, weight=1)

        heading = ttk.Frame(view, style="Panel.TFrame")
        heading.grid(row=0, column=0, sticky="ew")
        heading.columnconfigure(0, weight=1)
        ttk.Label(
            heading,
            text="实盘前检查",
            font=("Microsoft YaHei UI", 15, "bold"),
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(
            heading,
            textvariable=self.platform_write_status_var,
            style="Subtitle.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(5, 0))

        actions = ttk.Frame(view, style="Panel.TFrame")
        actions.grid(row=1, column=0, sticky="ew", pady=(12, 8))
        self.preflight_local_button = button(
            actions,
            "运行本地检查",
            self._run_preflight_local,
            "Primary",
        )
        self.preflight_local_button.pack(side="left", padx=(0, 6))
        self.preflight_platform_button = button(
            actions,
            "手动真实只读检查",
            self._run_preflight_platform,
        )
        self.preflight_platform_button.pack(side="left", padx=(0, 6))
        self.preflight_report_button = button(
            actions,
            "保存中文报告",
            self._save_preflight_report,
        )
        self.preflight_report_button.pack(side="left", padx=(0, 6))
        self.enable_platform_write_button = button(
            actions,
            "开启当前店铺真实写操作",
            self._enable_platform_writes,
        )
        self.enable_platform_write_button.pack(side="left")

        ttk.Label(
            view,
            text=(
                "真实只读检查只会在本次点击、二次确认并具备权限后执行；"
                "不会同意售后、拒绝售后、退款或修改平台数据。"
            ),
            style="Subtitle.TLabel",
            wraplength=900,
            justify="left",
        ).grid(row=2, column=0, sticky="w", pady=(0, 8))

        ttk.Label(
            view,
            textvariable=self.preflight_status_var,
            style="Subtitle.TLabel",
        ).grid(row=3, column=0, sticky="w", pady=(0, 5))

        self.preflight_tree = ttk.Treeview(
            view,
            columns=("state", "check", "message"),
            show="headings",
            height=9,
        )
        for key, label, width in (
            ("state", "状态", 110),
            ("check", "检查项", 150),
            ("message", "说明", 620),
        ):
            self.preflight_tree.heading(key, text=label)
            self.preflight_tree.column(key, width=width, anchor="w")
        self.preflight_tree.grid(row=5, column=0, sticky="nsew")

        ttk.Label(
            view,
            text="最近 API 调用（路径已去除查询参数和凭据）",
            style="Subtitle.TLabel",
        ).grid(row=6, column=0, sticky="w", pady=(10, 5))
        api_actions = ttk.Frame(view, style="Panel.TFrame")
        api_actions.grid(row=7, column=0, sticky="ew", pady=(0, 4))
        button(
            api_actions,
            "刷新调用记录",
            self._load_api_calls,
        ).pack(side="left")
        self.api_call_tree = ttk.Treeview(
            view,
            columns=(
                "time",
                "method",
                "path",
                "outcome",
                "request_id",
                "duration",
            ),
            show="headings",
            height=7,
        )
        for key, label, width in (
            ("time", "时间", 145),
            ("method", "方法", 65),
            ("path", "路径", 300),
            ("outcome", "结果", 105),
            ("request_id", "请求 ID", 235),
            ("duration", "耗时", 75),
        ):
            self.api_call_tree.heading(key, text=label)
            self.api_call_tree.column(key, width=width, anchor="w")
        self.api_call_tree.grid(row=8, column=0, sticky="nsew")
        self._update_platform_write_status()
        return view

    def _build_placeholder(self, key: str) -> ttk.Frame:
        view = ttk.Frame(self.section_host, style="Panel.TFrame")
        section = next(
            item for item in DONGXIAO_SECTIONS if item.key == key
        )
        ttk.Label(
            view,
            text=section.label,
            font=("Microsoft YaHei UI", 15, "bold"),
        ).pack(anchor="w")
        ttk.Label(
            view,
            text=section.description,
            style="Subtitle.TLabel",
            wraplength=760,
            justify="left",
        ).pack(anchor="w", pady=(6, 14))
        ttk.Label(
            view,
            text="该模块将在后续批准阶段接入；当前没有可执行按钮。",
            style="Subtitle.TLabel",
        ).pack(anchor="w")
        return view

    def show_section(self, key: str) -> None:
        section = next(
            (item for item in DONGXIAO_SECTIONS if item.key == key),
            DONGXIAO_SECTIONS[0],
        )
        target = section.key
        target = target if target in self.section_views else "overview"
        self._active_section = section.key
        self.section_title_var.set(section.label)
        self.section_description_var.set(section.description)
        self.section_stage_var.set(
            "第 5 阶段：实盘候选、接口审计、资金安全与后台恢复。"
        )
        for view_key, view in self.section_views.items():
            view.grid_remove()
        self.section_views[target].grid()
        for section_key, nav_button in self.section_buttons.items():
            selected = section_key == section.key
            nav_button.configure(
                bg=SOFT_TEAL if selected else PANEL,
                fg=BLUE if selected else TEXT,
            )
        if section.key == "products":
            self._apply_filters()
        elif section.key == "pools":
            self._load_pools()
        elif section.key == "tasks":
            self._load_tasks()
        elif section.key == "accounts":
            self._load_cart_accounts()
            self._load_buyer_accounts()
        elif section.key == "buyer_flow":
            self._load_buyer_accounts()
            self._load_buyer_runs()
        elif section.key == "cart_batch":
            self._load_cart_accounts()
            self._load_cart_batches()
        elif section.key == "pending_orders":
            self._load_pending_orders()
        elif section.key == "orders":
            self._load_orders()
        elif section.key == "aftersales":
            self._load_aftersales()
        elif section.key == "audit":
            self._load_audit()
        elif section.key == "real_ops":
            self._update_platform_write_status()
        elif section.key == "preflight":
            self._update_platform_write_status()
            self._load_api_calls()

    def refresh(self) -> None:
        self._load_stores()

    def _task_activity_tick(self) -> None:
        if not self.winfo_exists():
            return
        store = self._current_store()
        scope = (
            f"dongxiao:tasks-load:{store.id}"
            if store is not None
            else ""
        )
        if (
            self._active_section in {
                "tasks",
                "cart_batch",
                "pending_orders",
                "accounts",
                "buyer_flow",
            }
            and scope
            and not self.window.has_running_task(scope)
        ):
            if self._active_section == "cart_batch":
                self._refresh_cart_batches()
            elif self._active_section == "pending_orders":
                self._load_pending_orders()
            elif self._active_section == "accounts":
                self._load_cart_accounts()
                self._load_buyer_accounts()
            elif self._active_section == "buyer_flow":
                self._load_buyer_accounts()
                self._load_buyer_runs()
            else:
                self._load_tasks()
        self.after(2000, self._task_activity_tick)

    def _current_store(self):
        label = self.store_var.get()
        return next(
            (
                store
                for store in self._stores
                if f"{store.name} · {store.app_id}" == label
            ),
            None,
        )

    def _current_pool(self):
        label = self.pool_choice_var.get()
        return next(
            (
                pool
                for pool in self._pools
                if self._pool_label(pool) == label
            ),
            None,
        )

    @staticmethod
    def _pool_label(pool) -> str:
        group_name = pool.group_name or "未分组"
        return f"{pool.name} · {group_name} · {pool.pool_id[:8]}"

    def _load_stores(self) -> None:
        try:
            stores = self.db.list_stores()
        except Exception as exc:
            self._stores_failed(str(exc))
            return
        self._stores_loaded(stores)

    def _stores_loaded(self, stores: list[Any]) -> None:
        self._stores = list(stores)
        labels = [f"{store.name} · {store.app_id}" for store in stores]
        self.store_combo.configure(values=labels)
        if labels and self.store_var.get() not in labels:
            target_store = next(
                (
                    store
                    for store in stores
                    if str(store.name).strip()
                    == self.buyer_flow_service.TARGET_STORE_NAME
                ),
                stores[0],
            )
            self.store_var.set(
                f"{target_store.name} · {target_store.app_id}"
            )
        if not labels:
            self.store_var.set("")
        self.store_count_var.set(f"{len(labels)} 家店铺")
        if labels:
            self.active_store_var.set(f"当前店铺：{labels[0]}")
        else:
            self.active_store_var.set("当前店铺：尚未添加店铺")
        for row in self.store_tree.get_children():
            self.store_tree.delete(row)
        for store in stores:
            self.store_tree.insert(
                "",
                "end",
                values=(store.name, store.app_id, store.web_status),
            )
        if labels:
            self._store_changed()
        self.window.set_status(
            f"动销中心已读取 {len(labels)} 家店铺",
            busy=False,
        )

    def _stores_failed(self, error: str) -> None:
        messagebox.showerror("读取店铺失败", error, parent=self)

    def _store_changed(self) -> None:
        store = self._current_store()
        if store is None:
            return
        self._update_platform_write_status()
        self.active_store_var.set(f"当前店铺：{store.name}")
        self._page = 1
        self._load_products()
        self._load_pools()
        self._load_cart_accounts()
        self._load_buyer_accounts()
        self._load_cart_batches()
        self._load_overview_stats()

    def _load_overview_stats(self) -> None:
        store = self._current_store()
        if store is None:
            return

        def work(_progress):
            product_page = self.repository.query_products(
                store.id,
                ProductFilter(page_size=1),
            )
            return (
                len(self._stores),
                product_page.total,
                len(self.pool_service.list_pools(store)),
                self.task_service.statistics(store),
            )

        def done(result) -> None:
            store_count, products, pools, task_stats = result
            self.overview_stats_vars["stores"].set(str(store_count))
            self.overview_stats_vars["products"].set(str(products))
            self.overview_stats_vars["pools"].set(str(pools))
            self.overview_stats_vars["tasks"].set(
                f"{task_stats.running}/{task_stats.unknown}"
            )

        self.window.run_task(
            work,
            done,
            lambda _error: None,
            log_title="读取动销中心统计",
            task_scope=f"dongxiao:overview:{store.id}",
        )

    def _parse_optional_money(self, value: str, label: str) -> int | None:
        text = value.strip()
        if not text:
            return None
        try:
            return parse_yuan_to_cents(text)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{label}格式不正确：{exc}") from exc

    def _parse_optional_int(self, value: str, label: str) -> int | None:
        text = value.strip()
        if not text:
            return None
        try:
            number = int(text)
        except ValueError as exc:
            raise ValueError(f"{label}必须是整数") from exc
        if number < 0:
            raise ValueError(f"{label}不能小于 0")
        return number

    @staticmethod
    def _parse_optional_date(
        value: str,
        label: str,
        *,
        end: bool,
    ) -> datetime | None:
        text = value.strip()
        if not text:
            return None
        try:
            parsed = date.fromisoformat(text)
        except ValueError as exc:
            raise ValueError(f"{label}必须使用 YYYY-MM-DD") from exc
        return _day_end(parsed) if end else _day_start(parsed)

    def _build_filter(self) -> ProductFilter:
        state_map = {
            "全部状态": (),
            "上架": (ProductState.ACTIVE,),
            "下架": (ProductState.INACTIVE,),
            "未知": (ProductState.UNKNOWN,),
        }
        product_ids = tuple(
            item
            for item in self.filter_product_ids_var.get().replace(
                ",",
                " ",
            ).split()
            if item
        )
        try:
            page_size = int(self.page_size_var.get())
        except ValueError:
            page_size = 50
        sales_max = self._parse_optional_int(
            self.filter_sales_max_var.get(),
            "最高销量",
        )
        if self.filter_low_sales_var.get() and sales_max is None:
            sales_max = 10
        listed_from = self._parse_optional_date(
            self.filter_listed_from_var.get(),
            "上架起始日期",
            end=False,
        )
        if self.filter_new_7d_var.get() and listed_from is None:
            listed_from = datetime.now(UTC) - timedelta(days=7)
        return ProductFilter(
            keyword=self.filter_keyword_var.get(),
            title_exact=bool(self.filter_title_exact_var.get()),
            states=state_map.get(self.filter_state_var.get(), ()),
            product_ids=product_ids,
            min_price_cents=self._parse_optional_money(
                self.filter_min_price_var.get(),
                "最低价",
            ),
            max_price_cents=self._parse_optional_money(
                self.filter_max_price_var.get(),
                "最高价",
            ),
            zero_sales=bool(self.filter_zero_sales_var.get()),
            sales_min=self._parse_optional_int(
                self.filter_sales_min_var.get(),
                "最低销量",
            ),
            sales_max=sales_max,
            listed_from=listed_from,
            listed_to=self._parse_optional_date(
                self.filter_listed_to_var.get(),
                "上架结束日期",
                end=True,
            ),
            page=self._page,
            page_size=page_size,
        )

    def _apply_filters(self) -> None:
        store = self._current_store()
        if store is None:
            self.product_page_var.set("请先添加或选择店铺")
            return
        try:
            filters = self._build_filter()
        except ValueError as exc:
            messagebox.showwarning("筛选条件错误", str(exc), parent=self)
            return
        self.apply_filter_button.configure(state="disabled")

        def done(page: ProductPage) -> None:
            self.apply_filter_button.configure(state="normal")
            self._render_products(page)

        def failed(error: str) -> None:
            self.apply_filter_button.configure(state="normal")
            messagebox.showerror("商品筛选失败", error, parent=self)

        self.window.run_task(
            lambda _progress: self.cached_catalog_service.query(
                store.id,
                filters,
            ),
            done,
            failed,
            log_title="筛选动销中心商品",
            task_scope=f"dongxiao:filter:{store.id}",
        )

    def _render_products(self, page: ProductPage) -> None:
        for row in self.product_tree.get_children():
            self.product_tree.delete(row)
        self._products = list(page.items)
        self._page = page.page
        self._page_count = max(1, page.page_count)
        for product in page.items:
            source = FRESHNESS_LABELS[
                classify_catalog_freshness(product)
            ]
            self.product_tree.insert(
                "",
                "end",
                iid=product.product_id,
                values=(
                    product.product_id,
                    product.title,
                    STATE_LABELS.get(product.state, "未知"),
                    _money(product.min_price_cents),
                    product.stock_total,
                    (
                        product.sales_count
                        if product.trusted_sales
                        else "—"
                    ),
                    (
                        _date_time(product.listing_time)
                        if product.trusted_listing_time
                        else "—"
                    ),
                    source,
                    _date_time(product.last_synced_at),
                ),
            )
        unavailable = []
        if "sales" in page.unavailable_fields:
            unavailable.append("销量")
        if "listing_time" in page.unavailable_fields:
            unavailable.append("上架时间")
        suffix = (
            f"；缺少可信字段：{'、'.join(unavailable)}"
            if unavailable
            else ""
        )
        self.product_page_var.set(
            f"第 {page.page}/{self._page_count} 页 · "
            f"共 {page.total} 条{suffix}"
        )
        self.source_status_var.set(
            "当前列表：本地缓存（非实时）；原始来源：" + (
                "、".join(
                    SOURCE_LABELS.get(
                        DataSource(item),
                        item,
                    )
                    for item in page.source_summary
                )
                or "尚未同步"
            )
        )
        self.product_tree.selection_clear()
        self.window.set_status(
            f"商品筛选完成：第 {page.page}/{self._page_count} 页，"
            f"共 {page.total} 条",
            busy=False,
        )

    def _reset_filters(self) -> None:
        for variable in (
            self.filter_keyword_var,
            self.filter_min_price_var,
            self.filter_max_price_var,
            self.filter_sales_min_var,
            self.filter_sales_max_var,
            self.filter_listed_from_var,
            self.filter_listed_to_var,
            self.filter_product_ids_var,
        ):
            variable.set("")
        self.filter_state_var.set("全部状态")
        self.filter_title_exact_var.set(False)
        self.filter_new_7d_var.set(False)
        self.filter_low_sales_var.set(False)
        self.filter_zero_sales_var.set(False)
        self._page = 1
        self._apply_filters()

    def _previous_page(self) -> None:
        if self._page <= 1:
            return
        self._page -= 1
        self._apply_filters()

    def _next_page(self) -> None:
        if self._page >= self._page_count:
            return
        self._page += 1
        self._apply_filters()

    def _select_all_products(self) -> None:
        rows = self.product_tree.get_children()
        if rows:
            self.product_tree.selection_set(rows)

    def _sync_products(self) -> None:
        store = self._current_store()
        if store is None:
            messagebox.showinfo("同步商品", "请先选择店铺。", parent=self)
            return
        sync_scope = f"dongxiao:sync-products:{store.id}"
        if self.window.has_running_task(sync_scope):
            return
        self.sync_products_button.configure(state="disabled")
        self.source_status_var.set("数据来源：官方 API · 正在同步")

        def progress(message: str, current: int, total: int) -> None:
            self.window.set_status(message, busy=True)
            self.source_status_var.set(
                f"官方 API 同步中：{current}/{total or '…'}"
            )

        def done(result) -> None:
            self.sync_products_button.configure(state="normal")
            current_store = self._current_store()
            if current_store is None or current_store.id != store.id:
                self.window.set_status(
                    f"{store.name} 的后台商品同步已完成；"
                    "当前已切换店铺，未覆盖当前页面数据。",
                    busy=False,
                )
                self._load_products()
                return
            sync_state = (
                "完整同步"
                if result.complete
                else "部分同步（未清理缺失商品）"
            )
            self.source_status_var.set(
                f"数据来源：官方 API · {sync_state} · "
                f"最近同步 {result.last_synced_at}"
            )
            message = (
                f"发现 {result.discovered} 件，保存 {result.products_saved} 件"
            )
            if result.failed:
                message += f"，失败 {result.failed} 件"
            if result.reconciled_products or result.reconciled_skus:
                message += (
                    f"，标记目录缺失 {result.reconciled_products} 件商品、"
                    f"{result.reconciled_skus} 个 SKU"
                )
            self.window.set_status(message)
            if result.failed or result.errors:
                messagebox.showwarning(
                    "商品同步部分完成",
                    message
                    + "\n\n旧商品和 SKU 未被网络错误删除。"
                    + (
                        "\n" + "\n".join(result.errors[:10])
                        if result.errors
                        else ""
                    ),
                    parent=self,
                )
            elif result.discovered == 0:
                messagebox.showinfo(
                    "同步完成",
                    "当前店铺没有可同步商品。",
                    parent=self,
                )
            self._load_products()

        def failed(error: str) -> None:
            self.sync_products_button.configure(state="normal")
            self.source_status_var.set(
                "官方 API 同步失败；可查看本地缓存（非实时）"
            )
            messagebox.showerror("同步商品失败", error, parent=self)

        started = self.window.run_task(
            lambda progress_cb: self.official_catalog_service.sync_store(
                store,
                progress=progress_cb,
            ),
            done,
            failed,
            progress=progress,
            log_title="同步动销中心商品",
            task_scope=sync_scope,
        )
        if not started:
            self.sync_products_button.configure(state="normal")

    def _show_cached_products(self) -> None:
        self.source_status_var.set(
            "当前列表：本地缓存（非实时），不是官方实时数据"
        )
        self._apply_filters()

    def _load_products(self) -> None:
        self._apply_filters()

    def _open_import_dialog(self) -> None:
        store = self._current_store()
        if store is None:
            messagebox.showinfo("批量导入", "请先选择店铺。", parent=self)
            return
        ProductImportDialog(
            self,
            self.window,
            self.import_service,
            store,
            on_saved=self._apply_filters,
        )

    def _selected_product_ids(self) -> list[str]:
        return [
            str(item)
            for item in self.product_tree.selection()
            if str(item)
        ]

    def _add_selected_to_pool(self) -> None:
        store = self._current_store()
        pool = self._current_pool()
        product_ids = self._selected_product_ids()
        if store is None or pool is None:
            messagebox.showinfo(
                "加入商品池",
                "请先选择店铺和商品池。",
                parent=self,
            )
            return
        if not product_ids:
            messagebox.showinfo(
                "加入商品池",
                "请先在商品表中选择商品。",
                parent=self,
            )
            return
        entries = [(product_id, "") for product_id in product_ids]
        self.window.run_task(
            lambda _progress: self.pool_service.add_products(
                store,
                pool.pool_id,
                entries,
            ),
            lambda result: self._pool_added(result, pool.pool_id),
            lambda error: messagebox.showerror(
                "加入商品池失败",
                error,
                parent=self,
            ),
            log_title="加入动销商品池",
            task_scope=f"dongxiao:pool-add:{pool.pool_id}",
        )

    def _pool_added(self, result: dict[str, int], pool_id: str) -> None:
        messagebox.showinfo(
            "加入商品池",
            f"新增 {result['added']} 条，跳过重复或缺失 {result['skipped']} 条。",
            parent=self,
        )
        if self._current_pool() and self._current_pool().pool_id == pool_id:
            self._load_pool_details()

    def _load_pools(self) -> None:
        store = self._current_store()
        if store is None:
            return

        def done(pools) -> None:
            self._pools = list(pools)
            labels = [self._pool_label(pool) for pool in pools]
            self.pool_combo.configure(values=labels)
            if hasattr(self, "cart_pool_combo"):
                self.cart_pool_combo.configure(values=labels)
            current = self.pool_choice_var.get()
            if current not in labels:
                self.pool_choice_var.set(labels[0] if labels else "")
            if labels:
                self._load_pool_details()
            else:
                self.pool_stats_var.set("当前店铺还没有商品池")
                self._clear_pool_tree()

        self.window.run_task(
            lambda _progress: self.pool_service.list_pools(store),
            done,
            lambda error: messagebox.showerror(
                "读取商品池失败",
                error,
                parent=self,
            ),
            log_title="读取动销商品池",
            task_scope=f"dongxiao:pools:{store.id}",
        )

    def _clear_pool_tree(self) -> None:
        for row in self.pool_tree.get_children():
            self.pool_tree.delete(row)

    def _load_pool_details(self) -> None:
        store = self._current_store()
        pool = self._current_pool()
        if store is None or pool is None:
            self._clear_pool_tree()
            return

        def work(_progress):
            return (
                self.pool_service.details(store, pool.pool_id),
                self.pool_service.stats(store, pool.pool_id),
            )

        self.window.run_task(
            work,
            self._render_pool_details,
            lambda error: messagebox.showerror(
                "读取商品池明细失败",
                error,
                parent=self,
            ),
            log_title="读取商品池明细",
            task_scope=f"dongxiao:pool-details:{pool.pool_id}",
        )

    def _render_pool_details(self, result) -> None:
        items, stats = result
        self._pool_items = list(items)
        self._clear_pool_tree()
        for row in items:
            self.pool_tree.insert(
                "",
                "end",
                iid=str(row["item_id"]),
                values=(
                    row["product_id"],
                    row["sku_id"] or "商品级",
                    row["position"],
                    row["created_at"],
                ),
            )
        self.pool_stats_var.set(
            f"明细 {stats['item_count']} 条 · "
            f"商品 {stats['product_count']} 个 · "
            f"SKU 级 {stats['sku_level_count']} 条 · "
            f"商品级 {stats['product_level_count']} 条"
        )
        self.window.set_status(
            f"商品池明细已读取：{stats['item_count']} 条",
            busy=False,
        )

    def _pool_name_prompt(
        self,
        title: str,
        initial_name: str = "",
        initial_group: str = "",
    ) -> tuple[str, str] | None:
        name = simpledialog.askstring(
            title,
            "商品池名称：",
            initialvalue=initial_name,
            parent=self,
        )
        if name is None:
            return None
        group = simpledialog.askstring(
            title,
            "商品池分组（可留空）：",
            initialvalue=initial_group,
            parent=self,
        )
        if group is None:
            return None
        return name.strip(), group.strip()

    def _create_pool(self) -> None:
        store = self._current_store()
        if store is None:
            messagebox.showinfo("新建商品池", "请先选择店铺。", parent=self)
            return
        values = self._pool_name_prompt("新建商品池")
        if values is None or not values[0]:
            return
        name, group_name = values
        self.window.run_task(
            lambda _progress: self.pool_service.create_pool(
                store,
                name=name,
                group_name=group_name,
                idempotency_key=f"new-pool:{name}:{group_name}",
            ),
            lambda _pool: self._load_pools(),
            lambda error: messagebox.showerror(
                "新建商品池失败",
                error,
                parent=self,
            ),
            log_title="新建动销商品池",
            task_scope=f"dongxiao:pool-create:{store.id}",
        )

    def _rename_pool(self) -> None:
        store = self._current_store()
        pool = self._current_pool()
        if store is None or pool is None:
            messagebox.showinfo("重命名商品池", "请先选择商品池。", parent=self)
            return
        values = self._pool_name_prompt(
            "重命名商品池",
            pool.name,
            pool.group_name,
        )
        if values is None or not values[0]:
            return
        name, group_name = values
        self.window.run_task(
            lambda _progress: self.pool_service.rename(
                store,
                pool.pool_id,
                name=name,
                group_name=group_name,
            ),
            lambda _pool: self._load_pools(),
            lambda error: messagebox.showerror(
                "重命名商品池失败",
                error,
                parent=self,
            ),
            log_title="重命名动销商品池",
            task_scope=f"dongxiao:pool-rename:{pool.pool_id}",
        )

    def _copy_pool(self) -> None:
        store = self._current_store()
        pool = self._current_pool()
        if store is None or pool is None:
            messagebox.showinfo("复制商品池", "请先选择商品池。", parent=self)
            return
        values = self._pool_name_prompt(
            "复制商品池",
            f"{pool.name} 副本",
            pool.group_name,
        )
        if values is None or not values[0]:
            return
        name, group_name = values
        self.window.run_task(
            lambda _progress: self.pool_service.copy(
                store,
                pool.pool_id,
                name=name,
                group_name=group_name,
            ),
            lambda _copy: self._load_pools(),
            lambda error: messagebox.showerror(
                "复制商品池失败",
                error,
                parent=self,
            ),
            log_title="复制动销商品池",
            task_scope=f"dongxiao:pool-copy:{pool.pool_id}",
        )

    def _selected_pool_item(self) -> str:
        rows = self.pool_tree.selection()
        return str(rows[0]) if rows else ""

    def _remove_pool_item(self) -> None:
        store = self._current_store()
        pool = self._current_pool()
        item_id = self._selected_pool_item()
        if store is None or pool is None or not item_id:
            messagebox.showinfo(
                "移除商品池明细",
                "请先选择一条商品池明细。",
                parent=self,
            )
            return
        if not messagebox.askyesno(
            "移除商品池明细",
            "只从当前商品池移除该明细，不删除商品或 SKU 数据。是否继续？",
            parent=self,
        ):
            return
        self.window.run_task(
            lambda _progress: self.pool_service.remove(
                store,
                pool.pool_id,
                item_id,
            ),
            lambda _result: self._load_pool_details(),
            lambda error: messagebox.showerror(
                "移除商品池明细失败",
                error,
                parent=self,
            ),
            log_title="移除动销商品池明细",
            task_scope=f"dongxiao:pool-remove:{pool.pool_id}",
        )

    def _move_pool_item(self, offset: int) -> None:
        store = self._current_store()
        pool = self._current_pool()
        selected = self._selected_pool_item()
        if store is None or pool is None or not selected:
            return
        ordered = [str(row["item_id"]) for row in self._pool_items]
        if selected not in ordered:
            return
        index = ordered.index(selected)
        target = index + offset
        if not 0 <= target < len(ordered):
            return
        ordered[index], ordered[target] = ordered[target], ordered[index]
        self.window.run_task(
            lambda _progress: self.pool_service.reorder(
                store,
                pool.pool_id,
                ordered,
            ),
            lambda _result: self._load_pool_details(),
            lambda error: messagebox.showerror(
                "商品池排序失败",
                error,
                parent=self,
            ),
            log_title="商品池明细排序",
            task_scope=f"dongxiao:pool-sort:{pool.pool_id}",
        )

    def _export_pool(self) -> None:
        store = self._current_store()
        pool = self._current_pool()
        if store is None or pool is None:
            messagebox.showinfo("导出商品池", "请先选择商品池。", parent=self)
            return
        path = filedialog.asksaveasfilename(
            parent=self,
            title="导出商品池",
            defaultextension=".csv",
            filetypes=(("CSV 文件", "*.csv"),),
            initialfile=f"{pool.name}.csv",
        )
        if not path:
            return
        target = Path(path)

        def work(_progress):
            content = self.pool_service.export_csv(store, pool.pool_id)
            target.write_text(content, encoding="utf-8-sig")
            return target

        self.window.run_task(
            work,
            lambda saved_path: self.window.set_status(
                f"商品池已导出：{saved_path}"
            ),
            lambda error: messagebox.showerror(
                "导出商品池失败",
                error,
                parent=self,
            ),
            log_title="导出动销商品池",
            task_scope=f"dongxiao:pool-export:{pool.pool_id}",
        )

    def _import_pool(self) -> None:
        store = self._current_store()
        pool = self._current_pool()
        if store is None or pool is None:
            messagebox.showinfo(
                "导入商品池 CSV",
                "请先选择店铺和商品池。",
                parent=self,
            )
            return
        path = filedialog.askopenfilename(
            parent=self,
            title="导入商品池 CSV",
            filetypes=(("CSV 文件", "*.csv"),),
        )
        if not path:
            return
        source = Path(path)
        try:
            content = source.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeError) as exc:
            messagebox.showerror(
                "读取商品池 CSV 失败",
                str(exc),
                parent=self,
            )
            return
        self.window.run_task(
            lambda _progress: self.pool_service.import_csv(
                store,
                pool.pool_id,
                content,
            ),
            self._pool_imported,
            lambda error: messagebox.showerror(
                "导入商品池 CSV 失败",
                error,
                parent=self,
            ),
            log_title="导入动销商品池 CSV",
            task_scope=f"dongxiao:pool-import:{pool.pool_id}",
        )

    def _pool_imported(self, result: dict[str, int]) -> None:
        messagebox.showinfo(
            "导入商品池 CSV",
            f"新增 {result['added']} 条，跳过 {result['skipped']} 条，"
            f"无效 {result['invalid']} 条。",
            parent=self,
        )
        self._load_pool_details()

    def _show_pool_history(self) -> None:
        store = self._current_store()
        pool = self._current_pool()
        if store is None or pool is None:
            return
        self.window.run_task(
            lambda _progress: self.pool_service.history(
                store,
                pool.pool_id,
            ),
            lambda rows: self._show_pool_history_rows(rows),
            lambda error: messagebox.showerror(
                "读取商品池历史失败",
                error,
                parent=self,
            ),
            log_title="读取商品池历史",
            task_scope=f"dongxiao:pool-history:{pool.pool_id}",
        )

    def _show_pool_history_rows(self, rows) -> None:
        dialog = tk.Toplevel(self)
        dialog.title("商品池历史")
        dialog.geometry("720x420")
        dialog.transient(self.window)
        table = ttk.Treeview(
            dialog,
            columns=("time", "action", "product", "sku"),
            show="headings",
        )
        for key, label, width in (
            ("time", "时间", 180),
            ("action", "动作", 150),
            ("product", "商品 ID", 180),
            ("sku", "SKU ID", 160),
        ):
            table.heading(key, text=label)
            table.column(key, width=width)
        table.pack(fill="both", expand=True, padx=12, pady=12)
        for row in rows:
            table.insert(
                "",
                "end",
                values=(
                    row["created_at"],
                    row["action"],
                    row["product_id"],
                    row["sku_id"],
                ),
            )

    def _check_selected_sku(self) -> None:
        store = self._current_store()
        selected = self._selected_product_ids()
        if store is None or not selected:
            messagebox.showinfo(
                "SKU 核验",
                "请先在商品筛选页选择一件商品。",
                parent=self,
            )
            return
        product_id = selected[0]
        try:
            quantity = max(1, int(self.sku_quantity_var.get()))
        except ValueError:
            messagebox.showwarning(
                "SKU 核验",
                "数量必须是正整数。",
                parent=self,
            )
            return
        mode = {
            "任意可售 SKU": SkuSelectionMode.ANY,
            "最低价 SKU": SkuSelectionMode.LOWEST_PRICE,
            "最高价 SKU": SkuSelectionMode.HIGHEST_PRICE,
            "指定 SKU": SkuSelectionMode.SPECIFIED,
            "默认 SKU": SkuSelectionMode.PRODUCT_DEFAULT,
        }[self.sku_mode_var.get()]
        self.sku_result_var.set("正在核验 SKU…")

        def work(_progress):
            product = self.repository.get_product(store.id, product_id)
            if product is None:
                raise ValueError("商品缓存不存在")
            candidates = self.repository.list_skus(store.id, product_id)
            return (
                product,
                select_sku(
                    store_id=store.id,
                    product_id=product_id,
                    candidates=candidates,
                    quantity=quantity,
                    mode=mode,
                    specified_sku_id=self.sku_id_var.get(),
                    product_state=product.state,
                ),
            )

        self.window.run_task(
            work,
            lambda result: self._show_sku_result(*result),
            lambda error: messagebox.showerror(
                "SKU 核验失败",
                error,
                parent=self,
            ),
            log_title="核验动销 SKU 规则",
            task_scope=f"dongxiao:sku:{store.id}:{product_id}",
        )

    def _show_sku_result(self, product, selection: SkuSelection) -> None:
        if selection.selected is None:
            self._verified_product_id = ""
            self._verified_sku_id = ""
            self.sku_result_var.set(
                f"{product.title}：无法确定 SKU。{selection.reason}"
            )
            return
        selected = selection.selected
        self._verified_product_id = product.product_id
        self._verified_sku_id = selected.sku_id
        self.sku_result_var.set(
            f"{product.title}：已选择 {selected.sku_id}，"
            f"单价 {_money(selected.price_cents)}，"
            f"数量 {selection.quantity}，库存 {selected.stock}"
        )

    def _add_verified_sku_to_pool(self) -> None:
        store = self._current_store()
        pool = self._current_pool()
        if (
            store is None
            or pool is None
            or not self._verified_product_id
            or not self._verified_sku_id
        ):
            messagebox.showinfo(
                "加入商品池",
                "请先核验 SKU，并选择店铺和商品池。",
                parent=self,
            )
            return
        self.window.run_task(
            lambda _progress: self.pool_service.add_products(
                store,
                pool.pool_id,
                [(self._verified_product_id, self._verified_sku_id)],
            ),
            lambda result: self._pool_added(result, pool.pool_id),
            lambda error: messagebox.showerror(
                "加入商品池失败",
                error,
                parent=self,
            ),
            log_title="将 SKU 加入动销商品池",
            task_scope=f"dongxiao:pool-add-sku:{pool.pool_id}",
        )

    @staticmethod
    def _task_status_label(status: TaskStatus) -> str:
        return {
            TaskStatus.PENDING: "等待执行",
            TaskStatus.PREPARING: "准备中",
            TaskStatus.RUNNING: "执行中",
            TaskStatus.PAUSED: "已暂停",
            TaskStatus.COMPLETED: "已完成",
            TaskStatus.PARTIALLY_FAILED: "部分失败",
            TaskStatus.FAILED: "全部失败",
            TaskStatus.CANCELLED: "已取消",
            TaskStatus.UNKNOWN: "结果未知",
        }.get(status, status.value)

    @staticmethod
    def _task_type_label(task_type: str) -> str:
        return {
            "product_sku_validation": "商品 SKU 批量核验",
            "order_verification": "真实订单核验",
            "aftersale_verification": "真实售后核验",
            "aftersale_decision": "售后平台执行",
        }.get(str(task_type), str(task_type))

    @staticmethod
    def _task_item_status_label(status: str) -> str:
        return {
            "pending": "等待",
            "running": "执行中",
            "succeeded": "成功",
            "failed": "失败",
            "skipped": "跳过",
            "cancelled": "已取消",
            "unknown": "结果未知",
        }.get(str(status), str(status))

    @staticmethod
    def _aftersale_status_label(status: AftersaleStatus) -> str:
        return {
            AftersaleStatus.UNKNOWN: "未知",
            AftersaleStatus.REQUESTED: "已申请",
            AftersaleStatus.PENDING_REVIEW: "待审批",
            AftersaleStatus.APPROVED: "已审批",
            AftersaleStatus.REJECTED: "已驳回",
            AftersaleStatus.PROCESSING: "处理中",
            AftersaleStatus.COMPLETED: "已完成",
            AftersaleStatus.FAILED: "失败",
            AftersaleStatus.CANCELLED: "已取消",
        }.get(status, status.value)

    @staticmethod
    def _order_status_label(status: OrderStatus | str) -> str:
        try:
            normalized = OrderStatus(status)
        except ValueError:
            return str(status)
        return {
            OrderStatus.UNKNOWN: "未知",
            OrderStatus.PENDING_PAYMENT: "待付款",
            OrderStatus.PAID: "已付款",
            OrderStatus.SHIPPED: "已发货",
            OrderStatus.COMPLETED: "已完成",
            OrderStatus.CANCELLED: "已取消",
            OrderStatus.REFUNDING: "退款中",
            OrderStatus.REFUNDED: "已退款",
        }[normalized]

    def _selected_task_id(self) -> str:
        rows = self.task_tree.selection()
        return str(rows[0]) if rows else ""

    def _load_tasks(self) -> None:
        store = self._current_store()
        if store is None:
            return

        def work(_progress):
            return (
                self.task_service.list_tasks(store),
                self.task_service.statistics(store),
            )

        self.window.run_task(
            work,
            self._render_tasks,
            lambda error: messagebox.showerror(
                "读取任务失败",
                error,
                parent=self,
            ),
            log_title="读取动销任务中心",
            task_scope=f"dongxiao:tasks-load:{store.id}",
        )

    def _render_tasks(self, result) -> None:
        tasks, stats = result
        for row in self.task_tree.get_children():
            self.task_tree.delete(row)
        for task in tasks:
            self.task_tree.insert(
                "",
                "end",
                iid=task.task_id,
                values=(
                    _date_time(task.created_at),
                    task.task_id,
                    self._task_type_label(task.task_type),
                    self._task_status_label(task.status),
                    f"{task.succeeded_items + task.failed_items}/{task.total_items}",
                    task.last_error or task.status_reason or "—",
                ),
            )
        self.task_stats_var.set(
            f"任务 {stats.total} 个 · 运行中 {stats.running} · "
            f"暂停 {stats.paused} · 完成 {stats.completed} · "
            f"失败 {stats.failed} · 未知 {stats.unknown} · "
            f"明细成功 {stats.succeeded_items} / "
            f"失败 {stats.failed_items}"
        )
        self.window.set_status(
            f"任务中心已读取 {stats.total} 个任务",
            busy=False,
        )

    def _start_task_scheduler(self) -> None:
        if self._ensure_task_runtime() and not self.runtime.started:
            self.runtime.start()

    def _ensure_task_runtime(self) -> bool:
        if self.runtime is not None and self.task_scheduler is not None:
            return True
        ensure = getattr(self.window, "ensure_dongxiao_runtime", None)
        if not callable(ensure):
            return False
        runtime = ensure()
        if runtime is None:
            error = str(
                getattr(self.window, "dongxiao_runtime_error", "") or "未知错误"
            )
            self.source_status_var.set(f"动销中心初始化异常：{error}")
            return False
        self.runtime = runtime
        self.repository = runtime.repository
        self.task_service = runtime.task_service
        self.order_service = runtime.order_service
        self.aftersale_service = runtime.aftersale_service
        self.task_scheduler = runtime.scheduler
        self.source_status_var.set("动销中心运行时已恢复")
        return True

    def _create_pool_task(self) -> None:
        store = self._current_store()
        pool = self._current_pool()
        if store is None or pool is None:
            messagebox.showinfo(
                "创建批量任务",
                "请先选择店铺和商品池。",
                parent=self,
            )
            return
        if not self._ensure_task_runtime():
            messagebox.showerror(
                "创建批量任务失败",
                "动销中心运行时初始化失败，未创建任务。\n\n"
                + str(
                    getattr(self.window, "dongxiao_runtime_error", "")
                    or "未知错误"
                ),
                parent=self,
            )
            return
        task_key = (
            f"pool-task:{pool.pool_id}:"
            f"{int(datetime.now(UTC).timestamp())}"
        )

        def done(result) -> None:
            task, created = result
            self._start_task_scheduler()
            if self.task_scheduler is not None:
                self.task_scheduler.enqueue(task.task_id)
            messagebox.showinfo(
                "创建批量任务",
                (
                    "已创建"
                    if created
                    else "已识别重复请求，复用"
                )
                + f"任务 {task.task_id}，共 {task.total_items} 项。",
                parent=self,
            )
            self._load_tasks()

        self.window.run_task(
            lambda _progress: self.task_service.create_pool_validation_task(
                store,
                pool.pool_id,
                idempotency_key=task_key,
                actor_id=self._actor_id(),
                quantity=1,
            ),
            done,
            lambda error: messagebox.showerror(
                "创建批量任务失败",
                error,
                parent=self,
            ),
            log_title="创建动销批量任务",
            task_scope=f"dongxiao:task-create:{store.id}",
        )

    def _pause_task(self) -> None:
        self._task_control("pause")

    def _resume_task(self) -> None:
        self._task_control("resume")

    def _cancel_task(self) -> None:
        self._task_control("cancel")

    def _retry_task(self) -> None:
        self._task_control("retry")

    def _task_control(self, action: str) -> None:
        store = self._current_store()
        task_id = self._selected_task_id()
        if store is None or not task_id:
            messagebox.showinfo(
                "任务操作",
                "请先选择任务。",
                parent=self,
            )
            return
        methods = {
            "pause": self.task_service.pause,
            "resume": self.task_service.resume,
            "cancel": self.task_service.cancel,
            "retry": self.task_service.retry_failed,
        }
        method = methods[action]
        if action in {"resume", "retry"} and not self._ensure_task_runtime():
            messagebox.showerror(
                "任务恢复失败",
                "动销中心运行时不可用，任务未恢复。",
                parent=self,
            )
            return

        def done(task) -> None:
            if action in {"resume", "retry"}:
                self._start_task_scheduler()
                if self.task_scheduler is not None:
                    self.task_scheduler.enqueue(task.task_id)
            self._load_tasks()

        self.window.run_task(
            lambda _progress: method(
                store,
                task_id,
                actor_id=self._actor_id(),
            ),
            done,
            lambda error: messagebox.showerror(
                "任务操作失败",
                error,
                parent=self,
            ),
            log_title=f"动销任务操作：{action}",
            task_scope=f"dongxiao:task-{action}:{task_id}",
        )

    def _show_task_details(self) -> None:
        store = self._current_store()
        task_id = self._selected_task_id()
        if store is None or not task_id:
            return

        def show(result) -> None:
            task, items, attempts = result
            dialog = tk.Toplevel(self)
            dialog.title(f"任务详情 · {task.task_id}")
            dialog.geometry("980x560")
            if task is not None:
                ttk.Label(
                    dialog,
                    text=(
                        f"状态：{self._task_status_label(task.status)}  "
                        f"执行模式：{task.execution_mode}  "
                        f"租约：{task.lease_owner or '无'}  "
                        f"心跳：{_date_time(task.last_heartbeat_at)}"
                    ),
                ).pack(anchor="w", padx=12, pady=(12, 0))
            table = ttk.Treeview(
                dialog,
                columns=("type", "subject", "status", "attempts", "error"),
                show="headings",
            )
            for key, label, width in (
                ("type", "对象类型", 100),
                ("subject", "对象 ID", 240),
                ("status", "状态", 100),
                ("attempts", "尝试", 70),
                ("error", "错误", 360),
            ):
                table.heading(key, text=label)
                table.column(key, width=width, anchor="w")
            table.pack(fill="both", expand=True, padx=12, pady=(12, 4))
            for item in items:
                table.insert(
                    "",
                    "end",
                    values=(
                        item.subject_type,
                        item.subject_id,
                        self._task_item_status_label(item.status.value),
                        f"{item.attempt_count}/{item.max_attempts}",
                        item.last_error_message or "—",
                    ),
                )
            ttk.Label(
                dialog,
                text=f"尝试记录 {len(attempts)} 条",
            ).pack(anchor="w", padx=12, pady=8)

        self.window.run_task(
            lambda _progress: (
                self.repository.get_task(
                    task_id,
                    store_app_id=store.app_id,
                ),
                self.task_service.list_items(task_id, store=store),
                self.task_service.list_attempts(store, task_id),
            ),
            show,
            lambda error: messagebox.showerror(
                "读取任务详情失败",
                error,
                parent=self,
            ),
            log_title="读取动销任务详情",
            task_scope=f"dongxiao:task-detail:{task_id}",
        )

    def _verify_unknown_task(self) -> None:
        store = self._current_store()
        task_id = self._selected_task_id()
        if (
            store is None
            or not task_id
            or self.task_scheduler is None
        ):
            messagebox.showinfo(
                "核验未知结果",
                "请先选择结果未知任务。",
                parent=self,
            )
            return

        def done(result) -> None:
            task, verified_count = result
            messagebox.showinfo(
                "核验未知结果",
                f"已核验 {verified_count} 条，任务当前状态："
                f"{self._task_status_label(task.status)}",
                parent=self,
            )
            self._load_tasks()

        self.window.run_task(
            lambda _progress: self.task_scheduler.verify_unknown_task(
                task_id,
                store_app_id=store.app_id,
                actor_id=self._actor_id(),
                permission_checker=self._permission_checker,
            ),
            done,
            lambda error: messagebox.showerror(
                "核验未知结果失败",
                error,
                parent=self,
            ),
            log_title="核验动销未知任务",
            task_scope=f"dongxiao:task-verify:{task_id}",
        )

    def _date_range_timestamps(
        self,
        start_value: str,
        end_value: str,
        *,
        max_seconds: int,
    ) -> tuple[int, int]:
        start = self._parse_optional_date(
            start_value,
            "开始日期",
            end=False,
        )
        end = self._parse_optional_date(
            end_value,
            "结束日期",
            end=True,
        )
        if start is None or end is None or end <= start:
            raise ValueError("请填写有效的开始和结束日期")
        start_ts = int(start.timestamp())
        end_ts = int(end.timestamp())
        if end_ts - start_ts > max_seconds:
            raise ValueError("查询时间范围超过官方接口限制")
        return start_ts, end_ts

    def _load_orders(self) -> None:
        store = self._current_store()
        if store is None:
            return
        self.window.run_task(
            lambda _progress: self.order_service.list_orders(store),
            self._render_orders,
            lambda error: messagebox.showerror(
                "读取订单失败",
                error,
                parent=self,
            ),
            log_title="读取动销真实订单",
            task_scope=f"dongxiao:orders-load:{store.id}",
        )

    def _render_orders(self, records) -> None:
        for row in self.order_tree.get_children():
            self.order_tree.delete(row)
        for record in records:
            self.order_tree.insert(
                "",
                "end",
                iid=record.platform_order_id,
                values=(
                    record.platform_order_id,
                    self._order_status_label(record.status),
                    _money(record.total_amount_cents),
                    record.item_count,
                    _date_time(record.create_time),
                    _date_time(record.update_time),
                    _date_time(record.last_synced_at),
                ),
            )
        self.order_status_var.set(
            f"本地已核验真实订单 {len(records)} 条；"
            "数据仅来自官方订单接口"
        )
        self.window.set_status(
            f"真实订单已读取 {len(records)} 条",
            busy=False,
        )

    def _sync_orders(self) -> None:
        store = self._current_store()
        if store is None:
            messagebox.showinfo("同步订单", "请先选择店铺。", parent=self)
            return
        try:
            start_ts, end_ts = self._date_range_timestamps(
                self.order_start_var.get(),
                self.order_end_var.get(),
                max_seconds=7 * 86400,
            )
        except ValueError as exc:
            messagebox.showwarning("订单日期错误", str(exc), parent=self)
            return

        def done(result) -> None:
            self.order_status_var.set(
                f"同步完成：发现 {result.discovered}，"
                f"保存 {result.saved}，失败 {result.failed}"
            )
            if result.errors:
                messagebox.showwarning(
                    "订单同步部分完成",
                    "\n".join(result.errors[:10]),
                    parent=self,
                )
            self._load_orders()

        self.window.run_task(
            lambda progress: self.order_service.sync_orders(
                store,
                start_time=start_ts,
                end_time=end_ts,
                progress=progress,
            ),
            done,
            lambda error: messagebox.showerror(
                "同步真实订单失败",
                error,
                parent=self,
            ),
            log_title="同步动销真实订单",
            task_scope=f"dongxiao:orders-sync:{store.id}",
        )

    def _create_order_task(self) -> None:
        store = self._current_store()
        order_ids = [
            str(item) for item in self.order_tree.selection()
        ]
        if store is None or not order_ids:
            messagebox.showinfo(
                "订单核验任务",
                "请选择至少一个订单。",
                parent=self,
            )
            return
        if not self._ensure_task_runtime():
            messagebox.showerror(
                "订单核验任务",
                "动销中心运行时不可用，任务未创建。",
                parent=self,
            )
            return
        key = f"order-verify:{'|'.join(order_ids)}"

        def done(result) -> None:
            task, created = result
            self._start_task_scheduler()
            if self.task_scheduler is not None:
                self.task_scheduler.enqueue(task.task_id)
            messagebox.showinfo(
                "订单核验任务",
                ("已创建" if created else "已复用") + f"任务 {task.task_id}",
                parent=self,
            )

        self.window.run_task(
            lambda _progress: self.task_service.create_order_verification_task(
                store,
                order_ids,
                idempotency_key=key,
                actor_id=self._actor_id(),
            ),
            done,
            lambda error: messagebox.showerror(
                "创建订单核验任务失败",
                error,
                parent=self,
            ),
            log_title="创建真实订单核验任务",
            task_scope=f"dongxiao:order-task:{store.id}",
        )

    def _sync_order_by_id(self) -> None:
        store = self._current_store()
        order_id = self.order_id_var.get().strip()
        if store is None or not order_id:
            messagebox.showinfo(
                "核验订单",
                "请先选择店铺并填写订单号。",
                parent=self,
            )
            return

        def done(record) -> None:
            self.order_status_var.set(
                f"订单 {record.platform_order_id} 已通过官方接口核验"
            )
            self._load_orders()

        self.window.run_task(
            lambda _progress: self.order_service.sync_order(
                store,
                order_id,
            ),
            done,
            lambda error: messagebox.showerror(
                "核验订单失败",
                error,
                parent=self,
            ),
            log_title="按订单号核验真实订单",
            task_scope=f"dongxiao:order-get:{store.id}:{order_id}",
        )

    def _show_order_history(self) -> None:
        store = self._current_store()
        selected = self.order_tree.selection()
        if store is None or not selected:
            messagebox.showinfo(
                "订单状态历史",
                "请先选择一个订单。",
                parent=self,
            )
            return
        order_id = str(selected[0])

        def show(rows) -> None:
            dialog = tk.Toplevel(self)
            dialog.title(f"订单状态历史 · {order_id}")
            dialog.geometry("760x420")
            table = ttk.Treeview(
                dialog,
                columns=("time", "status", "trusted", "request"),
                show="headings",
            )
            for key, label, width in (
                ("time", "核验时间", 170),
                ("status", "状态", 120),
                ("trusted", "可信", 70),
                ("request", "请求标识", 260),
            ):
                table.heading(key, text=label)
                table.column(key, width=width, anchor="w")
            table.pack(fill="both", expand=True, padx=12, pady=12)
            for row in rows:
                table.insert(
                    "",
                    "end",
                    values=(
                        row["observed_at"],
                        self._order_status_label(row["status"]),
                        "是" if int(row["trusted"]) else "否",
                        row["request_id"] or "—",
                    ),
                )

        self.window.run_task(
            lambda _progress: self.repository.list_order_snapshots(
                store.app_id,
                order_id,
            ),
            show,
            lambda error: messagebox.showerror(
                "读取订单历史失败",
                error,
                parent=self,
            ),
            log_title="读取订单状态历史",
            task_scope=f"dongxiao:order-history:{order_id}",
        )

    def _load_aftersales(self) -> None:
        store = self._current_store()
        if store is None:
            return
        self.window.run_task(
            lambda _progress: self.aftersale_service.list_cases(store),
            self._render_aftersales,
            lambda error: messagebox.showerror(
                "读取售后工单失败",
                error,
                parent=self,
            ),
            log_title="读取动销售后工单",
            task_scope=f"dongxiao:aftersales-load:{store.id}",
        )

    def _render_aftersales(self, cases) -> None:
        for row in self.aftersale_tree.get_children():
            self.aftersale_tree.delete(row)
        for case in cases:
            self.aftersale_tree.insert(
                "",
                "end",
                iid=case.case_id,
                values=(
                    case.platform_case_id,
                    case.platform_order_id or "—",
                    self._aftersale_status_label(case.status),
                    case.platform_status or "—",
                    _money(case.requested_amount_cents),
                    _money(case.approved_amount_cents),
                    _money(case.paid_amount_cents),
                    _money(case.remaining_refundable_cents),
                    _date_time(case.deadline),
                    _date_time(case.last_synced_at),
                ),
            )
        self.aftersale_status_var.set(
            f"本地售后工单 {len(cases)} 条；"
            "退款执行前必须有已通过审批"
        )
        self.window.set_status(
            f"售后工单已读取 {len(cases)} 条",
            busy=False,
        )

    def _sync_aftersales(self) -> None:
        store = self._current_store()
        if store is None:
            messagebox.showinfo("同步售后", "请先选择店铺。", parent=self)
            return
        try:
            start_ts, end_ts = self._date_range_timestamps(
                self.aftersale_start_var.get(),
                self.aftersale_end_var.get(),
                max_seconds=86400,
            )
        except ValueError as exc:
            messagebox.showwarning("售后日期错误", str(exc), parent=self)
            return

        def done(result) -> None:
            self.aftersale_status_var.set(
                f"同步完成：发现 {result.discovered}，"
                f"保存 {result.saved}，失败 {result.failed}"
            )
            if result.errors:
                messagebox.showwarning(
                    "售后同步部分完成",
                    "\n".join(result.errors[:10]),
                    parent=self,
                )
            self._load_aftersales()

        self.window.run_task(
            lambda progress: self.aftersale_service.sync_aftersales(
                store,
                start_time=start_ts,
                end_time=end_ts,
                time_field="update",
                progress=progress,
            ),
            done,
            lambda error: messagebox.showerror(
                "同步真实售后失败",
                error,
                parent=self,
            ),
            log_title="同步动销真实售后",
            task_scope=f"dongxiao:aftersales-sync:{store.id}",
        )

    def _selected_aftersale(self):
        rows = self.aftersale_tree.selection()
        if not rows:
            return None
        store = self._current_store()
        if store is None:
            return None
        return self.repository.get_aftersale_case(store.id, str(rows[0]))

    def _request_aftersale_approval(self) -> None:
        store = self._current_store()
        case = self._selected_aftersale()
        if store is None or case is None:
            messagebox.showinfo(
                "申请退款审批",
                "请先选择售后工单。",
                parent=self,
            )
            return
        try:
            amount = parse_yuan_to_cents(
                self.aftersale_amount_var.get()
                or f"{case.requested_amount_cents / 100:.2f}"
            )
        except ValueError as exc:
            messagebox.showwarning("退款金额错误", str(exc), parent=self)
            return
        key = f"aftersale-request:{case.case_id}:{amount}"

        def done(result) -> None:
            approval, saved_case = result
            self.aftersale_status_var.set(
                f"审批已创建：{approval.approval_id}；"
                f"剩余可退 {_money(saved_case.remaining_refundable_cents)}"
            )
            self._load_aftersales()

        self.window.run_task(
            lambda _progress: self.aftersale_service.create_approval(
                store,
                case.case_id,
                approved_amount_cents=amount,
                requested_by=self._actor_id(),
                permission_checker=self._permission_checker,
                idempotency_key=key,
            ),
            done,
            lambda error: messagebox.showerror(
                "创建退款审批失败",
                error,
                parent=self,
            ),
            log_title="创建动销退款审批",
            task_scope=f"dongxiao:aftersale-request:{case.case_id}",
        )

    def _decide_aftersale_approval(self, approved: bool) -> None:
        store = self._current_store()
        case = self._selected_aftersale()
        if store is None or case is None:
            return
        if not self._ensure_task_runtime():
            messagebox.showerror(
                "售后执行任务",
                "动销中心运行时不可用，任务未创建。",
                parent=self,
            )
            return
        approvals = self.aftersale_service.list_approvals(
            store,
            case_id=case.case_id,
        )
        pending = next(
            (
                approval
                for approval in approvals
                if approval.status is ApprovalStatus.PENDING
            ),
            None,
        )
        if pending is None:
            messagebox.showinfo(
                "退款审批",
                "当前售后没有待处理审批。",
                parent=self,
            )
            return
        if not messagebox.askyesno(
            "退款审批",
            (
                "确认通过"
                if approved
                else "确认驳回"
            )
            + f"审批 {pending.approval_id}？",
            parent=self,
        ):
            return

        def done(result) -> None:
            approval, saved_case = result
            self.aftersale_status_var.set(
                f"审批结果：{'通过' if approved else '驳回'}；"
                f"售后状态：{self._aftersale_status_label(saved_case.status)}"
            )
            self._load_aftersales()

        self.window.run_task(
            lambda _progress: self.aftersale_service.decide_approval(
                store,
                pending.approval_id,
                approved=approved,
                actor_id=self._actor_id(),
                reason="页面人工审批",
                permission_checker=self._permission_checker,
            ),
            done,
            lambda error: messagebox.showerror(
                "处理退款审批失败",
                error,
                parent=self,
            ),
            log_title="处理动销退款审批",
            task_scope=f"dongxiao:aftersale-approve:{pending.approval_id}",
        )

    def _submit_aftersale_decision(self, action: str) -> None:
        store = self._current_store()
        case = self._selected_aftersale()
        if store is None or case is None:
            return
        if not self._ensure_task_runtime():
            messagebox.showerror(
                "售后核验任务",
                "动销中心运行时不可用，任务未创建。",
                parent=self,
            )
            return
        if not messagebox.askyesno(
            "提交售后平台操作",
            (
                "将通过官方接口提交售后操作。"
                "提交后若结果未知，系统只会核验，不会自动重复提交。\n\n"
                f"售后单：{case.platform_case_id}\n"
                f"审批金额：{_money(case.approved_amount_cents)}\n"
                "是否继续？"
            ),
            parent=self,
        ):
            return
        reason_type = 0
        reason = ""
        if action == "reject":
            raw = simpledialog.askstring(
                "拒绝售后",
                "请输入官方拒绝原因类型编号（不能留空）：",
                parent=self,
            )
            if raw is None:
                return
            try:
                reason_type = int(raw)
            except ValueError:
                messagebox.showwarning(
                    "拒绝原因错误",
                    "拒绝原因类型必须是整数。",
                    parent=self,
                )
                return
            reason = simpledialog.askstring(
                "拒绝售后",
                "拒绝原因说明（可留空）：",
                parent=self,
            ) or ""
        key = (
            f"aftersale-{action}:{case.case_id}:"
            f"{case.approved_amount_cents}"
        )

        def done(result) -> None:
            task, created = result
            self._start_task_scheduler()
            if self.task_scheduler is not None:
                self.task_scheduler.enqueue(task.task_id)
            self.aftersale_status_var.set(
                ("已创建" if created else "已复用")
                + f"售后执行任务 {task.task_id}"
            )

        self.window.run_task(
            lambda _progress: self.aftersale_service.create_decision_task(
                store,
                case.case_id,
                action=action,
                actor_id=self._actor_id(),
                permission_checker=self._permission_checker,
                idempotency_key=key,
                reject_reason_type=reason_type,
                reject_reason=reason,
            ),
            done,
            lambda error: messagebox.showerror(
                "创建售后执行任务失败",
                error,
                parent=self,
            ),
            log_title="创建动销售后执行任务",
            task_scope=f"dongxiao:aftersale-execute:{case.case_id}",
        )

    def _show_aftersale_approvals(self) -> None:
        store = self._current_store()
        case = self._selected_aftersale()
        if store is None or case is None:
            return
        approvals = self.aftersale_service.list_approvals(
            store,
            case_id=case.case_id,
        )
        dialog = tk.Toplevel(self)
        dialog.title(f"售后审批记录 · {case.platform_case_id}")
        dialog.geometry("820x400")
        table = ttk.Treeview(
            dialog,
            columns=("time", "status", "requester", "approver", "reason"),
            show="headings",
        )
        for key, label, width in (
            ("time", "申请时间", 150),
            ("status", "状态", 90),
            ("requester", "申请人", 120),
            ("approver", "审批人", 120),
            ("reason", "说明", 300),
        ):
            table.heading(key, text=label)
            table.column(key, width=width, anchor="w")
        table.pack(fill="both", expand=True, padx=12, pady=12)
        status_labels = {
            ApprovalStatus.PENDING: "待审批",
            ApprovalStatus.APPROVED: "已通过",
            ApprovalStatus.REJECTED: "已驳回",
            ApprovalStatus.EXPIRED: "已过期",
            ApprovalStatus.CANCELLED: "已取消",
        }
        for approval in approvals:
            table.insert(
                "",
                "end",
                values=(
                    approval.requested_at.strftime("%Y-%m-%d %H:%M"),
                    status_labels.get(approval.status, approval.status.value),
                    approval.requested_by,
                    approval.decided_by or "—",
                    approval.decision_reason or "—",
                ),
            )

    def _create_aftersale_task(self) -> None:
        store = self._current_store()
        case = self._selected_aftersale()
        if store is None or case is None:
            return
        task_key = f"aftersale-verify:{case.case_id}"

        def done(result) -> None:
            task, created = result
            self._start_task_scheduler()
            if self.task_scheduler is not None:
                self.task_scheduler.enqueue(task.task_id)
            messagebox.showinfo(
                "售后核验任务",
                ("已创建" if created else "已复用") + f"任务 {task.task_id}",
                parent=self,
            )

        self.window.run_task(
            lambda _progress: self.task_service.create_aftersale_verification_task(
                store,
                [case.case_id],
                idempotency_key=task_key,
                actor_id=self._actor_id(),
            ),
            done,
            lambda error: messagebox.showerror(
                "创建售后核验任务失败",
                error,
                parent=self,
            ),
            log_title="创建售后核验任务",
            task_scope=f"dongxiao:aftersale-task:{case.case_id}",
        )

    def _load_audit(self) -> None:
        store = self._current_store()
        if store is None:
            return
        self.window.run_task(
            lambda _progress: self.repository.list_audit_events(
                store_app_id=store.app_id
            ),
            self._render_audit,
            lambda error: messagebox.showerror(
                "读取审计日志失败",
                error,
                parent=self,
            ),
            log_title="读取动销审计日志",
            task_scope=f"dongxiao:audit:{store.id}",
        )

    def _render_audit(self, rows) -> None:
        for row in self.audit_tree.get_children():
            self.audit_tree.delete(row)
        for index, row in enumerate(rows):
            entity_label = AUDIT_ENTITY_LABELS.get(
                row["entity_type"],
                row["entity_type"],
            )
            self.audit_tree.insert(
                "",
                "end",
                iid=f"audit-{index}",
                values=(
                    row["created_at"],
                    AUDIT_ACTION_LABELS.get(
                        row["action"],
                        row["action"],
                    ),
                    f"{entity_label} · {row['entity_id']}",
                    row["result"],
                    _audit_detail_text(row["detail_json"]),
                ),
            )
        self.audit_status_var.set(f"已读取 {len(rows)} 条审计事件")
        self.window.set_status(
            f"动销审计日志已读取 {len(rows)} 条",
            busy=False,
        )

    def _run_preflight_local(self) -> None:
        window = self.window
        store = self._current_store()
        window.run_task(
            lambda _progress: self.preflight_service.run_local(
                store=store,
                actor_id=self._actor_id(),
                permission_checker=self._permission_checker,
            ),
            self._preflight_completed,
            self._preflight_failed,
            progress=lambda message, _current, _total: (
                self.preflight_status_var.set(message)
            ),
            log_title="动销实盘前本地检查",
            task_scope="dongxiao:preflight-local",
        )

    def _run_preflight_platform(self) -> None:
        store = self._current_store()
        if store is None:
            messagebox.showwarning(
                "实盘前检查",
                "请先选择当前店铺。",
                parent=self,
            )
            return
        if not self._permission_checker(
            "dongxiao_preflight_platform_read"
        ):
            messagebox.showwarning(
                "实盘前检查",
                "当前账号没有真实平台只读检查权限。",
                parent=self,
            )
            return
        confirmed = messagebox.askyesno(
            "确认真实只读检查",
            (
                f"将对店铺“{store.name}”执行一次真实平台只读检查。\n\n"
                "检查会获取 Token，并读取商品列表第一页、"
                "最近 24 小时订单和售后列表。\n"
                "不会同意售后、拒绝售后、退款或修改平台数据。\n\n"
                "是否仅授权本次手动检查？"
            ),
            parent=self,
        )
        if not confirmed:
            return
        self.window.run_task(
            lambda _progress: self.preflight_service.run_platform_readonly(
                store,
                actor_id=self._actor_id(),
                permission_checker=self._permission_checker,
                authorization_text=PLATFORM_READ_AUTHORIZATION,
            ),
            self._preflight_completed,
            self._preflight_failed,
            progress=lambda message, _current, _total: (
                self.preflight_status_var.set(message)
            ),
            log_title="动销真实平台手动只读检查",
            task_scope=f"dongxiao:preflight-platform:{store.id}",
            store_app_id=str(store.app_id),
        )

    def _preflight_completed(self, report) -> None:
        self._last_preflight_report = report
        for item_id in self.preflight_tree.get_children():
            self.preflight_tree.delete(item_id)
        labels = {
            PreflightState.PASSED: "已通过",
            PreflightState.FAILED: "未通过",
            PreflightState.NOT_VERIFIED: "未验证",
            PreflightState.MANUAL_AUTHORIZATION_REQUIRED: "需要人工授权",
        }
        for check in report.checks:
            self.preflight_tree.insert(
                "",
                "end",
                values=(
                    labels[check.state],
                    check.label,
                    check.message,
                ),
            )
        counts = report.counts
        self.preflight_status_var.set(
            "检查完成："
            f"通过 {counts['passed']}，"
            f"未通过 {counts['failed']}，"
            f"未验证 {counts['not_verified']}，"
            f"需人工授权 {counts['manual_authorization_required']}；"
            f"是否可放行：{'是' if report.release_ready else '否'}"
        )
        self._update_platform_write_status()
        self._load_api_calls()
        self.window.set_status(
            "动销实盘前检查完成",
            busy=False,
        )

    def _preflight_failed(self, error: str) -> None:
        self.preflight_status_var.set(f"检查失败：{error}")
        messagebox.showerror(
            "实盘前检查失败",
            error,
            parent=self,
        )

    def _save_preflight_report(self) -> None:
        report = getattr(self, "_last_preflight_report", None)
        if report is None:
            messagebox.showwarning(
                "保存验收报告",
                "请先运行实盘前检查。",
                parent=self,
            )
            return
        path = filedialog.asksaveasfilename(
            parent=self,
            title="保存动销实盘前验收报告",
            defaultextension=".txt",
            filetypes=(("文本文件", "*.txt"), ("全部文件", "*.*")),
            initialfile="Dongxiao_Phase5_Preflight.txt",
        )
        if not path:
            return
        Path(path).write_text(
            self.preflight_service.format_report(report),
            encoding="utf-8",
        )
        self.window.set_status("动销实盘前验收报告已保存", busy=False)

    def _disable_platform_writes_now(self) -> None:
        if not getattr(self, "preflight_service", None):
            return
        self.platform_write_status_var.set("真实平台写操作：正在关闭")
        runtime = self.runtime
        if runtime is None:
            ensure = getattr(self.window, "ensure_dongxiao_runtime", None)
            runtime = ensure() if callable(ensure) else None
        if runtime is None:
            self.platform_write_status_var.set("真实平台写操作：关闭失败")
            messagebox.showerror(
                "真实平台写操作",
                "动销 Runtime 未初始化，无法更新总开关。",
                parent=self,
            )
            return
        self.window.run_task(
            lambda _progress: runtime.set_platform_writes_enabled(
                False,
                actor_id=self._actor_id(),
                permission_checker=self._permission_checker,
                emergency=True,
            ),
            lambda _result: self._platform_gate_completed(False),
            self._platform_gate_failed,
            log_title="紧急关闭动销真实平台写操作",
            task_scope="dongxiao:platform-write-disable",
        )

    def _disable_current_store_writes(self) -> None:
        store = self._current_store()
        if store is None:
            messagebox.showwarning(
                "真实平台写操作",
                "请先选择店铺。",
                parent=self,
            )
            return
        runtime = self.runtime
        if runtime is None:
            ensure = getattr(self.window, "ensure_dongxiao_runtime", None)
            runtime = ensure() if callable(ensure) else None
        if runtime is None:
            messagebox.showerror(
                "真实平台写操作",
                "动销 Runtime 未初始化。",
                parent=self,
            )
            return
        self.window.run_task(
            lambda _progress: runtime.set_platform_writes_enabled(
                False,
                store=store,
                actor_id=self._actor_id(),
                permission_checker=self._permission_checker,
            ),
            lambda _result: self._platform_gate_completed(False),
            self._platform_gate_failed,
            log_title="关闭当前店铺真实平台写操作",
            task_scope=f"dongxiao:store-write-disable:{store.id}",
            store_app_id=str(store.app_id),
        )

    def _enable_platform_writes(self) -> None:
        store = self._current_store()
        if store is None:
            messagebox.showwarning(
                "真实平台写操作",
                "请先选择并完成当前店铺的真实只读验收。",
                parent=self,
            )
            return
        if not self._permission_checker(
            "dongxiao_platform_write_switch"
        ):
            messagebox.showwarning(
                "平台写操作",
                "当前账号没有修改真实平台写操作开关的权限。",
                parent=self,
            )
            return
        first = messagebox.askyesno(
                "开启真实平台写操作",
            (
                "开启后只允许创建“已经完成人工审批”的平台写任务，"
                "不会自动同意、拒绝或退款。\n"
                "当前店铺必须在最近 10 分钟内完成官方 Token、"
                "商品、订单和售后只读验收。\n\n"
                "是否进入准备状态？"
            ),
            parent=self,
        )
        if not first:
            return
        second = messagebox.askyesno(
            "二次确认",
            (
                "确认开启当前会话的平台写操作？"
                "店铺切换或验收过期后会立即阻止写任务。"
            ),
            parent=self,
        )
        if not second:
            return
        runtime = self.runtime
        if runtime is None:
            ensure = getattr(self.window, "ensure_dongxiao_runtime", None)
            runtime = ensure() if callable(ensure) else None
        if runtime is None:
            messagebox.showerror(
                "真实平台写操作",
                "动销 Runtime 未初始化，禁止开启平台写操作。",
                parent=self,
            )
            return
        self.window.run_task(
            lambda _progress: runtime.set_platform_writes_enabled(
                True,
                store=store,
                actor_id=self._actor_id(),
                permission_checker=self._permission_checker,
            ),
            lambda _result: self._platform_gate_completed(True),
            self._platform_gate_failed,
            log_title="开启动销平台写操作",
            task_scope="dongxiao:platform-write-enable",
        )

    def _platform_gate_completed(self, enabled: bool) -> None:
        self.platform_write_status_var.set(
            "真实平台写操作：已开启（需逐单人工确认）"
            if enabled
            else "真实平台写操作：已关闭"
        )
        self.window.set_status(
            "动销真实平台写操作已开启"
            if enabled
            else "已紧急关闭所有店铺的真实平台写操作",
            busy=False,
        )

    def _platform_gate_failed(self, error: str) -> None:
        self.platform_write_status_var.set("真实平台写操作：状态更新失败")
        messagebox.showerror(
            "真实平台写操作",
            error,
            parent=self,
        )

    def _update_platform_write_status(self) -> None:
        runtime = self.runtime
        if runtime is None:
            self.platform_write_status_var.set(
                "真实平台写操作：已关闭"
            )
            return
        try:
            store = self._current_store()
            emergency_enabled = runtime.platform_writes_enabled()
            enabled = (
                runtime.store_platform_writes_enabled(str(store.app_id))
                if store is not None
                else emergency_enabled
            )
            verification = (
                runtime.platform_readonly_verification(str(store.app_id))
                if store is not None and enabled
                else None
            )
        except Exception:
            self.platform_write_status_var.set(
                "真实平台写操作：状态读取失败"
            )
            return
        if enabled and verification is not None:
            self.platform_write_status_var.set(
                "真实平台写操作：已开启（当前店铺验收有效）"
            )
        elif enabled:
            self.platform_write_status_var.set(
                "真实平台写操作：已开启但当前店铺验收失效，任务将被阻止"
            )
        elif emergency_enabled and store is None:
            self.platform_write_status_var.set(
                "真实平台写操作：其他店铺已开启；选择店铺查看"
            )
        else:
            self.platform_write_status_var.set(
                "真实平台写操作：已关闭"
            )

    def _load_api_calls(self) -> None:
        store = self._current_store()
        store_app_id = str(store.app_id) if store is not None else ""

        def done(rows: list[dict[str, Any]]) -> None:
            for item_id in self.api_call_tree.get_children():
                self.api_call_tree.delete(item_id)
            for row in rows:
                self.api_call_tree.insert(
                    "",
                    "end",
                    values=(
                        str(row.get("started_at") or ""),
                        str(row.get("method") or ""),
                        str(row.get("path") or ""),
                        str(row.get("outcome") or ""),
                        str(row.get("request_id") or ""),
                        f"{int(row.get('duration_ms') or 0)} ms",
                    ),
                )

        self.window.run_task(
            lambda _progress: self.repository.list_api_calls(
                store_app_id,
                limit=200,
            ),
            done,
            lambda error: self.window.set_status(
                f"API 调用记录读取失败：{error}",
                busy=False,
            ),
            log_title="读取动销 API 调用记录",
            task_scope=f"dongxiao:api-calls:{store_app_id or 'all'}",
            store_app_id=store_app_id,
        )

    def _actor_id(self) -> str:
        context = getattr(self.window, "auth_context", None)
        return str(
            getattr(context, "username", "")
            or getattr(context, "account_id", "")
            or "local-user"
        )

    def _permission_checker(self, permission: str) -> bool:
        context = getattr(self.window, "auth_context", None)
        if context is None:
            return False
        role = str(getattr(context, "member_role", "") or "")
        if role in {"owner", "admin"}:
            return True
        permissions = dict(getattr(context, "permissions", {}) or {})
        return bool(permissions.get(permission))


class ProductImportDialog(tk.Toplevel):
    def __init__(
        self,
        parent: DongxiaoPage,
        window,
        service: ProductImportService,
        store,
        *,
        on_saved,
    ) -> None:
        super().__init__(parent)
        self.parent_page = parent
        self.window = window
        self.service = service
        self.store = store
        self.on_saved = on_saved
        self.preview = None
        self.title("批量导入商品")
        self.geometry("900x600")
        self.transient(window)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        ttk.Label(
            self,
            text="每行输入商品 ID、HTTP/HTTPS 商品链接或原始微信小店分享文本。",
            padding=(12, 10),
        ).grid(row=0, column=0, sticky="ew")
        self.text = tk.Text(self, wrap="none", undo=True)
        self.text.grid(row=1, column=0, sticky="nsew", padx=12)
        results_frame = ttk.Frame(self)
        results_frame.grid(row=2, column=0, sticky="nsew", padx=12, pady=8)
        results_frame.columnconfigure(0, weight=1)
        results_frame.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(
            results_frame,
            columns=("line", "status", "product", "url", "reason"),
            show="headings",
            height=7,
        )
        for key, label, width in (
            ("line", "行", 50),
            ("status", "状态", 130),
            ("product", "商品 ID", 150),
            ("url", "规范链接", 260),
            ("reason", "说明", 260),
        ):
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width, anchor="w")
        self.tree.grid(row=0, column=0, sticky="nsew")
        self.summary = ttk.Label(self, text="尚未预览")
        self.summary.grid(row=3, column=0, sticky="w", padx=12)
        actions = ttk.Frame(self)
        actions.grid(row=4, column=0, sticky="e", padx=12, pady=12)
        button(actions, "预览", self._preview).pack(side="left")
        button(
            actions,
            "手动关联选中待解析行",
            self._manually_associate,
        ).pack(side="left", padx=(6, 0))
        self.save_button = button(
            actions,
            "保存导入结果",
            self._save,
            "Primary",
        )
        self.save_button.configure(state="disabled")
        self.save_button.pack(side="left", padx=6)
        button(actions, "关闭", self.destroy).pack(side="left")
        self._center(parent)

    def _center(self, parent) -> None:
        self.update_idletasks()
        x = parent.winfo_rootx() + max(20, (parent.winfo_width() - 900) // 2)
        y = parent.winfo_rooty() + max(20, (parent.winfo_height() - 600) // 2)
        self.geometry(f"+{x}+{y}")

    def _preview(self) -> None:
        text = self.text.get("1.0", "end")
        self.save_button.configure(state="disabled")
        self.window.run_task(
            lambda _progress: self.service.preview(text),
            self._render_preview,
            lambda error: messagebox.showerror(
                "商品导入预览失败",
                error,
                parent=self,
            ),
            log_title="预览商品导入",
            task_scope=f"dongxiao:import-preview:{self.store.id}",
        )

    def _render_preview(self, preview) -> None:
        self.preview = preview
        for row in self.tree.get_children():
            self.tree.delete(row)
        labels = {
            ProductImportStatus.READY: "可导入",
            ProductImportStatus.PENDING_RESOLUTION: "待解析",
            ProductImportStatus.INVALID: "错误",
            ProductImportStatus.DUPLICATE: "重复",
        }
        for item in preview.items:
            self.tree.insert(
                "",
                "end",
                iid=str(item.line_number),
                values=(
                    item.line_number,
                    labels[item.status],
                    item.product_id or "—",
                    item.canonical_url or "—",
                    item.reason or "—",
                ),
            )
        self.summary.configure(
            text=(
                f"可导入 {preview.ready_count} · "
                f"待解析 {preview.pending_count} · "
                f"错误 {preview.invalid_count} · "
                f"重复 {preview.duplicate_count}"
            )
        )
        self.save_button.configure(
            state=(
                "normal"
                if self.preview.items
                else "disabled"
            )
        )

    def _manually_associate(self) -> None:
        if self.preview is None:
            messagebox.showinfo(
                "手动关联",
                "请先预览导入内容。",
                parent=self,
            )
            return
        selected = self.tree.selection()
        if not selected:
            messagebox.showinfo(
                "手动关联",
                "请选择一条待解析行。",
                parent=self,
            )
            return
        product_id = simpledialog.askstring(
            "手动关联商品",
            "请输入当前店铺已授权商品目录中的商品 ID：",
            parent=self,
        )
        if product_id is None:
            return
        self.window.run_task(
            lambda _progress: self.service.associate(
                self.store,
                self.preview,
                line_number=int(selected[0]),
                product_id=product_id,
            ),
            self._render_preview,
            lambda error: messagebox.showerror(
                "手动关联失败",
                error,
                parent=self,
            ),
            log_title="手动关联微信小店分享文本",
            task_scope=f"dongxiao:import-link:{self.store.id}",
        )

    def _save(self) -> None:
        if self.preview is None:
            return
        self.save_button.configure(state="disabled")

        def done(result: dict[str, int]) -> None:
            self.window.set_status(
                f"商品导入记录已保存：{result['saved']} 行"
            )
            self.on_saved()
            self.destroy()

        def failed(error: str) -> None:
            self.save_button.configure(state="normal")
            messagebox.showerror(
                "保存商品导入失败",
                error,
                parent=self,
            )

        self.window.run_task(
            lambda _progress: self.service.persist(
                self.store,
                self.preview,
            ),
            done,
            failed,
            log_title="保存商品导入预览",
            task_scope=f"dongxiao:import-save:{self.store.id}",
        )
