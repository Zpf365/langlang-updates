from __future__ import annotations

import tkinter as tk
from decimal import Decimal, InvalidOperation
from tkinter import messagebox, ttk
from typing import Any, Callable

_PLATFORMS = ("淘宝", "淘特", "1688", "拼多多", "京东")
_FLAG_CHOICES = (
    ("红", "#e5484d"),
    ("黄", "#e8b11b"),
    ("绿", "#22a06b"),
    ("蓝", "#3478f6"),
    ("紫", "#8e4ec6"),
)


class ProductTitleEditDialog(tk.Toplevel):
    def __init__(
        self,
        parent: tk.Widget,
        title: str,
        on_save: Callable[[str], None],
    ) -> None:
        super().__init__(parent)
        self.on_save = on_save
        self.title("修改商品标题")
        self.geometry("580x260")
        self.resizable(True, True)
        self.transient(parent.winfo_toplevel())
        self.grab_set()

        panel = ttk.Frame(self, padding=18)
        panel.pack(fill="both", expand=True)
        panel.columnconfigure(1, weight=1)
        panel.rowconfigure(0, weight=1)
        ttk.Label(panel, text="商品标题").grid(
            row=0,
            column=0,
            sticky="nw",
            padx=(0, 14),
            pady=(8, 0),
        )
        text_holder = ttk.Frame(panel)
        text_holder.grid(row=0, column=1, sticky="nsew")
        text_holder.columnconfigure(0, weight=1)
        text_holder.rowconfigure(0, weight=1)
        self.text = tk.Text(
            text_holder,
            wrap="word",
            height=5,
            font=("Microsoft YaHei UI", 10),
            relief="solid",
            bd=1,
            padx=8,
            pady=7,
        )
        self.text.grid(row=0, column=0, sticky="nsew")
        self.text.insert("1.0", title[:200])
        self.counter = ttk.Label(text_holder, text="0 / 200", anchor="e")
        self.counter.grid(row=1, column=0, sticky="e", pady=(4, 0))
        self.text.bind("<KeyRelease>", lambda _event: self._update_counter())
        self._update_counter()
        actions = ttk.Frame(panel)
        actions.grid(row=1, column=0, columnspan=2, sticky="e", pady=(14, 0))
        ttk.Button(actions, text="取消", command=self.destroy).pack(
            side="right",
            padx=(8, 0),
        )
        ttk.Button(actions, text="确定", command=self._save).pack(
            side="right",
        )

    def _update_counter(self) -> None:
        value = self.text.get("1.0", "end-1c")
        if len(value) > 200:
            self.text.delete("1.0+200c", "end")
            value = self.text.get("1.0", "end-1c")
        self.counter.configure(text=f"{len(value)} / 200")

    def _save(self) -> None:
        title = self.text.get("1.0", "end-1c").strip()
        if not title:
            messagebox.showwarning("商品标题", "商品标题不能为空。", parent=self)
            return
        if len(title) > 200:
            messagebox.showwarning(
                "商品标题",
                "商品标题不能超过 200 个字符。",
                parent=self,
            )
            return
        self.on_save(title)
        self.destroy()


