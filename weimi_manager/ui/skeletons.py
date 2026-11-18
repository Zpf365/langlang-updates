from __future__ import annotations

import json
import os
import shutil
import time as time_module
import tkinter as tk
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, time, timedelta
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import TYPE_CHECKING, Any

from .. import __version__
from ..exporter import export_xlsx
from .dialogs import CouponFormDialog, SimpleFieldsDialog
from .pages import BasePage
from .procurement_account_dialog import (
    ProcurementAccountDialog,
    ProcurementPasswordDialog,
)
from .theme import (
    BLUE,
    BORDER,
    GOLD,
    GREEN,
    MUTED,
    PANEL,
    SOFT_GOLD,
    SOFT_TEAL,
    TEXT,
    button,
)

if TYPE_CHECKING:
    from ..models import Store
    from .app import MainWindow


def _hosted_no_traffic_product_ids(
    products: list[dict[str, Any]],
    metrics_map: dict[str, dict[str, Any]],
    *,
    days: int,
    max_sales: int,
    now_timestamp: float | None = None,
    limit: int = 500,
) -> list[str]:
    """Select only listed products with known metrics and proven inactivity."""
    now = time_module.time() if now_timestamp is None else now_timestamp
    selected: list[str] = []
    for row in products:
        if int(row.get("status") or 0) != 5:
            continue
        product_id = str(row.get("product_id") or "")
        if not product_id:
            continue
        metrics = metrics_map.get(product_id)
        if not metrics:
            continue
        if (
            int(metrics.get("views30") or 0) > 0
            or int(metrics.get("click_cnt") or 0) > 0
        ):
            continue
        try:
            raw = json.loads(str(row.get("raw_json") or "{}"))
        except (TypeError, ValueError):
            raw = {}
        sold = int(
            raw.get("total_sold_num")
            or raw.get("sold_num")
            or 0
        )
        if sold > max_sales:
            continue
        listed_at = 0
        for key in (
            "_private_listing_time",
            "listing_time",
            "create_time",
            "update_time",
        ):
            try:
                listed_at = int(float(raw.get(key) or 0))
            except (TypeError, ValueError):
                listed_at = 0
            if listed_at > 0:
                break
        if listed_at > 10_000_000_000:
            listed_at //= 1000
        if listed_at <= 0 or now - listed_at < max(1, days) * 86400:
            continue
        selected.append(product_id)
        if len(selected) >= limit:
            break
    return selected


def _display_timestamp(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        timestamp = int(float(text))
    except (TypeError, ValueError):
        return text.replace("T", " ")[:19]
    if timestamp <= 0:
        return text
    if timestamp > 10_000_000_000:
        timestamp //= 1000
    try:
        return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")
    except (OSError, OverflowError, ValueError):
        return text


def _json_list_setting(raw: str) -> set[int]:
    try:
        values = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return set()
    if not isinstance(values, list):
        return set()
    result = set()
    for value in values:
        try:
            result.add(int(value))
        except (TypeError, ValueError):
            continue
    return result


def metric_card(
    parent: tk.Widget,
    label: str,
    value: str,
    note: str,
    tone: str = "teal",
) -> tk.Frame:
    background = SOFT_TEAL if tone == "teal" else SOFT_GOLD
    card = tk.Frame(
        parent,
        bg=PANEL,
        highlightbackground=BORDER,
        highlightthickness=1,
        padx=18,
        pady=15,
    )
    tk.Label(
        card,
        text=label,
        bg=PANEL,
        fg=MUTED,
        font=("Microsoft YaHei UI", 9),
    ).pack(anchor="w")
    tk.Label(
        card,
        text=value,
        bg=PANEL,
        fg=TEXT,
        font=("Microsoft YaHei UI", 23, "bold"),
    ).pack(anchor="w", pady=(3, 1))
    tk.Label(
        card,
        text=note,
        bg=background,
        fg=BLUE if tone == "teal" else "#8a6a2c",
        padx=8,
        pady=2,
        font=("Microsoft YaHei UI", 8),
    ).pack(anchor="w", pady=(6, 0))
    return card


def section_title(parent: tk.Widget, title: str, subtitle: str = "") -> None:
    header = ttk.Frame(parent, style="Panel.TFrame")
    header.pack(fill="x", pady=(0, 12))
    ttk.Label(header, text=title, style="Section.TLabel").pack(anchor="w")
    if subtitle:
        ttk.Label(
            header,
            text=subtitle,
            style="Panel.TLabel",
            foreground=MUTED,
        ).pack(anchor="w", pady=(3, 0))


class ScaffoldPage(BasePage):
    module_name = ""
    description = ""
    cards: tuple[tuple[str, str, str], ...] = ()
    columns: tuple[tuple[str, int], ...] = ()

    def __init__(self, parent: tk.Widget, window: "MainWindow"):
        super().__init__(parent, window)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        banner = tk.Frame(self, bg=SOFT_GOLD, padx=18, pady=12)
        banner.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        tk.Label(
            banner,
            text="框架预览",
            bg=GOLD,
            fg="#3e321c",
            padx=10,
            pady=2,
            font=("Microsoft YaHei UI", 8, "bold"),
        ).pack(side="left")
        tk.Label(
            banner,
            text=f"{self.module_name} 页面已进入产品框架，业务接口将在后续版本逐项接入。",
            bg=SOFT_GOLD,
            fg="#705b2e",
            padx=12,
            font=("Microsoft YaHei UI", 9),
        ).pack(side="left")

        cards_frame = ttk.Frame(self, style="TFrame")
        cards_frame.grid(row=1, column=0, sticky="ew", pady=(0, 12))
        for index, (label, value, note) in enumerate(self.cards):
            cards_frame.columnconfigure(index, weight=1, uniform="metric")
            card = metric_card(
                cards_frame,
                label,
                value,
                note,
                "gold" if index % 2 else "teal",
            )
            card.grid(row=0, column=index, sticky="nsew", padx=(0 if index == 0 else 6, 0))

        body = tk.Frame(self, bg=PANEL, highlightbackground=BORDER, highlightthickness=1)
        body.grid(row=2, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)
        body.rowconfigure(1, weight=1)
        toolbar = ttk.Frame(body, style="Panel.TFrame", padding=(18, 14))
        toolbar.grid(row=0, column=0, sticky="ew")
        section_title(
            toolbar,
            f"{self.module_name}工作区",
            self.description,
        )
        ttk.Button(toolbar, text="功能待接入", state="disabled").pack(side="right")

        table_frame = ttk.Frame(body, style="Panel.TFrame")
        table_frame.grid(row=1, column=0, sticky="nsew", padx=18, pady=(0, 18))
        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)
        tree = ttk.Treeview(
            table_frame,
            columns=[key for key, _ in self.columns],
            show="headings",
            height=9,
        )
        for key, width in self.columns:
            tree.heading(key, text=key)
            tree.column(key, width=width, anchor="w")
        tree.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(table_frame, orient="vertical", command=tree.yview)
        scrollbar.grid(
            row=0,
            column=1,
            sticky="ns",
        )
        tree.configure(yscrollcommand=scrollbar.set)
        tree.insert(
            "",
            "end",
            values=["暂无数据", *["—"] * (len(self.columns) - 1)],
        )
        placeholder = ttk.Label(
            table_frame,
            text="数据列和操作入口已预留，下一阶段接入真实业务",
            style="Panel.TLabel",
            foreground=MUTED,
            font=("Microsoft YaHei UI", 10),
        )
        placeholder.place(relx=0.5, rely=0.55, anchor="center")


class WorkbenchPage(BasePage):
    title = "工作台"
    subtitle = "澜琅电商运营工作台 · 今日状态与下一步工作"

    def __init__(self, parent: tk.Widget, window: "MainWindow"):
        super().__init__(parent, window)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)
        self.metrics = ttk.Frame(self, style="TFrame")
        self.metrics.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        for index in range(4):
            self.metrics.columnconfigure(index, weight=1, uniform="workbench")
        self.metric_values: list[tk.Label] = []
        for index, (label, note, tone) in enumerate(
            (
                ("店铺", "已接入工作区", "teal"),
                ("待发货", "等待物流处理", "gold"),
                ("商品详情", "本地可检索商品", "teal"),
                ("今日订单", "本地已有订单", "gold"),
            )
        ):
            card = metric_card(self.metrics, label, "—", note, tone)
            card.grid(row=0, column=index, sticky="nsew", padx=(0 if index == 0 else 6, 0))
            self.metric_values.append(card.winfo_children()[1])

        self.session_alert_var = tk.StringVar()
        self.session_alert = tk.Label(
            self,
            textvariable=self.session_alert_var,
            bg="#f7e8e4",
            fg="#a33f33",
            anchor="w",
            padx=14,
            pady=9,
            font=("Microsoft YaHei UI", 9, "bold"),
        )
        self.session_alert.grid(row=1, column=0, sticky="ew", pady=(0, 12))
        self.session_alert.grid_remove()

        columns = ttk.Frame(self, style="TFrame")
        columns.grid(row=2, column=0, sticky="nsew")
        columns.columnconfigure(0, weight=3)
        columns.columnconfigure(1, weight=2)
        columns.rowconfigure(0, weight=1)

        left = tk.Frame(columns, bg=PANEL, highlightbackground=BORDER, highlightthickness=1)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        left_header = tk.Frame(left, bg=PANEL, padx=20, pady=18)
        left_header.pack(fill="x")
        section_title(
            left_header,
            "今日工作流",
            "从数据同步到发货的推荐处理顺序",
        )
        workflow = tk.Frame(left, bg=PANEL, padx=20, pady=4)
        workflow.pack(fill="both", expand=True)
        self.workflow_labels: dict[str, tk.Label] = {}
        for index, (key, label, detail) in enumerate(
            (
                ("stores", "01  检查店铺连接", "确认凭据、接口权限和面单账号"),
                ("orders", "02  同步订单", "拉取最近订单并解密待发货收货信息"),
                ("products", "03  补齐商品", "分批同步目录和商品详情"),
                ("operations", "04  检查售后", "同步售后单并处理经营待办"),
            )
        ):
            row = tk.Frame(workflow, bg=PANEL, pady=9)
            row.pack(fill="x")
            text_box = tk.Frame(row, bg=PANEL)
            text_box.pack(side="left", fill="x", expand=True)
            tk.Label(
                text_box,
                text=label,
                bg=PANEL,
                fg=TEXT,
                font=("Microsoft YaHei UI", 10, "bold"),
            ).pack(anchor="w")
            tk.Label(
                text_box,
                text=detail,
                bg=PANEL,
                fg=MUTED,
                font=("Microsoft YaHei UI", 8),
            ).pack(anchor="w", pady=(2, 0))
            status = tk.Label(
                row,
                text="查看",
                bg=SOFT_TEAL,
                fg=BLUE,
                padx=8,
                pady=2,
                cursor="hand2",
                font=("Microsoft YaHei UI", 8, "bold"),
            )
            status.pack(side="right", padx=(10, 2))
            status.bind("<Button-1>", lambda _event, page=key: self.window.show_page(page))
            if index < 3:
                tk.Frame(workflow, bg=BORDER, height=1).pack(fill="x")

        right = tk.Frame(columns, bg=PANEL, highlightbackground=BORDER, highlightthickness=1)
        right.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        right_header = tk.Frame(right, bg=PANEL, padx=20, pady=18)
        right_header.pack(fill="x")
        section_title(
            right_header,
            "建设进度",
            "当前版本的能力边界",
        )
        progress_box = tk.Frame(right, bg=PANEL, padx=20, pady=4)
        progress_box.pack(fill="both", expand=True)
        self.progress_items: dict[str, ttk.Progressbar] = {}
        for label, value in (
            ("框架与导航", 100),
            ("店铺与订单", 82),
            ("商品运营", 62),
            ("售后与数据", 62),
        ):
            tk.Label(
                progress_box,
                text=label,
                bg=PANEL,
                fg=TEXT,
                font=("Microsoft YaHei UI", 9),
            ).pack(anchor="w", pady=(7, 3))
            bar = ttk.Progressbar(progress_box, maximum=100, value=value)
            bar.pack(fill="x")
            self.progress_items[label] = bar
        tk.Label(
            progress_box,
            text="所有未接入功能均使用明确的“框架预览”状态，不展示虚假业务结果。",
            bg=SOFT_GOLD,
            fg="#705b2e",
            padx=12,
            pady=10,
            wraplength=330,
            justify="left",
            font=("Microsoft YaHei UI", 8),
        ).pack(fill="x", pady=(18, 0))

    def refresh(self) -> None:
        stores = self.db.list_stores()
        counts = self.db.data_counts()
        values = (
            counts["stores"],
            counts["pending_shipping"],
            counts["catalog_done"],
            counts["today_orders"],
        )
        for label, value in zip(self.metric_values, values):
            label.configure(text=f"{value:,}")
        offline = [
            store.name
            for store in stores
            if store.web_status in {"已掉线", "需重新扫码"}
        ]
        attention = [
            store.name
            for store in stores
            if store.web_status in {"网络异常", "连接异常", "平台异常"}
        ]
        if offline:
            self.session_alert_var.set(
                f"登录提醒：{'、'.join(offline[:4])} 已掉线，请到店铺管理重新扫码。"
            )
            self.session_alert.configure(bg="#f7e8e4", fg="#a33f33")
            self.session_alert.grid()
        elif attention:
            self.session_alert_var.set(
                f"连接提醒：{'、'.join(attention[:4])} 当前连接异常，正在自动重试。"
            )
            self.session_alert.configure(bg=SOFT_GOLD, fg="#7a5d27")
            self.session_alert.grid()
        else:
            self.session_alert.grid_remove()


