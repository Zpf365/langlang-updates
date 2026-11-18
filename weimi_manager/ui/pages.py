from __future__ import annotations

import json
import re
import threading
import tkinter as tk
import urllib.parse
from collections.abc import Callable
from dataclasses import asdict
from datetime import date, datetime, timedelta
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import TYPE_CHECKING, Any

from ..diagnostics import redact_text
from ..exporter import export_csv, export_xlsx
from ..models import (
    SHIPMENT_STATUS,
    Order,
    OrderItem,
    ProductQueryResult,
    Store,
    is_unclaimed_gift_order,
    product_matches_tab,
)
from ..service import date_range
from ..updater import DEFAULT_UPDATE_SOURCE
from .dialogs import (
    CookieImportDialog,
    OrderAddressDialog,
    OrderDetailDialog,
    OrderPriceDialog,
    ProductDetailDialog,
    ProductFormDialog,
    ProductJsonDialog,
    ProductOperationsDialog,
    ShipmentDialog,
    ShippingConfigDialog,
    SimpleFieldsDialog,
    StoreDialog,
)
from .order_action_dialogs import (
    ManualShipmentDialog,
    ProductTitleEditDialog,
    PurchaseInfoDialog,
)
from .order_flag_dialog import OrderFlagNoteDialog
from .product_card_list import ProductCardList
from .product_link_dialog import ProductPurchaseLinkDialog
from .store_page import StorePage  # noqa: F401
from .theme import MUTED, PANEL, TEXT, button
from .virtual_order_card_list import ORDER_COLUMN_BOUNDARIES, OrderCardList

if TYPE_CHECKING:
    from .app import MainWindow


def _format_time(timestamp: int | None) -> str:
    if not timestamp:
        return "—"
    return date.fromtimestamp(timestamp).strftime("%Y-%m-%d")


def _format_datetime(timestamp: int | None) -> str:
    if not timestamp:
        return "—"
    return date.fromtimestamp(timestamp).strftime("%Y-%m-%d")


def _money(cents: int | None) -> str:
    return f"¥{(cents or 0) / 100:.2f}"


class BasePage(ttk.Frame):
    title = ""
    subtitle = ""

    def __init__(self, parent: tk.Widget, window: "MainWindow"):
        super().__init__(parent, style="TFrame", padding=(24, 20, 24, 14))
        self.window = window
        self.db = window.db

    def refresh(self) -> None:
        pass


class LegacyStorePage(BasePage):
    title = "店铺管理"
    subtitle = "配置微信小店 AppID/AppSecret，管理本地店铺与同步状态"
    field_definitions = (
        ("selected", "选", 36),
        ("sequence", "序号", 55),
        ("name", "店铺名称", 130),
        ("group", "分组", 85),
        ("app_id", "AppID", 190),
        ("secret", "AppSecret", 80),
        ("in_sale", "在售", 65),
        ("quota", "提审额度", 80),
        ("today", "今日成交", 85),
        ("week", "7日成交", 85),
        ("month", "30日成交", 85),
        ("login", "登录状态", 90),
        ("buyer_30d", "30日买家", 80),
        ("today_orders", "今日订单", 80),
        ("product_click_30d", "商品点击", 80),
        ("visitors_30d", "30日访客", 80),
        ("today_refund", "今日退款", 90),
        ("aftersale", "售后", 65),
        ("warn", "店铺预警", 80),
        ("violation", "店铺违规", 80),
        ("cash", "可提现", 90),
        ("frozen", "暂预留", 85),
        ("settle", "待结算", 85),
        ("deposit", "保证金", 85),
        ("dsr", "体验分", 75),
        ("score_product", "商品体验", 80),
        ("score_deliver", "物流体验", 80),
        ("score_service", "服务体验", 80),
    )

    def __init__(self, parent: tk.Widget, window: "MainWindow"):
        super().__init__(parent, window)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(3, weight=1)
        self.search_var = tk.StringVar()
        self.product_platform_var = tk.StringVar(value="全部平台")
        self.product_status_filter_var = tk.StringVar(value="销售中")
        self.sales_min_var = tk.StringVar()
        self.sales_max_var = tk.StringVar()
        self.price_min_var = tk.StringVar()
        self.price_max_var = tk.StringVar()
        self.relation_var = tk.StringVar(value="全部")
        self.ignore_product_var = tk.StringVar(value="未忽略")
        self.product_name_filter_var = tk.StringVar()
        self.product_id_filter_var = tk.StringVar()
        self.product_group_filter_var = tk.StringVar(value="全部商品分组")
        self.filter_var = tk.StringVar(value="全部店铺")
        self.group_var = tk.StringVar(value="全部分组")
        self.footer_var = tk.StringVar(value="在售 0    仓库商品 0    今日成交 ¥0.00")
        self.status_line_var = tk.StringVar(value="勾选店铺可批量操作；双击店铺可编辑")
        self.select_all_var = tk.BooleanVar(value=False)
        self.selected_store: Store | None = None
        self.active_filter = "all"
        self.filter_buttons: dict[str, ttk.Button] = {}
        self._visible_fields = [key for key, _label, _width in self.field_definitions]
        self.sort_field = "sequence"
        self.sort_desc = False
        self._compact_button: ttk.Button | None = None
        self._full_button: ttk.Button | None = None
        self._field_button: ttk.Button | None = None

        toolbar = ttk.Frame(self, style="TFrame")
        toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        ttk.Label(
            toolbar,
            text="凭据使用 Windows DPAPI 加密；网页登录用于官方商家后台",
            style="Subtitle.TLabel",
        ).pack(side="left")
        button(toolbar, "刷新数据", self._refresh_store_data).pack(side="right")
        button(toolbar, "扫码登录", self._scan_login).pack(side="right", padx=6)
        button(toolbar, "批量登录", self._batch_login).pack(side="right")
        button(toolbar, "添加店铺", self._add_store, "Primary").pack(
            side="right",
            padx=(0, 6),
        )

        view_bar = ttk.Frame(self, style="TFrame")
        view_bar.grid(row=1, column=0, sticky="ew", pady=(0, 8))
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
            width=15,
        )
        self.group_combo.pack(side="left")
        self.group_combo.bind("<<ComboboxSelected>>", lambda _event: self.refresh())
        tk.Checkbutton(
            view_bar,
            text="全选",
            variable=self.select_all_var,
            command=self._toggle_store_selection,
            bg="#f4f2eb",
            fg=TEXT,
            activebackground="#f4f2eb",
            selectcolor="white",
            bd=0,
        ).pack(side="left", padx=(12, 5))
        ttk.Label(
            view_bar,
            textvariable=self.status_line_var,
            style="Subtitle.TLabel",
        ).pack(side="left", padx=8)

        column_bar = ttk.Frame(self, style="TFrame")
        column_bar.grid(row=2, column=0, sticky="ew", pady=(0, 7))
        ttk.Label(column_bar, text="列显示").pack(side="left")
        self._compact_button = button(
            column_bar,
            "精简",
            lambda: self._set_columns(compact=True),
        )
        self._compact_button.pack(
            side="left",
            padx=(5, 4),
        )
        self._full_button = button(
            column_bar,
            "完整",
            lambda: self._set_columns(compact=False),
        )
        self._full_button.pack(
            side="left"
        )
        self._field_button = button(
            column_bar,
            f"字段设置({len(self.field_definitions)})",
            self._show_field_settings,
        )
        self._field_button.pack(side="left", padx=6)
        button(column_bar, "清理缓存", self._clear_store_cache, "Danger").pack(
            side="right",
            padx=(6, 0),
        )
        button(column_bar, "删除", self._delete_store, "Danger").pack(
            side="right",
            padx=(6, 0),
        )
        button(column_bar, "编辑", self._edit_store).pack(side="right", padx=(6, 0))
        button(column_bar, "打开后台", self._open_store_backend).pack(
            side="right",
            padx=(6, 0),
        )

        table_frame = ttk.Frame(self, style="Panel.TFrame")
        table_frame.grid(row=3, column=0, sticky="nsew")
        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)
        columns = tuple(key for key, _label, _width in self.field_definitions)
        self.tree = ttk.Treeview(
            table_frame,
            columns=columns,
            show="headings",
            selectmode="extended",
        )
        for key, text, width in self.field_definitions:
            self.tree.heading(key, text=text)
            self.tree.column(
                key,
                width=width,
                minwidth=50,
                anchor=(
                    "center"
                    if key in {"selected", "sequence", "in_sale", "quota"}
                    else "w"
                ),
            )
            self.tree.heading(
                key,
                text=text,
                command=lambda field=key: self._sort_by(field),
            )
        self.tree.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        x_scroll = ttk.Scrollbar(table_frame, orient="horizontal", command=self.tree.xview)
        x_scroll.grid(row=1, column=0, sticky="ew")
        self.tree.configure(yscrollcommand=scroll.set, xscrollcommand=x_scroll.set)
        self.tree.tag_configure("offline", foreground="#b54335")
        self.tree.tag_configure("warning", foreground="#947229")
        self.tree.tag_configure("online", foreground="#16785e")
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.tree.bind("<Double-1>", lambda _event: self._edit_store())
        self.tree.bind("<Button-1>", self._toggle_checkbox_column, add="+")
        self.tree.bind("<Button-3>", self._show_store_menu)

        footer = ttk.Frame(self, style="TFrame")
        footer.grid(row=4, column=0, sticky="ew", pady=(8, 0))
        ttk.Label(
            footer,
            textvariable=self.footer_var,
            style="Subtitle.TLabel",
        ).pack(side="left")

    def refresh(self) -> None:
        self.tree.delete(*self.tree.get_children())
        all_stores = self.db.list_stores(self.search_var.get())
        groups = sorted(
            set(self.db.list_store_groups())
            | {store.group_name for store in all_stores if store.group_name}
        )
        self.group_combo["values"] = ["全部分组", *groups]
        if self.group_var.get() not in self.group_combo["values"]:
            self.group_var.set("全部分组")
        stores = []
        for store in all_stores:
            if self.active_filter == "logged" and store.web_status != "已登录":
                continue
            if self.active_filter == "unlogged" and store.web_status in {"已登录", "检测中"}:
                continue
            if (
                self.group_var.get() != "全部分组"
                and store.group_name != self.group_var.get()
            ):
                continue
            stores.append(store)
        totals = {
            "in_sale": 0,
            "warehouse": 0,
            "today_revenue": 0,
            "today_orders": 0,
            "week_revenue": 0,
            "month_revenue": 0,
            "logged": 0,
        }
        for index, store in enumerate(stores, 1):
            metrics = self.db.store_summary(store.id)
            for key in totals:
                if key in metrics:
                    totals[key] += int(metrics[key] or 0)
            if store.web_status == "已登录":
                totals["logged"] += 1
            tags = ("online",) if store.web_status == "已登录" else ()
            if store.web_status in {"已掉线", "需重新扫码"}:
                tags = ("offline",)
            elif store.web_status in {"网络异常", "连接异常", "平台异常"}:
                tags = ("warning",)
            self.tree.insert(
                "",
                "end",
                iid=str(store.id),
                values=(
                    index,
                    store.name,
                    store.group_name or "未分组",
                    store.app_id,
                    "********",
                    metrics["in_sale"],
                    "—",
                    _money(metrics["today_revenue"]),
                    _money(metrics["week_revenue"]),
                    _money(metrics["month_revenue"]),
                    store.web_status,
                ),
                tags=tags,
            )
        for key, label in (
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
        self.status_line_var.set(
            f"经营数据已更新（后台登录 {totals['logged']} 家 / "
            f"当前列表 {len(stores)} 家）"
        )
        self.footer_var.set(
            f"在售商品 {totals['in_sale']}    仓库商品 {totals['warehouse']}    "
            f"提审额度 —    今日成交 {_money(totals['today_revenue'])}    "
            f"今日订单 {totals['today_orders']}    可提现 —    "
            f"待结算 —    保证金 —"
        )
        self.select_all_var.set(False)
        self.selected_store = None

    def _on_select(self, _event=None) -> None:
        selection = self.tree.selection()
        self.selected_store = self.db.get_store(int(selection[0])) if selection else None
        if selection:
            self.status_line_var.set(f"已选择 {len(selection)} 家店铺")
        else:
            self.status_line_var.set("勾选店铺可批量操作；双击店铺可编辑")

    def _change_filter(self, key: str) -> None:
        self.active_filter = key
        self.refresh()

    def _toggle_store_selection(self) -> None:
        items = tuple(self.tree.get_children())
        self.tree.selection_set(items if self.select_all_var.get() else ())
        self._on_select()

    def _selected_stores(self) -> list[Store]:
        stores = []
        for iid in self.tree.selection():
            store = self.db.get_store(int(iid))
            if store is not None:
                stores.append(store)
        return stores

    def _set_columns(self, compact: bool) -> None:
        if compact:
            self._visible_fields = ["sequence", "name", "group", "in_sale", "login"]
        else:
            self._visible_fields = [
                key for key, _label, _width in self.field_definitions
            ]
        self.tree["displaycolumns"] = tuple(self._visible_fields)

    def _show_field_settings(self) -> None:
        dialog = tk.Toplevel(self)
        dialog.title("字段设置")
        dialog.transient(self.winfo_toplevel())
        dialog.resizable(False, False)
        variables: dict[str, tk.BooleanVar] = {}
        for key, label, _width in self.field_definitions:
            variable = tk.BooleanVar(value=key in self._visible_fields)
            variables[key] = variable
            tk.Checkbutton(
                dialog,
                text=label,
                variable=variable,
                anchor="w",
            ).pack(fill="x", padx=18, pady=3)

        def apply_fields() -> None:
            visible = [
                key for key, _label, _width in self.field_definitions
                if variables[key].get()
            ]
            if not visible:
                messagebox.showwarning("字段设置", "至少保留一个字段。", parent=dialog)
                return
            self._visible_fields = visible
            self.tree["displaycolumns"] = tuple(visible)
            dialog.destroy()

        ttk.Button(dialog, text="应用", command=apply_fields).pack(pady=12)
        dialog.grab_set()

    def _batch_login(self) -> None:
        stores = self._selected_stores() or self.db.list_stores()
        if not stores:
            messagebox.showinfo("没有店铺", "请先添加店铺。", parent=self)
            return
        self.window.set_status(f"正在依次登录 {len(stores)} 家店铺…", busy=True)

        def work(_progress) -> list[str]:
            errors = []
            for store in stores:
                try:
                    self.window.web_session.interactive_login(store)
                except Exception as exc:
                    errors.append(f"{store.name}: {exc}")
            return errors

        def done(errors: list[str]) -> None:
            self.window.set_status(
                f"批量登录完成，成功 {len(stores) - len(errors)} 家"
            )
            self.window.store_data_changed()
            if errors:
                messagebox.showwarning(
                    "部分店铺登录失败",
                    "\n".join(errors[:8]),
                    parent=self,
                )

        self.window.run_task(
            work,
            done,
            lambda error: messagebox.showerror("批量登录失败", error, parent=self),
            log_title="批量扫码登录店铺",
        )

    def _open_store_backend(self) -> None:
        stores = self._selected_stores()
        if not stores:
            messagebox.showinfo("请选择店铺", "请先选择一家店铺。", parent=self)
            return
        store = stores[0]
        self.window.set_status(f"正在打开“{store.name}”的微信后台…", busy=True)

        def done(_result) -> None:
            self.window.set_status("微信商家后台已关闭")
            self.window.store_data_changed()

        self.window.run_task(
            lambda _progress: self.window.web_session.open_official_backend(store),
            done,
            lambda error: messagebox.showerror("打开后台失败", error, parent=self),
            log_title="打开官方商家后台",
        )

    def _refresh_store_data(self) -> None:
        stores = self._selected_stores() or self.db.list_stores()
        if not stores:
            messagebox.showinfo("没有店铺", "请先添加店铺。", parent=self)
            return
        self.window.set_status(f"正在刷新 {len(stores)} 家店铺数据…", busy=True)
        now = int(datetime.now().timestamp())

        def work(_progress) -> list[str]:
            errors = []
            for store in stores:
                try:
                    self.window.service.validate_store(store)
                    self.window.service.sync_store_profile(store)
                    self.window.service.sync_analytics_day(
                        store,
                        date.today().isoformat(),
                    )
                    self.window.service.sync_orders(
                        store,
                        now - 7 * 86400,
                        now,
                    )
                except Exception as exc:
                    errors.append(f"{store.name}: {exc}")
            return errors

        def done(errors: list[str]) -> None:
            self.window.set_status(
                f"店铺数据刷新完成，成功 {len(stores) - len(errors)} 家"
            )
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
            lambda error: messagebox.showerror("店铺数据刷新失败", error, parent=self),
            log_title="刷新店铺资料、经营数据和订单",
        )

    def _clear_store_cache(self) -> None:
        stores = self._selected_stores()
        if not stores:
            messagebox.showinfo("请选择店铺", "请先选择一家店铺。", parent=self)
            return
        store = stores[0]
        if not messagebox.askyesno(
            "清理缓存",
            f"确定清理“{store.name}”的本地订单、商品、售后和经营数据缓存吗？\n"
            "店铺凭据和网页登录状态会保留。",
            parent=self,
        ):
            return
        self.db.clear_store_cache(store.id)
        self.window.store_data_changed()
        self.window.set_status(f"已清理“{store.name}”的本地业务缓存")

    def _add_store(self) -> None:
        StoreDialog(self, self._create_store)

    def _create_store(self, values: dict[str, str]) -> None:
        try:
            self.db.add_store(
                values["name"],
                values["app_id"],
                values["secret"],
                values["group_name"],
            )
        except Exception as exc:
            messagebox.showerror("添加失败", str(exc), parent=self)
            return
        self.window.store_data_changed()
        self.window.set_status("店铺已添加，密钥已加密保存")

    def _add_demo(self) -> None:
        try:
            self.db.add_store("演示店铺", "demo-store", "local-demo-only", "示例分组")
        except Exception as exc:
            if "UNIQUE" not in str(exc):
                messagebox.showerror("添加失败", str(exc), parent=self)
                return
            messagebox.showinfo("提示", "演示店铺已经存在。", parent=self)
            return
        self.window.store_data_changed()
        self.window.set_status("演示店铺已创建，可在订单和商品页面同步")

    def _edit_store(self) -> None:
        if not self.selected_store:
            messagebox.showinfo("请选择店铺", "请先选择一家店铺。", parent=self)
            return
        store = self.selected_store

        def save(values: dict[str, str]) -> None:
            self.db.update_store(
                store.id,
                values["name"],
                values["group_name"],
                values["secret"] or None,
            )
            self.window.store_data_changed()
            self.window.set_status("店铺信息已更新")

        StoreDialog(self, save, store)

    def _delete_store(self) -> None:
        if not self.selected_store:
            messagebox.showinfo("请选择店铺", "请先选择一家店铺。", parent=self)
            return
        store = self.selected_store
        if not messagebox.askyesno(
            "删除店铺",
            f"确定删除“{store.name}”吗？\n本地订单和商品缓存也会一并删除。",
            parent=self,
        ):
            return
        self.db.delete_store(store.id)
        self.window.store_data_changed()
        self.window.set_status("店铺及本地缓存已删除")

    def _validate_store(self) -> None:
        if not self.selected_store:
            messagebox.showinfo("请选择店铺", "请先选择一家店铺。", parent=self)
            return
        store = self.selected_store
        self.window.set_status("正在验证店铺连接…", busy=True)

        def done(_result: Any) -> None:
            self.window.set_status("店铺连接验证成功")
            self.window.store_data_changed()
            messagebox.showinfo("连接成功", "Access Token 获取成功，店铺可用。", parent=self)

        def failed(error: str) -> None:
            self.window.set_status("店铺连接验证失败")
            messagebox.showerror("连接失败", error, parent=self)

        self.window.run_task(
            lambda _progress: self.window.service.validate_store(store),
            done,
            failed,
            log_title="验证店铺接口凭证",
        )

    def _sync_profile(self) -> None:
        if not self._require_store():
            return
        store = self.db.get_store(self.selected_store.id)
        if store is None:
            return
        self.window.set_status("正在获取微信小店资料…", busy=True)

        def done(result) -> None:
            self.window.set_status(result.message)
            self.refresh()

        self.window.run_task(
            lambda _progress: self.window.service.sync_store_profile(store),
            done,
            lambda error: messagebox.showerror("店铺资料获取失败", error, parent=self),
            log_title="同步店铺资料",
        )

    def _scan_login(self) -> None:
        if not self._require_store():
            return
        store = self.db.get_store(self.selected_store.id)
        if store is None:
            return
        self.window.set_status("正在打开微信小店扫码登录窗口…", busy=True)

        def done(_payload) -> None:
            self.window.store_data_changed()
            self.window.set_status("网页登录成功，Cookie 已加密保存")

        self.window.run_task(
            lambda _progress: self.window.web_session.interactive_login(store),
            done,
            lambda error: messagebox.showerror("扫码登录失败", error, parent=self),
            log_title="扫码登录店铺",
        )

    def _import_cookie(self) -> None:
        if not self._require_store():
            return
        store = self.db.get_store(self.selected_store.id)
        if store is None:
            return

        def save(header: str) -> None:
            self.window.set_status("正在验证导入的 Cookie…", busy=True)

            def done(_payload) -> None:
                refreshed = self.db.get_store(store.id)
                self.window.store_data_changed()
                if refreshed:
                    self.window.set_status(f"Cookie 已保存：{refreshed.web_status}")
                messagebox.showinfo("Cookie 已保存", "登录状态已检测并更新。", parent=self)

            self.window.run_task(
                lambda _progress: self.window.web_session.import_cookie_header(store, header),
                done,
                lambda error: messagebox.showerror("Cookie 验证失败", error, parent=self),
                log_title="导入并验证网页登录 Cookie",
            )

        CookieImportDialog(self, store.name, save)

    def _probe_login(self) -> None:
        if not self._require_store():
            return
        store = self.db.get_store(self.selected_store.id)
        if store is None:
            return
        self.window.set_status("正在检测网页登录…", busy=True)

        def done(result) -> None:
            status, detail = result
            self.window.store_data_changed()
            self.window.set_status(f"网页登录状态：{status} · {detail}")

        self.window.run_task(
            lambda _progress: self.window.web_session.probe(store),
            done,
            lambda error: messagebox.showerror("登录检测失败", error, parent=self),
            log_title="检测店铺网页登录状态",
        )

    def _logout_web(self) -> None:
        if not self._require_store():
            return
        if not messagebox.askyesno(
            "退出网页会话",
            "退出后依赖商家后台私有接口的功能将暂停，官方 API 不受影响。",
            parent=self,
        ):
            return
        self.window.web_session.logout(self.selected_store)
        self.window.store_data_changed()
        self.window.set_status("网页会话已退出")

    def _require_store(self) -> bool:
        if self.selected_store is None:
            messagebox.showinfo("请选择店铺", "请先选择一家店铺。", parent=self)
            return False
        return True


