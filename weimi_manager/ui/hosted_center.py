from __future__ import annotations

import json
import tkinter as tk
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from tkinter import messagebox, ttk
from typing import Any

from ..models import Store
from .pages import BasePage
from .skeletons import (
    _hosted_no_traffic_product_ids,
    _run_hosted_config_save,
)
from .theme import MUTED, PANEL, TEXT, button

HOSTED_ACTIONS = (
    ("add_affiliate", "添加联盟推广"),
    ("latest_affiliate", "最新联盟商品"),
    ("invite_influencer", "达人邀约"),
    ("revoke_audit", "撤销审核"),
    ("delete_audit_failed", "删除审核失败"),
    ("listing_quota", "上架额度"),
    ("list_products", "上架商品"),
    ("modify_commission", "修改佣金"),
    ("flash_sale", "限时抢购"),
    ("modify_price", "修改价格"),
    ("delete_no_traffic", "删除无流量"),
    ("cancel_flash_sale", "取消限时抢购"),
    ("cancel_direct_subsidy", "取消单品直降"),
    ("prefill_11", "双11一键预填报"),
    ("direct_subsidy_rights", "直降享权益"),
)

SUPPORTED_ACTIONS = {
    "add_affiliate",
    "latest_affiliate",
    "revoke_audit",
    "delete_audit_failed",
    "list_products",
    "modify_commission",
    "flash_sale",
    "modify_price",
    "delete_no_traffic",
    "cancel_flash_sale",
    "prefill_11",
}

UNSUPPORTED_ACTIONS = {
    key for key, _label in HOSTED_ACTIONS if key not in SUPPORTED_ACTIONS and key != "listing_quota"
}