class OperationsPage(BasePage):
    title = "运营中心"
    subtitle = "售后单与经营异常的统一入口"

    def __init__(self, parent: tk.Widget, window: "MainWindow"):
        super().__init__(parent, window)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(5, weight=1)
        self._store_lookup: dict[str, object] = {}
        self._rows: dict[str, dict] = {}
        self._proof_paths: list[str] = []
        self.store_var = tk.StringVar()
        self._rows: dict[str, dict] = {}
        self.period_var = tk.StringVar(value="今日")
        self.progress_var = tk.DoubleVar(value=0)

        filters = ttk.Frame(self, style="TFrame")
        filters.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        ttk.Label(filters, text="店铺").pack(side="left")
        self.store_combo = ttk.Combobox(
            filters,
            textvariable=self.store_var,
            state="readonly",
            width=28,
        )
        self.store_combo.pack(side="left", padx=(6, 12))
        self.store_combo.bind("<<ComboboxSelected>>", lambda _event: self._load())
        ttk.Label(filters, text="日期").pack(side="left")
        ttk.Combobox(
            filters,
            textvariable=self.period_var,
            values=["今日", "昨日"],
            state="readonly",
            width=10,
        ).pack(side="left", padx=(6, 12))
        button(filters, "同步售后", self._sync, "Primary").pack(side="left")
        button(filters, "刷新", self._load).pack(side="left", padx=8)
        ttk.Label(
            filters,
            text="售后查询接口按 24 小时区间同步",
            style="Subtitle.TLabel",
        ).pack(side="left", padx=12)

        metrics = ttk.Frame(self, style="TFrame")
        metrics.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        for index in range(3):
            metrics.columnconfigure(index, weight=1, uniform="after")
        self.total_card = metric_card(metrics, "售后单", "0", "本地已同步", "teal")
        self.total_card.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        self.processing_card = metric_card(metrics, "处理中", "0", "需要商家处理", "gold")
        self.processing_card.grid(row=0, column=1, sticky="nsew", padx=6)
        self.refund_card = metric_card(metrics, "退款金额", "¥0.00", "当前结果汇总", "teal")
        self.refund_card.grid(row=0, column=2, sticky="nsew", padx=(6, 0))

        actions = ttk.Frame(self, style="TFrame")
        actions.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        self._action_buttons = []
        for index, (label, command) in enumerate(
            (
                ("同意售后", self._accept),
                ("拒绝售后", self._reject),
                ("商家协商", self._negotiate),
                ("补寄发货", self._reship),
                ("更新补寄物流", self._update_reship),
                ("极速换货", self._quick_exchange),
                ("拒绝换货发货", self._reject_exchange),
                ("售后详情", self._show_detail),
                ("选择售后凭证", self._choose_proofs),
            )
        ):
            action_button = button(actions, label, command)
            action_button.pack(
                side="left",
                padx=(8 if index and index % 3 == 0 else 0, 0),
            )
            self._action_buttons.append(action_button)

        self.progress = ttk.Progressbar(self, variable=self.progress_var, maximum=100)
        self.progress.grid(row=3, column=0, sticky="ew", pady=(0, 8))

        body = tk.Frame(self, bg=PANEL, highlightbackground=BORDER, highlightthickness=1)
        body.grid(row=4, column=0, sticky="nsew")
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=1)
        columns = (
            "after_sale_id",
            "order_id",
            "type",
            "status",
            "reason",
            "refund",
            "updated",
        )
        self.tree = ttk.Treeview(body, columns=columns, show="headings")
        for key, text, width in (
            ("after_sale_id", "售后单号", 180),
            ("order_id", "订单号", 180),
            ("type", "类型", 100),
            ("status", "状态", 150),
            ("reason", "原因", 220),
            ("refund", "退款金额", 110),
            ("updated", "更新时间", 160),
        ):
            self.tree.heading(key, text=text)
            self.tree.column(key, width=width, anchor="w")
        self.tree.grid(row=0, column=0, sticky="nsew")
        self.tree.bind("<Double-1>", lambda _event: self._show_detail())
        self.tree.bind("<<TreeviewSelect>>", self._update_action_state)
        ttk.Scrollbar(body, orient="vertical", command=self.tree.yview).grid(
            row=0,
            column=1,
            sticky="ns",
        )
    def refresh(self) -> None:
        stores = self.db.list_stores()
        self._store_lookup = {
            f"{store.name} · #{store.id}": store
            for store in stores
        }
        values = list(self._store_lookup)
        self.store_combo["values"] = values
        if self.store_var.get() not in values:
            self.store_var.set(values[0] if values else "")
        self._load()

    def _selected_store(self):
        return self._store_lookup.get(self.store_var.get())

    def _load(self) -> None:
        store = self._selected_store()
        if store is None:
            return
        rows = self.db.list_after_sales(store.id)
        self._rows.clear()
        self.tree.delete(*self.tree.get_children())
        type_labels = {
            "REFUND": "仅退款",
            "RETURN": "退货退款",
            "EXCHANGE": "换货",
            "RESHIP": "补寄",
        }
        for index, row in enumerate(rows):
            iid = str(index)
            self._rows[iid] = row
            self.tree.insert(
                "",
                "end",
                iid=iid,
                values=(
                    row["after_sale_order_id"],
                    row["order_id"],
                    type_labels.get(row["after_sale_type"], row["after_sale_type"]),
                    row["status_text"],
                    row["reason_text"],
                    f"¥{row['refund_amount'] / 100:.2f}",
                    _display_timestamp(row["update_time"]),
                ),
            )
        self._update_action_state()
        self.total_card.winfo_children()[1].configure(text=f"{len(rows):,}")
        processing = sum("完成" not in row["status_text"] for row in rows)
        self.processing_card.winfo_children()[1].configure(text=f"{processing:,}")
        refund = sum(row["refund_amount"] for row in rows)
        self.refund_card.winfo_children()[1].configure(text=f"¥{refund / 100:,.2f}")

    def _update_action_state(self, _event=None) -> None:
        state = "normal" if self.tree.selection() else "disabled"
        for action_button in self._action_buttons:
            action_button.configure(state=state)

    def _sync(self) -> None:
        store = self._selected_store()
        if store is None:
            return
        from datetime import datetime, time, timedelta

        target = date.today()
        if self.period_var.get() == "昨日":
            target = target - timedelta(days=1)
        start = int(datetime.combine(target, time.min).timestamp())
        end = start + 86399
        self.window.set_status("正在同步售后单…", busy=True)

        def done(result) -> None:
            self._proof_paths = []
            self.window.set_status(result.message)
            self._load()

        self.window.run_task(
            lambda progress: self.window.service.sync_after_sales(
                store,
                start,
                end,
                progress,
            ),
            done,
            lambda error: messagebox.showerror(
                "售后同步失败",
                error,
                parent=self,
            ),
            self._progress,
            log_title="同步售后工单",
        )

    def _progress(self, message: str, current: int, total: int) -> None:
        self.window.set_status(message, busy=True)
        self.progress_var.set((current / total * 100) if total else 0)

    def _selected_after_sale(self) -> dict | None:
        selection = self.tree.selection()
        if not selection:
            messagebox.showinfo("请选择售后单", "请先选择一条售后单。", parent=self)
            return None
        return self._rows.get(selection[0])

    def _run_action(
        self,
        title: str,
        action: str,
        payload: dict,
        after_sale_id: str,
    ) -> None:
        store = self._selected_store()
        if store is None:
            return
        if not messagebox.askyesno(
            title,
            f"即将向微信小店提交“{title}”操作。是否继续？",
            parent=self,
        ):
            return
        self.window.set_status(f"正在{title}…", busy=True)

        def done(result) -> None:
            self.window.set_status(result.message)
            self._load()

        def work(_progress):
            prepared = dict(payload)
            local_media = prepared.pop("_local_media_paths", [])
            if local_media:
                uploaded = [
                    value
                    for value in local_media
                    if value.startswith(("http://", "https://"))
                ]
                files = [
                    value
                    for value in local_media
                    if not value.startswith(("http://", "https://"))
                ]
                if files:
                    self.window.set_status("正在上传售后凭证…", busy=True)
                    uploaded.extend(
                        self.window.service.upload_after_sale_media(store, files)
                    )
                prepared["media_files"] = uploaded
            return self.window.service.aftersale_action(
                store,
                after_sale_id,
                action,
                prepared,
            )

        self.window.run_task(
            work,
            done,
            lambda error: messagebox.showerror(f"{title}失败", error, parent=self),
            log_title=f"售后操作：{title}",
        )

    @staticmethod
    def _parse_media_paths(value: str) -> list[str]:
        return [
            item.strip()
            for item in value.replace("；", ",").replace("\n", ",").split(",")
            if item.strip()
        ]

    def _choose_proofs(self) -> None:
        paths = filedialog.askopenfilenames(
            parent=self,
            title="选择售后凭证图片",
            filetypes=[
                ("图片", "*.jpg *.jpeg *.png *.webp *.bmp"),
                ("所有文件", "*.*"),
            ],
        )
        if paths:
            self._proof_paths = list(paths)
            self.window.set_status(f"已选择 {len(paths)} 张售后凭证")

    def _accept(self) -> None:
        row = self._selected_after_sale()
        if row:
            self._run_action(
                "同意售后",
                "accept",
                {},
                row["after_sale_order_id"],
            )

    def _reject(self) -> None:
        row = self._selected_after_sale()
        if not row:
            return

        def save(values: dict) -> None:
            try:
                reason_type = int(values["reject_reason_type"])
            except ValueError:
                messagebox.showwarning("原因类型", "拒绝原因类型必须填写数字。", parent=self)
                return
            self._run_action(
                "拒绝售后",
                "reject",
                {
                    "reject_reason_type": reason_type,
                    "reject_reason": values.get("reject_reason", ""),
                    "_local_media_paths": self._parse_media_paths(
                        values.get("proof_paths", "")
                    )
                    + self._proof_paths,
                },
                row["after_sale_order_id"],
            )

        SimpleFieldsDialog(
            self,
            "拒绝售后",
            (
                ("reject_reason_type", "原因类型编号"),
                ("reject_reason", "拒绝说明"),
                ("proof_paths", "凭证图片路径（逗号分隔）"),
            ),
            save,
        )

    def _negotiate(self) -> None:
        row = self._selected_after_sale()
        if not row:
            return

        def save(values: dict) -> None:
            try:
                payload = {
                    "type": int(values["type"]),
                    "amount": int(values.get("amount") or 0),
                    "merchant_update_desc": values["merchant_update_desc"],
                    "update_reason_type": int(values["update_reason_type"]),
                    "merchant_update_type": int(values["merchant_update_type"]),
                    "_local_media_paths": self._parse_media_paths(
                        values.get("proof_paths", "")
                    )
                    + self._proof_paths,
                }
            except (KeyError, ValueError):
                messagebox.showwarning("参数错误", "协商参数必须按规定填写。", parent=self)
                return
            self._run_action(
                "商家协商",
                "negotiate",
                payload,
                row["after_sale_order_id"],
            )

        SimpleFieldsDialog(
            self,
            "商家协商",
            (
                ("merchant_update_type", "协商类型 1/2/3"),
                ("update_reason_type", "协商原因类型编号"),
                ("type", "修改后售后类型 1/2/3/4"),
                ("amount", "金额（分）"),
                ("merchant_update_desc", "协商说明"),
                ("proof_paths", "凭证图片路径（逗号分隔）"),
            ),
            save,
        )

    def _reship(self) -> None:
        row = self._selected_after_sale()
        if row:
            self._shipping_action(row, "补寄发货", "reship")

    def _update_reship(self) -> None:
        row = self._selected_after_sale()
        if row:
            self._shipping_action(row, "更新补寄物流", "update_reship")

    def _accept_exchange(self) -> None:
        row = self._selected_after_sale()
        if row:
            self._shipping_action(row, "换货发货", "accept_exchange")

    def _quick_exchange(self) -> None:
        row = self._selected_after_sale()
        if row:
            self._shipping_action(row, "极速换货", "accept_exchange")

    def _shipping_action(self, row: dict, title: str, action: str) -> None:
        def save(values: dict) -> None:
            if not values.get("delivery_id") or not values.get("waybill_id"):
                messagebox.showwarning("信息不完整", "请填写快递编码和运单号。", parent=self)
                return
            self._run_action(
                title,
                action,
                values,
                row["after_sale_order_id"],
            )

        SimpleFieldsDialog(
            self,
            title,
            (("delivery_id", "快递公司编码"), ("waybill_id", "运单号")),
            save,
        )

    def _reject_exchange(self) -> None:
        row = self._selected_after_sale()
        if row:
            self._run_action(
                "拒绝换货发货",
                "reject_exchange",
                {},
                row["after_sale_order_id"],
            )

    def _show_detail(self) -> None:
        row = self._selected_after_sale()
        if row is None:
            return
        dialog = tk.Toplevel(self)
        dialog.title(f"售后详情 · {row['after_sale_order_id']}")
        dialog.transient(self.winfo_toplevel())
        dialog.geometry("900x640")
        summary = tk.Frame(dialog, bg=PANEL, padx=16, pady=12)
        summary.pack(fill="x")
        tk.Label(
            summary,
            text=(
                f"售后单：{row['after_sale_order_id']}    "
                f"订单：{row['order_id']}    状态：{row['status_text']}\n"
                f"类型：{row['after_sale_type']}    退款：¥{row['refund_amount'] / 100:.2f}\n"
                f"原因：{row['reason_text']}"
            ),
            bg=PANEL,
            fg=TEXT,
            justify="left",
            anchor="w",
        ).pack(fill="x")
        tk.Label(
            summary,
            text=(
                "纠纷、平台保障单、凭证、极速换货等扩展字段均来自微信官方售后详情；"
                "原始字段如下。"
            ),
            bg=SOFT_GOLD,
            fg="#705b2e",
            justify="left",
            anchor="w",
            padx=8,
            pady=6,
        ).pack(fill="x", pady=(8, 0))
        text_frame = tk.Frame(dialog)
        text_frame.pack(fill="both", expand=True, padx=16, pady=12)
        text = tk.Text(text_frame, wrap="word")
        scroll = ttk.Scrollbar(text_frame, orient="vertical", command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        text.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        try:
            raw = json.dumps(
                json.loads(row.get("raw_json") or "{}"),
                ensure_ascii=False,
                indent=2,
            )
        except json.JSONDecodeError:
            raw = str(row.get("raw_json") or "")
        text.insert("1.0", raw)
        text.configure(state="disabled")


class ProcurementPage(BasePage):
    title = "采购中心"
    subtitle = "淘宝、1688采购账号、订单、物流、取件码和本地备注"

    def __init__(self, parent: tk.Widget, window: "MainWindow"):
        super().__init__(parent, window)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(4, weight=1)
        self._accounts: dict[str, dict] = {}
        self._account_tree_rows: dict[str, dict] = {}
        self._stores: dict[str, object] = {}
        self._rows: dict[str, dict] = {}
        self.account_platform_var = tk.StringVar(value="taobao")
        self.account_var = tk.StringVar(value="全部采购账号")
        self.store_var = tk.StringVar()
        self.search_var = tk.StringVar()
        self.interval_var = tk.StringVar(value="4 小时")
        self.state_var = tk.StringVar(value="采购数据尚未同步")
        self._auto_timer = False
        self._auto_after_id = None

        toolbar = ttk.Frame(self, style="TFrame")
        toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        for platform, label in (
            ("taobao", "淘宝"),
            ("1688", "1688"),
            ("pinduoduo", "拼多多"),
            ("jd", "京东"),
        ):
            ttk.Radiobutton(
                toolbar,
                text=label,
                value=platform,
                variable=self.account_platform_var,
                style="Toolbutton",
                command=self._switch_account_platform,
            ).pack(side="left", padx=(0, 4))
        ttk.Separator(toolbar, orient="vertical").pack(
            side="left",
            fill="y",
            padx=8,
        )
        ttk.Label(toolbar, text="采购账号").pack(side="left")
        self.account_combo = ttk.Combobox(
            toolbar,
            textvariable=self.account_var,
            state="readonly",
            width=24,
        )
        self.account_combo.pack(side="left", padx=(5, 8))
        self.account_combo.bind(
            "<<ComboboxSelected>>",
            self._update_procurement_actions,
        )
        self.add_account_button = button(
            toolbar,
            "添加淘宝账号",
            self._add_account,
        )
        self.add_account_button.pack(side="left")
        self.procurement_account_buttons = [
            button(toolbar, "登录", self._login_account),
            button(toolbar, "打开采购网页", self._browse_account),
            button(toolbar, "绑定当前店铺", self._bind_account_store),
            button(toolbar, "同步账号", self._sync_account),
        ]
        for index, action_button in enumerate(self.procurement_account_buttons):
            action_button.pack(side="left", padx=(6 if index in {0, 2} else 0, 0))
        button(toolbar, "同步全部", self._sync_all).pack(side="left", padx=6)
        ttk.Label(toolbar, text="自动同步").pack(side="left", padx=(12, 4))
        self.interval_combo = ttk.Combobox(
            toolbar,
            textvariable=self.interval_var,
            state="readonly",
            width=9,
            values=("关闭", "1 小时", "4 小时", "12 小时", "24 小时"),
        )
        self.interval_combo.pack(side="left")
        self.interval_combo.bind(
            "<<ComboboxSelected>>",
            lambda _event: self._schedule_auto_sync(),
        )

        filters = ttk.Frame(self, style="TFrame")
        filters.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        ttk.Label(filters, text="关联店铺").pack(side="left")
        self.store_combo = ttk.Combobox(
            filters,
            textvariable=self.store_var,
            state="readonly",
            width=22,
        )
        self.store_combo.pack(side="left", padx=(5, 10))
        ttk.Entry(filters, textvariable=self.search_var, width=24).pack(
            side="left"
        )
        button(filters, "查询", self._load).pack(side="left", padx=6)
        button(filters, "自动关联", self._auto_match).pack(side="left")
        button(filters, "手工绑定", self._manual_link).pack(side="left", padx=6)
        button(filters, "编辑本地备注", self._edit_local_note).pack(side="left")
        button(filters, "同步微信备注", self._sync_wechat_note).pack(
            side="left",
            padx=6,
        )
        ttk.Label(
            filters,
            textvariable=self.state_var,
            style="Subtitle.TLabel",
        ).pack(side="right")

        actions = ttk.Frame(self, style="TFrame")
        actions.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        self.delete_account_button = button(
            actions,
            "删除账号",
            self._delete_account,
            "Danger",
        )
        self.delete_account_button.pack(side="left")
        self.procurement_row_buttons = [
            button(actions, "编辑本地采购", self._edit_procurement_local),
            button(actions, "查看采购JSON", self._show_raw),
        ]
        for action_button in self.procurement_row_buttons:
            action_button.pack(side="left", padx=6)
        self.clear_session_button = button(
            actions,
            "清空登录态",
            self._clear_session,
        )
        self.clear_session_button.pack(side="left")
        ttk.Label(
            actions,
            text="采购备注默认只保存在本机；勾选同步后才写入微信订单备注。",
            style="Subtitle.TLabel",
        ).pack(side="left", padx=12)

        account_panel = ttk.Frame(self, style="TFrame")
        account_panel.grid(row=3, column=0, sticky="ew", pady=(0, 8))
        account_panel.columnconfigure(0, weight=1)
        account_columns = (
            "platform",
            "account",
            "nickname",
            "group",
            "status",
            "auto_login",
            "remark",
            "alipay",
            "operation",
        )
        self.account_tree = ttk.Treeview(
            account_panel,
            columns=account_columns,
            show="headings",
            height=7,
            selectmode="browse",
        )
        for key, text, width in (
            ("platform", "平台", 70),
            ("account", "账号", 150),
            ("nickname", "昵称", 150),
            ("group", "分组", 110),
            ("status", "在线状态", 90),
            ("auto_login", "自动登录", 80),
            ("remark", "备注", 170),
            ("alipay", "支付宝", 120),
            ("operation", "操作", 360),
        ):
            self.account_tree.heading(key, text=text)
            self.account_tree.column(key, width=width, anchor="w")
        self.account_tree.grid(row=0, column=0, sticky="nsew")
        self.account_tree.bind(
            "<<TreeviewSelect>>",
            self._on_account_tree_selected,
        )
        self.account_tree.bind(
            "<Double-1>",
            lambda _event: self._login_account(),
        )
        account_actions = ttk.Frame(account_panel)
        account_actions.grid(row=1, column=0, sticky="ew", pady=(5, 0))
        self.account_operation_buttons = [
            button(account_actions, "重新授权", self._login_account),
            button(account_actions, "设置密码", self._set_account_password),
            button(account_actions, "编辑备注", self._edit_account_remark),
            button(account_actions, "自动登录", self._toggle_account_auto_login),
            button(account_actions, "签约/支付宝绑定", self._bind_account_alipay),
            button(account_actions, "清除缓存", self._clear_account_cache),
            button(account_actions, "刷新状态", self._refresh_account_status),
            button(account_actions, "绑定当前店铺", self._bind_account_store),
            button(account_actions, "同步账号", self._sync_account),
        ]
        for index, action_button in enumerate(self.account_operation_buttons):
            action_button.pack(side="left", padx=(0 if index == 0 else 5, 0))

        body = tk.Frame(self, bg=PANEL, highlightbackground=BORDER, highlightthickness=1)
        body.grid(row=4, column=0, sticky="nsew")
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=1)
        columns = (
            "platform", "account", "order_id", "goods", "amount", "mail",
            "pickup", "station", "status", "linked",
        )
        self.tree = ttk.Treeview(body, columns=columns, show="headings")
        for key, text, width in (
            ("platform", "平台", 75),
            ("account", "采购账号", 130),
            ("order_id", "平台订单号", 160),
            ("goods", "商品", 280),
            ("amount", "采购金额", 90),
            ("mail", "物流单号", 145),
            ("pickup", "取件码", 90),
            ("station", "驿站", 120),
            ("status", "状态", 95),
            ("linked", "微信订单", 180),
        ):
            self.tree.heading(key, text=text)
            self.tree.column(key, width=width, anchor="w")
        self.tree.grid(row=0, column=0, sticky="nsew")
        ttk.Scrollbar(body, orient="vertical", command=self.tree.yview).grid(
            row=0,
            column=1,
            sticky="ns",
        )
        self.tree.bind("<Double-1>", lambda _event: self._edit_local_note())
        self.tree.bind(
            "<<TreeviewSelect>>",
            self._update_procurement_actions,
        )

    def refresh(self) -> None:
        platform = self.account_platform_var.get()
        accounts = self.db.list_procurement_accounts(platform)
        self._accounts = {
            f"{account['platform']} · {account['label']} · #{account['id']}": account
            for account in accounts
        }
        account_values = ["全部采购账号", *self._accounts]
        self.account_combo["values"] = account_values
        if self.account_var.get() not in account_values:
            self.account_var.set("全部采购账号")
        self.add_account_button.configure(
            text=f"添加{self._platform_label(platform)}账号"
        )
        self.account_tree.delete(*self.account_tree.get_children())
        self._account_tree_rows.clear()
        for account in accounts:
            iid = str(account["id"])
            self._account_tree_rows[iid] = account
            self.account_tree.insert(
                "",
                "end",
                iid=iid,
                values=(
                    self._platform_label(account["platform"]),
                    account.get("login_id") or account.get("label") or "",
                    account.get("nickname") or account.get("label") or "",
                    account.get("group_name") or "未分组",
                    account.get("status") or "未登录",
                    "开" if int(account.get("auto_login") or 0) else "关",
                    account.get("remark") or "",
                    (
                        f"已绑定 {account.get('alipay_uid') or ''}".strip()
                        if account.get("alipay_uid")
                        else "未绑定"
                    ),
                    "重新授权  设置密码  编辑备注  清除缓存  刷新状态",
                ),
            )
        stores = self.db.list_stores()
        self._stores = {
            f"{store.name} · #{store.id}": store for store in stores
        }
        store_values = list(self._stores)
        self.store_combo["values"] = store_values
        if self.store_var.get() not in store_values:
            self.store_var.set(store_values[0] if store_values else "")
        self._load()
        self._schedule_auto_sync()

    def _switch_account_platform(self) -> None:
        self.account_var.set("全部采购账号")
        self.refresh()

    @staticmethod
    def _platform_label(platform: str) -> str:
        return {
            "taobao": "淘宝",
            "1688": "1688",
            "pinduoduo": "拼多多",
            "jd": "京东",
        }.get(str(platform), str(platform))

    def _on_account_tree_selected(self, _event=None) -> None:
        selection = self.account_tree.selection()
        account = self._account_tree_rows.get(selection[0]) if selection else None
        if account is not None:
            display = (
                f"{account['platform']} · {account['label']} · #{account['id']}"
            )
            if display in self._accounts:
                self.account_var.set(display)
        self._update_procurement_actions()
        self._load()

    def _update_procurement_actions(self, _event=None) -> None:
        has_account = self._selected_account() is not None
        account_state = "normal" if has_account else "disabled"
        for action_button in self.procurement_account_buttons:
            action_button.configure(state=account_state)
        for action_button in getattr(self, "account_operation_buttons", []):
            action_button.configure(state=account_state)
        self.delete_account_button.configure(state=account_state)
        self.clear_session_button.configure(state=account_state)
        row_state = "normal" if self._selected_row() is not None else "disabled"
        for action_button in self.procurement_row_buttons:
            action_button.configure(state=row_state)

    def _selected_account(self) -> dict | None:
        selection = self.account_tree.selection()
        if selection:
            selected = self._account_tree_rows.get(selection[0])
            if selected is not None:
                return selected
        return self._accounts.get(self.account_var.get())

    def _selected_store(self):
        return self._stores.get(self.store_var.get())

    def _selected_row(self) -> dict | None:
        selection = self.tree.selection()
        return self._rows.get(selection[0]) if selection else None

    def _add_account(self) -> None:
        platform = self.account_platform_var.get()

        def save(values: dict[str, str]) -> None:
            account_name = str(values["account"]).strip()
            account_id = self.db.add_procurement_account(
                platform,
                account_name,
                account_name,
                nickname=account_name,
                remark=str(values.get("remark") or ""),
                auto_login=True,
            )
            profile = (
                self.window.paths.data_dir
                / "procurement_profiles"
                / f"account_{account_id}"
            )
            self.db.update_procurement_account(
                account_id,
                profile_dir=str(profile),
            )
            self.db.set_procurement_account_secrets(
                account_id,
                password=str(values["password"]),
            )
            store = self._selected_store()
            if store is not None:
                self.db.link_store_procurement_account(store.id, account_id)
            self.refresh()
            iid = str(account_id)
            if self.account_tree.exists(iid):
                self.account_tree.selection_set(iid)
                self.account_tree.focus(iid)
                self._on_account_tree_selected()

        ProcurementAccountDialog(
            self,
            platform_label=self._platform_label(platform),
            on_save=save,
        )

    def _login_account(self) -> None:
        account = self._selected_account()
        if account is None:
            messagebox.showinfo("请选择账号", "请选择具体采购账号。", parent=self)
            return
        self.window.set_status(f"正在打开{account['platform']}登录…", busy=True)

        def done(_payload) -> None:
            self.window.set_status("采购账号登录信息已保存")
            self.refresh()

        self.window.run_task(
            lambda _progress: self.window.service.procurement_login(
                int(account["id"])
            ),
            done,
            lambda error: messagebox.showerror("采购登录失败", error, parent=self),
            log_title=f"登录采购账号：{account['platform']}",
        )

    def _set_account_password(self) -> None:
        account = self._selected_account()
        if account is None:
            messagebox.showinfo("请选择账号", "请选择具体采购账号。", parent=self)
            return

        def save(password: str) -> None:
            self.db.set_procurement_account_secrets(
                int(account["id"]),
                password=password,
            )
            self.window.set_status("采购账号密码已使用 Windows DPAPI 加密保存")
            self.refresh()

        ProcurementPasswordDialog(
            self,
            title="设置账号密码",
            field_label="新密码：",
            on_save=save,
        )

    def _edit_account_remark(self) -> None:
        account = self._selected_account()
        if account is None:
            messagebox.showinfo("请选择账号", "请选择具体采购账号。", parent=self)
            return

        def save(values: dict[str, str]) -> None:
            self.db.update_procurement_account(
                int(account["id"]),
                remark=str(values.get("remark") or "").strip(),
            )
            self.refresh()

        SimpleFieldsDialog(
            self,
            "编辑采购账号备注",
            (("remark", "备注"),),
            save,
            {"remark": str(account.get("remark") or "")},
        )

    def _toggle_account_auto_login(self) -> None:
        account = self._selected_account()
        if account is None:
            messagebox.showinfo("请选择账号", "请选择具体采购账号。", parent=self)
            return
        enabled = not bool(int(account.get("auto_login") or 0))
        self.db.update_procurement_account(
            int(account["id"]),
            auto_login=1 if enabled else 0,
        )
        self.refresh()
        self.window.set_status(f"自动登录已{'开启' if enabled else '关闭'}")

    def _bind_account_alipay(self) -> None:
        account = self._selected_account()
        if account is None:
            messagebox.showinfo("请选择账号", "请选择具体采购账号。", parent=self)
            return
        self.window.set_status("正在打开支付宝官方授权页面…", busy=True)

        def done(result: dict) -> None:
            self.window.set_status("支付宝授权信息已加密保存")
            self.refresh()
            if isinstance(result, dict) and result.get("uid"):
                messagebox.showinfo(
                    "支付宝绑定",
                    "支付宝支付身份已绑定到当前采购账号。",
                    parent=self,
                )

        self.window.run_task(
            lambda _progress: self.window.service.procurement_bind_alipay(
                int(account["id"])
            ),
            done,
            lambda error: messagebox.showerror(
                "支付宝绑定失败",
                error,
                parent=self,
            ),
            log_title="采购账号：绑定支付宝",
        )

    def _clear_account_cache(self) -> None:
        account = self._selected_account()
        if account is None:
            messagebox.showinfo("请选择账号", "请选择具体采购账号。", parent=self)
            return
        if not messagebox.askyesno(
            "清除缓存",
            "将删除当前采购账号的本机浏览器配置和登录会话，"
            "账号记录与采购订单不会删除。是否继续？",
            parent=self,
        ):
            return
        profile = Path(
            str(account.get("profile_dir") or "")
            or (
                self.window.paths.data_dir
                / "procurement_profiles"
                / f"account_{account['id']}"
            )
        )
        profiles_root = (
            self.window.paths.data_dir / "procurement_profiles"
        ).resolve()
        try:
            resolved = profile.resolve()
            resolved.relative_to(profiles_root)
            if resolved.is_dir():
                shutil.rmtree(resolved)
        except (OSError, ValueError):
            messagebox.showerror("清除缓存失败", "账号缓存目录无效。", parent=self)
            return
        with self.db.connect() as conn:
            conn.execute(
                "DELETE FROM procurement_sessions WHERE account_id=?",
                (int(account["id"]),),
            )
            conn.execute(
                "DELETE FROM procurement_alipay_sessions WHERE account_id=?",
                (int(account["id"]),),
            )
        self.db.update_procurement_account(
            int(account["id"]),
            status="未登录",
            last_error="登录缓存已清理",
        )
        self.refresh()

    def _refresh_account_status(self) -> None:
        account = self._selected_account()
        if account is None:
            messagebox.showinfo("请选择账号", "请选择具体采购账号。", parent=self)
            return
        self.window.set_status("正在检查采购账号登录状态…", busy=True)

        def done(status: str) -> None:
            self.window.set_status(f"采购账号状态：{status}")
            self.refresh()

        self.window.run_task(
            lambda _progress: self.window.service.procurement_check_status(
                int(account["id"])
            ),
            done,
            lambda error: messagebox.showerror(
                "刷新状态失败",
                error,
                parent=self,
            ),
            log_title="采购账号：刷新登录状态",
        )

    def _sync_account(self) -> None:
        account = self._selected_account()
        if account is None:
            messagebox.showinfo("请选择账号", "请选择具体采购账号。", parent=self)
            return
        self._sync_accounts([account])

    def _browse_account(self) -> None:
        account = self._selected_account()
        if account is None:
            messagebox.showinfo("请选择账号", "请选择具体采购账号。", parent=self)
            return
        self.window.set_status(
            f"正在打开{account['platform']}采购网页，关闭网页后自动保存…",
            busy=True,
        )
        self.window.run_task(
            lambda _progress: self.window.service.procurement_browse(
                int(account["id"])
            ),
            lambda result: (self.refresh(), self.window.set_status(result.message)),
            lambda error: messagebox.showerror(
                "打开采购网页失败",
                error,
                parent=self,
            ),
            log_title=f"打开采购网页并同步：{account['platform']}",
        )

    def _bind_account_store(self) -> None:
        account = self._selected_account()
        store = self._selected_store()
        if account is None or store is None:
            messagebox.showinfo("请选择", "请选择采购账号和微信店铺。", parent=self)
            return
        self.db.link_store_procurement_account(store.id, int(account["id"]))
        messagebox.showinfo(
            "绑定完成",
            f"已将“{account['label']}”绑定到“{store.name}”。",
            parent=self,
        )
        self._load()

    def _sync_all(self) -> None:
        self._sync_accounts(self.db.list_procurement_accounts())

    def _sync_accounts(self, accounts: list[dict]) -> None:
        if not accounts:
            messagebox.showinfo("没有账号", "请先新增采购账号。", parent=self)
            return
        self.window.set_status(f"正在同步 {len(accounts)} 个采购账号…", busy=True)

        def work(progress) -> list[str]:
            errors = []
            for index, account in enumerate(accounts, 1):
                progress(
                    f"正在同步 {account['label']}",
                    index - 1,
                    len(accounts),
                )
                try:
                    self.window.service.sync_procurement_account(
                        int(account["id"]),
                        lambda message, current, total: progress(
                            f"{account['label']}：{message}",
                            current,
                            total,
                        ),
                    )
                except Exception as exc:
                    errors.append(f"{account['label']}: {exc}")
            return errors

        def done(errors: list[str]) -> None:
            self.refresh()
            self.window.set_status(
                f"采购同步完成，成功 {len(accounts) - len(errors)} 个账号"
            )
            if errors:
                messagebox.showwarning(
                    "部分采购账号同步失败",
                    "\n".join(errors[:8]),
                    parent=self,
                )

        self.window.run_task(
            work,
            done,
            lambda error: messagebox.showerror("采购同步失败", error, parent=self),
            log_title="同步采购账号和订单",
        )

    def _load(self) -> None:
        account = self._selected_account()
        platform = None
        account_id = None
        if account:
            platform = str(account["platform"])
            account_id = int(account["id"])
        rows = self.db.list_procurement_orders(
            account_id=account_id,
            platform=platform,
            search=self.search_var.get(),
        )
        store = self._selected_store()
        links = self.db.list_procurement_links(store.id if store else None)
        link_map: dict[int, list[str]] = {}
        for link in links:
            link_map.setdefault(int(link["procurement_order_id"]), []).append(
                str(link["order_id"])
            )
        self.tree.delete(*self.tree.get_children())
        self._rows.clear()
        for index, row in enumerate(rows):
            iid = str(index)
            self._rows[iid] = row
            self.tree.insert(
                "",
                "end",
                iid=iid,
                values=(
                    "淘宝" if row["platform"] == "taobao" else "1688",
                    row.get("account_label") or "",
                    row.get("external_order_id") or "",
                    row.get("goods_title") or "",
                    f"¥{int(row.get('pay_amount') or 0) / 100:,.2f}",
                    row.get("mail_no") or "",
                    row.get("pickup_code") or "",
                    row.get("station_name") or "",
                    row.get("status_text") or row.get("status") or "",
                    "、".join(link_map.get(int(row["id"]), [])),
                ),
            )
        self.state_var.set(
            f"{len(self._accounts)} 个采购账号 · {len(rows)} 条采购记录"
        )
        self._update_procurement_actions()

    def _auto_match(self) -> None:
        store = self._selected_store()
        row = self._selected_row()
        if store is None:
            messagebox.showinfo("请选择店铺", "自动关联前请选择微信店铺。", parent=self)
            return
        if row is None:
            self.window.set_status("正在自动匹配采购订单…", busy=True)
            self.window.run_task(
                lambda _progress: self.window.service.match_procurement_orders(store),
                lambda count: (
                    self._load(),
                    self.window.set_status(f"自动匹配完成：{count} 条"),
                ),
                lambda error: messagebox.showerror("自动关联失败", error, parent=self),
                log_title="自动关联采购单与微信订单",
            )
            return
        mail_no = str(row.get("mail_no") or "")
        orders = self.db.list_orders(store_id=store.id, search=mail_no) if mail_no else []
        exact = [
            order for order in orders
            if mail_no and str(order.get("waybill_id") or "") == mail_no
        ]
        if len(exact) != 1:
            messagebox.showinfo(
                "无法唯一匹配",
                "当前采购记录没有唯一匹配的物流单号，请使用手工绑定。",
                parent=self,
            )
            return
        self.db.upsert_procurement_link(
            store.id,
            str(exact[0]["order_id"]),
            int(row["id"]),
            "mail_no",
            100,
        )
        self._load()

    def _manual_link(self) -> None:
        store = self._selected_store()
        row = self._selected_row()
        if store is None or row is None:
            messagebox.showinfo("请选择记录", "请选择采购记录并指定微信店铺。", parent=self)
            return

        def save(values: dict[str, str]) -> None:
            order_id = values.get("order_id", "").strip()
            if not order_id or self.db.get_order(store.id, order_id) is None:
                messagebox.showwarning("订单不存在", "本店没有该微信订单号。", parent=self)
                return
            self.db.upsert_procurement_link(
                store.id,
                order_id,
                int(row["id"]),
                "manual",
                100,
                values.get("note", ""),
            )
            self._load()

        SimpleFieldsDialog(
            self,
            "手工绑定微信订单",
            (
                ("order_id", "微信订单号"),
                ("note", "本地备注"),
            ),
            save,
        )

    def _selected_link(self) -> dict | None:
        row = self._selected_row()
        store = self._selected_store()
        if row is None or store is None:
            return None
        links = self.db.list_procurement_links(store.id)
        return next(
            (
                link for link in links
                if int(link["procurement_order_id"]) == int(row["id"])
            ),
            None,
        )

    def _edit_local_note(self) -> None:
        link = self._selected_link()
        if link is None:
            messagebox.showinfo("未关联", "请先绑定到微信订单。", parent=self)
            return

        def save(values: dict[str, str]) -> None:
            self.db.upsert_procurement_link(
                int(link["store_id"]),
                str(link["order_id"]),
                int(link["procurement_order_id"]),
                str(link.get("match_type") or "manual"),
                int(link.get("confidence") or 100),
                values.get("note", ""),
                bool(link.get("sync_wechat_note")),
            )
            self._load()

        SimpleFieldsDialog(
            self,
            "编辑采购本地备注",
            (("note", "本地备注"),),
            save,
            initial={"note": str(link.get("local_note") or "")},
        )

    def _sync_wechat_note(self) -> None:
        store = self._selected_store()
        link = self._selected_link()
        if store is None or link is None:
            messagebox.showinfo("未关联", "请先选择已关联的采购记录。", parent=self)
            return
        if not messagebox.askyesno(
            "同步微信备注",
            "采购信息将写入微信订单备注。是否继续？",
            parent=self,
        ):
            return
        self.window.set_status("正在同步微信订单备注…", busy=True)
        self.window.run_task(
            lambda _progress: self.window.service.sync_procurement_note_to_wechat(
                store,
                str(link["order_id"]),
            ),
            lambda _result: self._load(),
            lambda error: messagebox.showerror("同步微信备注失败", error, parent=self),
            log_title="同步采购备注到微信订单",
        )

    def _show_raw(self) -> None:
        row = self._selected_row()
        if row is None:
            return
        dialog = tk.Toplevel(self)
        dialog.title("采购原始JSON")
        dialog.geometry("900x650")
        text = tk.Text(dialog, wrap="none")
        text.pack(fill="both", expand=True)
        try:
            content = json.dumps(
                json.loads(row.get("raw_json") or "{}"),
                ensure_ascii=False,
                indent=2,
            )
        except json.JSONDecodeError:
            content = str(row.get("raw_json") or "")
        text.insert("1.0", content)

    def _edit_procurement_local(self) -> None:
        row = self._selected_row()
        if row is None:
            messagebox.showinfo("请选择记录", "请先选择采购记录。", parent=self)
            return

        def save(values: dict[str, str]) -> None:
            try:
                amount = int(round(float(values.get("pay_amount") or 0) * 100))
                count = int(values.get("goods_count") or 0)
            except ValueError:
                messagebox.showwarning("数值错误", "金额和数量格式不正确。", parent=self)
                return
            self.db.update_procurement_order_local(
                int(row["id"]),
                goods_title=values.get("goods_title", "").strip(),
                pay_amount=amount,
                goods_count=count,
                mail_no=values.get("mail_no", "").strip(),
                pickup_code=values.get("pickup_code", "").strip(),
            )
            self._load()
            messagebox.showinfo(
                "本地采购已更新",
                "修改已保存在本机。若需提交平台，请点击“打开采购网页”在官方页面确认。",
                parent=self,
            )

        SimpleFieldsDialog(
            self,
            "编辑本地采购记录",
            (
                ("goods_title", "商品名称"),
                ("pay_amount", "采购金额（元）"),
                ("goods_count", "数量"),
                ("mail_no", "物流单号"),
                ("pickup_code", "取件码"),
            ),
            save,
            initial={
                "goods_title": str(row.get("goods_title") or ""),
                "pay_amount": f"{int(row.get('pay_amount') or 0) / 100:.2f}",
                "goods_count": str(row.get("goods_count") or 0),
                "mail_no": str(row.get("mail_no") or ""),
                "pickup_code": str(row.get("pickup_code") or ""),
            },
        )

    def _clear_session(self) -> None:
        self._clear_account_cache()

    def _delete_account(self) -> None:
        account = self._selected_account()
        if account is None:
            return
        if not messagebox.askyesno(
            "删除采购账号",
            f"确定删除采购账号“{account['label']}”及其本地采购缓存吗？",
            parent=self,
        ):
            return
        profile = Path(
            str(account.get("profile_dir") or "")
            or (
                self.window.paths.data_dir
                / "procurement_profiles"
                / f"account_{account['id']}"
            )
        )
        profiles_root = (
            self.window.paths.data_dir / "procurement_profiles"
        ).resolve()
        try:
            resolved = profile.resolve()
            resolved.relative_to(profiles_root)
            if resolved.is_dir():
                shutil.rmtree(resolved)
        except (OSError, ValueError):
            pass
        self.db.delete_procurement_account(int(account["id"]))
        self.refresh()

    def _schedule_auto_sync(self) -> None:
        intervals = {
            "关闭": 0,
            "1 小时": 60 * 60 * 1000,
            "4 小时": 4 * 60 * 60 * 1000,
            "12 小时": 12 * 60 * 60 * 1000,
            "24 小时": 24 * 60 * 60 * 1000,
        }
        delay = intervals.get(self.interval_var.get(), 0)
        if self._auto_after_id is not None:
            try:
                self.after_cancel(self._auto_after_id)
            except tk.TclError:
                pass
            self._auto_after_id = None
        self._auto_timer = bool(delay)
        if not delay or self._auto_timer:
            pass

        def tick() -> None:
            if not self.winfo_exists():
                return
            self._sync_all()
            self._auto_after_id = self.after(delay, tick)

        if delay:
            self._auto_after_id = self.after(delay, tick)