class OrdersPage(BasePage):
    title = "订单管理"
    subtitle = "从微信小店官方接口拉取订单并保存到本地，支持查询、导出和状态查看"
    order_flag_colors = ("红", "黄", "蓝", "绿", "紫", "橙")
    tab_definitions = (
        ("all", "全部订单"),
        ("purchase", "待采购"),
        ("abnormal", "异常订单"),
        ("pending_delivery", "待发货"),
        ("shipped", "已发货"),
        ("refunding", "退款中"),
        ("closed", "交易关闭"),
        ("completed", "交易成功"),
        ("logistics", "物流异常"),
        ("no_ship_24h", "24h未发货"),
        ("no_ship_overdue", "超时未发货"),
    )

    def __init__(self, parent: tk.Widget, window: "MainWindow"):
        super().__init__(parent, window)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(5, weight=1)
        self.stores: list[Store] = []
        self._store_lookup: dict[str, Store] = {}
        self.store_var = tk.StringVar(value="全部店铺")
        self.search_var = tk.StringVar()
        self.platform_var = tk.StringVar(value="全部平台")
        self.start_var = tk.StringVar(
            value=(datetime.now() - timedelta(days=15)).strftime("%Y-%m-%d")
        )
        self.end_var = tk.StringVar(value=datetime.now().strftime("%Y-%m-%d"))
        self.detail_filter_var = tk.StringVar(value="全部")
        self.marker_var = tk.StringVar(value="全部标识")
        self.purchase_no_var = tk.StringVar()
        self.supplier_no_var = tk.StringVar()
        self.group_var = tk.StringVar(value="全部分组")
        self.account_var = tk.StringVar(value="全部采购账号")
        self.product_id_var = tk.StringVar()
        self.buyer_var = tk.StringVar()
        self.min_amount_var = tk.StringVar()
        self.max_amount_var = tk.StringVar()
        self.item_count_var = tk.StringVar(value="全部")
        self.customer_note_var = tk.StringVar()
        self.shipping_status_var = tk.StringVar(value="全部")
        self.sort_var = tk.StringVar(value="按最新时间排序")
        self.flag_var = tk.StringVar(value="全部旗帜")
        self.display_var = tk.StringVar(value="全部订单")
        self.transfer_var = tk.StringVar(value="全部")
        self.product_group_var = tk.StringVar(value="全部商品分组")
        self.purchase_mode_var = tk.StringVar(value="极速下单")
        self.customer_service_var = tk.StringVar(value="全部客服")
        self.selection_info_var = tk.StringVar(value="显示 0 条")
        self.page_size_var = tk.StringVar(value="10条/页")
        self.current_page = 1
        self.progress_var = tk.DoubleVar(value=0)
        self._rows: dict[str, dict[str, Any]] = {}
        self._all_rows: list[dict[str, Any]] = []
        self._filtered_rows: list[dict[str, Any]] = []
        self._workbench: dict[tuple[int, str], dict[str, Any]] = {}
        self._after_sale_order_ids: set[tuple[int, str]] = set()
        self._account_option_values: list[str] = ["全部采购账号"]
        self._group_option_values: list[str] = ["全部分组"]
        self._order_account_map: dict[tuple[int, str], set[str]] = {}
        self._order_purchase_map: dict[tuple[int, str], list[str]] = {}
        self._order_ui_state: dict[str, dict[str, Any]] = {}
        self._order_image_cache: dict[tuple[int, str], str] = {}
        self._batch_order_filter: set[str] = set()
        self._dispatch_map: dict[tuple[int, str], list[dict[str, Any]]] = {}
        self.active_tab_key = "all"
        self.tab_buttons: dict[str, ttk.Button] = {}

        tabs = ttk.Frame(self, style="TFrame")
        tabs.grid(row=0, column=0, sticky="ew", pady=(0, 2))
        for key, label in self.tab_definitions:
            tab_button = button(
                tabs,
                label,
                lambda tab_key=key: self._change_order_tab(tab_key),
            )
            tab_button.pack(side="left", padx=(0, 10))
            self.tab_buttons[key] = tab_button

        self.subfilters = ttk.Frame(self, style="TFrame")
        subfilters = self.subfilters
        subfilters.grid(row=1, column=0, sticky="ew", pady=(3, 8))
        for label in ("全部", "已关联", "未关联", "有采购链接", "无采购链接"):
            ttk.Radiobutton(
                subfilters,
                text=label,
                value=label,
                variable=self.detail_filter_var,
                command=self._query,
            ).pack(side="left", padx=(0, 14))

        filters = ttk.Frame(self, style="TFrame")
        filters.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        for column in range(6):
            filters.columnconfigure(column, weight=1, uniform="order-filter")

        def filter_field(
            row: int,
            column: int,
            label: str,
            factory: Callable[[tk.Widget], tk.Widget],
            *,
            span: int = 1,
        ) -> tk.Widget:
            holder = ttk.Frame(filters)
            holder.grid(
                row=row,
                column=column,
                columnspan=span,
                sticky="ew",
                padx=3,
                pady=3,
            )
            ttk.Label(holder, text=label).pack(side="left")
            widget = factory(holder)
            widget.pack(side="left", fill="x", expand=True, padx=(4, 0))
            return widget

        filter_field(
            0,
            0,
            "平台 |",
            lambda parent: ttk.Combobox(
                parent,
                textvariable=self.platform_var,
                state="readonly",
                values=("全部平台", "微信小店"),
            ),
        )
        self.store_combo = filter_field(
            0,
            1,
            "店铺名称 |",
            lambda parent: ttk.Combobox(
                parent,
                textvariable=self.store_var,
                state="readonly",
            ),
        )
        time_holder = ttk.Frame(filters)
        time_holder.grid(row=0, column=2, columnspan=2, sticky="ew", padx=3, pady=3)
        ttk.Label(time_holder, text="成交时间").pack(side="left")
        ttk.Entry(time_holder, textvariable=self.start_var).pack(
            side="left", fill="x", expand=True, padx=3
        )
        ttk.Label(time_holder, text="至").pack(side="left")
        ttk.Entry(time_holder, textvariable=self.end_var).pack(
            side="left", fill="x", expand=True, padx=(3, 0)
        )
        filter_field(
            0,
            4,
            "订单编号 |",
            lambda parent: ttk.Entry(parent, textvariable=self.search_var),
        )
        filter_field(
            0,
            5,
            "采购单号 |",
            lambda parent: ttk.Entry(parent, textvariable=self.purchase_no_var),
        )

        self.group_combo = filter_field(
            1,
            0,
            "店铺分组 |",
            lambda parent: ttk.Combobox(
                parent,
                textvariable=self.group_var,
                values=self._group_option_values,
                state="readonly",
            ),
        )
        self.account_combo = filter_field(
            1,
            1,
            "采购账号 |",
            lambda parent: ttk.Combobox(
                parent,
                textvariable=self.account_var,
                values=self._account_option_values,
                state="readonly",
            ),
        )
        filter_field(
            1,
            2,
            "商品ID |",
            lambda parent: ttk.Entry(parent, textvariable=self.product_id_var),
        )
        filter_field(
            1,
            3,
            "收货姓名 |",
            lambda parent: ttk.Entry(parent, textvariable=self.buyer_var),
        )
        filter_field(
            1,
            4,
            "上家单号 |",
            lambda parent: ttk.Entry(parent, textvariable=self.supplier_no_var),
        )

        filter_field(
            2,
            0,
            "排序 |",
            lambda parent: ttk.Combobox(
                parent,
                textvariable=self.sort_var,
                values=("按最新时间排序", "按最早时间排序", "按金额从高到低", "按金额从低到高"),
                state="readonly",
            ),
        )
        filter_field(
            2,
            1,
            "旗帜颜色 |",
            lambda parent: ttk.Combobox(
                parent,
                textvariable=self.flag_var,
                values=("全部旗帜", *self.order_flag_colors),
                state="readonly",
            ),
        )
        filter_field(
            2,
            2,
            "显示 |",
            lambda parent: ttk.Combobox(
                parent,
                textvariable=self.display_var,
                values=("全部订单", "未忽略订单", "已忽略订单"),
                state="readonly",
            ),
        )
        filter_field(
            2,
            3,
            "中转换单 |",
            lambda parent: ttk.Combobox(
                parent,
                textvariable=self.transfer_var,
                values=("全部", "中转换单", "非中转换单"),
                state="readonly",
            ),
        )
        filter_field(
            2,
            4,
            "商品分组 |",
            lambda parent: ttk.Combobox(
                parent,
                textvariable=self.product_group_var,
                values=("全部商品分组",),
                state="readonly",
            ),
        )
        filter_field(
            2,
            5,
            "付款最小金额 |",
            lambda parent: ttk.Entry(parent, textvariable=self.min_amount_var),
        )
        filter_field(
            3,
            0,
            "付款最大金额 |",
            lambda parent: ttk.Entry(parent, textvariable=self.max_amount_var),
        )
        filter_field(
            3,
            1,
            "子订单数量 |",
            lambda parent: ttk.Combobox(
                parent,
                textvariable=self.item_count_var,
                values=("全部", "1 个", "2-4 个", "5 个及以上"),
                state="readonly",
            ),
        )
        filter_field(
            3,
            2,
            "客服备注 |",
            lambda parent: ttk.Entry(parent, textvariable=self.customer_note_var),
        )
        filter_field(
            3,
            3,
            "发货状态 |",
            lambda parent: ttk.Combobox(
                parent,
                textvariable=self.shipping_status_var,
                values=("全部", "待发货", "已发货", "已完成"),
                state="readonly",
            ),
        )
        search_actions = ttk.Frame(filters)
        search_actions.grid(
            row=3,
            column=4,
            columnspan=2,
            sticky="e",
            padx=3,
            pady=3,
        )
        button(search_actions, "搜索", self._query, "Primary").pack(side="left")
        button(search_actions, "刷新", self._query).pack(side="left", padx=4)
        button(search_actions, "收起搜索", self._toggle_search_area).pack(
            side="left"
        )
        button(search_actions, "批量搜索", self._batch_search_orders).pack(
            side="left", padx=4
        )
        self._search_filter_rows = (subfilters, filters)
        self.subfilters.grid_remove()

        order_actions = ttk.Frame(self, style="TFrame")
        order_actions.grid(row=3, column=0, sticky="ew", pady=(0, 7))
        button(
            order_actions,
            "快速同步订单",
            self._sync_recent_orders,
        ).pack(side="left")
        self._add_order_action_menu(
            order_actions,
            "合并/自动下单 ▾",
            (
                ("创建流程测试订单", self._create_flow_test_order),
                ("查看自动派单", self._show_dispatch_tasks),
                ("合并下单", self._fill_purchase_numbers),
                ("批量自动下单", self._show_1688_account),
                ("批量下单日志", self._show_task_logs),
                ("模拟采购成功", self._simulate_purchase_success),
                ("模拟发货成功", self._simulate_ship_success),
                ("模拟订单完成", self._simulate_order_complete),
                ("标记异常订单", lambda: self._set_flow_exception(True)),
                ("恢复异常订单", lambda: self._set_flow_exception(False)),
            ),
        )
        self._add_order_action_menu(
            order_actions,
            "批量操作 ▾",
            (
                ("快速同步近期订单", self._sync_recent_orders),
                ("完整同步所选店铺", self._sync_orders),
                ("采购同步", self._sync_pickup_codes),
                ("勾选订单同步", self._sync_selected_orders),
                ("批量忽略", self._batch_ignore_orders),
                ("取消忽略", self._batch_unignore_orders),
                ("批量备注插旗", self._edit_notes),
                ("批量过滤商品", self._filter_by_selected_products),
                ("标记异常订单", lambda: self._set_flow_exception(True)),
                ("恢复异常订单", lambda: self._set_flow_exception(False)),
            ),
        )
        self._add_order_action_menu(
            order_actions,
            "订单发货 ▾",
            (
                ("批量发货", self._batch_ship_orders),
                ("批量发货日志", self._show_task_logs),
                ("导入发货", self._import_deliveries),
                ("下载模板", self._download_order_template),
            ),
        )
        self._add_order_action_menu(
            order_actions,
            "导出/导入订单 ▾",
            (
                ("导出全部订单", lambda: self._export("xlsx", False)),
                ("导出勾选订单", lambda: self._export("xlsx", True)),
                ("导入订单", self._import_order_workbench),
            ),
        )
        ttk.Label(order_actions, text="采购模式 |").pack(side="left", padx=(10, 0))
        ttk.Combobox(
            order_actions,
            textvariable=self.purchase_mode_var,
            values=("极速下单", "手动下单"),
            state="readonly",
            width=10,
        ).pack(side="left", padx=(4, 8))
        ttk.Label(order_actions, text="选择客服 |").pack(side="left")
        ttk.Combobox(
            order_actions,
            textvariable=self.customer_service_var,
            values=("全部客服",),
            state="readonly",
            width=10,
        ).pack(side="left", padx=(4, 8))
        button(
            order_actions,
            "进入订单工作台",
            self._open_workbench,
            "Primary",
        ).pack(side="right")
        ttk.Label(
            order_actions,
            textvariable=self.selection_info_var,
            style="Subtitle.TLabel",
        ).pack(side="right", padx=10)

        self.progress = ttk.Progressbar(self, variable=self.progress_var, maximum=100)
        self.progress.grid(row=4, column=0, sticky="ew", pady=(0, 7))

        table_frame = tk.Frame(
            self,
            bg="#ffffff",
            highlightbackground="#dce3eb",
            highlightthickness=1,
        )
        table_frame.grid(row=5, column=0, sticky="nsew")
        table_frame.rowconfigure(1, weight=1)
        table_frame.columnconfigure(0, weight=1)
        order_columns = tk.Frame(
            table_frame,
            bg="#f4f7fb",
            height=34,
        )
        order_columns.grid(row=0, column=0, sticky="ew")
        order_columns.grid_propagate(False)
        for column, text in enumerate(
            (
                "店铺订单信息",
                "订单金额",
                "买家信息",
                "采购信息",
                "物流信息",
                "操作",
            )
        ):
            tk.Label(
                order_columns,
                text=text,
                bg="#f4f7fb",
                fg="#4f5f73",
                anchor="center",
                font=("Microsoft YaHei UI", 9, "bold"),
            ).place(
                relx=(
                    ORDER_COLUMN_BOUNDARIES[column]
                    + ORDER_COLUMN_BOUNDARIES[column + 1]
                )
                / 2,
                rely=0.5,
                anchor="center",
            )
        self.tree = OrderCardList(table_frame, self._handle_card_action)
        self.tree.grid(row=1, column=0, sticky="nsew")
        self.tree.bind("<Double-1>", self._open_official_order)
        self.tree.bind("<<TreeviewSelect>>", lambda _event: self._update_selection_info())
        self.tree.bind("<Button-3>", self._show_order_menu)
        footer = tk.Frame(table_frame, bg="#ffffff", padx=8, pady=7)
        footer.grid(row=2, column=0, sticky="ew")
        ttk.Combobox(
            footer,
            textvariable=self.page_size_var,
            values=("10条/页", "20条/页", "50条/页", "100条/页"),
            state="readonly",
            width=10,
        ).pack(side="left")
        self.page_size_var.trace_add(
            "write",
            lambda *_args: self._change_page(1),
        )
        self.page_prev = ttk.Button(
            footer,
            text="‹",
            command=lambda: self._change_page(self.current_page - 1),
        )
        self.page_prev.pack(side="right")
        self.page_next = ttk.Button(
            footer,
            text="›",
            command=lambda: self._change_page(self.current_page + 1),
        )
        self.page_next.pack(side="right", padx=(0, 4))
        self.page_label_var = tk.StringVar(value="第 1 页 · 共 0 条")
        tk.Label(
            footer,
            textvariable=self.page_label_var,
            bg="#ffffff",
            fg="#6f7d8f",
            font=("Microsoft YaHei UI", 9),
        ).pack(side="right", padx=10)

    def _add_order_action_menu(
        self,
        parent: tk.Widget,
        label: str,
        actions: tuple[tuple[str, Callable[[], None]], ...],
    ) -> None:
        menu_button = tk.Menubutton(
            parent,
            text=label,
            bg="#f8fbff",
            fg="#36506e",
            activebackground="#e7effa",
            relief="flat",
            bd=0,
            padx=10,
            pady=5,
            cursor="hand2",
            font=("Microsoft YaHei UI", 9),
        )
        menu_button.pack(side="left", padx=(6, 0))
        menu = tk.Menu(menu_button, tearoff=0)
        for action_label, command in actions:
            menu.add_command(label=action_label, command=command)
        menu_button.configure(menu=menu)

    def _toggle_search_area(self) -> None:
        visible = bool(self._search_filter_rows[0].winfo_manager())
        for row in self._search_filter_rows:
            if visible:
                row.grid_remove()
            else:
                row.grid()

    def _batch_search_orders(self) -> None:
        rows = self._selected_order_rows()
        initial = "\n".join(
            str(row.get("order_id") or "")
            for row in rows
        )
        dialog = tk.Toplevel(self)
        dialog.title("批量搜索订单")
        dialog.geometry("560x420")
        dialog.transient(self.winfo_toplevel())
        dialog.grab_set()
        panel = ttk.Frame(dialog, padding=16)
        panel.pack(fill="both", expand=True)
        panel.rowconfigure(1, weight=1)
        panel.columnconfigure(0, weight=1)
        ttk.Label(
            panel,
            text="每行一个订单编号；留空表示清除批量搜索。",
        ).grid(row=0, column=0, sticky="w", pady=(0, 8))
        text = tk.Text(panel, wrap="none")
        text.grid(row=1, column=0, sticky="nsew")
        text.insert("1.0", initial)

        def apply() -> None:
            values = {
                line.strip()
                for line in text.get("1.0", "end").splitlines()
                if line.strip()
            }
            self._batch_order_filter = values
            dialog.destroy()
            self._query()
            self.window.set_status(
                f"批量搜索范围：{len(values)} 个订单"
                if values
                else "已清除批量搜索"
            )

        actions = ttk.Frame(panel)
        actions.grid(row=2, column=0, sticky="e", pady=(10, 0))
        ttk.Button(actions, text="取消", command=dialog.destroy).pack(
            side="left",
            padx=(0, 8),
        )
        ttk.Button(
            actions,
            text="搜索",
            command=apply,
            style="Primary.TButton",
        ).pack(side="left")

    def _batch_ignore_orders(self) -> None:
        rows = self._selected_order_rows()
        if not rows:
            messagebox.showinfo("请选择订单", "请先选择至少一条订单。", parent=self)
            return
        self._apply_order_ui_changes(rows, ignored=True)

    def _batch_unignore_orders(self) -> None:
        rows = self._selected_order_rows()
        if not rows:
            messagebox.showinfo("请选择订单", "请先选择至少一条订单。", parent=self)
            return
        self._apply_order_ui_changes(rows, ignored=False)
        self.window.set_status(f"已恢复显示 {len(rows)} 条订单")

    def _filter_by_selected_products(self) -> None:
        rows = self._selected_order_rows()
        if not rows:
            messagebox.showinfo(
                "请选择订单",
                "请先选择需要作为筛选条件的订单。",
                parent=self,
            )
            return
        product_ids: set[str] = set()
        for row in rows:
            ids, _count = self._order_product_ids(row)
            product_ids.update(ids)
        if not product_ids:
            messagebox.showinfo(
                "没有商品ID",
                "所选订单尚未同步商品ID，请先刷新订单。",
                parent=self,
            )
            return
        self.product_id_var.set(" ".join(sorted(product_ids)))
        self._query()
        self.window.set_status(
            f"已按所选订单的 {len(product_ids)} 个商品ID筛选"
        )

    @staticmethod
    def _order_state_key(row: dict[str, Any]) -> str:
        return f"{int(row['store_id'])}:{row['order_id']}"

    def _apply_order_ui_changes(
        self,
        rows: list[dict[str, Any]],
        *,
        reload: bool = True,
        **changes: Any,
    ) -> None:
        state = dict(self._order_ui_state)
        for row in rows:
            key = self._order_state_key(row)
            current = dict(state.get(key) or {})
            current.update(changes)
            state[key] = current
            db_changes: dict[str, Any] = {}
            if "flag" in changes:
                db_changes["flag_color"] = str(changes.get("flag") or "")
            if "ignored" in changes:
                db_changes["ignored"] = (
                    1 if bool(changes.get("ignored")) else 0
                )
            if db_changes:
                self.db.update_order_workbench(
                    int(row["store_id"]),
                    str(row["order_id"]),
                    **db_changes,
                )
        self._order_ui_state = state
        self.db.set_setting(
            "order_card_ui_state",
            json.dumps(state, ensure_ascii=False),
        )
        if reload:
            self._query()

    def _set_order_flags(self, color: str) -> None:
        rows = self._selected_order_rows()
        if not rows:
            messagebox.showinfo(
                "请选择订单",
                "请先选择至少一条订单。",
                parent=self,
            )
            return
        self._apply_order_ui_changes(rows, flag=color)
        self.window.set_status(
            f"已为 {len(rows)} 条订单设置{'“' + color + '”旗' if color else '清除旗帜'}"
        )

    def _choose_order_flag(self) -> None:
        self._open_order_flag_note_dialog()

    def _open_order_flag_note_dialog(self) -> None:
        rows = self._selected_order_rows()
        if not rows:
            messagebox.showinfo(
                "请选择订单",
                "请先选择至少一条订单。",
                parent=self,
            )
            return
        first = rows[0]
        key = self._order_state_key(first)
        current_flag = str(
            (self._order_ui_state.get(key) or {}).get("flag") or ""
        )

        def save(values: dict[str, object]) -> None:
            flag = str(values.get("flag") or "")
            scope = str(values.get("scope") or "软件备注")
            note = str(values.get("note") or "")
            sync_store = bool(values.get("sync_store"))
            if scope == "软件备注":
                for row in rows:
                    self.db.update_order_workbench(
                        int(row["store_id"]),
                        str(row["order_id"]),
                        note=note,
                    )
            write_store = scope == "店铺备注" or sync_store
            if write_store and not messagebox.askyesno(
                "同步店铺备注",
                f"将把备注同步到 {len(rows)} 条微信订单的店铺备注。是否继续？",
                parent=self,
            ):
                write_store = False
            self._apply_order_ui_changes(
                rows,
                reload=False,
                flag=flag,
            )
            if not write_store:
                self._query()
                self.window.set_status(
                    f"已保存 {len(rows)} 条订单的旗帜和软件备注"
                )
                return
            self.window.set_status("正在同步店铺备注…", busy=True)

            def work(progress) -> list[str]:
                errors: list[str] = []
                for index, row in enumerate(rows, 1):
                    progress(
                        f"正在同步店铺备注 {index}/{len(rows)}",
                        index,
                        len(rows),
                    )
                    store = self.db.get_store(int(row["store_id"]))
                    if store is None:
                        errors.append(f"{row['order_id']}: 店铺不存在")
                        continue
                    try:
                        self.window.service.update_order_notes(
                            store,
                            str(row["order_id"]),
                            note,
                            0,
                        )
                    except Exception as exc:
                        errors.append(f"{row['order_id']}: {exc}")
                return errors

            def done(errors: list[str]) -> None:
                self._query()
                self.window.set_status(
                    f"旗帜和备注已保存，店铺备注失败 {len(errors)} 条",
                    busy=False,
                )
                if errors:
                    messagebox.showwarning(
                        "店铺备注同步失败",
                        "\n".join(errors[:10]),
                        parent=self,
                    )

            self.window.run_task(
                work,
                done,
                lambda error: messagebox.showerror(
                    "店铺备注同步失败",
                    error,
                    parent=self,
                ),
                log_title="同步订单旗帜与备注",
            )

        OrderFlagNoteDialog(
            self,
            count=len(rows),
            current_flag=current_flag,
            current_software_note=str(
                self._workbench_for(first).get("note") or ""
            ),
            current_store_note=str(first.get("merchant_notes") or ""),
            on_save=save,
        )

    def _edit_batch_notes_and_flags(
        self,
        rows: list[dict[str, Any]],
    ) -> None:
        current = self._order_ui_state.get(
            self._order_state_key(rows[0]),
            {},
        )
        current_flag = str(current.get("flag") or "")
        dialog = tk.Toplevel(self)
        dialog.title("批量备注插旗")
        dialog.geometry("560x330")
        dialog.transient(self.winfo_toplevel())
        dialog.grab_set()
        panel = ttk.Frame(dialog, padding=18)
        panel.pack(fill="both", expand=True)
        panel.columnconfigure(0, weight=1)
        panel.rowconfigure(1, weight=1)
        ttk.Label(
            panel,
            text=f"将应用到已选 {len(rows)} 条订单",
        ).grid(row=0, column=0, sticky="w", pady=(0, 8))
        text = tk.Text(panel, wrap="word", height=7)
        text.grid(row=1, column=0, sticky="nsew")
        first_note = str(
            self._workbench_for(rows[0]).get("note") or ""
        )
        text.insert("1.0", first_note)
        color_row = ttk.Frame(panel)
        color_row.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        ttk.Label(color_row, text="旗帜颜色").pack(side="left")
        flag_var = tk.StringVar(
            value=current_flag or "不设置"
        )
        ttk.Combobox(
            color_row,
            textvariable=flag_var,
            values=("不设置", *self.order_flag_colors),
            state="readonly",
            width=12,
        ).pack(side="left", padx=8)

        def save() -> None:
            note = text.get("1.0", "end").strip()
            flag = "" if flag_var.get() == "不设置" else flag_var.get()
            for row in rows:
                self.db.update_order_workbench(
                    int(row["store_id"]),
                    str(row["order_id"]),
                    note=note,
                )
            self._apply_order_ui_changes(rows, flag=flag)
            dialog.destroy()
            self.window.set_status(
                f"已为 {len(rows)} 条订单保存备注和旗帜"
            )

        actions = ttk.Frame(panel)
        actions.grid(row=3, column=0, sticky="e", pady=(12, 0))
        ttk.Button(actions, text="取消", command=dialog.destroy).pack(
            side="left",
            padx=(0, 8),
        )
        ttk.Button(
            actions,
            text="保存",
            command=save,
            style="Primary.TButton",
        ).pack(side="left")

    def _show_task_logs(self) -> None:
        method = getattr(self.window, "_show_task_logs", None)
        if callable(method):
            method()

    def refresh(self) -> None:
        self._reload_stores()
        self._query()

    def _reload_stores(self) -> None:
        self.stores = self.db.list_stores()
        self._store_lookup = {"全部店铺": None}  # type: ignore[dict-item]
        values = ["全部店铺"]
        for store in self.stores:
            label = f"{store.name} · #{store.id}"
            self._store_lookup[label] = store
            values.append(label)
        self.store_combo["values"] = values
        if self.store_var.get() not in values:
            self.store_var.set("全部店铺")
        self._group_option_values = [
            "全部分组",
            *self.db.list_store_groups(),
        ]
        self.group_combo["values"] = self._group_option_values
        if self.group_var.get() not in self._group_option_values:
            self.group_var.set("全部分组")
        account_labels = [
            f"{account.get('label') or account.get('login_id') or account.get('id')} "
            f"· {account.get('platform') or ''}".strip()
            for account in self.db.list_procurement_accounts()
        ]
        self._account_option_values = ["全部采购账号", *account_labels]
        self.account_combo["values"] = self._account_option_values
        if self.account_var.get() not in self._account_option_values:
            self.account_var.set("全部采购账号")

    def _selected_store(self) -> Store | None:
        value = self._store_lookup.get(self.store_var.get())
        return value

    def _query(self) -> None:
        start_text = self.start_var.get().strip()
        end_text = self.end_var.get().strip()
        start_time = end_time = None
        if start_text or end_text:
            try:
                start = datetime.strptime(start_text, "%Y-%m-%d")
                end = datetime.strptime(end_text, "%Y-%m-%d")
                if end < start:
                    raise ValueError("结束日期不能早于开始日期")
                start_time = int(start.timestamp())
                end_time = int(end.timestamp()) + 86399
            except ValueError as exc:
                messagebox.showwarning("筛选条件", str(exc), parent=self)
                return
        store = self._selected_store()
        try:
            ui_state = json.loads(
                self.db.get_setting("order_card_ui_state", "{}")
            )
        except (TypeError, ValueError):
            ui_state = {}
        self._order_ui_state = ui_state if isinstance(ui_state, dict) else {}
        self._all_rows = self.db.list_orders(
            store_id=store.id if store else None,
            start_time=start_time,
            end_time=end_time,
            search="",
        )
        self._all_rows = [
            row
            for row in self._all_rows
            if not self._is_unclaimed_gift_order(row)
        ]
        if self._batch_order_filter:
            self._all_rows = [
                row
                for row in self._all_rows
                if str(row.get("order_id") or "") in self._batch_order_filter
            ]
        self._workbench = self.db.get_order_workbench(store.id if store else None)
        self.db.sync_auto_dispatch_tasks()
        self._dispatch_map = self.db.order_dispatch_map(
            store.id if store else None
        )
        links = self.db.list_procurement_links(store.id if store else None)
        procurement_lookup = {
            int(row["id"]): row
            for row in self.db.list_procurement_orders()
        }
        self._order_account_map = {}
        self._order_purchase_map = {}
        for link in links:
            key = (int(link["store_id"]), str(link["order_id"]))
            purchase = procurement_lookup.get(
                int(link.get("procurement_order_id") or 0),
                {},
            )
            label = str(
                purchase.get("account_label")
                or purchase.get("label")
                or purchase.get("platform")
                or ""
            ).strip()
            if label:
                self._order_account_map.setdefault(key, set()).add(label)
            purchase_no = str(
                purchase.get("external_order_id")
                or link.get("external_order_id")
                or ""
            ).strip()
            if purchase_no:
                numbers = self._order_purchase_map.setdefault(key, [])
                if purchase_no not in numbers:
                    numbers.append(purchase_no)
        self._procurement_note_map: dict[tuple[int, str], str] = {}
        for link in links:
            key = (int(link["store_id"]), str(link["order_id"]))
            note = (
                f"[{link.get('platform') or ''}] "
                f"{link.get('external_order_id') or ''} "
                f"物流:{link.get('mail_no') or ''} "
                f"取件码:{link.get('pickup_code') or ''}"
            )
            if link.get("local_note"):
                note += f" {link['local_note']}"
            self._procurement_note_map[key] = (
                self._procurement_note_map.get(key, "") + "\n" + note
            ).strip()
        after_sales = self.db.list_after_sales(store.id if store else None)
        self._after_sale_order_ids = {
            (int(row["store_id"]), str(row["order_id"]))
            for row in after_sales
            if row.get("order_id")
        }
        term = self.search_var.get().strip().lower()
        if term:
            searchable_rows = []
            for row in self._all_rows:
                workbench = self._workbench_for(row)
                text = " ".join(
                    str(value or "")
                    for value in (
                        row.get("order_id"),
                        row.get("store_name"),
                        row.get("status_text"),
                        row.get("buyer_name"),
                        row.get("phone"),
                        row.get("address"),
                        row.get("waybill_id"),
                        row.get("item_summary"),
                        workbench.get("purchase_order_no"),
                        " ".join(self._purchase_numbers_for(row)),
                        workbench.get("pickup_code"),
                        workbench.get("note"),
                        row.get("merchant_notes"),
                    )
                ).lower()
                if term in text:
                    searchable_rows.append(row)
            self._all_rows = searchable_rows
        self._filtered_rows = [
            row
            for row in self._all_rows
            if self._matches_order_tab(row)
            and self._matches_marker(row)
            and self._matches_detail_filter(row)
            and self._matches_advanced_filters(row)
        ]
        self._sort_order_rows(self._filtered_rows)
        self.current_page = 1
        self._refresh_order_tab_labels()
        self._render_order_rows()
        self.window.set_status(f"订单查询完成，共 {len(self._filtered_rows)} 条")

    def _workbench_for(self, row: dict[str, Any]) -> dict[str, Any]:
        return self._workbench.get(
            (int(row["store_id"]), str(row["order_id"])),
            {},
        )

    def _purchase_numbers_for(self, row: dict[str, Any]) -> list[str]:
        key = (int(row["store_id"]), str(row["order_id"]))
        numbers = list(self._order_purchase_map.get(key, []))
        workbench_no = str(
            self._workbench_for(row).get("purchase_order_no") or ""
        ).strip()
        if workbench_no and workbench_no not in numbers:
            numbers.insert(0, workbench_no)
        return numbers

    @staticmethod
    def _is_unclaimed_gift_order(row: dict[str, Any]) -> bool:
        return is_unclaimed_gift_order(row)

    @staticmethod
    def _display_order_status(row: dict[str, Any]) -> str:
        return {
            10: "待付款",
            12: "礼物待收下",
            17: "待核销",
            20: "买家已付款",
            21: "卖家已发货",
            30: "待收货",
            100: "交易成功",
            200: "售后完成",
            250: "交易关闭",
        }.get(int(row.get("status") or 0), str(row.get("status_text") or ""))

    @staticmethod
    def _order_logistics_display(
        row: dict[str, Any],
        workbench: dict[str, Any],
        flow_stage: str,
        *,
        now_ts: int | None = None,
    ) -> tuple[str, bool]:
        status = int(row.get("status") or 0)
        now = int(
            datetime.now().timestamp()
            if now_ts is None
            else now_ts
        )

        def timestamp_text(value: Any) -> str:
            try:
                timestamp = int(value or 0)
            except (TypeError, ValueError):
                return ""
            if timestamp <= 0:
                return ""
            return datetime.fromtimestamp(timestamp).strftime(
                "%Y-%m-%d %H:%M"
            )

        def iso_text(value: Any) -> str:
            text = str(value or "").strip()
            if not text:
                return ""
            try:
                return datetime.fromisoformat(
                    text.replace("Z", "+00:00")
                ).strftime("%Y-%m-%d %H:%M")
            except ValueError:
                return text[:16].replace("T", " ")

        if status == 20 or flow_stage in {
            "pending_purchase",
            "purchased_pending_shipment",
        }:
            base = int(row.get("pay_time") or row.get("create_time") or 0)
            remaining = base + 2 * 86400 - now
            overdue = remaining < 0
            if overdue:
                return f"已超时 {abs(remaining) // 3600} 小时", True
            return (
                f"剩余{remaining // 86400}天"
                f"{(remaining % 86400) // 3600}时"
                f"{(remaining % 3600) // 60}分",
                False,
            )

        if status in {21, 30} or flow_stage == "shipped":
            shipped = iso_text(workbench.get("shipped_at"))
            if shipped:
                return f"发货时间 {shipped}", False
            updated = timestamp_text(row.get("update_time"))
            return (
                f"状态更新 {updated}" if updated else "已发货",
                False,
            )

        if status == 100 or flow_stage == "completed":
            completed = iso_text(workbench.get("completed_at"))
            if completed:
                return f"完成时间 {completed}", False
            updated = timestamp_text(row.get("update_time"))
            return (
                f"交易完成 {updated}" if updated else "交易成功",
                False,
            )

        if status == 200:
            updated = timestamp_text(row.get("update_time"))
            return (
                f"售后完成 {updated}" if updated else "售后完成",
                False,
            )

        if status == 250:
            updated = timestamp_text(row.get("update_time"))
            return (
                f"交易关闭 {updated}" if updated else "交易关闭",
                False,
            )

        updated = timestamp_text(row.get("update_time"))
        return (
            (
                f"{OrdersPage._display_order_status(row)} "
                f"{updated}"
            ).strip(),
            False,
        )

    def _matches_order_tab(self, row: dict[str, Any]) -> bool:
        key = self.active_tab_key
        if key == "all":
            return True
        status = int(row.get("status") or 0)
        stage = self._order_flow_stage(row)
        pair = (int(row["store_id"]), str(row["order_id"]))
        if key == "purchase":
            return stage == "pending_purchase"
        if key == "abnormal":
            return stage == "exception"
        if key == "pending_delivery":
            return stage == "purchased_pending_shipment"
        if key == "shipped":
            return stage == "shipped"
        if key == "refunding":
            return status == 200 or pair in self._after_sale_order_ids
        if key == "closed":
            return status == 250
        if key == "completed":
            return stage == "completed"
        if key == "logistics":
            return bool(
                int(row.get("update_time") or 0)
                and status in {20, 21}
                and int(row.get("update_time") or 0)
                < int(datetime.now().timestamp()) - 3 * 86400
            )
        if key in {"no_ship_24h", "no_ship_overdue"}:
            hours = 48 if key == "no_ship_overdue" else 24
            return (
                stage
                in {"pending_purchase", "purchased_pending_shipment"}
                and int(row.get("create_time") or 0)
                < int(datetime.now().timestamp()) - hours * 3600
            )
        return True

    def _order_flow_stage(self, row: dict[str, Any]) -> str:
        status = int(row.get("status") or 0)
        workbench = self._workbench_for(row)
        stage = str(workbench.get("fulfillment_stage") or "")
        pair = (int(row["store_id"]), str(row["order_id"]))
        linked = (
            bool(workbench.get("purchase_order_no"))
            or pair in self._order_account_map
        )
        if status == 250:
            return "closed"
        if stage == "exception" or status == 13:
            return "exception"
        if status == 100 or stage == "completed":
            return "completed"
        if (
            status in {21, 30}
            or stage == "shipped"
            or bool(workbench.get("is_marked_sent"))
        ):
            return "shipped"
        if status == 200:
            return "refunding"
        if status == 20:
            if stage in {"purchased", "purchased_pending_shipment"} or linked:
                return "purchased_pending_shipment"
            return "pending_purchase"
        if status in {12, 13}:
            return "exception"
        return "other"

    def _order_is_marked(self, row: dict[str, Any]) -> bool:
        workbench = self._workbench_for(row)
        ui_state = self._order_ui_state.get(
            self._order_state_key(row),
            {},
        )
        return bool(
            int(workbench.get("is_marked") or 0)
            or int(workbench.get("is_marked_sent") or 0)
            or str(workbench.get("note") or "").strip()
            or str(row.get("merchant_notes") or "").strip()
            or str(ui_state.get("flag") or "").strip()
        )

    def _matches_marker(self, row: dict[str, Any]) -> bool:
        selected = self.marker_var.get()
        if selected == "已标记":
            return self._order_is_marked(row)
        if selected == "未标记":
            return not self._order_is_marked(row)
        return True

    def _matches_detail_filter(self, row: dict[str, Any]) -> bool:
        selected = self.detail_filter_var.get()
        workbench = self._workbench_for(row)
        key = (int(row["store_id"]), str(row["order_id"]))
        has_purchase = bool(workbench.get("purchase_order_no")) or key in self._order_account_map
        if selected == "已关联":
            return has_purchase
        if selected == "未关联":
            return not has_purchase
        if selected == "有采购链接":
            return key in self._order_account_map
        if selected == "无采购链接":
            return key not in self._order_account_map
        return True

    def _refresh_order_tab_labels(self) -> None:
        counts = {
            key: sum(1 for row in self._all_rows if self._tab_matches_order(row, key))
            for key, _label in self.tab_definitions
        }
        for key, label in self.tab_definitions:
            count = counts[key]
            text = (
                f"{label}  {count}"
                if key != "all" and count
                else label
            )
            self.tab_buttons[key].configure(
                text=text,
                style=(
                    "Primary.TButton"
                    if key == self.active_tab_key
                    else "Secondary.TButton"
                ),
            )

    def _tab_matches_order(self, row: dict[str, Any], key: str) -> bool:
        previous = self.active_tab_key
        self.active_tab_key = key
        try:
            return self._matches_order_tab(row)
        finally:
            self.active_tab_key = previous

    def _render_order_rows(self) -> None:
        self._rows.clear()
        card_rows: list[dict[str, Any]] = []
        page_size = {
            "10条/页": 10,
            "20条/页": 20,
            "50条/页": 50,
            "100条/页": 100,
        }.get(self.page_size_var.get(), 10)
        total = len(self._filtered_rows)
        page_count = max(1, (total + page_size - 1) // page_size)
        self.current_page = max(1, min(self.current_page, page_count))
        start = (self.current_page - 1) * page_size
        visible_rows = self._filtered_rows[start : start + page_size]
        for index, row in enumerate(visible_rows):
            iid = str(index)
            self._rows[iid] = row
            workbench = self._workbench_for(row)
            key = (int(row["store_id"]), str(row["order_id"]))
            flow_stage = self._order_flow_stage(row)
            ui_state = self._order_ui_state.get(
                f"{key[0]}:{key[1]}",
                {},
            )
            logistics = " ".join(
                value
                for value in (
                    str(row.get("carrier") or ""),
                    str(row.get("waybill_id") or ""),
                )
                if value
            )
            try:
                raw = json.loads(row.get("raw_json") or "{}")
            except (TypeError, ValueError):
                raw = {}
            detail = raw.get("order_detail") if isinstance(raw, dict) else {}
            products = detail.get("product_infos") if isinstance(detail, dict) else []
            first_product = (
                products[0]
                if products and isinstance(products[0], dict)
                else {}
            )
            customer_note = str(
                raw.get("customer_note")
                or raw.get("customer_service_note")
                or (
                    raw.get("order_detail", {}).get("customer_note")
                    if isinstance(raw.get("order_detail"), dict)
                    else ""
                )
                or ""
            )
            user_message = str(
                raw.get("buyer_message")
                or raw.get("user_message")
                or raw.get("leave_message")
                or ""
            )
            product_title = str(
                first_product.get("title")
                or row.get("item_summary")
                or "商品信息待同步"
            )
            image_url = self._order_image_url(row, first_product)
            attributes = first_product.get("sku_attrs") or []
            attribute_text = "；".join(
                f"{item.get('attr_key', '')}:{item.get('attr_value', '')}"
                for item in attributes
                if isinstance(item, dict)
            )
            product_lines = [
                f"商家编码： {first_product.get('out_product_id') or '—'}",
                f"商品Id： {first_product.get('product_id') or '—'}",
                f"属性： {attribute_text or '—'}",
                f"数量： {first_product.get('sku_cnt') or 1}",
            ]
            created = int(row.get("create_time") or 0)
            deadline, overdue = self._order_logistics_display(
                row,
                workbench,
                flow_stage,
            )
            accounts = self._order_account_map.get(key, set())
            purchase_numbers = self._purchase_numbers_for(row)
            dispatch_tasks = self._dispatch_map.get(key, [])
            dispatch_text = ""
            if dispatch_tasks:
                task = dispatch_tasks[0]
                dispatch_text = (
                    f"{task.get('platform') or '采购'} · "
                    f"{'待采购' if task.get('status') == 'pending' else task.get('status')}"
                    f" · 数量 {int(task.get('quantity') or 1)}"
                )
            buyer_detail = "，".join(
                part
                for part in (
                    str(row.get("buyer_name") or ""),
                    str(row.get("phone") or ""),
                    str(row.get("address") or ""),
                )
                if part
            )
            card_rows.append(
                {
                    "iid": iid,
                    "row": row,
                    "created_text": _format_datetime(created),
                    "status_text": self._display_order_status(row),
                    "flow_stage_text": {
                        "pending_purchase": "待采购",
                        "purchased_pending_shipment": "已采购待发货",
                        "shipped": "已发货",
                        "completed": "已完成",
                        "exception": "异常订单",
                        "refunding": "退款中",
                        "closed": "交易关闭",
                    }.get(flow_stage, ""),
                    "store": row.get("store_name", ""),
                    "deadline": deadline,
                    "overdue": overdue,
                    "gift": bool(raw.get("is_present")) if isinstance(raw, dict) else False,
                    "flag_color": str(
                        ui_state.get("flag")
                        or workbench.get("flag_color")
                        or ""
                    ),
                    "image_url": image_url,
                    "product_title": str(
                        ui_state.get("title") or product_title
                    ),
                    "merchant_code": str(
                        first_product.get("out_product_id") or ""
                    ),
                    "product_id": str(first_product.get("product_id") or ""),
                    "product_lines": product_lines,
                    "amount": _money(int(row.get("total_amount") or 0)),
                    "buyer_name": str(row.get("buyer_name") or "买家信息待解密"),
                    "buyer_detail": buyer_detail,
                    "purchase_account": " / ".join(sorted(accounts))
                    if accounts
                    else "未匹配采购账号",
                    "purchase_no": " / ".join(purchase_numbers),
                    "logistics": logistics or "暂无物流",
                    "dispatch_text": dispatch_text,
                    "dispatch_count": len(dispatch_tasks),
                    "notes": (
                        ("客服备注", customer_note),
                        ("店铺备注", str(row.get("merchant_notes") or "")),
                        ("软件备注", str(workbench.get("note") or "")),
                        ("用户留言", user_message),
                        ("自动派单", dispatch_text),
                    ),
                }
            )
        self.tree.set_rows(card_rows)
        if hasattr(self, "page_prev"):
            self.page_prev.configure(
                state="normal" if self.current_page > 1 else "disabled"
            )
            self.page_next.configure(
                state="normal"
                if self.current_page < page_count
                else "disabled"
            )
            self.page_label_var.set(
                f"第 {self.current_page} 页 · 共 {total} 条"
            )
        self._update_selection_info()

    def _change_page(self, page: int) -> None:
        self.current_page = max(1, int(page))
        self._render_order_rows()

    def _handle_card_action(self, action: str, item: dict[str, Any]) -> None:
        iid = str(item["iid"])
        if not iid:
            return
        if action == "select":
            self.tree.selection_set((iid,))
            self._update_selection_info()
            return
        if action == "selection":
            self._update_selection_info()
            return
        if action == "copy_order":
            self.clipboard_clear()
            self.clipboard_append(str(item["row"].get("order_id") or ""))
            self.window.set_status("订单编号已复制")
            return
        if action in {"copy_product_title", "copy_product_id", "copy_merchant_code"}:
            field = {
                "copy_product_title": "product_title",
                "copy_product_id": "product_id",
                "copy_merchant_code": "merchant_code",
            }[action]
            value = str(item.get(field) or "").strip()
            if not value:
                messagebox.showinfo("没有内容", "当前订单没有可复制的字段。", parent=self)
                return
            self.clipboard_clear()
            self.clipboard_append(value)
            self.window.set_status(
                {
                    "copy_product_title": "商品标题已复制",
                    "copy_product_id": "商品ID已复制",
                    "copy_merchant_code": "商家编码已复制",
                }[action]
            )
            return
        if action == "open_order":
            self.tree.selection_set((iid,))
            self._open_official_order()
            return
        if action == "edit_product_title":
            self.tree.selection_set((iid,))
            self._edit_order_product_title()
            return
        if action == "open_product_link":
            self.tree.selection_set((iid,))
            self._open_product_link_for_order()
            return
        if action == "copy_address":
            self.tree.selection_set((iid,))
            self._copy_order_address()
            return
        if action != "menu":
            self.tree.selection_set((iid,))
        if action in {"refresh_order", "decode"}:
            if action == "refresh_order":
                self._sync_orders()
            else:
                self._decode_sensitive()
            return
        if action == "mark":
            self._set_marked_sent(True)
            return
        if action == "flag":
            self._choose_order_flag()
            return
        if action == "ignore":
            self._batch_ignore_orders()
            return
        if action == "unignore":
            self._batch_unignore_orders()
            return
        if action == "fast_purchase":
            self._start_purchase_flow(fast=True)
            return
        if action == "purchase":
            self._start_purchase_flow(fast=False)
            return
        if action == "link_product":
            self._open_product_link_for_order()
            return
        if action == "manual_purchase":
            self._manual_link_procurement_order()
            return
        if action == "ship":
            self._edit_delivery()
            return
        if action == "edit_address":
            self._request_address()
            return
        if action == "menu":
            event = type(
                "OrderMenuEvent",
                (),
                {
                    "y": 0,
                    "x_root": self.winfo_pointerx(),
                    "y_root": self.winfo_pointery(),
                    "widget": self.tree,
                },
            )()
            self._show_order_menu(event)

    def _change_order_tab(self, key: str) -> None:
        if key == self.active_tab_key:
            return
        self.active_tab_key = key
        if key == "purchase":
            self.subfilters.grid()
        else:
            self.subfilters.grid_remove()
            self.detail_filter_var.set("全部")
        self._query()

    def _reset_order_filters(self) -> None:
        self.search_var.set("")
        self.start_var.set("")
        self.end_var.set("")
        self.detail_filter_var.set("全部")
        self.marker_var.set("全部标识")
        self._clear_advanced_values()
        self._query()

    def _clear_advanced_values(self) -> None:
        self.purchase_no_var.set("")
        self.supplier_no_var.set("")
        self.group_var.set("全部分组")
        self.account_var.set("全部采购账号")
        self.product_id_var.set("")
        self.buyer_var.set("")
        self.min_amount_var.set("")
        self.max_amount_var.set("")
        self.item_count_var.set("全部")
        self.customer_note_var.set("")
        self.shipping_status_var.set("全部")
        self.sort_var.set("按最新时间排序")
        self.flag_var.set("全部旗帜")
        self.display_var.set("全部订单")
        self.transfer_var.set("全部")
        self.product_group_var.set("全部商品分组")
        self._batch_order_filter.clear()

    def _clear_advanced_filters(self) -> None:
        self._clear_advanced_values()
        self._query()

    @staticmethod
    def _order_product_ids(
        row: dict[str, Any],
    ) -> tuple[set[str], int]:
        try:
            raw = json.loads(row.get("raw_json") or "{}")
        except (TypeError, ValueError):
            raw = {}
        detail = raw.get("order_detail") if isinstance(raw, dict) else {}
        items = detail.get("product_infos") if isinstance(detail, dict) else []
        ids: set[str] = set()
        count = 0
        for item in items or []:
            if not isinstance(item, dict):
                continue
            product_id = str(item.get("product_id") or "").strip()
            if product_id:
                ids.add(product_id)
            count += max(0, int(item.get("sku_cnt") or 0))
        if not count and row.get("item_summary"):
            count = max(1, str(row["item_summary"]).count("×"))
        return ids, count

    @staticmethod
    def _payload_image_url(payload: Any, depth: int = 0) -> str:
        if depth > 5:
            return ""
        if isinstance(payload, str):
            value = payload.strip()
            if value.startswith("//"):
                value = "https:" + value
            parsed = urllib.parse.urlsplit(value)
            if parsed.scheme in {"http", "https"}:
                return value
            return ""
        if isinstance(payload, list):
            for item in payload:
                value = OrdersPage._payload_image_url(item, depth + 1)
                if value:
                    return value
            return ""
        if not isinstance(payload, dict):
            return ""
        for key in (
            "thumb_url",
            "head_img",
            "head_img_url",
            "image_url",
            "img_url",
            "sku_image",
            "sku_thumb_url",
            "cover_url",
        ):
            value = OrdersPage._payload_image_url(payload.get(key), depth + 1)
            if value:
                return value
        for value in payload.values():
            found = OrdersPage._payload_image_url(value, depth + 1)
            if found:
                return found
        return ""

    def _order_image_url(
        self,
        row: dict[str, Any],
        first_product: dict[str, Any],
    ) -> str:
        direct = self._payload_image_url(first_product)
        if direct:
            return direct
        product_id = str(first_product.get("product_id") or "").strip()
        if not product_id:
            ids, _count = self._order_product_ids(row)
            product_id = next(iter(sorted(ids)), "")
        if not product_id:
            return ""
        store_id = int(row["store_id"])
        cache_key = (store_id, product_id)
        cached = self._order_image_cache.get(cache_key)
        if cached is not None:
            return cached
        product = self.db.get_product(store_id, product_id)
        image_url = ""
        if product:
            image_url = str(product.get("head_img") or "").strip()
            if not image_url:
                try:
                    image_url = self._payload_image_url(
                        json.loads(product.get("raw_json") or "{}")
                    )
                except (TypeError, ValueError):
                    image_url = ""
        self._order_image_cache[cache_key] = image_url
        return image_url

    def _matches_advanced_filters(self, row: dict[str, Any]) -> bool:
        key = (int(row["store_id"]), str(row["order_id"]))
        workbench = self._workbench_for(row)
        store = next(
            (item for item in self.stores if item.id == int(row["store_id"])),
            None,
        )
        purchase_no = self.purchase_no_var.get().strip().casefold()
        purchase_numbers = " ".join(
            self._purchase_numbers_for(row)
        ).casefold()
        if purchase_no and purchase_no not in purchase_numbers:
            return False
        supplier_no = self.supplier_no_var.get().strip().casefold()
        if supplier_no and supplier_no not in purchase_numbers:
            return False
        if (
            self.group_var.get() != "全部分组"
            and (store is None or store.group_name != self.group_var.get())
        ):
            return False
        if self.account_var.get() != "全部采购账号":
            selected_label = self.account_var.get().split(" · ", 1)[0]
            accounts = self._order_account_map.get(key, set())
            if selected_label not in accounts:
                return False
        product_terms = [
            term
            for term in re.split(
                r"[\s,，;；]+",
                self.product_id_var.get().strip().casefold(),
            )
            if term
        ]
        product_ids, item_count = self._order_product_ids(row)
        if product_terms and not any(
            term in candidate.casefold()
            for term in product_terms
            for candidate in product_ids
        ):
            return False
        buyer = self.buyer_var.get().strip().casefold()
        if buyer and buyer not in str(row.get("buyer_name") or "").casefold():
            return False
        try:
            minimum = int(float(self.min_amount_var.get() or 0) * 100)
            maximum = int(float(self.max_amount_var.get() or 0) * 100)
        except ValueError:
            return False
        amount = int(row.get("total_amount") or 0)
        if minimum and amount < minimum:
            return False
        if maximum and amount > maximum:
            return False
        count_filter = self.item_count_var.get()
        if count_filter == "1 个" and item_count != 1:
            return False
        if count_filter == "2-4 个" and not 2 <= item_count <= 4:
            return False
        if count_filter == "5 个及以上" and item_count < 5:
            return False
        note = self.customer_note_var.get().strip().casefold()
        if note:
            note_text = " ".join(
                (
                    str(row.get("merchant_notes") or ""),
                    str(workbench.get("note") or ""),
                    self._procurement_note_map.get(key, ""),
                )
            ).casefold()
            if note not in note_text:
                return False
        flow_stage = self._order_flow_stage(row)
        shipping = self.shipping_status_var.get()
        if shipping == "待发货" and flow_stage != "purchased_pending_shipment":
            return False
        if shipping == "已发货" and flow_stage != "shipped":
            return False
        if shipping == "已完成" and flow_stage != "completed":
            return False
        transfer = self.transfer_var.get()
        has_after_sale = key in self._after_sale_order_ids
        if transfer == "中转换单" and not has_after_sale:
            return False
        if transfer == "非中转换单" and has_after_sale:
            return False
        state = self._order_ui_state.get(
            f"{key[0]}:{key[1]}",
            {},
        )
        display = self.display_var.get()
        ignored = bool(state.get("ignored")) or bool(
            workbench.get("ignored")
        )
        if display == "未忽略订单" and ignored:
            return False
        if display == "已忽略订单" and not ignored:
            return False
        flag = self.flag_var.get()
        current_flag = str(
            state.get("flag")
            or workbench.get("flag_color")
            or ""
        )
        if flag != "全部旗帜" and current_flag != flag:
            return False
        return True

    def _sort_order_rows(self, rows: list[dict[str, Any]]) -> None:
        mode = self.sort_var.get()
        if mode == "按最早时间排序":
            rows.sort(key=lambda row: int(row.get("create_time") or 0))
        elif mode == "按金额从高到低":
            rows.sort(
                key=lambda row: int(row.get("total_amount") or 0),
                reverse=True,
            )
        elif mode == "按金额从低到高":
            rows.sort(key=lambda row: int(row.get("total_amount") or 0))
        else:
            rows.sort(
                key=lambda row: int(row.get("create_time") or 0),
                reverse=True,
            )

    def _selected_order_rows(self) -> list[dict[str, Any]]:
        return [
            self._rows[iid]
            for iid in self.tree.selection()
            if iid in self._rows
        ]

    def _select_all_orders(self) -> None:
        self.tree.selection_set(tuple(self._rows))
        self._update_selection_info()

    def _clear_order_selection(self) -> None:
        self.tree.selection_remove(*self.tree.selection())
        self._update_selection_info()

    def _update_selection_info(self) -> None:
        selected = len(self.tree.selection())
        self.selection_info_var.set(
            f"显示 {len(self._rows)} 条 · 已选 {selected} 条"
        )

    def _fill_purchase_numbers(self) -> None:
        rows = self._selected_order_rows()
        if not rows:
            messagebox.showinfo("请选择订单", "请先选择至少一条订单。", parent=self)
            return

        def save(values: dict[str, str]) -> None:
            value = str(values.get("purchase_order_no") or "").strip()
            if not value:
                messagebox.showwarning("1688订单号", "请输入采购单号。", parent=self)
                return
            for row in rows:
                self.db.update_order_workbench(
                    int(row["store_id"]),
                    str(row["order_id"]),
                    purchase_order_no=value,
                    is_marked=1,
                    fulfillment_stage="purchased",
                    purchased_at=datetime.now().isoformat(timespec="seconds"),
                )
                self._mark_dispatch_tasks(row, "purchased")
            self._query()
            self.window.set_status(f"已填写 {len(rows)} 条订单的1688采购单号")

        SimpleFieldsDialog(
            self,
            "填写1688订单号",
            (("purchase_order_no", "1688采购单号"),),
            save,
        )

    def _mark_dispatch_tasks(
        self,
        row: dict[str, Any],
        status: str,
        error: str = "",
    ) -> None:
        key = (int(row["store_id"]), str(row["order_id"]))
        for task in self._dispatch_map.get(key, []):
            self.db.update_order_dispatch_task(
                int(task["id"]),
                status=status,
                error=error,
            )

    def _show_dispatch_tasks(self) -> None:
        rows = self._selected_order_rows()
        if rows:
            tasks = [
                task
                for row in rows
                for task in self._dispatch_map.get(
                    (int(row["store_id"]), str(row["order_id"])),
                    [],
                )
            ]
        else:
            tasks = self.db.list_order_dispatch_tasks(
                store_id=self._selected_store().id
                if self._selected_store() is not None
                else None
            )
        dialog = tk.Toplevel(self)
        dialog.title("自动派单任务")
        dialog.geometry("980x520")
        tree = ttk.Treeview(
            dialog,
            columns=(
                "order",
                "product",
                "sku",
                "platform",
                "quantity",
                "status",
                "url",
                "error",
            ),
            show="headings",
        )
        for key, label, width in (
            ("order", "订单号", 170),
            ("product", "商品ID", 130),
            ("sku", "店铺SKU", 150),
            ("platform", "平台", 80),
            ("quantity", "数量", 60),
            ("status", "状态", 90),
            ("url", "采购链接", 260),
            ("error", "错误", 160),
        ):
            tree.heading(key, text=label)
            tree.column(key, width=width, anchor="w")
        for task in tasks:
            tree.insert(
                "",
                "end",
                values=(
                    task.get("order_id") or "",
                    task.get("product_id") or "",
                    task.get("store_sku_id") or "",
                    task.get("platform") or "",
                    task.get("quantity") or 1,
                    task.get("status") or "",
                    task.get("source_url") or "",
                    task.get("error") or "",
                ),
            )
        tree.pack(fill="both", expand=True, padx=12, pady=12)
        ttk.Button(dialog, text="关闭", command=dialog.destroy).pack(
            pady=(0, 12)
        )

    def _create_flow_test_order(self) -> None:
        store = self._selected_store()
        if store is None:
            stores = self.db.list_stores()
            if len(stores) == 1:
                store = stores[0]
            else:
                messagebox.showinfo(
                    "请选择店铺",
                    "创建流程测试订单前请先选择具体店铺。",
                    parent=self,
                )
                return
        link_map = self.db.product_purchase_link_map()
        product_keys = [
            (store_id, product_id)
            for store_id, product_id in link_map
            if store_id == store.id
        ]
        if not product_keys:
            messagebox.showinfo(
                "没有已关联商品",
                "请先在商品管理中完成“关联规格”，再创建流程测试订单。",
                parent=self,
            )
            return
        product_id = product_keys[0][1]
        product = self.db.get_product(store.id, product_id)
        if product is None:
            messagebox.showwarning(
                "商品不存在",
                "已关联商品的本机缓存不存在，请先同步商品列表。",
                parent=self,
            )
            return
        mappings = self.db.list_product_sku_mappings(
            store.id,
            product_id,
        )
        store_sku_id = (
            str(mappings[0].get("store_sku_id") or "")
            if mappings
            else "SIM-DEFAULT"
        )
        try:
            raw_product = json.loads(product.get("raw_json") or "{}")
        except (TypeError, ValueError):
            raw_product = {}
        skus = (
            raw_product.get("skus")
            if isinstance(raw_product, dict)
            else []
        )
        sku = next(
            (
                item
                for item in skus or []
                if isinstance(item, dict)
                and str(item.get("sku_id") or "") == store_sku_id
            ),
            {},
        )
        attrs = sku.get("sku_attrs") or [
            {"attr_key": "规格", "attr_value": "流程测试"}
        ]
        now = int(datetime.now().timestamp())
        order_id = f"SIM-{datetime.now():%Y%m%d%H%M%S%f}"
        amount = int(
            sku.get("sale_price")
            or product.get("min_price")
            or 0
        )
        item = OrderItem(
            product_id=product_id,
            sku_id=store_sku_id,
            title=str(product.get("title") or "流程测试商品"),
            sku_count=1,
            sale_price=amount,
            thumb_url=str(product.get("head_img") or ""),
            sku_attributes="；".join(
                f"{entry.get('attr_key')}:{entry.get('attr_value')}"
                for entry in attrs
                if isinstance(entry, dict)
            ),
        )
        raw = {
            "is_present": False,
            "buyer_message": "流程模拟订单，不在微信平台产生真实交易",
            "order_detail": {
                "product_infos": [
                    {
                        "product_id": product_id,
                        "title": item.title,
                        "thumb_url": item.thumb_url,
                        "sku_cnt": 1,
                        "out_product_id": product.get(
                            "out_product_id"
                        )
                        or "",
                        "sku_attrs": attrs,
                    }
                ]
            },
        }
        self.db.upsert_order(
            Order(
                store_id=store.id,
                store_name=store.name,
                order_id=order_id,
                status=20,
                status_text="流程模拟待采购",
                create_time=now,
                update_time=now,
                pay_time=now,
                total_amount=amount,
                product_amount=amount,
                freight_amount=0,
                discount_amount=0,
                buyer_name="流程测试买家",
                phone="00000000000",
                address="流程测试地址（请勿发货）",
                waybill_id="",
                carrier="",
                merchant_notes="",
                items=[item],
                raw=raw,
            )
        )
        created = self.db.sync_auto_dispatch_tasks()
        self.active_tab_key = "purchase"
        self._query()
        self.window.set_status(
            f"已创建流程测试订单 {order_id}，自动派单 {created} 条"
        )

    def _simulate_purchase_success(self) -> None:
        rows = self._selected_order_rows()
        if not rows:
            messagebox.showinfo("请选择订单", "请先选择待采购订单。", parent=self)
            return
        now = datetime.now().isoformat(timespec="seconds")
        changed = 0
        for row in rows:
            stage = self._order_flow_stage(row)
            if stage not in {"pending_purchase"}:
                continue
            key = (int(row["store_id"]), str(row["order_id"]))
            purchase_no = (
                str(self._workbench_for(row).get("purchase_order_no") or "")
                or f"SIM-{str(row['order_id'])[-10:]}"
            )
            self.db.update_order_workbench(
                key[0],
                key[1],
                purchase_order_no=purchase_no,
                fulfillment_stage="purchased",
                purchased_at=now,
                is_marked=1,
                last_error="",
            )
            self._mark_dispatch_tasks(row, "purchased")
            changed += 1
        self._query()
        self.window.set_status(
            f"流程模拟：{changed} 条订单已进入已采购待发货"
        )

    def _simulate_ship_success(self) -> None:
        rows = self._selected_order_rows()
        if not rows:
            messagebox.showinfo("请选择订单", "请先选择待发货订单。", parent=self)
            return
        now = datetime.now().isoformat(timespec="seconds")
        changed = 0
        for row in rows:
            if self._order_flow_stage(row) != "purchased_pending_shipment":
                continue
            self.db.update_order_workbench(
                int(row["store_id"]),
                str(row["order_id"]),
                fulfillment_stage="shipped",
                shipped_at=now,
                is_marked_sent=1,
            )
            self._mark_dispatch_tasks(row, "shipped")
            changed += 1
        self._query()
        self.window.set_status(f"流程模拟：{changed} 条订单已进入已发货")

    def _simulate_order_complete(self) -> None:
        rows = self._selected_order_rows()
        if not rows:
            messagebox.showinfo("请选择订单", "请先选择已发货订单。", parent=self)
            return
        now = datetime.now().isoformat(timespec="seconds")
        changed = 0
        for row in rows:
            if self._order_flow_stage(row) != "shipped":
                continue
            self.db.update_order_workbench(
                int(row["store_id"]),
                str(row["order_id"]),
                fulfillment_stage="completed",
                completed_at=now,
            )
            self._mark_dispatch_tasks(row, "completed")
            changed += 1
        self._query()
        self.window.set_status(f"流程模拟：{changed} 条订单已完成")

    def _set_flow_exception(self, enabled: bool) -> None:
        rows = self._selected_order_rows()
        if not rows:
            messagebox.showinfo("请选择订单", "请先选择订单。", parent=self)
            return
        for row in rows:
            current = str(
                self._workbench_for(row).get("fulfillment_stage") or ""
            )
            key = (int(row["store_id"]), str(row["order_id"]))
            linked = (
                bool(self._workbench_for(row).get("purchase_order_no"))
                or key in self._order_account_map
            )
            stage = (
                "exception"
                if enabled
                else (
                    "purchased"
                    if current == "exception" and linked
                    else ""
                    if current == "exception"
                    else current
                )
            )
            self.db.update_order_workbench(
                int(row["store_id"]),
                str(row["order_id"]),
                fulfillment_stage=stage,
                last_error="人工标记异常" if enabled else "",
            )
        self._query()
        self.window.set_status(
            f"已将 {len(rows)} 条订单{'标记为异常' if enabled else '恢复为正常流程'}"
        )

    def _start_purchase_flow(self, *, fast: bool) -> None:
        rows = self._selected_order_rows()
        if not rows:
            messagebox.showinfo(
                "请选择订单",
                "请先选择一条订单。",
                parent=self,
            )
            return
        row = rows[0]
        stage = self._order_flow_stage(row)
        if stage != "pending_purchase":
            stage_text = {
                "purchased_pending_shipment": "已采购待发货",
                "shipped": "已发货",
                "completed": "已完成",
                "exception": "异常订单",
                "refunding": "退款中",
                "closed": "交易关闭",
            }.get(stage, "非待采购")
            messagebox.showwarning(
                "订单状态不允许采购",
                f"当前订单状态为“{stage_text}”，不能重复进入采购流程。",
                parent=self,
            )
            return
        accounts = self.db.list_procurement_accounts()
        if not accounts:
            messagebox.showinfo(
                "没有采购账号",
                "请先在采购账号中添加并登录淘宝或1688账号。",
                parent=self,
            )
            self.window.show_page("procurement")
            return
        product_ids, count = self._order_product_ids(row)
        detail = "\n".join(
            (
                f"店铺订单：{row.get('order_id') or ''}",
                f"商品：{row.get('item_summary') or ''}",
                f"商品ID：{', '.join(sorted(product_ids)) or '待同步'}",
                f"数量：{count}",
                f"采购模式：{'极速下单' if fast else '手动下单'}",
            )
        )
        self.clipboard_clear()
        self.clipboard_append(detail)
        self.db.set_setting(
            "procurement_focus",
            json.dumps(
                {
                    "store_id": int(row["store_id"]),
                    "order_id": str(row["order_id"]),
                    "product_ids": sorted(product_ids),
                    "count": count,
                    "mode": "fast" if fast else "manual",
                },
                ensure_ascii=False,
            ),
        )
        self.window.show_page("procurement")
        self.window.set_status(
            "订单采购信息已复制，并已打开采购中心；选择采购账号后完成下单。"
        )

    def _open_product_link_for_order(self) -> None:
        rows = self._selected_order_rows()
        if len(rows) != 1:
            messagebox.showinfo(
                "请选择一条订单",
                "关联商品时请只选择一条店铺订单。",
                parent=self,
            )
            return
        row = rows[0]
        product_ids, _count = self._order_product_ids(row)
        product_id = next(iter(sorted(product_ids)), "")
        if not product_id:
            messagebox.showinfo(
                "缺少商品ID",
                "该订单尚未同步商品ID，请先刷新订单。",
                parent=self,
            )
            return
        store_id = int(row["store_id"])
        product = self.db.get_product(store_id, product_id)
        if product is None:
            messagebox.showinfo(
                "商品尚未缓存",
                "请先到商品管理中同步该店铺商品，再返回关联商品。",
                parent=self,
            )
            return

        def saved() -> None:
            created = self.db.sync_auto_dispatch_tasks()
            self._query()
            self.window.set_status(
                f"商品关联已保存，自动派单 {created} 条"
            )

        ProductPurchaseLinkDialog(
            self,
            db=self.db,
            window=self.window,
            store_id=store_id,
            product=product,
            on_saved=saved,
        )

    def _edit_order_product_title(self) -> None:
        rows = self._selected_order_rows()
        if len(rows) != 1:
            messagebox.showinfo(
                "请选择一条订单",
                "修改商品标题时请只选择一条店铺订单。",
                parent=self,
            )
            return
        row = rows[0]
        product_ids, _count = self._order_product_ids(row)
        product_id = next(iter(sorted(product_ids)), "")
        if not product_id:
            messagebox.showinfo(
                "缺少商品ID",
                "该订单尚未同步商品ID，请先刷新订单。",
                parent=self,
            )
            return
        store = self.db.get_store(int(row["store_id"]))
        product = self.db.get_product(int(row["store_id"]), product_id)
        if store is None or product is None:
            messagebox.showinfo(
                "商品尚未缓存",
                "请先在商品管理中同步该店铺商品，再修改商品标题。",
                parent=self,
            )
            return
        try:
            raw = json.loads(product.get("raw_json") or "{}")
        except (TypeError, ValueError):
            raw = {}
        payload = ProductsPage._editable_product_payload(raw)
        payload["product_id"] = product_id
        current_title = str(
            product.get("title")
            or payload.get("title")
            or row.get("item_summary")
            or ""
        )

        def save(title: str) -> None:
            payload["title"] = title
            self.window.set_status("正在修改商品标题…", busy=True)

            def done(_result) -> None:
                ui_state = self._order_ui_state.setdefault(
                    f"{int(row['store_id'])}:{str(row['order_id'])}",
                    {},
                )
                ui_state["title"] = title
                self.window.set_status("商品标题已修改")
                self._query()

            self.window.run_task(
                lambda _progress: self.window.service.product_write(
                    store,
                    "update",
                    payload,
                    product_id=product_id,
                ),
                done,
                lambda error: messagebox.showerror(
                    "修改商品标题失败",
                    error,
                    parent=self,
                ),
                log_title="订单卡片：修改商品标题",
            )

        ProductTitleEditDialog(self, current_title, save)

    def _manual_link_procurement_order(self) -> None:
        rows = self._selected_order_rows()
        if len(rows) != 1:
            messagebox.showinfo(
                "请选择一条订单",
                "补录采购订单时请只选择一条店铺订单。",
                parent=self,
            )
            return
        row = rows[0]
        initial: dict[str, Any] = {}
        try:
            remembered = json.loads(
                self.db.get_setting("order_purchase_info", "{}")
            )
            if isinstance(remembered, dict):
                initial.update(remembered)
        except (TypeError, ValueError):
            pass

        def save(values: dict[str, Any]) -> None:
            platform = str(values["platform"])
            purchase_order_no = str(values["purchase_order_no"])
            amount = values["purchase_amount"]
            commission = values["commission"]
            purchase_mode = str(values["purchase_mode"])
            transfer_warehouse = bool(values["transfer_warehouse"])
            flag = str(values.get("flag") or "")
            accounts = self.db.list_procurement_accounts(platform)
            account = next(
                (
                    item
                    for item in accounts
                    if "手动补录" in str(item.get("label") or "")
                ),
                None,
            )
            account_id = (
                int(account["id"])
                if account is not None
                else self.db.add_procurement_account(
                    platform,
                    "手动补录",
                )
            )
            cents = int(amount * 100)
            procurement_id = self.db.upsert_procurement_order(
                account_id,
                platform,
                {
                    "external_order_id": purchase_order_no,
                    "trade_no": purchase_order_no,
                    "status": "manual",
                    "status_text": "手动补录",
                    "goods_title": str(row.get("item_summary") or ""),
                    "goods_count": 1,
                    "pay_amount": cents,
                    "raw": {
                        "purchase_mode": purchase_mode,
                        "transfer_warehouse": transfer_warehouse,
                        "commission_pct": float(commission),
                        "flag": flag,
                        "store_order_id": str(row["order_id"]),
                    },
                },
            )
            self.db.upsert_procurement_link(
                int(row["store_id"]),
                str(row["order_id"]),
                procurement_id,
                match_type="manual",
                confidence=100,
                local_note=(
                    f"{purchase_mode}；"
                    f"中转仓：{'是' if transfer_warehouse else '否'}；"
                    f"佣金：{float(commission):g}%"
                ),
            )
            self.db.update_order_workbench(
                int(row["store_id"]),
                str(row["order_id"]),
                purchase_order_no=purchase_order_no,
                flag_color=flag,
                is_marked=1,
                cost=cents,
                fulfillment_stage="purchased",
                purchased_at=datetime.now().isoformat(timespec="seconds"),
            )
            if values.get("remember"):
                self.db.set_setting(
                    "order_purchase_info",
                    json.dumps(
                        {
                            "purchase_mode": purchase_mode,
                            "flag": flag,
                            "transfer_warehouse": "是"
                            if transfer_warehouse
                            else "否",
                            "platform": platform,
                            "purchase_amount": str(amount),
                            "commission": str(commission),
                            "remember": True,
                        },
                        ensure_ascii=False,
                    ),
                )
            else:
                self.db.set_setting("order_purchase_info", "{}")
            self._mark_dispatch_tasks(row, "purchased")
            self._query()
            self.window.set_status(
                f"已添加采购信息：{purchase_order_no}"
            )

        PurchaseInfoDialog(
            self,
            order_id=str(row["order_id"]),
            on_save=save,
            initial=initial,
        )

    def _copy_pickup_codes(self) -> None:
        rows = self._selected_order_rows()
        codes = [
            str(self._workbench_for(row).get("pickup_code") or "").strip()
            for row in rows
        ]
        codes = [code for code in codes if code]
        if not codes:
            messagebox.showinfo("没有取件码", "当前选择中没有可取件码。", parent=self)
            return
        self.clipboard_clear()
        self.clipboard_append("\n".join(codes))
        self.window.set_status(f"已复制 {len(codes)} 条取件码")

    def _set_marked_sent(self, marked_sent: bool) -> None:
        rows = self._selected_order_rows()
        if not rows:
            messagebox.showinfo("请选择订单", "请先选择至少一条订单。", parent=self)
            return
        for row in rows:
            current_stage = self._order_flow_stage(row)
            self.db.update_order_workbench(
                int(row["store_id"]),
                str(row["order_id"]),
                is_marked_sent=1 if marked_sent else 0,
                fulfillment_stage=(
                    "shipped"
                    if marked_sent
                    else (
                        "purchased"
                        if current_stage == "shipped"
                        else str(
                            self._workbench_for(row).get(
                                "fulfillment_stage"
                            )
                            or ""
                        )
                    )
                ),
                shipped_at=(
                    datetime.now().isoformat(timespec="seconds")
                    if marked_sent
                    else ""
                ),
            )
            if marked_sent:
                self._mark_dispatch_tasks(row, "shipped")
        self._query()
        self.window.set_status(
            f"已{'标记' if marked_sent else '取消标记'} {len(rows)} 条订单"
        )

    def _show_1688_account(self) -> None:
        self.window.show_page("procurement")

    def _open_workbench(self) -> None:
        from .order_workbench import open_order_workbench

        open_order_workbench(self.window, self.window)

    def _sync_pickup_codes(self) -> None:
        accounts = self.db.list_procurement_accounts()
        if not accounts:
            messagebox.showinfo(
                "没有采购账号",
                "请先在采购中心添加并登录淘宝或1688账号。",
                parent=self,
            )
            self.window.show_page("procurement")
            return
        selected_store = self._selected_store()
        stores = (
            [selected_store]
            if selected_store is not None
            else list(self.db.list_stores())
        )
        self.window.set_status("正在同步采购订单与取件码…", busy=True)

        def work(_progress) -> list[str]:
            errors = []
            for account in accounts:
                try:
                    self.window.service.sync_procurement_account(
                        int(account["id"])
                    )
                except Exception as exc:
                    errors.append(f"{account['label']}: {exc}")
            for store in stores:
                try:
                    self.window.service.match_procurement_orders(store)
                except Exception as exc:
                    errors.append(f"{store.name}: {exc}")
            return errors

        def done(errors: list[str]) -> None:
            self._query()
            self.window.set_status(
                f"采购同步完成，处理 {len(accounts)} 个账号、"
                f"{len(stores)} 家店铺，失败 {len(errors)} 项"
            )
            if errors:
                messagebox.showwarning(
                    "部分采购同步失败",
                    "\n".join(errors[:8]),
                    parent=self,
                )

        self.window.run_task(
            work,
            done,
            lambda error: messagebox.showerror("采购同步失败", error, parent=self),
            log_title="同步采购订单与取件码",
        )

    def _show_order_menu(self, event) -> None:
        identify = getattr(self.tree, "identify_row", None)
        if callable(identify):
            iid = identify(event.y)
            if iid:
                self.tree.selection_set(iid)
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label="打开微信订单详情", command=self._open_official_order)
        menu.add_command(label="本地详情", command=self._show_local_detail)
        menu.add_command(label="解密收货信息", command=self._decode_sensitive)
        menu.add_separator()
        menu.add_command(label="修改备注", command=self._edit_notes)
        flag_menu = tk.Menu(menu, tearoff=0)
        for color in self.order_flag_colors:
            flag_menu.add_command(
                label=f"{color}旗",
                command=lambda value=color: self._set_order_flags(value),
            )
        flag_menu.add_separator()
        flag_menu.add_command(
            label="清除旗帜",
            command=lambda: self._set_order_flags(""),
        )
        menu.add_cascade(label="旗帜颜色", menu=flag_menu)
        menu.add_command(label="修改价格", command=self._edit_price)
        menu.add_command(label="申请改址", command=self._request_address)
        menu.add_command(label="修改物流信息", command=self._edit_delivery)
        menu.add_separator()
        menu.add_command(
            label="补录采购订单",
            command=self._manual_link_procurement_order,
        )
        menu.add_command(
            label="打开采购中心",
            command=lambda: self._start_purchase_flow(fast=False),
        )
        menu.add_separator()
        menu.add_command(label="复制订单号", command=lambda: self._copy_order_value("order_id"))
        menu.add_command(
            label="复制1688订单号",
            command=lambda: self._copy_order_value("purchase_order_no"),
        )
        menu.add_command(label="复制取件码", command=self._copy_pickup_codes)
        menu.add_command(label="标记已发", command=lambda: self._set_marked_sent(True))
        menu.add_command(label="取消已发", command=lambda: self._set_marked_sent(False))
        menu.add_command(label="忽略订单", command=self._batch_ignore_orders)
        menu.add_command(label="取消忽略", command=self._batch_unignore_orders)
        menu.add_command(
            label="标记异常订单",
            command=lambda: self._set_flow_exception(True),
        )
        menu.add_command(
            label="恢复异常订单",
            command=lambda: self._set_flow_exception(False),
        )
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _copy_order_value(self, field: str) -> None:
        rows = self._selected_order_rows()
        if not rows:
            return
        row = rows[0]
        if field == "order_id":
            value = row.get("order_id")
        else:
            value = self._workbench_for(row).get(field)
            if not value and field == "purchase_order_no":
                links = self.db.list_procurement_links(
                    int(row["store_id"]),
                    str(row["order_id"]),
                )
                value = " ".join(
                    str(link.get("external_order_id") or "").strip()
                    for link in links
                    if str(link.get("external_order_id") or "").strip()
                )
        if not value:
            messagebox.showinfo("没有内容", "当前订单没有可复制的值。", parent=self)
            return
        self.clipboard_clear()
        self.clipboard_append(str(value))
        self.window.set_status("已复制到剪贴板")

    def _sync_orders(self) -> None:
        selected = self._selected_store()
        stores = (
            [selected]
            if selected is not None
            else list(self.db.list_stores())
        )
        if not stores:
            messagebox.showinfo("没有店铺", "请先添加微信小店。", parent=self)
            return
        self._sync_order_stores(stores, "同步订单")

    def _sync_recent_orders(self) -> None:
        selected = self._selected_store()
        stores = (
            [selected]
            if selected is not None
            else list(self.db.list_stores())
        )
        if not stores:
            messagebox.showinfo("没有店铺", "请先添加微信小店。", parent=self)
            return
        end_time = int(datetime.now().timestamp())
        start_time = end_time - 7 * 86400
        self.window.set_status("正在增量同步近期订单…", busy=True)

        def work(progress) -> list[str]:
            errors: list[str] = []
            for index, store in enumerate(stores, 1):
                progress(
                    f"正在同步 {store.name} 的近期订单",
                    index - 1,
                    len(stores),
                )
                try:
                    result = self.window.service.sync_recent_order_updates(
                        store,
                        start_time,
                        end_time,
                    )
                    errors.extend(
                        f"{store.name}: {error}"
                        for error in getattr(result, "errors", []) or []
                    )
                except Exception as exc:
                    errors.append(f"{store.name}: {exc}")
            return errors

        def done(errors: list[str]) -> None:
            self._query()
            self.window.set_status(
                f"近期订单增量同步完成，失败 {len(errors)} 项",
                busy=False,
            )
            if errors:
                messagebox.showwarning(
                    "部分订单增量同步失败",
                    "\n".join(errors[:10]),
                    parent=self,
                )

        self.window.run_task(
            work,
            done,
            lambda error: messagebox.showerror(
                "近期订单增量同步失败",
                error,
                parent=self,
            ),
            self._progress,
            log_title="快速同步近期订单",
        )

    def _sync_selected_orders(self) -> None:
        rows = self._selected_order_rows()
        if not rows:
            messagebox.showinfo(
                "请选择订单",
                "请先选择需要同步的订单。",
                parent=self,
            )
            return
        self.window.set_status("正在同步勾选订单…", busy=True)

        def work(progress) -> list[str]:
            errors: list[str] = []
            for index, row in enumerate(rows, 1):
                progress(
                    f"正在同步订单 {index}/{len(rows)}",
                    index - 1,
                    len(rows),
                )
                store = self.db.get_store(int(row["store_id"]))
                if store is None:
                    errors.append(f"{row['order_id']}: 店铺不存在")
                    continue
                try:
                    self.window.service.sync_order(
                        store,
                        str(row["order_id"]),
                    )
                except Exception as exc:
                    errors.append(f"{row['order_id']}: {exc}")
            return errors

        def done(errors: list[str]) -> None:
            self._query()
            self.window.set_status(
                f"勾选订单同步完成，成功 "
                f"{len(rows) - len(errors)} 条，失败 {len(errors)} 条",
                busy=False,
            )
            if errors:
                messagebox.showwarning(
                    "部分勾选订单同步失败",
                    "\n".join(errors[:10]),
                    parent=self,
                )

        self.window.run_task(
            work,
            done,
            lambda error: messagebox.showerror(
                "勾选订单同步失败",
                error,
                parent=self,
            ),
            self._progress,
            log_title="同步勾选订单",
        )

    def _sync_order_stores(self, stores: list[Store], label: str) -> None:
        if not stores:
            messagebox.showinfo("没有店铺", "没有可同步的店铺。", parent=self)
            return
        start_text = self.start_var.get().strip() or (
            date.today() - timedelta(days=6)
        ).isoformat()
        end_text = self.end_var.get().strip() or date.today().isoformat()
        try:
            start_time, end_time = date_range(start_text, end_text)
        except ValueError as exc:
            messagebox.showwarning("日期范围", str(exc), parent=self)
            return

        self.window.set_status(f"正在{label}…", busy=True)

        def work(progress) -> list[str]:
            errors: list[str] = []
            for index, store in enumerate(stores, 1):
                progress(
                    f"正在同步 {store.name} 的订单",
                    index - 1,
                    len(stores),
                )
                try:
                    self.window.service.sync_orders(
                        store,
                        start_time,
                        end_time,
                        lambda _message, current, total: None,
                    )
                except Exception as exc:
                    errors.append(f"{store.name}: {exc}")
            progress(label, len(stores), len(stores))
            return errors

        def done(errors: list[str]) -> None:
            self.window.set_status(
                f"{label}完成，成功 {len(stores) - len(errors)} 家，"
                f"失败 {len(errors)} 家",
                busy=False,
            )
            self._reload_stores()
            self._query()
            if errors:
                messagebox.showwarning(
                    f"{label}部分失败",
                    "\n".join(errors[:10]),
                    parent=self,
                )

        self.window.run_task(
            work,
            done,
            lambda error: messagebox.showerror(
                f"{label}失败",
                error,
                parent=self,
            ),
            self._progress,
            log_title=label,
        )

    def _progress(self, message: str, current: int, total: int) -> None:
        self.window.set_status(message, busy=True)
        self.progress_var.set((current / total * 100) if total else 0)

    def _decode_sensitive(self) -> None:
        selection = self.tree.selection()
        if not selection:
            messagebox.showinfo("请选择订单", "请先选择一条订单。", parent=self)
            return
        row = self._rows.get(selection[0])
        if not row:
            return
        store = self.db.get_store(row["store_id"])
        if store is None:
            return
        address_text = str(row.get("address") or "")
        if (
            str(row.get("buyer_name") or "").strip()
            and address_text.strip()
            and "****" not in address_text
            and "****" not in str(row.get("phone") or "")
        ):
            self._show_order_address_popup(row)
            return
        if not messagebox.askyesno(
            "解密收货信息",
            "该操作会调用微信官方解密接口，并可能消耗当天的订单解密额度。\n"
            "解密结果只保存在本机数据库。是否继续？",
            parent=self,
        ):
            return
        self.window.set_status("正在解密收货信息…", busy=True)

        def done(_result) -> None:
            self.window.set_status("收货信息已解密并保存到本机")
            self._query()
            self._show_order_address_popup(row)

        self.window.run_task(
            lambda _progress: self.window.service.decode_order_sensitive(store, row["order_id"]),
            done,
            lambda error: messagebox.showerror("解密失败", error, parent=self),
            log_title="解密订单收货信息",
        )

    def _copy_order_address(self) -> None:
        rows = self._selected_order_rows()
        if not rows:
            messagebox.showinfo(
                "请选择订单",
                "请先选择一条订单。",
                parent=self,
            )
            return
        row = rows[0]
        parts = [
            str(row.get("buyer_name") or "").strip(),
            str(row.get("phone") or "").strip(),
            str(row.get("address") or "").strip(),
        ]
        value = "，".join(part for part in parts if part)
        if not value:
            messagebox.showinfo(
                "没有地址",
                "当前订单没有可复制的收货信息，请先点击解密。",
                parent=self,
            )
            return
        self.clipboard_clear()
        self.clipboard_append(value)
        self.window.set_status("收货人、电话和地址已复制")

    def _show_order_address_popup(self, row: dict[str, Any]) -> None:
        order = self.db.get_order(
            int(row["store_id"]),
            str(row["order_id"]),
        )
        if order is None:
            return
        dialog = tk.Toplevel(self)
        dialog.title(f"订单收货信息 · {order.order_id}")
        dialog.geometry("620x300")
        dialog.transient(self.winfo_toplevel())
        dialog.grab_set()
        panel = ttk.Frame(dialog, padding=18)
        panel.pack(fill="both", expand=True)
        panel.columnconfigure(1, weight=1)
        fields = (
            ("收货人", order.buyer_name),
            ("电话/虚拟号", order.phone),
            ("收货地址", order.address),
        )
        for index, (label, value) in enumerate(fields):
            ttk.Label(panel, text=label).grid(
                row=index,
                column=0,
                sticky="nw",
                padx=(0, 12),
                pady=8,
            )
            ttk.Label(
                panel,
                text=value or "—",
                wraplength=460,
                justify="left",
            ).grid(
                row=index,
                column=1,
                sticky="nw",
                pady=8,
            )

        def copy_values() -> None:
            text = "，".join(
                value
                for value in (
                    order.buyer_name,
                    order.phone,
                    order.address,
                )
                if value
            )
            self.clipboard_clear()
            self.clipboard_append(text)
            self.window.set_status("收货人、电话和地址已复制")

        def edit_address() -> None:
            dialog.destroy()
            iid = next(
                (
                    current_iid
                    for current_iid, current in self._rows.items()
                    if int(current["store_id"]) == int(row["store_id"])
                    and str(current["order_id"]) == str(row["order_id"])
                ),
                "",
            )
            if not iid:
                return
            self.tree.selection_set((iid,))
            self._request_address()

        actions = ttk.Frame(panel)
        actions.grid(
            row=len(fields),
            column=0,
            columnspan=2,
            sticky="e",
            pady=(18, 0),
        )
        ttk.Button(actions, text="关闭", command=dialog.destroy).pack(
            side="right"
        )
        ttk.Button(
            actions,
            text="修改地址",
            command=edit_address,
        ).pack(side="right", padx=8)
        ttk.Button(
            actions,
            text="复制信息",
            command=copy_values,
            style="Primary.TButton",
        ).pack(side="right")

    def _export(
        self,
        extension: str = "xlsx",
        selected_only: bool = False,
    ) -> None:
        export_rows = (
            self._selected_order_rows()
            if selected_only
            else self._filtered_rows
        )
        if not export_rows:
            messagebox.showinfo("没有数据", "当前筛选结果中没有可导出的订单。", parent=self)
            return
        path = filedialog.asksaveasfilename(
            parent=self,
            title="导出订单",
            defaultextension=f".{extension}",
            filetypes=(
                [("Excel 工作簿", "*.xlsx")]
                if extension == "xlsx"
                else [("CSV 文件", "*.csv")]
            ),
            initialfile=f"订单_{date.today():%Y%m%d}.{extension}",
        )
        if not path:
            return
        headers = [
            "序号",
            "店铺",
            "订单号",
            "状态",
            "下单时间",
            "商品信息",
            "实付金额",
            "利润",
            "成本",
            "1688订单号",
            "物流信息",
            "取件码",
            "备注",
            "标记",
        ]
        rows = [
            [
                index,
                row["store_name"],
                row["order_id"],
                row["status_text"],
                _format_datetime(row["create_time"]),
                row["item_summary"] or "",
                row["total_amount"] / 100,
                self._workbench_for(row).get("profit", 0) / 100,
                self._workbench_for(row).get("cost", 0) / 100,
                self._workbench_for(row).get("purchase_order_no") or "",
                " ".join(
                    value
                    for value in (
                        str(row.get("carrier") or ""),
                        str(row.get("waybill_id") or ""),
                    )
                    if value
                ),
                self._workbench_for(row).get("pickup_code") or "",
                self._workbench_for(row).get("note")
                or row.get("merchant_notes")
                or "",
                "已发" if self._workbench_for(row).get("is_marked_sent") else "",
            ]
            for index, row in enumerate(export_rows, 1)
        ]
        try:
            if extension == "xlsx":
                export_xlsx(path, "订单", headers, rows)
            else:
                export_csv(path, headers, rows)
        except Exception as exc:
            messagebox.showerror("导出失败", str(exc), parent=self)
            return
        self.window.set_status(f"已导出：{Path(path).name}")

    def _import_order_workbench(self) -> None:
        path = filedialog.askopenfilename(
            parent=self,
            title="导入订单工作台数据",
            filetypes=[
                ("Excel工作簿", "*.xlsx"),
                ("CSV文件", "*.csv"),
                ("所有文件", "*.*"),
            ],
        )
        if not path:
            return
        source = Path(path)
        try:
            if source.suffix.lower() == ".xlsx":
                from openpyxl import load_workbook

                workbook = load_workbook(source, read_only=True, data_only=True)
                sheet = workbook.active
                values = list(sheet.iter_rows(values_only=True))
                workbook.close()
                headers = [str(value or "").strip() for value in values[0]]
                records = [
                    dict(zip(headers, row))
                    for row in values[1:]
                    if any(value not in (None, "") for value in row)
                ]
            else:
                import csv

                with source.open("r", encoding="utf-8-sig", newline="") as handle:
                    records = list(csv.DictReader(handle))
        except Exception as exc:
            messagebox.showerror("导入失败", str(exc), parent=self)
            return
        aliases = {
            "订单号": "order_id",
            "1688订单号": "purchase_order_no",
            "采购单号": "purchase_order_no",
            "取件码": "pickup_code",
            "备注": "note",
            "标记已发": "is_marked_sent",
        }
        imported = 0
        missing = []
        for raw in records:
            normalized = {}
            for key, value in raw.items():
                normalized[aliases.get(str(key).strip(), str(key).strip())] = value
            order_id = str(normalized.get("order_id") or "").strip()
            if not order_id:
                continue
            store = self._selected_store()
            candidates = self.db.list_orders(
                store_id=store.id if store else None,
                search=order_id,
            )
            matches = [
                row for row in candidates
                if str(row["order_id"]) == order_id
            ]
            if not matches:
                missing.append(order_id)
                continue
            for row in matches:
                self.db.update_order_workbench(
                    int(row["store_id"]),
                    order_id,
                    purchase_order_no=str(
                        normalized.get("purchase_order_no") or ""
                    ).strip(),
                    pickup_code=str(normalized.get("pickup_code") or "").strip(),
                    note=str(normalized.get("note") or "").strip(),
                    is_marked_sent=1
                    if str(normalized.get("is_marked_sent") or "").lower()
                    in {"1", "true", "yes", "是", "已发"}
                    else 0,
                )
                imported += 1
        self._query()
        message = f"已导入 {imported} 条订单工作台记录。"
        if missing:
            message += f"\n未找到 {len(missing)} 个订单。"
        messagebox.showinfo("导入完成", message, parent=self)

    def _download_order_template(self) -> None:
        path = filedialog.asksaveasfilename(
            parent=self,
            title="保存发货模板",
            defaultextension=".xlsx",
            filetypes=(("Excel 工作簿", "*.xlsx"), ("CSV 文件", "*.csv")),
            initialfile=f"订单发货模板_{date.today():%Y%m%d}.xlsx",
        )
        if not path:
            return
        rows = self._selected_order_rows() or self._filtered_rows
        headers = ["订单号", "快递公司编码", "运单号"]
        values = [
            [row.get("order_id") or "", "", ""]
            for row in rows
        ]
        if not values:
            values = [["", "", ""]]
        try:
            if Path(path).suffix.lower() == ".csv":
                export_csv(path, headers, values)
            else:
                export_xlsx(path, "发货模板", headers, values)
        except Exception as exc:
            messagebox.showerror("模板生成失败", str(exc), parent=self)
            return
        self.window.set_status(f"发货模板已保存：{Path(path).name}")

    def _read_delivery_import(self, path: str) -> list[dict[str, str]]:
        source = Path(path)
        if source.suffix.lower() == ".xlsx":
            from openpyxl import load_workbook

            workbook = load_workbook(source, read_only=True, data_only=True)
            sheet = workbook.active
            values = list(sheet.iter_rows(values_only=True))
            workbook.close()
            if not values:
                return []
            headers = [str(value or "").strip() for value in values[0]]
            records = [
                dict(zip(headers, row))
                for row in values[1:]
                if any(value not in (None, "") for value in row)
            ]
        else:
            import csv

            with source.open("r", encoding="utf-8-sig", newline="") as handle:
                records = list(csv.DictReader(handle))
        aliases = {
            "订单编号": "order_id",
            "店铺订单号": "order_id",
            "快递公司": "delivery_id",
            "快递编码": "delivery_id",
            "物流单号": "waybill_id",
            "快递单号": "waybill_id",
        }
        return [
            {
                aliases.get(str(key).strip(), str(key).strip()): str(
                    value or ""
                ).strip()
                for key, value in record.items()
            }
            for record in records
        ]

    def _import_deliveries(self) -> None:
        path = filedialog.askopenfilename(
            parent=self,
            title="导入订单发货信息",
            filetypes=(
                ("Excel 工作簿", "*.xlsx"),
                ("CSV 文件", "*.csv"),
                ("所有文件", "*.*"),
            ),
        )
        if not path:
            return
        try:
            records = self._read_delivery_import(path)
        except Exception as exc:
            messagebox.showerror("读取发货文件失败", str(exc), parent=self)
            return
        valid = [
            record
            for record in records
            if record.get("order_id")
            and record.get("delivery_id")
            and record.get("waybill_id")
        ]
        if not valid:
            messagebox.showwarning(
                "没有有效发货数据",
                "文件中需要包含订单号、快递公司编码和运单号。",
                parent=self,
            )
            return
        if not messagebox.askyesno(
            "导入并发货",
            f"将向微信小店提交 {len(valid)} 条发货信息。\n"
            "提交前请确认订单号、快递公司编码和运单号无误。是否继续？",
            parent=self,
        ):
            return
        self.window.set_status("正在批量发货…", busy=True)

        def work(progress) -> list[str]:
            errors: list[str] = []
            for index, record in enumerate(valid, 1):
                progress(
                    f"正在处理发货 {index}/{len(valid)}",
                    index,
                    len(valid),
                )
                order_id = record["order_id"]
                selected_store = self._selected_store()
                candidates = self.db.list_orders(
                    store_id=selected_store.id
                    if selected_store is not None
                    else None,
                    search=order_id,
                )
                matches = [
                    row
                    for row in candidates
                    if str(row["order_id"]) == order_id
                ]
                if len(matches) != 1:
                    errors.append(
                        f"{order_id}: 找到 {len(matches)} 条店铺订单"
                    )
                    continue
                order_row = matches[0]
                store = self.db.get_store(int(order_row["store_id"]))
                order = self.db.get_order(
                    int(order_row["store_id"]),
                    order_id,
                )
                if store is None or order is None:
                    errors.append(f"{order_id}: 本地订单不存在")
                    continue
                products = [
                    {
                        "product_id": item.product_id,
                        "sku_id": item.sku_id,
                        "product_cnt": item.sku_count,
                    }
                    for item in order.items
                ]
                try:
                    self.window.service.update_order_delivery_info(
                        store,
                        order_id,
                        record["waybill_id"],
                        record["delivery_id"],
                        products,
                    )
                    self.db.update_order_workbench(
                        int(order_row["store_id"]),
                        order_id,
                        fulfillment_stage="shipped",
                        shipped_at=datetime.now().isoformat(
                            timespec="seconds"
                        ),
                        is_marked_sent=1,
                    )
                except Exception as exc:
                    errors.append(f"{order_id}: {exc}")
            return errors

        def done(errors: list[str]) -> None:
            self._query()
            self.window.set_status(
                f"发货导入完成，成功 {len(valid) - len(errors)} 条，"
                f"失败 {len(errors)} 条",
                busy=False,
            )
            if errors:
                messagebox.showwarning(
                    "部分订单发货失败",
                    "\n".join(errors[:12]),
                    parent=self,
                )

        self.window.run_task(
            work,
            done,
            lambda error: messagebox.showerror(
                "批量发货失败",
                error,
                parent=self,
            ),
            lambda message, current, total: self.window.set_status(
                f"{message} {current}/{total}",
                busy=True,
            ),
            log_title="导入订单发货",
        )

    def _batch_ship_orders(self) -> None:
        self._import_deliveries()

    def _sync_procurement_note(self) -> None:
        selection = self.tree.selection()
        if not selection:
            messagebox.showinfo("请选择订单", "请先选择一条订单。", parent=self)
            return
        row = self._rows.get(selection[0])
        if row is None:
            return
        store = self.db.get_store(int(row["store_id"]))
        if store is None:
            return
        links = self.db.list_procurement_links(store.id, str(row["order_id"]))
        if not links:
            messagebox.showinfo(
                "没有采购关联",
                "该订单还没有关联淘宝或1688采购记录。",
                parent=self,
            )
            return
        if not messagebox.askyesno(
            "同步采购备注",
            "将把采购信息追加到微信订单商家备注，是否继续？",
            parent=self,
        ):
            return
        self.window.set_status("正在同步采购备注到微信订单…", busy=True)
        self.window.run_task(
            lambda _progress: self.window.service.sync_procurement_note_to_wechat(
                store,
                str(row["order_id"]),
            ),
            lambda _result: self._query(),
            lambda error: messagebox.showerror(
                "同步采购备注失败",
                error,
                parent=self,
            ),
            log_title="同步采购备注到微信订单",
        )

    def _open_official_order(self, _event=None) -> None:
        selection = self.tree.selection()
        if not selection:
            return
        row = self._rows.get(selection[0])
        if not row:
            return
        store = self.db.get_store(row["store_id"])
        if store is None:
            messagebox.showerror("店铺不存在", "订单所属店铺已被删除。", parent=self)
            return
        self.window.set_status(f"正在打开订单 {row['order_id']} 的微信官方详情…", busy=True)

        def done(_result) -> None:
            self.window.set_status("微信官方订单详情已关闭")
            self.window.store_data_changed()

        self.window.run_task(
            lambda _progress: self.window.web_session.open_official_order(
                store,
                str(row["order_id"]),
            ),
            done,
            lambda error: messagebox.showerror(
                "打开官方订单失败",
                error,
                parent=self,
            ),
            log_title="打开微信官方订单详情",
        )

    def _show_local_detail(self, _event=None) -> None:
        selection = self.tree.selection()
        if not selection:
            return
        row = self._rows.get(selection[0])
        if not row:
            return
        order = self.db.get_order(row["store_id"], row["order_id"])
        if order is None:
            return
        data = asdict(order)
        data["items"] = [asdict(item) for item in order.items]
        OrderDetailDialog(self, data)

    def _selected_order_context(self):
        selection = self.tree.selection()
        if not selection:
            messagebox.showinfo("请选择订单", "请先选择一条订单。", parent=self)
            return None, None
        row = self._rows.get(selection[0])
        if not row:
            return None, None
        store = self.db.get_store(row["store_id"])
        if store is None:
            return None, None
        order = self.db.get_order(store.id, row["order_id"])
        return store, order

    def _edit_notes(self) -> None:
        self._open_order_flag_note_dialog()

    def _edit_price(self) -> None:
        store, order = self._selected_order_context()
        if store is None or order is None:
            return

        def save(changes: list[dict], change_express: bool, fee: int) -> None:
            self._submit_order_change(
                "修改价格",
                lambda: self.window.service.update_order_price(
                    store,
                    order.order_id,
                    changes,
                    change_express,
                    fee,
                ),
            )

        OrderPriceDialog(self, order, save)

    def _request_address(self) -> None:
        store, order = self._selected_order_context()
        if store is None or order is None:
            return
        address_info = (
            order.raw.get("order_detail", {})
            .get("delivery_info", {})
            .get("address_info", {})
        )

        def save(address: dict) -> None:
            self._submit_order_change(
                "提交改址申请",
                lambda: self.window.service.request_order_address_change(
                    store,
                    order.order_id,
                    address,
                ),
            )

        OrderAddressDialog(self, address_info, save)

    def _edit_delivery(self) -> None:
        store, order = self._selected_order_context()
        if store is None or order is None:
            return

        def save(packages: list[dict[str, str]]) -> None:
            products = [
                {
                    "product_id": item.product_id,
                    "sku_id": item.sku_id,
                    "product_cnt": item.sku_count,
                }
                for item in order.items
            ]
            self._submit_order_change(
                "手动发货",
                lambda: self.window.service.update_order_delivery_packages(
                    store,
                    order.order_id,
                    packages,
                    products,
                ),
            )

        ManualShipmentDialog(
            self,
            order_id=order.order_id,
            on_save=save,
        )

    def _submit_order_change(
        self,
        title: str,
        operation,
        after_success: Callable[[], None] | None = None,
    ) -> None:
        if not messagebox.askyesno(
            title,
            f"即将向微信小店提交“{title}”操作。是否继续？",
            parent=self,
        ):
            return
        self.window.set_status(f"正在{title}…", busy=True)

        def done(_result) -> None:
            if after_success is not None:
                after_success()
            self.window.set_status(f"{title}成功")
            self._query()

        self.window.run_task(
            lambda _progress: operation(),
            done,
            lambda error: messagebox.showerror(f"{title}失败", error, parent=self),
            log_title=f"微信订单操作：{title}",
        )


