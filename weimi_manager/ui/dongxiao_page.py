from __future__ import annotations

import tkinter as tk
from datetime import UTC, date, datetime, time
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Any

from ..dongxiao import DONGXIAO_SECTIONS
from ..dongxiao.application.catalog_service import ProductCatalogService
from ..dongxiao.application.import_service import ProductImportService
from ..dongxiao.application.pool_service import ProductPoolService
from ..dongxiao.domain.catalog import (
    CatalogFreshness,
    classify_catalog_freshness,
)
from ..dongxiao.domain.enums import (
    DataSource,
    ProductImportStatus,
    ProductState,
)
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


def _money(cents: int | None) -> str:
    if cents is None:
        return "—"
    value = int(cents)
    return f"¥{value // 100}.{value % 100:02d}"


def _date_time(value: datetime | None) -> str:
    return value.strftime("%Y-%m-%d %H:%M") if value else "—"


def _day_start(value: date) -> datetime:
    return datetime.combine(value, time.min, tzinfo=UTC)


def _day_end(value: date) -> datetime:
    return datetime.combine(value, time.max, tzinfo=UTC)


class DongxiaoPage(BasePage):
    title = "动销中心"
    subtitle = "商品目录、SKU 规则与商品池"

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
        self._verified_product_id = ""
        self._verified_sku_id = ""
        self._page = 1
        self._page_count = 1

        database_path = Path(getattr(self.db, "path"))
        self.repository = DongxiaoRepository(database_path)
        self.pool_service = ProductPoolService(self.repository)
        self.import_service = ProductImportService(self.repository)
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
        self.product_page_var = tk.StringVar(value="商品 0 条")
        self.pool_stats_var = tk.StringVar(value="商品池尚未选择")
        self.sku_result_var = tk.StringVar(value="请选择商品后核验 SKU")

        self.filter_keyword_var = tk.StringVar()
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

        self._build_header()
        self._build_workspace()
        self.show_section("overview")

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
        tk.Label(
            navigation,
            text="只读商品同步与本地管理。\n不会加入购物车或下单。",
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
        for key in ("tasks", "orders", "aftersales", "audit"):
            self.section_views[key] = self._build_placeholder(key)
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
        for index, (label, value) in enumerate(
            (
                ("店铺", "—"),
                ("已缓存商品", "—"),
                ("商品池", "—"),
                ("可信销量字段", "待同步"),
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
                text=value,
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
            "第 3.5 阶段：商品目录专项验收、缓存状态和数据一致性补强。"
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

    def refresh(self) -> None:
        self._load_stores()

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
            self.store_var.set(labels[0])
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
        self.active_store_var.set(f"当前店铺：{store.name}")
        self._page = 1
        self._load_products()
        self._load_pools()

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
        return ProductFilter(
            keyword=self.filter_keyword_var.get(),
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
            sales_max=self._parse_optional_int(
                self.filter_sales_max_var.get(),
                "最高销量",
            ),
            listed_from=self._parse_optional_date(
                self.filter_listed_from_var.get(),
                "上架起始日期",
                end=False,
            ),
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