class CustomerServicePage(BasePage):
    title = "客服工作台"
    subtitle = "在澜琅内打开微信小店官方客服页面，保持平台原生实时会话"

    def __init__(self, parent: tk.Widget, window: "MainWindow"):
        super().__init__(parent, window)
        self.columnconfigure(0, weight=1)
        self._store_lookup: dict[str, object] = {}
        self.store_var = tk.StringVar()
        panel = tk.Frame(self, bg=PANEL, highlightbackground=BORDER, highlightthickness=1)
        panel.grid(row=0, column=0, sticky="nsew")
        panel.columnconfigure(0, weight=1)
        ttk.Label(panel, text="官方实时客服", style="Section.TLabel").grid(
            row=0, column=0, sticky="w", padx=24, pady=(24, 8)
        )
        ttk.Label(
            panel,
            text=(
                "当前采用微信小店官方客服工作台。消息由微信页面自身的实时连接接收，"
                "不需要公网回调，也不会切换店铺的客服模式。"
            ),
            style="Panel.TLabel",
            foreground=MUTED,
            wraplength=900,
            justify="left",
        ).grid(row=1, column=0, sticky="w", padx=24)
        selector = ttk.Frame(panel, style="Panel.TFrame")
        selector.grid(row=2, column=0, sticky="w", padx=24, pady=18)
        ttk.Label(selector, text="店铺", style="Panel.TLabel").pack(side="left")
        self.store_combo = ttk.Combobox(
            selector,
            textvariable=self.store_var,
            state="readonly",
            width=30,
        )
        self.store_combo.pack(side="left", padx=10)
        button(
            selector,
            "打开官方客服工作台",
            self._open_console,
            "Primary",
        ).pack(side="left")
        ttk.Label(
            panel,
            text="如果登录过期，官方页面会提示重新扫码。关闭客服窗口后，登录状态会自动更新。",
            style="Panel.TLabel",
            foreground=MUTED,
        ).grid(row=3, column=0, sticky="w", padx=24)
        note = tk.Frame(panel, bg=SOFT_GOLD, padx=16, pady=12)
        note.grid(row=4, column=0, sticky="ew", padx=24, pady=24)
        tk.Label(
            note,
            text=(
                "当前边界：这里嵌入的是微信官方客服页面，不读取或保存聊天记录，"
                "未经允许不会自动发送消息。若未来改用自研客服 API，必须部署公网回调服务。"
            ),
            bg=SOFT_GOLD,
            fg="#705b2e",
            justify="left",
            wraplength=900,
            font=("Microsoft YaHei UI", 9),
        ).pack(anchor="w")

    def refresh(self) -> None:
        stores = self.db.list_stores()
        self._store_lookup = {
            f"{store.name} · #{store.id}": store
            for store in stores
        }
        values = list(self._store_lookup)
        self.store_combo["values"] = values
        if self.store_var.get() not in values:
            self.store_var.set(values[0] if values else "")

    def _open_console(self) -> None:
        store = self._store_lookup.get(self.store_var.get())
        if store is None:
            messagebox.showinfo("请选择店铺", "请先选择具体店铺。", parent=self)
            return
        self.window.set_status("正在打开微信小店官方客服工作台…", busy=True)

        def done(_result) -> None:
            self.window.store_data_changed()
            refreshed = self.db.get_store(store.id)
            self.window.set_status(
                f"官方客服工作台已关闭 · 登录状态："
                f"{refreshed.web_status if refreshed else '未知'}"
            )

        self.window.run_task(
            lambda _progress: (
                self.window.web_session.open_official_customer_console(store)
            ),
            done,
            lambda error: messagebox.showerror(
                "客服工作台打开失败",
                error,
                parent=self,
            ),
            log_title="打开官方客服工作台",
        )


class MarketingPage(BasePage):
    title = "营销中心"
    subtitle = "微信小店优惠券同步与状态查看"

    def __init__(self, parent: tk.Widget, window: "MainWindow"):
        super().__init__(parent, window)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)
        self._store_lookup: dict[str, object] = {}
        self.store_var = tk.StringVar()
        self._rows: dict[str, dict] = {}
        toolbar = ttk.Frame(self, style="TFrame")
        toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        ttk.Label(toolbar, text="店铺").pack(side="left")
        self.store_combo = ttk.Combobox(
            toolbar,
            textvariable=self.store_var,
            state="readonly",
            width=28,
        )
        self.store_combo.pack(side="left", padx=(6, 12))
        self.store_combo.bind("<<ComboboxSelected>>", lambda _event: self._load())
        button(toolbar, "同步优惠券", self._sync, "Primary").pack(side="left")
        button(toolbar, "新建优惠券", self._create_coupon).pack(side="left", padx=8)
        button(toolbar, "修改优惠券", self._edit_coupon).pack(side="left")
        button(toolbar, "变更状态", self._change_coupon_status).pack(side="left", padx=8)
        button(toolbar, "刷新", self._load).pack(side="left", padx=8)
        ttk.Label(
            toolbar,
            text="数据来自微信小店官方优惠券接口",
            style="Subtitle.TLabel",
        ).pack(side="left", padx=12)

        summary = ttk.Frame(self, style="TFrame")
        summary.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        for index in range(3):
            summary.columnconfigure(index, weight=1, uniform="coupon")
        self.total_card = metric_card(summary, "优惠券", "0", "本地已同步", "teal")
        self.total_card.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        self.active_card = metric_card(summary, "已生效", "0", "可正常领取", "gold")
        self.active_card.grid(row=0, column=1, sticky="nsew", padx=6)
        self.used_card = metric_card(summary, "已核销", "0", "累计使用", "teal")
        self.used_card.grid(row=0, column=2, sticky="nsew", padx=(6, 0))

        body = tk.Frame(self, bg=PANEL, highlightbackground=BORDER, highlightthickness=1)
        body.grid(row=2, column=0, sticky="nsew")
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=1)
        columns = ("name", "type", "status", "discount", "total", "received", "used")
        self.tree = ttk.Treeview(body, columns=columns, show="headings")
        for key, text, width in (
            ("name", "优惠券名称", 260),
            ("type", "类型", 150),
            ("status", "状态", 100),
            ("discount", "优惠", 150),
            ("total", "发行量", 100),
            ("received", "已领取", 100),
            ("used", "已核销", 100),
        ):
            self.tree.heading(key, text=text)
            self.tree.column(key, width=width, anchor="w")
        self.tree.grid(row=0, column=0, sticky="nsew")
        ttk.Scrollbar(body, orient="vertical", command=self.tree.yview).grid(
            row=0, column=1, sticky="ns"
        )

    def refresh(self) -> None:
        stores = self.db.list_stores()
        self._store_lookup = {
            f"{store.name} · #{store.id}": store
            for store in stores
        }
        values = list(self._store_lookup)
        self.store_combo["values"] = values
        if self.store_var.get() not in values:
            self.store_var.set(values[0] if values else "")
        self._load()

    def _selected_store(self):
        return self._store_lookup.get(self.store_var.get())

    def _load(self) -> None:
        store = self._selected_store()
        if store is None:
            return
        rows = self.db.list_coupons(store.id)
        type_names = {
            1: "商品条件折扣",
            2: "商品满减",
            3: "商品统一折扣",
            4: "商品直减",
            101: "店铺条件折扣",
            102: "店铺满减",
            103: "店铺统一折扣",
            104: "店铺直减",
        }
        status_names = {1: "编辑中", 2: "已生效", 3: "已过期", 4: "已作废", 5: "已删除"}
        self.tree.delete(*self.tree.get_children())
        self._rows.clear()
        for index, row in enumerate(rows):
            iid = str(index)
            self._rows[iid] = row
            if row["discount_fee"]:
                discount = f"减 ¥{row['discount_fee'] / 100:.2f}"
            elif row["discount_num"]:
                discount = f"{row['discount_num'] / 1000:.1f} 折"
            else:
                discount = "—"
            self.tree.insert(
                "",
                "end",
                iid=iid,
                values=(
                    row["name"],
                    type_names.get(row["coupon_type"], str(row["coupon_type"])),
                    status_names.get(row["status"], str(row["status"])),
                    discount,
                    row["total_num"],
                    row["receive_num"],
                    row["used_num"],
                ),
            )
        self.total_card.winfo_children()[1].configure(text=f"{len(rows):,}")
        self.active_card.winfo_children()[1].configure(
            text=f"{sum(row['status'] == 2 for row in rows):,}"
        )
        self.used_card.winfo_children()[1].configure(
            text=f"{sum(row['used_num'] for row in rows):,}"
        )

    def _sync(self) -> None:
        store = self._selected_store()
        if store is None:
            return
        self.window.set_status("正在同步优惠券…", busy=True)

        def done(result) -> None:
            self.window.set_status(result.message)
            self._load()
            if result.errors:
                messagebox.showwarning(
                    "部分优惠券同步失败",
                    "\n".join(result.errors[:8]),
                    parent=self,
                )

        self.window.run_task(
            lambda progress: self.window.service.sync_coupons(store, progress),
            done,
            lambda error: messagebox.showerror("优惠券同步失败", error, parent=self),
            log_title="同步优惠券",
        )

    def _selected_coupon(self) -> dict | None:
        selected = self.tree.selection()
        return self._rows.get(selected[0]) if selected else None

    def _create_coupon(self) -> None:
        store = self._selected_store()
        if store is None:
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
        CouponFormDialog(
            self,
            "新建优惠券",
            template,
            "add",
            lambda payload: self._submit_coupon(store, "create", payload),
        )

    def _edit_coupon(self) -> None:
        store = self._selected_store()
        row = self._selected_coupon()
        if store is None or row is None:
            messagebox.showinfo("请选择优惠券", "请先选择一张优惠券。", parent=self)
            return
        payload = json.loads(row["raw_json"])
        flattened = dict(payload.get("coupon_info") or {})
        flattened.update(
            {
                "coupon_id": payload.get("coupon_id"),
                "type": payload.get("type"),
            }
        )
        CouponFormDialog(
            self,
            "修改优惠券",
            flattened,
            "update",
            lambda value: self._submit_coupon(store, "update", value),
        )

    def _change_coupon_status(self) -> None:
        store = self._selected_store()
        row = self._selected_coupon()
        if store is None or row is None:
            messagebox.showinfo("请选择优惠券", "请先选择一张优惠券。", parent=self)
            return

        def save(values: dict) -> None:
            try:
                status = int(values["status"])
            except ValueError:
                messagebox.showwarning("状态错误", "状态必须是 2、4 或 5。", parent=self)
                return
            if not messagebox.askyesno(
                "确认变更优惠券状态",
                "2=生效，4=作废，5=删除。删除后可能无法恢复，是否继续？",
                parent=self,
            ):
                return
            self.window.run_task(
                lambda _progress: self.window.service.coupon_write(
                    store,
                    "status",
                    {"coupon_id": row["coupon_id"], "status": status},
                ),
                lambda _result: self._load(),
                lambda error: messagebox.showerror("优惠券状态变更失败", error, parent=self),
                log_title="变更优惠券状态",
            )

        SimpleFieldsDialog(
            self,
            "优惠券状态",
            (("status", "目标状态 2/4/5"),),
            save,
        )

    def _submit_coupon(self, store, action: str, payload: dict) -> None:
        if not messagebox.askyesno(
            "确认提交优惠券",
            "该操作会直接提交到微信小店，是否继续？",
            parent=self,
        ):
            return
        self.window.run_task(
            lambda _progress: self.window.service.coupon_write(store, action, payload),
            lambda _result: self._load(),
            lambda error: messagebox.showerror("优惠券提交失败", error, parent=self),
            log_title=(
                "新建优惠券"
                if action == "create"
                else "修改优惠券"
            ),
        )


class AnalyticsPage(BasePage):
    title = "数据中心"
    subtitle = "微信小店罗盘经营概览与本地历史记录"

    def __init__(self, parent: tk.Widget, window: "MainWindow"):
        super().__init__(parent, window)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(3, weight=1)
        self._store_lookup: dict[str, object] = {}
        self.store_var = tk.StringVar()
        self.date_var = tk.StringVar(value=date.today().isoformat())
        self.progress_var = tk.DoubleVar(value=0)

        toolbar = ttk.Frame(self, style="TFrame")
        toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        ttk.Label(toolbar, text="店铺").pack(side="left")
        self.store_combo = ttk.Combobox(
            toolbar,
            textvariable=self.store_var,
            state="readonly",
            width=28,
        )
        self.store_combo.pack(side="left", padx=(6, 12))
        self.store_combo.bind("<<ComboboxSelected>>", lambda _event: self._load())
        ttk.Label(toolbar, text="日期").pack(side="left")
        ttk.Entry(toolbar, textvariable=self.date_var, width=13).pack(
            side="left",
            padx=(6, 12),
        )
        button(toolbar, "同步当日", self._sync, "Primary").pack(side="left")
        button(toolbar, "刷新", self._load).pack(side="left", padx=8)
        ttk.Label(
            toolbar,
            text="数据来自微信小店罗盘官方接口",
            style="Subtitle.TLabel",
        ).pack(side="left", padx=12)

        metrics = ttk.Frame(self, style="TFrame")
        metrics.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        for index in range(4):
            metrics.columnconfigure(index, weight=1, uniform="analytics")
        self.cards = []
        for index, (label, note, tone) in enumerate(
            (
                ("成交金额", "pay_gmv", "teal"),
                ("成交人数", "pay_uv", "gold"),
                ("成交订单", "pay_order_cnt", "teal"),
                ("退款金额", "pay_refund_gmv", "gold"),
            )
        ):
            card = metric_card(metrics, label, "—", note, tone)
            card.grid(
                row=0,
                column=index,
                sticky="nsew",
                padx=(0 if index == 0 else 6, 0),
            )
            self.cards.append(card.winfo_children()[1])

        self.progress = ttk.Progressbar(self, variable=self.progress_var, maximum=100)
        self.progress.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        body = tk.Frame(self, bg=PANEL, highlightbackground=BORDER, highlightthickness=1)
        body.grid(row=3, column=0, sticky="nsew")
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=1)
        columns = (
            "date",
            "gmv",
            "uv",
            "orders",
            "refund",
            "click_uv",
            "live",
            "feed",
        )
        self.tree = ttk.Treeview(body, columns=columns, show="headings")
        for key, text, width in (
            ("date", "日期", 120),
            ("gmv", "成交金额", 130),
            ("uv", "成交人数", 110),
            ("orders", "订单数", 100),
            ("refund", "退款金额", 130),
            ("click_uv", "商品点击", 120),
            ("live", "直播成交", 130),
            ("feed", "短视频成交", 140),
        ):
            self.tree.heading(key, text=text)
            self.tree.column(key, width=width, anchor="w")
        self.tree.grid(row=0, column=0, sticky="nsew")
        ttk.Scrollbar(body, orient="vertical", command=self.tree.yview).grid(
            row=0,
            column=1,
            sticky="ns",
        )

    def refresh(self) -> None:
        stores = self.db.list_stores()
        self._store_lookup = {
            f"{store.name} · #{store.id}": store
            for store in stores
        }
        values = list(self._store_lookup)
        self.store_combo["values"] = values
        if self.store_var.get() not in values:
            self.store_var.set(values[0] if values else "")
        self._load()

    def _selected_store(self):
        return self._store_lookup.get(self.store_var.get())

    def _load(self) -> None:
        store = self._selected_store()
        if store is None:
            return
        rows = self.db.list_analytics_days(store.id)
        self.tree.delete(*self.tree.get_children())
        for row in rows:
            self.tree.insert(
                "",
                "end",
                values=(
                    row["business_date"],
                    f"¥{row['pay_gmv'] / 100:,.2f}",
                    row["pay_uv"],
                    row["pay_order_cnt"],
                    f"¥{row['pay_refund_gmv'] / 100:,.2f}",
                    row["product_click_uv"],
                    f"¥{row['live_pay_gmv'] / 100:,.2f}",
                    f"¥{row['feed_pay_gmv'] / 100:,.2f}",
                ),
            )
        selected_day = self.db.get_analytics_day(store.id, self.date_var.get())
        if selected_day is None and rows:
            selected_day = rows[0]
        values = (
            f"¥{(selected_day['pay_gmv'] if selected_day else 0) / 100:,.2f}",
            f"{selected_day['pay_uv'] if selected_day else 0:,}",
            f"{selected_day['pay_order_cnt'] if selected_day else 0:,}",
            f"¥{(selected_day['pay_refund_gmv'] if selected_day else 0) / 100:,.2f}",
        )
        for label, value in zip(self.cards, values):
            label.configure(text=value)

    def _sync(self) -> None:
        store = self._selected_store()
        if store is None:
            return
        business_date = self.date_var.get().strip()
        try:
            date.fromisoformat(business_date)
        except ValueError:
            messagebox.showwarning("日期格式", "请使用 YYYY-MM-DD 格式。", parent=self)
            return
        self.window.set_status("正在同步经营数据…", busy=True)

        def done(result) -> None:
            self.window.set_status(result.message)
            self._load()

        self.window.run_task(
            lambda _progress: self.window.service.sync_analytics_day(
                store,
                business_date,
            ),
            done,
            lambda error: messagebox.showerror("经营数据同步失败", error, parent=self),
            log_title="同步店铺经营数据",
        )