class ExtractPage(BasePage):
    title = "订单提取"
    subtitle = "从微信小店拉取待发货订单，解析收货信息并导出工作表"

    def __init__(self, parent: tk.Widget, window: "MainWindow"):
        super().__init__(parent, window)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(4, weight=1)
        self._store_lookup: dict[str, Store | None] = {"全部店铺": None}
        self.group_var = tk.StringVar(value="全部分组")
        self.store_var = tk.StringVar(value="全部店铺")
        self.template_var = tk.StringVar(value="请选择店铺")
        self.monthly_card_var = tk.StringVar(value="请选择店铺")
        self.progress_var = tk.DoubleVar(value=0)
        self.state_var = tk.StringVar(value="准备就绪")
        self.select_all_var = tk.BooleanVar(value=False)
        self.status_filter_var = tk.StringVar(value="未解析")
        self.search_var = tk.StringVar()
        self._rows: dict[str, dict[str, Any]] = {}
        self._workbench: dict[tuple[int, str], dict[str, Any]] = {}

        filters = ttk.Frame(self, style="TFrame")
        filters.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ttk.Label(filters, text="分组").pack(side="left")
        self.group_combo = ttk.Combobox(
            filters,
            textvariable=self.group_var,
            state="readonly",
            width=14,
        )
        self.group_combo.pack(side="left", padx=(5, 10))
        self.group_combo.bind("<<ComboboxSelected>>", lambda _event: self._load_stores())
        ttk.Label(filters, text="店铺").pack(side="left")
        self.store_combo = ttk.Combobox(
            filters,
            textvariable=self.store_var,
            state="readonly",
            width=18,
        )
        self.store_combo.pack(side="left", padx=(5, 10))
        self.store_combo.bind("<<ComboboxSelected>>", lambda _event: self._load_options())
        ttk.Label(filters, text="模板").pack(side="left")
        self.template_combo = ttk.Combobox(
            filters,
            textvariable=self.template_var,
            state="readonly",
            width=18,
        )
        self.template_combo.pack(side="left", padx=(5, 10))
        ttk.Label(filters, text="月结卡号").pack(side="left")
        self.monthly_combo = ttk.Combobox(
            filters,
            textvariable=self.monthly_card_var,
            state="readonly",
            width=18,
        )
        self.monthly_combo.pack(side="left", padx=(5, 8))
        button(filters, "重新获取模板", self._refresh_templates).pack(side="left")

        actions = ttk.Frame(self, style="TFrame")
        actions.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        tk.Checkbutton(
            actions,
            text="全选",
            variable=self.select_all_var,
            command=self._toggle_select_all,
            bg="#f4f2eb",
            fg=TEXT,
            activebackground="#f4f2eb",
            selectcolor="white",
            bd=0,
        ).pack(side="left", padx=(0, 8))
        button(actions, "拉取订单", self._pull_orders).pack(side="left")
        button(actions, "批量解析", self._parse_selected, "Primary").pack(
            side="left",
            padx=6,
        )
        button(actions, "导出Excel", self._export).pack(side="left")
        ttk.Label(
            actions,
            textvariable=self.state_var,
            style="Subtitle.TLabel",
        ).pack(side="left", padx=14)

        self.progress = ttk.Progressbar(self, variable=self.progress_var, maximum=100)
        self.progress.grid(row=2, column=0, sticky="ew", pady=(0, 8))

        search_bar = ttk.Frame(self, style="TFrame")
        search_bar.grid(row=3, column=0, sticky="ew", pady=(0, 8))
        ttk.Label(search_bar, text="状态").pack(side="left")
        ttk.Combobox(
            search_bar,
            textvariable=self.status_filter_var,
            state="readonly",
            width=10,
            values=("全部", "未解析", "已解析"),
        ).pack(side="left", padx=(5, 12))
        ttk.Label(search_bar, text="搜索订单").pack(side="left")
        ttk.Entry(
            search_bar,
            textvariable=self.search_var,
            width=32,
        ).pack(side="left", padx=(5, 6))
        button(search_bar, "查询", self._query).pack(side="left")
        ttk.Label(
            search_bar,
            text="单击订单号复制；双击订单复制整行",
            style="Subtitle.TLabel",
        ).pack(side="right")

        table_frame = ttk.Frame(self, style="Panel.TFrame")
        table_frame.grid(row=4, column=0, sticky="nsew")
        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)
        columns = ("sequence", "store", "order_id", "status", "buyer", "phone", "address")
        self.tree = ttk.Treeview(
            table_frame,
            columns=columns,
            show="headings",
            selectmode="extended",
        )
        headings = {
            "sequence": ("序号", 55),
            "store": ("店铺", 150),
            "order_id": ("订单号", 190),
            "status": ("状态", 100),
            "buyer": ("收货人", 100),
            "phone": ("虚拟号", 130),
            "address": ("完整地址", 430),
        }
        for key, (text, width) in headings.items():
            self.tree.heading(key, text=text)
            self.tree.column(key, width=width, minwidth=60, anchor="w")
        self.tree.grid(row=0, column=0, sticky="nsew")
        y_scroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        y_scroll.grid(row=0, column=1, sticky="ns")
        x_scroll = ttk.Scrollbar(table_frame, orient="horizontal", command=self.tree.xview)
        x_scroll.grid(row=1, column=0, sticky="ew")
        self.tree.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)
        self.tree.bind("<<TreeviewSelect>>", lambda _event: self._update_state())
        self.tree.bind("<ButtonRelease-1>", self._copy_order_number)
        self.tree.bind("<Double-1>", self._copy_order_row)
        self.tree.bind("<Button-3>", self._show_extract_menu)

        ttk.Label(
            self,
            text="提示：双击订单查看本地详情；批量解析会调用微信官方收货信息接口，"
            "并可能消耗当天额度。",
            style="Subtitle.TLabel",
        ).grid(row=5, column=0, sticky="w", pady=(8, 0))

    def refresh(self) -> None:
        self._load_stores()
        self._query()

    def _load_stores(self) -> None:
        stores = self.db.list_stores()
        groups = sorted(
            set(self.db.list_store_groups())
            | {store.group_name for store in stores if store.group_name}
        )
        self.group_combo["values"] = ["全部分组", *groups]
        if self.group_var.get() not in self.group_combo["values"]:
            self.group_var.set("全部分组")
        values = ["全部店铺"]
        self._store_lookup = {"全部店铺": None}
        for store in stores:
            if (
                self.group_var.get() != "全部分组"
                and store.group_name != self.group_var.get()
            ):
                continue
            label = f"{store.name} · #{store.id}"
            self._store_lookup[label] = store
            values.append(label)
        self.store_combo["values"] = values
        if self.store_var.get() not in values:
            self.store_var.set("全部店铺")
        self._load_options()

    def _selected_store(self) -> Store | None:
        value = self._store_lookup.get(self.store_var.get())
        return value if isinstance(value, Store) else None

    def _load_options(self) -> None:
        store = self._selected_store()
        if store is None:
            self.template_combo["values"] = ()
            self.monthly_combo["values"] = ()
            self.template_var.set("请选择店铺")
            self.monthly_card_var.set("请选择店铺")
            return
        templates = self.db.list_shipping_templates(store.id)
        template_values = [
            f"{row.get('template_name') or row.get('template_id')} · {row.get('template_id')}"
            for row in templates
        ]
        self.template_combo["values"] = template_values
        self.template_var.set(template_values[0] if template_values else "暂无模板")
        accounts = self.db.list_shipping_accounts(store.id)
        account_values = [
            f"{row.get('monthly_card') or row.get('acct_id')} · "
            f"{row.get('delivery_name') or row.get('delivery_id')}"
            for row in accounts
        ]
        self.monthly_combo["values"] = account_values
        self.monthly_card_var.set(
            account_values[0] if account_values else "暂无月结卡号"
        )

    def _query(self) -> None:
        store = self._selected_store()
        rows = self.db.list_orders(
            store_id=store.id if store else None,
            status=20,
        )
        self._workbench = self.db.get_order_workbench(store.id if store else None)
        self.tree.delete(*self.tree.get_children())
        self._rows.clear()
        for index, row in enumerate(rows):
            iid = str(index)
            self._rows[iid] = row
            parsed = bool(
                self._workbench.get(
                    (int(row["store_id"]), str(row["order_id"])),
                    {},
                ).get("parsed_at")
            )
            if (
                self.status_filter_var.get() == "已解析"
                and not parsed
            ):
                continue
            if (
                self.status_filter_var.get() == "未解析"
                and parsed
            ):
                continue
            term = self.search_var.get().strip().lower()
            if term and term not in " ".join(
                str(row.get(key) or "")
                for key in (
                    "order_id",
                    "store_name",
                    "buyer_name",
                    "phone",
                    "address",
                )
            ).lower():
                continue
            self.tree.insert(
                "",
                "end",
                iid=iid,
                values=(
                    index + 1,
                    row["store_name"],
                    row["order_id"],
                    "已解析" if parsed else "待解析",
                    row["buyer_name"],
                    row["phone"],
                    row["address"],
                ),
            )
        self.select_all_var.set(False)
        self._update_state()

    def _copy_order_number(self, event) -> None:
        iid = self.tree.identify_row(event.y)
        column = self.tree.identify_column(event.x)
        if not iid or column != "#3" or iid not in self._rows:
            return
        value = str(self._rows[iid].get("order_id") or "")
        if value:
            self.clipboard_clear()
            self.clipboard_append(value)
            self.window.set_status(f"已复制订单号：{value}")

    def _copy_order_row(self, event) -> str:
        iid = self.tree.identify_row(event.y)
        if iid not in self._rows:
            return "break"
        values = self.tree.item(iid, "values")
        self.clipboard_clear()
        self.clipboard_append("\t".join(str(value) for value in values))
        self.window.set_status("已复制整行订单")
        return "break"

    def _show_extract_menu(self, event) -> None:
        iid = self.tree.identify_row(event.y)
        if iid not in self._rows:
            return
        self.tree.selection_set(iid)
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label="复制订单号", command=lambda: self._copy_value("order_id"))
        menu.add_command(label="复制整行", command=lambda: self._copy_row_iid(iid))
        menu.add_command(label="本地订单详情", command=self._show_local_detail)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _copy_value(self, key: str) -> None:
        selection = self.tree.selection()
        if not selection:
            return
        value = str(self._rows[selection[0]].get(key) or "")
        if value:
            self.clipboard_clear()
            self.clipboard_append(value)
            self.window.set_status("已复制到剪贴板")

    def _copy_row_iid(self, iid: str) -> None:
        values = self.tree.item(iid, "values")
        self.clipboard_clear()
        self.clipboard_append("\t".join(str(value) for value in values))
        self.window.set_status("已复制整行订单")

    def _update_state(self) -> None:
        selected = len(self.tree.selection())
        self.state_var.set(f"显示 {len(self._rows)} 条 · 已选 {selected} 条")

    def _toggle_select_all(self) -> None:
        self.tree.selection_set(
            tuple(self._rows) if self.select_all_var.get() else ()
        )
        self._update_state()

    def _pull_orders(self) -> None:
        stores = [self._selected_store()] if self._selected_store() else self.db.list_stores()
        stores = [store for store in stores if store is not None]
        if not stores:
            messagebox.showinfo("没有店铺", "请先添加店铺。", parent=self)
            return
        now = int(datetime.now().timestamp())
        self.window.set_status("正在拉取待发货订单…", busy=True)

        def work(progress) -> list[str]:
            errors = []
            for index, store in enumerate(stores, 1):
                progress(f"正在拉取 {store.name} 的订单", index - 1, len(stores))
                try:
                    self.window.service.sync_orders(
                        store,
                        now - 7 * 86400,
                        now,
                        progress,
                    )
                except Exception as exc:
                    errors.append(f"{store.name}: {exc}")
            return errors

        def done(errors: list[str]) -> None:
            self.window.set_status(
                f"订单拉取完成，成功 {len(stores) - len(errors)} 家"
            )
            self._query()
            if errors:
                messagebox.showwarning(
                    "部分店铺拉取失败",
                    "\n".join(errors[:8]),
                    parent=self,
                )

        self.window.run_task(
            work,
            done,
            lambda error: messagebox.showerror("订单拉取失败", error, parent=self),
            log_title="拉取待发货订单",
        )

    def _parse_selected(self) -> None:
        selection = self.tree.selection()
        rows = [self._rows[iid] for iid in selection if iid in self._rows]
        if not rows:
            messagebox.showinfo("请选择订单", "请先选择需要解析的订单。", parent=self)
            return
        if not messagebox.askyesno(
            "批量解析",
            f"即将解析 {len(rows)} 条订单的收货信息。\n"
            "该操作会调用微信官方接口并可能消耗当天额度，是否继续？",
            parent=self,
        ):
            return
        self.window.set_status("正在批量解析订单…", busy=True)

        def work(progress) -> list[str]:
            errors = []
            for index, row in enumerate(rows, 1):
                progress(
                    f"正在解析 {row['order_id']}",
                    index - 1,
                    len(rows),
                )
                store = self.db.get_store(int(row["store_id"]))
                if store is None:
                    errors.append(f"{row['order_id']}: 店铺不存在")
                    continue
                try:
                    self.window.service.decode_order_sensitive(
                        store,
                        str(row["order_id"]),
                    )
                    self.db.update_order_workbench(
                        store.id,
                        str(row["order_id"]),
                        parsed_at=_format_datetime(int(datetime.now().timestamp())),
                    )
                except Exception as exc:
                    errors.append(f"{row['order_id']}: {exc}")
            return errors

        def done(errors: list[str]) -> None:
            self.window.set_status(
                f"批量解析完成，成功 {len(rows) - len(errors)} 条"
            )
            self._query()
            if errors:
                messagebox.showwarning(
                    "部分订单解析失败",
                    "\n".join(errors[:8]),
                    parent=self,
                )

        self.window.run_task(
            work,
            done,
            lambda error: messagebox.showerror("批量解析失败", error, parent=self),
            self._progress,
            log_title="批量解析订单收货信息",
        )

    def _refresh_templates(self) -> None:
        store = self._selected_store()
        if store is None:
            messagebox.showinfo("请选择店铺", "获取模板前请选择具体店铺。", parent=self)
            return
        self.window.set_status("正在获取物流模板…", busy=True)
        self.window.run_task(
            lambda _progress: self.window.service.refresh_shipping_options(store),
            lambda _result: (self._load_options(), self.window.set_status("物流模板已刷新")),
            lambda error: messagebox.showerror("获取模板失败", error, parent=self),
            log_title="刷新物流模板和发货选项",
        )

    def _export(self) -> None:
        if not self._rows:
            messagebox.showinfo("没有数据", "当前没有可导出的待发货订单。", parent=self)
            return
        path = filedialog.asksaveasfilename(
            parent=self,
            title="导出订单提取表",
            defaultextension=".xlsx",
            filetypes=[("Excel 工作簿", "*.xlsx")],
            initialfile=f"订单提取_{date.today():%Y%m%d}.xlsx",
        )
        if not path:
            return
        headers = ["序号", "店铺", "订单号", "状态", "收货人", "虚拟号", "完整地址"]
        rows = [
            [
                index,
                row["store_name"],
                row["order_id"],
                (
                    "已解析"
                    if self._workbench.get(
                        (int(row["store_id"]), str(row["order_id"])),
                        {},
                    ).get("parsed_at")
                    else "待解析"
                ),
                row["buyer_name"],
                row["phone"],
                row["address"],
            ]
            for index, row in enumerate(self._rows.values(), 1)
        ]
        try:
            export_xlsx(path, "订单提取", headers, rows)
        except Exception as exc:
            messagebox.showerror("导出失败", str(exc), parent=self)
            return
        self.window.set_status(f"已导出：{Path(path).name}")

    def _show_local_detail(self) -> None:
        selection = self.tree.selection()
        if not selection:
            return
        row = self._rows.get(selection[0])
        if not row:
            return
        order = self.db.get_order(int(row["store_id"]), str(row["order_id"]))
        if order is None:
            return
        data = asdict(order)
        data["items"] = [asdict(item) for item in order.items]
        OrderDetailDialog(self, data)

    def _progress(self, message: str, current: int, total: int) -> None:
        self.window.set_status(message, busy=True)
        self.progress_var.set((current / total * 100) if total else 0)


