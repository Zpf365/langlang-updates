from __future__ import annotations

import json
import tempfile
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import TYPE_CHECKING, Any

from openpyxl import Workbook

from ..exporter import export_xlsx
from ..models import Store
from .dialogs import CouponFormDialog
from .pages import BasePage
from .theme import BG, MUTED, button

if TYPE_CHECKING:
    from .app import MainWindow


COUPON_TYPES = {
    1: "商品条件折扣",
    2: "商品满减",
    3: "商品统一折扣",
    4: "商品直减",
    101: "店铺条件折扣",
    102: "店铺满减",
    103: "店铺统一折扣",
    104: "店铺直减",
}
COUPON_STATUSES = {
    1: "编辑中",
    2: "已生效",
    3: "已过期",
    4: "已作废",
    5: "已删除",
}
ACTIVITY_STATES = {1: "进行中", 2: "已结束"}
APPLY_STATES = {1: "可报名", 2: "已报名", 3: "已结束"}
SNAPUP_STATUSES = {0: "待开始", 1: "生效中", 2: "已结束"}


def _money(cents: Any) -> str:
    return f"¥{int(cents or 0) / 100:.2f}"


def _date(timestamp: Any) -> str:
    value = int(timestamp or 0)
    if value <= 0:
        return "—"
    return datetime.fromtimestamp(value).strftime("%Y-%m-%d")


def _date_time(timestamp: Any) -> str:
    value = int(timestamp or 0)
    if value <= 0:
        return "—"
    return datetime.fromtimestamp(value).strftime("%Y-%m-%d %H:%M")


def _record_activity_id(value: Any) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