class StorefrontPage(BasePage):
    title = "店铺主页"
    subtitle = "微信小店官方主页商品排序、置顶和隐藏"

    def __init__(self, parent: tk.Widget, window: "MainWindow"):
        super().__init__(parent, window)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)
        self._stores = {}
        self._rows = {}
        self._loaded_store_id: int | None = None
        self.store_var = tk.StringVar()
        bar = ttk.Frame(self, style="TFrame")
        bar.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        ttk.Label(bar, text="店铺").pack(side="left")
        self.store_combo = ttk.Combobox(bar, textvariable=self.store_var, state="readonly", width=28)
        self.store_combo.pack(side="left", padx=(6, 12))
        self.store_combo.bind("<<ComboboxSelected>>", self._store_changed)
        button(bar, "刷新主页商品", self._load, "Primary").pack(side="left")
        self.storefront_buttons = [
            button(bar, "置顶", lambda: self._action("top", 1)),
            button(bar, "取消置顶", lambda: self._action("top", 0)),
            button(bar, "隐藏", lambda: self._action("hide", 1)),
            button(bar, "取消隐藏", lambda: self._action("hide", 0)),
        ]
        for index, action_button in enumerate(self.storefront_buttons):
            action_button.pack(side="left", padx=(8 if index in {0, 2} else 0, 0))
        ttk.Label(bar, text="调整顺序").pack(side="left", padx=(16, 4))
        self.index_var = tk.StringVar(value="1")
        ttk.Entry(bar, textvariable=self.index_var, width=7).pack(side="left")
        self.reorder_button = button(bar, "应用", lambda: self._action("reorder", 0))
        self.reorder_button.pack(side="left", padx=6)
        self.storefront_buttons.append(self.reorder_button)
        body = ttk.Frame(self, style="Panel.TFrame")
        body.grid(row=2, column=0, sticky="nsew")
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=1)
        self.tree = ttk.Treeview(body, columns=("index", "id", "title", "status"), show="headings")
        for key, text, width in (
            ("index", "主页顺序", 90),
            ("id", "商品 ID", 200),
            ("title", "商品标题", 520),
            ("status", "状态", 120),
        ):
            self.tree.heading(key, text=text)
            self.tree.column(key, width=width, anchor="w")
        self.tree.grid(row=0, column=0, sticky="nsew")
        self.tree.bind("<<TreeviewSelect>>", self._update_action_state)
        ttk.Scrollbar(body, orient="vertical", command=self.tree.yview).grid(row=0, column=1, sticky="ns")
        self._update_action_state()

    def refresh(self) -> None:
        stores = self.db.list_stores()
        self._stores = {
            f"全部 {len(stores)} 家店铺": None,
            **{
                f"{store.name} · #{store.id}": store
                for store in stores
            },
        }
        values = list(self._stores)
        self.store_combo["values"] = values
        if self.store_var.get() not in values:
            self.store_var.set(values[0] if values else "")
            self._store_changed()

    def _store_changed(self, _event=None) -> None:
        self._loaded_store_id = None
        self.tree.delete(*self.tree.get_children())
        self._rows.clear()
        self._update_action_state()
        if self.store_var.get():
            self.window.set_status("已选择店铺，点击“刷新主页商品”读取")
        else:
            self.window.set_status("请先添加店铺")

    def _update_action_state(self, _event=None) -> None:
        state = "normal" if self.tree.selection() else "disabled"
        for action_button in self.storefront_buttons:
            action_button.configure(state=state)

    def _selected(self) -> dict | None:
        selection = self.tree.selection()
        return self._rows.get(selection[0]) if selection else None

    def _load(self) -> None:
        store = self._stores.get(self.store_var.get())
        if store is None:
            return
        self.window.set_status("正在读取店铺主页商品…", busy=True)
        self.window.run_task(
            lambda _progress: self.window.service.sync_storefront(store),
            self._render,
            lambda error: messagebox.showerror("主页商品读取失败", error, parent=self),
            log_title="读取店铺主页商品",
        )

    def _render(self, items: list[dict]) -> None:
        store = self._stores.get(self.store_var.get())
        self._loaded_store_id = store.id if store is not None else None
        self.tree.delete(*self.tree.get_children())
        self._rows.clear()
        for index, item in enumerate(items, 1):
            iid = str(index - 1)
            self._rows[iid] = item
            self.tree.insert(
                "",
                "end",
                iid=iid,
                values=(index, item["product_id"], item["title"], item["status_text"]),
            )
        self.window.set_status(f"主页商品 {len(items)} 个")
        self._update_action_state()

    def _action(self, action: str, value: int) -> None:
        store = self._stores.get(self.store_var.get())
        item = self._selected()
        if store is None or item is None:
            messagebox.showinfo("请选择商品", "请先选择主页商品。", parent=self)
            return
        if action == "reorder":
            try:
                value = int(self.index_var.get())
            except ValueError:
                messagebox.showwarning("序号错误", "主页序号必须是数字。", parent=self)
                return
        if not messagebox.askyesno("确认主页操作", "该操作会直接影响店铺主页展示，是否继续？", parent=self):
            return
        self.window.run_task(
            lambda _progress: self.window.service.storefront_action(
                store, action, item["product_id"], value
            ),
            lambda _result: self._load(),
            lambda error: messagebox.showerror("主页操作失败", error, parent=self),
            log_title=f"店铺主页操作：{action}",
        )


class FinancePage(BasePage):
    title = "资金账户"
    subtitle = "微信小店余额、结算和资金流水"

    def __init__(self, parent: tk.Widget, window: "MainWindow"):
        super().__init__(parent, window)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(3, weight=1)
        self._stores = {}
        self._flows: list[dict] = []
        self._loaded_store_id: int | None = None
        self.store_var = tk.StringVar()
        self.start_var = tk.StringVar(value=(date.today() - timedelta(days=29)).isoformat())
        self.end_var = tk.StringVar(value=date.today().isoformat())
        self.type_var = tk.StringVar(value="全部类型")
        bar = ttk.Frame(self, style="TFrame")
        bar.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        ttk.Label(bar, text="店铺").pack(side="left")
        self.store_combo = ttk.Combobox(bar, textvariable=self.store_var, state="readonly", width=28)
        self.store_combo.pack(side="left", padx=(6, 12))
        self.store_combo.bind("<<ComboboxSelected>>", self._store_changed)
        ttk.Label(bar, text="日期").pack(side="left")
        ttk.Entry(bar, textvariable=self.start_var, width=11).pack(side="left", padx=(5, 3))
        ttk.Label(bar, text="至").pack(side="left")
        ttk.Entry(bar, textvariable=self.end_var, width=11).pack(side="left", padx=(3, 8))
        ttk.Combobox(
            bar,
            textvariable=self.type_var,
            state="readonly",
            width=12,
            values=("全部类型", "收入", "退款", "结算", "提现"),
        ).pack(side="left", padx=(4, 8))
        button(bar, "刷新资金", self._load, "Primary").pack(side="left")
        self.export_button = button(bar, "导出流水", self._export)
        self.export_button.pack(side="left", padx=6)
        self.export_button.configure(state="disabled")
        cards = ttk.Frame(self, style="TFrame")
        cards.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        for index in range(3):
            cards.columnconfigure(index, weight=1)
        self.available = metric_card(cards, "可提现余额", "—", "官方只读", "teal")
        self.available.grid(row=0, column=0, sticky="nsew", padx=(0, 4))
        self.pending = metric_card(cards, "待结算余额", "—", "官方只读", "gold")
        self.pending.grid(row=0, column=1, sticky="nsew", padx=4)
        self.flow_count = metric_card(cards, "流水条数", "0", "当前日期范围", "teal")
        self.flow_count.grid(row=0, column=2, sticky="nsew", padx=(4, 0))
        self.detail_var = tk.StringVar(value="选择店铺后读取资金流水")
        ttk.Label(self, textvariable=self.detail_var, style="Subtitle.TLabel").grid(
            row=2, column=0, sticky="w", pady=(2, 7)
        )
        body = tk.Frame(self, bg=PANEL, highlightbackground=BORDER, highlightthickness=1)
        body.grid(row=3, column=0, sticky="nsew")
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=1)
        columns = ("time", "type", "order_id", "flow_id", "amount", "balance", "remark")
        self.tree = ttk.Treeview(body, columns=columns, show="headings")
        for key, text, width in (
            ("time", "时间", 150),
            ("type", "类型", 100),
            ("order_id", "订单号", 190),
            ("flow_id", "流水号", 180),
            ("amount", "金额", 110),
            ("balance", "余额", 110),
            ("remark", "说明", 320),
        ):
            self.tree.heading(key, text=text)
            self.tree.column(key, width=width, anchor="w")
        self.tree.grid(row=0, column=0, sticky="nsew")
        ttk.Scrollbar(body, orient="vertical", command=self.tree.yview).grid(
            row=0, column=1, sticky="ns"
        )

    def refresh(self) -> None:
        stores = self.db.list_stores()
        self._stores = {
            f"全部 {len(stores)} 家店铺": None,
            **{
                f"{store.name} · #{store.id}": store
                for store in stores
            },
        }
        values = list(self._stores)
        self.store_combo["values"] = values
        if self.store_var.get() not in values:
            self.store_var.set(values[0] if values else "")
            self._store_changed()

    def _store_changed(self, _event=None) -> None:
        self._loaded_store_id = None
        self._flows = []
        self.tree.delete(*self.tree.get_children())
        self.available.winfo_children()[1].configure(text="—")
        self.pending.winfo_children()[1].configure(text="—")
        self.flow_count.winfo_children()[1].configure(text="0")
        self.export_button.configure(state="disabled")
        if self.store_var.get():
            self.detail_var.set("已选择店铺，点击“刷新资金”读取")
            self.window.set_status("已选择店铺，点击“刷新资金”读取")
        else:
            self.detail_var.set("请先添加店铺")
            self.window.set_status("请先添加店铺")

    def _load(self) -> None:
        store = self._stores.get(self.store_var.get())
        if store is None:
            return
        try:
            start = datetime.strptime(self.start_var.get().strip(), "%Y-%m-%d")
            end = datetime.strptime(self.end_var.get().strip(), "%Y-%m-%d")
        except ValueError:
            messagebox.showwarning("日期", "日期格式必须为 YYYY-MM-DD。", parent=self)
            return
        if end < start:
            messagebox.showwarning("日期", "结束日期不能早于开始日期。", parent=self)
            return
        start_time = int(datetime.combine(start.date(), time.min).timestamp())
        end_time = int(datetime.combine(end.date(), time.max).timestamp())
        self.window.set_status("正在读取资金信息…", busy=True)
        self.window.run_task(
            lambda _progress: self.window.service.fetch_funds_summary(
                store,
                start_time,
                end_time,
            ),
            self._render,
            lambda error: messagebox.showerror("资金读取失败", error, parent=self),
            log_title="读取店铺资金和流水",
        )

    def _render(self, data: dict) -> None:
        store = self._stores.get(self.store_var.get())
        self._loaded_store_id = store.id if store is not None else None
        self.available.winfo_children()[1].configure(
            text=f"¥{int(data.get('available_amount') or 0) / 100:,.2f}"
        )
        self.pending.winfo_children()[1].configure(
            text=f"¥{int(data.get('pending_amount') or 0) / 100:,.2f}"
        )
        flows = [
            flow for flow in (data.get("flow_list") or [])
            if isinstance(flow, dict)
        ]
        selected_type = self.type_var.get()
        if selected_type != "全部类型":
            flows = [
                flow for flow in flows
                if selected_type in json.dumps(flow, ensure_ascii=False)
            ]
        self._flows = flows
        self.tree.delete(*self.tree.get_children())
        for flow in flows:
            amount = int(
                flow.get("amount")
                or flow.get("flow_amount")
                or flow.get("settle_amount")
                or 0
            )
            ts = int(
                flow.get("create_time")
                or flow.get("time")
                or flow.get("updated_time")
                or 0
            )
            time_text = (
                datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
                if ts > 0
                else str(flow.get("create_time_str") or "")
            )
            self.tree.insert(
                "",
                "end",
                values=(
                    time_text,
                    str(flow.get("type") or flow.get("flow_type") or ""),
                    str(flow.get("order_id") or ""),
                    str(flow.get("flow_id") or flow.get("id") or ""),
                    f"¥{amount / 100:,.2f}",
                    f"¥{int(flow.get('balance') or 0) / 100:,.2f}",
                    str(flow.get("remark") or flow.get("description") or ""),
                ),
            )
        self.flow_count.winfo_children()[1].configure(text=f"{len(flows):,}")
        self.detail_var.set(f"当前日期范围资金流水：{len(flows)} 条")
        self.export_button.configure(
            state="normal" if flows else "disabled"
        )
        self.window.set_status("资金信息读取完成")

    def _export(self) -> None:
        if not self._flows:
            messagebox.showinfo("没有数据", "当前没有可导出的资金流水。", parent=self)
            return
        path = filedialog.asksaveasfilename(
            parent=self,
            title="导出资金流水",
            defaultextension=".xlsx",
            filetypes=[("Excel工作簿", "*.xlsx")],
            initialfile=f"资金流水_{date.today():%Y%m%d}.xlsx",
        )
        if not path:
            return
        headers = ["时间", "类型", "订单号", "流水号", "金额", "余额", "说明"]
        rows = [
            list(self.tree.item(item, "values"))
            for item in self.tree.get_children()
        ]
        try:
            export_xlsx(path, "资金流水", headers, rows)
        except Exception as exc:
            messagebox.showerror("导出失败", str(exc), parent=self)
            return
        self.window.set_status(f"已导出：{Path(path).name}")


class AutomationPage(BasePage):
    title = "自动化"
    subtitle = "本地定时同步、托管执行和运行日志"

    task_names = {
        "order_sync": "订单同步",
        "product_sync": "商品同步",
        "product_status": "商品状态刷新",
        "aftersale_sync": "售后同步",
        "analytics_sync": "经营数据同步",
        "procurement_sync": "采购账号同步",
        "hosted_execute": "托管项目执行",
    }

    def __init__(self, parent: tk.Widget, window: "MainWindow"):
        super().__init__(parent, window)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)
        self._rows: dict[str, dict] = {}
        self.state_var = tk.StringVar(value="本地自动化调度器已启动")

        toolbar = ttk.Frame(self, style="TFrame")
        toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        button(toolbar, "新增任务", self._add_task, "Primary").pack(side="left")
        self.run_task_button = button(toolbar, "立即执行", self._run_selected)
        self.run_task_button.pack(side="left", padx=6)
        self.toggle_task_button = button(
            toolbar,
            "启用/停用",
            self._toggle_selected,
        )
        self.toggle_task_button.pack(side="left")
        self.delete_task_button = button(
            toolbar,
            "删除任务",
            self._delete_selected,
            "Danger",
        )
        self.delete_task_button.pack(side="left", padx=6)
        button(toolbar, "查看执行记录", self._show_runs).pack(side="left")
        ttk.Label(
            toolbar,
            textvariable=self.state_var,
            style="Subtitle.TLabel",
        ).pack(side="right")

        ttk.Label(
            self,
            text="任务只在澜琅运行时执行；关闭软件后不会在后台运行。",
            style="Subtitle.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(0, 8))

        body = tk.Frame(self, bg=PANEL, highlightbackground=BORDER, highlightthickness=1)
        body.grid(row=2, column=0, sticky="nsew")
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=1)
        columns = ("name", "type", "store", "interval", "enabled", "next", "status", "message")
        self.tree = ttk.Treeview(body, columns=columns, show="headings")
        for key, text, width in (
            ("name", "任务名称", 180),
            ("type", "动作", 140),
            ("store", "店铺", 160),
            ("interval", "间隔", 90),
            ("enabled", "状态", 80),
            ("next", "下次执行", 150),
            ("status", "结果", 90),
            ("message", "说明", 320),
        ):
            self.tree.heading(key, text=text)
            self.tree.column(key, width=width, anchor="w")
        self.tree.grid(row=0, column=0, sticky="nsew")
        self.tree.bind("<<TreeviewSelect>>", self._update_action_state)
        ttk.Scrollbar(body, orient="vertical", command=self.tree.yview).grid(
            row=0,
            column=1,
            sticky="ns",
        )
        self.after(60000, self._tick)

    def refresh(self) -> None:
        tasks = self.db.list_automation_tasks()
        self.tree.delete(*self.tree.get_children())
        self._rows.clear()
        enabled = 0
        for index, task in enumerate(tasks):
            iid = str(index)
            self._rows[iid] = task
            if task.get("enabled"):
                enabled += 1
            self.tree.insert(
                "",
                "end",
                iid=iid,
                values=(
                    task["name"],
                    self.task_names.get(task["task_type"], task["task_type"]),
                    task.get("store_name") or "全部店铺",
                    f"{task['interval_minutes']} 分钟",
                    "运行中" if task.get("enabled") else "已暂停",
                    task.get("next_run") or "",
                    task.get("last_status") or "",
                    task.get("last_message") or "",
                ),
            )
        self.state_var.set(f"{len(tasks)} 个任务 · {enabled} 个运行中")
        self._update_action_state()

    def _update_action_state(self, _event=None) -> None:
        state = "normal" if self.tree.selection() else "disabled"
        for action_button in (
            self.run_task_button,
            self.toggle_task_button,
            self.delete_task_button,
        ):
            action_button.configure(state=state)

    def _selected(self) -> dict | None:
        selection = self.tree.selection()
        return self._rows.get(selection[0]) if selection else None

    def _add_task(self) -> None:
        dialog = tk.Toplevel(self)
        dialog.title("新增自动化任务")
        dialog.transient(self.winfo_toplevel())
        dialog.resizable(False, False)
        name_var = tk.StringVar(value="定时同步")
        type_var = tk.StringVar(value="订单同步")
        store_var = tk.StringVar(value="全部店铺")
        interval_var = tk.StringVar(value="60")
        stores = self.db.list_stores()
        store_map = {store.name: store.id for store in stores}
        frame = ttk.Frame(dialog, style="Panel.TFrame", padding=20)
        frame.pack(fill="both", expand=True, padx=12, pady=12)
        frame.columnconfigure(1, weight=1)
        type_labels = {label: key for key, label in self.task_names.items()}
        controls = (
            ("任务名称", ttk.Entry(frame, textvariable=name_var, width=30)),
            (
                "执行动作",
                ttk.Combobox(
                    frame,
                    textvariable=type_var,
                    state="readonly",
                    values=tuple(type_labels),
                    width=27,
                ),
            ),
            (
                "目标店铺",
                ttk.Combobox(
                    frame,
                    textvariable=store_var,
                    state="readonly",
                    values=("全部店铺", *store_map),
                    width=27,
                ),
            ),
            ("间隔分钟", ttk.Entry(frame, textvariable=interval_var, width=30)),
        )
        for row, (label, widget) in enumerate(controls):
            ttk.Label(frame, text=label, style="Panel.TLabel").grid(
                row=row, column=0, sticky="w", padx=(0, 12), pady=6
            )
            widget.grid(row=row, column=1, sticky="ew", pady=6)

        def save() -> None:
            try:
                interval = max(1, int(interval_var.get()))
            except ValueError:
                messagebox.showwarning("间隔", "间隔必须是整数分钟。", parent=dialog)
                return
            task_type = type_labels.get(type_var.get())
            if not task_type:
                return
            store_id = store_map.get(store_var.get())
            self.db.add_automation_task(
                name_var.get().strip() or type_var.get(),
                task_type,
                store_id,
                interval,
            )
            dialog.destroy()
            self.refresh()

        ttk.Button(frame, text="保存", command=save).grid(
            row=len(controls),
            column=1,
            sticky="e",
            pady=(12, 0),
        )
        dialog.grab_set()

    def _toggle_selected(self) -> None:
        task = self._selected()
        if task is None:
            return
        self.db.update_automation_task(
            int(task["id"]),
            enabled=0 if task.get("enabled") else 1,
        )
        self.refresh()

    def _delete_selected(self) -> None:
        task = self._selected()
        if task is None:
            return
        if not messagebox.askyesno(
            "删除任务",
            f"确定删除自动化任务“{task['name']}”吗？",
            parent=self,
        ):
            return
        self.db.delete_automation_task(int(task["id"]))
        self.refresh()

    def _run_selected(self) -> None:
        task = self._selected()
        if task is None:
            return
        self._execute_tasks([task])

    def _execute_tasks(self, tasks: list[dict]) -> None:
        if not tasks:
            return
        self.window.set_status(f"正在执行 {len(tasks)} 个自动化任务…", busy=True)

        def work(_progress) -> list[str]:
            errors = []
            for task in tasks:
                try:
                    message = self._execute_one(task)
                    now = datetime.now()
                    next_run = now + timedelta(
                        minutes=max(1, int(task["interval_minutes"]))
                    )
                    self.db.update_automation_task(
                        int(task["id"]),
                        last_run=now.strftime("%Y-%m-%d %H:%M:%S"),
                        next_run=next_run.strftime("%Y-%m-%d %H:%M:%S"),
                        last_status="成功",
                        last_message=message,
                    )
                    self.db.log_automation_run(
                        int(task["id"]),
                        "success",
                        message,
                    )
                except Exception as exc:
                    errors.append(f"{task['name']}: {exc}")
                    self.db.update_automation_task(
                        int(task["id"]),
                        last_run=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        last_status="失败",
                        last_message=str(exc)[:1000],
                    )
                    self.db.log_automation_run(
                        int(task["id"]),
                        "failed",
                        str(exc),
                    )
            return errors

        def done(errors: list[str]) -> None:
            self.refresh()
            self.window.set_status(
                f"自动化执行完成，成功 {len(tasks) - len(errors)} 个"
            )
            if errors:
                messagebox.showwarning(
                    "部分自动化任务失败",
                    "\n".join(errors[:8]),
                    parent=self,
                )

        self.window.run_task(
            work,
            done,
            lambda error: messagebox.showerror("自动化执行失败", error, parent=self),
            log_title="执行自动化任务",
        )

    def _execute_one(self, task: dict) -> str:
        store_id = task.get("store_id")
        stores = (
            [self.db.get_store(int(store_id))]
            if store_id
            else self.db.list_stores()
        )
        stores = [store for store in stores if store is not None]
        task_type = task["task_type"]
        if task_type == "procurement_sync":
            accounts = self.db.list_procurement_accounts()
            count = 0
            for account in accounts:
                count += self.window.service.sync_procurement_account(
                    int(account["id"])
                ).count
            return f"采购同步 {count} 条"
        if task_type == "hosted_execute":
            self.window.hosted_page._execute_rows(
                [
                    item
                    for item in self.window.hosted_page._rows.values()
                    if item["config"].get("enabled")
                    and (
                        not store_id
                        or int(item["store"].id) == int(store_id)
                    )
                ],
                scheduled=True,
            )
            return "已提交托管执行"
        now = int(datetime.now().timestamp())
        messages = []
        for store in stores:
            if task_type == "order_sync":
                result = self.window.service.sync_orders(
                    store,
                    now - 7 * 86400,
                    now,
                )
                messages.append(result.message)
            elif task_type == "product_sync":
                result = self.window.service.sync_products(store, max_items=300)
                self.window.service.sync_product_details(
                    store,
                    max_items=300,
                )
                messages.append(result.message)
            elif task_type == "product_status":
                result = self.window.service.refresh_product_status_snapshot(store)
                messages.append(result.message)
            elif task_type == "aftersale_sync":
                result = self.window.service.sync_after_sales(
                    store,
                    now - 86400,
                    now,
                    time_field="update",
                )
                messages.append(result.message)
            elif task_type == "analytics_sync":
                result = self.window.service.sync_analytics_day(
                    store,
                    date.today().isoformat(),
                )
                messages.append(result.message)
        return "；".join(messages) or "没有可执行对象"

    def _tick(self) -> None:
        try:
            tasks = self.db.list_automation_tasks()
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            due = [
                task
                for task in tasks
                if task.get("enabled")
                and (not task.get("next_run") or str(task["next_run"]) <= now)
            ]
            if due and not self.window._task_running:
                self._execute_tasks(due)
        finally:
            self.after(60000, self._tick)

    def _show_runs(self) -> None:
        runs = self.db.list_automation_runs()
        if not runs:
            messagebox.showinfo("执行记录", "暂无自动化执行记录。", parent=self)
            return
        lines = [
            f"{row['finished_at'] or row['started_at']} · "
            f"{row['task_name']} · {row['status']} · {row['message']}"
            for row in runs[:80]
        ]
        dialog = tk.Toplevel(self)
        dialog.title("自动化执行记录")
        dialog.geometry("900x600")
        text = tk.Text(dialog, wrap="word")
        text.pack(fill="both", expand=True)
        text.insert("1.0", "\n".join(lines))


