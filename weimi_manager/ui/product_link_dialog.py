from __future__ import annotations

import json
import re
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any

from ..source_1688 import (
    Link1688Collector,
    Offer1688Link,
    Source1688Error,
    parse_1688_links,
)
from .dialogs import SimpleFieldsDialog
from .image_viewer import (
    bind_remote_image_label,
    bind_tree_row_images,
    set_tree_row_thumbnail,
)


def _json_list(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    try:
        payload = json.loads(str(value or "[]"))
    except (TypeError, ValueError):
        return []
    return [item for item in payload if isinstance(item, dict)] if isinstance(payload, list) else []


def _sku_attrs(sku: dict[str, Any]) -> str:
    attrs = (
        sku.get("sku_attrs")
        or sku.get("attributes")
        or sku.get("specAttrs")
        or []
    )
    values: list[str] = []
    if isinstance(attrs, dict):
        attrs = [
            {"attr_key": key, "attr_value": value}
            for key, value in attrs.items()
        ]
    if isinstance(attrs, list):
        for item in attrs:
            if isinstance(item, dict):
                key = str(
                    item.get("attr_key")
                    or item.get("key")
                    or item.get("name")
                    or ""
                ).strip()
                value = str(
                    item.get("attr_value")
                    or item.get("value")
                    or ""
                ).strip()
                if key and value:
                    values.append(f"{key}:{value}")
                elif value:
                    values.append(value)
            elif str(item or "").strip():
                values.append(str(item).strip())
    return "；".join(values)


def _sku_image(sku: dict[str, Any]) -> str:
    for key in (
        "thumb_img",
        "thumb_url",
        "image",
        "img",
        "sku_image",
        "sku_thumb_url",
    ):
        value = str(sku.get(key) or "").strip()
        if value.startswith(("http://", "https://")):
            return value
    return ""


def _normalized_attr_values(sku: dict[str, Any]) -> set[str]:
    text = _sku_attrs(sku)
    parts = re.split(r"[；;，,|]+", text)
    values: set[str] = set()
    for part in parts:
        if ":" in part:
            _key, value = part.split(":", 1)
        elif "：" in part:
            _key, value = part.split("：", 1)
        else:
            value = part
        normalized = re.sub(r"[\s_\-]+", "", value).casefold()
        if normalized:
            values.add(normalized)
    return values


def sku_match_score(
    store_sku: dict[str, Any],
    source_sku: dict[str, Any],
) -> float:
    left = _normalized_attr_values(store_sku)
    right = _normalized_attr_values(source_sku)
    score = float(len(left & right) * 10)
    if left and right and left == right:
        score += 25
    left_price = int(store_sku.get("sale_price") or store_sku.get("price") or 0)
    right_price = int(source_sku.get("sale_price") or source_sku.get("price") or 0)
    if left_price and right_price:
        difference = abs(left_price - right_price)
        if difference == 0:
            score += 3
        elif difference <= max(100, min(left_price, right_price) // 10):
            score += 1
    return score


def auto_match_skus(
    store_skus: list[dict[str, Any]],
    source_skus: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    used_sources: set[str] = set()
    for store_sku in store_skus:
        store_sku_id = str(store_sku.get("sku_id") or "").strip()
        if not store_sku_id:
            continue
        ranked = sorted(
            (
                (sku_match_score(store_sku, source), source)
                for source in source_skus
            ),
            key=lambda item: item[0],
            reverse=True,
        )
        if not ranked or ranked[0][0] <= 0:
            continue
        selected = ranked[0][1]
        source_id = str(
            selected.get("out_sku_id")
            or selected.get("sku_id")
            or ""
        )
        if source_id and source_id in used_sources:
            continue
        result[store_sku_id] = selected
        if source_id:
            used_sources.add(source_id)
    return result


class ProductPurchaseLinkDialog(tk.Toplevel):
    def __init__(
        self,
        parent: tk.Widget,
        *,
        db,
        window,
        store_id: int,
        product: dict[str, Any],
        on_saved=None,
    ) -> None:
        super().__init__(parent)
        self.db = db
        self.window = window
        self.store_id = int(store_id)
        self.product = dict(product)
        self.product_id = str(product.get("product_id") or "")
        self.on_saved = on_saved
        self._deleted_link_ids: set[int] = set()
        self._updating_link_selection = False
        self.links = self.db.list_product_purchase_links(
            self.store_id,
            self.product_id,
        )
        for link in self.links:
            link["source_skus"] = _json_list(link.get("source_skus_json"))
            try:
                link["address_info"] = json.loads(
                    link.get("address_info_json") or "{}"
                )
            except (TypeError, ValueError):
                link["address_info"] = {}
        self.mapping_rows = self.db.list_product_sku_mappings(
            self.store_id,
            self.product_id,
        )
        self.mappings = {
            str(row["store_sku_id"]): {
                "purchase_link_id": int(row["purchase_link_id"]),
                "purchase_sku_id": str(row.get("purchase_sku_id") or ""),
                "purchase_attrs": _json_list(row.get("purchase_attrs_json")),
                "purchase_price": int(row.get("purchase_price") or 0),
                "purchase_image": str(row.get("purchase_image") or ""),
            }
            for row in self.mapping_rows
        }
        self.store_skus = self._read_store_skus(self.product)
        self.active_link_index: int | None = self.links and 0 or None
        self._store_row_images: dict[str, str] = {}
        self._source_row_images: dict[str, str] = {}

        self.title(f"关联商品规格 · {self.product_id}")
        self.geometry("1460x900")
        self.minsize(1120, 720)
        self.transient(parent.winfo_toplevel())
        self.grab_set()
        ttk.Style(self).configure(
            "PurchaseLink.Treeview",
            rowheight=52,
        )
        self.columnconfigure(0, weight=1)
        self.rowconfigure(3, weight=1)
        self._build_header()
        self._build_link_area()
        self._build_link_editor()
        self._build_mapping_area()
        self._build_actions()
        if self.links:
            self._select_link(0)
        self._render_store_skus()

    @staticmethod
    def _read_store_skus(product: dict[str, Any]) -> list[dict[str, Any]]:
        try:
            payload = json.loads(product.get("raw_json") or "{}")
        except (TypeError, ValueError):
            payload = {}
        skus = payload.get("skus") if isinstance(payload, dict) else []
        if not isinstance(skus, list):
            skus = []
        normalized: list[dict[str, Any]] = []
        for index, item in enumerate(skus, 1):
            if not isinstance(item, dict):
                continue
            sku = dict(item)
            sku["sku_id"] = str(
                sku.get("sku_id")
                or sku.get("id")
                or f"DEFAULT-{index}"
            )
            normalized.append(sku)
        return normalized

    def _build_header(self) -> None:
        header = ttk.Frame(self, padding=(16, 12))
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(1, weight=1)
        image = tk.Label(
            header,
            text="商品\n图片",
            bg="#eef2f7",
            fg="#98a6b7",
            width=8,
            height=4,
            font=("Microsoft YaHei UI", 8),
            bd=0,
        )
        image.grid(row=0, column=0, rowspan=2, sticky="nw", padx=(0, 12))
        image_url = self._product_image_url()
        if image_url:
            bind_remote_image_label(image, image_url, size=(76, 76))
        ttk.Label(
            header,
            text=str(self.product.get("title") or "商品"),
            font=("Microsoft YaHei UI", 10, "bold"),
        ).grid(row=0, column=1, sticky="w")
        ttk.Label(
            header,
            text=(
                f"商品ID：{self.product_id}"
            ),
            style="Subtitle.TLabel",
        ).grid(row=1, column=1, sticky="w", pady=(4, 0))
        ttk.Label(
            header,
            text="已同步",
            foreground="#39a062",
        ).grid(row=2, column=1, sticky="w", pady=(3, 0))
        ttk.Button(
            header,
            text="刷新店铺SKU",
            command=self._refresh_store_skus,
        ).grid(row=2, column=0, sticky="w", padx=(0, 12))
        ttk.Button(
            header,
            text="新增链接",
            command=self._add_link,
            style="Primary.TButton",
        ).grid(row=0, column=2, rowspan=2, sticky="e")

    def _product_image_url(self) -> str:
        direct = _sku_image(self.product)
        if direct:
            return direct
        try:
            raw = json.loads(self.product.get("raw_json") or "{}")
        except (TypeError, ValueError):
            raw = {}
        images = raw.get("head_imgs") if isinstance(raw, dict) else []
        if isinstance(images, list):
            for item in images:
                value = (
                    str(item.get("url") or "")
                    if isinstance(item, dict)
                    else str(item or "")
                )
                if value.startswith(("http://", "https://")):
                    return value
        return ""

    def _build_link_area(self) -> None:
        panel = ttk.LabelFrame(self, text="采购链接", padding=8)
        panel.grid(row=1, column=0, sticky="ew", padx=16)
        panel.columnconfigure(0, weight=1)
        toolbar = ttk.Frame(panel)
        toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        ttk.Button(toolbar, text="删除链接", command=self._delete_link).pack(
            side="left", padx=6
        )
        ttk.Button(toolbar, text="获取SKU", command=self._fetch_selected_link_skus).pack(
            side="left"
        )
        ttk.Button(toolbar, text="手动添加采购SKU", command=self._add_manual_source_sku).pack(
            side="left", padx=6
        )
        ttk.Button(toolbar, text="关联采购地址", command=self._edit_address).pack(
            side="left"
        )
        columns = (
            "sequence",
            "updated",
            "url",
            "platform",
            "transfer",
            "commission",
            "note",
            "operation",
        )
        self.link_tree = ttk.Treeview(
            panel,
            columns=columns,
            show="headings",
            height=4,
            selectmode="browse",
        )
        labels = {
            "sequence": ("编号", 45),
            "updated": ("修改时间", 145),
            "url": ("采购链接", 430),
            "platform": ("采购平台", 85),
            "transfer": ("使用中转仓", 90),
            "commission": ("佣金比例", 85),
            "note": ("链接备注", 190),
            "operation": ("操作", 210),
        }
        for key, (text, width) in labels.items():
            self.link_tree.heading(key, text=text)
            self.link_tree.column(key, width=width, anchor="w")
        self.link_tree.grid(row=1, column=0, sticky="ew")
        self.link_tree.bind("<<TreeviewSelect>>", self._on_link_selected)
        self.link_tree.bind(
            "<Double-1>",
            lambda _event: self._fetch_selected_link_skus(),
        )

    def _build_link_editor(self) -> None:
        panel = ttk.LabelFrame(self, text="当前链接设置", padding=8)
        panel.grid(row=2, column=0, sticky="ew", padx=16, pady=(8, 0))
        for column in range(8):
            panel.columnconfigure(column, weight=1)
        self.url_var = tk.StringVar()
        self.platform_var = tk.StringVar(value="1688")
        self.transfer_var = tk.BooleanVar()
        self.commission_var = tk.StringVar(value="0")
        self.note_var = tk.StringVar()
        ttk.Label(panel, text="链接").grid(row=0, column=0, sticky="w")
        ttk.Entry(panel, textvariable=self.url_var).grid(
            row=1, column=0, columnspan=3, sticky="ew", padx=(0, 8)
        )
        ttk.Label(panel, text="平台").grid(row=0, column=3, sticky="w")
        ttk.Combobox(
            panel,
            textvariable=self.platform_var,
            values=("淘宝", "淘特", "1688", "拼多多", "京东"),
            state="readonly",
            width=10,
        ).grid(row=1, column=3, sticky="ew", padx=(0, 8))
        ttk.Checkbutton(
            panel,
            text="使用中转仓",
            variable=self.transfer_var,
        ).grid(row=1, column=4, sticky="w", padx=(0, 8))
        ttk.Label(panel, text="佣金比例 %").grid(row=0, column=5, sticky="w")
        ttk.Entry(panel, textvariable=self.commission_var, width=10).grid(
            row=1, column=5, sticky="ew", padx=(0, 8)
        )
        ttk.Label(panel, text="链接备注").grid(row=0, column=6, sticky="w")
        ttk.Entry(panel, textvariable=self.note_var).grid(
            row=1, column=6, columnspan=2, sticky="ew"
        )
        ttk.Button(
            panel,
            text="保存当前链接设置",
            command=self._capture_link_editor,
        ).grid(row=2, column=7, sticky="e", pady=(6, 0))

    def _build_mapping_area(self) -> None:
        panel = ttk.Frame(self, padding=(16, 8, 16, 0))
        panel.grid(row=3, column=0, sticky="nsew")
        panel.columnconfigure(0, weight=1)
        panel.rowconfigure(1, weight=1)
        toolbar = ttk.Frame(panel)
        toolbar.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        ttk.Button(toolbar, text="匹配选中规格", command=self._match_selected).pack(
            side="left"
        )
        ttk.Button(toolbar, text="自动匹配", command=self._auto_match).pack(
            side="left", padx=6
        )
        ttk.Button(toolbar, text="清除匹配", command=self._clear_selected_mapping).pack(
            side="left"
        )
        ttk.Button(toolbar, text="清空采购规格", command=self._clear_all_mappings).pack(
            side="left", padx=6
        )

        store_panel = ttk.LabelFrame(panel, text="商品规格与采购规格映射", padding=6)
        store_panel.grid(
            row=1,
            column=0,
            columnspan=2,
            sticky="nsew",
        )
        store_panel.rowconfigure(0, weight=1)
        store_panel.columnconfigure(0, weight=1)
        store_columns = (
            "sequence",
            "attrs",
            "store_price",
            "purchase_link",
            "purchase_price",
            "purchase_image",
            "purchase_attrs",
        )
        self.store_tree = ttk.Treeview(
            store_panel,
            columns=store_columns,
            show=("tree", "headings"),
            selectmode="extended",
            style="PurchaseLink.Treeview",
        )
        self.store_tree.heading("#0", text="图片")
        self.store_tree.column("#0", width=54, minwidth=54, stretch=False)
        for key, label, width in (
            ("sequence", "序号", 45),
            ("attrs", "店铺商品属性", 280),
            ("store_price", "价格", 85),
            ("purchase_link", "采购链接", 120),
            ("purchase_price", "采购价格", 90),
            ("purchase_image", "采购图片", 160),
            ("purchase_attrs", "采购商品属性", 300),
        ):
            self.store_tree.heading(key, text=label)
            self.store_tree.column(key, width=width, anchor="w")
        self.store_tree.grid(row=0, column=0, sticky="nsew")
        self.store_tree.bind(
            "<Double-1>",
            self._choose_mapping_for_store_row,
        )
        bind_tree_row_images(
            self.store_tree,
            lambda iid: self._store_row_images.get(iid, ""),
        )

        source_panel = ttk.LabelFrame(panel, text="采购商品SKU", padding=6)
        source_panel.grid(row=1, column=1, sticky="nsew", padx=(5, 0))
        source_panel.grid_remove()
        source_panel.rowconfigure(0, weight=1)
        source_panel.columnconfigure(0, weight=1)
        source_columns = ("link", "sku_id", "attrs", "price", "image")
        self.source_tree = ttk.Treeview(
            source_panel,
            columns=source_columns,
            show=("tree", "headings"),
            selectmode="browse",
            style="PurchaseLink.Treeview",
        )
        self.source_tree.heading("#0", text="图片")
        self.source_tree.column("#0", width=54, minwidth=54, stretch=False)
        for key, label, width in (
            ("link", "采购链接", 115),
            ("sku_id", "采购SKU", 145),
            ("attrs", "采购商品属性", 260),
            ("price", "采购价", 85),
            ("image", "采购图", 180),
        ):
            self.source_tree.heading(key, text=label)
            self.source_tree.column(key, width=width, anchor="w")
        self.source_tree.grid(row=0, column=0, sticky="nsew")
        bind_tree_row_images(
            self.source_tree,
            lambda iid: self._source_row_images.get(iid, ""),
        )

    def _build_actions(self) -> None:
        actions = ttk.Frame(self, padding=(16, 10))
        actions.grid(row=4, column=0, sticky="ew")
        ttk.Button(actions, text="取消", command=self.destroy).pack(side="right")
        ttk.Button(
            actions,
            text="保存",
            command=self._save,
            style="Primary.TButton",
        ).pack(side="right", padx=(0, 8))

    def _render_links(self) -> None:
        self.link_tree.delete(*self.link_tree.get_children())
        for index, link in enumerate(self.links):
            self.link_tree.insert(
                "",
                "end",
                iid=str(index),
                values=self._link_values(index, link),
            )
        if self.links and self.active_link_index is not None:
            iid = str(self.active_link_index)
            if self.link_tree.exists(iid):
                self._updating_link_selection = True
                self.link_tree.selection_set(iid)
                self.after_idle(
                    lambda: setattr(self, "_updating_link_selection", False)
                )

    @staticmethod
    def _link_values(
        index: int,
        link: dict[str, Any],
    ) -> tuple[Any, ...]:
        return (
            index + 1,
            str(link.get("updated_at") or "")[:19].replace("T", " "),
            link.get("source_url") or "",
            link.get("platform") or "",
            "是" if link.get("use_transfer_warehouse") else "否",
            f"{float(link.get('commission_pct') or 0):g} %",
            link.get("note") or "",
            (
                f"获取SKU({len(link.get('source_skus') or [])}) · "
                "删除 · 未关联地址"
            ),
        )

    def _render_store_skus(self) -> None:
        self.store_tree.delete(*self.store_tree.get_children())
        self._store_row_images.clear()
        for index, sku in enumerate(self.store_skus):
            sku_id = str(sku.get("sku_id") or sku.get("id") or "")
            mapping = self.mappings.get(sku_id) or {}
            mapped_link = ""
            mapped_price = ""
            mapped_image = ""
            mapped_attrs = ""
            if mapping:
                link_id = int(mapping.get("purchase_link_id") or 0)
                link_index = next(
                    (
                        index
                        for index, link in enumerate(self.links)
                        if int(link.get("id") or 0) == link_id
                    ),
                    None,
                )
                if link_index is not None:
                    link = self.links[link_index]
                    mapped_link = (
                        f"#{link_index + 1} "
                        f"{link.get('platform') or ''}"
                    )
                    for source in link.get("source_skus") or []:
                        source_id = str(
                            source.get("out_sku_id")
                            or source.get("sku_id")
                            or ""
                        )
                        if source_id == str(
                            mapping.get("purchase_sku_id") or ""
                        ):
                            source_price = int(
                                source.get("sale_price")
                                or source.get("price")
                                or 0
                            )
                            mapped_price = f"¥{source_price / 100:.2f}"
                            mapped_image = _sku_image(source)
                            mapped_attrs = _sku_attrs(source)
                            break
            iid = str(index)
            image_url = _sku_image(sku)
            self._store_row_images[iid] = image_url
            self.store_tree.insert(
                "",
                "end",
                iid=iid,
                values=(
                    index + 1,
                    _sku_attrs(sku) or "默认规格",
                    f"¥{int(sku.get('sale_price') or sku.get('price') or 0) / 100:.2f}",
                    mapped_link or "请选择采购链接",
                    mapped_price or "—",
                    mapped_image or "—",
                    mapped_attrs or "请选择采购商品属性",
                ),
            )
            set_tree_row_thumbnail(
                self.store_tree,
                iid,
                image_url,
            )

    def _render_source_skus(self) -> None:
        self.source_tree.delete(*self.source_tree.get_children())
        self._source_row_images.clear()
        if self.active_link_index is None:
            return
        link = self.links[self.active_link_index]
        for index, sku in enumerate(link.get("source_skus") or []):
            sku_id = str(
                sku.get("out_sku_id")
                or sku.get("sku_id")
                or f"SKU-{index + 1}"
            )
            iid = str(index)
            image_url = _sku_image(sku)
            self._source_row_images[iid] = image_url
            self.source_tree.insert(
                "",
                "end",
                iid=iid,
                values=(
                    f"#{self.active_link_index + 1}",
                    sku_id,
                    _sku_attrs(sku) or "默认规格",
                    f"¥{int(sku.get('sale_price') or sku.get('price') or 0) / 100:.2f}",
                    image_url,
                ),
            )
            set_tree_row_thumbnail(
                self.source_tree,
                iid,
                image_url,
            )

    def _choose_mapping_for_store_row(self, event=None) -> None:
        iid = (
            self.store_tree.identify_row(event.y)
            if event is not None
            else (self.store_tree.selection() or [""])[0]
        )
        if not iid or int(iid) >= len(self.store_skus):
            return
        store_sku = self.store_skus[int(iid)]
        store_sku_id = str(
            store_sku.get("sku_id")
            or store_sku.get("id")
            or ""
        )
        options: dict[str, dict[str, Any]] = {}
        for link_index, link in enumerate(self.links):
            source_skus = list(link.get("source_skus") or [])
            if not source_skus:
                source_skus = [{}]
            for source in source_skus:
                attrs = _sku_attrs(source) or "默认规格"
                price = int(
                    source.get("sale_price")
                    or source.get("price")
                    or 0
                )
                label = (
                    f"#{link_index + 1} {link.get('platform') or ''} · "
                    f"{attrs} · ¥{price / 100:.2f}"
                )
                if label in options:
                    label += f" · {str(source.get('out_sku_id') or '')}"
                options[label] = {
                    "purchase_link_local_index": link_index,
                    "purchase_link_id": int(link.get("id") or 0),
                    "purchase_sku_id": str(
                        source.get("out_sku_id")
                        or source.get("sku_id")
                        or ""
                    ),
                    "purchase_attrs": (
                        source.get("sku_attrs")
                        or source.get("attributes")
                        or []
                    ),
                    "purchase_price": price,
                    "purchase_image": _sku_image(source),
                }
        if not options:
            messagebox.showinfo(
                "没有采购规格",
                "请先新增采购链接并获取采购SKU。",
                parent=self,
            )
            return
        dialog = tk.Toplevel(self)
        dialog.title("选择采购商品属性")
        dialog.geometry("720x190")
        dialog.transient(self)
        dialog.grab_set()
        panel = ttk.Frame(dialog, padding=18)
        panel.pack(fill="both", expand=True)
        panel.columnconfigure(1, weight=1)
        ttk.Label(panel, text="店铺SKU").grid(
            row=0,
            column=0,
            sticky="w",
            padx=(0, 12),
        )
        ttk.Label(
            panel,
            text=_sku_attrs(store_sku) or "默认规格",
            wraplength=520,
        ).grid(row=0, column=1, sticky="w")
        ttk.Label(panel, text="采购商品属性").grid(
            row=1,
            column=0,
            sticky="w",
            padx=(0, 12),
            pady=(14, 0),
        )
        selected = tk.StringVar(value=next(iter(options)))
        ttk.Combobox(
            panel,
            textvariable=selected,
            values=tuple(options),
            state="readonly",
        ).grid(
            row=1,
            column=1,
            sticky="ew",
            pady=(14, 0),
        )

        def apply_mapping() -> None:
            value = options.get(selected.get())
            if value is None:
                return
            self.mappings[store_sku_id] = dict(value)
            dialog.destroy()
            self._render_store_skus()

        actions = ttk.Frame(panel)
        actions.grid(
            row=2,
            column=0,
            columnspan=2,
            sticky="e",
            pady=(18, 0),
        )
        ttk.Button(actions, text="保存", command=apply_mapping).pack(
            side="right",
        )
        ttk.Button(actions, text="取消", command=dialog.destroy).pack(
            side="right",
            padx=(0, 8),
        )

    def _on_link_selected(self, _event=None) -> None:
        if self._updating_link_selection:
            return
        selection = self.link_tree.selection()
        if not selection:
            return
        self._capture_link_editor(show_warning=False)
        self._select_link(int(selection[0]))

    def _select_link(self, index: int) -> None:
        if not 0 <= index < len(self.links):
            return
        self.active_link_index = index
        link = self.links[index]
        self.url_var.set(str(link.get("source_url") or ""))
        self.platform_var.set(str(link.get("platform") or "1688"))
        self.transfer_var.set(bool(link.get("use_transfer_warehouse")))
        self.commission_var.set(str(float(link.get("commission_pct") or 0)))
        self.note_var.set(str(link.get("note") or ""))
        self._render_source_skus()

    def _capture_link_editor(self, *, show_warning: bool = True) -> None:
        if self.active_link_index is None:
            return
        try:
            commission = float(self.commission_var.get() or 0)
        except ValueError:
            if show_warning:
                messagebox.showwarning(
                    "佣金比例",
                    "佣金比例必须是数字。",
                    parent=self,
                )
            return
        if commission < 0 or commission > 100:
            if show_warning:
                messagebox.showwarning(
                    "佣金比例",
                    "佣金比例必须在 0 到 100 之间。",
                    parent=self,
                )
            return
        self.links[self.active_link_index].update(
            {
                "source_url": self.url_var.get().strip(),
                "platform": self.platform_var.get().strip(),
                "use_transfer_warehouse": self.transfer_var.get(),
                "commission_pct": commission,
                "note": self.note_var.get().strip(),
            }
        )
        iid = str(self.active_link_index)
        if self.link_tree.exists(iid):
            self.link_tree.item(
                iid,
                values=self._link_values(
                    self.active_link_index,
                    self.links[self.active_link_index],
                ),
            )

    def _add_link(self) -> None:
        self._capture_link_editor(show_warning=False)
        self.links.append(
            {
                "id": None,
                "source_url": "",
                "platform": "1688",
                "use_transfer_warehouse": False,
                "commission_pct": 0,
                "note": "",
                "address_info": {},
                "source_skus": [],
            }
        )
        self._render_links()
        self.link_tree.selection_set(str(len(self.links) - 1))
        self._select_link(len(self.links) - 1)

    def _delete_link(self) -> None:
        selection = self.link_tree.selection()
        if not selection:
            messagebox.showinfo("请选择采购链接", "请先选择要删除的链接。", parent=self)
            return
        index = int(selection[0])
        link = self.links[index]
        if link.get("id"):
            self._deleted_link_ids.add(int(link["id"]))
        del self.links[index]
        mapped_ids = {int(link.get("id") or 0)}
        self.mappings = {
            key: value
            for key, value in self.mappings.items()
            if int(value.get("purchase_link_id") or 0) not in mapped_ids
        }
        self.active_link_index = 0 if self.links else None
        self._render_links()
        self._render_store_skus()
        self._render_source_skus()
        if self.active_link_index is not None:
            self._select_link(self.active_link_index)

    def _fetch_selected_link_skus(self) -> None:
        self._capture_link_editor()
        if self.active_link_index is None:
            messagebox.showinfo("请选择采购链接", "请先新增或选择采购链接。", parent=self)
            return
        link = self.links[self.active_link_index]
        url = str(link.get("source_url") or "").strip()
        if not url:
            messagebox.showwarning("采购链接", "请先填写采购链接。", parent=self)
            return
        links = parse_1688_links(url)
        if not links:
            messagebox.showinfo(
                "需要手动录入",
                "当前内置自动读取支持1688链接。淘宝、拼多多等平台请使用“手动添加采购SKU”。",
                parent=self,
            )
            return
        accounts = self.db.list_procurement_accounts("1688")
        if not accounts:
            messagebox.showinfo(
                "需要1688采购账号",
                "请先在采购中心登录1688账号，再读取采购SKU。",
                parent=self,
            )
            return
        account = accounts[0]
        self.window.set_status("正在读取1688采购SKU…", busy=True)

        def work(progress):
            collector = Link1688Collector(
                self.db,
                self.db.path.parent,
            )
            results = collector.collect(
                account,
                [
                    Offer1688Link(
                        offer_id=links[0].offer_id,
                        url=links[0].url,
                    )
                ],
                progress=progress,
            )
            if not results or not results[0].get("ok"):
                error = (
                    results[0].get("error", "未读取到采购SKU")
                    if results
                    else "未读取到采购SKU"
                )
                raise Source1688Error(error)
            return results[0]["data"]

        def done(data: dict[str, Any]) -> None:
            link["source_url"] = data.get("source_url") or url
            link["platform"] = "1688"
            link["source_product_id"] = str(data.get("offer_id") or "")
            link["source_title"] = str(data.get("title") or "")
            link["source_skus"] = _json_list(data.get("skus"))
            self.platform_var.set("1688")
            self._render_links()
            self._render_source_skus()
            self.window.set_status(
                f"已读取 {len(link['source_skus'])} 个采购SKU",
                busy=False,
            )

        self.window.run_task(
            work,
            done,
            lambda error: messagebox.showerror(
                "读取采购SKU失败",
                error,
                parent=self,
            ),
            lambda message, current, total: self.window.set_status(
                f"{message} {current}/{total}",
                busy=True,
            ),
            log_title="读取商品采购SKU",
        )

    def _add_manual_source_sku(self) -> None:
        if self.active_link_index is None:
            messagebox.showinfo("请选择采购链接", "请先新增或选择采购链接。", parent=self)
            return

        def save(values: dict[str, str]) -> None:
            attrs_text = values.get("attrs", "").strip()
            attrs = []
            for part in re.split(r"[；;]", attrs_text):
                if not part.strip():
                    continue
                separator = ":" if ":" in part else "：" if "：" in part else ""
                if separator:
                    key, value = part.split(separator, 1)
                    attrs.append(
                        {"attr_key": key.strip(), "attr_value": value.strip()}
                    )
                else:
                    attrs.append(
                        {"attr_key": "规格", "attr_value": part.strip()}
                    )
            try:
                price = int(round(float(values.get("price", "0")) * 100))
            except ValueError:
                messagebox.showwarning("采购价", "采购价必须是数字。", parent=self)
                return
            sku_id = values.get("sku_id", "").strip() or (
                f"MANUAL-{len(self.links[self.active_link_index]['source_skus']) + 1}"
            )
            self.links[self.active_link_index]["source_skus"].append(
                {
                    "out_sku_id": sku_id,
                    "sale_price": max(0, price),
                    "sku_attrs": attrs,
                    "thumb_img": values.get("image", "").strip(),
                }
            )
            self._render_links()
            self._render_source_skus()

        SimpleFieldsDialog(
            self,
            "手动添加采购SKU",
            (
                ("sku_id", "采购SKU ID"),
                ("attrs", "采购规格（格式：颜色:红；尺寸:M）"),
                ("price", "采购价（元）"),
                ("image", "采购图片URL"),
            ),
            save,
        )

    def _edit_address(self) -> None:
        if self.active_link_index is None:
            messagebox.showinfo("请选择采购链接", "请先选择采购链接。", parent=self)
            return
        link = self.links[self.active_link_index]
        current = link.get("address_info") or {}

        def save(values: dict[str, str]) -> None:
            link["address_info"] = values
            messagebox.showinfo(
                "采购地址",
                "采购地址已保存到当前链接，点击主窗口“保存”后生效。",
                parent=self,
            )

        SimpleFieldsDialog(
            self,
            "关联采购收货地址",
            (
                ("recipient_name", "收货人"),
                ("recipient_phone", "手机号"),
                ("recipient_address", "收货地址"),
            ),
            save,
            current,
        )

    def _selected_source_sku(self) -> dict[str, Any] | None:
        selection = self.source_tree.selection()
        if not selection or self.active_link_index is None:
            return None
        source_skus = self.links[self.active_link_index].get("source_skus") or []
        index = int(selection[0])
        return source_skus[index] if 0 <= index < len(source_skus) else None

    def _match_selected(self) -> None:
        store_selection = self.store_tree.selection()
        source_sku = self._selected_source_sku()
        if not store_selection or source_sku is None:
            messagebox.showinfo(
                "选择规格",
                "请选择店铺SKU和一条采购SKU。",
                parent=self,
            )
            return
        link = self.links[self.active_link_index or 0]
        link_local_id = self.active_link_index
        for iid in store_selection:
            index = int(iid)
            if not 0 <= index < len(self.store_skus):
                continue
            store_sku = self.store_skus[index]
            store_sku_id = str(store_sku.get("sku_id") or store_sku.get("id") or "")
            if not store_sku_id:
                continue
            self.mappings[store_sku_id] = {
                "purchase_link_local_index": link_local_id,
                "purchase_link_id": int(link.get("id") or 0),
                "purchase_sku_id": str(
                    source_sku.get("out_sku_id")
                    or source_sku.get("sku_id")
                    or ""
                ),
                "purchase_attrs": _json_list(source_sku.get("sku_attrs")),
                "purchase_price": int(
                    source_sku.get("sale_price")
                    or source_sku.get("price")
                    or 0
                ),
                "purchase_image": _sku_image(source_sku),
            }
        self._render_store_skus()

    def _auto_match(self) -> None:
        if self.active_link_index is None:
            messagebox.showinfo("请选择采购链接", "请先选择采购链接。", parent=self)
            return
        link = self.links[self.active_link_index]
        source_skus = link.get("source_skus") or []
        matches = auto_match_skus(self.store_skus, source_skus)
        for sku_id, source_sku in matches.items():
            self.mappings[sku_id] = {
                "purchase_link_local_index": self.active_link_index,
                "purchase_link_id": int(link.get("id") or 0),
                "purchase_sku_id": str(
                    source_sku.get("out_sku_id")
                    or source_sku.get("sku_id")
                    or ""
                ),
                "purchase_attrs": _json_list(source_sku.get("sku_attrs")),
                "purchase_price": int(
                    source_sku.get("sale_price")
                    or source_sku.get("price")
                    or 0
                ),
                "purchase_image": _sku_image(source_sku),
            }
        self._render_store_skus()
        self.window.set_status(f"自动匹配 {len(matches)} 个规格")

    def _clear_selected_mapping(self) -> None:
        for iid in self.store_tree.selection():
            index = int(iid)
            if not 0 <= index < len(self.store_skus):
                continue
            sku_id = str(
                self.store_skus[index].get("sku_id")
                or self.store_skus[index].get("id")
                or ""
            )
            self.mappings.pop(sku_id, None)
        self._render_store_skus()

    def _clear_all_mappings(self) -> None:
        self.mappings.clear()
        self._render_store_skus()

    def _refresh_store_skus(self) -> None:
        store = self.db.get_store(self.store_id)
        if store is None:
            messagebox.showerror("店铺不存在", "当前商品所属店铺不存在。", parent=self)
            return
        self.window.set_status("正在刷新店铺商品SKU…", busy=True)

        def work(_progress):
            product = self.window.service.client_for(store).fetch_product(
                store,
                self.product_id,
            )
            fresh = {
                "product_id": product.product_id,
                "title": product.title,
                "store_name": product.store_name,
                "raw_json": json.dumps(product.raw, ensure_ascii=False),
            }
            return fresh

        def done(fresh: dict[str, Any]) -> None:
            self.product.update(fresh)
            self.store_skus = self._read_store_skus(self.product)
            self._render_store_skus()
            self.window.set_status(
                f"店铺SKU已刷新，共 {len(self.store_skus)} 个",
                busy=False,
            )

        self.window.run_task(
            work,
            done,
            lambda error: messagebox.showerror(
                "刷新店铺SKU失败",
                error,
                parent=self,
            ),
            log_title="刷新商品关联SKU",
        )

    def _save(self) -> None:
        self._capture_link_editor()
        for index, link in enumerate(self.links, 1):
            if not str(link.get("source_url") or "").strip():
                messagebox.showwarning(
                    "采购链接",
                    f"第 {index} 条采购链接为空，请填写或删除。",
                    parent=self,
                )
                return
        try:
            for link_id in self._deleted_link_ids:
                self.db.delete_product_purchase_link(
                    link_id,
                    store_id=self.store_id,
                    product_id=self.product_id,
                )
            for index, link in enumerate(self.links):
                link["id"] = self.db.upsert_product_purchase_link(
                    self.store_id,
                    self.product_id,
                    link,
                )
                link["updated_at"] = ""
            payload = []
            mapping_by_local = {
                index: int(link["id"])
                for index, link in enumerate(self.links)
            }
            for store_sku_id, mapping in self.mappings.items():
                local_index = mapping.get("purchase_link_local_index")
                link_id = (
                    mapping_by_local.get(int(local_index))
                    if local_index is not None
                    else int(mapping.get("purchase_link_id") or 0)
                )
                if not link_id:
                    continue
                payload.append(
                    {
                        **mapping,
                        "store_sku_id": store_sku_id,
                        "purchase_link_id": link_id,
                    }
                )
            self.db.replace_product_sku_mappings(
                self.store_id,
                self.product_id,
                payload,
            )
        except Exception as exc:
            messagebox.showerror("保存商品关联失败", str(exc), parent=self)
            return
        if callable(self.on_saved):
            self.on_saved()
        self.window.set_status(
            f"商品关联已保存：{len(self.links)} 个采购链接，"
            f"{len(self.mappings)} 个SKU映射"
        )
        self.destroy()