class ActivityCenterPage(BasePage):
    title = "活动中心"
    subtitle = "平台活动、限时限量购、优惠券和续期管理"

    def __init__(self, parent: tk.Widget, window: "MainWindow"):
        super().__init__(parent, window)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)
        self._stores: dict[int, Store] = {}
        self._selection_initialized = False
        self._activity_catalog: list[dict[str, Any]] = []
        self._activity_previews: list[dict[str, Any]] = []
        self._auto_preview_job: str | None = None
        self._preview_selected: set[str] = set()
        self._preview_rows: dict[str, dict[str, Any]] = {}
        self._limited_preview_rows: list[dict[str, Any]] = []
        self._limited_preview_page = 1
        self._limited_preview_errors: list[str] = []
        self._coupon_rows: dict[str, dict[str, Any]] = {}
        self._snapup_rows: dict[str, dict[str, Any]] = {}
        self._coupon_templates: set[tuple[int, str]] = set()
        self._source_only = False

        store_box = ttk.Frame(self, style="Panel.TFrame", padding=(12, 10))
        store_box.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        store_box.columnconfigure(0, weight=1)
        ttk.Label(
            store_box,
            text="第1步 · 选择要申报的店铺",
            style="Section.TLabel",
        ).grid(row=0, column=0, sticky="w")
        actions = ttk.Frame(store_box, style="Panel.TFrame")
        actions.grid(row=0, column=1, sticky="e")
        button(actions, "全选", self._select_all).pack(side="left")
        button(actions, "清空", self._select_none).pack(side="left", padx=6)
        ttk.Label(
            actions,
            text="未登录、已掉线、无商品、注销状态会显示原因并安全跳过",
            style="Panel.TLabel",
            foreground=MUTED,
        ).pack(side="left", padx=10)
        self.store_tree = ttk.Treeview(
            store_box,
            columns=("name", "web", "api", "products", "group"),
            show="headings",
            selectmode="extended",
            height=5,
        )
        for key, label, width in (
            ("name", "店铺", 220),
            ("web", "网页登录", 100),
            ("api", "接口", 100),
            ("products", "商品数", 80),
            ("group", "分组", 120),
        ):
            self.store_tree.heading(key, text=label)
            self.store_tree.column(key, width=width, anchor="w")
        self.store_tree.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0))

        self.selection_var = tk.StringVar(value="未选择店铺")
        ttk.Label(
            self,
            textvariable=self.selection_var,
            style="Subtitle.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(0, 6))

        self.notebook = ttk.Notebook(self)
        self.notebook.grid(row=2, column=0, sticky="nsew")
        self.activity_tab = ttk.Frame(self.notebook, padding=(10, 10))
        self.limited_activity_tab = ttk.Frame(self.notebook, padding=(10, 10))
        self.coupon_activity_tab = ttk.Frame(self.notebook, padding=(10, 10))
        self.snapup_tab = ttk.Frame(self.notebook, padding=(10, 10))
        self.coupon_tab = ttk.Frame(self.notebook, padding=(10, 10))
        self.records_tab = ttk.Frame(self.notebook, padding=(10, 10))
        self.notebook.add(self.activity_tab, text="平台活动")
        self.notebook.add(self.limited_activity_tab, text="限时限量购活动")
        self.notebook.add(self.coupon_activity_tab, text="优惠券活动")
        self.notebook.add(self.snapup_tab, text="限时限量购续期")
        self.notebook.add(self.coupon_tab, text="优惠券管理")
        self.notebook.add(self.records_tab, text="执行记录")
        self._build_activity_tab()
        self._build_limited_activity_tab()
        self._build_coupon_activity_tab()
        self._build_snapup_tab()
        self._build_coupon_tab()
        self._build_records_tab()
        self.notebook.bind("<<NotebookTabChanged>>", self._tab_changed)

    def _selected_stores(self) -> list[Store]:
        selected = [
            int(iid)
            for iid in self.store_tree.selection()
            if iid.isdigit() and int(iid) in self._stores
        ]
        return [self._stores[store_id] for store_id in selected]

    def _require_stores(self) -> list[Store] | None:
        stores = self._selected_stores()
        if not stores:
            messagebox.showinfo(
                "请选择店铺",
                "请先在顶部选择至少一家店铺。",
                parent=self,
            )
            return None
        return stores

    def _select_all(self) -> None:
        self.store_tree.selection_set(tuple(str(key) for key in self._stores))
        self._update_selection_label()

    def _select_none(self) -> None:
        self.store_tree.selection_remove(*self.store_tree.selection())
        self._update_selection_label()

    def _update_selection_label(self, _event=None) -> None:
        selected = self._selected_stores()
        online = sum(store.web_status == "已登录" for store in selected)
        self.selection_var.set(
            f"已选择 {len(selected)} 家 · 网页已登录 {online} 家 · "
            f"API 可用 {sum(store.status == '连接成功' for store in selected)} 家"
        )

    def refresh(self) -> None:
        previous = set(self.store_tree.selection())
        stores = self.db.list_stores()
        self._stores = {store.id: store for store in stores}
        metrics = self.db.store_metric_map()
        self.store_tree.delete(*self.store_tree.get_children())
        for store in stores:
            self.store_tree.insert(
                "",
                "end",
                iid=str(store.id),
                values=(
                    store.name,
                    store.web_status,
                    store.status,
                    int(metrics.get(store.id, {}).get("product_total") or 0),
                    store.group_name or "未分组",
                ),
            )
        keep = [value for value in previous if value in self.store_tree.get_children()]
        if keep:
            self.store_tree.selection_set(keep)
        elif not self._selection_initialized:
            self._select_all()
            self._selection_initialized = True
        self._update_selection_label()
        logged = [store for store in stores if store.web_status == "已登录"]
        values = [f"{store.name} · #{store.id}" for store in logged]
        self.activity_source_combo["values"] = values
        self.coupon_source_combo["values"] = [
            f"{store.name} · #{store.id}" for store in stores
        ]
        if self.activity_source_var.get() not in values:
            saved_source = self.db.get_setting(
                "activity_catalog_source_store_id",
                "",
            )
            preferred = next(
                (
                    value
                    for value in values
                    if value.endswith(f"#{saved_source}")
                ),
                "",
            )
            self.activity_source_var.set(
                preferred or (values[0] if values else "")
            )
        coupon_values = list(self.coupon_source_combo["values"])
        if self.coupon_source_var.get() not in coupon_values:
            self.coupon_source_var.set(
                coupon_values[0] if coupon_values else ""
            )
        if not self._source_only:
            self._load_coupons()
        if hasattr(self, "records_tree"):
            self._load_operation_records()

    def _store_from_label(self, label: str) -> Store | None:
        try:
            return self._stores.get(int(label.rsplit("#", 1)[1]))
        except (IndexError, ValueError):
            return None

    def _build_activity_tab(self) -> None:
        tab = self._make_scrollable_tab(self.activity_tab)
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(6, weight=1)
        source = ttk.Frame(tab, style="TFrame")
        source.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        ttk.Label(source, text="活动目录基准店铺").pack(side="left")
        self.activity_source_var = tk.StringVar()
        self.activity_source_combo = ttk.Combobox(
            source,
            textvariable=self.activity_source_var,
            state="readonly",
            width=28,
        )
        self.activity_source_combo.pack(side="left", padx=(6, 8))
        button(
            source,
            "1-1 读取平台活动列表",
            self._load_activity_catalog,
            "Primary",
        ).pack(side="left")
        self.activity_filter_var = tk.StringVar(value="当前可报名")
        ttk.Combobox(
            source,
            textvariable=self.activity_filter_var,
            state="readonly",
            width=14,
            values=("当前可报名", "限时限量购", "全部活动", "已结束"),
        ).pack(side="left", padx=(8, 4))
        self.activity_search_var = tk.StringVar()
        search = ttk.Entry(
            source,
            textvariable=self.activity_search_var,
            width=18,
        )
        search.pack(side="left", padx=(0, 4))
        search.bind("<Return>", lambda _event: self._render_activity_catalog())
        button(
            source,
            "筛选",
            self._render_activity_catalog,
        ).pack(side="left")
        self.activity_catalog_var = tk.StringVar(
            value="活动列表尚未读取，只会通过这里指定的一家基准店铺读取。"
        )
        ttk.Label(
            tab,
            textvariable=self.activity_catalog_var,
            style="Subtitle.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(0, 6))

        self.activity_tree = ttk.Treeview(
            tab,
            columns=("name", "id", "state", "apply", "apply_end"),
            show="headings",
            selectmode="browse",
            height=6,
        )
        for key, label, width in (
            ("name", "活动名称", 360),
            ("id", "活动ID", 90),
            ("state", "活动状态", 90),
            ("apply", "报名状态", 90),
            ("apply_end", "报名截止", 100),
        ):
            self.activity_tree.heading(key, text=label)
            self.activity_tree.column(key, width=width, anchor="w")
        self.activity_tree.grid(row=2, column=0, sticky="ew")
        self.activity_tree.bind("<<TreeviewSelect>>", self._activity_selected)

        controls = ttk.Frame(tab, style="TFrame")
        controls.grid(row=3, column=0, sticky="ew", pady=(8, 6))
        ttk.Label(controls, text="活动ID").pack(side="left")
        self.activity_id_var = tk.StringVar(
            value=self.db.get_setting("activity_center_last_id", "1323")
        )
        ttk.Entry(controls, textvariable=self.activity_id_var, width=12).pack(
            side="left", padx=(4, 10)
        )
        ttk.Label(controls, text="统一折扣（折）").pack(side="left")
        self.discount_var = tk.StringVar(
            value=self.db.get_setting("activity_discount", "9.0")
        )
        ttk.Entry(controls, textvariable=self.discount_var, width=7).pack(
            side="left", padx=(4, 10)
        )
        ttk.Label(controls, text="最低优惠（元）").pack(side="left")
        self.min_discount_var = tk.StringVar(
            value=self.db.get_setting("activity_min_discount", "1.00")
        )
        ttk.Entry(controls, textvariable=self.min_discount_var, width=7).pack(
            side="left", padx=(4, 10)
        )
        self.activity_summary_var = tk.StringVar(value="尚未预览")
        ttk.Label(
            controls,
            textvariable=self.activity_summary_var,
            style="Subtitle.TLabel",
        ).pack(side="right")

        activity_ops = ttk.Frame(tab, style="TFrame")
        activity_ops.grid(row=4, column=0, sticky="ew", pady=(0, 4))
        ttk.Label(
            activity_ops,
            text="第2步 · 活动准备",
            style="Subtitle.TLabel",
        ).pack(side="left", padx=(0, 8))
        button(
            activity_ops,
            "2-1 同步所选商品",
            self._sync_activity_products,
        ).pack(side="left")
        button(
            activity_ops,
            "2-2 批量申报活动",
            self._apply_activity,
            "Primary",
        ).pack(side="left", padx=6)

        self.activity_preview_tree = ttk.Treeview(
            tab,
            columns=(
                "selected",
                "store",
                "product",
                "sku",
                "current",
                "activity",
                "discount",
                "rate",
                "status",
            ),
            show="headings",
            height=10,
            selectmode="browse",
        )
        for key, label, width in (
            ("selected", "选", 36),
            ("store", "店铺", 140),
            ("product", "商品", 230),
            ("sku", "SKU", 110),
            ("current", "原价", 85),
            ("activity", "活动价", 85),
            ("discount", "优惠", 80),
            ("rate", "实际折扣", 85),
            ("status", "预检结果", 180),
        ):
            self.activity_preview_tree.heading(key, text=label)
            self.activity_preview_tree.column(key, width=width, anchor="w")
        self.activity_preview_tree.grid(row=6, column=0, sticky="nsew")
        self.activity_preview_tree.bind(
            "<Button-1>",
            self._toggle_activity_preview_row,
        )
        self.activity_preview_tree.insert(
            "",
            "end",
            iid="preview_hint",
            values=(
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "选择活动后将自动加载商品，也可点击“加载活动商品”。",
            ),
        )
        preview_actions = ttk.Frame(tab, style="TFrame")
        preview_actions.grid(row=5, column=0, sticky="ew", pady=(0, 4))
        ttk.Label(
            preview_actions,
            text="第3步 · 商品报名",
            style="Subtitle.TLabel",
        ).pack(side="left", padx=(0, 8))
        button(
            preview_actions,
            "3-1 加载活动商品",
            self._preview_activity,
            "Primary",
        ).pack(side="left")
        button(
            preview_actions,
            "3-2 提交已选SKU",
            self._submit_activity,
            "Danger",
        ).pack(side="left", padx=6)
        button(
            preview_actions,
            "全选可报名",
            self._select_all_preview_rows,
        ).pack(side="left", padx=(6, 0))
        button(
            preview_actions,
            "清空选择",
            self._clear_preview_selection,
        ).pack(side="left", padx=6)
        button(
            preview_actions,
            "导出预览",
            self._export_activity_preview,
        ).pack(side="left")
        self.preview_selection_var = tk.StringVar(value="已选 0 个SKU")
        ttk.Label(
            preview_actions,
            textvariable=self.preview_selection_var,
            style="Subtitle.TLabel",
        ).pack(side="right")

    def _tab_changed(self, _event=None) -> None:
        selected = self.notebook.select()
        if selected == str(self.records_tab):
            self._load_operation_records()

    def _activity_selected(self, _event=None) -> None:
        selected = self.activity_tree.selection()
        if selected:
            self.activity_id_var.set(selected[0])
            if self._auto_preview_job is not None:
                try:
                    self.after_cancel(self._auto_preview_job)
                except tk.TclError:
                    pass
            self._auto_preview_job = self.after(250, self._auto_preview)

    def _auto_preview(self) -> None:
        self._auto_preview_job = None
        if self._selected_stores() and self.activity_id_var.get().strip():
            self._preview_activity()

    def _load_activity_catalog(self) -> None:
        store = self._store_from_label(self.activity_source_var.get())
        if store is None:
            messagebox.showinfo(
                "没有已登录店铺",
                "请先选择一家网页已登录店铺作为活动目录基准店铺。",
                parent=self,
            )
            return
        if store.web_status != "已登录":
            messagebox.showwarning(
                "基准店铺未登录",
                f"“{store.name}”当前为{store.web_status}，"
                "请先在店铺管理重新登录。",
                parent=self,
            )
            return
        self.activity_catalog_var.set(
            f"正在通过“{store.name}”读取平台活动列表…"
        )
        self.window.set_status(
            f"正在通过“{store.name}”读取平台活动列表…",
            busy=True,
        )
        self.update_idletasks()

        def done(result: dict[str, Any]) -> None:
            self._activity_catalog = list(result.get("activities") or [])
            self.db.set_setting(
                "activity_catalog_source_store_id",
                str(store.id),
            )
            self._activity_source_name = store.name
            self._activity_fetched_at = int(result.get("fetched_at") or 0)
            self._render_activity_catalog()
            reportable = sum(
                1
                for item in self._activity_catalog
                if int(item.get("activity_state") or 0) == 1
                and int(item.get("apply_state") or 0) in {1, 2}
            )
            self.activity_catalog_var.set(
                f"读取成功：{store.name} · 共 {len(self._activity_catalog)} 个活动"
                f" · 当前可报名 {reportable} 个"
            )
            self.window.set_status(
                f"平台活动列表已读取：{len(self._activity_catalog)} 个"
            )

        started = self.window.run_task(
            lambda _progress: self.window.service.platform_activity_catalog(
                store
            ),
            done,
            lambda error: self._activity_catalog_failed(error),
            log_title="读取平台活动列表",
        )
        if not started:
            self.activity_catalog_var.set(
                "读取未启动：已有任务正在执行，请等待完成后再试。"
            )
            self.window.set_status(
                "活动列表未读取：已有任务正在执行",
                busy=False,
            )

    def _activity_catalog_failed(self, error: str) -> None:
        self.activity_catalog_var.set(f"读取失败：{error}")
        self.window.set_status("平台活动列表读取失败", busy=False)
        messagebox.showerror(
            "读取平台活动失败",
            error,
            parent=self,
        )

    def _render_activity_catalog(self) -> None:
        mode = self.activity_filter_var.get()
        keyword = self.activity_search_var.get().strip().casefold()
        visible = []
        for item in self._activity_catalog:
            activity_id = str(item.get("activity_id") or "")
            if not activity_id:
                continue
            state = int(item.get("activity_state") or 0)
            apply_state = int(item.get("apply_state") or 0)
            name = str(item.get("name") or "")
            if mode == "当前可报名":
                if state != 1 or apply_state not in {1, 2}:
                    continue
            elif mode == "限时限量购":
                if not any(
                    word in name
                    for word in ("限时", "限量", "秒杀", "抢购")
                ):
                    continue
            elif mode == "已结束":
                if state != 2:
                    continue
            if keyword and keyword not in name.casefold() and keyword not in activity_id:
                continue
            visible.append(item)
        self.activity_tree.delete(*self.activity_tree.get_children())
        for item in visible:
            activity_id = str(item.get("activity_id") or "")
            self.activity_tree.insert(
                "",
                "end",
                iid=activity_id,
                values=(
                    item.get("name") or "",
                    activity_id,
                    ACTIVITY_STATES.get(
                        int(item.get("activity_state") or 0),
                        str(item.get("activity_state") or ""),
                    ),
                    APPLY_STATES.get(
                        int(item.get("apply_state") or 0),
                        str(item.get("apply_state") or ""),
                    ),
                    _date(item.get("apply_end_time")),
                ),
            )
        source_name = getattr(self, "_activity_source_name", "")
        fetched_at = getattr(self, "_activity_fetched_at", 0)
        if self._activity_catalog:
            self.activity_catalog_var.set(
                f"{source_name} · {_date(fetched_at)} · "
                f"显示 {len(visible)} / 全部 {len(self._activity_catalog)}"
            )
        else:
            self.activity_catalog_var.set("活动列表尚未读取")

    def _activity_parameters(self) -> tuple[int, int, int] | None:
        try:
            activity_id = int(self.activity_id_var.get().strip())
            discount = float(self.discount_var.get().strip())
            minimum = float(self.min_discount_var.get().strip())
        except ValueError:
            messagebox.showwarning(
                "参数错误",
                "活动ID、折扣和最低优惠必须填写正确数字。",
                parent=self,
            )
            return None
        if activity_id <= 0 or not 0 < discount <= 10 or minimum < 0:
            messagebox.showwarning(
                "参数错误",
                "折扣必须在0–10之间，最低优惠不能小于0。",
                parent=self,
            )
            return None
        self.db.set_setting("activity_center_last_id", str(activity_id))
        self.db.set_setting("activity_discount", f"{discount:g}")
        self.db.set_setting("activity_min_discount", f"{minimum:.2f}")
        return activity_id, int(round(discount * 1000)), int(round(minimum * 100))

    def _sync_activity_products(self) -> None:
        stores = self._require_stores()
        if stores is None:
            return
        self.window.set_status("正在同步所选店铺商品…", busy=True)

        def work(progress):
            results = []
            for index, store in enumerate(stores, 1):
                if progress:
                    progress(
                        f"正在同步 {store.name} 商品 {index}/{len(stores)}",
                        index,
                        len(stores),
                    )
                try:
                    result = self.window.service.sync_products(
                        store,
                        refresh_catalog=True,
                    )
                    product_count = int(
                        self.db.product_catalog_stats(store.id)["total"]
                    )
                    results.append(
                        {
                            "ok": True,
                            "store": store.name,
                            "count": product_count,
                            "message": result.message,
                        }
                    )
                except Exception as exc:
                    results.append(
                        {
                            "ok": False,
                            "store": store.name,
                            "count": 0,
                            "message": str(exc),
                        }
                    )
            return results

        def done(results: list[dict[str, Any]]) -> None:
            success = sum(bool(item["ok"]) for item in results)
            total = sum(int(item["count"] or 0) for item in results)
            self.window.set_status(
                f"商品同步完成：成功 {success} 家，共 {total} 个商品"
            )
            self.window.store_data_changed()
            if self.activity_id_var.get().strip():
                self.after_idle(self._preview_activity)
            failures = [
                f"{item['store']}：{item['message']}"
                for item in results
                if not item["ok"]
            ]
            if failures:
                messagebox.showwarning(
                    "部分店铺商品同步失败",
                    "\n".join(failures[:10]),
                    parent=self,
                )

        self.window.run_task(
            work,
            done,
            lambda error: messagebox.showerror(
                "活动商品同步失败",
                error,
                parent=self,
            ),
            log_title="同步平台活动商品",
        )

    def _apply_activity(self) -> None:
        stores = self._require_stores()
        params = self._activity_parameters()
        if stores is None or params is None:
            return
        activity_id, _discount_bp, _minimum = params
        eligible = [store for store in stores if store.web_status == "已登录"]
        skipped = [store for store in stores if store.web_status != "已登录"]
        if not eligible:
            messagebox.showinfo(
                "没有可申报店铺",
                "所选店铺均未登录网页后台，无法申报平台活动。",
                parent=self,
            )
            return
        if not messagebox.askyesno(
            "确认申报平台活动",
            f"将为 {len(eligible)} 家已登录店铺申报活动 {activity_id}。\n"
            + (
                f"另有 {len(skipped)} 家未登录店铺会跳过。\n"
                if skipped
                else ""
            )
            + "店铺申报成功后，还需要预览并提交活动商品价格。是否继续？",
            parent=self,
        ):
            return
        self.window.set_status("正在批量申报平台活动…", busy=True)

        def work(progress):
            results = []
            for index, store in enumerate(eligible, 1):
                if progress:
                    progress(
                        f"正在申报 {store.name} {index}/{len(eligible)}",
                        index,
                        len(eligible),
                    )
                try:
                    result = (
                        self.window.web_session.apply_platform_activity_page(
                            store,
                            activity_id,
                        )
                    )
                    results.append(
                        {
                            "ok": True,
                            "store": store.name,
                            "result": result,
                            "message": result.get("message") or "报名成功",
                        }
                    )
                except Exception as exc:
                    results.append(
                        {
                            "ok": False,
                            "store": store.name,
                            "error": str(exc),
                        }
                    )
            return results

        def done(results: list[dict[str, Any]]) -> None:
            success = sum(bool(item["ok"]) for item in results)
            failed = len(results) - success
            self.window.set_status(
                f"平台活动申报完成：成功 {success} 家，失败 {failed} 家"
            )
            lines = [
                f"{item['store']}："
                + (
                    (item.get("message") or "申报成功")
                    if item["ok"]
                    else item["error"]
                )
                for item in results
            ]
            if skipped:
                lines.append(
                    "跳过：" + "、".join(store.name for store in skipped)
                )
            messagebox.showinfo(
                "平台活动申报结果",
                "\n".join(lines[:20]),
                parent=self,
            )

        self.window.run_task(
            work,
            done,
            lambda error: messagebox.showerror(
                "批量申报平台活动失败",
                error,
                parent=self,
            ),
            log_title="批量申报平台活动",
        )

    def _preview_activity(self) -> None:
        stores = self._require_stores()
        params = self._activity_parameters()
        if stores is None or params is None:
            return
        activity_id, discount_bp, min_discount = params
        self.window.set_status("正在逐店预检平台活动…", busy=True)

        def work(progress):
            results = []
            for index, store in enumerate(stores, 1):
                if progress:
                    progress(
                        f"正在预检 {store.name} {index}/{len(stores)}",
                        index,
                        len(stores),
                    )
                results.append(
                    self.window.service.activity_preview(
                        store,
                        activity_id,
                        discount_bp,
                        min_discount,
                    )
                )
            return results

        def done(results: list[dict[str, Any]]) -> None:
            self._activity_previews = results
            self._preview_selected.clear()
            self._preview_rows.clear()
            self.activity_preview_tree.delete(
                *self.activity_preview_tree.get_children()
            )
            valid = conflicts = skipped = 0
            for result in results:
                status = str(result.get("status") or "")
                if status != "可预览":
                    skipped += 1
                    self.activity_preview_tree.insert(
                        "",
                        "end",
                        iid=f"skip:{result.get('store_id')}",
                        values=(
                            "",
                            result.get("store_name") or "",
                            "—",
                            "—",
                            "—",
                            "—",
                            "—",
                            "—",
                            result.get("reason") or "已跳过",
                        ),
                    )
                    continue
                valid += int(result.get("valid_sku_count") or 0)
                conflicts += int(result.get("conflict_count") or 0)
                for index, row in enumerate(result.get("rows") or []):
                    iid = (
                        f"data:{row.get('store_id')}:"
                        f"{row.get('product_id')}:{row.get('sku_id')}:{index}"
                    )
                    self._preview_rows[iid] = dict(row)
                    if row.get("status") == "可报名":
                        self._preview_selected.add(iid)
                    self.activity_preview_tree.insert(
                        "",
                        "end",
                        iid=iid,
                        values=(
                            "☑" if iid in self._preview_selected else "☐",
                            row.get("store_name") or "",
                            row.get("title") or row.get("product_id") or "",
                            row.get("sku_id") or "—",
                            _money(row.get("current_price")),
                            _money(row.get("activity_price")),
                            _money(row.get("discount_cents")),
                            f"{float(row.get('actual_discount') or 0):.2f} 折",
                            row.get("status") or "",
                        ),
                    )
            self.activity_summary_var.set(
                f"可报名 {valid} 个SKU · 冲突 {conflicts} 个 · "
                f"跳过店铺 {skipped} 家"
            )
            self._update_preview_selection_label()
            self.window.set_status("平台活动预检完成")

        self.window.run_task(
            work,
            done,
            lambda error: messagebox.showerror(
                "平台活动预检失败",
                error,
                parent=self,
            ),
            log_title="平台活动批量预检",
        )

    def _toggle_activity_preview_row(self, event: tk.Event) -> str | None:
        iid = self.activity_preview_tree.identify_row(event.y)
        column = self.activity_preview_tree.identify_column(event.x)
        if iid not in self._preview_rows or column != "#1":
            return None
        if iid in self._preview_selected:
            self._preview_selected.remove(iid)
        else:
            self._preview_selected.add(iid)
        self._refresh_preview_row_mark(iid)
        self._update_preview_selection_label()
        return "break"

    def _refresh_preview_row_mark(self, iid: str) -> None:
        values = list(self.activity_preview_tree.item(iid, "values"))
        if not values:
            return
        values[0] = "☑" if iid in self._preview_selected else "☐"
        self.activity_preview_tree.item(iid, values=values)

    def _update_preview_selection_label(self) -> None:
        self.preview_selection_var.set(
            f"已选 {len(self._preview_selected)} 个SKU"
        )

    def _select_all_preview_rows(self) -> None:
        self._preview_selected = {
            iid
            for iid, row in self._preview_rows.items()
            if row.get("status") == "可报名"
        }
        for iid in self._preview_rows:
            self._refresh_preview_row_mark(iid)
        self._update_preview_selection_label()

    def _clear_preview_selection(self) -> None:
        self._preview_selected.clear()
        for iid in self._preview_rows:
            self._refresh_preview_row_mark(iid)
        self._update_preview_selection_label()

    def _export_activity_preview(self) -> None:
        if not self._activity_previews:
            messagebox.showinfo(
                "没有预览结果",
                "请先执行批量预览。",
                parent=self,
            )
            return
        path = filedialog.asksaveasfilename(
            parent=self,
            title="导出活动预检结果",
            defaultextension=".xlsx",
            filetypes=[("Excel 工作簿", "*.xlsx")],
            initialfile="活动预检结果.xlsx",
        )
        if not path:
            return
        rows = []
        for result in self._activity_previews:
            if result.get("status") != "可预览":
                rows.append(
                    [
                        result.get("store_name") or "",
                        "",
                        "",
                        "",
                        "",
                        "",
                        "",
                        "",
                        result.get("reason") or "跳过",
                    ]
                )
                continue
            for row in result.get("rows") or []:
                rows.append(
                    [
                        row.get("store_name") or "",
                        row.get("product_id") or "",
                        row.get("title") or "",
                        row.get("sku_id") or "",
                        int(row.get("current_price") or 0) / 100,
                        int(row.get("activity_price") or 0) / 100,
                        int(row.get("discount_cents") or 0) / 100,
                        float(row.get("actual_discount") or 0),
                        row.get("status") or "",
                    ]
                )
        export_xlsx(
            path,
            "活动预检",
            [
                "店铺",
                "商品ID",
                "商品标题",
                "SKU ID",
                "原价",
                "活动价",
                "优惠金额",
                "实际折扣",
                "预检结果",
            ],
            rows,
        )
        self.window.set_status(f"活动预检已导出：{path}")

    def _submit_activity(self) -> None:
        params = self._activity_parameters()
        if params is None:
            return
        activity_id, _discount_bp, _minimum = params
        groups: dict[int, list[dict[str, Any]]] = {}
        for result in self._activity_previews:
            rows = []
            for index, row in enumerate(result.get("rows") or []):
                iid = (
                    f"data:{row.get('store_id')}:"
                    f"{row.get('product_id')}:{row.get('sku_id')}:{index}"
                )
                if (
                    row.get("status") == "可报名"
                    and iid in self._preview_selected
                ):
                    rows.append(row)
            if not rows:
                continue
            groups[int(result["store_id"])] = [
                {
                    "productId": str(row["product_id"]),
                    "skuId": str(row["sku_id"]),
                    "newActivityPrice": int(row["activity_price"]),
                    "applyType": 1,
                }
                for row in rows
            ]
        if not groups:
            messagebox.showinfo(
                "没有可提交商品",
                "请先批量预览，并勾选状态为“可报名”的SKU。",
                parent=self,
            )
            return
        count = sum(len(items) for items in groups.values())
        if not messagebox.askyesno(
            "确认平台批量提报",
            f"将通过微信原版“批量导入商品”向 {len(groups)} 家店铺"
            f"提交 {count} 个SKU。\n"
            f"活动ID：{activity_id}\n"
            "软件会生成平台官方三列表格，自动上传、解析、"
            "勾选协议并确认。是否继续？",
            parent=self,
        ):
            return
        self.window.set_status("正在提交平台活动…", busy=True)

        def work(progress):
            results = []
            for index, (store_id, items) in enumerate(groups.items(), 1):
                store = self._stores.get(store_id)
                if store is None:
                    continue
                if progress:
                    progress(
                        f"正在提交 {store.name} {index}/{len(groups)}",
                        index,
                        len(groups),
                    )
                try:
                    if store.web_status != "已登录":
                        raise ValueError(f"网页{store.web_status}")
                    store_results = []
                    for batch_index, batch in enumerate(
                        self._split_activity_product_batches(items),
                        1,
                    ):
                        with tempfile.TemporaryDirectory(
                            prefix=f"langlang_activity_{store.id}_"
                        ) as temp_dir:
                            template_path = (
                                Path(temp_dir)
                                / (
                                    f"activity_{activity_id}_"
                                    f"store_{store.id}_{batch_index}.xlsx"
                                )
                            )
                            workbook = Workbook()
                            sheet = workbook.active
                            sheet.title = "sheet1"
                            sheet.append(
                                [
                                    "商品ID（SPU id）",
                                    "规格ID（SKU id）",
                                    "活动报名价（元）",
                                ]
                            )
                            for item in batch:
                                sheet.append(
                                    [
                                        item["productId"],
                                        item["skuId"],
                                        item["newActivityPrice"] / 100,
                                    ]
                                )
                            workbook.save(template_path)
                            store_results.append(
                                self.window.web_session
                                .bulk_register_activity_products(
                                    store,
                                    activity_id,
                                    template_path,
                                    expected_count=len(batch),
                                )
                            )
                    results.append(
                        {
                            "ok": True,
                            "store": store.name,
                            "result": {
                                "batches": store_results,
                                "count": sum(
                                    int(batch.get("count") or 0)
                                    for batch in store_results
                                ),
                            },
                        }
                    )
                except Exception as exc:
                    results.append(
                        {
                            "ok": False,
                            "store": store.name,
                            "error": str(exc),
                        }
                    )
            return results

        def done(results: list[dict[str, Any]]) -> None:
            success = sum(bool(item["ok"]) for item in results)
            failed = len(results) - success
            self.window.set_status(
                f"活动提交完成：成功 {success} 家，失败 {failed} 家"
            )
            messagebox.showinfo(
                "活动提交结果",
                f"成功 {success} 家，失败 {failed} 家。\n\n"
                + "\n".join(
                    (
                        f"{item['store']}：成功"
                        if item["ok"]
                        else f"{item['store']}：{item['error']}"
                    )
                    for item in results[:20]
                ),
                parent=self,
            )
            self._preview_activity()

        self.window.run_task(
            work,
            done,
            lambda error: messagebox.showerror(
                "活动提交失败",
                error,
                parent=self,
            ),
            log_title="提交平台活动商品",
        )

    @staticmethod
    def _split_activity_product_batches(
        items: list[dict[str, Any]],
    ) -> list[list[dict[str, Any]]]:
        # The merchant backend silently confirms at most 200 rows per import.
        return [
            items[index : index + 200]
            for index in range(0, len(items), 200)
        ]

    def _build_limited_activity_tab(self) -> None:
        tab = self._make_scrollable_tab(self.limited_activity_tab)
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(5, weight=1)
        self._limited_product_rows: dict[str, dict[str, Any]] = {}

        intro = ttk.Frame(tab, style="TFrame")
        intro.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ttk.Label(
            intro,
            text=(
                "选择上方店铺后设置折扣和最低优惠。可先预览商品，"
                "再按选中商品或整店在售商品创建限时限量购活动。"
            ),
            style="Subtitle.TLabel",
            wraplength=1040,
            justify="left",
        ).pack(side="left")

        settings = ttk.Frame(tab, style="TFrame")
        settings.grid(row=1, column=0, sticky="ew", pady=(0, 6))
        ttk.Label(settings, text="统一折扣（折）").pack(side="left")
        self.limited_discount_var = tk.StringVar(value="8.5")
        ttk.Entry(
            settings,
            textvariable=self.limited_discount_var,
            width=8,
        ).pack(side="left", padx=(5, 14))
        ttk.Label(settings, text="最低优惠（元）").pack(side="left")
        saved_minimum = self.db.get_setting(
            "limited_activity_min_discount",
            "1.00",
        )
        try:
            saved_minimum = f"{max(1.0, float(saved_minimum)):.2f}"
        except (TypeError, ValueError):
            saved_minimum = "1.00"
        self.limited_min_discount_var = tk.StringVar(
            value=saved_minimum
        )
        ttk.Entry(
            settings,
            textvariable=self.limited_min_discount_var,
            width=8,
        ).pack(side="left", padx=(5, 14))
        ttk.Label(settings, text="持续小时").pack(side="left")
        saved_duration = str(
            self.db.get_setting(
                "limited_activity_duration_hours",
                "168",
            )
            or "168"
        ).strip()
        if saved_duration in {"", "24"}:
            saved_duration = "168"
        self.limited_duration_var = tk.StringVar(
            value=saved_duration
        )
        ttk.Entry(
            settings,
            textvariable=self.limited_duration_var,
            width=6,
        ).pack(side="left", padx=(5, 14))
        self.limited_activity_summary_var = tk.StringVar(value="尚未预览商品")
        ttk.Label(
            settings,
            textvariable=self.limited_activity_summary_var,
            style="Subtitle.TLabel",
        ).pack(side="right")

        actions = ttk.Frame(tab, style="TFrame")
        actions.grid(row=2, column=0, sticky="ew", pady=(0, 6))
        button(
            actions,
            "同步所选店铺商品目录",
            self._sync_limited_catalogs,
        ).pack(side="left")
        button(
            actions,
            "预览在售商品",
            self._preview_limited_products,
            "Primary",
        ).pack(side="left", padx=6)
        button(
            actions,
            "为选中商品创建",
            lambda: self._create_limited_activity(individual=True),
            "Primary",
        ).pack(side="left", padx=(12, 6))
        button(
            actions,
            "整店在售商品创建",
            lambda: self._create_limited_activity(individual=False),
            "Danger",
        ).pack(side="left", padx=6)
        ttk.Label(
            actions,
            text="没有本地商品目录时会先自动同步，再按平台在售商品创建。",
            style="Subtitle.TLabel",
        ).pack(side="right")

        help_text = ttk.Label(
            tab,
            text=(
                "平台规则：统一折扣 1–8.5 折、最低优惠至少 1 元、持续 1–168 "
                "小时。每条任务使用所选店铺当前在售商品；最低优惠按每件商品"
                "金额计算。"
            ),
            style="Subtitle.TLabel",
            foreground=MUTED,
            wraplength=1040,
            justify="left",
        )
        help_text.grid(row=3, column=0, sticky="w", pady=(0, 8))

        pager = ttk.Frame(tab, style="TFrame")
        pager.grid(row=4, column=0, sticky="ew", pady=(0, 6))
        saved_page_size = str(
            self.db.get_setting("limited_activity_page_size", "100") or "100"
        ).strip()
        if saved_page_size not in {"50", "100", "200", "500"}:
            saved_page_size = "100"
        self.limited_page_size_var = tk.StringVar(value=saved_page_size)
        self.limited_prev_button = button(
            pager,
            "上一页",
            lambda: self._change_limited_page(-1),
        )
        self.limited_prev_button.pack(side="left")
        self.limited_page_label_var = tk.StringVar(value="第 1 / 1 页")
        ttk.Label(
            pager,
            textvariable=self.limited_page_label_var,
            style="Subtitle.TLabel",
        ).pack(side="left", padx=8)
        self.limited_next_button = button(
            pager,
            "下一页",
            lambda: self._change_limited_page(1),
        )
        self.limited_next_button.pack(side="left")
        ttk.Label(pager, text="每页").pack(side="right", padx=(8, 0))
        page_size_box = ttk.Combobox(
            pager,
            textvariable=self.limited_page_size_var,
            values=("50", "100", "200", "500"),
            state="readonly",
            width=6,
        )
        page_size_box.pack(side="right", padx=(5, 0))
        page_size_box.bind("<<ComboboxSelected>>", self._change_limited_page_size)
        ttk.Label(
            pager,
            text="创建活动单店最多加入 200 个商品",
            style="Subtitle.TLabel",
        ).pack(side="right", padx=12)

        self.limited_product_tree = ttk.Treeview(
            tab,
            columns=("store", "product_id", "title", "price", "stock", "status"),
            show="headings",
            selectmode="extended",
        )
        for key, label, width in (
            ("store", "店铺", 160),
            ("product_id", "商品ID", 180),
            ("title", "商品标题", 420),
            ("price", "当前价", 100),
            ("stock", "库存", 80),
            ("status", "状态", 100),
        ):
            self.limited_product_tree.heading(key, text=label)
            self.limited_product_tree.column(key, width=width, anchor="w")
        self.limited_product_tree.grid(row=5, column=0, sticky="nsew")
        self._update_limited_page_controls()

    def _build_coupon_activity_tab(self) -> None:
        tab = self._make_scrollable_tab(self.coupon_activity_tab)
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(3, weight=1)
        ttk.Label(
            tab,
            text=(
                "优惠券活动用于将同一活动模板同步或新建到上方所选店铺；"
                "续期、停用和删除请在“优惠券管理”中操作。"
            ),
            style="Subtitle.TLabel",
            wraplength=1040,
            justify="left",
        ).grid(row=0, column=0, sticky="w", pady=(0, 8))
        actions = ttk.Frame(tab, style="TFrame")
        actions.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        button(actions, "同步所选店铺优惠券", self._sync_coupons, "Primary").pack(
            side="left"
        )
        button(actions, "新建优惠券活动", self._create_coupons).pack(
            side="left",
            padx=6,
        )
        button(
            actions,
            "打开优惠券管理",
            lambda: self.notebook.select(self.coupon_tab),
        ).pack(side="left", padx=6)
        ttk.Label(
            actions,
            text="优惠券接口返回成功不等于已生效，请以平台审核状态为准。",
            style="Subtitle.TLabel",
        ).pack(side="right")
        self.coupon_activity_summary_var = tk.StringVar(value="尚未同步优惠券活动")
        ttk.Label(
            tab,
            textvariable=self.coupon_activity_summary_var,
            style="Subtitle.TLabel",
        ).grid(row=2, column=0, sticky="w", pady=(0, 6))
        self.coupon_activity_tree = ttk.Treeview(
            tab,
            columns=("store", "name", "type", "status", "end", "discount"),
            show="headings",
        )
        for key, label, width in (
            ("store", "店铺", 180),
            ("name", "优惠券活动", 280),
            ("type", "类型", 160),
            ("status", "状态", 110),
            ("end", "领取截止", 150),
            ("discount", "优惠", 120),
        ):
            self.coupon_activity_tree.heading(key, text=label)
            self.coupon_activity_tree.column(key, width=width, anchor="w")
        self.coupon_activity_tree.grid(row=3, column=0, sticky="nsew")

    def _limited_settings(self) -> tuple[float, int, int]:
        try:
            discount = float(self.limited_discount_var.get().strip())
            minimum = float(self.limited_min_discount_var.get().strip())
            duration_hours = int(self.limited_duration_var.get().strip())
        except ValueError as exc:
            raise ValueError("折扣、最低优惠和持续时间必须填写正确数字") from exc
        if not 1 <= discount <= 8.5:
            raise ValueError("平台规则要求统一折扣必须在 1–8.5 折之间")
        if minimum < 1:
            raise ValueError("平台规则要求最低优惠不能低于 1 元")
        if not 1 <= duration_hours <= 168:
            raise ValueError("持续时间必须在 1–168 小时（7天）之间")
        self.db.set_setting(
            "limited_activity_min_discount",
            f"{minimum:.2f}",
        )
        self.db.set_setting(
            "limited_activity_duration_hours",
            str(duration_hours),
        )
        return discount, int(round(minimum * 100)), duration_hours

    def _limited_store_products(
        self,
        store: Store,
        *,
        sync_if_empty: bool,
    ) -> list[dict[str, Any]]:
        products = self.db.list_products(store_id=store.id)
        if not products and sync_if_empty:
            self.window.service.sync_products(store, refresh_catalog=True)
            products = self.db.list_products(store_id=store.id)
        return [
            row
            for row in products
            if int(row.get("status") or 0) == 5
        ]

    def _sync_limited_catalogs(self) -> None:
        stores = self._require_stores()
        if stores is None:
            return
        self.limited_activity_summary_var.set("正在同步所选店铺商品目录…")

        def work(_progress):
            errors = []
            for store in stores:
                try:
                    self.window.service.sync_products(
                        store,
                        refresh_catalog=True,
                    )
                except Exception as exc:
                    errors.append(f"{store.name}：{exc}")
            return errors

        started = self.window.run_task(
            work,
            self._after_limited_sync,
            lambda error: messagebox.showerror(
                "同步限时活动商品失败",
                error,
                parent=self,
            ),
            log_title="同步限时活动商品目录",
        )
        if not started:
            self.limited_activity_summary_var.set(
                "商品同步未启动：当前有相同任务正在执行"
            )

    def _after_limited_sync(self, errors: list[str]) -> None:
        if errors:
            self.limited_activity_summary_var.set(
                f"商品同步完成，失败 {len(errors)} 家"
            )
            messagebox.showwarning(
                "部分店铺同步失败",
                "\n".join(errors[:10]),
                parent=self,
            )
            return
        self.limited_activity_summary_var.set("商品目录同步完成")
        self.after(100, self._preview_limited_products)

    def _preview_limited_products(self) -> None:
        stores = self._require_stores()
        if stores is None:
            return
        try:
            self._limited_settings()
        except ValueError as exc:
            messagebox.showwarning("限时活动参数", str(exc), parent=self)
            return
        self.limited_activity_summary_var.set("正在读取店铺在售商品…")

        def work(_progress):
            rows = []
            errors = []
            for store in stores:
                try:
                    products = self._limited_store_products(
                        store,
                        sync_if_empty=True,
                    )
                    for product in products:
                        rows.append(
                            {
                                "store_id": store.id,
                                "store_name": store.name,
                                "product_id": str(product.get("product_id") or ""),
                                "title": str(product.get("title") or ""),
                                "price": int(product.get("min_price") or 0),
                                "stock": int(product.get("stock_total") or 0),
                                "status": "在售",
                            }
                        )
                except Exception as exc:
                    errors.append(f"{store.name}：{exc}")
            return {"rows": rows, "errors": errors}

        started = self.window.run_task(
            work,
            self._after_limited_preview,
            lambda error: messagebox.showerror(
                "预览限时活动商品失败",
                error,
                parent=self,
            ),
            log_title="预览限时活动商品",
        )
        if not started:
            self.limited_activity_summary_var.set(
                "商品预览未启动：当前有相同任务正在执行"
            )

    def _after_limited_preview(self, payload: dict[str, Any]) -> None:
        self._limited_preview_rows = [
            dict(row)
            for row in payload.get("rows") or []
            if isinstance(row, dict) and row.get("product_id")
        ]
        self._limited_preview_errors = [
            str(item) for item in payload.get("errors") or []
        ]
        self._limited_preview_page = 1
        self._render_limited_preview_page()
        if self._limited_preview_errors:
            messagebox.showwarning(
                "部分店铺商品读取失败",
                "\n".join(self._limited_preview_errors[:10]),
                parent=self,
            )

    def _limited_page_size(self) -> int:
        try:
            page_size = int(self.limited_page_size_var.get())
        except (AttributeError, TypeError, ValueError):
            page_size = 100
        return page_size if page_size in {50, 100, 200, 500} else 100

    def _limited_page_count(self) -> int:
        page_size = self._limited_page_size()
        return max(
            1,
            (len(self._limited_preview_rows) + page_size - 1) // page_size,
        )

    def _render_limited_preview_page(self) -> None:
        self.limited_product_tree.delete(
            *self.limited_product_tree.get_children()
        )
        self._limited_product_rows.clear()
        page_size = self._limited_page_size()
        page_count = self._limited_page_count()
        self._limited_preview_page = max(
            1,
            min(page_count, self._limited_preview_page),
        )
        start = (self._limited_preview_page - 1) * page_size
        rows = self._limited_preview_rows[start : start + page_size]
        for offset, row in enumerate(rows):
            index = start + offset
            iid = f"{row.get('store_id')}:{row.get('product_id')}:{index}"
            self._limited_product_rows[iid] = row
            self.limited_product_tree.insert(
                "",
                "end",
                iid=iid,
                values=(
                    row.get("store_name") or "",
                    row.get("product_id") or "",
                    row.get("title") or "",
                    _money(row.get("price")),
                    int(row.get("stock") or 0),
                    row.get("status") or "",
                ),
            )
        total = len(self._limited_preview_rows)
        shown_start = start + 1 if total else 0
        shown_end = start + len(rows)
        summary = (
            f"共 {total} 个在售商品 · 当前显示 {shown_start}–{shown_end}"
            if total
            else "共 0 个在售商品"
        )
        if self._limited_preview_errors:
            summary += f" · {len(self._limited_preview_errors)} 家店铺读取失败"
        self.limited_activity_summary_var.set(summary)
        self.limited_page_label_var.set(
            f"第 {self._limited_preview_page} / {page_count} 页"
        )
        self._update_limited_page_controls()

    def _update_limited_page_controls(self) -> None:
        page_count = self._limited_page_count()
        self.limited_prev_button.configure(
            state="normal" if self._limited_preview_page > 1 else "disabled"
        )
        self.limited_next_button.configure(
            state=(
                "normal"
                if self._limited_preview_page < page_count
                else "disabled"
            )
        )
        self.limited_page_label_var.set(
            f"第 {self._limited_preview_page} / {page_count} 页"
        )

    def _change_limited_page(self, offset: int) -> None:
        page_count = self._limited_page_count()
        target = max(
            1,
            min(page_count, self._limited_preview_page + int(offset)),
        )
        if target == self._limited_preview_page:
            return
        self._limited_preview_page = target
        self._render_limited_preview_page()

    def _change_limited_page_size(self, _event=None) -> None:
        self._limited_preview_page = 1
        self.db.set_setting(
            "limited_activity_page_size",
            str(self._limited_page_size()),
        )
        self._render_limited_preview_page()

    def _create_limited_activity(self, *, individual: bool) -> None:
        stores = self._require_stores()
        if stores is None:
            return
        try:
            discount_folds, minimum_cents, duration_hours = (
                self._limited_settings()
            )
        except ValueError as exc:
            messagebox.showwarning("限时活动参数", str(exc), parent=self)
            return
        selected_rows = [
            self._limited_product_rows[iid]
            for iid in self.limited_product_tree.selection()
            if iid in self._limited_product_rows
        ]
        if individual and not selected_rows:
            messagebox.showinfo(
                "请选择商品",
                "请先在商品预览中选中要设置的商品。",
                parent=self,
            )
            return
        product_ids: dict[int, list[str]] = {}
        if individual:
            for row in selected_rows:
                product_ids.setdefault(int(row["store_id"]), []).append(
                    str(row["product_id"])
                )
        target_stores = (
            [store for store in stores if product_ids.get(store.id)]
            if individual
            else stores
        )
        scope = (
            f"{len(selected_rows)} 个已选商品，覆盖 {len(target_stores)} 家店铺"
            if individual
            else f"{len(target_stores)} 家店铺的全部有效在售商品"
        )
        if not messagebox.askyesno(
            "确认创建限时限量购",
            f"将为{scope}创建 {discount_folds:g} 折活动。\n"
            f"最低优惠：{minimum_cents / 100:.2f} 元\n"
            f"持续：{duration_hours} 小时\n\n"
            "每场活动最多 200 个商品、2000 个 SKU。"
            "超出时会在同一店铺继续创建下一场，直到全部有效商品分完。"
            "平台会再次校验价格和库存，是否继续？",
            parent=self,
        ):
            return
        self.limited_activity_summary_var.set("正在创建限时限量购活动…")

        def work(_progress):
            results = []
            now = int(time.time())
            start_time = now + 300
            end_time = start_time + duration_hours * 3600
            discount = discount_folds / 10
            for store in target_stores:
                ids = product_ids.get(store.id, [])
                try:
                    if not individual:
                        ids = [
                            str(row.get("product_id") or "")
                            for row in self._limited_store_products(
                                store,
                                sync_if_empty=True,
                            )
                            if row.get("product_id")
                        ]
                    if not ids:
                        raise RuntimeError("没有可参与活动的在售商品")
                    result = self.window.service.create_snapup_activity(
                        store,
                        ids,
                        discount,
                        start_time=start_time,
                        end_time=end_time,
                        min_discount_cents=minimum_cents,
                        create_all=True,
                    )
                    activity_count = int(result.get("activity_count") or 0)
                    product_count = int(result.get("product_count") or 0)
                    sku_count = int(result.get("sku_count") or 0)
                    already_active = int(
                        result.get("already_active_count") or 0
                    )
                    if activity_count == 0 and already_active:
                        message = (
                            f"现有待开始/生效中活动已覆盖 {already_active} 个"
                            "商品，没有需要继续创建的商品"
                        )
                    else:
                        message = (
                            f"已创建 {activity_count} 场活动，共加入 "
                            f"{product_count} 个商品、"
                            f"{sku_count} 个 SKU"
                        )
                        if already_active:
                            message += (
                                f"；另有 {already_active} 个商品已在现有活动"
                            )
                    invalid_skipped = int(
                        result.get("invalid_skipped_count") or 0
                    )
                    capacity_skipped = int(
                        result.get("capacity_skipped_count") or 0
                    )
                    if invalid_skipped:
                        message += (
                            f"，跳过 {invalid_skipped} 个售罄、下架或"
                            "不满足价格条件的商品"
                        )
                    if capacity_skipped:
                        message += f"，{capacity_skipped} 个商品超过平台硬限制"
                    failed_count = int(result.get("failed_count") or 0)
                    status = "failed" if failed_count else "success"
                    self.db.record_activity_operation(
                        "限时限量购活动",
                        status,
                        store.id,
                        store.name,
                        _record_activity_id(result.get("activity_id")),
                        product_count,
                        message,
                        result,
                    )
                    results.append(
                        {
                            "ok": failed_count == 0,
                            "store": store.name,
                            "count": product_count,
                            "activity_id": result.get("activity_id") or "",
                            "message": message,
                            "error": (
                                "；".join(
                                    str(item.get("error") or "")
                                    for item in result.get("activities") or []
                                    if not item.get("ok")
                                )
                                if failed_count
                                else ""
                            ),
                        }
                    )
                except Exception as exc:
                    self.db.record_activity_operation(
                        "限时限量购活动",
                        "failed",
                        store.id,
                        store.name,
                        None,
                        0,
                        str(exc),
                    )
                    results.append(
                        {
                            "ok": False,
                            "store": store.name,
                            "count": 0,
                            "error": str(exc),
                        }
                    )
            return results

        def done(results: list[dict[str, Any]]) -> None:
            success = sum(bool(item["ok"]) for item in results)
            self.limited_activity_summary_var.set(
                f"限时活动创建完成：成功 {success} 家，失败 {len(results) - success} 家"
            )
            failures = [
                f"{item['store']}：{item['error']}"
                for item in results
                if not item["ok"]
            ]
            if failures:
                messagebox.showwarning(
                    "部分限时活动创建失败",
                    "\n".join(failures[:12]),
                    parent=self,
                )
            elif results:
                messagebox.showinfo(
                    "所有限时活动已完成",
                    "\n".join(
                        f"{item['store']}：{item['message']}"
                        for item in results
                    ),
                    parent=self,
                )
            if "records_tree" in self.__dict__:
                self._load_operation_records()

        started = self.window.run_task(
            work,
            done,
            lambda error: messagebox.showerror(
                "创建限时限量购失败",
                error,
                parent=self,
            ),
            log_title="创建限时限量购活动",
        )
        if not started:
            self.limited_activity_summary_var.set(
                "限时活动创建未启动：当前有相同任务正在执行"
            )

    def _build_snapup_tab(self) -> None:
        tab = self._make_scrollable_tab(self.snapup_tab)
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(2, weight=1)
        toolbar = ttk.Frame(tab, style="TFrame")
        toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        button(
            toolbar,
            "刷新所选店铺限时抢购",
            self._load_snapup_activities,
            "Primary",
        ).pack(side="left")
        button(
            toolbar,
            "全选",
            self._select_all_snapup,
        ).pack(side="left", padx=(8, 0))
        button(
            toolbar,
            "清空",
            self._clear_snapup_selection,
        ).pack(side="left", padx=6)
        button(
            toolbar,
            "复制并续期7天",
            self._renew_selected_snapup,
            "Danger",
        ).pack(side="right")
        button(
            toolbar,
            "删除选中活动",
            self._delete_selected_snapup,
            "Danger",
        ).pack(side="right", padx=(0, 6))
        ttk.Label(
            toolbar,
            text="只续期商家自己的限时抢购；平台双十一等活动不会出现在这里",
            style="Subtitle.TLabel",
        ).pack(side="right", padx=8)
        self.snapup_summary_var = tk.StringVar(
            value="请选择店铺后刷新限时抢购活动"
        )
        ttk.Label(
            tab,
            textvariable=self.snapup_summary_var,
            style="Subtitle.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(0, 6))
        self.snapup_tree = ttk.Treeview(
            tab,
            columns=(
                "store",
                "title",
                "status",
                "start",
                "end",
                "products",
                "id",
            ),
            show="headings",
            selectmode="extended",
        )
        for key, label, width in (
            ("store", "店铺", 150),
            ("title", "活动名称", 230),
            ("status", "状态", 80),
            ("start", "开始时间", 145),
            ("end", "结束时间", 145),
            ("products", "商品数", 70),
            ("id", "活动ID", 130),
        ):
            self.snapup_tree.heading(key, text=label)
            self.snapup_tree.column(key, width=width, anchor="w")
        self.snapup_tree.grid(row=2, column=0, sticky="nsew")

    def _select_all_snapup(self) -> None:
        children = self.snapup_tree.get_children()
        if children:
            self.snapup_tree.selection_set(*children)

    def _clear_snapup_selection(self) -> None:
        children = self.snapup_tree.selection()
        if children:
            self.snapup_tree.selection_remove(*children)

    def _load_snapup_activities(self) -> None:
        stores = self._require_stores()
        if stores is None:
            return
        online = [store for store in stores if store.web_status == "已登录"]
        skipped = len(stores) - len(online)
        if not online:
            messagebox.showinfo(
                "没有已登录店铺",
                "限时抢购续期需要先登录微信小店后台。",
                parent=self,
            )
            return
        self.snapup_summary_var.set("正在读取限时抢购活动…")
        self.window.set_status("正在读取限时抢购活动…", busy=True)

        def work(_progress):
            rows = []
            errors = []
            for store in online:
                try:
                    for item in self.window.service.snapup_activities(store):
                        row = dict(item)
                        row["store_id"] = store.id
                        row["store_name"] = store.name
                        rows.append(row)
                except Exception as exc:
                    errors.append(f"{store.name}：{exc}")
            return {"rows": rows, "errors": errors}

        def done(result: dict[str, Any]) -> None:
            rows = result["rows"]
            self.snapup_tree.delete(*self.snapup_tree.get_children())
            self._snapup_rows.clear()
            now = int(time.time())
            for row in rows:
                activity_id = str(row.get("activity_id") or "")
                if not activity_id:
                    continue
                iid = f"{row['store_id']}:{activity_id}"
                self._snapup_rows[iid] = row
                status = int(row.get("status") or 0)
                end_time = int(row.get("endTime") or 0)
                display_status = SNAPUP_STATUSES.get(
                    status,
                    str(status),
                )
                if status == 1 and end_time and end_time - now <= 86400:
                    display_status = "即将结束"
                self.snapup_tree.insert(
                    "",
                    "end",
                    iid=iid,
                    values=(
                        row["store_name"],
                        row.get("title") or "限时抢购",
                        display_status,
                        _date_time(row.get("startTime")),
                        _date_time(end_time),
                        int(row.get("product_count") or 0),
                        activity_id,
                    ),
                )
            summary = f"{len(rows)} 场限时抢购"
            if skipped:
                summary += f" · 跳过未登录 {skipped} 家"
            if result["errors"]:
                summary += f" · 失败 {len(result['errors'])} 家"
            self.snapup_summary_var.set(summary)
            self.window.set_status(summary, busy=False)
            if result["errors"]:
                messagebox.showwarning(
                    "部分店铺读取失败",
                    "\n".join(result["errors"][:10]),
                    parent=self,
                )

        started = self.window.run_task(
            work,
            done,
            lambda error: messagebox.showerror(
                "读取限时抢购失败",
                error,
                parent=self,
            ),
            log_title="读取限时抢购活动",
        )
        if not started:
            self.snapup_summary_var.set(
                "刷新未启动：当前有相同任务正在执行"
            )

    def _renew_selected_snapup(self) -> None:
        rows = [
            self._snapup_rows[iid]
            for iid in self.snapup_tree.selection()
            if iid in self._snapup_rows
        ]
        if not rows:
            messagebox.showinfo(
                "请选择限时抢购",
                "请先在列表中选择一场或多场限时抢购。",
                parent=self,
            )
            return
        now = int(time.time())
        preview_lines = []
        for row in rows[:12]:
            old_end = int(row.get("endTime") or 0)
            start_time = max(now + 60, old_end + 1)
            end_time = start_time + 7 * 86400
            preview_lines.append(
                f"{row.get('title') or row.get('activity_id')}："
                f"{_date_time(start_time)} 至 {_date_time(end_time)}"
            )
        if not messagebox.askyesno(
            "确认复制并续期7天",
            f"将复制 {len(rows)} 场限时抢购，并通过微信原后台创建新的"
            "7天活动。\n\n原活动不会被修改；仍在进行中的活动会从原结束"
            "时间之后开始，避免时间重叠。\n\n"
            + "\n".join(preview_lines)
            + ("\n…" if len(rows) > 12 else "")
            + "\n\n是否继续？",
            parent=self,
        ):
            return
        self.window.set_status("正在创建限时抢购续期活动…", busy=True)

        def work(_progress):
            results = []
            for row in rows:
                store = self._stores.get(int(row["store_id"]))
                if store is None:
                    results.append(
                        {
                            "ok": False,
                            "store": f"店铺 {row['store_id']}",
                            "name": row.get("title") or row.get("activity_id") or "",
                            "error": "店铺不存在或已从当前列表移除",
                        }
                    )
                    continue
                old_end = int(row.get("endTime") or 0)
                start_time = max(int(time.time()) + 60, old_end + 1)
                try:
                    result = self.window.web_session.renew_snapup_activity(
                        store,
                        str(row["activity_id"]),
                        start_time,
                        7,
                    )
                    results.append(
                        {
                            "ok": True,
                            "store": store.name,
                            "name": row.get("title") or "",
                            "result": result,
                        }
                    )
                    self.db.record_activity_operation(
                        "限时抢购续期",
                        "成功",
                        store.id,
                        store.name,
                        int(row["activity_id"]),
                        1,
                        f"复制旧活动并创建7天续期：{row.get('title') or ''}",
                        result,
                    )
                except Exception as exc:
                    results.append(
                        {
                            "ok": False,
                            "store": store.name,
                            "name": row.get("title") or "",
                            "error": str(exc),
                        }
                    )
                    self.db.record_activity_operation(
                        "限时抢购续期",
                        "失败",
                        store.id,
                        store.name,
                        int(row["activity_id"]),
                        0,
                        f"{row.get('title') or ''}：{exc}",
                    )
            return results

        def done(results: list[dict[str, Any]]) -> None:
            success = sum(bool(item["ok"]) for item in results)
            failed = len(results) - success
            text = f"成功 {success} 场，失败 {failed} 场"
            self.snapup_summary_var.set(text)
            self.window.set_status(f"限时抢购续期完成：{text}", busy=False)
            grouped: dict[str, list[dict[str, Any]]] = {}
            for item in results:
                grouped.setdefault(str(item["store"]), []).append(item)
            lines = []
            for store_name, items in grouped.items():
                store_success = sum(bool(item["ok"]) for item in items)
                store_failed = len(items) - store_success
                if store_failed == 0:
                    lines.append(f"{store_name}：已完成 {store_success} 场")
                    continue
                lines.append(
                    f"{store_name}：完成 {store_success} 场，失败 {store_failed} 场"
                )
                for item in items:
                    if not item["ok"]:
                        lines.append(
                            f"  {item.get('name') or '未命名活动'}：{item['error']}"
                        )
            if success == len(results) and results:
                title = "所有任务已完成"
                show = messagebox.showinfo
            elif results:
                title = "任务处理完成，部分失败"
                show = messagebox.showwarning
            else:
                title = "没有可续期任务"
                lines = ["没有可执行的限时抢购续期任务。"]
                show = messagebox.showinfo
            show(title, "\n".join(lines[:40]), parent=self)
            self._load_snapup_activities()

        started = self.window.run_task(
            work,
            done,
            lambda error: messagebox.showerror(
                "限时抢购续期失败",
                error,
                parent=self,
            ),
            log_title="续期限时抢购活动",
        )
        if not started:
            self.snapup_summary_var.set(
                "续期未启动：当前有相同任务正在执行"
            )

    def _delete_selected_snapup(self) -> None:
        rows = [
            self._snapup_rows[iid]
            for iid in self.snapup_tree.selection()
            if iid in self._snapup_rows
        ]
        if not rows:
            messagebox.showinfo(
                "请选择限时抢购",
                "请先选择要删除的一场或多场限时抢购。",
                parent=self,
            )
            return
        preview = "\n".join(
            f"{row.get('store_name') or ''} · "
            f"{row.get('title') or row.get('activity_id')}"
            for row in rows[:20]
        )
        if not messagebox.askyesno(
            "确认删除限时活动",
            f"将永久删除 {len(rows)} 场限时抢购活动，"
            "进行中的活动会先停止。\n\n"
            f"{preview}"
            + ("\n…" if len(rows) > 20 else "")
            + "\n\n此操作不可撤销，是否继续？",
            parent=self,
        ):
            return
        self.snapup_summary_var.set("正在删除限时抢购活动…")

        def work(_progress):
            results = []
            for row in rows:
                store = self._stores.get(int(row["store_id"]))
                if store is None:
                    results.append(
                        {
                            "ok": False,
                            "store": f"店铺 {row['store_id']}",
                            "name": row.get("title") or "",
                            "error": "店铺不存在或已从当前列表移除",
                        }
                    )
                    continue
                activity_id = str(row.get("activity_id") or "")
                try:
                    result = self.window.service.delete_snapup_activity(
                        store,
                        activity_id,
                    )
                    results.append(
                        {
                            "ok": True,
                            "store": store.name,
                            "name": row.get("title") or activity_id,
                            "result": result,
                        }
                    )
                    self.db.record_activity_operation(
                        "删除限时抢购",
                        "success",
                        store.id,
                        store.name,
                        _record_activity_id(activity_id),
                        1,
                        f"删除限时抢购：{row.get('title') or activity_id}",
                        result,
                    )
                except Exception as exc:
                    results.append(
                        {
                            "ok": False,
                            "store": store.name,
                            "name": row.get("title") or activity_id,
                            "error": str(exc),
                        }
                    )
                    self.db.record_activity_operation(
                        "删除限时抢购",
                        "failed",
                        store.id,
                        store.name,
                        _record_activity_id(activity_id),
                        0,
                        str(exc),
                    )
            return results

        def done(results: list[dict[str, Any]]) -> None:
            success = sum(bool(item["ok"]) for item in results)
            failed = len(results) - success
            summary = f"删除活动：成功 {success} 场，失败 {failed} 场"
            self.snapup_summary_var.set(summary)
            self.window.set_status(summary, busy=False)
            lines = [
                (
                    f"{item['store']} · {item['name']}：已删除"
                    if item["ok"]
                    else f"{item['store']} · {item['name']}：{item['error']}"
                )
                for item in results[:40]
            ]
            if failed:
                messagebox.showwarning(
                    "删除限时活动完成，部分失败",
                    "\n".join(lines),
                    parent=self,
                )
            else:
                messagebox.showinfo(
                    "所有选中活动已删除",
                    "\n".join(lines),
                    parent=self,
                )
            self._load_snapup_activities()

        started = self.window.run_task(
            work,
            done,
            lambda error: messagebox.showerror(
                "删除限时活动失败",
                error,
                parent=self,
            ),
            log_title="删除限时抢购活动",
        )
        if not started:
            self.snapup_summary_var.set(
                "删除未启动：当前有相同任务正在执行"
            )

    def _build_coupon_tab(self) -> None:
        tab = self._make_scrollable_tab(self.coupon_tab)
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(2, weight=1)
        tools = ttk.Frame(tab, style="TFrame")
        tools.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        button(tools, "同步所选店铺", self._sync_coupons, "Primary").pack(
            side="left"
        )
        button(tools, "新建到所选店铺", self._create_coupons).pack(
            side="left", padx=6
        )
        button(
            tools,
            "复制并续期7天",
            self._renew_selected_coupons,
            "Primary",
        ).pack(side="left", padx=(0, 6))
        button(tools, "停用选中券", self._stop_selected_coupons).pack(
            side="left"
        )
        button(
            tools,
            "删除选中券",
            self._delete_selected_coupons,
            "Danger",
        ).pack(side="left", padx=6)

        source = ttk.Frame(tab, style="TFrame")
        source.grid(row=1, column=0, sticky="ew", pady=(0, 6))
        ttk.Label(source, text="模板来源店铺").pack(side="left")
        self.coupon_source_var = tk.StringVar()
        self.coupon_source_combo = ttk.Combobox(
            source,
            textvariable=self.coupon_source_var,
            state="readonly",
            width=24,
        )
        self.coupon_source_combo.pack(side="left", padx=(6, 8))
        self.coupon_type_var = tk.StringVar(value="全部类型")
        ttk.Combobox(
            source,
            textvariable=self.coupon_type_var,
            state="readonly",
            width=16,
            values=("全部类型", *COUPON_TYPES.values()),
        ).pack(side="left")
        self.coupon_keyword_var = tk.StringVar()
        ttk.Entry(
            source,
            textvariable=self.coupon_keyword_var,
            width=18,
        ).pack(side="left", padx=6)
        button(source, "预读来源券", self._preload_source_coupons).pack(
            side="left"
        )
        button(
            source,
            "锁定选中模板",
            self._lock_coupon_templates,
        ).pack(side="left", padx=6)
        button(
            source,
            "按模板停用目标店铺",
            self._stop_matching_coupons,
            "Danger",
        ).pack(side="left")
        self.coupon_summary_var = tk.StringVar(value="尚未同步优惠券")
        ttk.Label(
            source,
            textvariable=self.coupon_summary_var,
            style="Subtitle.TLabel",
        ).pack(side="right")

        self.coupon_tree = ttk.Treeview(
            tab,
            columns=(
                "store",
                "name",
                "type",
                "status",
                "end",
                "discount",
                "total",
                "received",
                "used",
                "id",
            ),
            show="headings",
            selectmode="extended",
        )
        for key, label, width in (
            ("store", "店铺", 140),
            ("name", "优惠券名称", 220),
            ("type", "类型", 130),
            ("status", "状态", 90),
            ("end", "领取截止", 145),
            ("discount", "优惠", 100),
            ("total", "发行", 70),
            ("received", "领取", 70),
            ("used", "核销", 70),
            ("id", "优惠券ID", 160),
        ):
            self.coupon_tree.heading(key, text=label)
            self.coupon_tree.column(key, width=width, anchor="w")
        self.coupon_tree.grid(row=2, column=0, sticky="nsew")

    def _coupon_type_filter(self) -> int | None:
        label = self.coupon_type_var.get()
        return next(
            (value for value, text in COUPON_TYPES.items() if text == label),
            None,
        )

    def _load_coupons(self, store_ids: list[int] | None = None) -> None:
        stores = self._selected_stores() if store_ids is None else [
            self._stores[store_id]
            for store_id in store_ids
            if store_id in self._stores
        ]
        type_filter = self._coupon_type_filter()
        keyword = self.coupon_keyword_var.get().strip().casefold()
        rows: list[dict[str, Any]] = []
        for store in stores:
            for row in self.db.list_coupons(store.id):
                if type_filter is not None and int(row["coupon_type"]) != type_filter:
                    continue
                if keyword and keyword not in str(row["name"]).casefold():
                    continue
                item = dict(row)
                item["store_name"] = store.name
                rows.append(item)
        self.coupon_tree.delete(*self.coupon_tree.get_children())
        self._coupon_rows.clear()
        for index, row in enumerate(rows):
            iid = f"{row['store_id']}:{row['coupon_id']}:{index}"
            self._coupon_rows[iid] = row
            if row["discount_fee"]:
                discount = f"减 {_money(row['discount_fee'])}"
            elif row["discount_num"]:
                discount = f"{row['discount_num'] / 1000:.1f} 折"
            else:
                discount = "—"
            self.coupon_tree.insert(
                "",
                "end",
                iid=iid,
                values=(
                    row["store_name"],
                    row["name"],
                    COUPON_TYPES.get(row["coupon_type"], str(row["coupon_type"])),
                    COUPON_STATUSES.get(row["status"], str(row["status"])),
                    _date_time(row["end_time"]),
                    discount,
                    row["total_num"],
                    row["receive_num"],
                    row["used_num"],
                    row["coupon_id"],
                ),
            )
        if hasattr(self, "coupon_activity_tree"):
            self.coupon_activity_tree.delete(
                *self.coupon_activity_tree.get_children()
            )
            for iid in self.coupon_tree.get_children():
                values = list(self.coupon_tree.item(iid, "values"))
                self.coupon_activity_tree.insert(
                    "",
                    "end",
                    values=(
                        values[0] if len(values) > 0 else "",
                        values[1] if len(values) > 1 else "",
                        values[2] if len(values) > 2 else "",
                        values[3] if len(values) > 3 else "",
                        values[4] if len(values) > 4 else "",
                        values[5] if len(values) > 5 else "",
                    ),
                )
        if hasattr(self, "coupon_activity_summary_var"):
            self.coupon_activity_summary_var.set(f"{len(rows)} 个优惠券活动")
        self.coupon_summary_var.set(f"{len(rows)} 张优惠券")

    def _run_coupon_sync(self, stores: list[Store]) -> dict[str, Any]:
        results = []
        for store in stores:
            try:
                result = self.window.service.sync_coupons(store)
                results.append(
                    {
                        "ok": True,
                        "store": store.name,
                        "message": result.message,
                    }
                )
            except Exception as exc:
                results.append(
                    {
                        "ok": False,
                        "store": store.name,
                        "message": str(exc),
                    }
                )
        return {"results": results}

    def _sync_coupons(self) -> None:
        stores = self._require_stores()
        if stores is None:
            return
        self.window.set_status("正在同步所选店铺优惠券…", busy=True)

        def done(result):
            self._source_only = False
            self._load_coupons()
            success = sum(item["ok"] for item in result["results"])
            self.window.set_status(
                f"优惠券同步完成：成功 {success} 家，失败 {len(stores) - success} 家"
            )

        self.window.run_task(
            lambda _progress: self._run_coupon_sync(stores),
            done,
            lambda error: messagebox.showerror(
                "优惠券同步失败", error, parent=self
            ),
            log_title="同步活动优惠券",
        )

    def _create_coupons(self) -> None:
        stores = self._require_stores()
        if stores is None:
            return
        template = {
            "type": 104,
            "name": "店铺直减券",
            "promote_info": {"promote_type": 1},
            "discount_info": {"discount_fee": 500},
            "receive_info": {
                "start_time": 0,
                "end_time": 0,
                "limit_num_one_person": 1,
                "total_num": 100,
            },
            "valid_info": {"valid_type": 2, "valid_day_num": 7},
        }

        def save(payload: dict[str, Any]) -> None:
            if not messagebox.askyesno(
                "确认批量创建优惠券",
                f"将向 {len(stores)} 家店铺创建同一套优惠券，是否继续？",
                parent=self,
            ):
                return

            def work(_progress):
                results = []
                for store in stores:
                    try:
                        results.append(
                            {
                                "ok": True,
                                "store": store.name,
                                "result": self.window.service.coupon_write(
                                    store, "create", payload
                                ),
                            }
                        )
                        self.db.record_activity_operation(
                            "优惠券创建",
                            "成功",
                            store.id,
                            store.name,
                            None,
                            1,
                            f"创建优惠券：{payload.get('name') or ''}",
                        )
                    except Exception as exc:
                        results.append(
                            {
                                "ok": False,
                                "store": store.name,
                                "error": str(exc),
                            }
                        )
                        self.db.record_activity_operation(
                            "优惠券创建",
                            "失败",
                            store.id,
                            store.name,
                            None,
                            0,
                            str(exc),
                        )
                return results

            def done(results):
                self._load_coupons()
                success = sum(item["ok"] for item in results)
                self.window.set_status(
                    f"优惠券创建完成：成功 {success} 家，失败 {len(results) - success} 家"
                )
                messagebox.showinfo(
                    "批量创建结果",
                    "\n".join(
                        f"{item['store']}："
                        + ("成功" if item["ok"] else item["error"])
                        for item in results
                    ),
                    parent=self,
                )

            self.window.run_task(
                work,
                done,
                lambda error: messagebox.showerror(
                    "批量创建优惠券失败", error, parent=self
                ),
                log_title="批量创建优惠券",
            )

        CouponFormDialog(self, "批量新建优惠券", template, "add", save)

    def _selected_coupon_rows(self) -> list[dict[str, Any]]:
        return [
            self._coupon_rows[iid]
            for iid in self.coupon_tree.selection()
            if iid in self._coupon_rows
        ]

    def _renew_selected_coupons(self) -> None:
        rows = self._selected_coupon_rows()
        if not rows:
            messagebox.showinfo(
                "请选择优惠券",
                "请先选择一张或多张要复制的优惠券。",
                parent=self,
            )
            return
        now = int(time.time())
        preview_lines = []
        for row in rows[:12]:
            old_end = int(row.get("end_time") or 0)
            start_time = max(now + 60, old_end + 1)
            end_time = start_time + 7 * 86400
            preview_lines.append(
                f"{row['name']}：{_date_time(start_time)} 至 "
                f"{_date_time(end_time)}"
            )
        if not messagebox.askyesno(
            "确认复制并续期7天",
            f"将复制 {len(rows)} 张优惠券，并通过微信原后台创建新的"
            "7天领取期。\n\n原优惠券不会被修改；仍在生效的券会从原"
            "结束时间之后开始，避免重复叠加。\n\n"
            + "\n".join(preview_lines)
            + ("\n…" if len(rows) > 12 else "")
            + "\n\n是否继续？",
            parent=self,
        ):
            return
        self.window.set_status("正在创建7天优惠券续期…", busy=True)

        def work(_progress):
            results = []
            touched_stores: set[int] = set()
            for row in rows:
                store = self._stores.get(int(row["store_id"]))
                if store is None:
                    continue
                old_end = int(row.get("end_time") or 0)
                start_time = max(int(time.time()) + 60, old_end + 1)
                try:
                    raw = json.loads(str(row.get("raw_json") or "{}"))
                except json.JSONDecodeError:
                    raw = {}
                coupon_info = raw.get("coupon_info") or {}
                promote_info = coupon_info.get("promote_info") or {}
                promote_type = int(promote_info.get("promote_type") or 1)
                try:
                    result = self.window.web_session.renew_coupon(
                        store,
                        str(row["coupon_id"]),
                        int(row["coupon_type"]),
                        promote_type,
                        start_time,
                        7,
                    )
                    touched_stores.add(store.id)
                    results.append(
                        {
                            "ok": True,
                            "store": store.name,
                            "name": row["name"],
                            "result": result,
                        }
                    )
                    self.db.record_activity_operation(
                        "优惠券续期",
                        "成功",
                        store.id,
                        store.name,
                        None,
                        1,
                        f"复制优惠券并创建7天领取期：{row['name']}",
                        result,
                    )
                except Exception as exc:
                    results.append(
                        {
                            "ok": False,
                            "store": store.name,
                            "name": row["name"],
                            "error": str(exc),
                        }
                    )
                    self.db.record_activity_operation(
                        "优惠券续期",
                        "失败",
                        store.id,
                        store.name,
                        None,
                        0,
                        f"{row['name']}：{exc}",
                    )
            for store_id in touched_stores:
                store = self._stores.get(store_id)
                if store is not None:
                    try:
                        self.window.service.sync_coupons(store)
                    except Exception:
                        pass
            return results

        def done(results: list[dict[str, Any]]) -> None:
            self._load_coupons()
            success = sum(bool(item["ok"]) for item in results)
            text = f"成功 {success} 张，失败 {len(results) - success} 张"
            self.window.set_status(f"优惠券续期完成：{text}")
            messagebox.showinfo(
                "优惠券续期结果",
                text
                + "\n\n"
                + "\n".join(
                    (
                        f"{item['store']} · {item['name']}：已创建"
                        if item["ok"]
                        else f"{item['store']} · {item['name']}：{item['error']}"
                    )
                    for item in results[:20]
                ),
                parent=self,
            )

        self.window.run_task(
            work,
            done,
            lambda error: messagebox.showerror(
                "优惠券续期失败",
                error,
                parent=self,
            ),
            log_title="续期优惠券",
        )

    def _change_selected_coupon_status(self, status: int) -> None:
        rows = self._selected_coupon_rows()
        if not rows:
            messagebox.showinfo(
                "请选择优惠券",
                "请先在列表中选择优惠券。",
                parent=self,
            )
            return
        action_name = "停用（作废）" if status == 4 else "删除"
        if not messagebox.askyesno(
            f"确认{action_name}优惠券",
            f"将{action_name} {len(rows)} 张优惠券。\n"
            + ("删除可能无法恢复，是否继续？" if status == 5 else "是否继续？"),
            parent=self,
        ):
            return
        self.window.set_status(f"正在{action_name}优惠券…", busy=True)

        def work(_progress):
            results = []
            for row in rows:
                store = self._stores.get(int(row["store_id"]))
                if store is None:
                    continue
                try:
                    self.window.service.coupon_write(
                        store,
                        "status",
                        {"coupon_id": row["coupon_id"], "status": status},
                    )
                    results.append(
                        (store.name, row["name"], True, "")
                    )
                    self.db.record_activity_operation(
                        "优惠券停用" if status == 4 else "优惠券删除",
                        "成功",
                        store.id,
                        store.name,
                        None,
                        1,
                        str(row["name"]),
                    )
                except Exception as exc:
                    results.append(
                        (store.name, row["name"], False, str(exc))
                    )
                    self.db.record_activity_operation(
                        "优惠券停用" if status == 4 else "优惠券删除",
                        "失败",
                        store.id,
                        store.name,
                        None,
                        0,
                        f"{row['name']}：{exc}",
                    )
            return results

        def done(results):
            self._load_coupons()
            success = sum(item[2] for item in results)
            self.window.set_status(
                f"优惠券{action_name}完成：成功 {success}，失败 {len(results) - success}"
            )
            failures = [
                f"{store} · {name}：{error}"
                for store, name, ok, error in results
                if not ok
            ]
            if failures:
                messagebox.showwarning(
                    "部分优惠券操作失败",
                    "\n".join(failures[:10]),
                    parent=self,
                )

        self.window.run_task(
            work,
            done,
            lambda error: messagebox.showerror(
                f"{action_name}优惠券失败", error, parent=self
            ),
            log_title=f"{action_name}优惠券",
        )

    def _stop_selected_coupons(self) -> None:
        self._change_selected_coupon_status(4)

    def _delete_selected_coupons(self) -> None:
        self._change_selected_coupon_status(5)

    def _preload_source_coupons(self) -> None:
        store = self._store_from_label(self.coupon_source_var.get())
        if store is None:
            messagebox.showinfo("请选择来源店铺", "请先选择模板来源店铺。", parent=self)
            return
        self.window.set_status("正在预读来源店铺优惠券…", busy=True)

        def done(_result):
            self._source_only = True
            self._load_coupons([store.id])
            self.window.set_status(
                f"已预读“{store.name}”的 {len(self._coupon_rows)} 张优惠券"
            )

        self.window.run_task(
            lambda _progress: self._run_coupon_sync([store]),
            done,
            lambda error: messagebox.showerror(
                "预读优惠券失败", error, parent=self
            ),
            log_title="预读来源店铺优惠券",
        )

    def _lock_coupon_templates(self) -> None:
        rows = self._selected_coupon_rows()
        if not rows:
            messagebox.showinfo(
                "请选择优惠券",
                "请先选择一张或多张来源优惠券。",
                parent=self,
            )
            return
        self._coupon_templates = {
            (int(row["coupon_type"]), str(row["name"]))
            for row in rows
        }
        messagebox.showinfo(
            "模板已锁定",
            f"已锁定 {len(self._coupon_templates)} 种优惠券模板。",
            parent=self,
        )

    def _stop_matching_coupons(self) -> None:
        stores = self._require_stores()
        if stores is None:
            return
        if not self._coupon_templates:
            messagebox.showinfo(
                "没有模板",
                "请先预读来源店铺优惠券，选择后点击“锁定选中模板”。",
                parent=self,
            )
            return
        self.window.set_status("正在同步目标店铺并匹配同款优惠券…", busy=True)

        def prepare(_progress):
            matches = []
            for store in stores:
                try:
                    self.window.service.sync_coupons(store)
                except Exception as exc:
                    matches.append(
                        {
                            "store": store,
                            "row": None,
                            "error": str(exc),
                        }
                    )
                    continue
                for row in self.db.list_coupons(store.id):
                    pair = (int(row["coupon_type"]), str(row["name"]))
                    if pair in self._coupon_templates and int(row["status"]) == 2:
                        matches.append(
                            {"store": store, "row": dict(row), "error": ""}
                        )
            return matches

        def preview(matches):
            usable = [item for item in matches if item.get("row")]
            errors = [item for item in matches if item.get("error")]
            lines = [
                f"{item['store'].name} · {item['row']['name']}"
                for item in usable
            ]
            if not usable:
                messagebox.showinfo(
                    "没有匹配优惠券",
                    "目标店铺没有找到与模板类型和名称完全一致的有效优惠券。"
                    + (
                        "\n\n" + "\n".join(
                            f"{item['store'].name}：{item['error']}"
                            for item in errors[:8]
                        )
                        if errors
                        else ""
                    ),
                    parent=self,
                )
                self.window.set_status("没有匹配到可停用优惠券")
                return
            if not messagebox.askyesno(
                "确认批量停用优惠券",
                f"匹配到 {len(usable)} 张优惠券，将统一作废。\n\n"
                + "\n".join(lines[:20])
                + ("\n…" if len(lines) > 20 else ""),
                parent=self,
            ):
                return

            def work(_progress):
                results = []
                for item in usable:
                    store = item["store"]
                    row = item["row"]
                    try:
                        self.window.service.coupon_write(
                            store,
                            "status",
                            {"coupon_id": row["coupon_id"], "status": 4},
                        )
                        results.append((store.name, True, ""))
                        self.db.record_activity_operation(
                            "模板券停用",
                            "成功",
                            store.id,
                            store.name,
                            None,
                            1,
                            str(row["name"]),
                        )
                    except Exception as exc:
                        results.append((store.name, False, str(exc)))
                        self.db.record_activity_operation(
                            "模板券停用",
                            "失败",
                            store.id,
                            store.name,
                            None,
                            0,
                            f"{row['name']}：{exc}",
                        )
                return results

            def done(results):
                self._load_coupons()
                success = sum(item[1] for item in results)
                self.window.set_status(
                    f"模板优惠券停用完成：成功 {success}，失败 {len(results) - success}"
                )
                failures = [
                    f"{name}：{error}"
                    for name, ok, error in results
                    if not ok
                ]
                if failures:
                    messagebox.showwarning(
                        "部分店铺停用失败",
                        "\n".join(failures[:10]),
                        parent=self,
                    )

            self.window.run_task(
                work,
                done,
                lambda error: messagebox.showerror(
                    "批量停用失败", error, parent=self
                ),
                log_title="批量停用匹配优惠券",
            )

        self.window.run_task(
            prepare,
            preview,
            lambda error: messagebox.showerror(
                "模板匹配失败", error, parent=self
            ),
            log_title="匹配优惠券模板",
        )

    def _build_records_tab(self) -> None:
        tab = self._make_scrollable_tab(self.records_tab)
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(1, weight=1)
        toolbar = ttk.Frame(tab, style="TFrame")
        toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        button(
            toolbar,
            "刷新执行记录",
            self._load_operation_records,
        ).pack(side="left")
        ttk.Label(
            toolbar,
            text="活动申报、商品价格提交和优惠券操作永久保存在本地数据库",
            style="Subtitle.TLabel",
        ).pack(side="right")
        self.records_tree = ttk.Treeview(
            tab,
            columns=(
                "time",
                "action",
                "store",
                "activity",
                "status",
                "count",
                "message",
            ),
            show="headings",
        )
        for key, label, width in (
            ("time", "执行时间", 145),
            ("action", "操作", 120),
            ("store", "店铺", 150),
            ("activity", "活动ID", 80),
            ("status", "结果", 80),
            ("count", "数量", 70),
            ("message", "说明", 460),
        ):
            self.records_tree.heading(key, text=label)
            self.records_tree.column(key, width=width, anchor="w")
        self.records_tree.grid(row=1, column=0, sticky="nsew")

    def _load_operation_records(self) -> None:
        if not hasattr(self, "records_tree"):
            return
        self.records_tree.delete(*self.records_tree.get_children())
        for row in self.db.list_activity_operations(limit=500):
            created = str(row.get("created_at") or "")
            try:
                created = datetime.fromisoformat(created).strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
            except ValueError:
                created = created[:19]
            self.records_tree.insert(
                "",
                "end",
                values=(
                    created,
                    row.get("action") or "",
                    row.get("store_name") or "",
                    row.get("activity_id") or "",
                    row.get("status") or "",
                    row.get("item_count") or 0,
                    row.get("message") or "",
                ),
            )

    def _make_scrollable_tab(self, container: ttk.Frame) -> ttk.Frame:
        container.columnconfigure(0, weight=1)
        container.rowconfigure(0, weight=1)
        canvas = tk.Canvas(
            container,
            bg=BG,
            highlightthickness=0,
        )
        scrollbar = ttk.Scrollbar(
            container,
            orient="vertical",
            command=canvas.yview,
        )
        canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        canvas.configure(yscrollcommand=scrollbar.set)
        body = ttk.Frame(canvas)
        window_id = canvas.create_window((0, 0), window=body, anchor="nw")
        body.bind(
            "<Configure>",
            lambda _event: canvas.configure(
                scrollregion=canvas.bbox("all")
            ),
        )
        canvas.bind(
            "<Configure>",
            lambda event: canvas.itemconfigure(
                window_id,
                width=event.width,
            ),
        )

        def on_mousewheel(event: tk.Event) -> str | None:
            widget = getattr(event, "widget", None)
            while widget is not None:
                if widget == container:
                    canvas.yview_scroll(
                        int(-1 * (event.delta / 120)),
                        "units",
                    )
                    return "break"
                widget = getattr(widget, "master", None)
            return None

        container.bind_all("<MouseWheel>", on_mousewheel, add="+")
        return body