class PurchaseInfoDialog(tk.Toplevel):
    def __init__(
        self,
        parent: tk.Widget,
        *,
        order_id: str,
        on_save: Callable[[dict[str, Any]], None],
        initial: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(parent)
        self.on_save = on_save
        initial = initial or {}
        self.title("添加采购信息")
        self.geometry("470x620")
        self.minsize(450, 590)
        self.transient(parent.winfo_toplevel())
        self.grab_set()

        self.purchase_mode = tk.StringVar(
            value=str(initial.get("purchase_mode") or "线上采购")
        )
        self.flag = tk.StringVar(value=str(initial.get("flag") or ""))
        self.transfer_warehouse = tk.StringVar(
            value=str(initial.get("transfer_warehouse") or "否")
        )
        self.platform = tk.StringVar(value=str(initial.get("platform") or ""))
        self.purchase_order_no = tk.StringVar(
            value=str(initial.get("purchase_order_no") or "")
        )
        self.purchase_amount = tk.StringVar(
            value=str(initial.get("purchase_amount") or "0")
        )
        self.commission = tk.StringVar(
            value=str(initial.get("commission") or "0")
        )
        self.remember = tk.BooleanVar(value=bool(initial.get("remember")))

        panel = ttk.Frame(self, padding=(24, 18))
        panel.pack(fill="both", expand=True)
        panel.columnconfigure(1, weight=1)

        self._label(panel, 0, "订单编号：", required=False)
        ttk.Label(panel, text=order_id).grid(
            row=0,
            column=1,
            sticky="w",
            pady=8,
        )

        self._label(panel, 1, "采购方式：", required=False)
        mode = ttk.Frame(panel)
        mode.grid(row=1, column=1, sticky="w", pady=8)
        ttk.Radiobutton(
            mode,
            text="线上采购",
            value="线上采购",
            variable=self.purchase_mode,
        ).pack(side="left", padx=(0, 28))
        ttk.Radiobutton(
            mode,
            text="线下代发",
            value="线下代发",
            variable=self.purchase_mode,
        ).pack(side="left")

        self._label(panel, 2, "标记：", required=False)
        flags = ttk.Frame(panel)
        flags.grid(row=2, column=1, sticky="w", pady=8)
        for index, (name, color) in enumerate(_FLAG_CHOICES):
            radio = tk.Radiobutton(
                flags,
                text="⚑",
                value=name,
                variable=self.flag,
                fg=color,
                activeforeground=color,
                indicatoron=True,
                bd=0,
                highlightthickness=0,
                font=("Microsoft YaHei UI", 11),
            )
            radio.grid(row=index // 4, column=index % 4, padx=(0, 24), pady=3)

        self._label(panel, 3, "中转仓下单：")
        transfer = ttk.Frame(panel)
        transfer.grid(row=3, column=1, sticky="w", pady=8)
        ttk.Radiobutton(
            transfer,
            text="是",
            value="是",
            variable=self.transfer_warehouse,
        ).pack(side="left", padx=(0, 28))
        ttk.Radiobutton(
            transfer,
            text="否",
            value="否",
            variable=self.transfer_warehouse,
        ).pack(side="left")

        self._label(panel, 4, "采购平台：")
        self.platform_combo = ttk.Combobox(
            panel,
            textvariable=self.platform,
            values=_PLATFORMS,
            state="readonly",
        )
        self.platform_combo.grid(row=4, column=1, sticky="ew", pady=8)

        self._label(panel, 5, "采购订单编号：")
        ttk.Entry(
            panel,
            textvariable=self.purchase_order_no,
        ).grid(row=5, column=1, sticky="ew", pady=8)

        self._label(panel, 6, "采购金额：", required=False)
        ttk.Entry(
            panel,
            textvariable=self.purchase_amount,
        ).grid(row=6, column=1, sticky="ew", pady=8)

        self._label(panel, 7, "佣金比例：", required=False)
        commission = ttk.Frame(panel)
        commission.grid(row=7, column=1, sticky="ew", pady=8)
        commission.columnconfigure(0, weight=1)
        ttk.Entry(
            commission,
            textvariable=self.commission,
        ).grid(row=0, column=0, sticky="ew")
        ttk.Label(commission, text="%").grid(row=0, column=1, padx=(7, 0))

        footer = ttk.Frame(panel)
        footer.grid(row=8, column=0, columnspan=2, sticky="ew", pady=(18, 0))
        ttk.Checkbutton(
            footer,
            text="记住本次内容",
            variable=self.remember,
        ).pack(side="left")
        ttk.Button(footer, text="保存", command=self._save).pack(
            side="right",
        )
        ttk.Button(footer, text="取消", command=self.destroy).pack(
            side="right",
            padx=(0, 8),
        )

    @staticmethod
    def _label(
        parent: ttk.Frame,
        row: int,
        text: str,
        *,
        required: bool = True,
    ) -> None:
        holder = ttk.Frame(parent)
        holder.grid(row=row, column=0, sticky="w", padx=(0, 12), pady=8)
        if required:
            ttk.Label(
                holder,
                text="*",
                foreground="#e5484d",
            ).pack(side="left")
        ttk.Label(holder, text=text).pack(side="left")

    def _save(self) -> None:
        platform = self.platform.get().strip()
        purchase_order_no = self.purchase_order_no.get().strip()
        if not platform:
            messagebox.showwarning("采购平台", "请选择采购平台。", parent=self)
            return
        if not purchase_order_no:
            messagebox.showwarning(
                "采购订单编号",
                "请输入采购订单编号。",
                parent=self,
            )
            return
        try:
            amount = Decimal(self.purchase_amount.get().strip() or "0")
        except InvalidOperation:
            messagebox.showwarning("采购金额", "采购金额必须是数字。", parent=self)
            return
        if amount < 0:
            messagebox.showwarning("采购金额", "采购金额不能小于 0。", parent=self)
            return
        try:
            commission = Decimal(self.commission.get().strip() or "0")
        except InvalidOperation:
            messagebox.showwarning("佣金比例", "佣金比例必须是数字。", parent=self)
            return
        if commission < 0 or commission > 100:
            messagebox.showwarning(
                "佣金比例",
                "佣金比例必须在 0 到 100 之间。",
                parent=self,
            )
            return
        self.on_save(
            {
                "purchase_mode": self.purchase_mode.get(),
                "flag": self.flag.get(),
                "transfer_warehouse": self.transfer_warehouse.get() == "是",
                "platform": platform,
                "purchase_order_no": purchase_order_no,
                "purchase_amount": amount,
                "commission": commission,
                "remember": self.remember.get(),
            }
        )
        self.destroy()


class ManualShipmentDialog(tk.Toplevel):
    def __init__(
        self,
        parent: tk.Widget,
        *,
        order_id: str,
        on_save: Callable[[list[dict[str, str]]], None],
    ) -> None:
        super().__init__(parent)
        self.on_save = on_save
        self.title("手动发货")
        self.geometry("920x470")
        self.minsize(820, 420)
        self.transient(parent.winfo_toplevel())
        self.grab_set()
        self.package_rows: list[tuple[ttk.Frame, tk.StringVar, tk.StringVar]] = []
        self.package_host: ttk.Frame

        panel = ttk.Frame(self, padding=16)
        panel.pack(fill="both", expand=True)
        panel.columnconfigure(0, weight=1)
        panel.rowconfigure(5, weight=1)
        notice = tk.Frame(panel, bg="#fff7e8", padx=14, pady=10)
        notice.grid(row=0, column=0, sticky="ew")
        tk.Label(
            notice,
            text=(
                "1、软件自动同步上家物流并发货，多包裹请添加店铺包裹后点击发货按钮发货\n"
                "2、请仔细核对添加包裹的物流信息是否正确"
            ),
            bg="#fff7e8",
            fg="#f08a35",
            justify="left",
            anchor="w",
            font=("Microsoft YaHei UI", 9),
        ).pack(fill="x")
        ttk.Label(panel, text="订单编号：" + order_id).grid(
            row=1,
            column=0,
            sticky="w",
            pady=(14, 6),
        )
        ttk.Label(
            panel,
            text="暂无上家包裹",
            foreground="#e5484d",
        ).grid(row=2, column=0, sticky="w", pady=(4, 10))
        toolbar = ttk.Frame(panel)
        toolbar.grid(row=3, column=0, sticky="w", pady=(0, 8))
        ttk.Label(toolbar, text="店铺包裹：").pack(side="left")
        ttk.Button(toolbar, text="+", width=3, command=self._add_package).pack(
            side="left"
        )
        self.package_host = ttk.Frame(panel)
        self.package_host.grid(row=5, column=0, sticky="nsew")
        self.package_host.columnconfigure(0, weight=1)
        actions = ttk.Frame(panel)
        actions.grid(row=6, column=0, pady=(16, 0))
        ttk.Button(actions, text="发货", command=self._save).pack(
            side="left"
        )
        ttk.Button(actions, text="取消", command=self.destroy).pack(
            side="left",
            padx=(10, 0),
        )
        self._add_package()

    def _add_package(self) -> None:
        row = ttk.Frame(self.package_host)
        row.grid(
            row=len(self.package_rows),
            column=0,
            sticky="ew",
            pady=4,
        )
        row.columnconfigure(2, weight=1)
        ttk.Label(row, text=f"店铺包裹{len(self.package_rows) + 1}：").grid(
            row=0,
            column=0,
            sticky="w",
            padx=(0, 10),
        )
        company = tk.StringVar()
        tracking = tk.StringVar()
        ttk.Combobox(
            row,
            textvariable=company,
            values=("快递公司", "顺丰速运", "中通快递", "圆通速递", "韵达快递"),
            width=22,
        ).grid(row=0, column=1, sticky="w", padx=(0, 10))
        ttk.Entry(row, textvariable=tracking).grid(
            row=0,
            column=2,
            sticky="ew",
            padx=(0, 10),
        )
        ttk.Button(
            row,
            text="删除",
            command=lambda current=row: self._remove_package(current),
        ).grid(row=0, column=3)
        self.package_rows.append((row, company, tracking))

    def _remove_package(self, row: ttk.Frame) -> None:
        found = next(
            (item for item in self.package_rows if item[0] is row),
            None,
        )
        if found is None:
            return
        self.package_rows.remove(found)
        row.destroy()
        for index, (current, _company, _tracking) in enumerate(self.package_rows):
            current.grid_configure(row=index)

    def _save(self) -> None:
        packages = [
            {
                "delivery_id": company.get().strip(),
                "waybill_id": tracking.get().strip(),
            }
            for _row, company, tracking in self.package_rows
            if company.get().strip() or tracking.get().strip()
        ]
        if not packages:
            messagebox.showwarning("店铺包裹", "请至少填写一个店铺包裹。", parent=self)
            return
        for package in packages:
            if not package["delivery_id"] or not package["waybill_id"]:
                messagebox.showwarning(
                    "店铺包裹",
                    "快递公司和快递单号必须完整填写。",
                    parent=self,
                )
                return
        self.on_save(packages)
        self.destroy()


__all__ = [
    "ManualShipmentDialog",
    "ProductTitleEditDialog",
    "PurchaseInfoDialog",
]