class ParsedOrdersPage(BasePage):
    title = "已解析订单"
    subtitle = "查询已解析 / 已打印记录"

    def __init__(self, parent: tk.Widget, window: "MainWindow"):
        super().__init__(parent, window)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)
        self._store_lookup: dict[str, Store | None] = {"全部店铺": None}
        self.store_var = tk.StringVar(value="全部店铺")
        self.status_var = tk.StringVar(value="全部")
        self.start_var = tk.StringVar()
        self.end_var = tk.StringVar()
        self.order_id_var = tk.StringVar()
        self.count_var = tk.StringVar(value="共 0 条已解析记录")
        self._rows: dict[str, dict[str, Any]] = {}
        self._workbench: dict[tuple[int, str], dict[str, Any]] = {}

        filters = ttk.Frame(self, style="TFrame")
        filters.grid(row=0, column=0, sticky="ew", pady=(0, 7))
        ttk.Label(filters, text="店铺").pack(side="left")
        self.store_combo = ttk.Combobox(
            filters,
            textvariable=self.store_var,
            state="readonly",
            width=19,
        )
        self.store_combo.pack(side="left", padx=(5, 10))
        ttk.Label(filters, text="状态").pack(side="left")
        ttk.Combobox(
            filters,
            textvariable=self.status_var,
            state="readonly",
            width=10,
            values=("全部", "已解析", "已打印"),
        ).pack(side="left", padx=(5, 10))
        ttk.Label(filters, text="日期").pack(side="left")
        ttk.Entry(filters, textvariable=self.start_var, width=11).pack(
            side="left",
            padx=(5, 3),
        )
        ttk.Label(filters, text="至").pack(side="left")
        ttk.Entry(filters, textvariable=self.end_var, width=11).pack(
            side="left",
            padx=(3, 10),
        )
        ttk.Label(filters, text="订单号").pack(side="left")
        ttk.Entry(filters, textvariable=self.order_id_var, width=22).pack(
            side="left",
            padx=(5, 6),
        )
        button(filters, "查询", self._query, "Primary").pack(side="left")

        toolbar = ttk.Frame(self, style="TFrame")
        toolbar.grid(row=1, column=0, sticky="ew", pady=(0, 7))
        ttk.Label(
            toolbar,
            textvariable=self.count_var,
            style="Subtitle.TLabel",
        ).pack(side="left")
        button(toolbar, "补回收", self._recover).pack(side="right", padx=(6, 0))
        import_export = tk.Menubutton(
            toolbar,
            text="导出/导入订单 ▾",
            bg="#fffefa",
            fg=TEXT,
            activebackground="#e8efec",
            relief="flat",
            bd=0,
            padx=10,
            pady=6,
            cursor="hand2",
        )
        import_export.pack(side="right")
        import_menu = tk.Menu(import_export, tearoff=0)
        import_menu.add_command(label="导出已解析订单", command=self._export)
        import_menu.add_command(label="导入订单解析记录", command=self._import)
        import_export.configure(menu=import_menu)

        table_frame = ttk.Frame(self, style="Panel.TFrame")
        table_frame.grid(row=2, column=0, sticky="nsew")
        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)
        columns = (
            "store",
            "order_id",
            "status",
            "buyer",
            "phone",
            "address",
            "waybill",
            "parsed_at",
        )
        self.tree = ttk.Treeview(
            table_frame,
            columns=columns,
            show="headings",
            selectmode="extended",
        )
        headings = {
            "store": ("店铺", 150),
            "order_id": ("订单号", 190),
            "status": ("状态", 90),
            "buyer": ("收货人", 100),
            "phone": ("虚拟号", 125),
            "address": ("完整地址", 330),
            "waybill": ("运单号", 160),
            "parsed_at": ("解析时间", 145),
        }
        for key, (text, width) in headings.items():
            self.tree.heading(key, text=text)
            self.tree.column(key, width=width, minwidth=60, anchor="w")
        self.tree.grid(row=0, column=0, sticky="nsew")
        y_scroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        y_scroll.grid(row=0, column=1, sticky="ns")
        x_scroll = ttk.Scrollbar(table_frame, orient="horizontal", command=self.tree.xview)
        x_scroll.grid(row=1, column=0, sticky="ew")
        self.tree.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)
        self.tree.bind("<Double-1>", lambda _event: self._show_detail())

    def refresh(self) -> None:
        values = ["全部店铺"]
        self._store_lookup = {"全部店铺": None}
        for store in self.db.list_stores():
            label = f"{store.name} · #{store.id}"
            self._store_lookup[label] = store
            values.append(label)
        self.store_combo["values"] = values
        if self.store_var.get() not in values:
            self.store_var.set("全部店铺")
        self._query()

    def _selected_store(self) -> Store | None:
        value = self._store_lookup.get(self.store_var.get())
        return value if isinstance(value, Store) else None

    def _query(self) -> None:
        start_time = end_time = None
        if self.start_var.get().strip() or self.end_var.get().strip():
            try:
                start_time, end_time = date_range(
                    self.start_var.get().strip()
                    or (date.today() - timedelta(days=29)).isoformat(),
                    self.end_var.get().strip() or date.today().isoformat(),
                )
            except ValueError as exc:
                messagebox.showwarning("日期范围", str(exc), parent=self)
                return
        store = self._selected_store()
        all_rows = self.db.list_orders(
            store_id=store.id if store else None,
            start_time=start_time,
            end_time=end_time,
            search=self.order_id_var.get().strip(),
        )
        self._workbench = self.db.get_order_workbench(store.id if store else None)
        rows = []
        for row in all_rows:
            workbench = self._workbench.get(
                (int(row["store_id"]), str(row["order_id"])),
                {},
            )
            parsed = bool(workbench.get("parsed_at"))
            printed = bool(workbench.get("is_marked_sent"))
            if not parsed and not printed:
                continue
            if self.status_var.get() == "已解析" and not parsed:
                continue
            if self.status_var.get() == "已打印" and not printed:
                continue
            rows.append(row)
        self.tree.delete(*self.tree.get_children())
        self._rows.clear()
        for index, row in enumerate(rows):
            workbench = self._workbench.get(
                (int(row["store_id"]), str(row["order_id"])),
                {},
            )
            iid = str(index)
            self._rows[iid] = row
            parsed_at = str(workbench.get("parsed_at") or "")
            status_text = "已打印" if workbench.get("is_marked_sent") else "已解析"
            self.tree.insert(
                "",
                "end",
                iid=iid,
                values=(
                    row["store_name"],
                    row["order_id"],
                    status_text,
                    row["buyer_name"],
                    row["phone"],
                    row["address"],
                    row["waybill_id"],
                    parsed_at,
                ),
            )
        self.count_var.set(f"共 {len(rows)} 条已解析记录")
        self.window.set_status(f"已解析订单查询完成，共 {len(rows)} 条")

    def _recover(self) -> None:
        def save(values: dict[str, str]) -> None:
            order_ids = [
                value.strip()
                for value in str(values.get("order_ids") or "").replace("，", ",").split(",")
                if value.strip()
            ]
            if not order_ids:
                messagebox.showwarning("补回收", "请输入订单号。", parent=self)
                return
            recovered = 0
            for order_id in order_ids:
                candidates = self.db.list_orders(search=order_id)
                exact = [
                    row for row in candidates if str(row["order_id"]) == order_id
                ]
                for row in exact:
                    self.db.update_order_workbench(
                        int(row["store_id"]),
                        order_id,
                        parsed_at=_format_datetime(int(datetime.now().timestamp())),
                    )
                    recovered += 1
            self._query()
            messagebox.showinfo(
                "补回收完成",
                f"已恢复 {recovered} 条订单记录。",
                parent=self,
            )

        SimpleFieldsDialog(
            self,
            "补回收订单",
            (("order_ids", "订单号（逗号分隔）"),),
            save,
        )

    def _export(self) -> None:
        if not self._rows:
            messagebox.showinfo("没有数据", "当前没有已解析订单。", parent=self)
            return
        path = filedialog.asksaveasfilename(
            parent=self,
            title="导出已解析订单",
            defaultextension=".xlsx",
            filetypes=[("Excel 工作簿", "*.xlsx")],
            initialfile=f"已解析订单_{date.today():%Y%m%d}.xlsx",
        )
        if not path:
            return
        headers = ["店铺", "订单号", "状态", "收货人", "虚拟号", "完整地址", "运单号", "解析时间"]
        rows = [
            [
                row["store_name"],
                row["order_id"],
                "已打印"
                if self._workbench.get(
                    (int(row["store_id"]), str(row["order_id"])),
                    {},
                ).get("is_marked_sent")
                else "已解析",
                row["buyer_name"],
                row["phone"],
                row["address"],
                row["waybill_id"],
                self._workbench.get(
                    (int(row["store_id"]), str(row["order_id"])),
                    {},
                ).get("parsed_at")
                or "",
            ]
            for row in self._rows.values()
        ]
        try:
            export_xlsx(path, "已解析订单", headers, rows)
        except Exception as exc:
            messagebox.showerror("导出失败", str(exc), parent=self)
            return
        self.window.set_status(f"已导出：{Path(path).name}")

    def _import(self) -> None:
        path = filedialog.askopenfilename(
            parent=self,
            title="导入订单解析记录",
            filetypes=[("Excel 工作簿", "*.xlsx")],
        )
        if not path:
            return
        try:
            from openpyxl import load_workbook

            workbook = load_workbook(path, read_only=True, data_only=True)
            sheet = workbook.active
            recovered = 0
            for row in sheet.iter_rows(min_row=2, values_only=True):
                if len(row) < 2 or not row[1]:
                    continue
                order_id = str(row[1]).strip()
                candidates = self.db.list_orders(search=order_id)
                for match in candidates:
                    if str(match["order_id"]) != order_id:
                        continue
                    parsed_at = str(row[7]).strip() if len(row) > 7 and row[7] else ""
                    self.db.update_order_workbench(
                        int(match["store_id"]),
                        order_id,
                        parsed_at=parsed_at
                        or _format_datetime(int(datetime.now().timestamp())),
                    )
                    recovered += 1
            workbook.close()
        except Exception as exc:
            messagebox.showerror("导入失败", str(exc), parent=self)
            return
        self._query()
        messagebox.showinfo(
            "导入完成",
            f"已导入 {recovered} 条订单解析记录。",
            parent=self,
        )

    def _show_detail(self) -> None:
        selection = self.tree.selection()
        if not selection:
            return
        row = self._rows.get(selection[0])
        if not row:
            return
        order = self.db.get_order(int(row["store_id"]), str(row["order_id"]))
        if order is None:
            return
        data = asdict(order)
        data["items"] = [asdict(item) for item in order.items]
        OrderDetailDialog(self, data)