class AccountPage(BasePage):
    title = "账号中心"
    subtitle = "本地账号、平台会话、数据库备份与运行信息"

    def __init__(self, parent: tk.Widget, window: "MainWindow"):
        super().__init__(parent, window)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)
        self.summary_var = tk.StringVar()
        context = getattr(window, "auth_context", None)
        role = getattr(context, "member_role", "owner")
        self.is_manager = role in {"owner", "admin"}

        toolbar = ttk.Frame(self, style="TFrame")
        toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        if self.is_manager:
            button(
                toolbar,
                "备份数据库",
                self._backup,
                "Primary",
            ).pack(side="left")
            button(toolbar, "恢复数据库", self._restore).pack(
                side="left",
                padx=6,
            )
            button(toolbar, "打开数据目录", self._open_data_dir).pack(
                side="left"
            )
            button(
                toolbar,
                "清空全部网页登录",
                self._clear_web_sessions,
                "Danger",
            ).pack(side="left", padx=6)
        button(toolbar, "刷新", self.refresh).pack(side="left")
        ttk.Label(
            toolbar,
            textvariable=self.summary_var,
            style="Subtitle.TLabel",
        ).pack(side="right")

        note = tk.Frame(self, bg=SOFT_GOLD, padx=12, pady=8)
        note.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        tk.Label(
            note,
            text=(
                "所有业务数据保存在本机SQLite。AppSecret、Cookie和采购会话均单独加密；"
                "恢复备份后应重新启动澜琅，确保所有页面读取新数据。"
            ),
            bg=SOFT_GOLD,
            fg="#705b2e",
            anchor="w",
        ).pack(fill="x")

        body = tk.Frame(self, bg=PANEL, highlightbackground=BORDER, highlightthickness=1)
        body.grid(row=2, column=0, sticky="nsew")
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=1)
        self.tree = ttk.Treeview(
            body,
            columns=("item", "status", "detail"),
            show="headings",
        )
        for key, text, width in (
            ("item", "项目", 220),
            ("status", "状态", 180),
            ("detail", "说明", 620),
        ):
            self.tree.heading(key, text=text)
            self.tree.column(key, width=width, anchor="w")
        self.tree.grid(row=0, column=0, sticky="nsew")
        ttk.Scrollbar(body, orient="vertical", command=self.tree.yview).grid(
            row=0,
            column=1,
            sticky="ns",
        )

    def refresh(self) -> None:
        counts = self.db.data_counts()
        backup_dir = self.window.paths.data_dir / "backups"
        backups = (
            sorted(
                backup_dir.glob("weimi_manager_*.db"),
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            )
            if backup_dir.is_dir()
            else []
        )
        latest_backup = backups[0] if backups else None
        size = self.db.path.stat().st_size if self.db.path.exists() else 0
        context = getattr(self.window, "auth_context", None)
        role_labels = {
            "owner": "主账号",
            "admin": "共同主账号",
            "manager": "管理员",
            "operator": "运营子账号",
            "viewer": "只读子账号",
        }
        role = str(getattr(context, "member_role", "owner") or "owner")
        permissions = getattr(context, "permissions", {})
        enabled_permissions = (
            sum(bool(value) for value in permissions.values())
            if isinstance(permissions, dict)
            else 0
        )
        rows = (
            ("软件版本", __version__, "澜琅微信小店版"),
            (
                "当前账号",
                str(getattr(context, "username", "") or "开发模式"),
                role_labels.get(role, role),
            ),
            (
                "团队权限",
                "全部功能" if self.is_manager else f"{enabled_permissions} 项",
                (
                    "主账号可管理团队、店铺和全部业务功能"
                    if self.is_manager
                    else "由主账号分配，子账号不能管理团队或其他账号"
                ),
            ),
            ("本地数据库", "正常", str(self.db.path)),
            ("数据库大小", f"{size / 1024 / 1024:.2f} MB", "本机SQLite"),
            (
                "微信店铺",
                f"{counts['stores']} 家",
                f"网页已登录 {counts['logged_stores']} 家",
            ),
            ("微信订单", f"{counts['orders']} 条", "本地缓存"),
            ("商品详情", f"{counts['products']} 条", "本地缓存"),
            ("售后单", f"{counts['after_sales']} 条", "本地缓存"),
            (
                "采购账号",
                f"{counts['procurement_accounts']} 个",
                "淘宝/1688本地浏览器会话",
            ),
            (
                "自动化任务",
                f"{counts['automation_tasks']} 个",
                "仅软件运行时执行",
            ),
            (
                "自动备份",
                (
                    latest_backup.name
                    if latest_backup is not None
                    else "尚未生成"
                ),
                f"每天自动备份，目录 {backup_dir}",
            ),
            (
                "运行设备",
                "当前Windows用户",
                f"升级和重新解压不会清空：{self.window.paths.data_dir}",
            ),
            (
                "组织同步",
                (
                    str(getattr(context, "organization_name", "") or "已加入")
                    if getattr(context, "organization_id", "")
                    else "未加入组织"
                ),
                "点击店铺管理中的“刷新数据”同步组织店铺",
            ),
        )
        self.tree.delete(*self.tree.get_children())
        for row in rows:
            self.tree.insert("", "end", values=row)
        self.summary_var.set(
            f"{counts['stores']} 家店铺 · {counts['orders']} 条订单 · "
            f"{counts['products']} 个商品"
        )

    def _backup(self) -> None:
        path = filedialog.asksaveasfilename(
            parent=self,
            title="备份澜琅数据库",
            defaultextension=".db",
            filetypes=[("SQLite数据库", "*.db"), ("所有文件", "*.*")],
            initialfile=f"langlang_backup_{date.today():%Y%m%d}.db",
        )
        if not path:
            return
        self.db.backup_to(Path(path))
        messagebox.showinfo("备份完成", f"数据库已备份到：\n{path}", parent=self)

    def _restore(self) -> None:
        path = filedialog.askopenfilename(
            parent=self,
            title="选择数据库备份",
            filetypes=[("SQLite数据库", "*.db"), ("所有文件", "*.*")],
        )
        if not path:
            return
        if not messagebox.askyesno(
            "恢复数据库",
            "恢复会覆盖当前店铺、订单、商品、采购和设置数据。是否继续？",
            parent=self,
        ):
            return
        safety = self.window.paths.data_dir / "before_restore.db"
        self.db.backup_to(safety)
        self.db.restore_from(Path(path))
        self.window.store_data_changed()
        messagebox.showinfo(
            "恢复完成",
            f"数据已恢复。恢复前数据备份在：\n{safety}\n\n请重新启动澜琅。",
            parent=self,
        )

    def _open_data_dir(self) -> None:
        self.window.paths.data_dir.mkdir(parents=True, exist_ok=True)
        os.startfile(str(self.window.paths.data_dir))

    def _clear_web_sessions(self) -> None:
        if not messagebox.askyesno(
            "清空全部网页登录",
            "微信店铺、1688和淘宝采购账号的本地登录会话都会被清除。是否继续？",
            parent=self,
        ):
            return
        for store in self.db.list_stores():
            self.db.clear_web_session(store.id, "账号中心批量清理")
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE procurement_accounts SET status='未登录', last_error='登录态已清理'"
            )
            conn.execute("DELETE FROM procurement_sessions")
        self.window.store_data_changed()
        self.refresh()


class SalesSuggestionsPage(BasePage):
    title = "动销建议"
    subtitle = "曝光 · 点击 · 成交 · 动销建议"

    def __init__(self, parent: tk.Widget, window: "MainWindow"):
        super().__init__(parent, window)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(4, weight=1)
        self._stores: dict[str, object] = {}
        self._rows: dict[str, dict] = {}
        self.store_var = tk.StringVar()
        self.period_var = tk.StringVar(value="3 天")
        self.view_var = tk.StringVar(value="全部销售中")
        self.hit_filter_var = tk.StringVar(value="全部")
        self.min_exposure_var = tk.StringVar(value="0")
        self.max_orders_var = tk.StringVar(value="3")
        self.max_sales_var = tk.StringVar(value="3")
        self.min_clicks_var = tk.StringVar(value="0")
        self.listing_mode_var = tk.StringVar(value="不限")
        self.listing_start_var = tk.StringVar()
        self.listing_end_var = tk.StringVar()
        self.export_scope_var = tk.StringVar(value="包含新上架")
        self.summary_var = tk.StringVar(value="等待智能分析")
        self.empty_var = tk.StringVar(value="还没有分析结果")

        toolbar = ttk.Frame(self, style="TFrame")
        toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ttk.Label(toolbar, text="分析范围").pack(side="left")
        self.store_combo = ttk.Combobox(
            toolbar,
            textvariable=self.store_var,
            state="readonly",
            width=20,
        )
        self.store_combo.pack(side="left", padx=(5, 10))
        ttk.Label(toolbar, text="统计周期").pack(side="left")
        ttk.Combobox(
            toolbar,
            textvariable=self.period_var,
            state="readonly",
            width=9,
            values=("3 天", "7 天", "15 天", "30 天"),
        ).pack(side="left", padx=(5, 10))
        ttk.Label(toolbar, text="范围").pack(side="left")
        ttk.Combobox(
            toolbar,
            textvariable=self.view_var,
            state="readonly",
            width=13,
            values=("全部销售中", "待动销", "新上架"),
        ).pack(side="left", padx=(5, 8))
        ttk.Label(toolbar, text="命中条件").pack(side="left")
        ttk.Combobox(
            toolbar,
            textvariable=self.hit_filter_var,
            state="readonly",
            width=9,
            values=("全部", "命中", "价格"),
        ).pack(side="left", padx=(5, 8))
        button(toolbar, "查询", self._load).pack(side="left")
        button(toolbar, "智能分析", self._analyze, "Primary").pack(
            side="left",
            padx=6,
        )
        button(toolbar, "规则设置…", self._show_rule_settings).pack(side="left")
        button(toolbar, "恢复默认", self._restore_defaults).pack(
            side="left", padx=6
        )
        filters = ttk.Frame(self, style="TFrame")
        filters.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        ttk.Label(filters, text="命中条件").pack(side="left")
        for label, variable, suffix in (
            ("曝光 ≥", self.min_exposure_var, "次"),
            ("成交订单 <", self.max_orders_var, "单"),
            ("累计销量 <", self.max_sales_var, "件"),
            ("点击 ≥", self.min_clicks_var, "人"),
        ):
            ttk.Label(filters, text=label).pack(side="left", padx=(9, 3))
            ttk.Entry(filters, textvariable=variable, width=7).pack(side="left")
            ttk.Label(filters, text=suffix).pack(side="left", padx=(2, 0))
        ttk.Label(filters, text="上架日期").pack(side="left", padx=(14, 4))
        ttk.Combobox(
            filters,
            textvariable=self.listing_mode_var,
            state="readonly",
            width=10,
            values=("不限", "自定义"),
        ).pack(side="left")
        ttk.Entry(
            filters, textvariable=self.listing_start_var, width=11
        ).pack(side="left", padx=(4, 2))
        ttk.Label(filters, text="至").pack(side="left")
        ttk.Entry(
            filters, textvariable=self.listing_end_var, width=11
        ).pack(side="left", padx=(2, 6))
        button(filters, "重置条件", self._reset_filters).pack(side="left")

        legend = tk.Frame(self, bg=SOFT_GOLD, padx=12, pady=8)
        legend.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        tk.Label(
            legend,
            text=(
                "规则：销售中商品才参与分析；上架超过所选周期且累计销量<3 标记为“建议动销”，"
                "新上架商品单独标记观察。智能分析会读取商品30天浏览、点击和成交指标。"
            ),
            bg=SOFT_GOLD,
            fg="#705b2e",
            justify="left",
            anchor="w",
        ).pack(fill="x")

        export_bar = ttk.Frame(self, style="TFrame")
        export_bar.grid(row=3, column=0, sticky="ew", pady=(0, 8))
        button(export_bar, "导出 Excel", self._export).pack(side="left")
        button(export_bar, "导出待动销", self._export_pending).pack(
            side="left", padx=6
        )
        ttk.Label(
            export_bar, text="导出时新上架的品"
        ).pack(side="left", padx=(10, 4))
        ttk.Combobox(
            export_bar,
            textvariable=self.export_scope_var,
            state="readonly",
            width=12,
            values=("包含新上架", "不含新上架"),
        ).pack(side="left")
        button(export_bar, "导出新上架项", self._export_new).pack(
            side="left", padx=6
        )
        ttk.Label(
            export_bar,
            textvariable=self.summary_var,
            style="Subtitle.TLabel",
        ).pack(side="right")

        body = tk.Frame(self, bg=PANEL, highlightbackground=BORDER, highlightthickness=1)
        body.grid(row=4, column=0, sticky="nsew")
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=1)
        columns = (
            "seq", "store", "id", "title", "listed", "views", "clicks",
            "sold", "price", "stock", "suggestion",
        )
        self.tree = ttk.Treeview(body, columns=columns, show="headings")
        for key, text, width in (
            ("seq", "序号", 55),
            ("store", "店铺", 130),
            ("id", "商品ID", 150),
            ("title", "商品标题", 320),
            ("listed", "上架时间", 105),
            ("views", "30天浏览", 90),
            ("clicks", "30天点击", 90),
            ("sold", "累计销量", 80),
            ("price", "价格", 90),
            ("stock", "库存", 80),
            ("suggestion", "建议", 110),
        ):
            self.tree.heading(key, text=text)
            self.tree.column(key, width=width, anchor="w")
        self.tree.grid(row=0, column=0, sticky="nsew")
        ttk.Scrollbar(body, orient="vertical", command=self.tree.yview).grid(
            row=0,
            column=1,
            sticky="ns",
        )
        self.empty_label = tk.Label(
            body,
            text=(
                "还没有分析结果\n\n"
                "点“智能分析”拉取店铺数据；之后修改条件点“查询”"
                "即可在本机快速重算。"
            ),
            bg=PANEL,
            fg=MUTED,
            justify="center",
            font=("Microsoft YaHei UI", 10),
        )

    def refresh(self) -> None:
        stores = self.db.list_stores()
        self._stores = {
            f"全部 {len(stores)} 家店铺": None,
            **{
                f"{store.name} · #{store.id}": store
                for store in stores
            },
        }
        values = list(self._stores)
        self.store_combo["values"] = values
        if self.store_var.get() not in values:
            self.store_var.set(values[0] if values else "")
        self._load()

    def _selected_store(self):
        return self._stores.get(self.store_var.get())

    @staticmethod
    def _raw(row: dict) -> dict:
        try:
            payload = json.loads(row.get("raw_json") or "{}")
        except json.JSONDecodeError:
            return {}
        return payload if isinstance(payload, dict) else {}

    @classmethod
    def _listed_at(cls, row: dict) -> int:
        raw = cls._raw(row)
        for key in ("listing_time", "onsale_time", "create_time", "edit_time"):
            try:
                value = int(raw.get(key) or 0)
            except (TypeError, ValueError):
                value = 0
            if value:
                return value
        return 0

    def _load(self) -> None:
        store = self._selected_store()
        stores = [store] if store is not None else list(self.db.list_stores())
        if not stores:
            self.tree.delete(*self.tree.get_children())
            return
        days = int(self.period_var.get().split()[0])
        try:
            min_exposure = max(0, int(self.min_exposure_var.get() or 0))
            max_orders = max(0, int(self.max_orders_var.get() or 0))
            max_sales = max(0, int(self.max_sales_var.get() or 0))
            min_clicks = max(0, int(self.min_clicks_var.get() or 0))
        except ValueError:
            messagebox.showwarning(
                "筛选条件", "动销筛选条件必须是整数。", parent=self
            )
            return
        listing_start = listing_end = None
        if self.listing_mode_var.get() == "自定义":
            try:
                if self.listing_start_var.get().strip():
                    listing_start = date.fromisoformat(
                        self.listing_start_var.get().strip()
                    )
                if self.listing_end_var.get().strip():
                    listing_end = date.fromisoformat(
                        self.listing_end_var.get().strip()
                    )
            except ValueError:
                messagebox.showwarning(
                    "日期格式",
                    "上架日期请使用 YYYY-MM-DD。",
                    parent=self,
                )
                return
        default_rules = {
            "price_click_lt": 1,
            "conversion_pay_le": 0,
            "suggest_sold_lt": 3,
            "exposure_low": 50,
        }
        try:
            saved_rules = json.loads(
                self.db.get_setting("dynamic_rule_settings", "{}")
            )
        except (TypeError, ValueError):
            saved_rules = {}
        for key in default_rules:
            try:
                default_rules[key] = float(
                    (saved_rules or {}).get(key, default_rules[key])
                )
            except (TypeError, ValueError, AttributeError):
                pass
        prepared = []
        for current_store in stores:
            metrics_map = self.db.get_product_metrics_map(current_store.id)
            rows = self.db.list_products(
                store_id=current_store.id,
                status=5,
            )
            for row in rows:
                raw = self._raw(row)
                sold = int(
                    raw.get("total_sold_num")
                    or raw.get("sold_num")
                    or 0
                )
                metrics = metrics_map.get(str(row["product_id"]), {})
                views = int(metrics.get("views30") or 0)
                clicks = int(metrics.get("click_cnt") or 0)
                orders = int(metrics.get("pay_cnt") or 0)
                listed_at = self._listed_at(row)
                listed_date = (
                    date.fromtimestamp(listed_at) if listed_at else None
                )
                if listing_start and (
                    listed_date is None or listed_date < listing_start
                ):
                    continue
                if listing_end and (
                    listed_date is None or listed_date > listing_end
                ):
                    continue
                if views < min_exposure or clicks < min_clicks:
                    continue
                if max_orders and orders >= max_orders:
                    continue
                if max_sales and sold >= max_sales:
                    continue
                age_days = (
                    (date.today() - listed_date).days
                    if listed_date is not None
                    else 999
                )
                if age_days < days:
                    suggestion = "新上架观察"
                elif views < default_rules["exposure_low"]:
                    suggestion = "曝光不足"
                elif clicks < default_rules["price_click_lt"]:
                    suggestion = "零点击"
                elif orders <= default_rules["conversion_pay_le"]:
                    suggestion = "零成交"
                elif sold < default_rules["suggest_sold_lt"]:
                    suggestion = "建议动销"
                else:
                    suggestion = "正常"
                selected = self.view_var.get()
                if (
                    selected == "待动销"
                    and suggestion not in {
                        "建议动销",
                        "曝光不足",
                        "零点击",
                        "零成交",
                    }
                ):
                    continue
                if (
                    selected == "新上架"
                    and suggestion != "新上架观察"
                ):
                    continue
                hit_filter = self.hit_filter_var.get()
                if hit_filter == "命中" and suggestion == "正常":
                    continue
                if hit_filter == "价格" and suggestion not in {
                    "曝光不足",
                    "零点击",
                }:
                    continue
                prepared.append(
                    (
                        row,
                        raw,
                        sold,
                        listed_at,
                        views,
                        clicks,
                        suggestion,
                        orders,
                    )
                )
        prepared.sort(key=lambda item: item[3] or 0)
        self.tree.delete(*self.tree.get_children())
        self._rows.clear()
        for index, (
            row,
            raw,
            sold,
            listed_at,
            views,
            clicks,
            suggestion,
            orders,
        ) in enumerate(prepared, 1):
            iid = str(index - 1)
            self._rows[iid] = row
            self.tree.insert(
                "",
                "end",
                iid=iid,
                values=(
                    index,
                    row["store_name"],
                    row["product_id"],
                    row["title"],
                    date.fromtimestamp(listed_at).isoformat() if listed_at else "",
                    views,
                    clicks,
                    sold,
                    f"¥{(row['min_price'] or 0) / 100:.2f}",
                    row["stock_total"],
                    suggestion,
                ),
            )
        waiting = sum(
            1
            for item in prepared
            if item[6]
            in {"建议动销", "曝光不足", "零点击", "零成交"}
        )
        fresh = sum(1 for item in prepared if item[6] == "新上架观察")
        if prepared:
            self.empty_label.place_forget()
        else:
            self.empty_label.place(
                relx=0.5, rely=0.48, anchor="center"
            )
        self.summary_var.set(
            f"当前 {len(prepared)} 个商品 · 建议动销 {waiting} · 新上架 {fresh}"
        )

    def _analyze(self) -> None:
        store = self._selected_store()
        stores = [store] if store is not None else list(self.db.list_stores())
        if not stores:
            messagebox.showinfo(
                "没有店铺", "请先添加微信店铺。", parent=self
            )
            return
        self.window.set_status("正在刷新商品经营数据…", busy=True)

        def work(progress):
            messages = []
            for index, current_store in enumerate(stores, 1):
                if progress:
                    progress(
                        f"正在分析 {current_store.name} "
                        f"{index}/{len(stores)}",
                        index,
                        len(stores),
                    )
                details = self.window.service.sync_product_details(
                    current_store,
                    max_items=300,
                    workers=4,
                )
                product_ids = [
                    str(row["product_id"])
                    for row in self.db.list_products(
                        store_id=current_store.id,
                        status=5,
                    )
                ]
                performance = self.window.service.sync_product_performance(
                    current_store,
                    product_ids,
                    days=30,
                )
                messages.append(
                    f"{current_store.name}：{details.message}；"
                    f"{performance.message}"
                )
            return {
                "messages": messages,
                "store_count": len(stores),
            }

        def done(result) -> None:
            messages = result["messages"]
            self._load()
            self.window.set_status(
                f"智能分析完成：{result['store_count']} 家店铺"
            )

        self.window.run_task(
            work,
            done,
            lambda error: messagebox.showerror(
                "智能分析失败", error, parent=self
            ),
            log_title="刷新并分析商品经营数据",
        )

    def _export(self) -> None:
        if not self._rows:
            messagebox.showinfo("没有数据", "当前没有待导出的商品。", parent=self)
            return
        path = filedialog.asksaveasfilename(
            parent=self,
            title="导出待动销",
            defaultextension=".xlsx",
            filetypes=[("Excel 工作簿", "*.xlsx")],
            initialfile=f"待动销_{date.today():%Y%m%d}.xlsx",
        )
        if not path:
            return
        headers = [
            "序号", "店铺", "商品ID", "商品标题", "上架时间",
            "30天浏览", "30天点击", "累计销量", "价格", "库存", "建议",
        ]
        rows = []
        for index, item in enumerate(self.tree.get_children(), 1):
            values = self.tree.item(item, "values")
            if (
                self.export_scope_var.get() == "不含新上架"
                and len(values) > 10
                and values[10] == "新上架观察"
            ):
                continue
            rows.append([index, *values[1:]])
        if not rows:
            messagebox.showinfo(
                "没有数据", "当前导出条件下没有商品。", parent=self
            )
            return
        try:
            export_xlsx(path, "动销建议", headers, rows)
        except Exception as exc:
            messagebox.showerror("导出失败", str(exc), parent=self)
            return
        self.window.set_status(f"已导出：{Path(path).name}")

    def _export_pending(self) -> None:
        items = [
            iid
            for iid in self.tree.get_children()
            if len(self.tree.item(iid, "values")) > 10
            and self.tree.item(iid, "values")[10]
            in {"建议动销", "曝光不足", "零点击", "零成交"}
        ]
        if not items:
            messagebox.showinfo(
                "没有数据", "当前没有待动销商品。", parent=self
            )
            return
        path = filedialog.asksaveasfilename(
            parent=self,
            title="导出待动销",
            defaultextension=".xlsx",
            filetypes=[("Excel 工作簿", "*.xlsx")],
            initialfile=f"待动销_{date.today():%Y%m%d}.xlsx",
        )
        if not path:
            return
        headers = [
            "序号",
            "店铺",
            "商品ID",
            "商品标题",
            "上架时间",
            "30天浏览",
            "30天点击",
            "累计销量",
            "价格",
            "库存",
            "建议",
        ]
        rows = []
        for index, iid in enumerate(items, 1):
            values = self.tree.item(iid, "values")
            rows.append([index, *values[1:]])
        try:
            export_xlsx(path, "待动销", headers, rows)
        except Exception as exc:
            messagebox.showerror("导出失败", str(exc), parent=self)
            return
        self.window.set_status(f"已导出：{Path(path).name}")

    def _reset_filters(self) -> None:
        self.min_exposure_var.set("0")
        self.max_orders_var.set("3")
        self.max_sales_var.set("3")
        self.min_clicks_var.set("0")
        self.listing_mode_var.set("不限")
        self.listing_start_var.set("")
        self.listing_end_var.set("")
        self.view_var.set("全部销售中")
        self.hit_filter_var.set("全部")
        self.period_var.set("3 天")
        self._load()

    def _restore_defaults(self) -> None:
        if not messagebox.askyesno(
            "恢复默认",
            "把筛选条件和命中规则都恢复成默认值？",
            parent=self,
        ):
            return
        self.db.set_setting("dynamic_rule_settings", "{}")
        self._reset_filters()

    def _show_rule_settings(self) -> None:
        defaults = {
            "views_band_low": "0",
            "views_band_high": "999999",
            "clicks_band_low": "0",
            "clicks_band_high": "999999",
            "price_click_lt": "1",
            "conversion_pay_le": "0",
            "suggest_sold_lt": "3",
            "exposure_low": "50",
        }
        try:
            saved = json.loads(
                self.db.get_setting("dynamic_rule_settings", "{}")
            )
        except (TypeError, ValueError):
            saved = {}
        if isinstance(saved, dict):
            defaults.update(
                {
                    key: str(value)
                    for key, value in saved.items()
                    if key in defaults
                }
            )

        def save(fields: dict[str, str]) -> None:
            try:
                for value in fields.values():
                    float(value)
            except ValueError:
                messagebox.showwarning(
                    "规则设置",
                    "规则阈值必须是数字。",
                    parent=self,
                )
                return
            self.db.set_setting(
                "dynamic_rule_settings",
                json.dumps(fields, ensure_ascii=False),
            )
            self._load()

        SimpleFieldsDialog(
            self,
            "动销规则设置",
            (
                ("views_band_low", "曝光规则带下界"),
                ("views_band_high", "曝光规则带上界"),
                ("clicks_band_low", "点击规则带下界"),
                ("clicks_band_high", "点击规则带上界"),
                ("price_click_lt", "价格偏高：点击低于"),
                ("conversion_pay_le", "转换偏低：成交不高于"),
                ("suggest_sold_lt", "建议动销：累计销量低于"),
                ("exposure_low", "曝光不足：曝光低于"),
            ),
            save,
            defaults,
        )

    def _export_new(self) -> None:
        items = [
            iid
            for iid in self.tree.get_children()
            if len(self.tree.item(iid, "values")) > 10
            and self.tree.item(iid, "values")[10] == "新上架观察"
        ]
        if not items:
            messagebox.showinfo(
                "没有数据",
                "当前没有新上架商品。",
                parent=self,
            )
            return
        path = filedialog.asksaveasfilename(
            parent=self,
            title="导出新上架商品",
            defaultextension=".xlsx",
            filetypes=[("Excel 工作簿", "*.xlsx")],
            initialfile=f"新上架_{date.today():%Y%m%d}.xlsx",
        )
        if not path:
            return
        headers = [
            "序号",
            "店铺",
            "商品ID",
            "商品标题",
            "上架时间",
            "30天浏览",
            "30天点击",
            "累计销量",
            "价格",
            "库存",
            "建议",
        ]
        rows = [
            [index, *self.tree.item(iid, "values")[1:]]
            for index, iid in enumerate(items, 1)
        ]
        try:
            export_xlsx(path, "新上架商品", headers, rows)
        except Exception as exc:
            messagebox.showerror("导出失败", str(exc), parent=self)
            return
        self.window.set_status(f"已导出：{Path(path).name}")


