from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Any, Callable

from .image_viewer import bind_remote_image_label


class ProductCardList(tk.Frame):
    def __init__(
        self,
        parent: tk.Widget,
        on_action: Callable[[str, int], None],
    ) -> None:
        super().__init__(parent)
        self.on_action = on_action
        self._rows: dict[str, dict[str, Any]] = {}
        self._selected: set[str] = set()
        self._double_callback = None
        self._right_callback = None
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self.canvas = tk.Canvas(self, bg="#ffffff", highlightthickness=0)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.scrollbar = ttk.Scrollbar(
            self,
            orient="vertical",
            command=self.canvas.yview,
        )
        self.scrollbar.grid(row=0, column=1, sticky="ns")
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.body = tk.Frame(self.canvas, bg="#ffffff")
        self.window_id = self.canvas.create_window((0, 0), window=self.body, anchor="nw")
        self.body.bind(
            "<Configure>",
            lambda _event: self.canvas.configure(
                scrollregion=self.canvas.bbox("all")
            ),
        )
        self.canvas.bind(
            "<Configure>",
            lambda event: self.canvas.itemconfigure(
                self.window_id,
                width=event.width,
            ),
        )
        self.canvas.bind("<MouseWheel>", self._on_mousewheel)

    def _on_mousewheel(self, event) -> None:
        self.canvas.yview_scroll(int(-event.delta / 120), "units")

    def get_children(self) -> tuple[str, ...]:
        return tuple(self._rows)

    def selection(self) -> tuple[str, ...]:
        return tuple(self._selected)

    def selection_set(self, values) -> None:
        self._selected = {str(value) for value in values if str(value) in self._rows}
        self._refresh_selection()

    def selection_remove(self, *values) -> None:
        for value in values:
            self._selected.discard(str(value))
        self._refresh_selection()

    def identify_row(self, _y: int) -> str:
        return next(iter(self._selected), "")

    def identify_column(self, _x: int) -> str:
        return "#1"

    def yview(self, *_args):
        return self.canvas.yview(*_args)

    def xview(self, *_args):
        return ""

    def bind(self, sequence=None, func=None, add=None):  # noqa: A003
        if sequence == "<Double-1>":
            self._double_callback = func
            return ""
        if sequence == "<Button-3>":
            self._right_callback = func
            return ""
        return super().bind(sequence, func, add)

    def delete(self, *items) -> None:
        if not items:
            self.set_rows([])
            return
        removing = {str(item) for item in items}
        for iid in removing:
            self._rows.pop(iid, None)
            self._selected.discard(iid)
        self._redraw()

    def set_rows(self, rows: list[dict[str, Any]]) -> None:
        self._rows = {str(row["iid"]): row for row in rows}
        self._selected.intersection_update(self._rows)
        self._redraw()

    def _redraw(self) -> None:
        for child in self.body.winfo_children():
            child.destroy()
        if not self._rows:
            tk.Label(
                self.body,
                text="暂无数据",
                bg="#ffffff",
                fg="#95a1b2",
                font=("Microsoft YaHei UI", 11),
                pady=70,
            ).pack(fill="x")
        else:
            for iid, row in self._rows.items():
                self._build_card(iid, row)
        self.body.update_idletasks()
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _build_card(self, iid: str, item: dict[str, Any]) -> None:
        base = "#ffffff"
        card = tk.Frame(
            self.body,
            bg=base,
            highlightbackground="#dce3eb",
            highlightthickness=1,
        )
        card.pack(fill="x", pady=(0, 6))
        card._iid = iid  # type: ignore[attr-defined]
        top = tk.Frame(card, bg="#f8fafc", padx=8, pady=6)
        top.pack(fill="x")
        var = tk.BooleanVar(value=iid in self._selected)
        tk.Checkbutton(
            top,
            variable=var,
            command=lambda: self._toggle(iid, var.get()),
            bg="#f8fafc",
            activebackground="#f8fafc",
            bd=0,
            highlightthickness=0,
        ).pack(side="left")
        tk.Label(
            top,
            text=f"创建时间： {item.get('created_at', '')}",
            bg="#f8fafc",
            fg="#657386",
            font=("Microsoft YaHei UI", 9),
        ).pack(side="left", padx=4)
        tk.Button(
            top,
            text=item.get("ignored_text", "忽略商品"),
            command=lambda: self._action("ignore", iid),
            bg="#f8fafc",
            fg="#66809b",
            activebackground="#e9eff6",
            relief="flat",
            bd=0,
            cursor="hand2",
            font=("Microsoft YaHei UI", 8),
        ).pack(side="right")
        row = tk.Frame(card, bg=base, padx=8, pady=8)
        row.pack(fill="x")
        for column, weight in enumerate((28, 13, 10, 9, 8, 10, 10, 12)):
            row.columnconfigure(column, weight=weight, uniform="product-card")

        product = tk.Frame(row, bg=base)
        product.grid(row=0, column=0, sticky="nw", padx=(0, 8))
        image_label = tk.Label(
            product,
            text="商品\n图片",
            bg="#edf1f6",
            fg="#9aa8b8",
            width=8,
            height=4,
            font=("Microsoft YaHei UI", 8),
            bd=0,
            highlightthickness=1,
            highlightbackground="#d7e0ea",
        )
        image_label.pack(side="left", padx=(0, 10))
        bind_remote_image_label(
            image_label,
            str(item.get("image_url") or ""),
            size=(76, 76),
        )
        product_text = tk.Frame(product, bg=base)
        product_text.pack(side="left", fill="both", expand=True)
        title_row = tk.Frame(product_text, bg=base)
        title_row.pack(fill="x", anchor="w")
        tk.Label(
            title_row,
            text=item.get("title", ""),
            bg=base,
            fg="#26364b",
            anchor="w",
            justify="left",
            wraplength=420,
            font=("Microsoft YaHei UI", 9),
        ).pack(side="left", anchor="w")
        for label, action in (
            ("复制标题", "copy_title"),
            ("改标题", "edit_title"),
        ):
            tk.Button(
                title_row,
                text=label,
                command=lambda key=action: self._action(key, iid),
                bg="#f8fbff",
                fg="#3f70bd",
                activebackground="#e7effa",
                relief="flat",
                bd=0,
                padx=5,
                pady=1,
                cursor="hand2",
                font=("Microsoft YaHei UI", 7),
            ).pack(side="left", padx=(4, 0))
        merchant_row = tk.Frame(product_text, bg=base)
        merchant_row.pack(anchor="w", pady=(5, 0))
        tk.Label(
            merchant_row,
            text=f"商家编码/上家ID: {item.get('merchant_code', '') or '—'}",
            bg=base,
            fg="#7e8a9b",
            anchor="w",
            font=("Microsoft YaHei UI", 8),
        ).pack(side="left")
        tk.Button(
            merchant_row,
            text="复制",
            command=lambda: self._action("copy_merchant_code", iid),
            bg=base,
            fg="#3f70bd",
            activebackground="#e7effa",
            relief="flat",
            bd=0,
            padx=4,
            pady=0,
            cursor="hand2",
            font=("Microsoft YaHei UI", 7),
        ).pack(side="left", padx=(4, 0))
        product_id_row = tk.Frame(product_text, bg=base)
        product_id_row.pack(anchor="w", pady=(2, 0))
        tk.Label(
            product_id_row,
            text=f"商品ID: {item.get('product_id', '')}",
            bg=base,
            fg="#7e8a9b",
            anchor="w",
            font=("Microsoft YaHei UI", 8),
        ).pack(side="left")
        tk.Button(
            product_id_row,
            text="复制",
            command=lambda: self._action("copy_product_id", iid),
            bg=base,
            fg="#3f70bd",
            activebackground="#e7effa",
            relief="flat",
            bd=0,
            padx=4,
            pady=0,
            cursor="hand2",
            font=("Microsoft YaHei UI", 7),
        ).pack(side="left", padx=(4, 0))

        values = (
            ("store", item.get("store", "")),
            ("price", item.get("price", "")),
            ("sold", item.get("sold", "0")),
            ("has_sku", item.get("has_sku", "否")),
            ("status", item.get("status", "")),
            ("group", item.get("group", "")),
            ("related", item.get("related", "未关联")),
        )
        for column, (key, value) in enumerate(values, 1):
            tk.Label(
                row,
                text=value,
                bg="#fff3eb" if key == "store" else base,
                fg="#e27b28" if key == "store" else "#5e6b7d",
                anchor="center",
                justify="center",
                font=("Microsoft YaHei UI", 9),
            ).grid(row=0, column=column, sticky="new", padx=4, pady=3)
        tk.Button(
            row,
            text="关联/更换上家",
            command=lambda: self._action("link", iid),
            bg="#3f7de8",
            fg="white",
            activebackground="#285fbd",
            activeforeground="white",
            relief="flat",
            bd=0,
            padx=10,
            pady=4,
            cursor="hand2",
            font=("Microsoft YaHei UI", 8),
        ).grid(row=0, column=8, sticky="n", padx=4, pady=3)

        def select(_event=None):
            self._selected.add(iid)
            self._refresh_selection()
            self._action("select", iid)

        def double(event=None):
            select()
            if self._double_callback is not None:
                self._double_callback(event)

        for widget in (
            card,
            top,
            row,
            product,
            product_text,
            title_row,
            merchant_row,
            product_id_row,
        ):
            widget.bind("<Button-1>", select)
            widget.bind("<Double-1>", double)
            widget.bind("<Button-3>", lambda event: self._right(iid, event))

    def _toggle(self, iid: str, selected: bool) -> None:
        if selected:
            self._selected.add(iid)
        else:
            self._selected.discard(iid)
        self._refresh_selection()
        self._action("selection", iid)

    def _refresh_selection(self) -> None:
        for card in self.body.winfo_children():
            iid = getattr(card, "_iid", None)
            if iid is None:
                continue
            card.configure(
                highlightbackground="#5e8fe8" if iid in self._selected else "#dce3eb",
                highlightthickness=2 if iid in self._selected else 1,
            )

    def _action(self, action: str, iid: str) -> None:
        row = self._rows.get(iid)
        if row is not None:
            self.on_action(action, int(row.get("index", iid)))

    def _right(self, iid: str, event) -> None:
        self._selected = {iid}
        self._refresh_selection()
        if self._right_callback is not None:
            self._right_callback(event)
