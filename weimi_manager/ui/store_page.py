from __future__ import annotations

import json
import os
import re
import time
import tkinter as tk
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import TYPE_CHECKING, Any

from ..account_store_sync import (
    decrypt_store_snapshot,
    encrypt_store_snapshot,
)
from ..db import Database
from ..models import Store
from .dialogs import CookieImportDialog, StoreDialog
from .theme import MUTED, PANEL, TEXT, button

if TYPE_CHECKING:
    from .app import MainWindow


def _money(cents: int | None) -> str:
    return f"¥{(cents or 0) / 100:,.2f}"


def parse_store_batch_line(
    line: str,
) -> tuple[str, str, str] | None:
    """Parse one batch-login line while allowing an empty AppSecret."""
    parts = [
        value
        for value in re.split(r"[\s,，;；|\t]+", line.strip())
        if value
    ]
    if not parts:
        return None
    app_id = next(
        (
            value
            for value in parts
            if value.casefold().startswith("wx")
        ),
        parts[0],
    )
    rest = [value for value in parts if value != app_id]
    secret = next(
        (
            value
            for value in rest
            if len(value) in {32, 64}
            and re.fullmatch(r"[A-Za-z0-9_-]+", value)
        ),
        "",
    )
    name = " ".join(value for value in rest if value != secret)
    return name, app_id, secret


class _PlaceholderEntry(ttk.Entry):
    def __init__(
        self,
        parent: tk.Widget,
        textvariable: tk.StringVar,
        placeholder: str,
        **kwargs: Any,
    ):
        super().__init__(parent, **kwargs)
        self.variable = textvariable
        self.placeholder = placeholder
        self._placeholder_visible = False
        self._updating = False
        self.bind("<FocusIn>", self._focus_in, add="+")
        self.bind("<FocusOut>", self._focus_out, add="+")
        self.bind("<KeyRelease>", self._key_release, add="+")
        self.variable.trace_add("write", self._variable_changed)
        self._show_placeholder()

    def _show_placeholder(self) -> None:
        if self.focus_get() is self or self.variable.get():
            return
        self._placeholder_visible = True
        self.configure(foreground="#9aa6ad")
        super().delete(0, "end")
        super().insert(0, self.placeholder)

    def _hide_placeholder(self) -> None:
        if self._placeholder_visible:
            self._placeholder_visible = False
            super().delete(0, "end")
            self.configure(foreground=TEXT)

    def _focus_in(self, _event=None) -> None:
        self._hide_placeholder()

    def _focus_out(self, _event=None) -> None:
        self._show_placeholder()

    def _key_release(self, _event=None) -> None:
        if self._placeholder_visible:
            return
        self._updating = True
        try:
            self.variable.set(super().get())
        finally:
            self._updating = False

    def _variable_changed(self, *_args) -> None:
        if self._updating:
            return
        self._hide_placeholder()
        value = self.variable.get()
        if super().get() != value:
            super().delete(0, "end")
            if value:
                super().insert(0, value)
            elif self.focus_get() is not self:
                self.after_idle(self._show_placeholder)

    def get(self) -> str:
        return "" if self._placeholder_visible else super().get()


def _center_dialog(parent: tk.Widget, dialog: tk.Toplevel) -> None:
    parent.update_idletasks()
    dialog.update_idletasks()
    root = parent.winfo_toplevel()
    x = root.winfo_rootx() + max(
        0, (root.winfo_width() - dialog.winfo_width()) // 2
    )
    y = root.winfo_rooty() + max(
        0, (root.winfo_height() - dialog.winfo_height()) // 2
    )
    dialog.geometry(f"+{x}+{y}")


class StoreBatchDialog(tk.Toplevel):
    def __init__(self, parent: tk.Widget, on_save: Any):
        super().__init__(parent)
        self.on_save = on_save
        self.title("批量登录店铺")
        self.configure(bg="#f4f2eb")
        self.geometry("760x520")
        self.minsize(680, 440)
        self.transient(parent.winfo_toplevel())
        self.grab_set()

        body = ttk.Frame(self, style="Panel.TFrame", padding=22)
        body.pack(fill="both", expand=True, padx=16, pady=16)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(2, weight=1)

        ttk.Label(body, text="批量登录店铺", style="Section.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(
            body,
            text=(
                "一行一家：AppID + AppSecret；仅提供 AppID 时按网页登录店铺导入。"
                "重复 AppID 会更新原记录。"
            ),
            style="Panel.TLabel",
            foreground=MUTED,
        ).grid(row=1, column=0, sticky="w", pady=(5, 10))

        text_wrap = ttk.Frame(body, style="Panel.TFrame")
        text_wrap.grid(row=2, column=0, sticky="nsew")
        text_wrap.columnconfigure(0, weight=1)
        text_wrap.rowconfigure(0, weight=1)
        self.text = tk.Text(
            text_wrap,
            wrap="none",
            undo=True,
            bg="white",
            fg=TEXT,
            relief="solid",
            bd=1,
            padx=10,
            pady=8,
        )
        self.text.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(
            text_wrap, orient="vertical", command=self.text.yview
        )
        scroll.grid(row=0, column=1, sticky="ns")
        self.text.configure(yscrollcommand=scroll.set)

        group_row = ttk.Frame(body, style="Panel.TFrame")
        group_row.grid(row=3, column=0, sticky="ew", pady=(12, 0))
        ttk.Label(group_row, text="统一分组", style="Panel.TLabel").pack(
            side="left"
        )
        self.group_var = tk.StringVar()
        ttk.Entry(group_row, textvariable=self.group_var, width=24).pack(
            side="left", padx=8
        )
        ttk.Label(
            group_row,
            text="可留空",
            style="Panel.TLabel",
            foreground=MUTED,
        ).pack(side="left")

        actions = ttk.Frame(body, style="Panel.TFrame")
        actions.grid(row=4, column=0, sticky="e", pady=(16, 0))
        button(actions, "关闭", self.destroy).pack(side="left", padx=(0, 8))
        button(actions, "开始登录", self._submit, "Primary").pack(side="left")
        self.text.focus_set()
        self.bind("<Escape>", lambda _event: self.destroy())
        _center_dialog(parent, self)

    def _submit(self) -> None:
        raw = self.text.get("1.0", "end").strip()
        if not raw:
            messagebox.showwarning(
                "信息不完整", "请粘贴至少一条店铺凭据。", parent=self
            )
            return
        records: list[tuple[str, str, str]] = []
        errors: list[str] = []
        for line_no, line in enumerate(raw.splitlines(), 1):
            if not line.strip():
                continue
            parsed = parse_store_batch_line(line)
            if parsed is None:
                continue
            name, app_id, secret = parsed
            if not app_id:
                errors.append(f"第 {line_no} 行：缺少 AppID")
                continue
            records.append((name, app_id, secret))
        if records:
            try:
                accepted = self.on_save(
                    records,
                    self.group_var.get().strip(),
                )
            except Exception as exc:
                messagebox.showerror(
                    "批量保存失败",
                    str(exc),
                    parent=self,
                )
                return
            if accepted is not False:
                self.destroy()
        if errors:
            messagebox.showwarning(
                "部分记录未导入", "\n".join(errors), parent=self
            )