class ProductsPage(BasePage):
    title = "商品管理"
    subtitle = "多店铺合并查看、批量上下架、删除、取口令和按店铺一键上架"
    tab_definitions = (
        ("sales", "销售中"),
        ("audit", "审核中"),
        ("failed", "审核失败"),
        ("draft", "草稿箱"),
        ("offline", "已下架"),
        ("recycle", "回收站"),
        ("strategy", "上架策略生效中"),
    )
    page_size = 50

    def __init__(self, parent: tk.Widget, window: "MainWindow"):
        super().__init__(parent, window)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(5, weight=1)
        self._all_stores: list[Store] = []
        self._store_lookup: dict[str, Store | None] = {"全部店铺": None}
        self.group_var = tk.StringVar(value="全部分组")
        self.store_var = tk.StringVar(value="全部店铺")
        self.search_var = tk.StringVar()
        self.product_platform_var = tk.StringVar(value="全部平台")
        self.product_status_filter_var = tk.StringVar(value="销售中")
        self.sales_min_var = tk.StringVar()
        self.sales_max_var = tk.StringVar()
        self.price_min_var = tk.StringVar()
        self.price_max_var = tk.StringVar()
        self.relation_var = tk.StringVar(value="全部")
        self.ignore_product_var = tk.StringVar(value="未忽略")
        self.product_name_filter_var = tk.StringVar()
        self.product_id_filter_var = tk.StringVar()
        self.product_group_filter_var = tk.StringVar(value="全部商品分组")
        self.sort_var = tk.StringVar(value="默认排序")
        self.listing_time_var = tk.StringVar(value="不限")
        self.listing_start_var = tk.StringVar()
        self.listing_end_var = tk.StringVar()
        self.product_limit_var = tk.StringVar(value="300")
        self.catalog_stats_var = tk.StringVar(value="商品目录：尚未建立")
        self.page_info_var = tk.StringVar(value="第 1 / 1 页")
        self.cancel_event = threading.Event()
        self.progress_var = tk.DoubleVar(value=0)
        self._rows: dict[str, dict[str, Any]] = {}
        self._all_rows: list[dict[str, Any]] = []
        self._filtered_rows: list[dict[str, Any]] = []
        self._tab_counts: dict[str, int] = {}
        self._query_generation = 0
        self._query_loading = False
        self._query_dirty = False
        self._fresh_store_ids: set[int] = set()
        self._fresh_scope: tuple[int | None, str] | None = None
        self._product_query_errors = 0
        self._page = 1
        self.active_tab_key = "sales"
        self.tab_buttons: dict[str, ttk.Button] = {}
        self.quota_var = tk.StringVar(value="提审额度 —")

        tabs = ttk.Frame(self, style="TFrame")
        tabs.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        for key, label in self.tab_definitions:
            tab_button = button(
                tabs,
                label,
                lambda tab_key=key: self._change_tab(tab_key),
            )
            tab_button.pack(side="left", padx=(0, 6))
            self.tab_buttons[key] = tab_button
        ttk.Label(tabs, textvariable=self.quota_var, style="Subtitle.TLabel").pack(
            side="right",
            padx=(8, 0),
        )
        tabs.grid_remove()

        filters = ttk.Frame(self, style="TFrame")
        filters.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        for column in range(6):
            filters.columnconfigure(column, weight=1, uniform="product-top")
        filter_row = ttk.Frame(filters)
        filter_row.grid(row=0, column=0, columnspan=6, sticky="ew")
        ttk.Label(filter_row, text="店铺平台 |").pack(side="left")
        ttk.Combobox(
            filter_row,
            textvariable=self.product_platform_var,
            values=("全部平台", "微信小店"),
            state="readonly",
            width=12,
        ).pack(side="left", padx=(3, 9))
        ttk.Label(filter_row, text="店铺名称 |").pack(side="left")
        self.store_combo = ttk.Combobox(
            filter_row,
            textvariable=self.store_var,
            state="readonly",
            width=17,
        )
        self.store_combo.pack(side="left", padx=(3, 9))
        self.store_combo.bind("<<ComboboxSelected>>", self._on_store_selected)
        ttk.Label(filter_row, text="商品状态 |").pack(side="left")
        status_combo = ttk.Combobox(
            filter_row,
            textvariable=self.product_status_filter_var,
            values=("销售中", "审核中", "审核失败", "草稿箱", "已下架", "回收站", "上架策略"),
            state="readonly",
            width=12,
        )
        status_combo.pack(side="left", padx=(3, 9))
        status_combo.bind("<<ComboboxSelected>>", self._on_product_status)
        ttk.Label(filter_row, text="销量：").pack(side="left")
        ttk.Entry(filter_row, textvariable=self.sales_min_var, width=8).pack(side="left")
        ttk.Label(filter_row, text="-").pack(side="left", padx=2)
        ttk.Entry(filter_row, textvariable=self.sales_max_var, width=8).pack(side="left")
        ttk.Label(filter_row, text="价格：").pack(side="left", padx=(8, 0))
        ttk.Entry(filter_row, textvariable=self.price_min_var, width=8).pack(side="left")
        ttk.Label(filter_row, text="-").pack(side="left", padx=2)
        ttk.Entry(filter_row, textvariable=self.price_max_var, width=8).pack(side="left")
        button(
            filter_row,
            "同步商品列表",
            self._query_products,
            "Primary",
        ).pack(side="left", padx=(8, 0))
        filter_row_2 = ttk.Frame(filters)
        filter_row_2.grid(row=1, column=0, columnspan=6, sticky="ew", pady=(6, 0))
        ttk.Label(filter_row_2, text="是否关联 |").pack(side="left")
        ttk.Combobox(
            filter_row_2,
            textvariable=self.relation_var,
            values=("全部", "已关联", "未关联"),
            state="readonly",
            width=11,
        ).pack(side="left", padx=(3, 9))
        ttk.Label(filter_row_2, text="忽略商品 |").pack(side="left")
        ttk.Combobox(
            filter_row_2,
            textvariable=self.ignore_product_var,
            values=("全部", "未忽略", "已忽略"),
            state="readonly",
            width=11,
        ).pack(side="left", padx=(3, 9))
        ttk.Label(filter_row_2, text="商品名称 |").pack(side="left")
        ttk.Entry(filter_row_2, textvariable=self.product_name_filter_var, width=16).pack(
            side="left", padx=(3, 9)
        )
        ttk.Label(filter_row_2, text="商品ID |").pack(side="left")
        ttk.Entry(filter_row_2, textvariable=self.product_id_filter_var, width=15).pack(
            side="left", padx=(3, 9)
        )
        self.group_combo = ttk.Combobox(
            filter_row_2,
            textvariable=self.product_group_filter_var,
            state="readonly",
            width=13,
        )
        ttk.Label(filter_row_2, text="商品分组").pack(side="left")
        self.group_combo.pack(side="left", padx=(3, 9))
        self.group_combo.bind("<<ComboboxSelected>>", self._on_group_selected)
        button(filter_row_2, "搜索", self._query, "Primary").pack(side="left")
        button(filter_row_2, "重置", self._reset_filters).pack(side="left", padx=5)

        listing_filters = ttk.Frame(self, style="TFrame")
        listing_filters.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        listing_filters.grid_remove()
        ttk.Label(listing_filters, text="上架时间").pack(side="left")
        ttk.Combobox(
            listing_filters,
            textvariable=self.listing_time_var,
            state="readonly",
            width=12,
            values=("不限", "近1天", "近7天", "近30天", "自定义"),
        ).pack(side="left", padx=(6, 8))
        ttk.Entry(
            listing_filters,
            textvariable=self.listing_start_var,
            width=12,
        ).pack(side="left")
        ttk.Label(listing_filters, text="至").pack(side="left", padx=4)
        ttk.Entry(
            listing_filters,
            textvariable=self.listing_end_var,
            width=12,
        ).pack(side="left", padx=(0, 8))
        button(listing_filters, "获取口令", self._get_product_token).pack(side="left")

        batch_actions = ttk.Frame(self, style="TFrame")
        batch_actions.grid(row=3, column=0, sticky="ew", pady=(0, 8))
        self.select_all_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            batch_actions,
            text="全选",
            variable=self.select_all_var,
            command=self._toggle_select_all,
            bg="#f4f2eb",
            fg=TEXT,
            activebackground="#f4f2eb",
            selectcolor="white",
            bd=0,
        ).pack(side="left", padx=(0, 8))
        button(batch_actions, "批量忽略", self._batch_ignore_products).pack(side="left")
        self._product_action_menu(
            batch_actions,
            "导入货源ID ▾",
            (("导入货源ID", self._import_source_ids), ("下载模板", self._download_source_template)),
        )
        self._product_action_menu(
            batch_actions,
            "分组管理 ▾",
            (("分组管理", self._manage_product_groups), ("批量分组", self._batch_group_products)),
        )
        self._product_action_menu(
            batch_actions,
            "导入数据包 ▾",
            (("淘宝数据", self._open_upload_page), ("1688数据", self._open_upload_page)),
        )
        button(
            batch_actions,
            "商品同步日志",
            self._show_operations,
        ).pack(side="left", padx=(6, 0))
        ttk.Label(
            batch_actions,
            textvariable=self.catalog_stats_var,
            style="Subtitle.TLabel",
        ).pack(side="right")

        self.progress = ttk.Progressbar(self, variable=self.progress_var, maximum=100)
        self.progress.grid(row=4, column=0, sticky="ew", pady=(0, 8))

        table_frame = ttk.Frame(self, style="Panel.TFrame")
        table_frame.grid(row=5, column=0, sticky="nsew")
        table_frame.rowconfigure(1, weight=1)
        table_frame.columnconfigure(0, weight=1)
        headings = ttk.Frame(table_frame, style="TFrame", padding=(8, 7))
        headings.grid(row=0, column=0, sticky="ew")
        for column, text in enumerate(
            (
                "原平台商品信息",
                "店铺名称",
                "价格",
                "商品销量",
                "是否有SKU",
                "商品状态",
                "商品分组",
                "采购关联商品",
                "操作",
            )
        ):
            ttk.Label(headings, text=text).grid(
                row=0,
                column=column,
                sticky="ew",
                padx=4,
            )
            headings.columnconfigure(column, weight=1)
        self.tree = ProductCardList(table_frame, self._handle_product_card_action)
        self.tree.grid(row=1, column=0, sticky="nsew")
        self.tree.bind("<Double-1>", self._copy_product_title)
        self.tree.bind("<Button-3>", self._show_product_menu)
        self._visible_product_fields = [
            "product",
            "store",
            "price",
            "sold",
            "has_sku",
            "status",
            "group",
            "related",
            "action",
        ]

        paging = ttk.Frame(self, style="TFrame")
        paging.grid(row=6, column=0, sticky="ew", pady=(8, 0))
        button(paging, "上一页", lambda: self._change_page(-1)).pack(side="left")
        ttk.Label(paging, textvariable=self.page_info_var).pack(side="left", padx=10)
        button(paging, "下一页", lambda: self._change_page(1)).pack(side="left")

    def refresh(self) -> None:
        self._reload_stores()
        self._load_cached_products()

    def _reload_stores(self) -> None:
        self._all_stores = list(self.db.list_stores())
        group_names = set(self.db.list_store_groups()) | {
            store.group_name
            for store in self._all_stores
            if store.group_name
        }
        if any(not store.group_name for store in self._all_stores):
            group_names.add("未分组")
        self.group_var.set("全部分组")
        product_groups = [
            line.strip()
            for line in self.db.get_setting(
                "product_group_rules",
                "",
            ).splitlines()
            if line.strip()
        ]
        self.group_combo["values"] = [
            "全部商品分组",
            *sorted(set(product_groups)),
        ]
        if self.product_group_filter_var.get() not in self.group_combo["values"]:
            self.product_group_filter_var.set("全部商品分组")
        self._refresh_store_choices()
        self._refresh_catalog_stats()

    def _group_matches(self, store: Store) -> bool:
        group_var = getattr(self, "group_var", None)
        group = group_var.get() if group_var is not None else "全部分组"
        if group == "全部分组":
            return True
        if group == "未分组":
            return not store.group_name
        return store.group_name == group

    def _refresh_store_choices(self) -> None:
        self._store_lookup = {"全部店铺": None}
        values = ["全部店铺"]
        for store in self._all_stores:
            if not self._group_matches(store):
                continue
            label = f"{store.name} · #{store.id}"
            self._store_lookup[label] = store
            values.append(label)
        self.store_combo["values"] = values
        if self.store_var.get() not in values:
            self.store_var.set("全部店铺")

    def _on_group_selected(self, _event=None) -> None:
        self._query(reset_page=True)

    def _on_store_selected(self, _event=None) -> None:
        self._query_generation += 1
        self._fresh_scope = None
        self._fresh_store_ids.clear()
        self._load_cached_products()

    def _query_store_ids(self) -> list[int]:
        selected = self._store_lookup.get(self.store_var.get())
        if isinstance(selected, Store):
            return [selected.id]
        all_stores = getattr(self, "_all_stores", None)
        if all_stores is None:
            all_stores = list(self.db.list_stores())
        return [
            store.id
            for store in all_stores
            if self._group_matches(store)
        ]

    def _load_cached_products(self) -> None:
        selected = self._store_lookup.get(self.store_var.get())
        store_id = selected.id if isinstance(selected, Store) else None
        scope_ids = set(self._query_store_ids())
        self._fresh_scope = (store_id, self.active_tab_key)
        self._fresh_store_ids = scope_ids
        self._product_query_errors = 0
        self._query_background()
        self.window.set_status(
            f"已显示本机缓存商品；当前范围 {len(scope_ids)} 家店铺。"
            "点击“查询”才会联网刷新。"
        )

    def _query_products(self) -> None:
        if self._task_running():
            self.window.set_status(
                "商品任务正在执行。可切换到店铺管理等其他页面继续操作。"
            )
            return
        product_name_filter = getattr(
            self,
            "product_name_filter_var",
            None,
        )
        if product_name_filter is not None and product_name_filter.get().strip():
            self.search_var.set(product_name_filter.get().strip())
        selected = self._store_lookup.get(self.store_var.get())
        tab_key = self.active_tab_key
        store_id = selected.id if selected is not None else None
        group_var = getattr(self, "group_var", None)
        group_key = (
            group_var.get()
            if group_var is not None
            else "全部分组"
        )
        store_ids = self._query_store_ids()
        if not store_ids:
            messagebox.showinfo(
                "没有店铺",
                "当前分组没有可查询的店铺。",
                parent=self,
            )
            return
        self.cancel_event.clear()
        self._query_generation += 1
        self._fresh_scope = (store_id, tab_key)
        self._fresh_store_ids = set()
        self._product_query_errors = 0

        def done(result: ProductQueryResult) -> None:
            current = self._store_lookup.get(self.store_var.get())
            current_scope = (current.id if current is not None else None, self.active_tab_key)
            current_group = getattr(self, "group_var", None)
            current_group_key = (
                current_group.get()
                if current_group is not None
                else "全部分组"
            )
            if (
                current_scope != (store_id, tab_key)
                or current_group_key != group_key
            ):
                self._query_products()
                return
            self._fresh_store_ids = set(result.store_ids)
            self._product_query_errors = len(result.errors)
            self._query_background()
            if result.errors:
                messagebox.showwarning(
                    "部分店铺商品读取失败",
                    "\n".join(result.errors[:8]),
                    parent=self,
                )

        self.window.run_task(
            lambda progress: self._refresh_product_tabs(
                tab_key,
                store_id,
                progress,
                store_ids=store_ids,
            ),
            done,
            lambda error: messagebox.showerror("商品查询失败", error, parent=self),
            self._progress,
            log_title="查询店铺商品",
        )
        self._query()
        self.window.set_status("正在读取微信小店最新商品…", busy=True)

    def _refresh_product_tabs(
        self,
        tab_key: str,
        store_id: int | None,
        progress: Callable[[str, int, int], None],
        *,
        store_ids: list[int] | None = None,
    ) -> ProductQueryResult:
        allowed_ids = set(store_ids or [])
        stores = [
            store
            for store in self.db.list_stores()
            if (
                (store.id == store_id if store_id is not None else True)
                and (not allowed_ids or store.id in allowed_ids)
            )
        ]
        result = ProductQueryResult()
        for index, store in enumerate(stores, start=1):
            if self.cancel_event.is_set():
                result.errors.append("查询已取消，尚未读取完成的店铺未显示")
                break
            progress(f"正在读取店铺商品：{store.name}", index, len(stores))
            try:
                fetched = self.window.service.refresh_product_tab(
                    store, tab_key, cancel_event=self.cancel_event
                )
                if fetched.cancelled:
                    result.errors.append(f"{store.name}：查询已取消")
                    break
                result.store_ids.append(store.id)
            except Exception as exc:
                result.errors.append(f"{store.name}：{redact_text(str(exc))}")
        result.message = (
            f"已读取 {len(result.store_ids)} 家店铺的最新商品，"
            f"失败或取消 {len(result.errors)} 项"
        )
        return result

    def _query_background(self, reset_page: bool = True) -> None:
        self._query_generation += 1
        generation = self._query_generation
        if self._query_loading:
            self._query_dirty = True
            return
        self._query_loading = True
        self._query_dirty = False
        value = self._store_lookup.get(self.store_var.get())
        store = value if isinstance(value, Store) else None
        context = {
            "store_id": store.id if store else None,
            "search": self.search_var.get().strip().lower(),
            "active_tab": self.active_tab_key,
            "listing_time": self.listing_time_var.get(),
            "listing_start": self.listing_start_var.get().strip(),
            "listing_end": self.listing_end_var.get().strip(),
            "sort": self.sort_var.get(),
            "store_ids": set(self._fresh_store_ids) if self._fresh_scope == (
                store.id if store else None, self.active_tab_key
            ) else set(),
        }
        self.window.set_status("正在后台加载商品数据…", busy=True)

        def worker() -> None:
            try:
                rows = self.db.list_products(
                    store_id=context["store_id"],
                    search="",
                )
                rows = [row for row in rows if row["store_id"] in context["store_ids"]]
                worker_page = object.__new__(ProductsPage)
                worker_page.active_tab_key = str(
                    context["active_tab"]
                )

                class FixedValue:
                    def __init__(self, current: str) -> None:
                        self.current = current

                    def get(self) -> str:
                        return self.current

                worker_page.listing_time_var = FixedValue(
                    str(context["listing_time"])
                )
                worker_page.listing_start_var = FixedValue(
                    str(context["listing_start"])
                )
                worker_page.listing_end_var = FixedValue(
                    str(context["listing_end"])
                )
                worker_page.sort_var = FixedValue(
                    str(context["sort"])
                )
                term = str(context["search"])
                if term:
                    candidates = [
                        row
                        for row in rows
                        if term
                        in " ".join(
                            (
                                str(row.get("title") or ""),
                                str(row.get("product_id") or ""),
                                str(row.get("spu_code") or ""),
                                str(row.get("out_product_id") or ""),
                            )
                        ).lower()
                    ]
                else:
                    candidates = list(rows)
                filtered = [
                    row
                    for row in candidates
                    if worker_page._matches_active_tab(row)
                    and worker_page._matches_listing_time(row)
                ]
                worker_page._filtered_rows = filtered
                worker_page._sort_product_rows()
                counts = worker_page._compute_tab_counts(rows)
            except Exception as exc:
                self.window._post(
                    self._query_background_failed,
                    generation,
                    str(exc),
                )
            else:
                self.window._post(
                    self._query_background_finished,
                    generation,
                    rows,
                    filtered,
                    counts,
                    reset_page,
                )

        threading.Thread(
            target=worker,
            daemon=True,
            name="langlang-product-query",
        ).start()

    def _query_background_finished(
        self,
        generation: int,
        rows: list[dict[str, Any]],
        filtered: list[dict[str, Any]],
        counts: dict[str, int],
        reset_page: bool,
    ) -> None:
        self._query_loading = False
        if generation != self._query_generation:
            if self._query_dirty:
                self._query_background(reset_page=reset_page)
            return
        self._all_rows = rows
        self._filtered_rows = filtered
        self._tab_counts = counts
        if reset_page:
            self._page = 1
        self._refresh_tab_labels()
        self._render_page()
        self.window.set_status(
            f"当前页签最新商品 {len(filtered)} 条，读取失败或取消 {self._product_query_errors} 项"
        )
        self._refresh_catalog_stats()
        self._refresh_quota()
        if self._query_dirty:
            self._query_background(reset_page=reset_page)

    def _query_background_failed(
        self,
        generation: int,
        error: str,
    ) -> None:
        self._query_loading = False
        if generation == self._query_generation:
            self.window.set_status(f"商品加载失败：{error}")
        if self._query_dirty:
            self._query_background()

    def _query(self, reset_page: bool = True) -> None:
        value = self._store_lookup.get(self.store_var.get())
        store = value if isinstance(value, Store) else None
        self._all_rows = self.db.list_products(
            store_id=store.id if store else None,
            search="",
        )
        scope = (store.id if store else None, self.active_tab_key)
        self._all_rows = [
            row for row in self._all_rows
            if scope == self._fresh_scope and row["store_id"] in self._fresh_store_ids
        ]
        term = self.search_var.get().strip().lower()
        if term:
            candidates = [
                row
                for row in self._all_rows
                if term
                in " ".join(
                    (
                        str(row.get("title") or ""),
                        str(row.get("product_id") or ""),
                        str(row.get("spu_code") or ""),
                        str(row.get("out_product_id") or ""),
                    )
                ).lower()
            ]
        else:
            candidates = list(self._all_rows)
        name_term = self.product_name_filter_var.get().strip().casefold()
        id_term = self.product_id_filter_var.get().strip().casefold()
        candidates = [
            row
            for row in candidates
            if (
                not name_term
                or name_term in str(row.get("title") or "").casefold()
            )
            and (
                not id_term
                or id_term
                in (
                    f"{row.get('product_id') or ''} "
                    f"{row.get('out_product_id') or ''} "
                    f"{row.get('spu_code') or ''}"
                ).casefold()
            )
        ]
        self._filtered_rows = [
            row
            for row in candidates
            if self._matches_active_tab(row) and self._matches_listing_time(row)
        ]
        try:
            price_min = int(float(self.price_min_var.get() or 0) * 100)
            price_max = int(float(self.price_max_var.get() or 0) * 100)
            sales_min = int(float(self.sales_min_var.get() or 0))
            sales_max = int(float(self.sales_max_var.get() or 0))
        except ValueError:
            price_min = price_max = sales_min = sales_max = 0
        try:
            ignored = set(json.loads(self.db.get_setting("ignored_products", "[]")))
        except (TypeError, ValueError):
            ignored = set()
        try:
            product_group_map = json.loads(
                self.db.get_setting("product_group_map", "{}")
            )
        except (TypeError, ValueError):
            product_group_map = {}
        product_group_map = (
            product_group_map if isinstance(product_group_map, dict) else {}
        )
        try:
            imported_source_ids = set(
                json.loads(
                    self.db.get_setting("imported_source_ids", "[]")
                )
            )
        except (TypeError, ValueError):
            imported_source_ids = set()
        product_link_map = self.db.product_purchase_link_map()
        relation_filter = self.relation_var.get()
        self._filtered_rows = [
            row
            for row in self._filtered_rows
            if (
                (not price_min or int(row.get("min_price") or 0) >= price_min)
                and (not price_max or int(row.get("min_price") or 0) <= price_max)
                and (
                    not sales_min
                    or int(self._query_meta(row).get("total_sold_num") or 0)
                    >= sales_min
                )
                and (
                    not sales_max
                    or int(self._query_meta(row).get("total_sold_num") or 0)
                    <= sales_max
                )
                and (
                    self.ignore_product_var.get() == "全部"
                    or (
                        self.ignore_product_var.get() == "已忽略"
                        and f"{row['store_id']}:{row['product_id']}" in ignored
                    )
                    or (
                        self.ignore_product_var.get() == "未忽略"
                        and f"{row['store_id']}:{row['product_id']}" not in ignored
                    )
                )
                and (
                    self.product_group_filter_var.get() == "全部商品分组"
                    or product_group_map.get(
                        f"{row['store_id']}:{row['product_id']}"
                    )
                    == self.product_group_filter_var.get()
                )
                and (
                    relation_filter == "全部"
                    or (
                        relation_filter == "已关联"
                        and (
                            (
                                int(row["store_id"]),
                                str(row["product_id"]),
                            )
                            in product_link_map
                            or str(row.get("out_product_id") or "")
                            in imported_source_ids
                        )
                    )
                    or (
                        relation_filter == "未关联"
                        and (
                            int(row["store_id"]),
                            str(row["product_id"]),
                        )
                        not in product_link_map
                        and str(row.get("out_product_id") or "")
                        not in imported_source_ids
                    )
                )
            )
        ]
        self._sort_product_rows()
        if reset_page:
            self._page = 1
        self._refresh_tab_labels()
        self._render_page()
        self.window.set_status(
            f"商品查询完成，当前页签共 {len(self._filtered_rows)} 条"
        )
        self._refresh_catalog_stats()
        self._refresh_quota()

    @staticmethod
    def _row_raw(row: dict[str, Any]) -> dict[str, Any]:
        try:
            payload = json.loads(row.get("raw_json") or "{}")
        except (TypeError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _query_meta(row: dict[str, Any]) -> dict[str, Any]:
        cached = row.get("_query_meta")
        if isinstance(cached, dict):
            return cached
        raw = ProductsPage._row_raw(row)
        listing_timestamp = 0
        for key in (
            "_private_listing_time",
            "_private_audit_time",
            "listing_time",
            "onsale_time",
            "create_time",
            "edit_time",
        ):
            try:
                value = int(raw.get(key) or 0)
            except (TypeError, ValueError):
                value = 0
            if value > 0:
                listing_timestamp = value
                break
        meta = {
            "audit_marker": str(
                raw.get("audit_status")
                or raw.get("audit_result")
                or raw.get("audit_state")
                or ""
            ).lower(),
            "has_strategy": bool(
                raw.get("timing_onsale_info")
                or raw.get("listing_strategy")
            ),
            "listing_timestamp": listing_timestamp,
            "token": str(
                raw.get("product_token")
                or raw.get("token")
                or raw.get("product_code")
                or ""
            ),
            "audit_result": str(
                raw.get("audit_result")
                or raw.get("audit_status_text")
                or raw.get("_private_audit")
                or ""
            ),
            "operation_result": str(
                raw.get("operation_result")
                or raw.get("operation_status_text")
                or ""
            ),
            "total_sold_num": int(raw.get("total_sold_num") or 0),
        }
        row["_query_meta"] = meta
        return meta

    @staticmethod
    def _listing_timestamp(row: dict[str, Any]) -> int:
        return int(
            ProductsPage._query_meta(row).get(
                "listing_timestamp"
            )
            or 0
        )

    def _matches_active_tab(self, row: dict[str, Any]) -> bool:
        return self._matches_tab(row, self.active_tab_key)

    def _matches_tab(self, row: dict[str, Any], key: str) -> bool:
        return product_matches_tab(row, key)

    def _matches_listing_time(self, row: dict[str, Any]) -> bool:
        selected = self.listing_time_var.get()
        if selected == "不限":
            return True
        timestamp = self._listing_timestamp(row)
        if not timestamp:
            return False
        row_date = date.fromtimestamp(timestamp)
        if selected == "自定义":
            try:
                start = (
                    date.fromisoformat(self.listing_start_var.get().strip())
                    if self.listing_start_var.get().strip()
                    else None
                )
                end = (
                    date.fromisoformat(self.listing_end_var.get().strip())
                    if self.listing_end_var.get().strip()
                    else None
                )
            except ValueError:
                return True
            return (start is None or row_date >= start) and (
                end is None or row_date <= end
            )
        days = {"近1天": 1, "近7天": 7, "近30天": 30}.get(selected)
        return bool(days and row_date >= date.today() - timedelta(days=days))

    def _sort_product_rows(self) -> None:
        selected = self.sort_var.get()
        if selected == "上架时间":
            self._filtered_rows.sort(
                key=self._listing_timestamp,
                reverse=True,
            )
        elif selected == "价格从低到高":
            self._filtered_rows.sort(key=lambda row: int(row.get("min_price") or 0))
        elif selected == "价格从高到低":
            self._filtered_rows.sort(
                key=lambda row: int(row.get("min_price") or 0),
                reverse=True,
            )
        elif selected == "销量":
            self._filtered_rows.sort(
                key=lambda row: int(
                    self._query_meta(row).get("total_sold_num") or 0
                ),
                reverse=True,
            )
        elif selected == "库存":
            self._filtered_rows.sort(key=lambda row: int(row.get("stock_total") or 0))
        else:
            self._filtered_rows.sort(
                key=lambda row: (
                    str(row.get("synced_at") or ""),
                    str(row.get("product_id") or ""),
                ),
                reverse=True,
            )

    def _refresh_tab_labels(self) -> None:
        counts = self._compute_tab_counts(self._all_rows)
        self._tab_counts = counts
        for key, label in self.tab_definitions:
            text = f"{label}({counts[key]})" if key == self.active_tab_key else label
            self.tab_buttons[key].configure(
                text=text,
                style="Primary.TButton" if key == self.active_tab_key else "Secondary.TButton",
            )

    def _compute_tab_counts(
        self,
        rows: list[dict[str, Any]],
    ) -> dict[str, int]:
        counts = {
            key: 0
            for key, _label in self.tab_definitions
        }
        for row in rows:
            for key, _label in self.tab_definitions:
                if self._matches_tab(row, key):
                    counts[key] += 1
        return counts

    def _tab_matches(self, row: dict[str, Any], key: str) -> bool:
        return self._matches_tab(row, key)

    def _render_page(self) -> None:
        self._rows.clear()
        total = len(self._filtered_rows)
        total_pages = max(1, (total + self.page_size - 1) // self.page_size)
        self._page = min(max(1, self._page), total_pages)
        start = (self._page - 1) * self.page_size
        page_rows = self._filtered_rows[start : start + self.page_size]
        try:
            imported_source_ids = set(
                json.loads(self.db.get_setting("imported_source_ids", "[]"))
            )
        except (TypeError, ValueError):
            imported_source_ids = set()
        product_link_map = self.db.product_purchase_link_map()
        try:
            ignored = set(
                json.loads(self.db.get_setting("ignored_products", "[]"))
            )
        except (TypeError, ValueError):
            ignored = set()
        try:
            product_group_map = json.loads(
                self.db.get_setting("product_group_map", "{}")
            )
        except (TypeError, ValueError):
            product_group_map = {}
        product_group_map = (
            product_group_map if isinstance(product_group_map, dict) else {}
        )
        cards: list[dict[str, Any]] = []
        for offset, row in enumerate(page_rows):
            index = start + offset
            iid = str(index)
            self._rows[iid] = row
            meta = self._query_meta(row)
            image_url = str(row.get("head_img") or "").strip()
            if not image_url:
                image_url = OrdersPage._payload_image_url(
                    self._row_raw(row)
                )
            link_info = product_link_map.get(
                (int(row["store_id"]), str(row["product_id"])),
                {},
            )
            related = bool(link_info) or (
                str(row.get("out_product_id") or "") in imported_source_ids
            )
            linked_source_ids = [
                str(value)
                for value in link_info.get("source_product_ids") or []
                if str(value).strip()
            ]
            merchant_code = str(row.get("out_product_id") or "").strip()
            if not merchant_code and linked_source_ids:
                merchant_code = linked_source_ids[0]
            created = str(row.get("synced_at") or "")[:19]
            cards.append(
                {
                    "iid": iid,
                    "index": index,
                    "title": row["title"],
                    "image_url": image_url,
                    "merchant_code": merchant_code,
                    "product_id": row["product_id"],
                    "store": row["store_name"],
                    "price": _money(row["min_price"]),
                    "sold": str(int(meta["total_sold_num"])),
                    "has_sku": "是" if int(row.get("sku_count") or 0) > 1 else "否",
                    "status": row["status_text"],
                    "group": product_group_map.get(
                        f"{row['store_id']}:{row['product_id']}",
                        "",
                    ),
                    "related": (
                        f"已关联 {int(link_info.get('mapping_count') or 0)} 规格"
                        if related
                        else "未关联"
                    ),
                    "created_at": created,
                    "ignored_text": (
                        "取消忽略"
                        if f"{row['store_id']}:{row['product_id']}" in ignored
                        else "忽略商品"
                    ),
                }
            )
        self.tree.set_rows(cards)
        self.page_info_var.set(
            f"第 {self._page} / {total_pages} 页 · 共 {total} 条"
        )
        self.select_all_var.set(False)

    def _handle_product_card_action(self, action: str, index: int) -> None:
        iid = str(index)
        if action == "select":
            self.tree.selection_set((iid,))
            return
        if action in {
            "copy_title",
            "copy_merchant_code",
            "copy_product_id",
        }:
            self.tree.selection_set((iid,))
            field = {
                "copy_title": "title",
                "copy_merchant_code": "out_product_id",
                "copy_product_id": "product_id",
            }[action]
            self._copy_product_field(field)
            return
        if action == "edit_title":
            self.tree.selection_set((iid,))
            self._edit_product_title()
            return
        if action == "ignore":
            row = self._rows.get(iid)
            if not row:
                return
            try:
                ignored = set(
                    json.loads(self.db.get_setting("ignored_products", "[]"))
                )
            except (TypeError, ValueError):
                ignored = set()
            key = f"{row['store_id']}:{row['product_id']}"
            if key in ignored:
                ignored.remove(key)
            else:
                ignored.add(key)
            self.db.set_setting(
                "ignored_products",
                json.dumps(sorted(ignored), ensure_ascii=False),
            )
            self._query()
            return
        if action == "link":
            self.tree.selection_set((iid,))
            self._show_product_link_editor()

    def _change_tab(self, key: str) -> None:
        if key == self.active_tab_key:
            return
        if self._task_running():
            self.window.set_status("请等当前任务结束后切换商品页签")
            return
        self.active_tab_key = key
        self._load_cached_products()

    def _change_page(self, offset: int) -> None:
        total_pages = max(
            1,
            (len(self._filtered_rows) + self.page_size - 1) // self.page_size,
        )
        target = self._page + offset
        if 1 <= target <= total_pages:
            self._page = target
            self._render_page()

    def _reset_filters(self) -> None:
        self.search_var.set("")
        self.product_name_filter_var.set("")
        self.product_id_filter_var.set("")
        self.product_status_filter_var.set("销售中")
        self.product_platform_var.set("全部平台")
        self.sales_min_var.set("")
        self.sales_max_var.set("")
        self.price_min_var.set("")
        self.price_max_var.set("")
        self.relation_var.set("全部")
        self.ignore_product_var.set("未忽略")
        self.product_group_filter_var.set("全部商品分组")
        self.sort_var.set("默认排序")
        self.group_var.set("全部分组")
        self._refresh_store_choices()
        self.store_var.set("全部店铺")
        self.listing_time_var.set("不限")
        self.listing_start_var.set("")
        self.listing_end_var.set("")
        self._load_cached_products()

    def _on_product_status(self, _event=None) -> None:
        mapping = {
            "销售中": "sales",
            "审核中": "audit",
            "审核失败": "failed",
            "草稿箱": "draft",
            "已下架": "offline",
            "回收站": "recycle",
            "上架策略": "strategy",
        }
        self._change_tab(mapping.get(self.product_status_filter_var.get(), "sales"))

    def _product_action_menu(
        self,
        parent: tk.Widget,
        label: str,
        actions: tuple[tuple[str, Callable[[], None]], ...],
    ) -> None:
        menu_button = tk.Menubutton(
            parent,
            text=label,
            bg="#f8fbff",
            fg="#36506e",
            activebackground="#e7effa",
            relief="flat",
            bd=0,
            padx=10,
            pady=5,
            cursor="hand2",
            font=("Microsoft YaHei UI", 9),
        )
        menu_button.pack(side="left", padx=(6, 0))
        menu = tk.Menu(menu_button, tearoff=0)
        for text, command in actions:
            menu.add_command(label=text, command=command)
        menu_button.configure(menu=menu)

    def _batch_ignore_products(self) -> None:
        selected = self.tree.selection()
        if not selected:
            messagebox.showinfo("请选择商品", "请先选择至少一个商品。", parent=self)
            return
        try:
            ignored = set(json.loads(self.db.get_setting("ignored_products", "[]")))
        except (TypeError, ValueError):
            ignored = set()
        for iid in selected:
            row = self._rows.get(iid)
            if row:
                ignored.add(f"{row['store_id']}:{row['product_id']}")
        self.db.set_setting(
            "ignored_products",
            json.dumps(sorted(ignored), ensure_ascii=False),
        )
        self._query()

    def _import_source_ids(self) -> None:
        path = filedialog.askopenfilename(
            title="导入货源ID",
            filetypes=(("CSV/文本", "*.csv *.txt"), ("全部文件", "*.*")),
            parent=self,
        )
        if not path:
            return
        try:
            text = Path(path).read_text(encoding="utf-8-sig")
        except OSError as exc:
            messagebox.showerror("导入失败", str(exc), parent=self)
            return
        ids = {
            line.split(",", 1)[0].split("\t", 1)[0].strip()
            for line in text.splitlines()
            if line.strip()
        }
        self.db.set_setting(
            "imported_source_ids",
            json.dumps(sorted(ids), ensure_ascii=False),
        )
        self.window.set_status(f"已导入 {len(ids)} 个货源ID")

    def _download_source_template(self) -> None:
        path = filedialog.asksaveasfilename(
            title="保存货源ID模板",
            defaultextension=".csv",
            filetypes=(("CSV", "*.csv"),),
            parent=self,
        )
        if not path:
            return
        Path(path).write_text("source_id\n", encoding="utf-8-sig")
        self.window.set_status("货源ID模板已保存")

    def _manage_product_groups(self) -> None:
        dialog = tk.Toplevel(self)
        dialog.title("商品分组管理")
        dialog.geometry("520x420")
        text = tk.Text(dialog, wrap="word")
        text.pack(fill="both", expand=True, padx=12, pady=12)
        text.insert(
            "1.0",
            self.db.get_setting("product_group_rules", "每行填写一个分组名称"),
        )

        def save() -> None:
            self.db.set_setting(
                "product_group_rules",
                text.get("1.0", "end").strip(),
            )
            dialog.destroy()
            self._reload_stores()
            self._query()
            self.window.set_status("商品分组规则已保存")

        ttk.Button(dialog, text="保存", command=save).pack(pady=(0, 12))

    def _batch_group_products(self) -> None:
        selected = self.tree.selection()
        if not selected:
            messagebox.showinfo("请选择商品", "请先选择至少一个商品。", parent=self)
            return
        rows = [self._rows[iid] for iid in selected if iid in self._rows]

        def save(values: dict[str, str]) -> None:
            group_name = str(values.get("group_name") or "").strip()
            if not group_name:
                messagebox.showwarning("商品分组", "请输入商品分组名称。", parent=self)
                return
            try:
                mapping = json.loads(
                    self.db.get_setting("product_group_map", "{}")
                )
            except (TypeError, ValueError):
                mapping = {}
            mapping = mapping if isinstance(mapping, dict) else {}
            for row in rows:
                mapping[f"{row['store_id']}:{row['product_id']}"] = group_name
            self.db.set_setting(
                "product_group_map",
                json.dumps(mapping, ensure_ascii=False),
            )
            self._query()
            self.window.set_status(f"已设置 {len(rows)} 个商品的商品分组")

        SimpleFieldsDialog(
            self,
            "批量分组",
            (("group_name", "商品分组"),),
            save,
        )

    def _open_upload_page(self) -> None:
        self.window.show_page("upload")

    def _toggle_select_all(self) -> None:
        all_items = tuple(self._rows)
        self.tree.selection_set(all_items if self.select_all_var.get() else ())

    def _copy_product_title(self, event) -> None:
        if self.tree.identify_column(event.x) != "#1":
            return
        iid = self.tree.identify_row(event.y)
        row = self._rows.get(iid)
        if row is None or not row.get("title"):
            return
        self.clipboard_clear()
        self.clipboard_append(str(row["title"]))
        self.window.set_status("商品名称已复制")

    def _copy_product_field(self, field: str) -> None:
        row = self._selected_product_row()
        if row is None:
            return
        value = str(row.get(field) or "").strip()
        if not value:
            messagebox.showinfo("没有内容", "当前商品没有可复制的值。", parent=self)
            return
        self.clipboard_clear()
        self.clipboard_append(value)
        labels = {
            "title": "商品标题",
            "out_product_id": "商家编码",
            "product_id": "商品ID",
        }
        self.window.set_status(f"{labels.get(field, '商品字段')}已复制")

    def _edit_product_title(self) -> None:
        row = self._selected_product_row()
        if row is None:
            messagebox.showinfo("请选择商品", "请先选择一条商品。", parent=self)
            return
        store = self.db.get_store(int(row["store_id"]))
        if store is None:
            return

        def show(fresh) -> None:
            payload = self._editable_product_payload(
                json.loads(fresh["raw_json"])
            )
            payload["product_id"] = str(fresh["product_id"])

            def save(values: dict[str, str]) -> None:
                title = str(values.get("title") or "").strip()
                if not title:
                    messagebox.showwarning(
                        "商品标题",
                        "商品标题不能为空。",
                        parent=self,
                    )
                    return
                payload["title"] = title
                self._submit_product(store, "update", payload)

            SimpleFieldsDialog(
                self,
                "修改商品标题",
                (("title", "商品标题"),),
                save,
                {"title": str(payload.get("title") or "")},
            )

        self._load_product_detail(row, show)

    def _show_product_menu(self, event) -> None:
        iid = self.tree.identify_row(event.y)
        if iid:
            self.tree.selection_set(iid)
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label="实时商品详情", command=lambda: self._show_detail())
        menu.add_command(
            label="复制商品标题",
            command=lambda: self._copy_product_field("title"),
        )
        menu.add_command(
            label="复制商家编码",
            command=lambda: self._copy_product_field("out_product_id"),
        )
        menu.add_command(
            label="复制商品ID",
            command=lambda: self._copy_product_field("product_id"),
        )
        menu.add_command(label="修改商品标题", command=self._edit_product_title)
        menu.add_command(
            label="关联/更换上家",
            command=self._show_product_link_editor,
        )
        menu.add_command(label="打开后台编辑", command=self._edit_product)
        menu.add_command(label="编辑商品表单", command=self._edit_product_form)
        menu.add_command(label="高级字段(JSON)", command=self._advanced_edit_product)
        menu.add_command(label="上货发布", command=self._add_product)
        menu.add_separator()
        menu.add_command(label="撤回审核", command=self._cancel_audit)
        menu.add_command(label="删除商品", command=self._delete_product)
        menu.add_command(label="操作记录", command=self._show_operations)
        menu.add_separator()
        menu.add_command(label="刷新目录", command=self._refresh_catalog)
        menu.add_command(
            label="刷新服务端状态",
            command=self._refresh_server_status_counts,
        )
        menu.add_command(
            label="同步当前页签",
            command=lambda: self._sync_private_product_tab(
                self.active_tab_key,
            ),
        )
        menu.add_command(label="同步待处理", command=self._sync_pending)
        menu.add_command(label="停止同步", command=self._stop_sync)
        menu.add_command(label="显示字段", command=self._show_product_fields)
        menu.add_command(label="导出 XLSX", command=self._export)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _show_product_fields(self) -> None:
        labels = {
            "sequence": "序号",
            "store": "店铺名称",
            "product_id": "商品ID",
            "title": "商品名称",
            "token": "口令",
            "price": "价格",
            "stock": "库存",
            "sold": "销量",
            "status": "状态",
            "listed": "上架时间",
            "audit": "审核结果",
            "operation": "操作结果",
        }
        dialog = tk.Toplevel(self)
        dialog.title("商品字段设置")
        dialog.transient(self.winfo_toplevel())
        dialog.resizable(False, False)
        variables = {}
        for key, label in labels.items():
            variable = tk.BooleanVar(value=key in self._visible_product_fields)
            variables[key] = variable
            tk.Checkbutton(
                dialog,
                text=label,
                variable=variable,
                anchor="w",
            ).pack(fill="x", padx=18, pady=3)

        def apply_fields() -> None:
            visible = [key for key in labels if variables[key].get()]
            if not visible:
                messagebox.showwarning("字段设置", "至少保留一个字段。", parent=dialog)
                return
            self._visible_product_fields = visible
            self.tree["displaycolumns"] = tuple(visible)
            dialog.destroy()

        ttk.Button(dialog, text="应用", command=apply_fields).pack(pady=12)
        dialog.grab_set()

    def _sync_products(self) -> None:
        self._start_product_sync("一键同步商品", self._run_sync_products)

    def _refresh_catalog(self) -> None:
        self._start_product_sync("刷新商品目录", self._run_refresh_catalog)

    def _refresh_server_status_counts(self) -> None:
        value = self._store_lookup.get(self.store_var.get())
        if not isinstance(value, Store):
            messagebox.showinfo(
                "请选择店铺",
                "刷新服务端商品状态前请选择具体店铺。",
                parent=self,
            )
            return
        self.cancel_event.clear()
        self.window.set_status("正在读取服务端商品状态数量…", busy=True)

        def done(result) -> None:
            self.window.set_status(result.message)
            self._query_products()

        self.window.run_task(
            lambda progress: self.window.service.refresh_product_status_snapshot(
                value,
                progress,
                cancel_event=self.cancel_event,
            ),
            done,
            lambda error: messagebox.showerror(
                "服务端状态刷新失败",
                error,
                parent=self,
            ),
            self._progress,
            log_title="刷新服务端商品状态数量",
        )

    def _sync_private_product_tab(self, tab_key: str) -> None:
        if self._task_running():
            self.window.set_status("请等当前任务结束后同步商品")
            return
        self.active_tab_key = tab_key
        self._query_products()

    def _task_running(self) -> bool:
        checker = getattr(self.window, "has_running_task", None)
        if callable(checker):
            return bool(checker("products"))
        return bool(getattr(self.window, "_task_running", False))

    def _sync_pending(self) -> None:
        self._start_product_sync("同步待处理商品", self._run_sync_pending)

    def _run_sync_products(self, store: Store, limit: int | None, cancel):
        return self.window.service.sync_products(
            store,
            self._progress,
            max_items=limit,
            workers=4,
            cancel_event=cancel,
        )

    def _run_refresh_catalog(self, store: Store, _limit: int | None, cancel):
        return self.window.service.scan_product_catalog(
            store,
            self._progress,
            cancel_event=cancel,
            reset=True,
        )

    def _run_sync_pending(self, store: Store, limit: int | None, cancel):
        return self.window.service.sync_product_details(
            store,
            self._progress,
            max_items=limit,
            workers=4,
            cancel_event=cancel,
        )

    def _start_product_sync(self, label: str, runner) -> None:
        value = self._store_lookup.get(self.store_var.get())
        if not isinstance(value, Store):
            messagebox.showinfo("请选择店铺", f"{label}前请选择具体店铺。", parent=self)
            return
        store = value
        if not self._require_product_api(store):
            return
        try:
            limit = int(self.product_limit_var.get().strip() or "0")
        except ValueError:
            messagebox.showwarning("每批数量", "每批商品数量必须是整数，0 表示不限。", parent=self)
            return
        self.cancel_event.clear()
        self.window.set_status(f"{label}…", busy=True)

        def done(result) -> None:
            self.window.set_status(result.message)
            self._query_products()
            if result.errors:
                messagebox.showwarning(
                    "部分商品同步失败",
                    "\n".join(result.errors[:8]),
                    parent=self,
                )

        self.window.run_task(
            lambda progress: runner(
                store,
                limit if limit > 0 else None,
                self.cancel_event,
            ),
            done,
            lambda error: messagebox.showerror("商品同步失败", error, parent=self),
            self._progress,
            log_title=label,
        )

    def _stop_sync(self) -> None:
        self.cancel_event.set()
        self.window.set_status("已请求停止，正在等待当前商品请求结束…", busy=True)

    def _show_listing_settings(self) -> None:
        try:
            settings = json.loads(
                self.db.get_setting("product_listing_settings", "{}")
            )
        except (TypeError, ValueError):
            settings = {}
        dialog = tk.Toplevel(self)
        dialog.title("上架设置")
        dialog.transient(self.winfo_toplevel())
        dialog.resizable(False, False)
        panel = ttk.Frame(dialog, style="Panel.TFrame", padding=20)
        panel.pack(fill="both", expand=True)
        max_count = tk.StringVar(
            value=str(settings.get("max_count", 200))
        )
        min_stock = tk.StringVar(
            value=str(settings.get("min_stock", 1))
        )
        skip_zero = tk.BooleanVar(
            value=bool(settings.get("skip_zero_stock", True))
        )
        skip_blacklist = tk.BooleanVar(
            value=bool(settings.get("skip_blacklist", True))
        )
        for row, (label, variable) in enumerate(
            (
                ("本次最多上架", max_count),
                ("最低库存", min_stock),
            )
        ):
            ttk.Label(panel, text=label, style="Panel.TLabel").grid(
                row=row, column=0, sticky="w", padx=(0, 12), pady=6
            )
            ttk.Entry(panel, textvariable=variable, width=18).grid(
                row=row, column=1, sticky="ew", pady=6
            )
        tk.Checkbutton(
            panel,
            text="跳过零库存商品",
            variable=skip_zero,
            bg=PANEL,
            fg=TEXT,
            activebackground=PANEL,
            selectcolor="white",
            bd=0,
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=4)
        tk.Checkbutton(
            panel,
            text="跳过违规黑名单商品",
            variable=skip_blacklist,
            bg=PANEL,
            fg=TEXT,
            activebackground=PANEL,
            selectcolor="white",
            bd=0,
        ).grid(row=3, column=0, columnspan=2, sticky="w", pady=4)

        def save() -> None:
            try:
                payload = {
                    "max_count": max(0, int(max_count.get() or 0)),
                    "min_stock": max(0, int(min_stock.get() or 0)),
                    "skip_zero_stock": skip_zero.get(),
                    "skip_blacklist": skip_blacklist.get(),
                }
            except ValueError:
                messagebox.showwarning(
                    "上架设置", "数量必须是整数。", parent=dialog
                )
                return
            self.db.set_setting(
                "product_listing_settings",
                json.dumps(payload, ensure_ascii=False),
            )
            dialog.destroy()
            self.window.set_status("上架设置已保存")

        actions = ttk.Frame(panel, style="Panel.TFrame")
        actions.grid(row=4, column=0, columnspan=2, sticky="e", pady=(12, 0))
        button(actions, "取消", dialog.destroy).pack(side="left", padx=6)
        button(actions, "保存", save, "Primary").pack(side="left")
        dialog.grab_set()

    def _get_product_token(self) -> None:
        value = self._store_lookup.get(self.store_var.get())
        if not isinstance(value, Store):
            messagebox.showinfo("请选择店铺", "获取商品口令前请选择具体店铺。", parent=self)
            return
        row = self._selected_product_row()
        if row is None:
            messagebox.showinfo("请选择商品", "请先选择一条商品记录。", parent=self)
            return
        if not self._require_product_api(value):
            return
        self.window.set_status("正在向微信获取商品口令…", busy=True)

        def done(result: str) -> None:
            self.clipboard_clear()
            self.clipboard_append(result)
            self.window.set_status("商品口令已获取并复制")
            messagebox.showinfo(
                "获取口令",
                f"商品口令已复制：\n{result}",
                parent=self,
            )

        self.window.run_task(
            lambda _progress: self.window.service.fetch_product_taglink(
                value,
                str(row["product_id"]),
            ),
            done,
            lambda error: messagebox.showerror(
                "获取商品口令失败",
                error,
                parent=self,
            ),
            log_title="获取商品口令",
        )

    def _batch_delete_products(self) -> None:
        self._delete_selected_products("批量删除")

    def _batch_delete_violations(self) -> None:
        product_ids = [
            str(self._rows[iid]["product_id"])
            for iid in self.tree.selection()
            if iid in self._rows
            and int(self._rows[iid].get("status") or 0) in {13, 14, 15, 20}
        ]
        if not product_ids:
            messagebox.showinfo(
                "没有违规商品",
                "当前选择中没有违规下架商品。请切换到“已下架”页签后选择。",
                parent=self,
            )
            return
        product_ids = self._exclude_blacklisted(
            product_ids,
            "批量删除违规商品",
        )
        if product_ids:
            self._delete_product_ids(product_ids, "批量删除违规商品")

    def _delete_selected_products(self, label: str) -> None:
        product_ids = [
            str(self._rows[iid]["product_id"])
            for iid in self.tree.selection()
            if iid in self._rows
        ]
        if not product_ids:
            messagebox.showinfo("请选择商品", "请先选择至少一个商品。", parent=self)
            return
        product_ids = self._exclude_blacklisted(product_ids, label)
        if product_ids:
            self._delete_product_ids(product_ids, label)

    def _exclude_blacklisted(
        self,
        product_ids: list[str],
        action: str,
    ) -> list[str]:
        blocked = self.db.violation_blacklist_ids().intersection(product_ids)
        if not blocked:
            return product_ids
        messagebox.showwarning(
            "违规黑名单拦截",
            f"已阻止对 {len(blocked)} 个黑名单商品执行{action}：\n"
            + "\n".join(sorted(blocked)[:12]),
            parent=self,
        )
        return [value for value in product_ids if value not in blocked]

    def _delete_product_ids(self, product_ids: list[str], label: str) -> None:
        value = self._store_lookup.get(self.store_var.get())
        if not isinstance(value, Store):
            messagebox.showinfo("请选择店铺", f"{label}前请选择具体店铺。", parent=self)
            return
        if not messagebox.askyesno(
            label,
            f"即将对“{value.name}”的 {len(product_ids)} 个商品执行线上删除。\n"
            "该操作不可逆，审核中的商品可能需要先撤回审核。是否继续？",
            parent=self,
        ):
            return
        self.window.set_status(f"正在执行{label}…", busy=True)

        def work(_progress) -> list[str]:
            errors = []
            for product_id in product_ids:
                try:
                    self.window.service.product_write(
                        value,
                        "delete",
                        product_id=product_id,
                    )
                except Exception as exc:
                    errors.append(f"{product_id}: {exc}")
            return errors

        def done(errors: list[str]) -> None:
            self.window.set_status(
                f"{label}完成，成功 {len(product_ids) - len(errors)} 个"
            )
            self._query_products()
            if errors:
                messagebox.showwarning(
                    f"{label}部分失败",
                    "\n".join(errors[:8]),
                    parent=self,
                )

        self.window.run_task(
            work,
            done,
            lambda error: self._product_action_failed(f"{label}失败", error),
            log_title=f"商品批量操作：{label}",
        )

    def _refresh_catalog_stats(self) -> None:
        value = self._store_lookup.get(self.store_var.get())
        store = value if isinstance(value, Store) else None
        stats = self.db.product_catalog_stats(store.id if store else None)
        self.catalog_stats_var.set(
            f"目录 {stats['total']} · 已同步 {stats['done']} · "
            f"待处理 {stats['pending']} · 失败 {stats['failed']} · "
            f"已消失 {stats['missing']}"
        )

    def _refresh_quota(self) -> None:
        value = self._store_lookup.get(self.store_var.get())
        if not isinstance(value, Store):
            self.quota_var.set("提审额度 —")
            return
        try:
            quota = json.loads(
                self.db.get_setting(f"product_quota_{value.id}", "{}")
            )
        except json.JSONDecodeError:
            quota = {}
        remaining = int(quota.get("remaining") or 0)
        total = int(quota.get("total") or 0)
        self.quota_var.set(
            f"提审额度 {remaining}/{total}" if total or remaining else "提审额度 —"
        )

    def _progress(self, message: str, current: int, total: int) -> None:
        self.window.set_status(message, busy=True)
        self.progress_var.set((current / total * 100) if total else 0)

    def _batch_listing(
        self,
        listed: bool,
        one_click: bool = False,
    ) -> None:
        value = self._store_lookup.get(self.store_var.get())
        if not isinstance(value, Store):
            messagebox.showinfo("请选择店铺", "批量操作前请选择具体店铺。", parent=self)
            return
        if listed and not self._require_product_api(value):
            return
        product_ids = [
            str(self._rows[iid]["product_id"])
            for iid in self.tree.selection()
            if iid in self._rows
        ]
        if not product_ids and listed and one_click:
            try:
                settings = json.loads(
                    self.db.get_setting(
                        "product_listing_settings", "{}"
                    )
                )
            except (TypeError, ValueError):
                settings = {}
            max_count = max(0, int(settings.get("max_count") or 0))
            min_stock = max(0, int(settings.get("min_stock") or 0))
            blacklist = (
                self.db.violation_blacklist_ids()
                if settings.get("skip_blacklist", True)
                else set()
            )
            candidates = [
                row
                for row in self._all_rows
                if int(row.get("status") or 0) in {11, 12, 13, 20}
                and int(row.get("stock_total") or 0) >= min_stock
                and (
                    not settings.get("skip_zero_stock", True)
                    or int(row.get("stock_total") or 0) > 0
                )
                and str(row.get("product_id")) not in blacklist
            ]
            if max_count:
                candidates = candidates[:max_count]
            product_ids = [
                str(row["product_id"]) for row in candidates
            ]
        if not product_ids:
            messagebox.showinfo("请选择商品", "请先选择至少一个商品。", parent=self)
            return
        action = "上架" if listed else "下架"
        product_ids = self._exclude_blacklisted(product_ids, action)
        if not product_ids:
            return
        if not messagebox.askyesno(
            f"确认批量{action}",
            f"即将对“{value.name}”的 {len(product_ids)} 个商品执行线上{action}。\n\n"
            "这是正式平台操作，请确认选择无误。",
            parent=self,
        ):
            return
        self.cancel_event.clear()
        self.window.set_status(f"正在批量{action}…", busy=True)

        def done(result) -> None:
            self.window.set_status(result.message)
            self._query_products()
            if result.errors:
                messagebox.showwarning(
                    f"部分商品{action}失败",
                    "\n".join(result.errors[:8]),
                    parent=self,
                )

        self.window.run_task(
            lambda progress: self.window.service.set_products_listing(
                value,
                product_ids,
                listed,
                progress,
                cancel_event=self.cancel_event,
            ),
            done,
            lambda error: self._product_action_failed(f"批量{action}失败", error),
            self._progress,
            log_title=f"批量{action}商品",
        )

    def _show_operations(self) -> None:
        value = self._store_lookup.get(self.store_var.get())
        if not isinstance(value, Store):
            messagebox.showinfo("请选择店铺", "查看操作记录前请选择具体店铺。", parent=self)
            return
        ProductOperationsDialog(
            self,
            self.db.list_product_operations(value.id),
        )

    def _selected_product_row(self) -> dict[str, Any] | None:
        selection = self.tree.selection()
        if not selection:
            return None
        return self._rows.get(selection[0])

    @staticmethod
    def _editable_product_payload(raw: dict | None) -> dict[str, Any]:
        raw = raw or {}
        editable_keys = (
            "out_product_id",
            "title",
            "short_title",
            "head_imgs",
            "deliver_method",
            "deliver_acct_type",
            "desc_info",
            "cats",
            "cats_v2",
            "attrs",
            "spu_code",
            "brand_id",
            "product_qua_infos",
            "express_info",
            "aftersale_desc",
            "limited_info",
            "extra_service",
            "skus",
            "listing",
            "after_sale_info",
            "size_chart",
            "hide_in_window",
            "release_mode",
            "timing_onsale_info",
            "head_videos",
            "spu_deliver_info",
            "supply_source",
            "vertical_head_imgs",
            "white_bg_imgs",
        )
        payload = {key: raw[key] for key in editable_keys if key in raw}
        payload.setdefault("title", "")
        payload.setdefault("head_imgs", [])
        payload.setdefault("deliver_method", 0)
        payload.setdefault("deliver_acct_type", [])
        payload.setdefault("cats", [])
        payload.setdefault("cats_v2", [])
        payload.setdefault("desc_info", {"imgs": [], "desc": ""})
        payload.setdefault("extra_service", {"seven_day_return": 0, "freight_insurance": 0})
        payload.setdefault("skus", [])
        payload["listing"] = 0
        for sku in payload.get("skus", []):
            if isinstance(sku, dict):
                sku.pop("status", None)
        return payload

    def _add_product(self) -> None:
        store = self._store_lookup.get(self.store_var.get())
        if not isinstance(store, Store):
            messagebox.showinfo("请选择店铺", "上货前请选择具体店铺。", parent=self)
            return
        if not self._require_product_api(store):
            return

        def show(fresh=None) -> None:
            raw = json.loads(fresh["raw_json"]) if fresh else None
            payload = self._editable_product_payload(raw)
            payload.pop("product_id", None)
            ProductFormDialog(
                self, "上货发布", payload, "add",
                lambda product: self._submit_product(store, "add", product),
                lambda paths: self.window.run_logged_call(
                    "上货：上传商品图片",
                    lambda: self.window.service.upload_product_images(store, paths),
                    scope="products", store_id=store.id,
                    success_message=f"图片上传完成：{len(paths)} 个文件",
                ),
            )

        source = self._selected_product_row()
        if source:
            self._load_product_detail(source, show)
        else:
            show()

    def _edit_product_form(self) -> None:
        self._open_product_editor(ProductFormDialog)

    def _open_product_editor(self, dialog_class) -> None:
        row = self._selected_product_row()
        if row is None:
            messagebox.showinfo("请选择商品", "请先选择一条商品。", parent=self)
            return
        store = self.db.get_store(row["store_id"])
        if store is None:
            return

        def show(fresh) -> None:
            payload = self._editable_product_payload(json.loads(fresh["raw_json"]))
            payload["product_id"] = fresh["product_id"]
            dialog_class(
                self, "编辑商品", payload, "update",
                lambda product: self._submit_product(store, "update", product),
                lambda paths: self.window.run_logged_call(
                    "编辑商品：上传商品图片",
                    lambda: self.window.service.upload_product_images(store, paths),
                    scope="products", store_id=store.id,
                    success_message=f"图片上传完成：{len(paths)} 个文件",
                ),
            )

        self._load_product_detail(row, show)

    def _edit_product(self) -> None:
        value = self._store_lookup.get(self.store_var.get())
        row = self._selected_product_row()
        if not isinstance(value, Store) and row is not None:
            value = self.db.get_store(row["store_id"])
        if not isinstance(value, Store):
            messagebox.showinfo("请选择店铺", "打开后台编辑前请选择具体店铺。", parent=self)
            return
        self.window.set_status(f"正在打开“{value.name}”的微信商品后台…", busy=True)

        def done(_result) -> None:
            self.window.set_status("微信商品后台已关闭")
            self.window.store_data_changed()

        self.window.run_task(
            lambda _progress: self.window.web_session.open_official_product_backend(value),
            done,
            lambda error: messagebox.showerror(
                "打开商品后台失败",
                error,
                parent=self,
            ),
            log_title="打开官方商品后台",
        )

    def _advanced_edit_product(self) -> None:
        self._open_product_editor(ProductJsonDialog)

    def _submit_product(self, store: Store, action: str, payload: dict) -> None:
        if not self._require_product_api(store):
            return
        if not messagebox.askyesno(
            "确认提交商品",
            "该操作会提交到微信小店，可能消耗提审或编辑额度。是否继续？",
            parent=self,
        ):
            return
        self.window.set_status("正在提交商品…", busy=True)

        def done(_result) -> None:
            self.window.set_status("商品操作已提交")
            self._query_products()

        self.window.run_task(
            lambda _progress: self.window.service.product_write(
                store,
                action,
                payload=payload,
            ),
            done,
            lambda error: self._product_action_failed("商品操作失败", error),
            log_title=(
                "发布商品"
                if action == "add"
                else "更新商品"
            ),
        )

    def _cancel_audit(self) -> None:
        value = self._store_lookup.get(self.store_var.get())
        row = self._selected_product_row()
        if not isinstance(value, Store) or row is None:
            messagebox.showinfo("请选择商品", "请先选择一条商品记录。", parent=self)
            return
        if not self._require_product_api(value):
            return
        if not messagebox.askyesno(
            "撤回商品审核",
            f"确定撤回商品 {row['product_id']} 的审核申请吗？",
            parent=self,
        ):
            return
        self.window.run_task(
            lambda _progress: self.window.service.product_write(
                value,
                "cancel_audit",
                product_id=row["product_id"],
            ),
            lambda _result: self._query_products(),
            lambda error: self._product_action_failed("撤回审核失败", error),
            log_title="撤回商品审核",
        )

    def _delete_product(self) -> None:
        value = self._store_lookup.get(self.store_var.get())
        row = self._selected_product_row()
        if not isinstance(value, Store) or row is None:
            messagebox.showinfo("请选择商品", "请先选择一条商品记录。", parent=self)
            return
        product_id = str(row["product_id"])
        if product_id in self.db.violation_blacklist_ids():
            messagebox.showwarning(
                "违规黑名单拦截",
                f"商品 {product_id} 在黑名单中，已阻止删除操作。",
                parent=self,
            )
            return
        if not messagebox.askyesno(
            "删除商品",
            f"确定删除商品 {product_id} 吗？\n审核中的商品需要先撤回审核。",
            parent=self,
        ):
            return
        self.window.run_task(
            lambda _progress: self.window.service.product_write(
                value,
                "delete",
                product_id=product_id,
            ),
            lambda _result: self._query_products(),
            lambda error: self._product_action_failed("删除商品失败", error),
            log_title="删除商品",
        )

    def _product_action_failed(self, title: str, error: str) -> None:
        # A timeout can occur after WeChat accepted the write. Reconcile remotely.
        self._query_products()
        messagebox.showerror(title, error, parent=self)

    def _require_product_api(self, store: Store) -> bool:
        if store.app_id == "demo-store" or self.db.get_store_secret(store.id)[1]:
            return True
        messagebox.showinfo(
            "需要店铺接口授权",
            "此操作需要店铺的 AppSecret。请在店铺管理中配置接口凭据，"
            "或使用“打开后台编辑”在微信商家后台操作。",
            parent=self,
        )
        return False

    def _load_product_detail(self, row, on_done) -> None:
        store = self.db.get_store(row["store_id"])
        if store is None or not self._require_product_api(store):
            return

        def work(_progress):
            if store.app_id == "demo-store":
                return dict(row)
            product = self.window.service.client_for(store).fetch_product(
                store, str(row["product_id"])
            )
            fresh = asdict(product)
            fresh["raw_json"] = json.dumps(product.raw, ensure_ascii=False)
            return fresh

        self.window.run_task(
            work, on_done,
            lambda error: messagebox.showerror("读取实时商品详情失败", error, parent=self),
            log_title="读取实时商品详情",
        )

    def _show_detail(self, _event=None) -> None:
        selection = self.tree.selection()
        if not selection:
            return
        row = self._rows.get(selection[0])
        def show(fresh) -> None:
            ProductDetailDialog(
                self,
                fresh,
                on_adjust_stock=lambda sku_id, diff_type, amount: self._update_stock(
                    fresh["store_id"],
                    fresh["product_id"],
                    sku_id,
                    diff_type,
                    amount,
                ),
            )
        if row:
            self._load_product_detail(row, show)

    def _show_product_link_editor(self) -> None:
        row = self._selected_product_row()
        if row is None:
            messagebox.showinfo(
                "请选择商品",
                "请先选择需要关联规格的商品。",
                parent=self,
            )
            return
        ProductPurchaseLinkDialog(
            self,
            db=self.db,
            window=self.window,
            store_id=int(row["store_id"]),
            product=row,
            on_saved=self._after_product_links_saved,
        )

    def _after_product_links_saved(self) -> None:
        created = self.db.sync_auto_dispatch_tasks()
        self._query()
        if created:
            self.window.set_status(
                f"商品关联已保存，并自动派单 {created} 条待采购任务"
            )

    def _update_stock(
        self,
        store_id: int,
        product_id: str,
        sku_id: str,
        diff_type: int,
        amount: int,
    ) -> None:
        store = self.db.get_store(store_id)
        if store is None:
            messagebox.showerror("店铺不存在", "商品所属店铺已不存在。", parent=self)
            return
        if not self._require_product_api(store):
            return
        self.window.set_status("正在更新线上库存…", busy=True)

        def done(_product) -> None:
            self.window.set_status("库存更新成功")
            self._query_products()
            messagebox.showinfo("库存更新成功", "商品库存已刷新。", parent=self)

        self.window.run_task(
            lambda _progress: self.window.service.update_product_stock(
                store,
                product_id,
                sku_id,
                diff_type,
                amount,
            ),
            done,
            lambda error: self._product_action_failed("库存更新失败", error),
            log_title="更新商品库存",
        )

    def _export(self) -> None:
        if not self._filtered_rows:
            messagebox.showinfo("没有数据", "当前筛选结果中没有可导出的商品。", parent=self)
            return
        path = filedialog.asksaveasfilename(
            parent=self,
            title="导出商品",
            defaultextension=".xlsx",
            filetypes=[("Excel 工作簿", "*.xlsx")],
            initialfile=f"商品_{date.today():%Y%m%d}.xlsx",
        )
        if not path:
            return
        headers = [
            "序号",
            "店铺名称",
            "商品ID",
            "商品名称",
            "口令",
            "价格",
            "库存",
            "销量",
            "状态",
            "上架时间",
            "审核结果",
            "操作结果",
        ]
        rows = [
            [
                index,
                row["store_name"],
                row["product_id"],
                row["title"],
                self._row_raw(row).get("product_token")
                or self._row_raw(row).get("token")
                or "",
                row["min_price"] / 100,
                row["stock_total"],
                int(self._row_raw(row).get("total_sold_num") or 0),
                row["status_text"],
                _format_time(self._listing_timestamp(row))
                if self._listing_timestamp(row)
                else "",
                self._row_raw(row).get("audit_result") or "",
                self._row_raw(row).get("operation_result") or "",
            ]
            for index, row in enumerate(self._filtered_rows, 1)
        ]
        try:
            export_xlsx(path, "商品", headers, rows)
        except Exception as exc:
            messagebox.showerror("导出失败", str(exc), parent=self)
            return
        self.window.set_status(f"已导出：{Path(path).name}")


class ShippingPage(BasePage):
    title = "物流发货"
    subtitle = "电子面单账号、模板、取号、打印确认和订单发货"

    def __init__(self, parent: tk.Widget, window: "MainWindow"):
        super().__init__(parent, window)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(4, weight=1)
        self._store_lookup: dict[str, Store] = {}
        self._rows: dict[str, dict[str, Any]] = {}
        self.store_var = tk.StringVar()
        self.config_summary_var = tk.StringVar(value="尚未配置电子面单")
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
        self.store_combo.bind(
            "<<ComboboxSelected>>",
            lambda _event: self._load_store(),
        )
        button(filters, "刷新物流配置", self._refresh_options, "Primary").pack(side="left")
        button(filters, "发货设置", self._open_config).pack(side="left", padx=8)

        actions = ttk.Frame(self, style="TFrame")
        actions.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        button(actions, "生成电子面单", self._create_shipment, "Primary").pack(side="left")
        button(actions, "查看面单/发货", self._open_shipment).pack(side="left", padx=8)
        ttk.Label(
            actions,
            text="取号会占用面单库存；未确认打印前不会自动发货",
            style="Subtitle.TLabel",
        ).pack(side="left", padx=12)

        ttk.Label(
            self,
            textvariable=self.config_summary_var,
            style="Subtitle.TLabel",
        ).grid(row=2, column=0, sticky="w", pady=(0, 6))
        self.progress = ttk.Progressbar(self, variable=self.progress_var, maximum=100)
        self.progress.grid(row=3, column=0, sticky="ew", pady=(0, 8))

        table = ttk.Frame(self, style="Panel.TFrame")
        table.grid(row=4, column=0, sticky="nsew")
        table.rowconfigure(0, weight=1)
        table.columnconfigure(0, weight=1)
        columns = (
            "order_id",
            "status",
            "buyer",
            "phone",
            "address",
            "carrier",
            "waybill",
            "shipment",
        )
        self.tree = ttk.Treeview(table, columns=columns, show="headings")
        headings = {
            "order_id": ("订单号", 180),
            "status": ("订单状态", 100),
            "buyer": ("收货人", 100),
            "phone": ("电话/虚拟号", 140),
            "address": ("地址", 330),
            "carrier": ("快递", 100),
            "waybill": ("运单号", 170),
            "shipment": ("面单状态", 120),
        }
        for key, (text, width) in headings.items():
            self.tree.heading(key, text=text)
            self.tree.column(key, width=width, minwidth=80, anchor="w")
        self.tree.grid(row=0, column=0, sticky="nsew")
        y_scroll = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        y_scroll.grid(row=0, column=1, sticky="ns")
        x_scroll = ttk.Scrollbar(table, orient="horizontal", command=self.tree.xview)
        x_scroll.grid(row=1, column=0, sticky="ew")
        self.tree.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)

    def refresh(self) -> None:
        self._reload_stores()
        self._load_store()

    def _reload_stores(self) -> None:
        stores = self.db.list_stores()
        self._store_lookup = {
            f"{store.name} · #{store.id}": store
            for store in stores
        }
        values = list(self._store_lookup)
        self.store_combo["values"] = values
        if self.store_var.get() not in values:
            self.store_var.set(values[0] if values else "")

    def _selected_store(self) -> Store | None:
        return self._store_lookup.get(self.store_var.get())

    def _load_store(self) -> None:
        store = self._selected_store()
        if store is None:
            self.config_summary_var.set("请先添加店铺")
            self.tree.delete(*self.tree.get_children())
            self._rows.clear()
            return
        config = self.db.get_shipping_config(store.id)
        accounts = self.db.list_shipping_accounts(store.id)
        templates = self.db.list_shipping_templates(store.id)
        if config:
            account = next(
                (
                    item
                    for item in accounts
                    if item["acct_id"] == config["default_acct_id"]
                ),
                None,
            )
            account_text = (
                f"{account['delivery_name']} / "
                f"{account['site_name'] or account['acct_id']}"
                if account
                else config["default_acct_id"]
            )
            self.config_summary_var.set(
                f"默认面单：{account_text} · {config['sender_name']} · "
                f"{config['sender_province']}{config['sender_city']}"
            )
        elif accounts:
            self.config_summary_var.set(
                f"已发现 {len(accounts)} 个面单账号、"
                f"{len(templates)} 个模板，请点击发货设置"
            )
        else:
            self.config_summary_var.set("尚无物流配置，请点击刷新物流配置")
        self._refresh_orders()

    def _refresh_orders(self) -> None:
        store = self._selected_store()
        if store is None:
            return
        rows = [
            row
            for row in self.db.list_orders(store_id=store.id, limit=100000)
            if row["status"] in {20, 21}
        ]
        self.tree.delete(*self.tree.get_children())
        self._rows.clear()
        for index, row in enumerate(rows):
            shipment = self.db.get_shipment(store.id, row["order_id"])
            iid = str(index)
            self._rows[iid] = row
            self.tree.insert(
                "",
                "end",
                iid=iid,
                values=(
                    row["order_id"],
                    row["status_text"],
                    row["buyer_name"],
                    row["phone"],
                    row["address"],
                    row["carrier"],
                    shipment["waybill_id"] if shipment else row["waybill_id"],
                    SHIPMENT_STATUS.get(shipment["status"], shipment["status"])
                    if shipment
                    else "未取号",
                ),
            )
        self.window.set_status(f"待发货或已发货订单 {len(rows)} 条")

    def _refresh_options(self) -> None:
        store = self._selected_store()
        if store is None:
            messagebox.showinfo("请选择店铺", "请先选择具体店铺。", parent=self)
            return
        self.window.set_status("正在刷新物流配置…", busy=True)

        def done(result) -> None:
            self.window.set_status(result.message)
            self._load_store()

        self.window.run_task(
            lambda progress: self.window.service.refresh_shipping_options(
                store,
                progress,
            ),
            done,
            lambda error: messagebox.showerror("物流配置刷新失败", error, parent=self),
            self._progress,
            log_title="刷新物流配置",
        )

    def _progress(self, message: str, current: int, total: int) -> None:
        self.window.set_status(message, busy=True)
        self.progress_var.set((current / total * 100) if total else 0)

    def _open_config(self) -> None:
        store = self._selected_store()
        if store is None:
            messagebox.showinfo("请选择店铺", "请先选择具体店铺。", parent=self)
            return
        accounts = self.db.list_shipping_accounts(store.id)
        templates = self.db.list_shipping_templates(store.id)
        if not accounts:
            messagebox.showinfo("没有面单账号", "请先刷新物流配置。", parent=self)
            return

        def save(values: dict) -> None:
            self.db.save_shipping_config(
                store.id,
                values["sender"],
                values["acct_id"],
                values["delivery_id"],
                values["template_id"],
            )
            self._load_store()
            self.window.set_status("发货设置已保存")

        ShippingConfigDialog(
            self,
            accounts,
            templates,
            self.db.get_shipping_config(store.id),
            save,
        )

    def _selected_order(self) -> dict[str, Any] | None:
        selection = self.tree.selection()
        return self._rows.get(selection[0]) if selection else None

    def _create_shipment(self) -> None:
        store = self._selected_store()
        row = self._selected_order()
        if store is None or row is None:
            messagebox.showinfo("请选择订单", "请选择一条待发货订单。", parent=self)
            return
        if row["status"] != 20:
            messagebox.showinfo("订单状态不适用", "当前只对“待发货”订单取号。", parent=self)
            return
        if not messagebox.askyesno(
            "确认生成电子面单",
            f"将为订单 {row['order_id']} 预取并正式取号。\n"
            "该操作可能扣除电子面单库存。是否继续？",
            parent=self,
        ):
            return
        self.window.set_status("正在生成电子面单…", busy=True)

        def done(shipment) -> None:
            self.window.set_status(f"面单已生成：{shipment['waybill_id']}")
            self._refresh_orders()
            ShipmentDialog(
                self,
                shipment,
                lambda: self._complete_shipment(row["order_id"]),
            )

        self.window.run_task(
            lambda _progress: self.window.service.create_shipment(
                store,
                row["order_id"],
            ),
            done,
            lambda error: messagebox.showerror(
                "电子面单生成失败",
                error,
                parent=self,
            ),
            log_title="生成电子面单",
        )

    def _open_shipment(self) -> None:
        store = self._selected_store()
        row = self._selected_order()
        if store is None or row is None:
            messagebox.showinfo("请选择订单", "请选择一条已取号订单。", parent=self)
            return
        shipment = self.db.get_shipment(store.id, row["order_id"])
        if shipment is None:
            messagebox.showinfo("没有面单", "该订单尚未生成电子面单。", parent=self)
            return
        ShipmentDialog(
            self,
            shipment,
            lambda: self._complete_shipment(row["order_id"]),
        )

    def _complete_shipment(self, order_id: str) -> None:
        store = self._selected_store()
        if store is None:
            return
        self.window.set_status("正在提交发货状态…", busy=True)

        def done(_shipment) -> None:
            self.window.set_status("订单已发货")
            self._refresh_orders()
            messagebox.showinfo("发货完成", "平台已收到发货状态。", parent=self)

        self.window.run_task(
            lambda _progress: self.window.service.complete_shipment(
                store,
                order_id,
            ),
            done,
            lambda error: messagebox.showerror("发货失败", error, parent=self),
            log_title="提交订单发货",
        )