class NoTrafficPage(BasePage):
    title = "清理无流量"
    subtitle = "上架时间 · 流量 · 安全清理"

    def __init__(self, parent: tk.Widget, window: "MainWindow"):
        super().__init__(parent, window)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(4, weight=1)
        self._stores: dict[str, object] = {}
        self._rows: dict[str, dict] = {}
        self.store_var = tk.StringVar()
        self.preserve_stock_var = tk.BooleanVar(value=False)
        self.preserve_sales_var = tk.BooleanVar(value=True)
        self.method_var = tk.StringVar(value="recycle")
        self.max_count_var = tk.StringVar(value="0")
        self.age_days_var = tk.StringVar(value="15")
        self.max_views_var = tk.StringVar(value="1")
        self.max_clicks_var = tk.StringVar(value="1")
        self.max_orders_var = tk.StringVar(value="1")
        self.max_paid_var = tk.StringVar(value="0")
        self.state_var = tk.StringVar(
            value="准备就绪，填写条件后点“扫描预览”。"
        )
        self.configured_var = tk.StringVar(value="已配置凭证 0/0")

        top = ttk.Frame(self, style="TFrame")
        top.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ttk.Label(top, text="店铺").pack(side="left")
        self.store_combo = ttk.Combobox(
            top,
            textvariable=self.store_var,
            state="readonly",
            width=20,
        )
        self.store_combo.pack(side="left", padx=(5, 12))
        tk.Label(
            top,
            textvariable=self.configured_var,
            bg=SOFT_TEAL,
            fg=GREEN,
            padx=10,
            pady=4,
            font=("Microsoft YaHei UI", 8, "bold"),
        ).pack(side="left", padx=(0, 14))
        ttk.Label(top, text="上架超过").pack(side="left")
        ttk.Spinbox(
            top,
            textvariable=self.age_days_var,
            from_=1,
            to=3650,
            width=7,
        ).pack(side="left", padx=4)
        ttk.Label(top, text="天").pack(side="left")
        ttk.Label(top, text="近30天曝光<").pack(side="left", padx=(12, 4))
        ttk.Entry(top, textvariable=self.max_views_var, width=7).pack(side="left")
        ttk.Label(top, text="近30天点击<").pack(side="left", padx=(10, 4))
        ttk.Entry(top, textvariable=self.max_clicks_var, width=7).pack(side="left")
        ttk.Label(top, text="本次最多清理").pack(side="left", padx=(14, 4))
        ttk.Spinbox(
            top,
            textvariable=self.max_count_var,
            from_=0,
            to=100000,
            width=8,
        ).pack(side="left")
        ttk.Label(top, text="个（0 = 不限）").pack(side="left")

        options = ttk.Frame(self, style="TFrame")
        options.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        ttk.Label(options, text="保留条件").pack(side="left")
        tk.Checkbutton(
            options,
            text="保留店铺有历史销量的商品",
            variable=self.preserve_sales_var,
            bg="#f4f2eb",
            fg=TEXT,
            activebackground="#f4f2eb",
            selectcolor="white",
            bd=0,
        ).pack(side="left", padx=6)
        ttk.Label(options, text="处理方式").pack(side="left", padx=(16, 4))
        for value, label in (
            ("delete", "彻底删除"),
            ("recycle", "删除到回收站"),
            ("downlist", "下架"),
        ):
            tk.Radiobutton(
                options,
                text=label,
                value=value,
                variable=self.method_var,
                bg="#f4f2eb",
                fg=TEXT,
                activebackground="#f4f2eb",
                selectcolor="white",
                bd=0,
                command=self._update_state,
            ).pack(side="left", padx=5)

        conditions = ttk.Frame(self, style="TFrame")
        conditions.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        ttk.Label(conditions, text="成交订单小于").pack(side="left")
        ttk.Entry(
            conditions, textvariable=self.max_orders_var, width=7
        ).pack(side="left", padx=(4, 4))
        ttk.Label(conditions, text="单").pack(side="left")
        ttk.Label(conditions, text="支付金额小于").pack(
            side="left", padx=(14, 4)
        )
        ttk.Entry(
            conditions, textvariable=self.max_paid_var, width=8
        ).pack(side="left")
        ttk.Label(conditions, text="元（0 = 不限制）").pack(
            side="left", padx=(4, 0)
        )

        actions = ttk.Frame(self, style="TFrame")
        actions.grid(row=3, column=0, sticky="ew", pady=(0, 8))
        button(actions, "扫描预览", self._scan, "Primary").pack(side="left")
        button(actions, "同步经营指标", self._sync_metrics).pack(side="left", padx=6)
        self.no_traffic_select_all_button = button(
            actions,
            "全选",
            self._select_all,
        )
        self.no_traffic_select_all_button.pack(side="left", padx=6)
        self.no_traffic_select_none_button = button(
            actions,
            "清空勾选",
            self._select_none,
        )
        self.no_traffic_select_none_button.pack(side="left")
        self.no_traffic_execute_button = button(
            actions,
            "执行清理",
            self._execute,
            "Danger",
        )
        self.no_traffic_execute_button.pack(side="left", padx=8)
        ttk.Label(
            actions,
            textvariable=self.state_var,
            style="Subtitle.TLabel",
        ).pack(side="left", padx=12)

        self.progress_var = tk.DoubleVar(value=0)
        self.progress = ttk.Progressbar(self, variable=self.progress_var, maximum=100)
        self.progress.grid(row=4, column=0, sticky="ew", pady=(0, 8))

        body = tk.Frame(self, bg=PANEL, highlightbackground=BORDER, highlightthickness=1)
        body.grid(row=5, column=0, sticky="nsew")
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=1)
        columns = ("store", "id", "title", "listed", "sold", "orders", "paid", "exposure", "clicks")
        self.tree = ttk.Treeview(
            body,
            columns=columns,
            show="headings",
            selectmode="extended",
        )
        for key, text, width in (
            ("store", "店铺", 130),
            ("id", "商品ID", 150),
            ("title", "商品标题", 360),
            ("listed", "上架时间", 105),
            ("sold", "销量", 75),
            ("orders", "成交订单", 85),
            ("paid", "支付金额", 95),
            ("exposure", "近30天曝光", 105),
            ("clicks", "近30天点击", 105),
        ):
            self.tree.heading(key, text=text)
            self.tree.column(key, width=width, anchor="w")
        self.tree.grid(row=0, column=0, sticky="nsew")
        ttk.Scrollbar(body, orient="vertical", command=self.tree.yview).grid(
            row=0,
            column=1,
            sticky="ns",
        )
        self.tree.bind("<<TreeviewSelect>>", lambda _event: self._update_state())
        self.no_traffic_empty_label = tk.Label(
            body,
            text=(
                "还没有扫描结果\n\n"
                "填写条件后点“扫描预览”；结果只用于预览，"
                "不会自动删除任何商品。"
            ),
            bg=PANEL,
            fg=MUTED,
            justify="center",
            font=("Microsoft YaHei UI", 10),
        )

    def refresh(self) -> None:
        self._stores = {"全部店铺": None, **{
            f"{store.name} · #{store.id}": store
            for store in self.db.list_stores()
        }}
        values = list(self._stores)
        self.store_combo["values"] = values
        if self.store_var.get() not in values:
            self.store_var.set(values[0] if values else "")
        configured = sum(1 for store in self.db.list_stores() if store.app_id)
        self.configured_var.set(
            f"已配置凭证 {configured}/{len(self.db.list_stores())}"
        )
        self.tree.delete(*self.tree.get_children())
        self._rows.clear()
        if hasattr(self, "no_traffic_empty_label"):
            self.no_traffic_empty_label.place(
                relx=0.5, rely=0.48, anchor="center"
            )
        self._update_state()

    def _selected_store(self):
        return self._stores.get(self.store_var.get())

    @staticmethod
    def _raw(row: dict) -> dict:
        try:
            payload = json.loads(row.get("raw_json") or "{}")
        except json.JSONDecodeError:
            return {}
        return payload if isinstance(payload, dict) else {}

    @classmethod
    def _listed_at(cls, row: dict) -> int:
        raw = cls._raw(row)
        for key in ("listing_time", "onsale_time", "create_time", "edit_time"):
            try:
                value = int(raw.get(key) or 0)
            except (TypeError, ValueError):
                value = 0
            if value:
                return value
        return 0

    def _scan(self) -> None:
        store = self._selected_store()
        stores = [store] if store is not None else list(self.db.list_stores())
        if not stores:
            return
        try:
            age_days = max(1, int(self.age_days_var.get()))
            max_count = max(0, int(self.max_count_var.get() or 0))
            max_views = max(0, int(self.max_views_var.get() or 0))
            max_clicks = max(0, int(self.max_clicks_var.get() or 0))
            max_orders = max(0, int(self.max_orders_var.get() or 0))
            max_paid = max(0, int(float(self.max_paid_var.get() or 0) * 100))
        except ValueError:
            messagebox.showwarning(
                "清理条件",
                "上架天数、订单数、金额和数量必须填写数字。",
                parent=self,
            )
            return
        if (max_views or max_clicks or max_orders or max_paid) and not any(
            self.db.get_product_metrics_map(current_store.id)
            for current_store in stores
        ):
            messagebox.showinfo(
                "缺少经营指标",
                "请先点击“同步经营指标”，否则无法按成交、金额、浏览量和点击量筛选。",
                parent=self,
            )
            return
        candidates = []
        for current_store in stores:
            metrics_map = self.db.get_product_metrics_map(current_store.id)
            for row in self.db.list_products(
                store_id=current_store.id,
                status=5,
            ):
                raw = self._raw(row)
                sold = int(
                    raw.get("total_sold_num")
                    or raw.get("sold_num")
                    or 0
                )
                metrics = metrics_map.get(str(row["product_id"]))
                if (
                    max_views
                    or max_clicks
                    or max_orders
                    or max_paid
                ) and metrics is None:
                    continue
                views = int((metrics or {}).get("views30") or 0)
                clicks = int((metrics or {}).get("click_cnt") or 0)
                orders = int((metrics or {}).get("pay_cnt") or 0)
                paid = int((metrics or {}).get("pay_gmv") or 0)
                if max_views and views >= max_views:
                    continue
                if max_clicks and clicks >= max_clicks:
                    continue
                if max_orders and orders >= max_orders:
                    continue
                if max_paid and paid >= max_paid:
                    continue
                listed_at = self._listed_at(row)
                if (
                    listed_at
                    and (date.today() - date.fromtimestamp(listed_at)).days
                    < age_days
                ):
                    continue
                if (
                    self.preserve_stock_var.get()
                    and int(row["stock_total"] or 0) > 0
                ):
                    continue
                if self.preserve_sales_var.get() and sold > 0:
                    continue
                candidates.append(
                    (
                        row,
                        listed_at,
                        sold,
                        views,
                        clicks,
                        metrics or {},
                    )
                )
        candidates.sort(key=lambda item: item[1] or 0)
        if max_count:
            candidates = candidates[:max_count]
        self.tree.delete(*self.tree.get_children())
        self._rows.clear()
        for index, (row, listed_at, sold, views, clicks, metrics) in enumerate(candidates):
            iid = str(index)
            self._rows[iid] = row
            self.tree.insert(
                "",
                "end",
                iid=iid,
                values=(
                    row["store_name"],
                    row["product_id"],
                    row["title"],
                    date.fromtimestamp(listed_at).isoformat() if listed_at else "",
                    sold,
                    int(metrics.get("pay_cnt") or 0),
                    f"¥{int(metrics.get('pay_gmv') or 0) / 100:,.2f}",
                    views,
                    clicks,
                ),
            )
        if candidates:
            self.no_traffic_empty_label.place_forget()
        else:
            self.no_traffic_empty_label.place(
                relx=0.5, rely=0.48, anchor="center"
            )
        self._update_state()

    def _sync_metrics(self) -> None:
        store = self._selected_store()
        stores = [store] if store is not None else list(self.db.list_stores())
        if not stores:
            return
        self.window.set_status("正在同步商品经营指标…", busy=True)

        def work(progress):
            results = []
            for index, current_store in enumerate(stores, 1):
                if progress:
                    progress(
                        f"同步经营指标：{current_store.name}",
                        index,
                        len(stores),
                    )
                product_ids = [
                    str(row["product_id"])
                    for row in self.db.list_products(
                        store_id=current_store.id,
                        status=5,
                    )
                ]
                results.append(
                    self.window.service.sync_product_performance(
                        current_store,
                        product_ids,
                        days=30,
                    )
                )
            return results

        self.window.run_task(
            work,
            lambda results: (
                self._scan(),
                self.window.set_status(
                    f"经营指标同步完成：{len(results)} 家店铺"
                ),
            ),
            lambda error: messagebox.showerror(
                "商品经营指标同步失败",
                error,
                parent=self,
            ),
            log_title="同步商品经营指标",
        )

    def _select_all(self) -> None:
        self.tree.selection_set(tuple(self._rows))
        self._update_state()

    def _select_none(self) -> None:
        self.tree.selection_remove(*self.tree.selection())
        self._update_state()

    def _update_state(self) -> None:
        action_label = {
            "delete": "执行彻底删除",
            "recycle": "执行删除到回收站",
            "downlist": "执行下架",
        }[self.method_var.get()]
        self.state_var.set(
            f"已选 {len(self.tree.selection())} / {len(self._rows)}"
        )
        self.no_traffic_execute_button.configure(text=action_label)
        self.no_traffic_select_all_button.configure(
            state="normal" if self._rows else "disabled"
        )
        self.no_traffic_select_none_button.configure(
            state="normal" if self.tree.selection() else "disabled"
        )
        self.no_traffic_execute_button.configure(
            state="normal" if self.tree.selection() else "disabled"
        )

    def _execute(self) -> None:
        rows = [
            self._rows[iid]
            for iid in self.tree.selection()
            if iid in self._rows
        ]
        if not rows:
            messagebox.showinfo("请选择商品", "请先扫描并选择需要处理的商品。", parent=self)
            return
        blacklist = self.db.violation_blacklist_ids()
        blocked = [
            str(row["product_id"])
            for row in rows
            if str(row["product_id"]) in blacklist
        ]
        if blocked:
            messagebox.showwarning(
                "违规黑名单拦截",
                f"已跳过 {len(blocked)} 个黑名单商品：\n"
                + "\n".join(blocked[:12]),
                parent=self,
            )
            rows = [
                row
                for row in rows
                if str(row["product_id"]) not in blacklist
            ]
        if not rows:
            return
        method = self.method_var.get()
        label = {
            "delete": "彻底删除",
            "recycle": "移入回收站",
            "downlist": "下架",
        }[method]
        if not messagebox.askyesno(
            "执行清理",
            f"即将对 {len(rows)} 个商品执行{label}。\n"
            "这是正式平台操作，是否继续？",
            parent=self,
        ):
            return
        by_store: dict[int, list[str]] = {}
        for row in rows:
            by_store.setdefault(int(row["store_id"]), []).append(
                str(row["product_id"])
            )
        self.window.set_status(f"正在{label}…", busy=True)

        way = {
            "delete": "real",
            "recycle": "recycle",
            "downlist": "offshelf",
        }[method]

        def work(progress):
            results = []
            for index, (store_id, product_ids) in enumerate(
                by_store.items(), 1
            ):
                store = self.db.get_store(store_id)
                if store is None:
                    continue
                if progress:
                    progress(
                        f"{label}：{store.name}",
                        index,
                        len(by_store),
                    )
                results.append(
                    self.window.service.private_batch_product_action(
                        store,
                        product_ids,
                        way,
                    )
                )
            return results

        self.window.run_task(
            work,
            lambda results: (
                self.window.set_status(f"{label}完成"),
                self._scan(),
                messagebox.showinfo(
                    label,
                    f"已处理 {len(by_store)} 家店铺、{len(rows)} 个商品。",
                    parent=self,
                ),
            ),
            lambda error: messagebox.showerror(f"{label}失败", error, parent=self),
            self._progress,
            log_title=f"商品批量操作：{label}",
        )

    def _progress(self, message: str, current: int, total: int) -> None:
        self.window.set_status(message, busy=True)
        self.progress_var.set((current / total * 100) if total else 0)


