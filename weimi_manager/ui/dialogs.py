from __future__ import annotations

import json
import tkinter as tk
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any, Callable

from ..models import SHIPMENT_STATUS, Store
from .theme import BG, BLUE, MUTED, PANEL, TEXT, button


class StoreDialog(tk.Toplevel):
    def __init__(
        self,
        parent: tk.Widget,
        on_save: Callable[[dict[str, str]], Any],
        store: Store | None = None,
    ):
        super().__init__(parent)
        self.on_save = on_save
        self.store = store
        self.title("编辑店铺" if store else "添加店铺")
        self.configure(bg=BG)
        self.resizable(False, False)
        self.transient(parent.winfo_toplevel())
        self.grab_set()

        self.name_var = tk.StringVar(value=store.name if store else "")
        self.app_id_var = tk.StringVar(value=store.app_id if store else "")
        self.group_var = tk.StringVar(value=store.group_name if store else "")
        self.secret_var = tk.StringVar()
        self.web_only_var = tk.BooleanVar(value=False)

        panel = ttk.Frame(self, style="Panel.TFrame", padding=24)
        panel.grid(sticky="nsew")
        panel.columnconfigure(1, weight=1)

        ttk.Label(panel, text=self.title(), style="Section.TLabel").grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 18)
        )
        self._field(
            panel,
            1,
            "店铺名称" if store else "店铺名称（可留空）",
            self.name_var,
        )
        self._field(panel, 2, "AppID", self.app_id_var, state="disabled" if store else "normal")
        self._field(panel, 3, "分组名称", self.group_var)
        self._field(
            panel,
            4,
            "AppSecret",
            self.secret_var,
            show="●",
        )
        if not store:
            ttk.Checkbutton(
                panel,
                text="仅网页登录（不填写 AppSecret）",
                variable=self.web_only_var,
            ).grid(
                row=5,
                column=0,
                columnspan=2,
                sticky="w",
                pady=(2, 0),
            )

        help_text = (
            "编辑店铺时留空 AppSecret 表示不修改原密钥。"
            if store
            else "仅网页登录无需 AppSecret；接口密钥使用 Windows DPAPI 加密保存在本机。"
        )
        ttk.Label(
            panel,
            text=help_text,
            style="Panel.TLabel",
            foreground=MUTED,
        ).grid(
            row=6,
            column=0,
            columnspan=2,
            sticky="w",
            pady=(4, 18),
        )

        actions = ttk.Frame(panel, style="Panel.TFrame")
        actions.grid(row=7, column=0, columnspan=2, sticky="e")
        button(actions, "取消", self.destroy).pack(side="left", padx=(0, 8))
        button(actions, "保存", self._save, "Primary").pack(side="left")

        self.bind("<Return>", lambda _event: self._save())
        self.bind("<Escape>", lambda _event: self.destroy())
        self.update_idletasks()
        self._center(parent)

    def _field(
        self,
        parent: ttk.Frame,
        row: int,
        label: str,
        variable: tk.StringVar,
        state: str = "normal",
        show: str = "",
    ) -> None:
        ttk.Label(parent, text=label, style="Panel.TLabel").grid(
            row=row, column=0, sticky="w", padx=(0, 16), pady=6
        )
        entry = ttk.Entry(parent, textvariable=variable, width=38, show=show, state=state)
        entry.grid(row=row, column=1, sticky="ew", pady=6)
        if row == 1:
            self.after(100, entry.focus_set)

    def _save(self) -> None:
        values = {
            "name": self.name_var.get().strip(),
            "app_id": self.app_id_var.get().strip(),
            "group_name": self.group_var.get().strip(),
            "secret": self.secret_var.get().strip(),
            "web_only": "1" if self.web_only_var.get() else "0",
        }
        if not values["app_id"]:
            messagebox.showwarning("信息不完整", "AppID 不能为空。", parent=self)
            return
        if self.store and not values["name"]:
            messagebox.showwarning("信息不完整", "店铺名称不能为空。", parent=self)
            return
        if not self.store:
            if values["web_only"] == "1" and values["secret"]:
                messagebox.showwarning(
                    "信息冲突",
                    "仅网页登录模式不能同时填写 AppSecret。",
                    parent=self,
                )
                return
            if values["web_only"] != "1" and not values["secret"]:
                messagebox.showwarning(
                    "信息不完整",
                    "请填写 AppSecret，或勾选“仅网页登录”。",
                    parent=self,
                )
                return
        try:
            result = self.on_save(values)
        except Exception as exc:
            messagebox.showerror(
                "保存失败",
                str(exc),
                parent=self,
            )
            return
        if result is False:
            return
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()

    def _center(self, parent: tk.Widget) -> None:
        root = parent.winfo_toplevel()
        x = root.winfo_rootx() + max(0, (root.winfo_width() - self.winfo_width()) // 2)
        y = root.winfo_rooty() + max(0, (root.winfo_height() - self.winfo_height()) // 2)
        self.geometry(f"+{x}+{y}")


class OrderDetailDialog(tk.Toplevel):
    def __init__(self, parent: tk.Widget, order: dict):
        super().__init__(parent)
        self.title(f"订单详情 · {order['order_id']}")
        self.configure(bg=BG)
        self.geometry("820x620")
        self.minsize(680, 500)
        self.transient(parent.winfo_toplevel())

        wrapper = ttk.Frame(self, style="Panel.TFrame", padding=20)
        wrapper.pack(fill="both", expand=True, padx=16, pady=16)
        wrapper.rowconfigure(1, weight=1)
        wrapper.columnconfigure(0, weight=1)

        title = ttk.Label(wrapper, text=f"订单 {order['order_id']}", style="Section.TLabel")
        title.grid(row=0, column=0, sticky="w", pady=(0, 12))

        text = tk.Text(
            wrapper,
            wrap="word",
            bg="#fbfcfe",
            fg=TEXT,
            relief="solid",
            bd=1,
            padx=14,
            pady=12,
            font=("Microsoft YaHei UI", 10),
        )
        text.grid(row=1, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(wrapper, orient="vertical", command=text.yview)
        scrollbar.grid(row=1, column=1, sticky="ns")
        text.configure(yscrollcommand=scrollbar.set)

        lines = [
            f"店铺：{order['store_name']}",
            f"状态：{order['status_text']}",
            f"买家：{order['buyer_name']}",
            f"电话：{order['phone']}",
            f"地址：{order['address']}",
            f"运单号：{order['waybill_id'] or '—'}",
            f"快递公司：{order['carrier'] or '—'}",
            f"商家备注：{order['merchant_notes'] or '—'}",
            "",
            "商品明细",
            "-" * 64,
        ]
        for item in order["items"]:
            lines.append(
                f"{item['title']} × {item['sku_count']}  "
                f"{item['sale_price'] / 100:.2f} 元  SKU: {item['sku_id']}"
            )
        text.insert("1.0", "\n".join(lines))
        text.configure(state="disabled")
        button(wrapper, "关闭", self.destroy, "Primary").grid(
            row=2, column=0, columnspan=2, sticky="e", pady=(12, 0)
        )


class ProductDetailDialog(tk.Toplevel):
    def __init__(
        self,
        parent: tk.Widget,
        product: dict,
        on_adjust_stock: Callable[[str, int, int], None] | None = None,
    ):
        super().__init__(parent)
        self.title(f"商品详情 · {product['product_id']}")
        self.configure(bg=BG)
        self.geometry("680x460")
        self.transient(parent.winfo_toplevel())

        frame = ttk.Frame(self, style="Panel.TFrame", padding=22)
        frame.pack(fill="both", expand=True, padx=16, pady=16)
        fields = [
            ("商品名称", product["title"]),
            ("店铺", product["store_name"]),
            ("商品 ID", product["product_id"]),
            ("线上状态", product["status_text"]),
            ("最低售价", f"{product['min_price'] / 100:.2f} 元"),
            ("SKU 数量", str(product["sku_count"])),
            ("总库存", str(product["stock_total"])),
            ("商品编码", product["spu_code"] or "—"),
            ("外部商品 ID", product["out_product_id"] or "—"),
        ]
        for row, (label, value) in enumerate(fields):
            ttk.Label(frame, text=label, style="Panel.TLabel", foreground=MUTED).grid(
                row=row, column=0, sticky="nw", padx=(0, 24), pady=7
            )
            ttk.Label(frame, text=value, style="Panel.TLabel", wraplength=430).grid(
                row=row, column=1, sticky="w", pady=7
            )
        actions = ttk.Frame(frame, style="Panel.TFrame")
        actions.grid(row=len(fields), column=0, columnspan=2, sticky="e", pady=(18, 0))
        if on_adjust_stock:
            button(
                actions,
                "调整库存",
                lambda: StockDialog(self, product, on_adjust_stock),
            ).pack(side="left", padx=(0, 8))
        button(actions, "关闭", self.destroy, "Primary").pack(side="left")


class StockDialog(tk.Toplevel):
    def __init__(
        self,
        parent: tk.Widget,
        product: dict,
        on_apply: Callable[[str, int, int], None],
    ):
        super().__init__(parent)
        self.title(f"调整库存 · {product['title']}")
        self.configure(bg=BG)
        self.geometry("700x480")
        self.minsize(620, 420)
        self.transient(parent.winfo_toplevel())
        self.grab_set()
        self.on_apply = on_apply

        try:
            payload = json.loads(product.get("raw_json") or "{}")
        except json.JSONDecodeError:
            payload = {}
        skus = [item for item in payload.get("skus") or [] if isinstance(item, dict)]

        frame = ttk.Frame(self, style="Panel.TFrame", padding=20)
        frame.pack(fill="both", expand=True, padx=16, pady=16)
        frame.rowconfigure(1, weight=1)
        frame.columnconfigure(0, weight=1)
        ttk.Label(frame, text="SKU 与当前库存", style="Section.TLabel").grid(
            row=0, column=0, sticky="w", pady=(0, 10)
        )

        columns = ("sku_id", "code", "price", "stock")
        self.tree = ttk.Treeview(frame, columns=columns, show="headings", height=10)
        for key, text, width in (
            ("sku_id", "SKU ID", 170),
            ("code", "SKU 编码", 160),
            ("price", "售价", 110),
            ("stock", "当前库存", 110),
        ):
            self.tree.heading(key, text=text)
            self.tree.column(key, width=width, anchor="w")
        self.tree.grid(row=1, column=0, columnspan=2, sticky="nsew")
        self.tree.bind("<<TreeviewSelect>>", self._sync_values)
        for sku in skus:
            self.tree.insert(
                "",
                "end",
                values=(
                    sku.get("sku_id", ""),
                    sku.get("sku_code", ""),
                    f"¥{int(sku.get('sale_price') or 0) / 100:.2f}",
                    sku.get("stock_num", 0),
                ),
            )
        if not skus:
            ttk.Label(
                frame,
                text="该商品缓存中没有 SKU 数据，请先重新同步商品详情。",
                style="Panel.TLabel",
                foreground=MUTED,
            ).grid(row=2, column=0, columnspan=2, sticky="w", pady=10)

        controls = ttk.Frame(frame, style="Panel.TFrame")
        controls.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(14, 0))
        ttk.Label(controls, text="方式", style="Panel.TLabel").pack(side="left")
        self.mode_var = tk.StringVar(value="增加库存")
        mode = ttk.Combobox(
            controls,
            textvariable=self.mode_var,
            values=["增加库存", "减少库存"],
            state="readonly",
            width=12,
        )
        mode.pack(side="left", padx=(6, 12))
        ttk.Label(controls, text="数量", style="Panel.TLabel").pack(side="left")
        self.amount_var = tk.StringVar(value="1")
        ttk.Entry(controls, textvariable=self.amount_var, width=10).pack(side="left", padx=(6, 12))
        button(controls, "应用", self._apply, "Primary").pack(side="right")
        button(controls, "关闭", self.destroy).pack(side="right", padx=(0, 8))

    def _sync_values(self, _event=None) -> None:
        pass

    def _apply(self) -> None:
        selection = self.tree.selection()
        if not selection:
            messagebox.showinfo("请选择 SKU", "请先选择一个 SKU。", parent=self)
            return
        try:
            amount = int(self.amount_var.get().strip())
        except ValueError:
            messagebox.showwarning("数量错误", "库存调整数量必须是整数。", parent=self)
            return
        if amount <= 0:
            messagebox.showwarning("数量错误", "库存调整数量必须大于 0。", parent=self)
            return
        values = self.tree.item(selection[0], "values")
        sku_id = str(values[0])
        diff_type = 1 if self.mode_var.get() == "增加库存" else 2
        action = self.mode_var.get()
        if not messagebox.askyesno(
            "确认调整库存",
            f"将 {action} {amount} 件（SKU {sku_id}）。\n"
            "该操作会立即影响线上库存，是否继续？",
            parent=self,
        ):
            return
        self.on_apply(sku_id, diff_type, amount)
        self.destroy()


class ProductOperationsDialog(tk.Toplevel):
    def __init__(self, parent: tk.Widget, rows: list[dict]):
        super().__init__(parent)
        self.title("商品操作记录")
        self.configure(bg=BG)
        self.geometry("860x520")
        self.minsize(680, 420)
        self.transient(parent.winfo_toplevel())

        frame = ttk.Frame(self, style="Panel.TFrame", padding=20)
        frame.pack(fill="both", expand=True, padx=16, pady=16)
        frame.rowconfigure(1, weight=1)
        frame.columnconfigure(0, weight=1)
        ttk.Label(frame, text="最近操作记录", style="Section.TLabel").grid(
            row=0, column=0, sticky="w", pady=(0, 10)
        )
        columns = ("time", "product_id", "action", "status", "error")
        tree = ttk.Treeview(frame, columns=columns, show="headings")
        labels = {
            "time": ("时间", 165),
            "product_id": ("商品 ID", 180),
            "action": ("动作", 100),
            "status": ("结果", 90),
            "error": ("失败原因", 360),
        }
        for key, (text, width) in labels.items():
            tree.heading(key, text=text)
            tree.column(key, width=width, anchor="w")
        tree.grid(row=1, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        scroll.grid(row=1, column=1, sticky="ns")
        tree.configure(yscrollcommand=scroll.set)
        action_labels = {
            "listing": "上架",
            "delisting": "下架",
            "stock": "库存",
        }
        for row in rows:
            tree.insert(
                "",
                "end",
                values=(
                    str(row["created_at"])[:19].replace("T", " "),
                    row["product_id"],
                    action_labels.get(row["action"], row["action"]),
                    "成功" if row["status"] == "success" else "失败",
                    row["error"],
                ),
            )
        button(frame, "关闭", self.destroy, "Primary").grid(
            row=2, column=0, columnspan=2, sticky="e", pady=(12, 0)
        )


class ShippingConfigDialog(tk.Toplevel):
    def __init__(
        self,
        parent: tk.Widget,
        accounts: list[dict],
        templates: list[dict],
        config: dict | None,
        on_save: Callable[[dict], None],
    ):
        super().__init__(parent)
        self.title("电子面单设置")
        self.configure(bg=BG)
        self.resizable(False, False)
        self.transient(parent.winfo_toplevel())
        self.grab_set()
        self.on_save = on_save
        config = config or {}

        panel = ttk.Frame(self, style="Panel.TFrame", padding=24)
        panel.grid(sticky="nsew")
        panel.columnconfigure(1, weight=1)
        ttk.Label(panel, text="发货人与默认面单", style="Section.TLabel").grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 16)
        )
        self.account_var = tk.StringVar()
        self.template_var = tk.StringVar()
        self.account_lookup: dict[str, dict] = {}
        account_values = []
        for account in accounts:
            label = (
                f"{account.get('delivery_name') or account.get('delivery_id')} · "
                f"{account.get('site_name') or '默认网点'} · {account['acct_id']}"
            )
            self.account_lookup[label] = account
            account_values.append(label)
        self.template_lookup: dict[str, dict] = {}
        template_values = ["不使用指定模板（标准一联单）"]
        for template in templates:
            label = (
                f"{template.get('delivery_id')} · {template.get('template_name')} "
                f"{'（默认）' if template.get('is_default') else ''}"
            )
            self.template_lookup[label] = template
            template_values.append(label)
        selected_account = next(
            (
                label
                for label, value in self.account_lookup.items()
                if value["acct_id"] == config.get("default_acct_id")
            ),
            account_values[0] if account_values else "",
        )
        selected_template = next(
            (
                label
                for label, value in self.template_lookup.items()
                if value["template_id"] == config.get("default_template_id")
            ),
            template_values[0],
        )
        self.account_var.set(selected_account)
        self.template_var.set(selected_template)

        self._combo_field(panel, 1, "面单账号", self.account_var, account_values)
        self._combo_field(panel, 2, "面单模板", self.template_var, template_values)
        self.fields = {
            "name": tk.StringVar(value=str(config.get("sender_name") or "")),
            "mobile": tk.StringVar(value=str(config.get("sender_mobile") or "")),
            "province": tk.StringVar(value=str(config.get("sender_province") or "")),
            "city": tk.StringVar(value=str(config.get("sender_city") or "")),
            "county": tk.StringVar(value=str(config.get("sender_county") or "")),
            "street": tk.StringVar(value=str(config.get("sender_street") or "")),
            "address": tk.StringVar(value=str(config.get("sender_address") or "")),
        }
        labels = {
            "name": "寄件人",
            "mobile": "联系电话",
            "province": "省份",
            "city": "城市",
            "county": "区县",
            "street": "街道",
            "address": "详细地址",
        }
        for row, key in enumerate(self.fields, 4):
            ttk.Label(panel, text=labels[key], style="Panel.TLabel").grid(
                row=row, column=0, sticky="w", padx=(0, 14), pady=5
            )
            ttk.Entry(panel, textvariable=self.fields[key], width=48).grid(
                row=row, column=1, sticky="ew", pady=5
            )
        ttk.Button(panel, text="使用账号默认地址", command=self._fill_defaults).grid(
            row=3, column=1, sticky="w", pady=(4, 8)
        )
        actions = ttk.Frame(panel, style="Panel.TFrame")
        actions.grid(row=12, column=0, columnspan=2, sticky="e", pady=(18, 0))
        button(actions, "取消", self.destroy).pack(side="left", padx=(0, 8))
        button(actions, "保存设置", self._save, "Primary").pack(side="left")
        self.bind("<Escape>", lambda _event: self.destroy())
        self.update_idletasks()
        self._center(parent)

    def _combo_field(
        self,
        parent: ttk.Frame,
        row: int,
        label: str,
        variable: tk.StringVar,
        values: list[str],
    ) -> None:
        ttk.Label(parent, text=label, style="Panel.TLabel").grid(
            row=row, column=0, sticky="w", padx=(0, 14), pady=5
        )
        ttk.Combobox(
            parent,
            textvariable=variable,
            values=values,
            state="readonly",
            width=46,
        ).grid(row=row, column=1, sticky="ew", pady=5)

    def _fill_defaults(self) -> None:
        account = self.account_lookup.get(self.account_var.get())
        if not account:
            return
        self.fields["name"].set(account.get("sender_name") or "")
        self.fields["mobile"].set(account.get("sender_mobile") or "")
        try:
            address = json.loads(account.get("sender_address_json") or "{}")
        except json.JSONDecodeError:
            address = {}
        for source, target in (
            ("province", "province"),
            ("city", "city"),
            ("county", "county"),
            ("street", "street"),
            ("address", "address"),
        ):
            self.fields[target].set(str(address.get(source) or ""))

    def _save(self) -> None:
        account = self.account_lookup.get(self.account_var.get())
        if account is None:
            messagebox.showwarning("缺少面单账号", "请先刷新物流配置并选择面单账号。", parent=self)
            return
        template = self.template_lookup.get(self.template_var.get())
        sender = {key: value.get().strip() for key, value in self.fields.items()}
        required = ("name", "mobile", "province", "city", "county", "street", "address")
        if any(not sender[key] for key in required):
            messagebox.showwarning("地址不完整", "请填写寄件人、电话和完整地址。", parent=self)
            return
        self.on_save(
            {
                "sender": sender,
                "acct_id": account["acct_id"],
                "delivery_id": account["delivery_id"],
                "template_id": template["template_id"] if template else "",
            }
        )
        self.destroy()

    def _center(self, parent: tk.Widget) -> None:
        root = parent.winfo_toplevel()
        x = root.winfo_rootx() + max(0, (root.winfo_width() - self.winfo_width()) // 2)
        y = root.winfo_rooty() + max(0, (root.winfo_height() - self.winfo_height()) // 2)
        self.geometry(f"+{x}+{y}")


class CookieImportDialog(tk.Toplevel):
    def __init__(
        self,
        parent: tk.Widget,
        store_name: str,
        on_save: Callable[[str], None],
    ):
        super().__init__(parent)
        self.title(f"导入网页登录 · {store_name}")
        self.configure(bg=BG)
        self.geometry("760x390")
        self.transient(parent.winfo_toplevel())
        self.grab_set()
        self.on_save = on_save

        frame = ttk.Frame(self, style="Panel.TFrame", padding=22)
        frame.pack(fill="both", expand=True, padx=16, pady=16)
        frame.rowconfigure(2, weight=1)
        frame.columnconfigure(0, weight=1)
        ttk.Label(frame, text="粘贴微信小店后台 Cookie", style="Section.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(
            frame,
            text="格式示例：name1=value1; name2=value2。内容只在点击保存后加密写入本机。",
            style="Panel.TLabel",
            foreground=MUTED,
        ).grid(row=1, column=0, sticky="w", pady=(5, 10))
        self.text = tk.Text(
            frame,
            wrap="word",
            height=10,
            bg="#fbfcfe",
            fg=TEXT,
            relief="solid",
            bd=1,
            padx=10,
            pady=8,
            font=("Consolas", 9),
        )
        self.text.grid(row=2, column=0, sticky="nsew")
        actions = ttk.Frame(frame, style="Panel.TFrame")
        actions.grid(row=3, column=0, sticky="e", pady=(12, 0))
        button(actions, "取消", self.destroy).pack(side="left", padx=(0, 8))
        button(actions, "加密保存", self._save, "Primary").pack(side="left")
        self.bind("<Escape>", lambda _event: self.destroy())

    def _save(self) -> None:
        value = self.text.get("1.0", "end").strip()
        if not value:
            messagebox.showwarning("Cookie 为空", "请粘贴有效的 Cookie。", parent=self)
            return
        self.on_save(value)
        self.destroy()


class OrderNotesDialog(tk.Toplevel):
    def __init__(self, parent: tk.Widget, current: str, on_save: Callable[[str, int], None]):
        super().__init__(parent)
        self.title("修改订单备注")
        self.configure(bg=BG)
        self.geometry("560x300")
        self.transient(parent.winfo_toplevel())
        self.grab_set()
        self.on_save = on_save
        frame = ttk.Frame(self, style="Panel.TFrame", padding=22)
        frame.pack(fill="both", expand=True, padx=16, pady=16)
        frame.rowconfigure(2, weight=1)
        frame.columnconfigure(0, weight=1)
        ttk.Label(frame, text="商家备注", style="Section.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        self.text = tk.Text(
            frame,
            wrap="word",
            height=7,
            bg="#fbfcfe",
            fg=TEXT,
            relief="solid",
            bd=1,
            padx=10,
            pady=8,
            font=("Microsoft YaHei UI", 9),
        )
        self.text.grid(row=1, column=0, sticky="nsew", pady=10)
        self.text.insert("1.0", current)
        color_row = ttk.Frame(frame, style="Panel.TFrame")
        color_row.grid(row=2, column=0, sticky="w")
        ttk.Label(color_row, text="标签颜色", style="Panel.TLabel").pack(side="left")
        self.color_var = tk.StringVar(value="灰色")
        ttk.Combobox(
            color_row,
            textvariable=self.color_var,
            values=["灰色", "红色", "橙色", "绿色", "蓝色"],
            state="readonly",
            width=10,
        ).pack(side="left", padx=8)
        actions = ttk.Frame(frame, style="Panel.TFrame")
        actions.grid(row=3, column=0, sticky="e", pady=(12, 0))
        button(actions, "取消", self.destroy).pack(side="left", padx=(0, 8))
        button(actions, "保存", self._save, "Primary").pack(side="left")

    def _save(self) -> None:
        colors = {"灰色": 0, "红色": 1, "橙色": 2, "绿色": 3, "蓝色": 4}
        self.on_save(
            self.text.get("1.0", "end").strip(),
            colors.get(self.color_var.get(), 0),
        )
        self.destroy()


class OrderPriceDialog(tk.Toplevel):
    def __init__(self, parent: tk.Widget, order, on_save: Callable[[list, bool, int], None]):
        super().__init__(parent)
        self.title(f"修改订单价格 · {order.order_id}")
        self.configure(bg=BG)
        self.geometry("700x450")
        self.transient(parent.winfo_toplevel())
        self.grab_set()
        self.order = order
        self.on_save = on_save
        frame = ttk.Frame(self, style="Panel.TFrame", padding=20)
        frame.pack(fill="both", expand=True, padx=16, pady=16)
        frame.rowconfigure(1, weight=1)
        frame.columnconfigure(0, weight=1)
        ttk.Label(
            frame,
            text="每行填写该商品修改后的实际成交总金额",
            style="Section.TLabel",
        ).grid(row=0, column=0, sticky="w", pady=(0, 10))
        body = ttk.Frame(frame, style="Panel.TFrame")
        body.grid(row=1, column=0, sticky="nsew")
        body.columnconfigure(1, weight=1)
        self.price_vars = []
        for index, item in enumerate(order.items):
            ttk.Label(
                body,
                text=f"{item.title} ×{item.sku_count}",
                style="Panel.TLabel",
            ).grid(row=index, column=0, sticky="w", pady=5)
            variable = tk.StringVar(
                value=f"{item.sale_price * item.sku_count / 100:.2f}"
            )
            ttk.Entry(body, textvariable=variable, width=16).grid(
                row=index,
                column=1,
                sticky="e",
                pady=5,
            )
            self.price_vars.append(variable)
        self.express_var = tk.BooleanVar(value=False)
        express_row = ttk.Frame(frame, style="Panel.TFrame")
        express_row.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        ttk.Checkbutton(
            express_row,
            text="同时修改运费",
            variable=self.express_var,
        ).pack(side="left")
        self.express_fee_var = tk.StringVar(value="0.00")
        ttk.Entry(express_row, textvariable=self.express_fee_var, width=12).pack(
            side="left", padx=8
        )
        ttk.Label(express_row, text="元", style="Panel.TLabel").pack(side="left")
        actions = ttk.Frame(frame, style="Panel.TFrame")
        actions.grid(row=3, column=0, sticky="e", pady=(14, 0))
        button(actions, "取消", self.destroy).pack(side="left", padx=(0, 8))
        button(actions, "提交改价", self._save, "Primary").pack(side="left")

    def _save(self) -> None:
        try:
            changes = []
            for item, variable in zip(self.order.items, self.price_vars):
                changes.append(
                    {
                        "product_id": item.product_id,
                        "sku_id": item.sku_id,
                        "change_price": int(round(float(variable.get()) * 100)),
                    }
                )
            express_fee = int(round(float(self.express_fee_var.get()) * 100))
        except ValueError:
            messagebox.showwarning("金额格式", "价格必须填写数字。", parent=self)
            return
        self.on_save(changes, self.express_var.get(), express_fee)
        self.destroy()


class OrderAddressDialog(tk.Toplevel):
    FIELDS = (
        ("user_name", "收货人"),
        ("tel_number", "联系电话"),
        ("province_name", "省份"),
        ("city_name", "城市"),
        ("county_name", "区县"),
        ("detail_info", "详细地址"),
    )

    def __init__(self, parent: tk.Widget, address: dict, on_save: Callable[[dict], None]):
        super().__init__(parent)
        self.title("申请修改收货地址")
        self.configure(bg=BG)
        self.transient(parent.winfo_toplevel())
        self.grab_set()
        self.on_save = on_save
        frame = ttk.Frame(self, style="Panel.TFrame", padding=22)
        frame.pack(fill="both", expand=True, padx=16, pady=16)
        frame.columnconfigure(1, weight=1)
        ttk.Label(
            frame,
            text="提交后需要买家同意，平台不会立即修改成功",
            style="Panel.TLabel",
            foreground=MUTED,
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 10))
        self.vars = {}
        for row, (key, label) in enumerate(self.FIELDS, 1):
            ttk.Label(frame, text=label, style="Panel.TLabel").grid(
                row=row, column=0, sticky="w", padx=(0, 14), pady=5
            )
            variable = tk.StringVar(value=str(address.get(key) or ""))
            ttk.Entry(frame, textvariable=variable, width=42).grid(
                row=row, column=1, sticky="ew", pady=5
            )
            self.vars[key] = variable
        actions = ttk.Frame(frame, style="Panel.TFrame")
        actions.grid(row=7, column=0, columnspan=2, sticky="e", pady=(14, 0))
        button(actions, "取消", self.destroy).pack(side="left", padx=(0, 8))
        button(actions, "提交改址申请", self._save, "Primary").pack(side="left")

    def _save(self) -> None:
        values = {key: variable.get().strip() for key, variable in self.vars.items()}
        if not all(values[key] for key in ("user_name", "tel_number", "province_name", "city_name", "detail_info")):
            messagebox.showwarning("地址不完整", "请填写联系人和完整地址。", parent=self)
            return
        self.on_save(values)
        self.destroy()


class SimpleFieldsDialog(tk.Toplevel):
    def __init__(
        self,
        parent: tk.Widget,
        title: str,
        fields: tuple[tuple[str, str], ...],
        on_save: Callable[[dict], None],
        initial: dict[str, str] | None = None,
    ):
        super().__init__(parent)
        self.title(title)
        self.configure(bg=BG)
        self.transient(parent.winfo_toplevel())
        self.grab_set()
        self.on_save = on_save
        frame = ttk.Frame(self, style="Panel.TFrame", padding=22)
        frame.pack(fill="both", expand=True, padx=16, pady=16)
        frame.columnconfigure(1, weight=1)
        self.vars = {}
        initial = initial or {}
        for row, (key, label) in enumerate(fields):
            ttk.Label(frame, text=label, style="Panel.TLabel").grid(
                row=row, column=0, sticky="w", padx=(0, 14), pady=6
            )
            variable = tk.StringVar()
            variable.set(str(initial.get(key, "")))
            ttk.Entry(frame, textvariable=variable, width=38).grid(
                row=row, column=1, sticky="ew", pady=6
            )
            self.vars[key] = variable
        actions = ttk.Frame(frame, style="Panel.TFrame")
        actions.grid(row=len(fields), column=0, columnspan=2, sticky="e", pady=(14, 0))
        button(actions, "取消", self.destroy).pack(side="left", padx=(0, 8))
        button(actions, "确认提交", self._save, "Primary").pack(side="left")

    def _save(self) -> None:
        self.on_save({key: value.get().strip() for key, value in self.vars.items()})
        self.destroy()


class CouponFormDialog(tk.Toplevel):
    def __init__(
        self,
        parent: tk.Widget,
        title: str,
        payload: dict,
        mode: str,
        on_submit: Callable[[dict], None],
    ):
        super().__init__(parent)
        self.title(title)
        self.configure(bg=BG)
        self.geometry("720x620")
        self.resizable(False, False)
        self.transient(parent.winfo_toplevel())
        self.grab_set()
        self.payload = deepcopy(payload)
        self.mode = mode
        self.on_submit = on_submit
        coupon_info = self.payload.get("coupon_info") or {}
        discount = coupon_info.get("discount_info") or self.payload.get(
            "discount_info"
        ) or {}
        receive = coupon_info.get("receive_info") or self.payload.get(
            "receive_info"
        ) or {}
        valid = coupon_info.get("valid_info") or self.payload.get(
            "valid_info"
        ) or {}
        promote = coupon_info.get("promote_info") or self.payload.get(
            "promote_info"
        ) or {}

        self.vars = {
            "coupon_id": tk.StringVar(value=str(self.payload.get("coupon_id") or "")),
            "type": tk.StringVar(
                value=str(self.payload.get("type") or coupon_info.get("type") or 104)
            ),
            "name": tk.StringVar(
                value=str(coupon_info.get("name") or self.payload.get("name") or "")
            ),
            "promote_type": tk.StringVar(value=str(promote.get("promote_type") or 1)),
            "discount_fee": tk.StringVar(
                value=f"{int(discount.get('discount_fee') or 0) / 100:.2f}"
            ),
            "discount_num": tk.StringVar(
                value=str(discount.get("discount_num") or 0)
            ),
            "total_num": tk.StringVar(value=str(receive.get("total_num") or 0)),
            "limit_num_one_person": tk.StringVar(
                value=str(receive.get("limit_num_one_person") or 1)
            ),
            "start_time": tk.StringVar(
                value=self._format_time(receive.get("start_time"))
            ),
            "end_time": tk.StringVar(
                value=self._format_time(receive.get("end_time"))
            ),
            "valid_type": tk.StringVar(value=str(valid.get("valid_type") or 2)),
            "valid_day_num": tk.StringVar(
                value=str(valid.get("valid_day_num") or 7)
            ),
        }
        frame = ttk.Frame(self, style="Panel.TFrame", padding=22)
        frame.pack(fill="both", expand=True, padx=16, pady=16)
        frame.columnconfigure(1, weight=1)
        ttk.Label(frame, text=title, style="Section.TLabel").grid(
            row=0,
            column=0,
            columnspan=2,
            sticky="w",
            pady=(0, 14),
        )
        rows = (
            ("优惠券 ID", "coupon_id"),
            ("优惠券类型", "type"),
            ("优惠券名称", "name"),
            ("促销类型", "promote_type"),
            ("优惠金额（元）", "discount_fee"),
            ("折扣数值", "discount_num"),
            ("发放总量", "total_num"),
            ("每人限领", "limit_num_one_person"),
            ("开始时间", "start_time"),
            ("结束时间", "end_time"),
            ("有效期类型", "valid_type"),
            ("领取后有效天数", "valid_day_num"),
        )
        for row, (label, key) in enumerate(rows, 1):
            ttk.Label(frame, text=label, style="Panel.TLabel").grid(
                row=row,
                column=0,
                sticky="w",
                padx=(0, 12),
                pady=5,
            )
            entry = ttk.Entry(frame, textvariable=self.vars[key])
            entry.grid(row=row, column=1, sticky="ew", pady=5)
            if key == "coupon_id":
                entry.configure(state="readonly")
        ttk.Label(
            frame,
            text="时间格式：YYYY-MM-DD HH:MM。折扣和活动类型必须符合微信优惠券接口规则。",
            style="Panel.TLabel",
            foreground=MUTED,
        ).grid(
            row=len(rows) + 1,
            column=0,
            columnspan=2,
            sticky="w",
            pady=(8, 0),
        )
        actions = ttk.Frame(frame, style="Panel.TFrame")
        actions.grid(
            row=len(rows) + 2,
            column=0,
            columnspan=2,
            sticky="e",
            pady=(14, 0),
        )
        button(actions, "高级字段", self._advanced).pack(side="left")
        button(actions, "取消", self.destroy).pack(side="left", padx=8)
        button(actions, "保存并提交", self._submit, "Primary").pack(side="left")

    @staticmethod
    def _format_time(value) -> str:
        try:
            timestamp = int(value or 0)
        except (TypeError, ValueError):
            timestamp = 0
        if not timestamp:
            return ""
        return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M")

    @staticmethod
    def _parse_time(value: str) -> int:
        value = value.strip()
        if not value:
            return 0
        for pattern in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                return int(datetime.strptime(value, pattern).timestamp())
            except ValueError:
                continue
        raise ValueError("时间格式必须为 YYYY-MM-DD HH:MM")

    def _collect(self) -> dict:
        name = self.vars["name"].get().strip()
        if not name:
            messagebox.showwarning("优惠券名称", "优惠券名称不能为空。", parent=self)
            return {}
        try:
            discount_fee = int(
                round(float(self.vars["discount_fee"].get() or 0) * 100)
            )
            values = {
                "coupon_id": self.vars["coupon_id"].get().strip(),
                "type": int(self.vars["type"].get()),
                "name": name,
                "promote_info": {
                    "promote_type": int(self.vars["promote_type"].get() or 1)
                },
                "discount_info": {
                    "discount_fee": discount_fee,
                    "discount_num": int(self.vars["discount_num"].get() or 0),
                },
                "receive_info": {
                    "total_num": int(self.vars["total_num"].get() or 0),
                    "limit_num_one_person": int(
                        self.vars["limit_num_one_person"].get() or 1
                    ),
                    "start_time": self._parse_time(
                        self.vars["start_time"].get()
                    ),
                    "end_time": self._parse_time(self.vars["end_time"].get()),
                },
                "valid_info": {
                    "valid_type": int(self.vars["valid_type"].get() or 2),
                    "valid_day_num": int(
                        self.vars["valid_day_num"].get() or 7
                    ),
                },
            }
        except ValueError as exc:
            messagebox.showwarning("优惠券参数", str(exc), parent=self)
            return {}
        payload = deepcopy(self.payload)
        if "coupon_info" in payload:
            coupon_info = payload.setdefault("coupon_info", {})
            coupon_info.update(
                {
                    "name": name,
                    "promote_info": values["promote_info"],
                    "discount_info": values["discount_info"],
                    "receive_info": values["receive_info"],
                    "valid_info": values["valid_info"],
                }
            )
            payload.update(
                {
                    "coupon_id": values["coupon_id"],
                    "type": values["type"],
                }
            )
        else:
            payload.update(values)
        return payload

    def _advanced(self) -> None:
        payload = self._collect()
        if payload:
            def submit_advanced(updated: dict) -> None:
                self.on_submit(updated)
                self.destroy()

            ProductJsonDialog(
                self,
                "优惠券高级字段",
                payload,
                self.mode,
                submit_advanced,
            )

    def _submit(self) -> None:
        payload = self._collect()
        if payload:
            self.on_submit(payload)
            self.destroy()


class ProductFormDialog(tk.Toplevel):
    def __init__(
        self,
        parent: tk.Widget,
        title: str,
        payload: dict,
        mode: str,
        on_submit: Callable[[dict], None],
        on_upload_images: Callable[[list[str]], list[str]] | None = None,
    ):
        super().__init__(parent)
        self.title(title)
        self.configure(bg=BG)
        self.geometry("1040x760")
        self.minsize(900, 650)
        self.transient(parent.winfo_toplevel())
        self.grab_set()
        self.payload = deepcopy(payload)
        self.mode = mode
        self.on_submit = on_submit
        self.on_upload_images = on_upload_images

        wrapper = ttk.Frame(self, style="Panel.TFrame", padding=18)
        wrapper.pack(fill="both", expand=True, padx=14, pady=14)
        wrapper.rowconfigure(1, weight=1)
        wrapper.columnconfigure(0, weight=1)
        ttk.Label(wrapper, text=title, style="Section.TLabel").grid(
            row=0,
            column=0,
            sticky="w",
            pady=(0, 10),
        )
        notebook = ttk.Notebook(wrapper)
        notebook.grid(row=1, column=0, sticky="nsew")

        basic = ttk.Frame(notebook, style="Panel.TFrame", padding=18)
        media = ttk.Frame(notebook, style="Panel.TFrame", padding=18)
        sku_tab = ttk.Frame(notebook, style="Panel.TFrame", padding=18)
        services = ttk.Frame(notebook, style="Panel.TFrame", padding=18)
        notebook.add(basic, text="基本资料")
        notebook.add(media, text="图片与详情")
        notebook.add(sku_tab, text="SKU 与价格库存")
        notebook.add(services, text="售后与展示")

        self.title_var = tk.StringVar(value=str(self.payload.get("title") or ""))
        self.short_title_var = tk.StringVar(
            value=str(self.payload.get("short_title") or "")
        )
        self.spu_code_var = tk.StringVar(
            value=str(self.payload.get("spu_code") or "")
        )
        self.out_product_id_var = tk.StringVar(
            value=str(self.payload.get("out_product_id") or "")
        )
        self.brand_id_var = tk.StringVar(
            value=str(self.payload.get("brand_id") or "")
        )
        self.deliver_method_var = tk.StringVar(
            value=str(self.payload.get("deliver_method") or 0)
        )
        express = self.payload.get("express_info") or {}
        self.template_id_var = tk.StringVar(
            value=str(express.get("template_id") or "")
        )
        self.weight_var = tk.StringVar(value=str(express.get("weight") or 0))
        self.express_type_var = tk.StringVar(
            value=str(express.get("express_type") or 0)
        )
        cats_v2 = self.payload.get("cats_v2") or self.payload.get("cats") or []
        self.cats_var = tk.StringVar(
            value=",".join(
                str(item.get("cat_id"))
                for item in cats_v2
                if isinstance(item, dict) and item.get("cat_id")
            )
        )
        self._build_basic(basic)
        self._build_media(media)
        self._build_sku(sku_tab)
        self._build_services(services)

        actions = ttk.Frame(wrapper, style="Panel.TFrame")
        actions.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        button(actions, "取消", self.destroy).pack(side="right", padx=(8, 0))
        button(
            actions,
            "保存并提交",
            self._submit,
            "Primary",
        ).pack(side="right")

    @staticmethod
    def _form_row(parent, row: int, label: str, widget) -> None:
        ttk.Label(parent, text=label, style="Panel.TLabel").grid(
            row=row,
            column=0,
            sticky="nw",
            padx=(0, 12),
            pady=5,
        )
        widget.grid(row=row, column=1, sticky="ew", pady=5)

    def _build_basic(self, frame) -> None:
        frame.columnconfigure(1, weight=1)
        rows = (
            ("商品标题", ttk.Entry(frame, textvariable=self.title_var)),
            ("短标题", ttk.Entry(frame, textvariable=self.short_title_var)),
            ("SPU 编码", ttk.Entry(frame, textvariable=self.spu_code_var)),
            (
                "外部商品 ID",
                ttk.Entry(frame, textvariable=self.out_product_id_var),
            ),
            ("品牌 ID", ttk.Entry(frame, textvariable=self.brand_id_var)),
            (
                "类目 ID（逗号分隔）",
                ttk.Entry(frame, textvariable=self.cats_var),
            ),
            (
                "发货方式编号",
                ttk.Entry(frame, textvariable=self.deliver_method_var),
            ),
            (
                "运费模板 ID",
                ttk.Entry(frame, textvariable=self.template_id_var),
            ),
            ("重量", ttk.Entry(frame, textvariable=self.weight_var)),
            (
                "运费类型",
                ttk.Entry(frame, textvariable=self.express_type_var),
            ),
        )
        for row, (label, widget) in enumerate(rows):
            self._form_row(frame, row, label, widget)
        ttk.Button(
            frame,
            text="编辑类目属性",
            command=self._edit_attributes,
        ).grid(row=len(rows), column=1, sticky="e", pady=(10, 0))
        ttk.Label(
            frame,
            text=(
                "类目、品牌、运费模板需要与微信小店后台一致。修改已有商品时会保留"
                "未在表单中展开的官方字段。"
            ),
            style="Panel.TLabel",
            foreground=MUTED,
            wraplength=760,
            justify="left",
        ).grid(row=len(rows) + 1, column=0, columnspan=2, sticky="w", pady=(12, 0))

    def _build_media(self, frame) -> None:
        frame.rowconfigure(1, weight=1)
        frame.rowconfigure(4, weight=1)
        frame.columnconfigure(0, weight=1)
        images_header = ttk.Frame(frame, style="Panel.TFrame")
        images_header.grid(row=0, column=0, sticky="ew")
        ttk.Label(
            images_header,
            text="商品主图 URL（每行一个，最多 9 张）",
            style="Section.TLabel",
        ).pack(side="left")
        button(images_header, "上传主图", self._upload_main_images).pack(
            side="right"
        )
        self.main_images_text = tk.Text(frame, height=7, wrap="none")
        self.main_images_text.grid(row=1, column=0, sticky="nsew", pady=(7, 12))
        self.main_images_text.insert(
            "1.0",
            "\n".join(str(value) for value in self.payload.get("head_imgs") or []),
        )
        detail_header = ttk.Frame(frame, style="Panel.TFrame")
        detail_header.grid(row=2, column=0, sticky="ew")
        ttk.Label(
            detail_header,
            text="详情图 URL（每行一个）",
            style="Section.TLabel",
        ).pack(side="left")
        button(detail_header, "上传详情图", self._upload_detail_images).pack(
            side="right"
        )
        self.detail_images_text = tk.Text(frame, height=7, wrap="none")
        self.detail_images_text.grid(
            row=3,
            column=0,
            sticky="nsew",
            pady=(7, 12),
        )
        desc_info = self.payload.get("desc_info") or {}
        self.detail_images_text.insert(
            "1.0",
            "\n".join(str(value) for value in desc_info.get("imgs") or []),
        )
        ttk.Label(frame, text="商品详情文字", style="Panel.TLabel").grid(
            row=4,
            column=0,
            sticky="nw",
        )
        self.desc_text = tk.Text(frame, height=8, wrap="word")
        self.desc_text.grid(row=5, column=0, sticky="nsew", pady=(7, 0))
        self.desc_text.insert("1.0", str(desc_info.get("desc") or ""))

    def _build_sku(self, frame) -> None:
        frame.columnconfigure(0, weight=1)
        self.sku_summary_var = tk.StringVar()
        skus = self.payload.get("skus") or []
        self._set_sku_summary(skus)
        ttk.Label(
            frame,
            text="商品规格、SKU 编码、价格、库存和 SKU 图片",
            style="Section.TLabel",
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(
            frame,
            textvariable=self.sku_summary_var,
            style="Panel.TLabel",
            foreground=MUTED,
        ).grid(row=1, column=0, sticky="w", pady=(8, 12))
        button(frame, "打开 SKU 编辑器", self._edit_skus, "Primary").grid(
            row=2,
            column=0,
            sticky="w",
        )

    def _build_services(self, frame) -> None:
        frame.columnconfigure(1, weight=1)
        service = self.payload.get("extra_service") or {}
        self.service_vars = {
            "seven_day_return": tk.BooleanVar(
                value=bool(service.get("seven_day_return"))
            ),
            "freight_insurance": tk.BooleanVar(
                value=bool(service.get("freight_insurance"))
            ),
            "pay_after_use": tk.BooleanVar(
                value=bool(service.get("pay_after_use"))
            ),
            "damage_guarantee": tk.BooleanVar(
                value=bool(service.get("damage_guarantee"))
            ),
            "fake_one_pay_three": tk.BooleanVar(
                value=bool(service.get("fake_one_pay_three"))
            ),
            "exchange_support": tk.BooleanVar(
                value=bool(service.get("exchange_support"))
            ),
        }
        for row, (key, label) in enumerate(
            (
                ("seven_day_return", "七天无理由退货"),
                ("freight_insurance", "运费险"),
                ("pay_after_use", "先用后付"),
                ("damage_guarantee", "破损保障"),
                ("fake_one_pay_three", "假一赔三"),
                ("exchange_support", "支持换货"),
            )
        ):
            tk.Checkbutton(
                frame,
                text=label,
                variable=self.service_vars[key],
                anchor="w",
            ).grid(row=row, column=0, columnspan=2, sticky="w", pady=4)
        self.hide_var = tk.BooleanVar(
            value=bool(self.payload.get("hide_in_window"))
        )
        tk.Checkbutton(
            frame,
            text="在店铺主页隐藏",
            variable=self.hide_var,
            anchor="w",
        ).grid(row=6, column=0, columnspan=2, sticky="w", pady=4)
        ttk.Label(frame, text="售后说明", style="Panel.TLabel").grid(
            row=7,
            column=0,
            sticky="nw",
            pady=(10, 4),
        )
        self.aftersale_text = tk.Text(frame, height=7, wrap="word")
        self.aftersale_text.grid(
            row=7,
            column=1,
            sticky="nsew",
            pady=(10, 4),
        )
        self.aftersale_text.insert(
            "1.0",
            str(self.payload.get("aftersale_desc") or ""),
        )

    def _set_sku_summary(self, skus: list[dict]) -> None:
        prices = [
            int(item.get("sale_price") or 0)
            for item in skus
            if isinstance(item, dict)
        ]
        stock = sum(
            int(item.get("stock_num") or 0)
            for item in skus
            if isinstance(item, dict)
        )
        price_text = f"¥{min(prices) / 100:.2f} 起" if prices else "未设置价格"
        self.sku_summary_var.set(
            f"当前 {len(skus)} 个 SKU · {price_text} · 总库存 {stock}"
        )

    def _lines(self, text_widget: tk.Text) -> list[str]:
        return [
            line.strip()
            for line in text_widget.get("1.0", "end").splitlines()
            if line.strip()
        ]

    def _upload(self, title: str) -> list[str]:
        if self.on_upload_images is None:
            return []
        paths = filedialog.askopenfilenames(
            parent=self,
            title=title,
            filetypes=[
                ("图片", "*.jpg *.jpeg *.png *.webp *.bmp"),
                ("所有文件", "*.*"),
            ],
        )
        if not paths:
            return []
        try:
            return self.on_upload_images(list(paths))
        except Exception as exc:
            messagebox.showerror("图片上传失败", str(exc), parent=self)
            return []

    def _upload_main_images(self) -> None:
        urls = self._upload("选择商品主图")
        if urls:
            existing = self._lines(self.main_images_text)
            self.main_images_text.delete("1.0", "end")
            self.main_images_text.insert(
                "1.0",
                "\n".join(dict.fromkeys(existing + urls)),
            )

    def _upload_detail_images(self) -> None:
        urls = self._upload("选择商品详情图")
        if urls:
            existing = self._lines(self.detail_images_text)
            self.detail_images_text.delete("1.0", "end")
            self.detail_images_text.insert(
                "1.0",
                "\n".join(dict.fromkeys(existing + urls)),
            )

    def _edit_skus(self) -> None:
        def save(skus: list[dict]) -> None:
            self.payload["skus"] = skus
            self._set_sku_summary(skus)

        SkuMatrixDialog(self, self.payload.get("skus") or [], save)

    def _edit_attributes(self) -> None:
        attributes = [
            dict(item)
            for item in self.payload.get("attrs") or []
            if isinstance(item, dict)
        ]
        dialog = tk.Toplevel(self)
        dialog.title("商品类目属性")
        dialog.transient(self)
        dialog.geometry("760x500")
        body = ttk.Frame(dialog, style="Panel.TFrame", padding=14)
        body.pack(fill="both", expand=True)
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=1)
        tree = ttk.Treeview(
            body,
            columns=("key", "value"),
            show="headings",
        )
        tree.heading("key", text="属性名")
        tree.heading("value", text="属性值")
        tree.column("key", width=240)
        tree.column("value", width=420)
        tree.grid(row=0, column=0, sticky="nsew")
        ttk.Scrollbar(body, orient="vertical", command=tree.yview).grid(
            row=0, column=1, sticky="ns"
        )

        def render() -> None:
            tree.delete(*tree.get_children())
            for index, item in enumerate(attributes):
                tree.insert(
                    "",
                    "end",
                    iid=str(index),
                    values=(
                        str(item.get("attr_key") or item.get("key") or ""),
                        str(item.get("attr_value") or item.get("value") or ""),
                    ),
                )

        def edit() -> None:
            selection = tree.selection()
            if not selection:
                return
            index = int(selection[0])
            current = attributes[index]

            def save(values: dict[str, str]) -> None:
                attributes[index] = {
                    **current,
                    "attr_key": values.get("attr_key", ""),
                    "attr_value": values.get("attr_value", ""),
                }
                render()

            SimpleFieldsDialog(
                dialog,
                "编辑属性",
                (("attr_key", "属性名"), ("attr_value", "属性值")),
                save,
                initial={
                    "attr_key": str(current.get("attr_key") or current.get("key") or ""),
                    "attr_value": str(
                        current.get("attr_value") or current.get("value") or ""
                    ),
                },
            )

        def add() -> None:
            def save(values: dict[str, str]) -> None:
                attributes.append(
                    {
                        "attr_key": values.get("attr_key", ""),
                        "attr_value": values.get("attr_value", ""),
                    }
                )
                render()

            SimpleFieldsDialog(
                dialog,
                "新增属性",
                (("attr_key", "属性名"), ("attr_value", "属性值")),
                save,
            )

        def remove() -> None:
            for iid in sorted(tree.selection(), reverse=True):
                del attributes[int(iid)]
            render()

        actions = ttk.Frame(body, style="Panel.TFrame")
        actions.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        button(actions, "新增", add).pack(side="left")
        button(actions, "编辑", edit).pack(side="left", padx=6)
        button(actions, "删除", remove, "Danger").pack(side="left")

        def apply() -> None:
            self.payload["attrs"] = attributes
            dialog.destroy()

        button(actions, "应用", apply, "Primary").pack(side="right")
        render()
        dialog.grab_set()

    def _collect(self) -> dict:
        title = self.title_var.get().strip()
        if not title:
            messagebox.showwarning("商品标题", "商品标题不能为空。", parent=self)
            return {}
        skus = self.payload.get("skus") or []
        if not skus:
            messagebox.showwarning("商品 SKU", "至少需要配置一个 SKU。", parent=self)
            return {}
        try:
            deliver_method = int(self.deliver_method_var.get() or 0)
            weight = int(self.weight_var.get() or 0)
            express_type = int(self.express_type_var.get() or 0)
        except ValueError:
            messagebox.showwarning(
                "商品数值",
                "发货方式、重量和运费类型必须是整数。",
                parent=self,
            )
            return {}
        payload = deepcopy(self.payload)
        payload.update(
            {
                "title": title,
                "short_title": self.short_title_var.get().strip(),
                "spu_code": self.spu_code_var.get().strip(),
                "out_product_id": self.out_product_id_var.get().strip(),
                "brand_id": self.brand_id_var.get().strip(),
                "deliver_method": deliver_method,
            }
        )
        category_ids = [
            value.strip()
            for value in self.cats_var.get().replace("，", ",").split(",")
            if value.strip()
        ]
        payload["cats_v2"] = [{"cat_id": value} for value in category_ids]
        if not payload.get("cats"):
            payload["cats"] = [{"cat_id": value} for value in category_ids]
        payload["express_info"] = {
            **(payload.get("express_info") or {}),
            "template_id": self.template_id_var.get().strip(),
            "weight": weight,
            "express_type": express_type,
        }
        payload["head_imgs"] = self._lines(self.main_images_text)[:9]
        payload["desc_info"] = {
            **(payload.get("desc_info") or {}),
            "imgs": self._lines(self.detail_images_text),
            "desc": self.desc_text.get("1.0", "end").strip(),
        }
        payload["extra_service"] = {
            **(payload.get("extra_service") or {}),
            **{
                key: 1 if variable.get() else 0
                for key, variable in self.service_vars.items()
            },
        }
        payload["hide_in_window"] = 1 if self.hide_var.get() else 0
        payload["aftersale_desc"] = self.aftersale_text.get(
            "1.0",
            "end",
        ).strip()
        return payload

    def _open_advanced_fields(self) -> None:
        payload = self._collect()
        if not payload:
            return

        def apply_payload(updated: dict) -> None:
            self.payload = deepcopy(updated)
            self.title_var.set(str(updated.get("title") or ""))
            self.short_title_var.set(str(updated.get("short_title") or ""))

        ProductJsonDialog(
            self,
            f"{self.mode_label()} · 高级字段",
            payload,
            self.mode,
            apply_payload,
        )

    def mode_label(self) -> str:
        return "新建商品" if self.mode == "add" else "编辑商品"

    def _submit(self) -> None:
        payload = self._collect()
        if payload:
            self.on_submit(payload)
            self.destroy()


class ProductJsonDialog(tk.Toplevel):
    def __init__(
        self,
        parent: tk.Widget,
        title: str,
        payload: dict,
        mode: str,
        on_submit: Callable[[dict], None],
        on_upload_images: Callable[[list[str]], list[str]] | None = None,
        help_text: str = "按微信小店官方字段填写。图片必须是已上传到微信的图片 URL；发布前会再次确认。",
    ):
        super().__init__(parent)
        self.title(title)
        self.configure(bg=BG)
        self.geometry("980x740")
        self.transient(parent.winfo_toplevel())
        self.grab_set()
        self.on_submit = on_submit
        self.on_upload_images = on_upload_images
        self.mode = mode
        frame = ttk.Frame(self, style="Panel.TFrame", padding=18)
        frame.pack(fill="both", expand=True, padx=14, pady=14)
        frame.rowconfigure(2, weight=1)
        frame.columnconfigure(0, weight=1)
        ttk.Label(frame, text=title, style="Section.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(
            frame,
            text=help_text,
            style="Panel.TLabel",
            foreground=MUTED,
        ).grid(row=1, column=0, sticky="w", pady=(4, 8))
        self.text = tk.Text(
            frame,
            wrap="none",
            bg="#fbfcfe",
            fg=TEXT,
            relief="solid",
            bd=1,
            padx=10,
            pady=8,
            font=("Consolas", 9),
        )
        self.text.grid(row=2, column=0, sticky="nsew")
        y_scroll = ttk.Scrollbar(frame, orient="vertical", command=self.text.yview)
        y_scroll.grid(row=2, column=1, sticky="ns")
        x_scroll = ttk.Scrollbar(frame, orient="horizontal", command=self.text.xview)
        x_scroll.grid(row=3, column=0, sticky="ew")
        self.text.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)
        self.text.insert("1.0", json.dumps(payload, ensure_ascii=False, indent=2))
        actions = ttk.Frame(frame, style="Panel.TFrame")
        actions.grid(row=4, column=0, columnspan=2, sticky="e", pady=(10, 0))
        if on_upload_images:
            button(actions, "上传主图", self._upload_images).pack(side="left")
            button(actions, "上传详情图", self._upload_detail_images).pack(side="left", padx=(8, 0))
        button(actions, "编辑 SKU", self._edit_skus).pack(side="left", padx=(8, 0))
        button(actions, "取消", self.destroy).pack(side="left", padx=(0, 8))
        button(
            actions,
            "提交商品",
            self._submit,
            "Primary",
        ).pack(side="left")

    def _upload_images(self) -> None:
        if self.on_upload_images is None:
            return
        paths = filedialog.askopenfilenames(
            parent=self,
            title="选择商品主图",
            filetypes=[
                ("图片", "*.jpg *.jpeg *.png *.webp *.bmp"),
                ("所有文件", "*.*"),
            ],
        )
        if not paths:
            return
        try:
            urls = self.on_upload_images(list(paths))
            payload = json.loads(self.text.get("1.0", "end"))
            current = payload.setdefault("head_imgs", [])
            for url in urls:
                if url not in current:
                    current.append(url)
            payload["head_imgs"] = current[:9]
            self.text.delete("1.0", "end")
            self.text.insert("1.0", json.dumps(payload, ensure_ascii=False, indent=2))
        except Exception as exc:
            messagebox.showerror("图片上传失败", str(exc), parent=self)

    def _edit_skus(self) -> None:
        try:
            payload = json.loads(self.text.get("1.0", "end"))
        except json.JSONDecodeError as exc:
            messagebox.showwarning("JSON 格式错误", str(exc), parent=self)
            return

        def save(skus: list[dict]) -> None:
            payload["skus"] = skus
            self.text.delete("1.0", "end")
            self.text.insert("1.0", json.dumps(payload, ensure_ascii=False, indent=2))

        SkuMatrixDialog(self, payload.get("skus") or [], save)

    def _upload_detail_images(self) -> None:
        if self.on_upload_images is None:
            return
        paths = filedialog.askopenfilenames(
            parent=self,
            title="选择商品详情图",
            filetypes=[("图片", "*.jpg *.jpeg *.png *.webp *.bmp"), ("所有文件", "*.*")],
        )
        if not paths:
            return
        try:
            urls = self.on_upload_images(list(paths))
            payload = json.loads(self.text.get("1.0", "end"))
            desc_info = payload.setdefault("desc_info", {"imgs": [], "desc": ""})
            current = desc_info.setdefault("imgs", [])
            for url in urls:
                if url not in current:
                    current.append(url)
            desc_info["imgs"] = current[:50]
            self.text.delete("1.0", "end")
            self.text.insert("1.0", json.dumps(payload, ensure_ascii=False, indent=2))
        except Exception as exc:
            messagebox.showerror("详情图上传失败", str(exc), parent=self)


class SkuMatrixDialog(tk.Toplevel):
    def __init__(self, parent: tk.Widget, skus: list[dict], on_save: Callable[[list[dict]], None]):
        super().__init__(parent)
        self.title("SKU 与多规格")
        self.configure(bg=BG)
        self.geometry("980x620")
        self.transient(parent.winfo_toplevel())
        self.grab_set()
        self.on_save = on_save
        frame = ttk.Frame(self, style="Panel.TFrame", padding=18)
        frame.pack(fill="both", expand=True, padx=14, pady=14)
        frame.rowconfigure(2, weight=1)
        frame.columnconfigure(0, weight=1)
        ttk.Label(frame, text="批量生成规格组合", style="Section.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        generator = ttk.Frame(frame, style="Panel.TFrame")
        generator.grid(row=1, column=0, sticky="ew", pady=8)
        ttk.Label(generator, text="规格格式", style="Panel.TLabel").pack(side="left")
        self.spec_var = tk.StringVar(value="颜色=红色,蓝色;尺寸=M,L")
        ttk.Entry(generator, textvariable=self.spec_var, width=54).pack(
            side="left", padx=8
        )
        button(generator, "生成组合", self._generate).pack(side="left")
        ttk.Label(
            generator,
            text="分号分组，逗号分隔值",
            style="Panel.TLabel",
            foreground=MUTED,
        ).pack(side="left", padx=8)

        body = ttk.Frame(frame, style="Panel.TFrame")
        body.grid(row=2, column=0, sticky="nsew")
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=1)
        columns = ("sku_id", "code", "price", "stock", "attrs", "image")
        self.tree = ttk.Treeview(body, columns=columns, show="headings")
        for key, text, width in (
            ("sku_id", "SKU ID", 150),
            ("code", "SKU 编码", 150),
            ("price", "价格（元）", 100),
            ("stock", "库存", 80),
            ("attrs", "规格", 260),
            ("image", "图片 URL", 190),
        ):
            self.tree.heading(key, text=text)
            self.tree.column(key, width=width, anchor="w")
        self.tree.grid(row=0, column=0, sticky="nsew")
        ttk.Scrollbar(body, orient="vertical", command=self.tree.yview).grid(
            row=0, column=1, sticky="ns"
        )
        self.tree.bind("<<TreeviewSelect>>", self._load_selected)
        self.skus = [dict(item) for item in skus]
        if not self.skus:
            self.skus = [{"sale_price": 0, "stock_num": 0, "sku_attrs": []}]
        self._render()
        editor = ttk.Frame(frame, style="Panel.TFrame")
        editor.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        self.edit_code = tk.StringVar()
        self.edit_price = tk.StringVar()
        self.edit_stock = tk.StringVar()
        self.edit_image = tk.StringVar()
        for label, variable, width in (
            ("编码", self.edit_code, 15),
            ("价格元", self.edit_price, 10),
            ("库存", self.edit_stock, 8),
            ("SKU图URL", self.edit_image, 32),
        ):
            ttk.Label(editor, text=label, style="Panel.TLabel").pack(side="left")
            ttk.Entry(editor, textvariable=variable, width=width).pack(side="left", padx=(4, 8))
        button(editor, "应用到选中", self._apply_selected).pack(side="left")
        actions = ttk.Frame(frame, style="Panel.TFrame")
        actions.grid(row=4, column=0, sticky="ew", pady=(8, 0))
        button(actions, "新增空白 SKU", self._add).pack(side="left")
        button(actions, "删除选中", self._remove, "Danger").pack(side="left", padx=8)
        button(actions, "保存 SKU", self._save, "Primary").pack(side="right")
        button(actions, "取消", self.destroy).pack(side="right", padx=8)

    def _render(self) -> None:
        self.tree.delete(*self.tree.get_children())
        for index, sku in enumerate(self.skus):
            attrs = "；".join(
                f"{item.get('attr_key')}={item.get('attr_value')}"
                for item in sku.get("sku_attrs", [])
                if isinstance(item, dict)
            )
            self.tree.insert(
                "",
                "end",
                iid=str(index),
                values=(
                    sku.get("sku_id", ""),
                    sku.get("sku_code", ""),
                    f"{int(sku.get('sale_price') or 0) / 100:.2f}",
                    sku.get("stock_num", 0),
                    attrs,
                    sku.get("thumb_img", ""),
                ),
            )

    def _generate(self) -> None:
        groups = []
        try:
            for part in self.spec_var.get().split(";"):
                if not part.strip():
                    continue
                key, values = part.split("=", 1)
                groups.append((key.strip(), [v.strip() for v in values.split(",") if v.strip()]))
        except ValueError:
            messagebox.showwarning("规格格式", "示例：颜色=红,蓝;尺寸=M,L", parent=self)
            return
        if not groups:
            return
        combinations: list[list[tuple[str, str]]] = [[]]
        for key, values in groups:
            combinations = [existing + [(key, value)] for existing in combinations for value in values]
        if len(combinations) > 500:
            messagebox.showwarning("组合过多", "平台最多支持 500 个 SKU。", parent=self)
            return
        self.skus = [
            {
                "sale_price": 0,
                "stock_num": 0,
                "sku_attrs": [
                    {"attr_key": key, "attr_value": value}
                    for key, value in combination
                ],
            }
            for combination in combinations
        ]
        self._render()

    def _selected_index(self) -> int | None:
        selected = self.tree.selection()
        return int(selected[0]) if selected else None

    def _load_selected(self, _event=None) -> None:
        index = self._selected_index()
        if index is None or index >= len(self.skus):
            return
        sku = self.skus[index]
        self.edit_code.set(str(sku.get("sku_code") or ""))
        self.edit_price.set(f"{int(sku.get('sale_price') or 0) / 100:.2f}")
        self.edit_stock.set(str(sku.get("stock_num") or 0))
        self.edit_image.set(str(sku.get("thumb_img") or ""))

    def _apply_selected(self) -> None:
        index = self._selected_index()
        if index is None or index >= len(self.skus):
            messagebox.showinfo("请选择 SKU", "请先选择一行 SKU。", parent=self)
            return
        try:
            price = int(round(float(self.edit_price.get()) * 100))
            stock = int(self.edit_stock.get())
        except ValueError:
            messagebox.showwarning("数值错误", "价格和库存必须为数字。", parent=self)
            return
        self.skus[index].update(
            {
                "sku_code": self.edit_code.get().strip(),
                "sale_price": price,
                "stock_num": max(0, stock),
                "thumb_img": self.edit_image.get().strip(),
            }
        )
        self._render()

    def _add(self) -> None:
        self.skus.append({"sale_price": 0, "stock_num": 0, "sku_attrs": []})
        self._render()

    def _remove(self) -> None:
        selected = self.tree.selection()
        if not selected:
            return
        for index in sorted((int(value) for value in selected), reverse=True):
            del self.skus[index]
        self._render()

    def _save(self) -> None:
        for sku in self.skus:
            sku["sale_price"] = int(sku.get("sale_price") or 0)
            sku["stock_num"] = int(sku.get("stock_num") or 0)
        self.on_save(self.skus)
        self.destroy()

    def _submit(self) -> None:
        try:
            payload = json.loads(self.text.get("1.0", "end"))
        except json.JSONDecodeError as exc:
            messagebox.showwarning("JSON 格式错误", str(exc), parent=self)
            return
        if not isinstance(payload, dict):
            messagebox.showwarning("数据格式错误", "商品数据必须是 JSON 对象。", parent=self)
            return
        self.on_submit(payload)
        self.destroy()


class ShipmentDialog(tk.Toplevel):
    def __init__(
        self,
        parent: tk.Widget,
        shipment: dict,
        on_complete: Callable[[], None],
    ):
        super().__init__(parent)
        self.title(f"电子面单 · {shipment['order_id']}")
        self.configure(bg=BG)
        self.geometry("620x420")
        self.transient(parent.winfo_toplevel())
        self.grab_set()
        self.shipment = shipment
        frame = ttk.Frame(self, style="Panel.TFrame", padding=22)
        frame.pack(fill="both", expand=True, padx=16, pady=16)
        frame.rowconfigure(1, weight=1)
        frame.columnconfigure(0, weight=1)
        ttk.Label(frame, text="电子面单已生成", style="Section.TLabel").grid(
            row=0, column=0, sticky="w", pady=(0, 12)
        )
        text = tk.Text(
            frame,
            wrap="word",
            height=11,
            bg="#fbfcfe",
            fg=TEXT,
            relief="solid",
            bd=1,
            padx=12,
            pady=10,
            font=("Microsoft YaHei UI", 10),
        )
        text.grid(row=1, column=0, columnspan=2, sticky="nsew")
        text.insert(
            "1.0",
            f"订单号：{shipment['order_id']}\n"
            f"运单号：{shipment['waybill_id']}\n"
            f"快递公司：{shipment['delivery_id']}\n"
            f"面单状态：{SHIPMENT_STATUS.get(shipment['status'], shipment['status'])}\n\n"
            "打印报文已保存在本地数据库中。\n"
            "后续版本将接入 Windows 打印组件；当前可先导出报文。",
        )
        text.configure(state="disabled")
        actions = ttk.Frame(frame, style="Panel.TFrame")
        actions.grid(row=2, column=0, columnspan=2, sticky="e", pady=(12, 0))
        button(actions, "导出打印报文", self._save_print_info).pack(side="left")
        button(actions, "关闭", self.destroy).pack(side="left", padx=8)
        button(actions, "确认已打印并发货", self._complete, "Primary").pack(side="left")
        self.on_complete = on_complete

    def _save_print_info(self) -> None:
        content = self.shipment.get("print_info") or ""
        if not content:
            messagebox.showinfo("没有报文", "当前面单响应没有打印报文。", parent=self)
            return
        path = filedialog.asksaveasfilename(
            parent=self,
            title="导出打印报文",
            defaultextension=".txt",
            initialfile=f"面单_{self.shipment['waybill_id']}.txt",
            filetypes=[("文本文件", "*.txt"), ("所有文件", "*.*")],
        )
        if not path:
            return
        Path(path).write_text(content, encoding="utf-8")
        messagebox.showinfo("导出完成", f"已保存到：\n{path}", parent=self)

    def _complete(self) -> None:
        if not messagebox.askyesno(
            "确认发货",
            "该操作会通知平台面单已打印，并提交订单发货状态。\n"
            "请确认已经实际打印并获得运单号。是否继续？",
            parent=self,
        ):
            return
        self.on_complete()
        self.destroy()