class SettingsPage(BasePage):
    title = "应用设置与说明"
    subtitle = "官方接口、本地优先的数据管理和可控线上操作"

    def __init__(self, parent: tk.Widget, window: "MainWindow"):
        super().__init__(parent, window)
        self.columnconfigure(0, weight=1)
        panel = ttk.Frame(self, style="Panel.TFrame", padding=24)
        panel.grid(row=0, column=0, sticky="nsew")
        self.rowconfigure(0, weight=1)
        ttk.Label(panel, text="当前版本能力", style="Section.TLabel").pack(anchor="w")
        items = [
            "多店铺配置、编辑、删除和连接验证",
            "使用 Windows DPAPI 加密保存 AppSecret",
            "官方稳定版 access_token 自动缓存",
            "订单列表和详情同步、本地 SQLite 查询、XLSX/CSV 导出",
            "商品列表和详情同步、本地查询、XLSX 导出",
            "演示数据模式用于离线验证",
        ]
        for item in items:
            ttk.Label(panel, text=f"•  {item}", style="Panel.TLabel", foreground=TEXT).pack(
                anchor="w", pady=4
            )
        ttk.Separator(panel).pack(fill="x", pady=16)
        ttk.Label(
            panel,
            text="多店铺自动检查",
            style="Section.TLabel",
        ).pack(anchor="w")
        scheduler = ttk.Frame(panel, style="Panel.TFrame")
        scheduler.pack(anchor="w", fill="x", pady=(8, 4))
        ttk.Label(
            scheduler,
            text="每 60 秒轮转检查",
            style="Panel.TLabel",
            foreground=TEXT,
        ).pack(side="left")
        self.auto_batch_var = tk.StringVar(
            value=str(self.window._auto_refresh_batch_size)
        )
        ttk.Combobox(
            scheduler,
            textvariable=self.auto_batch_var,
            state="readonly",
            width=8,
            values=("10", "20", "50", "100", "200", "500"),
        ).pack(side="left", padx=8)
        ttk.Label(
            scheduler,
            text="家店铺 / 批",
            style="Panel.TLabel",
            foreground=MUTED,
        ).pack(side="left")
        button(
            scheduler,
            "保存",
            self._save_auto_refresh_size,
            "Primary",
        ).pack(side="left", padx=12)
        ttk.Label(
            panel,
            text=(
                "店铺很多时不会在单分钟内强行检查全部店铺，避免卡死和平台限流；"
                "页面先显示本地缓存，自动任务按批次循环更新。"
            ),
            style="Panel.TLabel",
            foreground=MUTED,
            wraplength=900,
            justify="left",
        ).pack(anchor="w", pady=(0, 8))
        ttk.Separator(panel).pack(fill="x", pady=16)
        ttk.Label(panel, text="自动更新", style="Section.TLabel").pack(
            anchor="w"
        )
        update_row = ttk.Frame(panel, style="Panel.TFrame")
        update_row.pack(fill="x", pady=(8, 4))
        self.update_source_var = tk.StringVar(
            value=self.db.get_setting(
                "update_source",
                DEFAULT_UPDATE_SOURCE,
            )
        )
        ttk.Label(
            update_row,
            text="更新清单地址",
            style="Panel.TLabel",
        ).pack(side="left")
        ttk.Entry(
            update_row,
            textvariable=self.update_source_var,
            width=70,
        ).pack(side="left", padx=8, fill="x", expand=True)
        self.update_auto_var = tk.BooleanVar(
            value=self.db.get_setting("update_auto_check", "1") == "1"
        )
        tk.Checkbutton(
            update_row,
            text="启动时检查",
            variable=self.update_auto_var,
            bg=PANEL,
            fg=TEXT,
            activebackground=PANEL,
            selectcolor="white",
            bd=0,
        ).pack(side="left", padx=8)
        button(
            update_row,
            "保存并检查",
            self._save_update_settings,
            "Primary",
        ).pack(side="left")
        ttk.Label(
            panel,
            text=(
                "支持 HTTP/HTTPS 更新清单、局域网共享目录或本机更新的 JSON 文件。"
                "点击升级后软件会自动下载、校验、关闭、替换并重新启动；"
                "业务数据始终保存在独立目录。"
            ),
            style="Panel.TLabel",
            foreground=MUTED,
            wraplength=900,
            justify="left",
        ).pack(anchor="w", pady=(0, 8))
        ttk.Separator(panel).pack(fill="x", pady=16)
        ttk.Label(panel, text="接口与安全", style="Section.TLabel").pack(anchor="w")
        notes = [
            "默认直连微信 API；设置环境变量 WEIMI_NETWORK_MODE=system 可改用系统代理。",
            "AppSecret 不会写入日志，也不会进入 Git 版本库。",
            "微信接口可能要求将本机公网 IP 加入开放平台白名单。",
            "电子面单和物流打印硬件仍需外部账号与设备；淘宝/1688采购使用本地浏览器登录。",
            "上货发布支持本地模板、批次、任务队列和微信商品 JSON 提交。",
        ]
        for note in notes:
            ttk.Label(
                panel,
                text=note,
                style="Panel.TLabel",
                foreground=MUTED,
                wraplength=900,
                justify="left",
            ).pack(anchor="w", pady=4)

    def _save_auto_refresh_size(self) -> None:
        try:
            batch_size = int(self.auto_batch_var.get())
        except ValueError:
            batch_size = 50
        batch_size = min(500, max(5, batch_size))
        self.window._auto_refresh_batch_size = batch_size
        self.db.set_setting(
            "auto_refresh_batch_size",
            str(batch_size),
        )
        self.auto_batch_var.set(str(batch_size))
        self.window.set_status(
            f"自动检查已设置为每分钟最多检查 {batch_size} 家店铺"
        )

    def _save_update_settings(self) -> None:
        source = self.update_source_var.get().strip()
        if not source.startswith(("https://", "http://")):
            messagebox.showwarning(
                "更新地址",
                "更新清单必须使用 HTTP 或 HTTPS 地址。",
                parent=self,
            )
            return
        self.db.set_setting("update_source", source)
        self.db.set_setting(
            "update_auto_check",
            "1" if self.update_auto_var.get() else "0",
        )
        self.db.set_setting("skipped_update_version", "")
        self.window.set_status("自动更新设置已保存")
        self.window._check_updates(manual=True)


def _format_datetime_from_iso(value: str | None) -> str:
    if not value:
        return "—"
    return value[:10]