class HostedOperationsPage(BasePage):
    title = "托管中心"
    subtitle = "动作配置 · 并行执行 · 每日定时"

    def __init__(self, parent: tk.Widget, window: "MainWindow"):
        super().__init__(parent, window)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)
        self._rows: dict[str, dict] = {}
        self.timed_enabled = (
            self.db.get_setting("hosted_schedule_enabled", "0") == "1"
        )
        self.schedule_time_var = tk.StringVar(
            value=self.db.get_setting("hosted_schedule_time", "09:30")
        )
        self.schedule_store_ids = _json_list_setting(
            self.db.get_setting("hosted_schedule_stores", "[]")
        )
        if not self.schedule_store_ids:
            self.schedule_store_ids = {
                store.id for store in self.db.list_stores()
            }
        self._timed_after_id: str | None = None
        self.state_var = tk.StringVar(value="还没有店铺配置")
        self.store_filter_var = tk.StringVar(value="全部店铺")
        self.last_run_var = tk.StringVar(value="上次执行：尚未执行")

        intro = ttk.Frame(self, style="TFrame")
        intro.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ttk.Label(
            intro,
            textvariable=self.state_var,
            style="Subtitle.TLabel",
        ).pack(side="left")
        ttk.Label(
            intro,
            text="最多 5 家店铺并行，单店动作严格顺序执行。",
            style="Subtitle.TLabel",
        ).pack(side="left", padx=14)
        button(intro, "刷新", self.refresh).pack(side="right")
        button(intro, "一键配置", self._configure_all).pack(side="right", padx=6)
        button(
            intro,
            "批量配置选中",
            self._configure_bulk_selected,
        ).pack(side="right", padx=6)
        self.execute_configured_button = button(
            intro,
            "执行全部已配置",
            self._execute_configured,
        )
        self.execute_configured_button.pack(side="right", padx=6)
        self.execute_all_button = button(
            intro,
            "执行选中店铺",
            self._execute_all,
            "Primary",
        )
        self.execute_all_button.pack(side="right", padx=6)

        selector = ttk.Frame(self, style="TFrame")
        selector.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        ttk.Label(selector, text="选择执行店铺").pack(side="left")
        self.store_filter_combo = ttk.Combobox(
            selector,
            textvariable=self.store_filter_var,
            state="readonly",
            width=24,
        )
        self.store_filter_combo.pack(side="left", padx=(6, 12))
        self.store_filter_combo.bind(
            "<<ComboboxSelected>>", lambda _event: self.refresh()
        )
        self.select_all_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            selector,
            text="全选托管店铺",
            variable=self.select_all_var,
            command=self._toggle_select_all,
            bg="#f4f2eb",
            fg=TEXT,
            activebackground="#f4f2eb",
            selectcolor="white",
            bd=0,
        ).pack(side="left")
        ttk.Label(
            selector,
            text="执行前会再次检查店铺扫码登录态",
            style="Subtitle.TLabel",
        ).pack(side="right")

        content = ttk.Frame(self, style="TFrame")
        content.grid(row=2, column=0, sticky="nsew")
        content.columnconfigure(0, weight=1)
        content.rowconfigure(0, weight=1)
        body = tk.Frame(
            content,
            bg=PANEL,
            highlightbackground=BORDER,
            highlightthickness=1,
        )
        body.grid(row=0, column=0, sticky="nsew")
        body.rowconfigure(1, weight=1)
        body.columnconfigure(0, weight=1)
        tk.Label(
            body,
            text="店铺托管配置",
            bg=PANEL,
            fg=TEXT,
            font=("Microsoft YaHei UI", 11, "bold"),
        ).grid(row=0, column=0, sticky="w", padx=12, pady=9)
        columns = ("store", "actions", "listing_mode", "listing_count", "config")
        self.tree = ttk.Treeview(body, columns=columns, show="headings")
        for key, text, width in (
            ("store", "店铺", 190),
            ("actions", "启用动作", 280),
            ("listing_mode", "上架方式", 140),
            ("listing_count", "上架数量", 100),
            ("config", "配置", 90),
        ):
            self.tree.heading(key, text=text)
            self.tree.column(
                key,
                width=width,
                anchor="center" if key != "store" else "w",
            )
        self.tree.grid(row=1, column=0, sticky="nsew")
        ttk.Scrollbar(body, orient="vertical", command=self.tree.yview).grid(
            row=1,
            column=1,
            sticky="ns",
        )
        self.tree.bind("<Double-1>", lambda _event: self._configure_selected())
        self.tree.bind("<Button-3>", self._show_context_menu)
        self.tree.bind("<<TreeviewSelect>>", self._update_execute_state)
        if self.timed_enabled:
            self._schedule_timed_tick(1000)

        schedule = ttk.Frame(
            content,
            style="Panel.TFrame",
            padding=14,
        )
        schedule.grid(row=0, column=1, sticky="ns", padx=(10, 0))
        ttk.Label(
            schedule, text="定时执行", style="Section.TLabel"
        ).pack(anchor="w")
        ttk.Label(
            schedule,
            text="到点后同一天只执行一次",
            style="Panel.TLabel",
            foreground=MUTED,
        ).pack(anchor="w", pady=(2, 10))
        self.schedule_enabled_var = tk.BooleanVar(value=self.timed_enabled)
        tk.Checkbutton(
            schedule,
            text="启用每日定时执行",
            variable=self.schedule_enabled_var,
            bg=PANEL,
            fg=TEXT,
            activebackground=PANEL,
            selectcolor="white",
            bd=0,
        ).pack(anchor="w", pady=(0, 8))
        ttk.Label(
            schedule, text="执行时间", style="Panel.TLabel"
        ).pack(anchor="w")
        ttk.Entry(
            schedule,
            textvariable=self.schedule_time_var,
            width=10,
        ).pack(anchor="w", pady=(3, 8))
        ttk.Label(
            schedule, text="定时执行的店铺", style="Panel.TLabel"
        ).pack(anchor="w")
        self.schedule_store_box = tk.Frame(schedule, bg=PANEL, height=180)
        self.schedule_store_box.pack(fill="x", pady=(3, 8))
        button(
            schedule,
            "保存定时设置",
            self._save_inline_schedule,
            "Primary",
        ).pack(fill="x")
        ttk.Label(
            schedule,
            textvariable=self.last_run_var,
            style="Panel.TLabel",
            foreground=MUTED,
        ).pack(anchor="w", pady=(8, 0))

    def refresh(self) -> None:
        stores = self.db.list_stores()
        names = [f"全部 {len(stores)} 家店铺", *[
            f"{store.name} · #{store.id}" for store in stores
        ]]
        self.store_filter_combo["values"] = names
        if self.store_filter_var.get() not in names:
            self.store_filter_var.set(names[0] if names else "")
        selected_filter = next(
            (
                store
                for store in stores
                if self.store_filter_var.get()
                == f"{store.name} · #{store.id}"
            ),
            None,
        )
        self.tree.delete(*self.tree.get_children())
        self._rows.clear()
        configured = 0
        enabled = 0
        for index, store in enumerate(stores):
            stored_config = self.db.get_hosted_config(store.id)
            config = dict(stored_config or {})
            config["listing_mode"] = self.db.get_setting(
                f"hosted_listing_mode_{store.id}", "manual"
            )
            try:
                config["listing_count"] = int(
                    self.db.get_setting(
                        f"hosted_listing_count_{store.id}", "200"
                    )
                )
            except ValueError:
                config["listing_count"] = 200
            if stored_config:
                configured += 1
            if config.get("enabled"):
                enabled += 1
            if selected_filter is not None and selected_filter.id != store.id:
                continue
            iid = str(index)
            self._rows[iid] = {"store": store, "config": config}
            action_names = []
            if config.get("revoke_audit"):
                action_names.append("撤销审核")
            if config.get("delete_audit_failed"):
                action_names.append("删审核失败")
            if config.get("list_products"):
                action_names.append("上架商品")
            if config.get("prefill_11"):
                action_names.append("双11预填报")
            if config.get("delete_no_traffic"):
                action_names.append("清理无流量")
            self.tree.insert(
                "",
                "end",
                iid=iid,
                values=(
                    store.name,
                    (
                        "、".join(action_names)
                        if action_names
                        else "未配置"
                    ),
                    "模拟手工上架"
                    if config.get("listing_mode") != "api"
                    else "API 上架",
                    config.get("listing_count", 200),
                    "配置",
                ),
            )
        self.state_var.set(
            f"{len(stores)} 家店铺 · {configured} 家已配置 · "
            f"{enabled} 家已启用 · 定时"
            + (
                f" {self.schedule_time_var.get()}"
                if self.timed_enabled
                else "关闭"
            )
        )
        for child in self.schedule_store_box.winfo_children():
            child.destroy()
        self._schedule_store_vars: dict[int, tk.BooleanVar] = {}
        for store in stores:
            variable = tk.BooleanVar(
                value=store.id in self.schedule_store_ids
            )
            self._schedule_store_vars[store.id] = variable
            tk.Checkbutton(
                self.schedule_store_box,
                text=store.name,
                variable=variable,
                anchor="w",
                bg=PANEL,
                fg=TEXT,
                activebackground=PANEL,
                selectcolor="white",
                bd=0,
            ).pack(fill="x", anchor="w")
        last_run = self.db.get_setting("hosted_last_run_date", "")
        self.last_run_var.set(
            f"上次执行：{last_run}" if last_run else "上次执行：尚未执行"
        )
        self._update_execute_state()
        self.execute_configured_button.configure(
            state="normal" if enabled else "disabled"
        )

    def _toggle_select_all(self) -> None:
        if self.select_all_var.get():
            self.tree.selection_set(tuple(self.tree.get_children()))
        else:
            self.tree.selection_remove(*self.tree.selection())
        self._update_execute_state()

    def _update_execute_state(self, _event=None) -> None:
        has_selection = bool(self.tree.selection())
        self.execute_all_button.configure(
            state="normal" if has_selection else "disabled"
        )
        if not has_selection:
            self.select_all_var.set(False)
        elif len(self.tree.selection()) == len(self.tree.get_children()):
            self.select_all_var.set(True)

    def _save_inline_schedule(self) -> None:
        value = self.schedule_time_var.get().strip()
        try:
            datetime.strptime(value, "%H:%M")
        except ValueError:
            messagebox.showwarning(
                "时间格式",
                "执行时间必须使用 HH:MM。",
                parent=self,
            )
            return
        chosen = {
            store_id
            for store_id, variable in getattr(
                self, "_schedule_store_vars", {}
            ).items()
            if variable.get()
        }
        self.timed_enabled = self.schedule_enabled_var.get()
        self.schedule_time_var.set(value)
        self.schedule_store_ids = chosen
        self.db.set_setting(
            "hosted_schedule_enabled",
            "1" if self.timed_enabled else "0",
        )
        self.db.set_setting("hosted_schedule_time", value)
        self.db.set_setting(
            "hosted_schedule_stores",
            json.dumps(sorted(chosen)),
        )
        if self.timed_enabled:
            self._schedule_timed_tick(1000)
        elif self._timed_after_id is not None:
            self.after_cancel(self._timed_after_id)
            self._timed_after_id = None
        self.refresh()
        self.window.set_status("定时执行设置已保存")

    def _selected(self):
        selection = self.tree.selection()
        return self._rows.get(selection[0]) if selection else None

    def _configure_selected(self) -> None:
        selected = self._selected()
        if selected is None:
            messagebox.showinfo("请选择店铺", "请先选择一家店铺。", parent=self)
            return
        self._open_hosted_config_dialog(
            [selected["store"]],
            selected["config"],
        )

    def _configure_bulk_selected(self) -> None:
        selected = [
            self._rows[iid]
            for iid in self.tree.selection()
            if iid in self._rows
        ]
        if not selected:
            messagebox.showinfo(
                "请选择店铺",
                "请先选择需要批量配置的店铺。",
                parent=self,
            )
            return
        self._open_hosted_config_dialog(
            [row["store"] for row in selected],
            selected[0]["config"],
        )

    def _open_hosted_config_dialog(
        self,
        stores: list[Store],
        config: dict[str, Any],
    ) -> None:
        if not stores:
            return
        suffix = (
            f"{stores[0].name}"
            if len(stores) == 1
            else f"{len(stores)} 家店铺"
        )
        dialog = tk.Toplevel(self)
        dialog.title(f"托管配置 · {suffix}")
        dialog.transient(self.winfo_toplevel())
        dialog.geometry("760x640")
        dialog.minsize(680, 560)
        dialog.columnconfigure(0, weight=1)
        dialog.rowconfigure(1, weight=1)
        header = ttk.Frame(dialog, padding=(16, 12, 16, 4))
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)
        ttk.Label(
            header,
            text=f"配置对象：{suffix}",
            style="Section.TLabel",
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(
            header,
            text="勾选多个动作后，执行时按单店顺序运行；批量保存会覆盖所选店铺原配置。",
            style="Subtitle.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(3, 0))
        enabled_var = tk.BooleanVar(value=bool(config.get("enabled")))
        tk.Checkbutton(
            header,
            text="启用该配置",
            variable=enabled_var,
            bg=PANEL,
            fg=TEXT,
            activebackground=PANEL,
            selectcolor="white",
            bd=0,
        ).grid(row=0, column=1, rowspan=2, sticky="e")

        notebook = ttk.Notebook(dialog)
        notebook.grid(
            row=1,
            column=0,
            sticky="nsew",
            padx=16,
            pady=(4, 0),
        )
        variables = {
            "revoke_audit": tk.BooleanVar(value=bool(config.get("revoke_audit"))),
            "delete_audit_failed": tk.BooleanVar(
                value=bool(config.get("delete_audit_failed"))
            ),
            "list_products": tk.BooleanVar(value=bool(config.get("list_products"))),
            "prefill_11": tk.BooleanVar(value=bool(config.get("prefill_11"))),
            "delete_no_traffic": tk.BooleanVar(
                value=bool(config.get("delete_no_traffic"))
            ),
        }
        actions_page = ttk.Frame(notebook, padding=14)
        notebook.add(actions_page, text="运营动作")
        ttk.Label(
            actions_page,
            text="可同时选择多个动作，执行顺序固定，删除操作放在最后。",
            style="Subtitle.TLabel",
        ).pack(anchor="w", pady=(0, 10))
        for key, label in (
            ("revoke_audit", "撤销审核中的商品"),
            ("delete_audit_failed", "删除审核失败商品"),
            ("list_products", "上架已下架商品"),
            ("prefill_11", "双11活动一键预填报"),
            ("delete_no_traffic", "清理无流量商品"),
        ):
            tk.Checkbutton(
                actions_page,
                text=label,
                variable=variables[key],
                anchor="w",
                bg=PANEL,
                fg=TEXT,
                activebackground=PANEL,
                selectcolor="white",
                bd=0,
            ).pack(fill="x", pady=6)

        listing_page = ttk.Frame(notebook, padding=16)
        notebook.add(listing_page, text="上架设置")
        listing_page.columnconfigure(1, weight=1)
        ttk.Label(
            listing_page,
            text="上架方式",
            style="Panel.TLabel",
        ).grid(row=0, column=0, sticky="w", pady=6)
        listing_mode_var = tk.StringVar(
            value=(
                "API 上架"
                if config.get("listing_mode") == "api"
                else "模拟手工上架"
            )
        )
        listing_combo = ttk.Combobox(
            listing_page,
            textvariable=listing_mode_var,
            values=("模拟手工上架", "API 上架"),
            state="readonly",
            width=20,
        )
        listing_combo.grid(row=0, column=1, sticky="w", padx=(10, 0), pady=6)
        ttk.Label(
            listing_page,
            text="单次上架数量",
            style="Panel.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=6)
        listing_count_var = tk.StringVar(
            value=str(config.get("listing_count") or 200)
        )
        ttk.Spinbox(
            listing_page,
            textvariable=listing_count_var,
            from_=1,
            to=200,
            width=12,
        ).grid(row=1, column=1, sticky="w", padx=(10, 0), pady=6)
        ttk.Label(
            listing_page,
            text=(
                "模拟手工上架按设定数量执行；API 上架使用官方接口，"
                "遇到额度或权限不足会记录失败并继续后续动作。"
            ),
            style="Panel.TLabel",
            foreground=MUTED,
            wraplength=560,
            justify="left",
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(14, 0))

        cleanup_page = ttk.Frame(notebook, padding=16)
        notebook.add(cleanup_page, text="无流量清理")
        cleanup_page.columnconfigure(1, weight=1)
        ttk.Label(
            cleanup_page,
            text="无流量天数",
            style="Panel.TLabel",
        ).grid(row=0, column=0, sticky="w", pady=6)
        no_traffic_days_var = tk.StringVar(
            value=str(config.get("no_traffic_days") or 7)
        )
        ttk.Spinbox(
            cleanup_page,
            textvariable=no_traffic_days_var,
            from_=1,
            to=365,
            width=12,
        ).grid(row=0, column=1, sticky="w", padx=(10, 0), pady=6)
        ttk.Label(
            cleanup_page,
            text="最高销量",
            style="Panel.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=6)
        no_traffic_sales_var = tk.StringVar(
            value=str(config.get("no_traffic_max_sales") or 0)
        )
        ttk.Spinbox(
            cleanup_page,
            textvariable=no_traffic_sales_var,
            from_=0,
            to=1000000,
            width=12,
        ).grid(row=1, column=1, sticky="w", padx=(10, 0), pady=6)
        ttk.Label(
            cleanup_page,
            text="处理方式",
            style="Panel.TLabel",
        ).grid(row=2, column=0, sticky="w", pady=6)
        no_traffic_method_var = tk.StringVar(
            value=(
                "彻底删除"
                if config.get("no_traffic_method") == "real"
                else (
                    "下架"
                    if config.get("no_traffic_method") == "offshelf"
                    else "移入回收站"
                )
            )
        )
        ttk.Combobox(
            cleanup_page,
            textvariable=no_traffic_method_var,
            values=("移入回收站", "下架", "彻底删除"),
            state="readonly",
            width=20,
        ).grid(row=2, column=1, sticky="w", padx=(10, 0), pady=6)
        ttk.Label(
            cleanup_page,
            text=(
                "只处理有经营数据、已上架且超过设定天数、"
                "没有浏览量/点击量、销量不超过上限的商品。"
                "没有经营数据的商品不会被删除。"
            ),
            style="Panel.TLabel",
            foreground=MUTED,
            wraplength=560,
            justify="left",
        ).grid(row=3, column=0, columnspan=2, sticky="w", pady=(14, 0))

        actions = ttk.Frame(dialog, padding=(16, 12))
        actions.grid(row=2, column=0, sticky="ew")
        actions.columnconfigure(0, weight=1)
        button(actions, "取消", dialog.destroy).grid(
            row=0,
            column=1,
            padx=(0, 8),
        )

        def save() -> None:
            try:
                listing_count = min(
                    200, max(1, int(listing_count_var.get()))
                )
                no_traffic_days = min(
                    365, max(1, int(no_traffic_days_var.get()))
                )
                no_traffic_sales = max(
                    0, int(no_traffic_sales_var.get())
                )
            except ValueError:
                messagebox.showwarning(
                    "配置数值",
                    "上架数量、无流量天数和最高销量必须是整数。",
                    parent=dialog,
                )
                return
            listing_mode = (
                "api"
                if listing_mode_var.get() == "API 上架"
                else "manual"
            )
            no_traffic_method = {
                "移入回收站": "recycle",
                "下架": "offshelf",
                "彻底删除": "real",
            }[no_traffic_method_var.get()]
            for store in stores:
                self.db.update_hosted_config(
                    store.id,
                    **{
                        key: variable.get()
                        for key, variable in variables.items()
                    },
                    enabled=enabled_var.get(),
                    no_traffic_days=no_traffic_days,
                    no_traffic_max_sales=no_traffic_sales,
                    no_traffic_method=no_traffic_method,
                )
                self.db.set_setting(
                    f"hosted_listing_mode_{store.id}",
                    listing_mode,
                )
                self.db.set_setting(
                    f"hosted_listing_count_{store.id}",
                    str(listing_count),
                )
            dialog.destroy()
            self.refresh()
            self.window.set_status(
                f"已保存 {len(stores)} 家店铺的托管配置"
            )

        button(actions, "保存配置", save, "Primary").grid(
            row=0,
            column=2,
        )
        dialog.bind("<Escape>", lambda _event: dialog.destroy())
        dialog.grab_set()

    def _configure_all(self) -> None:
        stores = self.db.list_stores()
        if not stores:
            return
        first = self.db.get_hosted_config(stores[0].id) or {}
        self._open_hosted_config_dialog(stores, first)

    def _execute_all(self) -> None:
        rows = [
            self._rows[iid]
            for iid in self.tree.selection()
            if iid in self._rows
        ]
        if not rows:
            messagebox.showinfo(
                "请选择店铺",
                "请先勾选至少一家托管店铺。",
                parent=self,
            )
            return
        self._execute_rows(rows, scheduled=False)

    def _execute_configured(self) -> None:
        rows: list[dict[str, Any]] = []
        for store in self.db.list_stores():
            config = dict(self.db.get_hosted_config(store.id) or {})
            config["listing_mode"] = self.db.get_setting(
                f"hosted_listing_mode_{store.id}",
                "manual",
            )
            try:
                config["listing_count"] = int(
                    self.db.get_setting(
                        f"hosted_listing_count_{store.id}",
                        "200",
                    )
                )
            except ValueError:
                config["listing_count"] = 200
            if config.get("enabled"):
                rows.append({"store": store, "config": config})
        if not rows:
            messagebox.showinfo(
                "没有启用配置",
                "请先为店铺保存并启用托管配置。",
                parent=self,
            )
            return
        self._execute_rows(rows, scheduled=False)

    def _execute_selected(self) -> None:
        selected = self._selected()
        if selected is None:
            messagebox.showinfo("请选择店铺", "请先选择一家店铺。", parent=self)
            return
        self._execute_rows([selected], scheduled=False)

    def _execute_rows(self, rows: list[dict], scheduled: bool) -> None:
        if not scheduled and not messagebox.askyesno(
            "执行托管",
            f"即将执行 {len(rows)} 家店铺已启用的托管项目。"
            "正式平台写操作执行前会再次检查。是否继续？",
            parent=self,
        ):
            return
        self.window.set_status("正在执行托管项目…", busy=True)

        def execute_one(item: dict) -> str:
            store = item["store"]
            config = item["config"]
            actions = []
            try:
                blacklist = self.db.violation_blacklist_ids()
                products = [
                    row
                    for row in self.db.list_products(store_id=store.id)
                    if str(row.get("product_id")) not in blacklist
                ]
                if config.get("revoke_audit"):
                    audit_ids = [
                        str(row["product_id"])
                        for row in products
                        if int(row.get("status") or 0) == 2
                    ][:500]
                    for product_id in audit_ids:
                        self.window.service.product_write(
                            store,
                            "cancel_audit",
                            product_id=product_id,
                        )
                    actions.append(f"撤销审核 {len(audit_ids)}")
                if config.get("delete_audit_failed"):
                    failed_ids = [
                        str(row["product_id"])
                        for row in products
                        if int(row.get("status") or 0) == 3
                    ][:500]
                    for product_id in failed_ids:
                        self.window.service.product_write(
                            store,
                            "delete",
                            product_id=product_id,
                        )
                    actions.append(f"删除审核失败 {len(failed_ids)}")
                if config.get("list_products"):
                    listing_count = max(
                        1, int(config.get("listing_count") or 200)
                    )
                    listing_ids = [
                        str(row["product_id"])
                        for row in products
                        if int(row.get("status") or 0) == 11
                    ][:listing_count]
                    if listing_ids:
                        self.window.service.set_products_listing(
                            store,
                            listing_ids,
                            True,
                        )
                    actions.append(f"上架 {len(listing_ids)}")
                if config.get("delete_no_traffic"):
                    days = max(1, int(config.get("no_traffic_days") or 7))
                    max_sales = max(
                        0,
                        int(config.get("no_traffic_max_sales") or 0),
                    )
                    method = str(
                        config.get("no_traffic_method") or "recycle"
                    )
                    no_traffic_ids = _hosted_no_traffic_product_ids(
                        products,
                        self.db.get_product_metrics_map(store.id),
                        days=days,
                        max_sales=max_sales,
                    )
                    if no_traffic_ids:
                        self.window.service.private_batch_product_action(
                            store,
                            no_traffic_ids,
                            method,
                        )
                    actions.append(
                        f"清理无流量 {len(no_traffic_ids)}"
                    )
                if config.get("prefill_11"):
                    try:
                        drafts = json.loads(
                            self.db.get_setting(
                                f"double11_prefill_{store.id}",
                                "[]",
                            )
                        )
                    except json.JSONDecodeError:
                        drafts = []
                    if isinstance(drafts, list) and drafts:
                        self.window.service.double11_submit(store, drafts)
                        actions.append(f"双11预填报 {len(drafts)}")
                    else:
                        actions.append("双11预填报：无草稿")
                self.db.log_hosted_run(
                    store.id,
                    "、".join(actions) or "无动作",
                    "success",
                    "定时执行" if scheduled else "手动执行",
                )
                return ""
            except Exception as exc:
                self.db.log_hosted_run(
                    store.id,
                    "、".join(actions) or "执行失败",
                    "failed",
                    str(exc),
                )
                return f"{store.name}: {exc}"

        def work(_progress) -> list[str]:
            workers = min(5, max(1, len(rows)))
            errors: list[str] = []
            with ThreadPoolExecutor(
                max_workers=workers,
                thread_name_prefix="hosted-store",
            ) as executor:
                futures = [executor.submit(execute_one, item) for item in rows]
                for future in as_completed(futures):
                    error = future.result()
                    if error:
                        errors.append(error)
            return errors

        def done(errors: list[str]) -> None:
            self.window.set_status(
                f"托管执行完成，成功 {len(rows) - len(errors)} 家"
            )
            self.refresh()
            if errors:
                messagebox.showwarning(
                    "部分托管执行失败",
                    "\n".join(errors[:8]),
                    parent=self,
                )

        self.window.run_task(
            work,
            done,
            lambda error: messagebox.showerror("托管执行失败", error, parent=self),
            log_title="执行托管店铺任务",
        )

    def _toggle_timed(self) -> None:
        stores = self.db.list_stores()
        dialog = tk.Toplevel(self)
        dialog.title("定时执行")
        dialog.transient(self.winfo_toplevel())
        dialog.resizable(False, False)
        panel = ttk.Frame(dialog, style="Panel.TFrame", padding=18)
        panel.pack(fill="both", expand=True)
        enabled = tk.BooleanVar(value=self.timed_enabled)
        execution_time = tk.StringVar(value=self.schedule_time_var.get())
        selected_ids = set(self.schedule_store_ids)
        tk.Checkbutton(
            panel,
            text="启用每日定时执行",
            variable=enabled,
            bg=PANEL,
            fg=TEXT,
            activebackground=PANEL,
            selectcolor="white",
            bd=0,
        ).pack(anchor="w", pady=(0, 10))
        time_row = ttk.Frame(panel, style="Panel.TFrame")
        time_row.pack(fill="x", pady=(0, 10))
        ttk.Label(
            time_row, text="执行时间", style="Panel.TLabel"
        ).pack(side="left")
        ttk.Entry(
            time_row, textvariable=execution_time, width=9
        ).pack(side="left", padx=8)
        ttk.Label(
            time_row,
            text="格式 HH:MM",
            style="Panel.TLabel",
            foreground=MUTED,
        ).pack(side="left")
        ttk.Label(
            panel, text="定时执行的店铺", style="Panel.TLabel"
        ).pack(anchor="w", pady=(4, 4))
        store_vars = {}
        for store in stores:
            variable = tk.BooleanVar(value=store.id in selected_ids)
            store_vars[store.id] = variable
            tk.Checkbutton(
                panel,
                text=store.name,
                variable=variable,
                bg=PANEL,
                fg=TEXT,
                activebackground=PANEL,
                selectcolor="white",
                bd=0,
            ).pack(anchor="w", pady=2)

        def save() -> None:
            value = execution_time.get().strip()
            try:
                datetime.strptime(value, "%H:%M")
            except ValueError:
                messagebox.showwarning(
                    "时间格式",
                    "执行时间必须使用 HH:MM。",
                    parent=dialog,
                )
                return
            chosen = {
                store_id
                for store_id, variable in store_vars.items()
                if variable.get()
            }
            self.timed_enabled = enabled.get()
            self.schedule_time_var.set(value)
            self.schedule_store_ids = chosen
            self.db.set_setting(
                "hosted_schedule_enabled",
                "1" if self.timed_enabled else "0",
            )
            self.db.set_setting("hosted_schedule_time", value)
            self.db.set_setting(
                "hosted_schedule_stores",
                json.dumps(sorted(chosen)),
            )
            dialog.destroy()
            self.refresh()
            if self.timed_enabled:
                self._schedule_timed_tick(1000)
            elif self._timed_after_id is not None:
                self.after_cancel(self._timed_after_id)
                self._timed_after_id = None

        actions = ttk.Frame(panel, style="Panel.TFrame")
        actions.pack(anchor="e", pady=(12, 0))
        button(actions, "取消", dialog.destroy).pack(side="left", padx=6)
        button(actions, "保存定时设置", save, "Primary").pack(side="left")
        dialog.grab_set()

    def _timed_tick(self) -> None:
        self._timed_after_id = None
        if not self.timed_enabled:
            return
        now = datetime.now()
        if now.strftime("%H:%M") != self.schedule_time_var.get():
            self._schedule_timed_tick(30000)
            return
        if self.db.get_setting("hosted_last_run_date", "") == (
            now.date().isoformat()
        ):
            self._schedule_timed_tick(30000)
            return
        rows = []
        for store_id in self.schedule_store_ids:
            store = self.db.get_store(store_id)
            if store is None:
                continue
            config = self.db.get_hosted_config(store_id) or {}
            if config.get("enabled"):
                rows.append({"store": store, "config": config})
        if rows:
            self.db.set_setting(
                "hosted_last_run_date", now.date().isoformat()
            )
            self._execute_rows(rows, scheduled=True)
        self._schedule_timed_tick(30000)

    def _schedule_timed_tick(self, delay: int) -> None:
        if not self.timed_enabled or self._timed_after_id is not None:
            return
        self._timed_after_id = self.after(delay, self._timed_tick)

    def _show_context_menu(self, event) -> None:
        iid = self.tree.identify_row(event.y)
        if iid:
            self.tree.selection_set(iid)
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label="配置店铺", command=self._configure_selected)
        menu.add_command(label="执行该店铺", command=self._execute_selected)
        menu.add_separator()
        menu.add_command(label="查看执行记录", command=self._show_runs)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _show_runs(self) -> None:
        rows = self.db.list_hosted_runs()
        if not rows:
            messagebox.showinfo("执行记录", "暂无托管执行记录。", parent=self)
            return
        lines = [
            f"{row['created_at']} · {row['store_name']} · "
            f"{row['result']} · {row['actions']} · {row['message']}"
            for row in rows[:40]
        ]
        messagebox.showinfo("托管执行记录", "\n".join(lines), parent=self)


class BigScreenWindow(tk.Toplevel):
    window_title = "澜琅电商数据大屏"

    def __init__(self, parent: tk.Misc, window: "MainWindow"):
        from .reference_big_screen import ReferenceBigScreenPage

        super().__init__(parent)
        self.window = window
        self.title(self.window_title)
        self.configure(bg="#081a2d")
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.refresh_after_id: str | None = None
        self.refresh_interval_var = tk.StringVar(value="1 分钟")
        style = ttk.Style(self)
        style.configure("FullScreen.TFrame", background="#081a2d")
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.screen_page = ReferenceBigScreenPage(
            self,
            window,
            on_close=self.close,
            refresh_interval_var=self.refresh_interval_var,
            on_refresh_interval=self._schedule_refresh,
        )
        self.screen_page.grid(row=0, column=0, sticky="nsew")
        self._bind_escape_tree(self)
        self.after_idle(self._enter_fullscreen)
        self._schedule_refresh()

    def _bind_escape_tree(self, widget: tk.Misc) -> None:
        widget.bind("<Escape>", self._handle_escape, add="+")
        widget.bind("<F11>", self._handle_escape, add="+")
        for child in widget.winfo_children():
            self._bind_escape_tree(child)

    def _handle_escape(self, _event=None):
        self.close()
        return "break"

    def _enter_fullscreen(self) -> None:
        if not self.winfo_exists():
            return
        try:
            self.attributes("-fullscreen", True)
        except tk.TclError:
            self.state("zoomed")
        self.lift()
        self.focus_force()
        self.screen_page.refresh()

    def _schedule_refresh(self) -> None:
        if not self.winfo_exists():
            return
        if self.refresh_after_id is not None:
            try:
                self.after_cancel(self.refresh_after_id)
            except tk.TclError:
                pass
            self.refresh_after_id = None
        milliseconds = {
            "关闭": 0,
            "30 秒": 30_000,
            "1 分钟": 60_000,
            "5 分钟": 300_000,
        }.get(self.refresh_interval_var.get(), 60_000)
        if milliseconds:
            self.refresh_after_id = self.after(milliseconds, self._auto_refresh)

    def _auto_refresh(self) -> None:
        self.refresh_after_id = None
        if not self.winfo_exists():
            return
        self.screen_page.refresh()
        self._schedule_refresh()

    def close(self) -> None:
        if not self.winfo_exists():
            return
        try:
            self.attributes("-fullscreen", False)
        except tk.TclError:
            pass
        if self.refresh_after_id is not None:
            try:
                self.after_cancel(self.refresh_after_id)
            except tk.TclError:
                pass
            self.refresh_after_id = None
        self.destroy()
        if self.window._big_screen_window is self:
            self.window._big_screen_window = None
        if self.window.winfo_exists():
            self.window.deiconify()
            self.window.lift()
            self.window.focus_force()


class BigScreenPage(BasePage):
    title = "澜琅电商数据大屏"
    subtitle = "今日经营 · 趋势 · 店铺明细"

    def __init__(
        self,
        parent: tk.Widget,
        window: "MainWindow",
        on_close=None,
        refresh_interval_var: tk.StringVar | None = None,
        on_refresh_interval=None,
    ):
        super().__init__(parent, window)
        self.configure(style="FullScreen.TFrame")
        screen_style = ttk.Style(self)
        screen_style.configure(
            "BigScreen.Treeview",
            background="#0b223a",
            fieldbackground="#0b223a",
            foreground="#d8f2ff",
            rowheight=34,
            borderwidth=0,
        )
        screen_style.configure(
            "BigScreen.Treeview.Heading",
            background="#123657",
            foreground="#d8f2ff",
            relief="flat",
            padding=(8, 8),
            font=("Microsoft YaHei UI", 9, "bold"),
        )
        screen_style.map(
            "BigScreen.Treeview",
            background=[("selected", "#1b5074")],
            foreground=[("selected", "white")],
        )
        screen_style.map(
            "BigScreen.Treeview.Heading",
            background=[("active", "#1b5074")],
        )
        self.columnconfigure(0, weight=1)
        self.rowconfigure(3, weight=1)
        self.columnconfigure(1, weight=1)
        self.period_var = tk.StringVar(value="今日")
        self.refresh_interval_var = refresh_interval_var or tk.StringVar(value="1 分钟")
        self.on_refresh_interval = on_refresh_interval
        self.hide_store_names_var = tk.BooleanVar(value=False)
        self.sort_column = ""
        self.sort_reverse = False
        self._store_rows: list[dict] = []

        header = tk.Frame(self, bg="#081a2d", padx=18, pady=12)
        header.grid(row=0, column=0, columnspan=2, sticky="ew")
        tk.Label(
            header,
            text="澜琅电商数据大屏",
            bg="#081a2d",
            fg="#e9fbff",
            font=("Microsoft YaHei UI", 18, "bold"),
        ).pack(side="left")
        self.updated_var = tk.StringVar(value="正在读取本机经营数据…")
        tk.Label(
            header,
            textvariable=self.updated_var,
            bg="#081a2d",
            fg="#6b8da5",
            font=("Microsoft YaHei UI", 8),
        ).pack(side="left", padx=14, pady=(5, 0))
        tools = tk.Frame(header, bg="#081a2d")
        tools.pack(side="right")
        if on_close is not None:
            tk.Button(
                tools,
                text="✕  关闭大屏",
                command=on_close,
                bg="#21384e",
                fg="white",
                activebackground="#b74444",
                activeforeground="white",
                relief="flat",
                bd=0,
                padx=15,
                pady=6,
                cursor="hand2",
                font=("Microsoft YaHei UI", 9, "bold"),
            ).pack(side="right", padx=(8, 0))
        tk.Button(
            tools,
            text="刷新数据",
            command=self._sync,
            bg="#1677e8",
            fg="white",
            activebackground="#0f68cf",
            activeforeground="white",
            relief="flat",
            bd=0,
            padx=14,
            pady=6,
            cursor="hand2",
        ).pack(side="right", padx=5)
        tk.Label(
            tools,
            text="实时刷新",
            bg="#081a2d",
            fg="#9db6ca",
            font=("Microsoft YaHei UI", 9),
        ).pack(side="right", padx=(8, 4))
        interval = ttk.Combobox(
            tools,
            textvariable=self.refresh_interval_var,
            values=("关闭", "30 秒", "1 分钟", "5 分钟"),
            state="readonly",
            width=8,
        )
        interval.pack(side="right")
        interval.bind(
            "<<ComboboxSelected>>",
            lambda _event: self.on_refresh_interval()
            if callable(self.on_refresh_interval)
            else None,
        )
        tk.Label(
            tools,
            text="全屏显示",
            bg="#0e2b42",
            fg="#8fb5ce",
            padx=10,
            pady=6,
            font=("Microsoft YaHei UI", 8),
        ).pack(side="right", padx=5)
        period_bar = tk.Frame(self, bg="#081a2d", padx=18)
        period_bar.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        self.period_buttons: dict[str, tk.Button] = {}
        for label in ("今日", "昨日", "近 7 天", "近 15 天", "近 30 天"):
            value = label.replace(" ", "")
            period_button = tk.Button(
                period_bar,
                text=label,
                command=lambda selected=value: self._change_period(selected),
                bg="#0f8d84" if value == self.period_var.get() else "#0c253c",
                fg="white",
                activebackground="#1c4a72",
                activeforeground="white",
                relief="flat",
                bd=0,
                padx=12,
                pady=5,
                cursor="hand2",
            )
            period_button.pack(side="left", padx=(0, 3))
            self.period_buttons[value] = period_button

        metrics = tk.Frame(self, bg="#081a2d")
        metrics.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        for index in range(8):
            metrics.columnconfigure(index, weight=1, uniform="screen")
        self.metric_labels = []
        for index, (label, color) in enumerate(
            (
                ("今日订单", "#58b9ff"),
                ("今日成交金额", "#39ef91"),
                ("昨日订单", "#9bc7ff"),
                ("待发货", "#ffcb67"),
                ("待收货", "#c39cff"),
                ("曝光人数", "#55d6be"),
                ("成交人数", "#78a9ff"),
                ("售后", "#ff8f8f"),
            )
        ):
            card = tk.Frame(
                metrics,
                bg="#0b223a",
                highlightbackground="#133b5c",
                highlightthickness=1,
                padx=18,
                pady=12,
            )
            card.grid(
                row=0,
                column=index,
                sticky="nsew",
                padx=(0 if index == 0 else 6, 0),
            )
            tk.Label(
                card,
                text=label,
                bg="#0b223a",
                fg="#8aa9bf",
                font=("Microsoft YaHei UI", 10),
            ).pack(anchor="w")
            value_label = tk.Label(
                card,
                text="—",
                bg="#0b223a",
                fg=color,
                font=("Microsoft YaHei UI", 20, "bold"),
            )
            value_label.pack(anchor="w", pady=(5, 0))
            self.metric_labels.append(value_label)

        charts = tk.Frame(self, bg="#081a2d")
        charts.grid(row=3, column=0, columnspan=2, sticky="nsew", pady=(0, 8))
        charts.columnconfigure(0, weight=2)
        charts.columnconfigure(1, weight=1)
        charts.rowconfigure(0, weight=1)
        detail_panel = tk.Frame(
            charts,
            bg="#0b223a",
            highlightbackground="#133b5c",
            highlightthickness=1,
        )
        detail_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        detail_panel.rowconfigure(1, weight=1)
        detail_panel.columnconfigure(0, weight=1)
        detail_header = tk.Frame(detail_panel, bg="#0b223a")
        detail_header.grid(row=0, column=0, sticky="ew", padx=14, pady=(9, 5))
        tk.Label(
            detail_header,
            text="按天明细",
            bg="#0b223a",
            fg="#d8f2ff",
            font=("Microsoft YaHei UI", 11, "bold"),
        ).pack(side="left")
        tk.Label(
            detail_header,
            text="订单 / 成交金额 / 退款金额 / 推荐曝光 / 搜索曝光",
            bg="#0b223a",
            fg="#7895aa",
            font=("Microsoft YaHei UI", 8),
        ).pack(side="right")
        self.day_tree = ttk.Treeview(
            detail_panel,
            columns=("date", "orders", "gmv", "refund", "exposure", "search"),
            show="headings",
            height=7,
            style="BigScreen.Treeview",
        )
        for key, text, width in (
            ("date", "日期", 90),
            ("orders", "订单", 80),
            ("gmv", "成交金额", 125),
            ("refund", "退款金额", 120),
            ("exposure", "推荐曝光", 110),
            ("search", "搜索曝光（昨日）", 130),
        ):
            self.day_tree.heading(key, text=text)
            self.day_tree.column(key, width=width, anchor="w")
        self.day_tree.grid(row=1, column=0, sticky="nsew", padx=(10, 0), pady=(0, 8))
        ttk.Scrollbar(
            detail_panel, orient="vertical", command=self.day_tree.yview
        ).grid(row=1, column=1, sticky="ns", padx=(0, 8), pady=(0, 8))
        ranking_panel = tk.Frame(
            charts,
            bg="#0b223a",
            highlightbackground="#133b5c",
            highlightthickness=1,
        )
        ranking_panel.grid(row=0, column=1, sticky="nsew")
        tk.Label(
            ranking_panel,
            text="店铺成交 TOP",
            bg="#0b223a",
            fg="#d8f2ff",
            font=("Microsoft YaHei UI", 11, "bold"),
        ).pack(anchor="w", padx=14, pady=(10, 0))
        self.ranking_canvas = tk.Canvas(
            ranking_panel,
            bg="#0b223a",
            highlightthickness=0,
            height=220,
        )
        self.ranking_canvas.pack(fill="both", expand=True, padx=10, pady=8)

        body = tk.Frame(self, bg="#0b223a", highlightbackground="#133b5c", highlightthickness=1)
        body.grid(row=4, column=0, columnspan=2, sticky="nsew")
        body.rowconfigure(1, weight=1)
        body.columnconfigure(0, weight=1)
        store_header = tk.Frame(body, bg="#0b223a")
        store_header.grid(row=0, column=0, columnspan=2, sticky="ew", padx=12, pady=(7, 5))
        tk.Label(
            store_header,
            text="店铺明细",
            bg="#0b223a",
            fg="#d8f2ff",
            font=("Microsoft YaHei UI", 10, "bold"),
        ).pack(side="left")
        tk.Checkbutton(
            store_header,
            text="隐藏店铺名",
            variable=self.hide_store_names_var,
            command=self.refresh,
            bg="#0b223a",
            fg="#8ca9bd",
            activebackground="#0b223a",
            activeforeground="#d8f2ff",
            selectcolor="#153b58",
            bd=0,
            font=("Microsoft YaHei UI", 8),
        ).pack(side="right")
        columns = (
            "store",
            "today_success",
            "quota",
            "login",
            "in_sale",
            "gmv",
            "buyers",
            "orders",
            "clicks",
            "recommend_today",
            "recommend_yesterday",
            "refund",
            "aftersale",
            "warn",
            "violation",
        )
        self.tree = ttk.Treeview(
            body,
            columns=columns,
            show="headings",
            height=7,
            style="BigScreen.Treeview",
        )
        for key, text, width in (
            ("store", "店铺名", 160),
            ("today_success", "今日成功", 90),
            ("quota", "提审额度", 100),
            ("login", "登录状态", 95),
            ("in_sale", "在售", 75),
            ("gmv", "成交金额", 110),
            ("buyers", "成交人数", 90),
            ("orders", "订单数", 85),
            ("clicks", "商品点击", 90),
            ("recommend_today", "今日推荐曝光", 110),
            ("recommend_yesterday", "昨日推荐曝光", 110),
            ("refund", "退款金额", 105),
            ("aftersale", "售后", 75),
            ("warn", "店铺预警", 85),
            ("violation", "店铺违规", 85),
        ):
            self.tree.heading(key, text=text)
            self.tree.column(key, width=width, anchor="w")
        self.tree.grid(row=1, column=0, sticky="nsew")
        y_scroll = ttk.Scrollbar(body, orient="vertical", command=self.tree.yview)
        y_scroll.grid(
            row=1,
            column=1,
            sticky="ns",
        )
        x_scroll = ttk.Scrollbar(
            body, orient="horizontal", command=self.tree.xview
        )
        x_scroll.grid(
            row=2,
            column=0,
            sticky="ew",
        )
        self.tree.configure(
            yscrollcommand=y_scroll.set,
            xscrollcommand=x_scroll.set,
        )

    def refresh(self) -> None:
        days = self._period_days()
        reference_date = (
            date.today() - timedelta(days=1)
            if self.period_var.get() == "昨日"
            else date.today()
        )
        start_date = reference_date - timedelta(days=days - 1)
        end_date = reference_date
        start_time = int(
            datetime.combine(start_date, time.min).timestamp()
        )
        end_time = int(
            datetime.combine(end_date, time.max).timestamp()
        )
        stores = self.db.list_stores()
        self._store_rows = []
        trend_totals: dict[str, int] = {}
        day_totals: dict[str, dict[str, int]] = {}
        totals = {
            "today_orders": 0,
            "today_gmv": 0,
            "yesterday_orders": 0,
            "wait_send": 0,
            "wait_receive": 0,
            "recommend_exposure": 0,
            "buyers_today": 0,
            "aftersale": 0,
        }
        for store in stores:
            orders = self.db.list_orders(
                store_id=store.id,
                start_time=start_time,
                end_time=end_time,
            )
            valid = [row for row in orders if int(row["status"] or 0) != 250]
            all_orders = self.db.list_orders(store_id=store.id)
            try:
                metrics = json.loads(
                    self.db.get_setting(
                        f"store_metrics_{store.id}", "{}"
                    )
                )
            except (TypeError, ValueError):
                metrics = {}
            if not isinstance(metrics, dict):
                metrics = {}
            analytics = [
                row
                for row in self.db.list_analytics_days(store.id, days)
                if row["business_date"] >= start_date.isoformat()
            ]
            period_gmv = sum(
                int(row["pay_gmv"] or 0)
                for row in analytics
            )
            period_buyers = sum(
                int(row["pay_uv"] or 0) for row in analytics
            )
            period_orders = sum(
                int(row["pay_order_cnt"] or 0) for row in analytics
            )
            period_clicks = sum(
                int(row["product_click_uv"] or 0) for row in analytics
            )
            period_refund = sum(
                int(row["pay_refund_gmv"] or 0) for row in analytics
            )
            if self.period_var.get() == "今日":
                period_gmv = int(metrics.get("gmv_today") or 0)
                period_buyers = int(metrics.get("buyers_today") or 0)
                period_orders = int(metrics.get("orders_today") or 0)
                period_clicks = int(
                    metrics.get("product_click_today") or 0
                )
                period_refund = int(metrics.get("refund_today") or 0)
            elif self.period_var.get() == "昨日":
                period_gmv = int(metrics.get("gmv_yesterday") or 0)
                period_orders = int(
                    metrics.get("orders_yesterday") or 0
                )
                yesterday = (
                    date.today() - timedelta(days=1)
                ).isoformat()
                yday_row = next(
                    (
                        row
                        for row in analytics
                        if row["business_date"] == yesterday
                    ),
                    {},
                )
                period_buyers = int(yday_row.get("pay_uv") or 0)
                period_clicks = int(
                    yday_row.get("product_click_uv") or 0
                )
                period_refund = int(
                    yday_row.get("pay_refund_gmv") or 0
                )
            wait_send = sum(
                int(row["status"] or 0) == 20 for row in all_orders
            )
            wait_receive = sum(
                int(row["status"] or 0) == 30 for row in all_orders
            )
            today_values = {
                "today_orders": int(metrics.get("orders_today") or 0),
                "today_gmv": int(metrics.get("gmv_today") or 0),
                "yesterday_orders": int(
                    metrics.get("orders_yesterday") or 0
                ),
                "wait_send": int(
                    metrics.get("wait_send", wait_send) or wait_send
                ),
                "wait_receive": wait_receive,
                "recommend_exposure": int(
                    metrics.get("recommend_exposure") or 0
                ),
                "buyers_today": int(metrics.get("buyers_today") or 0),
                "aftersale": int(metrics.get("aftersale") or 0),
            }
            for key, value in today_values.items():
                totals[key] += value
            self._store_rows.append(
                {
                    "store": store,
                    "gmv": period_gmv,
                    "orders": period_orders,
                    "buyers": period_buyers,
                    "clicks": period_clicks,
                    "refund": period_refund,
                    "in_sale": int(metrics.get("in_sale") or 0),
                    "quota": (
                        f"{int(metrics.get('quota_remaining') or 0)}/"
                        f"{int(metrics.get('quota_total') or 0)}"
                        if int(metrics.get("quota_total") or 0)
                        else "—"
                    ),
                    "today_success": int(
                        metrics.get("today_success") or 0
                    ),
                    "recommend_today": int(
                        metrics.get("recommend_exposure") or 0
                    ),
                    "recommend_yesterday": int(
                        metrics.get("recommend_exposure_yesterday") or 0
                    ),
                    "aftersale": int(metrics.get("aftersale") or 0),
                    "warn": int(metrics.get("warn") or 0),
                    "violation": int(metrics.get("violation") or 0),
                }
            )
            for row in analytics:
                trend_totals[row["business_date"]] = (
                    trend_totals.get(row["business_date"], 0)
                    + int(row["pay_gmv"] or 0)
                )
                try:
                    raw = json.loads(row.get("raw_json") or "{}")
                except (TypeError, ValueError):
                    raw = {}
                if not isinstance(raw, dict):
                    raw = {}
                daily = day_totals.setdefault(
                    row["business_date"],
                    {
                        "orders": 0,
                        "gmv": 0,
                        "refund": 0,
                        "exposure": 0,
                        "search": 0,
                    },
                )
                daily["orders"] += int(row["pay_order_cnt"] or 0)
                daily["gmv"] += int(row["pay_gmv"] or 0)
                daily["refund"] += int(row["pay_refund_gmv"] or 0)
                daily["exposure"] += int(
                    raw.get("recommend_exposure")
                    or raw.get("recommendExposure")
                    or raw.get("exposure")
                    or 0
                )
                daily["search"] += int(
                    raw.get("search_exposure_yesterday")
                    or raw.get("searchExposure")
                    or raw.get("search_exposure")
                    or 0
                )
        self.metric_labels[0].configure(text=f"{totals['today_orders']:,}")
        self.metric_labels[1].configure(
            text=f"¥{totals['today_gmv'] / 100:,.2f}"
        )
        self.metric_labels[2].configure(
            text=f"{totals['yesterday_orders']:,}"
        )
        self.metric_labels[3].configure(text=f"{totals['wait_send']:,}")
        self.metric_labels[4].configure(text=f"{totals['wait_receive']:,}")
        self.metric_labels[5].configure(
            text=f"{totals['recommend_exposure']:,}"
        )
        self.metric_labels[6].configure(
            text=f"{totals['buyers_today']:,}"
        )
        self.metric_labels[7].configure(text=f"{totals['aftersale']:,}")
        self.tree.delete(*self.tree.get_children())
        for row in sorted(self._store_rows, key=lambda item: item["gmv"], reverse=True):
            store = row["store"]
            self.tree.insert(
                "",
                "end",
                values=(
                    "******" if self.hide_store_names_var.get() else store.name,
                    row["today_success"],
                    row["quota"],
                    store.web_status,
                    row["in_sale"],
                    f"¥{row['gmv'] / 100:,.2f}",
                    row["buyers"],
                    row["orders"],
                    row["clicks"],
                    row["recommend_today"],
                    row["recommend_yesterday"],
                    f"¥{row['refund'] / 100:,.2f}",
                    row["aftersale"],
                    row["warn"],
                    row["violation"],
                ),
            )
        if hasattr(self, "day_tree"):
            self.day_tree.delete(*self.day_tree.get_children())
            for offset in range(days):
                business_date = start_date + timedelta(days=offset)
                daily = day_totals.get(
                    business_date.isoformat(),
                    {"orders": 0, "gmv": 0, "refund": 0, "exposure": 0, "search": 0},
                )
                self.day_tree.insert(
                    "",
                    "end",
                    values=(
                        business_date.strftime("%m-%d"),
                        daily["orders"],
                        f"¥{daily['gmv'] / 100:,.2f}",
                        f"¥{daily['refund'] / 100:,.2f}",
                        daily["exposure"],
                        daily["search"],
                    ),
                )
        self._draw_ranking()
        self.updated_var.set(
            f"数据更新于 {datetime.now():%m/%d %H:%M} · {len(stores)}/{len(stores)} 家有数据"
        )
        self.window.set_status(
            f"数据大屏已刷新：{len(stores)} 家店铺 · {self.period_var.get()}"
        )

    def _period_days(self) -> int:
        return {
            "今日": 1,
            "昨日": 1,
            "近7天": 7,
            "近15天": 15,
            "近30天": 30,
        }.get(self.period_var.get(), 1)

    def _change_period(self, value: str) -> None:
        self.period_var.set(value)
        for key, period_button in getattr(self, "period_buttons", {}).items():
            period_button.configure(
                bg="#0f8d84" if key == value else "#0c253c"
            )
        self.refresh()

    def _draw_trend(
        self,
        values: dict[str, int],
        start_date: date,
        days: int,
    ) -> None:
        canvas = self.trend_canvas
        canvas.delete("all")
        canvas.update_idletasks()
        width = max(500, canvas.winfo_width())
        height = max(180, canvas.winfo_height())
        left, right, top, bottom = 42, 18, 18, 34
        chart_width = width - left - right
        chart_height = height - top - bottom
        points = []
        max_value = max(values.values(), default=0)
        for index in range(days):
            business_date = (start_date + timedelta(days=index)).isoformat()
            value = values.get(business_date, 0)
            x = left + (
                chart_width * index / max(1, days - 1)
            )
            y = top + chart_height - (
                chart_height * value / max(1, max_value)
            )
            points.extend((x, y))
        canvas.create_line(left, top, left, top + chart_height, fill="#31546e")
        canvas.create_line(
            left,
            top + chart_height,
            left + chart_width,
            top + chart_height,
            fill="#31546e",
        )
        if len(points) >= 4:
            canvas.create_line(
                *points,
                fill="#3de79b",
                width=3,
                smooth=True,
            )
            for x, y in zip(points[0::2], points[1::2]):
                canvas.create_oval(
                    x - 3,
                    y - 3,
                    x + 3,
                    y + 3,
                    fill="#3de79b",
                    outline="",
                )
        canvas.create_text(
            left + 4,
            top,
            text=f"¥{max_value / 100:,.2f}",
            fill="#7695aa",
            anchor="nw",
        )
        canvas.create_text(
            left,
            height - 14,
            text=start_date.strftime("%m-%d"),
            fill="#7695aa",
            anchor="w",
        )
        canvas.create_text(
            left + chart_width,
            height - 14,
            text=date.today().strftime("%m-%d"),
            fill="#7695aa",
            anchor="e",
        )

    def _draw_ranking(self) -> None:
        canvas = self.ranking_canvas
        canvas.delete("all")
        canvas.update_idletasks()
        width = max(260, canvas.winfo_width())
        rows = sorted(
            self._store_rows,
            key=lambda item: item["gmv"],
            reverse=True,
        )[:6]
        max_value = max((item["gmv"] for item in rows), default=0)
        for index, item in enumerate(rows):
            y = 20 + index * 31
            store = item["store"]
            canvas.create_text(
                12,
                y,
                text=store.name[:10],
                fill="#bfe4f8",
                anchor="w",
                font=("Microsoft YaHei UI", 9),
            )
            bar_width = (
                (width - 150) * item["gmv"] / max_value
                if max_value
                else 0
            )
            canvas.create_rectangle(
                124,
                y - 7,
                124 + bar_width,
                y + 7,
                fill="#26b9e8",
                outline="",
            )

    def _sync(self) -> None:
        stores = self.db.list_stores()
        if not stores:
            return
        self.window.set_status("正在同步经营数据…", busy=True)

        def work(progress) -> list[str]:
            errors = []
            for index, store in enumerate(stores, 1):
                try:
                    progress(
                        f"正在同步 {store.name} 的经营数据",
                        index - 1,
                        len(stores),
                    )
                    self.window.service.refresh_store_metrics(store)
                except Exception as exc:
                    errors.append(f"{store.name}: {exc}")
            return errors

        def done(errors: list[str]) -> None:
            self.refresh()
            if errors:
                messagebox.showwarning(
                    "部分店铺同步失败",
                    "\n".join(errors[:8]),
                    parent=self,
                )

        self.window.run_task(
            work,
            done,
            lambda error: messagebox.showerror("经营数据同步失败", error, parent=self),
            lambda message, current, total: self.window.set_status(
                message,
                busy=True,
            ),
            log_title="同步店铺经营指标",
        )