class HostedCenterPage(BasePage):
    title = "托管中心"
    subtitle = "逐店配置、执行项目和一键批量托管"

    def __init__(self, parent: tk.Widget, window):
        super().__init__(parent, window)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(3, weight=1)
        self._rows: dict[str, dict[str, Any]] = {}
        self._row_buttons: dict[str, tuple[ttk.Button, ttk.Button]] = {}
        self.timed_enabled = self.db.get_setting("hosted_schedule_enabled", "0") == "1"
        self.schedule_time_var = tk.StringVar(
            value=self.db.get_setting("hosted_schedule_time", "09:30")
        )
        self.schedule_store_ids = self._load_schedule_store_ids()
        self._timed_after_id: str | None = None
        self.state_var = tk.StringVar(value="托管配置加载中…")
        self.result_var = tk.StringVar(value="执行结果：尚未执行")
        self.select_all_var = tk.BooleanVar(value=False)

        toolbar = ttk.Frame(self, style="TFrame")
        toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        ttk.Label(
            toolbar,
            textvariable=self.state_var,
            style="Subtitle.TLabel",
        ).pack(side="left")
        self.execute_configured_button = button(
            toolbar,
            "执行全部已配置项目",
            self._execute_configured,
            "Primary",
        )
        self.execute_configured_button.pack(side="right")
        button(toolbar, "一键配置", self._configure_all).pack(
            side="right",
            padx=6,
        )
        button(toolbar, "定时执行", self._open_timed_dialog).pack(
            side="right",
            padx=6,
        )
        button(toolbar, "刷新", self.refresh).pack(side="right", padx=6)

        result_bar = tk.Frame(
            self,
            bg="#eef5fd",
            highlightbackground="#cbdff5",
            highlightthickness=1,
        )
        result_bar.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        tk.Label(
            result_bar,
            textvariable=self.result_var,
            bg="#eef5fd",
            fg="#264f76",
            anchor="w",
            padx=14,
            pady=8,
        ).pack(fill="x")

        actions = ttk.Frame(self, style="TFrame")
        actions.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        tk.Checkbutton(
            actions,
            text="全选店铺",
            variable=self.select_all_var,
            command=self._toggle_select_all,
            bg="#f4f2eb",
            fg=TEXT,
            activebackground="#f4f2eb",
            selectcolor="white",
            bd=0,
        ).pack(side="left")
        button(
            actions,
            "配置选中店铺",
            self._configure_bulk_selected,
        ).pack(side="left", padx=6)
        self.execute_all_button = button(
            actions,
            "执行选中店铺",
            self._execute_all,
            "Primary",
        )
        self.execute_all_button.pack(side="left", padx=6)
        ttk.Label(
            actions,
            text="双击“配置”打开逐店配置，双击“执行”运行选中店铺。",
            style="Subtitle.TLabel",
        ).pack(side="right")

        body = ttk.Frame(self, style="Panel.TFrame")
        body.grid(row=3, column=0, sticky="nsew")
        self._body = body
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=1)
        columns = (
            ("store", "店铺", 180),
            *[(key, label, 112 if key != "listing_quota" else 92) for key, label in HOSTED_ACTIONS],
            ("config", "店铺配置", 92),
            ("execute", "执行", 72),
        )
        self.tree = ttk.Treeview(
            body,
            columns=tuple(key for key, _label, _width in columns),
            show="headings",
            selectmode="extended",
        )
        for key, label, width in columns:
            self.tree.heading(key, text=label)
            self.tree.column(
                key,
                width=width,
                minwidth=70,
                anchor="w" if key == "store" else "center",
                stretch=False,
            )
        self.tree.grid(row=0, column=0, sticky="nsew")
        y_scroll = ttk.Scrollbar(
            body,
            orient="vertical",
            command=self._scroll_tree_y,
        )
        y_scroll.grid(row=0, column=1, sticky="ns")
        x_scroll = ttk.Scrollbar(body, orient="horizontal", command=self.tree.xview)
        x_scroll.grid(row=1, column=0, sticky="ew")
        self.tree.configure(
            yscrollcommand=lambda first, last: (
                y_scroll.set(first, last),
                self._position_row_buttons(),
            ),
            xscrollcommand=x_scroll.set,
        )
        self.tree.tag_configure("configured", background="#fff7df")
        self.tree.tag_configure("failed", background="#fff0ee")
        self.tree.bind("<<TreeviewSelect>>", self._update_execute_state)
        self.tree.bind("<Double-1>", self._tree_double_click)
        self.tree.bind(
            "<Configure>",
            lambda _event: self.after_idle(self._position_row_buttons),
            add="+",
        )
        self.tree.bind(
            "<MouseWheel>",
            lambda _event: self.after_idle(self._position_row_buttons),
            add="+",
        )

        if self.timed_enabled:
            self._schedule_timed_tick(1000)

    def _load_schedule_store_ids(self) -> set[int]:
        try:
            values = json.loads(self.db.get_setting("hosted_schedule_stores", "[]"))
        except (TypeError, ValueError):
            values = []
        selected = {int(value) for value in values if str(value).strip().isdigit()}
        if selected:
            return selected
        return {store.id for store in self.db.list_stores()}

    @staticmethod
    def _quota_text(db, store_id: int) -> str:
        try:
            payload = json.loads(db.get_setting(f"product_quota_{store_id}", "{}"))
        except (TypeError, ValueError):
            payload = {}
        remaining = int(payload.get("remaining") or 0)
        total = int(payload.get("total") or 0)
        return f"{remaining}/{total}" if total else "-/-"

    @classmethod
    def _action_status(
        cls,
        key: str,
        config: dict[str, Any],
        quota_text: str,
    ) -> str:
        if key == "listing_quota":
            return quota_text
        if key in UNSUPPORTED_ACTIONS:
            return "☐ 接口待接入"
        return "☑ 待执行" if bool(config.get(key)) else "☐ 未配置"

    def refresh(self) -> None:
        stores = self.db.list_stores()
        for pair in self._row_buttons.values():
            for widget in pair:
                widget.destroy()
        self._row_buttons.clear()
        self.tree.delete(*self.tree.get_children())
        self._rows.clear()
        configured = 0
        enabled = 0
        for index, store in enumerate(stores):
            config = dict(self.db.get_hosted_config(store.id) or {})
            if config:
                configured += 1
            if config.get("enabled"):
                enabled += 1
            quota_text = self._quota_text(self.db, store.id)
            values = [
                f"{store.name} · {store.web_status or '未登录'}",
            ]
            values.extend(
                self._action_status(key, config, quota_text) for key, _label in HOSTED_ACTIONS
            )
            values.extend(("配置", "执行"))
            iid = str(index)
            self._rows[iid] = {"store": store, "config": config}
            self.tree.insert(
                "",
                "end",
                iid=iid,
                values=tuple(values),
                tags=("configured",) if config.get("enabled") else (),
            )
        self.state_var.set(
            f"{len(stores)} 个店铺 · {configured} 个已保存配置 · "
            f"{enabled} 个已启用项目 · "
            + (
                f"定时执行 {self.schedule_time_var.get()}"
                if self.timed_enabled
                else "定时执行未开启"
            )
        )
        self.execute_configured_button.configure(state="normal" if enabled else "disabled")
        self._update_execute_state()
        self.after_idle(self._position_row_buttons)

    def _scroll_tree_y(self, *args) -> None:
        self.tree.yview(*args)
        self.after_idle(self._position_row_buttons)

    def _position_row_buttons(self) -> None:
        if not hasattr(self, "tree") or not self.tree.winfo_exists():
            return
        tree_width = self.tree.winfo_width()
        tree_height = self.tree.winfo_height()
        for iid, row in self._rows.items():
            pair = self._row_buttons.get(iid)
            if pair is None:
                config_button = ttk.Button(
                    self._body,
                    text="配置",
                    style="Secondary.TButton",
                    command=lambda key=iid: self._configure_row(key),
                    width=5,
                )
                execute_button = ttk.Button(
                    self._body,
                    text="执行",
                    style="Primary.TButton",
                    command=lambda key=iid: self._execute_rows(
                        [self._rows[key]],
                        scheduled=False,
                    ),
                    width=5,
                )
                pair = (config_button, execute_button)
                self._row_buttons[iid] = pair
            bbox = self.tree.bbox(iid, "config")
            exec_bbox = self.tree.bbox(iid, "execute")
            if not bbox or not exec_bbox:
                pair[0].place_forget()
                pair[1].place_forget()
                continue
            x, y, width, height = bbox
            exec_x, _exec_y, exec_width, _exec_height = exec_bbox
            if (
                y < 0
                or y + height > tree_height
                or x + width > tree_width
                or exec_x + exec_width > tree_width
            ):
                pair[0].place_forget()
                pair[1].place_forget()
                continue
            pair[0].place(
                x=x + 3,
                y=y + 2,
                width=max(44, width - 6),
                height=max(22, height - 4),
            )
            pair[1].place(
                x=exec_x + 3,
                y=_exec_y + 2,
                width=max(44, exec_width - 6),
                height=max(22, _exec_height - 4),
            )

    def _configure_row(self, iid: str) -> None:
        row = self._rows.get(iid)
        if row is None:
            return
        self.tree.selection_set((iid,))
        self._open_hosted_config_dialog([row["store"]], row["config"])

    def _toggle_select_all(self) -> None:
        if self.select_all_var.get():
            self.tree.selection_set(tuple(self.tree.get_children()))
        else:
            self.tree.selection_remove(*self.tree.selection())
        self._update_execute_state()

    def _update_execute_state(self, _event=None) -> None:
        count = len(self.tree.selection())
        self.execute_all_button.configure(state="normal" if count else "disabled")
        self.select_all_var.set(bool(count) and count == len(self.tree.get_children()))

    def _selected_rows(self) -> list[dict[str, Any]]:
        return [self._rows[iid] for iid in self.tree.selection() if iid in self._rows]

    def _tree_double_click(self, event) -> None:
        iid = self.tree.identify_row(event.y)
        if not iid or iid not in self._rows:
            return
        self.tree.selection_set((iid,))
        column = self.tree.identify_column(event.x)
        last_columns = {
            f"#{len(HOSTED_ACTIONS) + 2}": "config",
            f"#{len(HOSTED_ACTIONS) + 3}": "execute",
        }
        if last_columns.get(column) == "execute":
            self._execute_rows([self._rows[iid]], scheduled=False)
        else:
            row = self._rows[iid]
            self._open_hosted_config_dialog([row["store"]], row["config"])

    def _configure_selected(self) -> None:
        rows = self._selected_rows()
        if not rows:
            messagebox.showinfo(
                "请选择店铺",
                "请先选择一家店铺。",
                parent=self,
            )
            return
        self._open_hosted_config_dialog(
            [row["store"] for row in rows],
            rows[0]["config"],
        )

    def _configure_bulk_selected(self) -> None:
        self._configure_selected()

    def _configure_all(self) -> None:
        stores = self.db.list_stores()
        if not stores:
            return
        first = self.db.get_hosted_config(stores[0].id) or {}
        self._open_hosted_config_dialog(stores, first)

    def _open_hosted_config_dialog(
        self,
        stores: list[Store],
        config: dict[str, Any],
    ) -> None:
        if not stores:
            return
        target_name = (
            "全部店铺"
            if len(stores) == len(self.db.list_stores())
            else (stores[0].name if len(stores) == 1 else f"{len(stores)} 家店铺")
        )
        dialog = tk.Toplevel(self)
        dialog.title(target_name)
        dialog.transient(self.winfo_toplevel())
        dialog.geometry("1120x780")
        dialog.minsize(960, 660)
        dialog.configure(bg="#f5f6f7")
        dialog.columnconfigure(0, weight=1)
        dialog.rowconfigure(1, weight=1)
        style = ttk.Style(dialog)
        style.configure(
            "HostedCard.TLabelframe",
            background="#ffffff",
            bordercolor="#82c995",
            relief="solid",
            borderwidth=2,
        )
        style.configure(
            "HostedCard.TLabelframe.Label",
            background="#eef8f1",
            foreground="#26734a",
            padding=(8, 4),
            font=("Microsoft YaHei UI", 10, "bold"),
        )
        style.configure(
            "HostedHeader.TLabel",
            background="#f5f6f7",
            foreground=TEXT,
            font=("Microsoft YaHei UI", 11, "bold"),
        )
        style.configure(
            "HostedHint.TLabel",
            background="#ffffff",
            foreground=MUTED,
            font=("Microsoft YaHei UI", 8),
        )
        style.configure(
            "HostedField.TLabel",
            background="#ffffff",
            foreground=TEXT,
            font=("Microsoft YaHei UI", 9),
        )

        header = ttk.Frame(dialog, padding=(20, 14, 20, 8))
        header.grid(row=0, column=0, sticky="ew")
        ttk.Label(
            header,
            text=target_name,
            style="HostedHeader.TLabel",
        ).pack(anchor="w")
        ttk.Label(
            header,
            text=(f"保存后覆盖这些店铺的托管配置 · 当前 {len(stores)} 家店铺"),
            style="Subtitle.TLabel",
        ).pack(anchor="w", pady=(2, 0))

        canvas = tk.Canvas(
            dialog,
            bg="#f5f6f7",
            highlightthickness=0,
        )
        scrollbar = ttk.Scrollbar(dialog, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.grid(row=1, column=0, sticky="nsew", padx=(20, 0))
        scrollbar.grid(row=1, column=1, sticky="ns", padx=(0, 8))
        body = ttk.Frame(canvas, padding=(4, 4, 14, 4))
        body.columnconfigure(0, weight=1, uniform="hosted-card")
        body.columnconfigure(1, weight=1, uniform="hosted-card")
        body_window = canvas.create_window((0, 0), window=body, anchor="nw")
        body.bind(
            "<Configure>",
            lambda _event: canvas.configure(scrollregion=canvas.bbox("all")),
        )
        canvas.bind(
            "<Configure>",
            lambda event: canvas.itemconfigure(
                body_window,
                width=event.width,
            ),
        )

        promotion = ttk.LabelFrame(
            body,
            text="推广佣金",
            padding=12,
            style="HostedCard.TLabelframe",
        )
        promotion.grid(
            row=0,
            column=0,
            columnspan=2,
            sticky="ew",
            pady=(4, 6),
        )
        influencer = ttk.LabelFrame(
            body,
            text="达人邀约模板",
            padding=12,
            style="HostedCard.TLabelframe",
        )
        influencer.grid(
            row=1,
            column=0,
            columnspan=2,
            sticky="ew",
            pady=6,
        )
        listing = ttk.LabelFrame(
            body,
            text="商品上架",
            padding=12,
            style="HostedCard.TLabelframe",
        )
        listing.grid(row=2, column=0, sticky="nsew", padx=(0, 6), pady=6)
        commission = ttk.LabelFrame(
            body,
            text="修改佣金",
            padding=12,
            style="HostedCard.TLabelframe",
        )
        commission.grid(row=2, column=1, sticky="nsew", padx=(6, 0), pady=6)
        flash = ttk.LabelFrame(
            body,
            text="限时抢购",
            padding=12,
            style="HostedCard.TLabelframe",
        )
        flash.grid(row=3, column=0, sticky="nsew", padx=(0, 6), pady=6)
        price = ttk.LabelFrame(
            body,
            text="修改价格",
            padding=12,
            style="HostedCard.TLabelframe",
        )
        price.grid(row=3, column=1, sticky="nsew", padx=(6, 0), pady=6)
        cleanup = ttk.LabelFrame(
            body,
            text="删除无流量商品",
            padding=12,
            style="HostedCard.TLabelframe",
        )
        cleanup.grid(
            row=4,
            column=0,
            sticky="nsew",
            padx=(0, 6),
            pady=6,
        )
        projects = ttk.LabelFrame(
            body,
            text="执行项目",
            padding=12,
            style="HostedCard.TLabelframe",
        )
        projects.grid(
            row=5,
            column=0,
            columnspan=2,
            sticky="ew",
            pady=(6, 4),
        )

        promotion_commission_var = tk.StringVar(
            value=str(config.get("promotion_commission_pct", 20))
        )
        influencer_name_var = tk.StringVar(value=str(config.get("influencer_name", "")))
        influencer_phone_var = tk.StringVar(value=str(config.get("influencer_phone", "")))
        influencer_wechat_var = tk.StringVar(value=str(config.get("influencer_wechat", "")))
        influencer_count_var = tk.StringVar(value=str(config.get("influencer_count", 1000)))
        influencer_message_var = tk.StringVar(
            value=str(config.get("influencer_message", "合作愉快"))
        )
        influencer_category_var = tk.StringVar(value=str(config.get("influencer_category", "")))
        influencer_follower_var = tk.StringVar(
            value=str(config.get("influencer_follower_filter", ""))
        )
        influencer_gmv_var = tk.StringVar(value=str(config.get("influencer_gmv_filter", "")))
        influencer_live_var = tk.BooleanVar(value=bool(config.get("influencer_live")))
        influencer_video_var = tk.BooleanVar(value=bool(config.get("influencer_video")))
        influencer_public_var = tk.BooleanVar(value=bool(config.get("influencer_public_account")))
        listing_mode_var = tk.StringVar(
            value="API上架" if config.get("listing_mode") == "api" else "手工上架"
        )
        listing_count_var = tk.StringVar(value=str(config.get("listing_count", 150)))
        commission_var = tk.StringVar(value=str(config.get("modify_commission_pct", 20)))
        flash_mode_var = tk.StringVar(value=str(config.get("flash_sale_mode", "打折")))
        configured_flash_discount = config.get("flash_sale_discount", 0.85)
        try:
            configured_flash_discount = min(
                0.85,
                max(0.1, float(configured_flash_discount)),
            )
        except (TypeError, ValueError):
            configured_flash_discount = 0.85
        flash_discount_var = tk.StringVar(value=str(configured_flash_discount))
        price_mode_var = tk.StringVar(value=str(config.get("price_mode", "倍率")))
        price_multiplier_var = tk.StringVar(value=str(config.get("price_multiplier", 1.1)))
        no_traffic_days_var = tk.StringVar(value=str(config.get("no_traffic_days", 7)))
        no_traffic_sales_var = tk.StringVar(value=str(config.get("no_traffic_max_sales", 0)))

        self._field_grid(
            promotion,
            0,
            "联盟佣金",
            promotion_commission_var,
            suffix="%",
        )
        ttk.Label(
            promotion,
            text="执行时对最多 20 个在售商品创建联盟推广计划。",
            style="HostedHint.TLabel",
            wraplength=470,
        ).grid(row=2, column=0, columnspan=3, sticky="w", pady=(6, 0))

        influencer_fields = (
            ("姓名", influencer_name_var),
            ("手机号", influencer_phone_var),
            ("微信号", influencer_wechat_var),
            ("达人数", influencer_count_var),
        )
        for index, (label, variable) in enumerate(influencer_fields):
            label_column = index * 2
            ttk.Label(
                influencer,
                text=label,
                style="HostedField.TLabel",
            ).grid(row=0, column=label_column, sticky="w", pady=(0, 5))
            ttk.Entry(
                influencer,
                textvariable=variable,
            ).grid(
                row=1,
                column=label_column,
                columnspan=2,
                sticky="ew",
                padx=(0, 16 if index < 3 else 0),
                pady=(0, 10),
            )
            influencer.columnconfigure(label_column, weight=1)
        ttk.Label(
            influencer,
            text="留言",
            style="HostedField.TLabel",
        ).grid(row=2, column=0, sticky="w", pady=(0, 5))
        ttk.Entry(
            influencer,
            textvariable=influencer_message_var,
        ).grid(
            row=3,
            column=0,
            columnspan=8,
            sticky="ew",
            pady=(0, 10),
        )
        filter_fields = (
            ("达人类目", influencer_category_var, ("达人类目（已选1个）",)),
            ("粉丝量", influencer_follower_var, ("粉丝量筛选",)),
            ("GMV", influencer_gmv_var, ("GMV筛选",)),
        )
        for index, (label, variable, values) in enumerate(filter_fields):
            column = index * 2
            ttk.Label(
                influencer,
                text=label,
                style="HostedField.TLabel",
            ).grid(row=4, column=column, sticky="w", pady=(0, 5))
            ttk.Combobox(
                influencer,
                textvariable=variable,
                values=values,
                state="readonly",
            ).grid(
                row=5,
                column=column,
                columnspan=2,
                sticky="ew",
                padx=(0, 16),
                pady=(0, 8),
            )
        ttk.Label(
            influencer,
            text="达人条件",
            style="HostedField.TLabel",
        ).grid(row=4, column=6, sticky="w", pady=(0, 5))
        conditions = ttk.Frame(influencer)
        conditions.grid(
            row=5,
            column=6,
            columnspan=2,
            sticky="w",
            pady=(0, 8),
        )
        for label, variable in (
            ("直播", influencer_live_var),
            ("短视频", influencer_video_var),
            ("公众号", influencer_public_var),
        ):
            tk.Checkbutton(
                conditions,
                text=label,
                variable=variable,
                bg="#ffffff",
                fg=TEXT,
                activebackground="#ffffff",
                selectcolor="white",
                bd=0,
            ).pack(side="left", padx=(8, 0))
        ttk.Label(
            influencer,
            text=(
                "每位达人随机使用该店铺联盟商品库中的 30 个商品。"
                "达人邀约接口待接入；模板字段会本地保存，不自动发送。"
            ),
            style="HostedHint.TLabel",
            wraplength=980,
        ).grid(row=6, column=0, columnspan=8, sticky="w", pady=(2, 0))

        self._field_grid(
            listing,
            0,
            "上架方式",
            listing_mode_var,
            values=("手工上架", "API上架"),
        )
        self._field_grid(
            listing,
            1,
            "上架数量",
            listing_count_var,
        )
        ttk.Label(
            listing,
            text="手工上架按数量执行；API 上架忽略数量，额度用完后停止。",
            style="HostedHint.TLabel",
            wraplength=470,
        ).grid(row=2, column=0, columnspan=3, sticky="w", pady=(6, 0))

        self._field_grid(
            commission,
            0,
            "佣金比例",
            commission_var,
            suffix="%",
        )
        ttk.Label(
            commission,
            text="读取现有联盟推广计划并更新佣金比例。",
            style="HostedHint.TLabel",
        ).grid(row=2, column=0, columnspan=3, sticky="w", pady=(6, 0))

        self._field_grid(
            flash,
            0,
            "限时抢购方式",
            flash_mode_var,
            values=("打折",),
        )
        self._field_grid(
            flash,
            1,
            "折扣倍率（0.85=8.5折）",
            flash_discount_var,
            suffix="倍",
        )
        self._field_grid(
            price,
            0,
            "修改价格方式",
            price_mode_var,
            values=("倍率",),
        )
        self._field_grid(
            price,
            1,
            "价格倍率",
            price_multiplier_var,
            suffix="倍",
        )
        self._field_grid(
            cleanup,
            0,
            "无流量天数",
            no_traffic_days_var,
            suffix="天",
        )
        self._field_grid(
            cleanup,
            1,
            "最高销量",
            no_traffic_sales_var,
        )

        action_vars: dict[str, tk.BooleanVar] = {}
        for index, (key, label) in enumerate(HOSTED_ACTIONS):
            if key == "listing_quota":
                continue
            supported = key in SUPPORTED_ACTIONS
            display = label if supported else f"{label}（待接入）"
            variable = tk.BooleanVar(value=bool(config.get(key)))
            action_vars[key] = variable
            tk.Checkbutton(
                projects,
                text=display,
                variable=variable,
                state="normal" if supported else "disabled",
                bg="#ffffff",
                fg=TEXT,
                activebackground="#ffffff",
                selectcolor="white",
                bd=0,
                anchor="w",
            ).grid(
                row=index // 4,
                column=index % 4,
                sticky="w",
                padx=(0, 18),
                pady=5,
            )

        enabled_var = tk.BooleanVar(
            value=bool(config.get("enabled"))
            or any(variable.get() for variable in action_vars.values())
        )
        footer = ttk.Frame(dialog, padding=(20, 10, 20, 14))
        footer.grid(row=2, column=0, columnspan=2, sticky="ew")
        tk.Checkbutton(
            footer,
            text="保存并启用该配置",
            variable=enabled_var,
            bg="#f4f2eb",
            fg=TEXT,
            activebackground="#f4f2eb",
            selectcolor="white",
            bd=0,
        ).pack(side="left")
        save_button = button(footer, "保存配置", lambda: save())
        save_button.pack(side="right")
        button(footer, "取消", dialog.destroy).pack(
            side="right",
            padx=6,
        )

        def save() -> None:
            try:
                promotion_commission = self._bounded_number(
                    promotion_commission_var.get(),
                    0,
                    100,
                    "联盟佣金",
                )
                influencer_count = self._bounded_integer(
                    influencer_count_var.get(),
                    1,
                    100000,
                    "达人数",
                )
                listing_count = self._bounded_integer(
                    listing_count_var.get(),
                    1,
                    200,
                    "上架数量",
                )
                modify_commission = self._bounded_number(
                    commission_var.get(),
                    0,
                    100,
                    "佣金比例",
                )
                flash_discount = self._bounded_number(
                    flash_discount_var.get(),
                    0.1,
                    0.85,
                    "折扣倍率",
                )
                price_multiplier = self._bounded_number(
                    price_multiplier_var.get(),
                    0.01,
                    100,
                    "价格倍率",
                )
                no_traffic_days = self._bounded_integer(
                    no_traffic_days_var.get(),
                    1,
                    365,
                    "无流量天数",
                )
                no_traffic_sales = self._bounded_integer(
                    no_traffic_sales_var.get(),
                    0,
                    1000000,
                    "最高销量",
                )
            except ValueError as exc:
                messagebox.showwarning(
                    "配置数值",
                    str(exc),
                    parent=dialog,
                )
                return
            common = {
                **{key: variable.get() for key, variable in action_vars.items()},
                "enabled": enabled_var.get(),
                "promotion_commission_pct": promotion_commission,
                "influencer_name": influencer_name_var.get().strip(),
                "influencer_phone": influencer_phone_var.get().strip(),
                "influencer_wechat": influencer_wechat_var.get().strip(),
                "influencer_count": influencer_count,
                "influencer_message": influencer_message_var.get().strip(),
                "influencer_category": influencer_category_var.get().strip(),
                "influencer_follower_filter": (influencer_follower_var.get().strip()),
                "influencer_gmv_filter": influencer_gmv_var.get().strip(),
                "influencer_live": influencer_live_var.get(),
                "influencer_video": influencer_video_var.get(),
                "influencer_public_account": influencer_public_var.get(),
                "listing_mode": ("api" if listing_mode_var.get() == "API上架" else "manual"),
                "listing_count": listing_count,
                "modify_commission_pct": modify_commission,
                "flash_sale_mode": flash_mode_var.get(),
                "flash_sale_discount": flash_discount,
                "price_mode": price_mode_var.get(),
                "price_multiplier": price_multiplier,
                "no_traffic_days": no_traffic_days,
                "no_traffic_max_sales": no_traffic_sales,
                "no_traffic_method": str(config.get("no_traffic_method") or "recycle"),
            }
            def saved(count: int) -> None:
                if dialog.winfo_exists():
                    dialog.destroy()
                self.refresh()
                self.window.set_status(
                    f"已保存 {count} 家店铺托管配置"
                )

            _run_hosted_config_save(
                self.window,
                self.db,
                stores,
                common,
                str(common["listing_mode"]),
                int(common["listing_count"]),
                on_done=saved,
                save_button=save_button,
            )

        dialog.bind("<Escape>", lambda _event: dialog.destroy())
        dialog.grab_set()

    @staticmethod
    def _field_grid(
        parent: ttk.LabelFrame,
        row: int,
        label: str,
        variable: tk.StringVar,
        *,
        values: tuple[str, ...] = (),
        suffix: str = "",
    ) -> None:
        ttk.Label(
            parent,
            text=label,
            style="HostedField.TLabel",
        ).grid(row=row, column=0, sticky="w", pady=5)
        if values:
            widget: tk.Widget = ttk.Combobox(
                parent,
                textvariable=variable,
                values=values,
                state="readonly",
                width=18,
            )
        else:
            widget = ttk.Entry(
                parent,
                textvariable=variable,
                width=22,
            )
        widget.grid(row=row, column=1, sticky="ew", padx=(10, 6), pady=5)
        parent.columnconfigure(1, weight=1)
        ttk.Label(
            parent,
            text=suffix or " ",
            style="HostedField.TLabel",
            foreground=MUTED,
        ).grid(row=row, column=2, sticky="w", pady=5)

    @staticmethod
    def _bounded_number(
        value: str,
        minimum: float,
        maximum: float,
        label: str,
    ) -> float:
        try:
            number = float(value)
        except ValueError as exc:
            raise ValueError(f"{label}必须是数字。") from exc
        if not minimum <= number <= maximum:
            raise ValueError(f"{label}必须在 {minimum}–{maximum} 之间。")
        return number

    @classmethod
    def _bounded_integer(
        cls,
        value: str,
        minimum: int,
        maximum: int,
        label: str,
    ) -> int:
        number = cls._bounded_number(value, minimum, maximum, label)
        if int(number) != number:
            raise ValueError(f"{label}必须是整数。")
        return int(number)

    def _execute_all(self) -> None:
        rows = self._selected_rows()
        if not rows:
            messagebox.showinfo(
                "请选择店铺",
                "请先选择至少一家托管店铺。",
                parent=self,
            )
            return
        self._execute_rows(rows, scheduled=False)

    def _execute_configured(self) -> None:
        rows = []
        for store in self.db.list_stores():
            config = dict(self.db.get_hosted_config(store.id) or {})
            if config.get("enabled"):
                rows.append({"store": store, "config": config})
        if not rows:
            messagebox.showinfo(
                "没有启用配置",
                "请先保存并启用托管配置。",
                parent=self,
            )
            return
        self._execute_rows(rows, scheduled=False)

    def _execute_rows(
        self,
        rows: list[dict[str, Any]],
        *,
        scheduled: bool,
    ) -> None:
        if not scheduled and not messagebox.askyesno(
            "执行托管",
            f"即将执行 {len(rows)} 家店铺的托管项目。\n"
            "支持的微信写入动作会在执行前再次检查登录态和商品状态。",
            parent=self,
        ):
            return
        self.window.set_status("正在执行托管项目…", busy=True)

        def execute_one(item: dict[str, Any]) -> str:
            store = item["store"]
            config = item["config"]
            unsupported = [
                label
                for key, label in HOSTED_ACTIONS
                if key in UNSUPPORTED_ACTIONS and config.get(key)
            ]
            if unsupported:
                return f"{store.name}: 以下项目尚未接入接口：" + "、".join(unsupported)
            actions: list[str] = []
            try:
                blacklist = self.db.violation_blacklist_ids()
                products = [
                    row
                    for row in self.db.list_products(store_id=store.id)
                    if str(row.get("product_id")) not in blacklist
                ]
                active_products = [row for row in products if int(row.get("status") or 0) == 5][:20]
                action_errors: list[str] = []
                if config.get("add_affiliate"):
                    promoted = self.window.service.promote_products_to_league(
                        store,
                        [str(row["product_id"]) for row in active_products],
                        float(config.get("promotion_commission_pct") or 20),
                    )
                    result_rows = promoted.get("result_info_list") or []
                    failed = sum(
                        1
                        for row in result_rows
                        if isinstance(row, dict) and int(row.get("errcode") or 0) != 0
                    )
                    if int(promoted.get("errcode") or 0) != 0:
                        action_errors.append(
                            "添加联盟推广：" + str(promoted.get("errmsg") or "失败")
                        )
                    actions.append(
                        f"添加联盟推广 {len(result_rows) - failed}"
                        + (f"，失败 {failed}" if failed else "")
                    )
                if config.get("latest_affiliate"):
                    league = self.window.service.fetch_league_products(store)
                    league_count = len(league.get("items") or [])
                    self.db.set_setting(
                        f"hosted_latest_league_{store.id}",
                        str(league_count),
                    )
                    actions.append(f"读取联盟商品 {league_count}")
                if config.get("modify_commission"):
                    league = self.window.service.fetch_league_products(store)
                    league_ids = [
                        str(row.get("product_id") or "")
                        for row in league.get("items") or []
                        if isinstance(row, dict) and row.get("product_id")
                    ][:20]
                    commission_errors = self.window.service.update_league_ratio(
                        store,
                        league_ids,
                        float(config.get("modify_commission_pct") or 20),
                    )
                    action_errors.extend(f"修改佣金：{error}" for error in commission_errors)
                    actions.append(f"修改佣金 {len(league_ids) - len(commission_errors)}")
                if config.get("revoke_audit"):
                    ids = [
                        str(row["product_id"])
                        for row in products
                        if int(row.get("status") or 0) == 2
                    ][:500]
                    for product_id in ids:
                        self.window.service.product_write(
                            store,
                            "cancel_audit",
                            product_id=product_id,
                        )
                    actions.append(f"撤销审核 {len(ids)}")
                if config.get("delete_audit_failed"):
                    ids = [
                        str(row["product_id"])
                        for row in products
                        if int(row.get("status") or 0) == 3
                    ][:500]
                    for product_id in ids:
                        self.window.service.product_write(
                            store,
                            "delete",
                            product_id=product_id,
                        )
                    actions.append(f"删除审核失败 {len(ids)}")
                if config.get("list_products"):
                    listing_count = max(
                        1,
                        int(config.get("listing_count") or 200),
                    )
                    ids = [
                        str(row["product_id"])
                        for row in products
                        if int(row.get("status") or 0) == 11
                    ][:listing_count]
                    if ids:
                        self.window.service.set_products_listing(
                            store,
                            ids,
                            True,
                        )
                    actions.append(f"上架 {len(ids)}")
                if config.get("flash_sale"):
                    try:
                        flash_result = self.window.service.create_snapup_activity(
                            store,
                            [str(row["product_id"]) for row in active_products],
                            float(config.get("flash_sale_discount") or 0.85),
                            create_all=True,
                        )
                        actions.append(
                            f"创建 {int(flash_result.get('activity_count') or 0)} "
                            "场限时活动，商品 "
                            f"{int(flash_result.get('product_count') or 0)} 个"
                        )
                    except Exception as exc:
                        action_errors.append(f"限时抢购：{exc}")
                if config.get("modify_price"):
                    price_errors = self.window.service.update_product_prices(
                        store,
                        [str(row["product_id"]) for row in active_products],
                        float(config.get("price_multiplier") or 1.1),
                    )
                    action_errors.extend(f"修改价格：{error}" for error in price_errors)
                    actions.append(f"修改价格 {len(active_products) - len(price_errors)}")
                if config.get("delete_no_traffic"):
                    ids = _hosted_no_traffic_product_ids(
                        products,
                        self.db.get_product_metrics_map(store.id),
                        days=max(
                            1,
                            int(config.get("no_traffic_days") or 7),
                        ),
                        max_sales=max(
                            0,
                            int(config.get("no_traffic_max_sales") or 0),
                        ),
                    )
                    if ids:
                        self.window.service.private_batch_product_action(
                            store,
                            ids,
                            str(config.get("no_traffic_method") or "recycle"),
                        )
                    actions.append(f"清理无流量 {len(ids)}")
                if config.get("cancel_flash_sale"):
                    cancel_errors = self.window.service.stop_active_limited_discounts(store)
                    action_errors.extend(f"取消限时抢购：{error}" for error in cancel_errors)
                    actions.append(
                        "取消限时抢购" + (f"，失败 {len(cancel_errors)}" if cancel_errors else "")
                    )
                if config.get("prefill_11"):
                    try:
                        drafts = json.loads(
                            self.db.get_setting(
                                f"double11_prefill_{store.id}",
                                "[]",
                            )
                        )
                    except (TypeError, ValueError):
                        drafts = []
                    if isinstance(drafts, list) and drafts:
                        self.window.service.double11_submit(store, drafts)
                        actions.append(f"双11预填报 {len(drafts)}")
                    else:
                        actions.append("双11预填报：无草稿")
                self.db.log_hosted_run(
                    store.id,
                    "、".join(actions) or "无动作",
                    "failed" if action_errors else "success",
                    (
                        ("定时执行" if scheduled else "手动执行")
                        + ("；" + "；".join(action_errors) if action_errors else "")
                    ),
                )
                return f"{store.name}: " + "；".join(action_errors) if action_errors else ""
            except Exception as exc:
                self.db.log_hosted_run(
                    store.id,
                    "、".join(actions) or "执行失败",
                    "failed",
                    str(exc),
                )
                return f"{store.name}: {exc}"

        def work(_progress) -> list[str]:
            errors = []
            with ThreadPoolExecutor(
                max_workers=min(5, max(1, len(rows))),
                thread_name_prefix="hosted-store",
            ) as executor:
                futures = [executor.submit(execute_one, item) for item in rows]
                for future in as_completed(futures):
                    error = future.result()
                    if error:
                        errors.append(error)
            return errors

        def done(errors: list[str]) -> None:
            success = max(0, len(rows) - len(errors))
            self.result_var.set(f"执行结果：成功 {success}，失败 {len(errors)}，跳过 0")
            self.window.set_status(f"托管执行完成：成功 {len(rows) - len(errors)} 家")
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
            lambda error: messagebox.showerror(
                "托管执行失败",
                error,
                parent=self,
            ),
            log_title="执行托管店铺任务",
            task_scope="hosted:execute",
        )

    def _open_timed_dialog(self) -> None:
        stores = self.db.list_stores()
        dialog = tk.Toplevel(self)
        dialog.title("定时执行")
        dialog.transient(self.winfo_toplevel())
        dialog.geometry("760x720")
        dialog.minsize(680, 620)
        dialog.resizable(False, False)
        dialog.configure(bg="#f5f6f7")
        panel = ttk.Frame(dialog, style="TFrame", padding=(20, 14, 20, 14))
        panel.pack(fill="both", expand=True)
        enabled = tk.BooleanVar(value=self.timed_enabled)
        execution_time = tk.StringVar(value=self.schedule_time_var.get())
        ttk.Label(
            panel,
            text="定时执行",
            style="Title.TLabel",
        ).pack(anchor="w")
        ttk.Label(
            panel,
            text="按每天设定时间执行所选店铺已配置任务",
            style="Subtitle.TLabel",
        ).pack(anchor="w", pady=(2, 12))
        tk.Checkbutton(
            panel,
            text="开启定时执行",
            variable=enabled,
            bg="#f5f6f7",
            fg=TEXT,
            activebackground="#f5f6f7",
            selectcolor="white",
            bd=0,
        ).pack(anchor="w", pady=(0, 10))

        time_card = ttk.LabelFrame(
            panel,
            text="每天执行时间",
            padding=12,
            style="HostedCard.TLabelframe",
        )
        time_card.pack(fill="x", pady=(0, 10))
        time_row = ttk.Frame(time_card)
        time_row.pack(fill="x")
        ttk.Label(
            time_row,
            text="执行时间",
            style="HostedField.TLabel",
        ).pack(side="left")
        ttk.Entry(
            time_row,
            textvariable=execution_time,
            width=14,
        ).pack(side="left", padx=(8, 14))
        ttk.Label(
            time_row,
            text="执行时会按照最多 5 家店铺并发，并只执行各店铺已勾选的功能。",
            style="HostedHint.TLabel",
            foreground=MUTED,
        ).pack(side="left")

        stores_card = ttk.LabelFrame(
            panel,
            text="执行店铺",
            padding=10,
            style="HostedCard.TLabelframe",
        )
        stores_card.pack(fill="both", expand=True, pady=(0, 10))
        select_all_timed = tk.BooleanVar(value=False)
        select_all_widget = tk.Checkbutton(
            stores_card,
            text="全选",
            variable=select_all_timed,
            bg="#ffffff",
            fg=TEXT,
            activebackground="#ffffff",
            selectcolor="white",
            bd=0,
        )
        select_all_widget.pack(anchor="w", pady=(0, 6))
        list_shell = ttk.Frame(stores_card, style="Panel.TFrame")
        list_shell.pack(fill="both", expand=True)
        list_canvas = tk.Canvas(
            list_shell,
            bg="#ffffff",
            highlightthickness=0,
        )
        list_scroll = ttk.Scrollbar(
            list_shell,
            orient="vertical",
            command=list_canvas.yview,
        )
        list_canvas.configure(yscrollcommand=list_scroll.set)
        list_canvas.pack(side="left", fill="both", expand=True)
        list_scroll.pack(side="right", fill="y")
        store_list = ttk.Frame(list_canvas, style="Panel.TFrame")
        store_window = list_canvas.create_window(
            (0, 0),
            window=store_list,
            anchor="nw",
        )
        store_list.bind(
            "<Configure>",
            lambda _event: list_canvas.configure(scrollregion=list_canvas.bbox("all")),
        )
        list_canvas.bind(
            "<Configure>",
            lambda event: list_canvas.itemconfigure(
                store_window,
                width=event.width,
            ),
        )
        store_list.columnconfigure(0, weight=1)
        store_list.columnconfigure(1, weight=1)
        store_vars: dict[int, tk.BooleanVar] = {}
        for index, store in enumerate(stores):
            variable = tk.BooleanVar(value=store.id in self.schedule_store_ids)
            store_vars[store.id] = variable
            row = index // 2
            column = index % 2
            item = ttk.Frame(store_list, style="Panel.TFrame")
            item.grid(
                row=row,
                column=column,
                sticky="ew",
                padx=(0, 10),
                pady=3,
            )
            tk.Checkbutton(
                item,
                text=store.name,
                variable=variable,
                bg=PANEL,
                fg=TEXT,
                activebackground=PANEL,
                selectcolor="white",
                bd=0,
            ).pack(side="left")
            configured = bool(self.db.get_hosted_config(store.id))
            tk.Label(
                item,
                text="已配置" if configured else "未配置",
                bg=PANEL,
                fg=MUTED,
                font=("Microsoft YaHei UI", 8),
            ).pack(side="right")

        def toggle_timed_all() -> None:
            value = select_all_timed.get()
            for variable in store_vars.values():
                variable.set(value)

        select_all_widget.configure(command=toggle_timed_all)

        status_text = tk.StringVar(
            value=(
                f"定时执行当前为开启状态 · {execution_time.get()}"
                if self.timed_enabled
                else "定时执行当前为关闭状态"
            )
        )
        ttk.Label(
            panel,
            textvariable=status_text,
            style="Subtitle.TLabel",
        ).pack(anchor="w", pady=(2, 8))

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
            self.timed_enabled = enabled.get()
            self.schedule_time_var.set(value)
            self.schedule_store_ids = {
                store_id for store_id, variable in store_vars.items() if variable.get()
            }
            status_text.set(
                f"定时执行当前为开启状态 · {value}"
                if self.timed_enabled
                else "定时执行当前为关闭状态"
            )
            self.db.set_setting(
                "hosted_schedule_enabled",
                "1" if self.timed_enabled else "0",
            )
            self.db.set_setting("hosted_schedule_time", value)
            self.db.set_setting(
                "hosted_schedule_stores",
                json.dumps(sorted(self.schedule_store_ids)),
            )
            dialog.destroy()
            self.refresh()
            if self.timed_enabled:
                self._schedule_timed_tick(1000)
            elif self._timed_after_id is not None:
                self.after_cancel(self._timed_after_id)
                self._timed_after_id = None

        actions = ttk.Frame(panel, style="TFrame")
        actions.pack(anchor="e", pady=(8, 0))
        button(actions, "取消", dialog.destroy).pack(side="left", padx=6)
        button(actions, "保存配置", save, "Primary").pack(side="left")
        dialog.grab_set()

    def _schedule_timed_tick(self, delay_ms: int) -> None:
        if self._timed_after_id is not None:
            self.after_cancel(self._timed_after_id)
        self._timed_after_id = self.after(delay_ms, self._timed_tick)

    def _timed_tick(self) -> None:
        self._timed_after_id = None
        if not self.timed_enabled:
            return
        now = datetime.now()
        if now.strftime("%H:%M") != self.schedule_time_var.get():
            self._schedule_timed_tick(30000)
            return
        if self.db.get_setting("hosted_last_run_date", "") == now.date().isoformat():
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
                "hosted_last_run_date",
                now.date().isoformat(),
            )
            self._execute_rows(rows, scheduled=True)
        self._schedule_timed_tick(30000)