class StorePage(ttk.Frame):
    _METRICS_CACHE_SECONDS = 2.0
    _INCREMENTAL_REFRESH_MS = 350
    title = "店铺管理"
    subtitle = (
        "维护 AppID / AppSecret、登录态和经营数据；"
        "分组与列显示会保存在本机。"
    )
    field_definitions = (
        ("name", "店铺名称", 150),
        ("group", "分组", 100),
        ("app_id", "AppID", 180),
        ("secret", "AppSecret", 96),
        ("login", "登录状态", 88),
        ("in_sale", "在售", 64),
        ("warehouse", "仓库", 64),
        ("product_total", "全部商品", 88),
        ("quota", "提审额度", 92),
        ("gmv_today", "今日成交", 92),
        ("gmv_yesterday", "昨日成交", 92),
        ("gmv_7d", "7日成交", 92),
        ("gmv_30d", "30日成交", 92),
        ("buyers_today", "今日买家", 88),
        ("buyer_30d", "30日买家", 88),
        ("orders_today", "今日订单", 88),
        ("orders_yesterday", "昨日订单", 88),
        ("product_click_today", "商品点击", 88),
        ("visitors_30d", "30日访客", 88),
        ("refund_today", "今日退款", 88),
        ("refund_30d", "30日退款", 88),
        ("aftersale", "售后", 70),
        ("warn", "店铺预警", 88),
        ("violation", "店铺违规", 88),
        ("cash", "可提现", 92),
        ("frozen", "暂预留", 82),
        ("settle", "待结算", 92),
        ("deposit", "保证金", 92),
        ("dsr", "体验分", 76),
        ("score_product", "商品体验", 84),
        ("score_deliver", "物流体验", 84),
        ("score_service", "服务体验", 84),
        ("good_eval_ratio", "商品好评率", 96),
        ("quality_refund_ratio", "品质退款率", 96),
        ("wait_send", "待发货", 76),
        ("wait_receive", "待收货", 76),
        ("recommend_exposure", "推荐曝光", 88),
        ("recommend_exposure_yesterday", "昨日推荐曝光", 104),
        ("search_exposure_yesterday", "昨日搜索曝光", 104),
        ("today_success", "今日成功", 82),
        ("stats_at", "数据更新", 112),
    )
    compact_fields = (
        "name",
        "group",
        "app_id",
        "secret",
        "in_sale",
        "quota",
        "gmv_today",
        "gmv_7d",
        "gmv_30d",
        "login",
    )
    fixed_left_fields = ("selected", "sequence")
    action_field = "action"
    money_fields = {
        "gmv_today",
        "gmv_yesterday",
        "gmv_7d",
        "gmv_30d",
        "refund_today",
        "refund_30d",
        "cash",
        "frozen",
        "settle",
        "deposit",
    }
    score_fields = {
        "dsr",
        "score_product",
        "score_deliver",
        "score_service",
    }
    percent_fields = {"good_eval_ratio", "quality_refund_ratio"}
    numeric_fields = {
        "in_sale",
        "warehouse",
        "buyers_today",
        "buyer_30d",
        "orders_today",
        "orders_yesterday",
        "product_click_today",
        "visitors_30d",
        "aftersale",
        "warn",
        "violation",
        "wait_send",
        "wait_receive",
        "recommend_exposure",
        "recommend_exposure_yesterday",
        "search_exposure_yesterday",
        "today_success",
    }

    def __init__(self, parent: tk.Widget, window: "MainWindow"):
        super().__init__(parent, style="TFrame", padding=(24, 16, 24, 14))
        self.window = window
        self.db = window.db
        self.columnconfigure(0, weight=1)
        self.rowconfigure(3, weight=1)
        self.search_var = tk.StringVar()
        self.filter_var = tk.StringVar(value="全部店铺")
        self.group_var = tk.StringVar(value="全部分组")
        self.footer_var = tk.StringVar(value="第 1 / 1 页 · 0–0 / 共 0 条")
        self.status_line_var = tk.StringVar(
            value="勾选店铺后可批量操作"
        )
        self.selected_store: Store | None = None
        self.active_filter = "all"
        self.filter_buttons: dict[str, ttk.Button] = {}
        self._all_columns = (
            *self.fixed_left_fields,
            *(key for key, _label, _width in self.field_definitions),
            self.action_field,
        )
        valid_fields = {key for key, _label, _width in self.field_definitions}
        try:
            saved_fields = json.loads(
                self.db.get_setting("store_visible_fields", "[]")
            )
        except (TypeError, ValueError):
            saved_fields = []
        selected_fields = [
            key for key in saved_fields if key in valid_fields
        ] if isinstance(saved_fields, list) else []
        self._visible_fields = [
            key
            for key, _label, _width in self.field_definitions
            if not selected_fields or key in selected_fields
        ]
        self._display_mode = self.db.get_setting(
            "store_display_mode", "full"
        )
        if self._display_mode not in {"compact", "full"}:
            self._display_mode = "full"
        try:
            self._page_size = int(self.db.get_setting("store_page_size", "100"))
        except ValueError:
            self._page_size = 100
        if self._page_size not in {20, 50, 100, 200}:
            self._page_size = 100
        self._page = 1
        self._search_refresh_job: str | None = None
        self._filtered_ids: list[int] = []
        self._page_ids: list[int] = []
        self._metrics_by_id: dict[int, dict[str, Any]] = {}
        self._metrics_cache_at = 0.0
        self._data_refresh_job: str | None = None
        self._checked_ids: set[int] = set()
        self._shown_secrets: set[int] = set()
        self.sort_field = "name"
        self.sort_desc = False
        self._compact_button: ttk.Button | None = None
        self._full_button: ttk.Button | None = None
        self._field_button: ttk.Button | None = None
        self._edit_button: ttk.Button | None = None
        self._copy_button: ttk.Button | None = None
        self._export_button: ttk.Button | None = None
        self._import_button: ttk.Button | None = None
        self._delete_button: ttk.Button | None = None
        self._select_all_button: ttk.Button | None = None

        toolbar = ttk.Frame(self, style="TFrame")
        toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        ttk.Label(
            toolbar,
            text="接口凭证 · 扫码登录 · 店铺维护",
            style="Subtitle.TLabel",
        ).pack(side="left")
        button(toolbar, "刷新数据", self._refresh_store_data).pack(side="right")
        button(toolbar, "扫码登录", self._scan_login).pack(side="right", padx=6)
        button(toolbar, "批量登录", self._batch_import_credentials).pack(
            side="right"
        )
        button(toolbar, "添加店铺", self._add_store, "Primary").pack(
            side="right",
            padx=(0, 6),
        )
        button(
            toolbar,
            "离线所选店铺",
            self._offline_selected_stores,
        ).pack(side="right", padx=(0, 6))

        view_bar = ttk.Frame(self, style="TFrame")
        view_bar.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        search_group = ttk.Frame(view_bar, style="TFrame")
        search_group.pack(side="right")
        ttk.Label(search_group, text="⌕").pack(side="left", padx=(4, 3))
        search_entry = _PlaceholderEntry(
            search_group,
            textvariable=self.search_var,
            placeholder="搜索店铺 / AppID / 分组",
            width=28,
        )
        search_entry.pack(side="left")
        search_entry.bind("<Return>", lambda _event: self._reset_and_refresh())
        self.search_var.trace_add("write", self._schedule_search_refresh)
        for key, label in (
            ("all", "全部店铺"),
            ("logged", "已登录"),
            ("unlogged", "未登录"),
        ):
            filter_button = button(
                view_bar,
                label,
                lambda filter_key=key: self._change_filter(filter_key),
            )
            filter_button.pack(side="left", padx=(0, 5))
            self.filter_buttons[key] = filter_button
        ttk.Label(view_bar, text="分组").pack(side="left", padx=(8, 4))
        self.group_combo = ttk.Combobox(
            view_bar,
            textvariable=self.group_var,
            state="readonly",
            width=16,
        )
        self.group_combo.pack(side="left")
        self.group_combo.bind(
            "<<ComboboxSelected>>", lambda _event: self.refresh()
        )
        self._select_all_button = button(
            view_bar,
            "全选",
            self._toggle_store_selection,
        )
        self._select_all_button.pack(side="left", padx=(12, 6))

        column_bar = ttk.Frame(self, style="TFrame")
        column_bar.grid(row=2, column=0, sticky="ew", pady=(0, 7))
        ops = ttk.Frame(column_bar, style="TFrame")
        ops.pack(side="right")
        ttk.Label(
            column_bar,
            textvariable=self.status_line_var,
            style="Subtitle.TLabel",
        ).pack(side="left")
        ttk.Label(ops, text="列显示").pack(side="left", padx=(0, 5))
        self._compact_button = button(
            ops,
            "精简",
            lambda: self._set_columns("compact"),
        )
        self._compact_button.pack(side="left", padx=(0, 4))
        self._full_button = button(
            ops,
            "完整",
            lambda: self._set_columns("full"),
        )
        self._full_button.pack(side="left", padx=(0, 4))
        self._field_button = button(
            ops,
            f"字段设置（{len(self._visible_fields)}）",
            self._show_field_settings,
        )
        self._field_button.pack(side="left", padx=(0, 4))
        button(ops, "分组", self._show_groups).pack(side="left", padx=(0, 4))
        button(
            ops,
            "打开后台",
            self._open_store_backend,
        ).pack(side="left", padx=(0, 4))
        self._edit_button = button(
            ops,
            "编辑",
            self._edit_store,
        )
        self._edit_button.pack(side="left", padx=(0, 4))
        self._copy_button = button(
            ops,
            "复制密文",
            self._copy_export,
        )
        self._copy_button.pack(side="left", padx=(0, 4))
        self._export_button = button(
            ops,
            "导出密文",
            self._export_encrypted,
        )
        self._export_button.pack(side="left", padx=(0, 4))
        self._import_button = button(
            ops,
            "导入密文",
            self._import_encrypted,
        )
        self._import_button.pack(side="left", padx=(0, 4))
        self._delete_button = button(
            ops,
            "删除",
            self._delete_store,
            "Danger",
        )
        self._delete_button.pack(side="left", padx=(0, 4))
        button(ops, "清理缓存", self._clear_store_cache).pack(side="left")

        table_frame = ttk.Frame(self, style="Panel.TFrame")
        table_frame.grid(row=3, column=0, sticky="nsew")
        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)
        self.tree = ttk.Treeview(
            table_frame,
            columns=self._all_columns,
            show="headings",
            selectmode="extended",
        )
        widths = {
            key: width for key, _label, width in self.field_definitions
        }
        widths.update({"selected": 38, "sequence": 54, "action": 148})
        labels = {
            "selected": "☐",
            "sequence": "序号",
            "action": "操作",
            **{
                key: label
                for key, label, _width in self.field_definitions
            },
        }
        centered = {
            "selected",
            "sequence",
            "login",
            "in_sale",
            "warehouse",
            "product_total",
            "quota",
            "buyers_today",
            "buyer_30d",
            "orders_today",
            "orders_yesterday",
            "product_click_today",
            "visitors_30d",
            "aftersale",
            "warn",
            "violation",
            "wait_send",
            "wait_receive",
            "recommend_exposure",
            "recommend_exposure_yesterday",
            "search_exposure_yesterday",
            "today_success",
        }
        for key in self._all_columns:
            self.tree.column(
                key,
                width=widths[key],
                minwidth=42,
                stretch=False,
                anchor="center" if key in centered else "w",
            )
            command = ""
            if key == "selected":
                command = self._toggle_page_selection
            elif key not in {"sequence", self.action_field}:
                def command(field=key):
                    return self._sort_by(field)
            self.tree.heading(key, text=labels[key], command=command)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(
            table_frame, orient="vertical", command=self.tree.yview
        )
        scroll.grid(row=0, column=1, sticky="ns")
        x_scroll = ttk.Scrollbar(
            table_frame, orient="horizontal", command=self.tree.xview
        )
        x_scroll.grid(row=1, column=0, sticky="ew")
        self.tree.configure(
            yscrollcommand=scroll.set,
            xscrollcommand=x_scroll.set,
        )
        self.tree.tag_configure("offline", foreground="#b54335")
        self.tree.tag_configure("warning", foreground="#947229")
        self.tree.tag_configure("online", foreground="#16785e")
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.tree.bind("<Double-1>", self._handle_tree_double_click)
        self.tree.bind("<Button-1>", self._handle_tree_click, add="+")
        self.tree.bind("<Button-3>", self._show_store_menu)

        footer = ttk.Frame(self, style="TFrame")
        footer.grid(row=4, column=0, sticky="ew", pady=(8, 0))
        self._previous_button = button(
            footer, "上一页", lambda: self._change_page(-1)
        )
        self._previous_button.pack(side="left")
        ttk.Label(
            footer,
            textvariable=self.footer_var,
            style="Subtitle.TLabel",
        ).pack(side="left", padx=12)
        self._next_button = button(
            footer, "下一页", lambda: self._change_page(1)
        )
        self._next_button.pack(side="left")
        ttk.Label(footer, text="每页", style="Subtitle.TLabel").pack(
            side="right", padx=(8, 4)
        )
        self._page_size_var = tk.StringVar(value=str(self._page_size))
        page_size_combo = ttk.Combobox(
            footer,
            textvariable=self._page_size_var,
            state="readonly",
            width=6,
            values=("20", "50", "100", "200"),
        )
        page_size_combo.pack(side="right")
        page_size_combo.bind(
            "<<ComboboxSelected>>", lambda _event: self._change_page_size()
        )
        self._apply_column_display()
        self._sync_action_buttons()

    def refresh(self) -> None:
        self._log_store_load("refresh_enter", -1)
        self.tree.delete(*self.tree.get_children())
        all_store_objects = self._load_stores_for_refresh()
        all_store_ids = {store.id for store in all_store_objects}
        search_term = self.search_var.get().strip().casefold()
        all_stores = [
            store
            for store in all_store_objects
            if not search_term
            or search_term in store.name.casefold()
            or search_term in store.app_id.casefold()
            or search_term in store.group_name.casefold()
        ]
        groups = sorted(
            set(self.db.list_store_groups())
            | {
                store.group_name
                for store in all_store_objects
                if store.group_name
            }
        )
        self.group_combo["values"] = ["全部分组", *groups]
        if self.group_var.get() not in self.group_combo["values"]:
            self.group_var.set("全部分组")
        count_all = len(all_store_objects)
        count_logged = sum(
            store.web_status == "已登录"
            for store in all_store_objects
        )
        for key, label in (
            ("all", f"全部店铺 {count_all}"),
            ("logged", f"已登录 {count_logged}"),
            ("unlogged", f"未登录 {count_all - count_logged}"),
        ):
            self.filter_buttons[key].configure(text=label)

        stores: list[Store] = []
        for store in all_stores:
            if self.active_filter == "logged" and store.web_status != "已登录":
                continue
            if (
                self.active_filter == "unlogged"
                and store.web_status in {"已登录", "检测中"}
            ):
                continue
            if (
                self.group_var.get() != "全部分组"
                and store.group_name != self.group_var.get()
            ):
                continue
            stores.append(store)
        self._refresh_metrics_if_due()
        stores.sort(key=self._sort_value, reverse=self.sort_desc)
        self._filtered_ids = [store.id for store in stores]
        self._checked_ids.intersection_update(all_store_ids)
        page_count = max(
            1, (len(stores) + self._page_size - 1) // self._page_size
        )
        self._page = min(max(1, self._page), page_count)
        start = (self._page - 1) * self._page_size
        page_stores = stores[start : start + self._page_size]
        self._page_ids = [store.id for store in page_stores]
        for index, store in enumerate(page_stores, start + 1):
            metrics = self._metrics_by_id.get(store.id, {})
            tags = ("online",) if store.web_status == "已登录" else ()
            if store.web_status in {"已掉线", "需重新扫码"}:
                tags = ("offline",)
            elif store.web_status in {
                "网络异常",
                "连接异常",
                "平台异常",
            }:
                tags = ("warning",)
            values: dict[str, Any] = {
                "selected": (
                    "☑" if store.id in self._checked_ids else "☐"
                ),
                "sequence": index,
                "name": store.name,
                "group": store.group_name or "未分组",
                "app_id": store.app_id,
                "secret": (
                    "********"
                    if store.id not in self._shown_secrets
                    else self.db.get_store_secret(store.id)[1]
                ),
                "login": self._display_login_status(store),
                "quota": self._format_quota(metrics),
                "stats_at": self._format_stats_time(metrics.get("stats_at")),
                "action": "编辑        测试",
            }
            for key in self.numeric_fields:
                values.setdefault(key, self._format_count(metrics.get(key)))
            for key in self.money_fields:
                values.setdefault(
                    key,
                    _money(self._as_metric_int(metrics.get(key))),
                )
            for key in self.score_fields:
                values.setdefault(key, self._format_score(metrics.get(key)))
            for key in self.percent_fields:
                values.setdefault(
                    key, self._format_percent(metrics.get(key))
                )
            values["product_total"] = self._format_count(
                metrics.get(
                    "product_total",
                    self._as_metric_int(metrics.get("in_sale"))
                    + self._as_metric_int(metrics.get("warehouse")),
                )
            )
            self.tree.insert(
                "",
                "end",
                iid=str(store.id),
                values=tuple(
                    values.get(key, "") for key in self._all_columns
                ),
                tags=tags,
            )
        for key, _label in (
            ("all", "全部店铺"),
            ("logged", "已登录"),
            ("unlogged", "未登录"),
        ):
            self.filter_buttons[key].configure(
                style=(
                    "Primary.TButton"
                    if key == self.active_filter
                    else "Secondary.TButton"
                )
            )
        first = start + 1 if stores else 0
        last = min(start + self._page_size, len(stores))
        self.footer_var.set(
            f"第 {self._page} / {page_count} 页 · "
            f"{first}–{last} / 共 {len(stores)} 条"
        )
        self._previous_button.configure(
            state="normal" if self._page > 1 else "disabled"
        )
        self._next_button.configure(
            state="normal" if self._page < page_count else "disabled"
        )
        self._select_all_button.configure(
            text=(
                "取消全选"
                if self._filtered_ids
                and all(
                    store_id in self._checked_ids
                    for store_id in self._filtered_ids
                )
                else "全选"
            )
        )
        self.tree.heading(
            "selected",
            text=(
                "☑"
                if self._page_ids
                and all(
                    store_id in self._checked_ids
                    for store_id in self._page_ids
                )
                else "☐"
            ),
        )
        if self.selected_store is not None:
            self.selected_store = self.db.get_store(self.selected_store.id)
        self._sync_action_buttons()

    def _load_stores_for_refresh(self) -> list[Store]:
        stores = self.db.list_stores()
        self._log_store_load("primary", len(stores))
        if stores:
            return stores
        reload_database = getattr(
            self.window,
            "reload_account_database",
            None,
        )
        if callable(reload_database):
            fresh_db = reload_database()
            if fresh_db is not None:
                recovered = fresh_db.list_stores()
                if recovered:
                    self.db = fresh_db
                    return recovered
        direct_stores = Database.direct_list_stores(
            getattr(self.db, "path", "")
        )
        if direct_stores:
            self._log_store_recovery(
                "direct_sqlite_fallback",
                len(direct_stores),
            )
        return direct_stores

    def _log_store_load(self, event: str, count: int) -> None:
        paths = getattr(self.window, "paths", None)
        data_dir = getattr(paths, "data_dir", None)
        if data_dir is None:
            return
        try:
            line = json.dumps(
                {
                    "at": datetime.now(UTC).isoformat(timespec="seconds"),
                    "event": event,
                    "database": str(getattr(self.db, "path", "")),
                    "stores": int(count),
                },
                ensure_ascii=False,
            )
            path = data_dir / "store_load_recovery.log"
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except OSError:
            pass

    def _log_store_recovery(self, event: str, count: int) -> None:
        self._log_store_load(event, count)

    def _schedule_search_refresh(self, *_args: object) -> None:
        if self._search_refresh_job is not None:
            try:
                self.after_cancel(self._search_refresh_job)
            except tk.TclError:
                pass
        self._search_refresh_job = self.after(
            180,
            self._run_scheduled_search_refresh,
        )

    def _run_scheduled_search_refresh(self) -> None:
        self._search_refresh_job = None
        self._reset_and_refresh()

    def invalidate_metrics_cache(self) -> None:
        self._metrics_cache_at = 0.0

    def _refresh_metrics_if_due(self) -> None:
        now = time.monotonic()
        if (
            self._metrics_by_id
            and now - self._metrics_cache_at
            < self._METRICS_CACHE_SECONDS
        ):
            return
        self._metrics_by_id = self.db.store_metric_map()
        self._metrics_cache_at = now

    def _schedule_incremental_refresh(self) -> None:
        if not hasattr(self, "tk"):
            return
        if not self.winfo_ismapped():
            return
        if getattr(self, "_data_refresh_job", None) is not None:
            return
        self._data_refresh_job = self.after(
            self._INCREMENTAL_REFRESH_MS,
            self._run_incremental_refresh,
        )

    def _run_incremental_refresh(self) -> None:
        self._data_refresh_job = None
        if self.winfo_exists():
            self.refresh()

    def _cancel_incremental_refresh(self) -> None:
        job = getattr(self, "_data_refresh_job", None)
        self._data_refresh_job = None
        if job is None:
            return
        try:
            self.after_cancel(job)
        except tk.TclError:
            pass

    def _on_select(self, _event=None) -> None:
        selection = self.tree.selection()
        self.selected_store = (
            self.db.get_store(int(selection[0])) if selection else None
        )
        self._sync_action_buttons()

    def _change_filter(self, key: str) -> None:
        self.active_filter = key
        self._page = 1
        self.refresh()

    def _reset_and_refresh(self) -> None:
        self._page = 1
        self.refresh()

    def _toggle_store_selection(self) -> None:
        all_checked = bool(self._filtered_ids) and all(
            store_id in self._checked_ids
            for store_id in self._filtered_ids
        )
        if all_checked:
            self._checked_ids.difference_update(self._filtered_ids)
        else:
            self._checked_ids.update(self._filtered_ids)
        self.refresh()

    def _toggle_page_selection(self) -> None:
        all_checked = bool(self._page_ids) and all(
            store_id in self._checked_ids for store_id in self._page_ids
        )
        if all_checked:
            self._checked_ids.difference_update(self._page_ids)
        else:
            self._checked_ids.update(self._page_ids)
        self.refresh()

    def _toggle_checked(self, store_id: int) -> None:
        if store_id in self._checked_ids:
            self._checked_ids.remove(store_id)
        else:
            self._checked_ids.add(store_id)
        self.refresh()

    def _selected_stores(self) -> list[Store]:
        by_id = {
            store.id: store
            for store in self.db.list_stores()
        }
        return [
            by_id[store_id]
            for store_id in self._filtered_ids
            if store_id in self._checked_ids and store_id in by_id
        ]

    def _primary_store(self) -> Store | None:
        selected = self._selected_stores()
        if selected:
            return selected[0]
        if self.selected_store is not None:
            return self.selected_store
        if self._filtered_ids:
            return self.db.get_store(self._filtered_ids[0])
        return None

    def _set_columns(self, mode: str) -> None:
        self._display_mode = "compact" if mode == "compact" else "full"
        self.db.set_setting("store_display_mode", self._display_mode)
        self._apply_column_display()

    def _apply_column_display(self) -> None:
        data_fields = (
            self.compact_fields
            if self._display_mode == "compact"
            else tuple(self._visible_fields)
        )
        self.tree["displaycolumns"] = (
            *self.fixed_left_fields,
            *data_fields,
            self.action_field,
        )
        if self._compact_button is not None:
            self._compact_button.configure(
                style=(
                    "Primary.TButton"
                    if self._display_mode == "compact"
                    else "Secondary.TButton"
                )
            )
        if self._full_button is not None:
            self._full_button.configure(
                style=(
                    "Primary.TButton"
                    if self._display_mode == "full"
                    else "Secondary.TButton"
                )
            )
        if self._field_button is not None:
            self._field_button.configure(
                text=f"字段设置（{len(self._visible_fields)}）"
            )

    def _show_field_settings(self) -> None:
        dialog = tk.Toplevel(self)
        dialog.title("店铺字段设置")
        dialog.transient(self.winfo_toplevel())
        dialog.geometry("660x650")
        dialog.minsize(560, 480)
        dialog.columnconfigure(0, weight=1)
        dialog.rowconfigure(1, weight=1)
        ttk.Label(
            dialog,
            text="店铺字段设置",
            style="Section.TLabel",
        ).grid(row=0, column=0, sticky="w", padx=20, pady=(16, 8))
        canvas = tk.Canvas(dialog, bg=PANEL, highlightthickness=0)
        canvas.grid(row=1, column=0, sticky="nsew", padx=(14, 0))
        scroll = ttk.Scrollbar(
            dialog, orient="vertical", command=canvas.yview
        )
        scroll.grid(row=1, column=1, sticky="ns", padx=(0, 12))
        canvas.configure(yscrollcommand=scroll.set)
        grid = ttk.Frame(canvas, style="Panel.TFrame", padding=8)
        window_id = canvas.create_window((0, 0), window=grid, anchor="nw")
        grid.bind(
            "<Configure>",
            lambda _event: canvas.configure(scrollregion=canvas.bbox("all")),
        )
        canvas.bind(
            "<Configure>",
            lambda event: canvas.itemconfigure(window_id, width=event.width),
        )
        canvas.bind(
            "<MouseWheel>",
            lambda event: canvas.yview_scroll(
                int(-1 * (event.delta / 120)), "units"
            ),
        )
        variables: dict[str, tk.BooleanVar] = {}
        for index, (key, label, _width) in enumerate(self.field_definitions):
            variable = tk.BooleanVar(value=key in self._visible_fields)
            variables[key] = variable
            tk.Checkbutton(
                grid,
                text=label,
                variable=variable,
                anchor="w",
                bg=PANEL,
                fg=TEXT,
                activebackground=PANEL,
                selectcolor="white",
                bd=0,
            ).grid(
                row=index // 2,
                column=index % 2,
                sticky="w",
                padx=16,
                pady=4,
            )

        def apply_fields() -> None:
            visible = [
                key
                for key, _label, _width in self.field_definitions
                if variables[key].get()
            ]
            if not visible:
                messagebox.showwarning(
                    "字段设置", "至少保留一个字段。", parent=dialog
                )
                return
            self._visible_fields = visible
            self.db.set_setting(
                "store_visible_fields",
                json.dumps(visible, ensure_ascii=False),
            )
            self._display_mode = "full"
            self.db.set_setting("store_display_mode", "full")
            self._apply_column_display()
            dialog.destroy()

        actions = ttk.Frame(
            dialog, style="TFrame", padding=(14, 10, 14, 14)
        )
        actions.grid(row=2, column=0, columnspan=2, sticky="e")
        button(actions, "取消", dialog.destroy).pack(side="left", padx=(0, 8))
        button(actions, "保存字段", apply_fields, "Primary").pack(side="left")
        dialog.grab_set()
        dialog.bind("<Escape>", lambda _event: dialog.destroy())
        _center_dialog(self, dialog)

    def _batch_import_credentials(
        self,
        records: list[tuple[str, str, str]] | None = None,
        group_name: str = "",
    ) -> bool:
        if not self._has_permission("stores_edit"):
            return False
        if records is None:
            StoreBatchDialog(self, self._batch_import_credentials)
            return True
        self.window.set_status(
            f"正在加密并保存 {len(records)} 家店铺…",
            busy=True,
        )

        def work(progress) -> tuple[int, int, list[str]]:
            if progress:
                progress(
                    f"正在批量写入 {len(records)} 家店铺",
                    0,
                    len(records),
                )
            return self.db.upsert_stores(records, group_name)

        def done(result: tuple[int, int, list[str]]) -> None:
            created, updated, errors = result
            self.window.store_data_changed()
            self.window.set_status(
                f"批量登录完成：新增 {created} 家，更新 {updated} 家"
                + (f"，失败 {len(errors)} 家" if errors else "")
            )
            if errors:
                messagebox.showwarning(
                    "部分店铺未保存",
                    "\n".join(errors[:8]),
                    parent=self,
                )

        return self.window.run_task(
            work,
            done,
            lambda error: messagebox.showerror(
                "批量保存失败",
                error,
                parent=self,
            ),
            task_scope="stores:batch-import",
        )

    def _show_groups(self) -> None:
        from .business_tools import StoreGroupsPage

        dialog = tk.Toplevel(self)
        dialog.title("店铺分组管理")
        dialog.geometry("980x680")
        dialog.minsize(820, 540)
        dialog.transient(self.winfo_toplevel())
        dialog.columnconfigure(0, weight=1)
        dialog.rowconfigure(0, weight=1)
        page = StoreGroupsPage(dialog, self.window)
        page.grid(row=0, column=0, sticky="nsew")
        page.refresh()

        def close() -> None:
            dialog.destroy()
            self.refresh()

        actions = ttk.Frame(dialog, style="TFrame", padding=(14, 8))
        actions.grid(row=1, column=0, sticky="e")
        button(actions, "关闭", close).pack()
        dialog.protocol("WM_DELETE_WINDOW", close)
        dialog.grab_set()
        dialog.bind("<Escape>", lambda _event: close())
        _center_dialog(self, dialog)

    def _open_store_backend(self, store: Store | None = None) -> None:
        if not self._has_permission("stores_view"):
            return
        store = store or self._primary_store()
        if store is None:
            messagebox.showinfo(
                "请选择店铺", "请先选择一家店铺。", parent=self
            )
            return
        self.window.set_status(
            f"正在打开“{store.name}”的微信后台…", busy=True
        )

        def done(_result) -> None:
            self.window.set_status("微信商家后台已关闭")
            self.window.store_data_changed()

        self.window.run_task(
            lambda _progress: self.window.web_session.open_official_backend(
                store
            ),
            done,
            lambda error: messagebox.showerror(
                "打开后台失败", error, parent=self
            ),
            log_title="打开官方商家后台",
            task_scope=f"stores:backend:{store.id}",
            command="stores.view",
        )

    def _refresh_store_data(
        self,
        stores: list[Store] | None = None,
    ) -> None:
        if not self._has_permission("stores_view"):
            return
        stores = stores or self._selected_stores() or self.db.list_stores()
        account_sync = getattr(self.window, "account_store_sync", None)
        if not stores and account_sync is None:
            messagebox.showinfo("没有店铺", "请先添加店铺。", parent=self)
            return
        self.window.set_status(
            "正在联网同步账户店铺并刷新数据…",
            busy=True,
        )

        def work(progress) -> tuple[list[str], dict[str, int], int]:
            errors: list[str] = []
            sync_result = {"created": 0, "updated": 0, "logins": 0}
            if account_sync is not None:
                try:
                    sync_result = account_sync.pull()
                except Exception as exc:
                    errors.append(f"云端店铺同步失败：{exc}")
                latest_stores = self.db.list_stores()
            else:
                latest_stores = stores
            if not latest_stores:
                return errors, sync_result, 0

            def refresh_one(store: Store) -> tuple[str, list[str]]:
                store_errors: list[str] = []
                try:
                    self.window.web_session.probe(store)
                except Exception as exc:
                    store_errors.append(
                        f"{store.name}: 登录状态检测失败：{exc}"
                    )
                try:
                    self.window.service.refresh_store_metrics(store)
                except Exception as exc:
                    store_errors.append(f"{store.name}: {exc}")
                return store.name, store_errors

            try:
                configured = int(
                    os.environ.get(
                        "WEIMI_STORE_REFRESH_WORKERS",
                        "6",
                    )
                )
            except ValueError:
                configured = 6
            workers = min(8, max(1, configured, 1))
            workers = min(workers, len(latest_stores))
            completed = 0
            total = len(latest_stores)
            with ThreadPoolExecutor(
                max_workers=workers,
                thread_name_prefix="store-refresh",
            ) as executor:
                futures = {
                    executor.submit(refresh_one, store): store
                    for store in latest_stores
                }
                for future in as_completed(futures):
                    store = futures[future]
                    completed += 1
                    try:
                        _name, store_errors = future.result()
                    except Exception as exc:
                        store_errors = [f"{store.name}: {exc}"]
                    errors.extend(store_errors)
                    self.window._post(
                        self._schedule_incremental_refresh
                    )
                    if progress:
                        progress(
                            f"已刷新 {completed}/{total} 家：{store.name}",
                            completed,
                            total,
                        )
            return errors, sync_result, total

        def done(result: tuple[list[str], dict[str, int], int]) -> None:
            errors, sync_result, refreshed_total = result
            failed_names = {
                error.split(":", 1)[0]
                for error in errors
            }
            self.window.set_status(
                "账户店铺已联网同步并刷新："
                f"新增 {sync_result.get('created', 0)} 家，"
                f"恢复登录 {sync_result.get('logins', 0)} 家，"
                f"刷新成功 "
                f"{max(0, refreshed_total - len(failed_names))} 家"
                + (
                    f"，失败 {len(failed_names)} 家"
                    if failed_names
                    else ""
                )
            )
            self._cancel_incremental_refresh()
            self.invalidate_metrics_cache()
            self.window.store_data_changed()
            if errors:
                messagebox.showwarning(
                    "部分店铺刷新失败",
                    "\n".join(errors[:8]),
                    parent=self,
                )

        self.window.run_task(
            work,
            done,
            lambda error: messagebox.showerror(
                "店铺数据刷新失败", error, parent=self
            ),
            log_title="同步账户店铺并刷新数据",
            task_scope="stores:refresh-data",
            command="stores.view",
        )

    def refresh_after_upload(self, store_ids: set[int]) -> None:
        stores = [
            store
            for store in self.db.list_stores()
            if store.id in store_ids
        ]
        if not stores:
            return
        self._refresh_store_data(stores)

    def _clear_store_cache(self) -> None:
        if not messagebox.askyesno(
            "清理缓存",
            "清理已过期的登录快照和浏览器缓存？当前有效登录态会保留。",
            parent=self,
        ):
            return
        removed = self.window.web_session.cleanup_cache()
        self.window.set_status(f"缓存清理完成，删除 {removed} 项")
        self.refresh()

    def _add_store(self) -> None:
        if not self._has_permission("stores_edit"):
            return
        StoreDialog(self, self._create_store)

    def _create_store(self, values: dict[str, str]) -> bool:
        display_name = (
            values["name"].strip() or f"店铺{values['app_id'][-4:]}"
        )

        def work(_progress) -> int:
            return self.db.add_store(
                display_name,
                values["app_id"],
                values["secret"],
                values["group_name"],
            )

        def done(store_id: int) -> None:
            self.window.set_status(
                "店铺已添加，登录凭据已保存"
                if values.get("secret")
                else "网页登录店铺已添加"
            )
            self.window.store_data_changed()
            if values["name"].strip():
                return
            store = self.db.get_store(store_id)

            def profile_work(_progress) -> str:
                if store is None:
                    return ""
                self.window.service.validate_store(store)
                self.window.service.sync_store_profile(store)
                profile = self.db.get_store_profile(store_id) or {}
                return str(profile.get("nickname") or "")

            def profile_done(name: str) -> None:
                current = self.db.get_store(store_id)
                if not name or current is None:
                    return
                try:
                    self.db.update_store(
                        store_id,
                        name,
                        current.group_name,
                        None,
                    )
                except Exception as exc:
                    self.window.set_status(
                        f"店铺已保存；名称云端同步失败：{exc}"
                    )
                    return
                self.window.store_data_changed()
                self.window.set_status(f"店铺名称已自动获取：{name}")

            self.window.run_task(
                profile_work,
                profile_done,
                lambda error: self.window.set_status(
                    f"店铺已保存；名称自动获取失败：{error}"
                ),
                log_title="自动获取店铺名称",
                task_scope=f"stores:auto-name:{store_id}",
            )

        self.window.run_task(
            work,
            done,
            lambda error: messagebox.showerror(
                "添加失败",
                error,
                parent=self,
            ),
            log_title="添加店铺",
            task_scope="stores:create",
        )
        return True

    def _edit_store(self, store: Store | None = None) -> None:
        if not self._has_permission("stores_edit"):
            return
        if store is None:
            selected = self._selected_stores()
            store = selected[0] if selected else self.selected_store
        if store is None:
            messagebox.showinfo(
                "请选择店铺", "请先选择一家店铺。", parent=self
            )
            return

        def save(values: dict[str, str]) -> bool:
            def work(_progress) -> None:
                self.db.update_store(
                    store.id,
                    values["name"],
                    values["group_name"],
                    values["secret"] or None,
                )

            def done(_result: None) -> None:
                self.window.store_data_changed()
                self.window.set_status("店铺信息已更新")

            self.window.run_task(
                work,
                done,
                lambda error: messagebox.showerror(
                    "保存失败",
                    error,
                    parent=self,
                ),
                log_title="编辑店铺",
                task_scope=f"stores:edit:{store.id}",
            )
            return True

        StoreDialog(self, save, store)

    def _delete_store(self) -> None:
        if not self._has_permission("stores_delete"):
            return
        stores = self._selected_stores()
        if not stores:
            messagebox.showinfo(
                "请选择店铺", "请先勾选一家店铺。", parent=self
            )
            return
        prompt = (
            f"确认删除选中的“{stores[0].name}”吗？\n"
            if len(stores) == 1
            else f"确认删除选中的 {len(stores)} 家店铺？\n"
        )
        prompt += "登录信息会一并清除，本地订单数据不会立即删除。"
        if not messagebox.askyesno("删除店铺", prompt, parent=self):
            return
        deleted_app_ids = [store.app_id for store in stores]
        if getattr(self.window, "account_store_sync", None) is None:
            for store in stores:
                self.db.delete_store(store.id)
                self._checked_ids.discard(store.id)
            self.window.store_data_changed()
            self.window.set_status("店铺已删除")
            return

        self.window.set_status(
            f"正在从云端删除 {len(stores)} 家店铺…",
            busy=True,
        )

        def work(_progress) -> dict[str, Any]:
            delete_one = getattr(
                self.db,
                "delete_store_for_app_id",
                None,
            )
            if callable(delete_one):
                removed = 0
                for store in stores:
                    result = delete_one(store.id, store.app_id)
                    removed += int(result.get("removed") or 0)
                return {"stored": True, "removed": removed}
            result = self.window.account_store_sync.delete_now(
                deleted_app_ids
            )
            for store in stores:
                self.db.delete_store(store.id)
            return result

        def done(_result: dict[str, Any]) -> None:
            for store in stores:
                self._checked_ids.discard(store.id)
            self.window.store_data_changed()
            self.window.set_status("店铺已从云端和本机删除")

        self.window.run_task(
            work,
            done,
            lambda error: messagebox.showerror(
                "删除店铺失败",
                error,
                parent=self,
            ),
            log_title="删除云端店铺",
            task_scope="stores:delete",
        )

    def _copy_export(self) -> None:
        if not self._has_permission("stores_export"):
            return
        stores = self._selected_stores()
        if not stores:
            messagebox.showinfo(
                "请选择店铺", "请先勾选要导出的店铺。", parent=self
            )
            return
        try:
            envelope = self._encrypted_store_export(stores)
        except Exception as exc:
            messagebox.showerror("生成密文失败", str(exc), parent=self)
            return
        self.clipboard_clear()
        self.clipboard_append(json.dumps(envelope, ensure_ascii=False))
        self.window.set_status(
            f"已复制 {len(stores)} 家店铺的加密数据"
        )

    def _encrypted_store_export(
        self,
        stores: list[Store],
    ) -> dict[str, Any]:
        context = getattr(self.window, "auth_context", None)
        key = getattr(context, "vault_key", None)
        account_id = str(getattr(context, "account_id", "") or "")
        if not key or not account_id:
            raise ValueError("当前账户没有可用的加密密钥，请使用邮箱账户重新登录")
        selected_ids = {store.app_id for store in stores}
        snapshot = self.db.export_store_accounts()
        snapshot["stores"] = [
            item
            for item in snapshot.get("stores") or []
            if isinstance(item, dict)
            and str(item.get("app_id") or "") in selected_ids
        ]
        envelope = encrypt_store_snapshot(snapshot, key, account_id)
        envelope["store_count"] = len(snapshot["stores"])
        return envelope

    def _export_encrypted(self) -> None:
        if not self._has_permission("stores_export"):
            return
        stores = self._selected_stores()
        if not stores:
            messagebox.showinfo(
                "请选择店铺",
                "请先勾选要导出的店铺。",
                parent=self,
            )
            return
        try:
            envelope = self._encrypted_store_export(stores)
        except Exception as exc:
            messagebox.showerror("生成密文失败", str(exc), parent=self)
            return
        path = filedialog.asksaveasfilename(
            parent=self,
            title="导出店铺密文",
            defaultextension=".llshop",
            filetypes=[
                ("澜琅店铺密文", "*.llshop"),
                ("JSON 文件", "*.json"),
            ],
            initialfile=f"澜琅店铺_{datetime.now():%Y%m%d_%H%M%S}.llshop",
        )
        if not path:
            return
        Path(path).write_text(
            json.dumps(envelope, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self.window.set_status(
            f"已加密导出 {len(stores)} 家店铺：{Path(path).name}"
        )

    def _import_encrypted(self) -> None:
        if not self._has_permission("stores_export"):
            return
        path = filedialog.askopenfilename(
            parent=self,
            title="导入店铺密文",
            filetypes=[
                ("澜琅店铺密文", "*.llshop *.json"),
                ("所有文件", "*.*"),
            ],
        )
        if not path:
            return
        context = getattr(self.window, "auth_context", None)
        key = getattr(context, "vault_key", None)
        account_id = str(getattr(context, "account_id", "") or "")
        if not key or not account_id:
            messagebox.showerror(
                "无法导入",
                "当前账户没有可用的解密密钥，请使用邮箱账户重新登录。",
                parent=self,
            )
            return
        try:
            envelope = json.loads(
                Path(path).read_text(encoding="utf-8")
            )
            snapshot = decrypt_store_snapshot(
                envelope,
                key,
                account_id,
            )
            result = self.db.import_store_accounts(snapshot)
        except Exception as exc:
            messagebox.showerror(
                "导入密文失败",
                f"{exc}\n\n请确认文件来自同一澜琅邮箱账户。",
                parent=self,
            )
            return
        self.window.store_data_changed()
        self.refresh()
        self.window.set_status(
            "店铺密文导入完成："
            f"新增 {result['created']}，更新 {result['updated']}，"
            f"恢复登录 {result['logins']}"
        )

    def _validate_store(self, store: Store | None = None) -> None:
        if not self._has_permission("stores_edit"):
            return
        if store is None:
            selected = self._selected_stores()
            store = selected[0] if selected else self.selected_store
        if store is None:
            messagebox.showinfo(
                "请选择店铺", "请先选择一家店铺。", parent=self
            )
            return
        self.window.set_status(
            f"正在测试“{store.name}”的接口凭证…", busy=True
        )

        def done(_result: Any) -> None:
            self.window.set_status(f"{store.name} 凭证有效")
            self.window.store_data_changed()
            messagebox.showinfo(
                "测试成功",
                "Access Token 获取成功，店铺接口凭证可用。",
                parent=self,
            )

        def failed(error: str) -> None:
            self.window.set_status("店铺凭证测试失败")
            messagebox.showerror("测试失败", error, parent=self)

        self.window.run_task(
            lambda _progress: self.window.service.validate_store(store),
            done,
            failed,
            log_title="验证店铺接口凭证",
            task_scope=f"stores:validate:{store.id}",
            command="stores.edit",
            store_app_id=store.app_id,
        )

    def _scan_login(self) -> None:
        if not self._has_permission("stores_login"):
            return
        stores = self._selected_stores() or self.db.list_stores()
        if not stores:
            messagebox.showinfo("没有店铺", "请先添加店铺。", parent=self)
            return
        pending_ids = {
            store.id
            for store in stores
            if store.web_status != "已登录"
        }
        if not pending_ids:
            messagebox.showinfo(
                "无需登录",
                "所选店铺当前都已登录。如需重新授权，请单独选择店铺后扫码登录。",
                parent=self,
            )
            return
        stores_by_id = {store.id: store for store in stores}
        authorization_host = next(
            store for store in stores if store.id in pending_ids
        )
        self.window.set_status(
            f"智能扫码已开始：待登录 {len(pending_ids)} 家店铺",
            busy=True,
        )

        def work(
            progress,
        ) -> tuple[set[int], list[str], list[str]]:
            errors: list[str] = []
            warnings: list[str] = []
            matched_ids: set[int] = set()
            remaining = set(pending_ids)
            attempts = 0
            max_attempts = len(stores) + 5

            def on_store_saved(_store_id: int, _name: str) -> None:
                self.window._post(self.window.store_login_updated)

            while remaining and attempts < max_attempts:
                attempts += 1
                host_id = next(
                    store.id
                    for store in stores
                    if store.id in remaining
                )
                host = stores_by_id[host_id]
                if progress:
                    progress(
                        f"等待第 {attempts} 次扫码 · 可扫描任一未登录店铺"
                        f" · 剩余 {len(remaining)} 家",
                        len(pending_ids) - len(remaining),
                        len(pending_ids),
                    )
                try:
                    payload = self.window.web_session.interactive_login(
                        host,
                        progress=progress,
                        allowed_store_ids=pending_ids,
                        on_store_saved=on_store_saved,
                    )
                    payload_ids = [
                        int(value)
                        for value in (
                            payload.get("matched_store_ids")
                            or [payload.get("matched_store_id") or host.id]
                        )
                        if value
                    ]
                    matched_ids.update(payload_ids)
                    remaining.difference_update(payload_ids)
                    excluded_ids = {
                        int(value)
                        for value in payload.get("excluded_main_ids") or []
                        if value
                    }
                    remaining.difference_update(excluded_ids)
                    if payload.get("shop_switch_login"):
                        account_count = int(
                            payload.get("shop_switch_count")
                            or len(payload_ids)
                        )
                        warnings.append(
                            f"本微信识别到 {account_count} 家可登录店铺，"
                            f"已保存 {len(payload_ids)} 家"
                        )
                        excluded = list(
                            payload.get("excluded_main_names") or []
                        )
                        if excluded:
                            warnings.append(
                                "同时存在主账号与子账号，已跳过主账号："
                                + "、".join(excluded[:8])
                            )
                        failures = list(payload.get("failures") or [])
                        if failures:
                            warnings.append(
                                "部分店铺未识别完成："
                                + "、".join(failures[:8])
                            )
                    extra_names = [
                        name
                        for store_id, name in zip(
                            payload_ids,
                            payload.get("matched_store_names") or [],
                        )
                        if store_id not in pending_ids
                    ]
                    if extra_names:
                        warnings.append(
                            "本次扫码同时识别并保存了："
                            + "、".join(extra_names[:10])
                        )
                    self.window._post(self.window.store_data_changed)
                except Exception as exc:
                    errors.append(str(exc))
                    if (
                        "登录窗口意外关闭" in str(exc)
                        or "没有捕获到登录 Cookie" in str(exc)
                        or "未确认扫码店铺身份" in str(exc)
                    ):
                        break
            return matched_ids, warnings, errors

        def done(
            result: tuple[set[int], list[str], list[str]],
        ) -> None:
            matched_ids, warnings, errors = result
            self.window.store_data_changed()
            remaining = len(pending_ids - matched_ids)
            self.window.set_status(
                f"扫码登录结束：识别登录 {len(matched_ids)} 家"
                + (f"，仍有 {remaining} 家未登录" if remaining else "")
            )
            notices = [*warnings, *errors]
            if notices:
                messagebox.showwarning(
                    "扫码登录结果",
                    "\n".join(notices[:8]),
                    parent=self,
                )

        self.window.run_task(
            work,
            done,
            lambda error: messagebox.showerror(
                "扫码登录失败", error, parent=self
            ),
            log_title="多店铺扫码登录",
            task_scope="stores:scan-login",
            command="stores.login",
            store_app_id=authorization_host.app_id,
        )

    def _import_cookie(self, store: Store | None = None) -> None:
        if not self._has_permission("stores_login"):
            return
        store = store or self._primary_store()
        if store is None:
            messagebox.showinfo(
                "请选择店铺", "请先选择一家店铺。", parent=self
            )
            return

        def save(header: str) -> None:
            self.window.set_status("正在验证导入的 Cookie…", busy=True)

            def done(_payload) -> None:
                refreshed = self.db.get_store(store.id)
                self.window.store_data_changed()
                if refreshed:
                    self.window.set_status(
                        f"Cookie 已保存：{refreshed.web_status}"
                    )
                messagebox.showinfo(
                    "Cookie 已保存",
                    "登录状态已检测并更新。",
                    parent=self,
                )

            self.window.run_task(
                lambda _progress: self.window.web_session.import_cookie_header(
                    store, header
                ),
                done,
                lambda error: messagebox.showerror(
                    "Cookie 验证失败", error, parent=self
                ),
                log_title="导入并验证网页登录 Cookie",
                task_scope=f"stores:cookie:{store.id}",
                command="stores.login",
                store_app_id=store.app_id,
            )

        CookieImportDialog(self, store.name, save)

    def _probe_login(self, store: Store | None = None) -> None:
        if not self._has_permission("stores_login"):
            return
        store = store or self._primary_store()
        if store is None:
            messagebox.showinfo(
                "请选择店铺", "请先选择一家店铺。", parent=self
            )
            return
        self.window.set_status("正在检测网页登录…", busy=True)

        def done(result) -> None:
            status, detail = result
            self.window.store_data_changed()
            self.window.set_status(f"网页登录状态：{status} · {detail}")

        self.window.run_task(
            lambda _progress: self.window.web_session.probe(store),
            done,
            lambda error: messagebox.showerror(
                "登录检测失败", error, parent=self
            ),
            log_title="检测店铺网页登录状态",
            task_scope=f"stores:probe:{store.id}",
            command="stores.login",
            store_app_id=store.app_id,
        )

    def _logout_web(self, store: Store | None = None) -> None:
        if not self._has_permission("stores_login"):
            return
        store = store or self._primary_store()
        if store is None:
            return
        if not messagebox.askyesno(
            "退出网页会话",
            "退出后依赖商家后台私有接口的功能将暂停，官方 API 不受影响。",
            parent=self,
        ):
            return
        def done(_result: Any) -> None:
            self.window.store_data_changed()
            self.window.set_status("网页会话已退出")

        self.window.run_task(
            lambda _progress: self.window.web_session.logout(store),
            done,
            lambda error: messagebox.showerror(
                "退出网页会话失败",
                error,
                parent=self,
            ),
            log_title="退出店铺网页会话",
            task_scope=f"stores:logout:{store.id}",
            command="stores.login",
            store_app_id=store.app_id,
        )

    def _offline_selected_stores(self) -> None:
        if not self._has_permission("stores_login"):
            return
        if getattr(self.window, "_auto_refresh_running", False):
            messagebox.showinfo(
                "自动检查进行中",
                "正在自动检查店铺登录状态，请等待本次检查完成后再离线店铺。",
                parent=self,
            )
            return
        selected = self._selected_stores()
        if not selected:
            messagebox.showinfo(
                "请选择店铺",
                "请先勾选需要离线的店铺。",
                parent=self,
            )
            return
        eligible = [
            store for store in selected if store.web_status == "已登录"
        ]
        skipped = len(selected) - len(eligible)
        if not eligible:
            messagebox.showinfo(
                "无需离线",
                "所选店铺当前都未登录，本次会全部跳过。",
                parent=self,
            )
            return
        if not messagebox.askyesno(
            "离线所选店铺",
            (
                f"将把所选店铺中 {len(eligible)} 家“已登录”店铺改为"
                f"“未登录”，并清除其网页登录会话。\n\n"
                f"另有 {skipped} 家本来未登录，会直接跳过。\n"
                "店铺资料和业务数据不会删除，是否继续？"
            ),
            parent=self,
        ):
            return

        store_ids = [store.id for store in eligible]
        detail = "已离线所选店铺"
        self.window.set_status(
            f"正在后台离线 {len(store_ids)} 家店铺…",
            busy=True,
        )

        def work(progress):
            if progress:
                progress(
                    f"正在批量离线 {len(store_ids)} 家店铺",
                    0,
                    1,
                )
            result = self.db.clear_selected_web_sessions(
                store_ids,
                detail,
            )
            if progress:
                progress("店铺登录状态已保存", 1, 1)
            return result

        def done(result: dict[str, int]) -> None:
            cleared = int(result.get("cleared") or 0)
            database_skipped = int(result.get("skipped") or 0)
            self.window.store_login_updated()
            message = (
                f"已离线 {cleared} 家店铺"
                + (
                    f"，跳过 {database_skipped} 家未登录店铺"
                    if database_skipped
                    else ""
                )
            )
            self.status_line_var.set(message)
            self.window.set_status(message)

        def failed(error: str) -> None:
            self.window.set_status("离线所选店铺失败")
            messagebox.showerror(
                "离线所选店铺失败",
                error,
                parent=self,
            )

        self.window.run_task(
            work,
            done,
            failed,
            log_title="离线所选店铺",
            task_scope="stores:offline-selected",
        )

    def _show_store_menu(self, event: tk.Event) -> None:
        row_id = self.tree.identify_row(event.y)
        if not row_id:
            return
        store = self.db.get_store(int(row_id))
        if store is None:
            return
        self.tree.selection_set(row_id)
        self.tree.focus(row_id)
        self.selected_store = store
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(
            label="打开后台",
            command=lambda selected=store: self._open_store_backend(selected),
        )
        menu.add_command(
            label="编辑店铺",
            command=lambda selected=store: self._edit_store(selected),
        )
        menu.add_command(
            label="测试凭证",
            command=lambda selected=store: self._validate_store(selected),
        )
        menu.add_separator()
        menu.add_command(label="扫码登录", command=self._scan_login)
        menu.add_command(
            label="检测登录状态",
            command=lambda selected=store: self._probe_login(selected),
        )
        menu.add_command(
            label="导入 Cookie",
            command=lambda selected=store: self._import_cookie(selected),
        )
        menu.add_command(
            label="退出网页会话",
            command=lambda selected=store: self._logout_web(selected),
        )
        menu.add_separator()
        menu.add_command(label="复制密文", command=self._copy_export)
        menu.add_command(label="删除店铺", command=self._delete_store)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _handle_tree_click(self, event: tk.Event) -> str | None:
        row_id = self.tree.identify_row(event.y)
        column_id = self.tree.identify_column(event.x)
        if not row_id or not column_id:
            return None
        columns = tuple(self.tree["displaycolumns"])
        try:
            key = columns[int(column_id.lstrip("#")) - 1]
        except (IndexError, ValueError):
            return None
        store_id = int(row_id)
        if key == "selected":
            self._toggle_checked(store_id)
            return "break"
        if key == "secret":
            if store_id in self._shown_secrets:
                self._shown_secrets.remove(store_id)
            else:
                self._shown_secrets.add(store_id)
                self.after(
                    60000,
                    lambda sid=store_id: self._hide_secret(sid),
                )
            self.refresh()
            return "break"
        if key == self.action_field:
            bbox = self.tree.bbox(row_id, key)
            if bbox:
                store = self.db.get_store(store_id)
                if store is not None:
                    self.selected_store = store
                    if event.x < bbox[0] + bbox[2] / 2:
                        self._edit_store(store)
                    else:
                        self._validate_store(store)
                return "break"
        return None

    def _handle_tree_double_click(self, event: tk.Event) -> str | None:
        row_id = self.tree.identify_row(event.y)
        if not row_id:
            return None
        store = self.db.get_store(int(row_id))
        if store is None:
            return None
        self.selected_store = store
        self.tree.selection_set(row_id)
        columns = tuple(self.tree["displaycolumns"])
        column_id = self.tree.identify_column(event.x)
        try:
            key = columns[int(column_id.lstrip("#")) - 1]
        except (IndexError, ValueError):
            key = ""
        if key in {"app_id", self.action_field}:
            self._edit_store(store)
        else:
            self._open_store_backend()
        return "break"

    def _hide_secret(self, store_id: int) -> None:
        if store_id in self._shown_secrets and self.winfo_exists():
            self._shown_secrets.remove(store_id)
            self.refresh()

    def _change_page(self, delta: int) -> None:
        self._page = max(1, self._page + delta)
        self.refresh()

    def _change_page_size(self) -> None:
        try:
            self._page_size = int(self._page_size_var.get())
        except ValueError:
            self._page_size = 100
        self.db.set_setting("store_page_size", str(self._page_size))
        self._page = 1
        self.refresh()

    def _sync_action_buttons(self) -> None:
        checked = len(self._selected_stores())
        for widget, state in (
            (
                self._edit_button,
                "normal"
                if checked == 1 and self._has_permission("stores_edit")
                else "disabled",
            ),
            (
                self._copy_button,
                "normal"
                if checked > 0 and self._has_permission("stores_export")
                else "disabled",
            ),
            (
                self._export_button,
                "normal"
                if checked > 0 and self._has_permission("stores_export")
                else "disabled",
            ),
            (
                self._import_button,
                "normal" if self._has_permission("stores_export") else "disabled",
            ),
            (
                self._delete_button,
                "normal"
                if checked > 0 and self._has_permission("stores_delete")
                else "disabled",
            ),
        ):
            if widget is not None:
                widget.configure(state=state)
        if hasattr(self, "_select_all_button"):
            selected_suffix = f" 已选 {checked}" if checked else ""
            self.status_line_var.set(
                "勾选店铺后可批量操作"
                + selected_suffix
            )

    def _has_permission(self, permission: str) -> bool:
        checker = getattr(self.window, "_can_permission", None)
        if not callable(checker):
            return True
        if checker(permission):
            return True
        messagebox.showwarning(
            "没有功能权限",
            "当前子账号没有被分配此功能权限，请联系主账号。",
            parent=self,
        )
        return False

    def _store_metrics(self, store: Store) -> dict[str, Any]:
        summary = self.db.store_summary(store.id)
        try:
            cached = json.loads(
                self.db.get_setting(f"store_metrics_{store.id}", "{}")
            )
        except (TypeError, ValueError):
            cached = {}
        merged: dict[str, Any] = dict(summary)
        merged.update(cached if isinstance(cached, dict) else {})
        merged.setdefault("gmv_today", summary.get("today_revenue", 0))
        merged.setdefault("gmv_7d", summary.get("week_revenue", 0))
        merged.setdefault("gmv_30d", summary.get("month_revenue", 0))
        merged.setdefault("orders_today", summary.get("today_orders", 0))
        merged.setdefault("buyers_today", 0)
        merged.setdefault("buyer_30d", 0)
        merged.setdefault("visitors_30d", 0)
        merged.setdefault("product_click_today", 0)
        merged.setdefault(
            "product_total",
            self._as_metric_int(summary.get("in_sale"))
            + self._as_metric_int(summary.get("warehouse")),
        )
        return merged

    @staticmethod
    def _as_metric_int(value: Any) -> int:
        try:
            return int(float(value or 0))
        except (TypeError, ValueError):
            return 0

    def _sort_value(self, store: Store) -> Any:
        if self.sort_field in {"name", "group", "app_id"}:
            value = (
                store.group_name
                if self.sort_field == "group"
                else getattr(store, self.sort_field)
            )
            return str(value or "")
        metrics = self._metrics_by_id.get(store.id, {})
        value = metrics.get(self.sort_field)
        if self.sort_field == "quota":
            value = self._as_metric_int(metrics.get("quota_remaining"))
        elif self.sort_field == "product_total":
            value = self._as_metric_int(metrics.get("product_total"))
        elif self.sort_field in (
            self.percent_fields | self.score_fields | self.money_fields
        ):
            try:
                value = float(value or 0)
            except (TypeError, ValueError):
                value = 0.0
        else:
            value = self._as_metric_int(value)
        return value

    def _sort_by(self, field: str) -> None:
        if field in {"selected", "sequence", self.action_field}:
            return
        if self.sort_field == field:
            self.sort_desc = not self.sort_desc
        else:
            self.sort_field = field
            self.sort_desc = field not in {"name", "group", "app_id"}
        labels = {
            key: label for key, label, _width in self.field_definitions
        }
        labels.update(
            {"selected": "☐", "sequence": "序号", "action": "操作"}
        )
        for key in self._all_columns:
            text = labels[key]
            if key == self.sort_field:
                text += " ↓" if self.sort_desc else " ↑"
            self.tree.heading(key, text=text)
        self._page = 1
        self.refresh()

    @staticmethod
    def _display_login_status(store: Store) -> str:
        if store.web_status == "已登录":
            return "已登录"
        if store.web_status in {"已掉线", "需重新扫码"}:
            return "已掉线"
        if store.web_status in {"网络异常", "连接异常", "平台异常"}:
            return store.web_status
        return "未登录"

    @staticmethod
    def _format_count(value: Any) -> str:
        if value is None or value == "":
            return "—"
        try:
            number = float(value)
        except (TypeError, ValueError):
            return str(value)
        return (
            f"{int(number):,}"
            if number.is_integer()
            else f"{number:,.2f}"
        )

    @staticmethod
    def _format_score(value: Any) -> str:
        if value is None or value == "":
            return "—"
        try:
            return f"{float(value):.2f}"
        except (TypeError, ValueError):
            return str(value)

    @staticmethod
    def _format_percent(value: Any) -> str:
        if value is None or value == "":
            return "—"
        try:
            return f"{float(value):,.2f}%".replace(".00%", "%")
        except (TypeError, ValueError):
            return str(value)

    @staticmethod
    def _format_quota(metrics: dict[str, Any]) -> str:
        if metrics.get("quota_error"):
            return "获取失败"
        remaining = StorePage._as_metric_int(
            metrics.get("quota_remaining")
        )
        total = StorePage._as_metric_int(metrics.get("quota_total"))
        if not remaining and not total:
            return "—"
        return f"{remaining}/{total}"

    @staticmethod
    def _format_stats_time(value: Any) -> str:
        if not value:
            return "—"
        if isinstance(value, (int, float)):
            try:
                return datetime.fromtimestamp(float(value)).strftime(
                    "%m/%d %H:%M"
                )
            except (OverflowError, OSError, ValueError):
                return "—"
        text = str(value)
        try:
            return datetime.fromisoformat(text).strftime("%m/%d %H:%M")
        except ValueError:
            return text[:16]
