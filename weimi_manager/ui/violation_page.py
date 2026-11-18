from __future__ import annotations

import csv
import io
import json
import tkinter as tk
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import TYPE_CHECKING, Any

from .theme import MUTED, PANEL, TEXT, button

if TYPE_CHECKING:
    from .app import MainWindow


class ViolationLibraryPage(ttk.Frame):
    title = "违规商品库"
    subtitle = "本机违规 · 云违规 · 精准比对"

    def __init__(self, parent: tk.Widget, window: "MainWindow"):
        super().__init__(parent, style="TFrame", padding=(24, 16, 24, 14))
        self.window = window
        self.db = window.db
        self.columnconfigure(0, weight=1)
        self.rowconfigure(3, weight=1)
        self.rowconfigure(6, weight=1)
        self._all_rows: list[dict[str, Any]] = []
        self._visible_rows: dict[str, dict[str, Any]] = {}
        self._stores: dict[str, Any] = {"全部店铺": None}
        self._matches: dict[str, dict[str, Any]] = {}
        self.store_var = tk.StringVar(value="全部店铺")
        self.search_var = tk.StringVar()
        self.source_var = tk.StringVar(value="全部来源")
        self.count_var = tk.StringVar(value="0 条本机违规")
        self.match_status_var = tk.StringVar(value="选择店铺后点「开始比对」。")
        self.action_var = tk.StringVar(value="删除到回收站")
        self.compare_local_var = tk.BooleanVar(
            value=self.db.get_setting("violation_compare_local", "1") == "1"
        )
        self.compare_cloud_var = tk.BooleanVar(
            value=self.db.get_setting("violation_compare_cloud", "1") == "1"
        )
        try:
            self.title_similarity = float(
                self.db.get_setting("violation_title_similarity", "60")
            )
        except ValueError:
            self.title_similarity = 60.0

        toolbar = ttk.Frame(self, style="TFrame")
        toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ttk.Label(toolbar, text="店铺").pack(side="left")
        self.store_combo = ttk.Combobox(
            toolbar,
            textvariable=self.store_var,
            state="readonly",
            width=20,
        )
        self.store_combo.pack(side="left", padx=(5, 10))
        button(toolbar, "手工添加", self._add_manual).pack(side="left")
        button(toolbar, "云违规库", self._show_cloud_library).pack(
            side="left", padx=6
        )
        button(toolbar, "导入清单", self._import_file).pack(side="left")
        button(toolbar, "抓取违规", self._sync_platform, "Primary").pack(
            side="right"
        )

        filters = ttk.Frame(self, style="TFrame")
        filters.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        ttk.Entry(filters, textvariable=self.search_var, width=36).pack(
            side="left"
        )
        ttk.Combobox(
            filters,
            textvariable=self.source_var,
            state="readonly",
            width=14,
            values=(
                "全部来源",
                "违规记录",
                "云端",
                "预警",
                "本机黑名单",
                "导入清单",
            ),
        ).pack(side="left", padx=8)
        ttk.Label(
            filters,
            textvariable=self.count_var,
            style="Subtitle.TLabel",
        ).pack(side="left", padx=8)
        self.search_var.trace_add(
            "write", lambda *_args: self._render()
        )
        self.source_var.trace_add(
            "write", lambda *_args: self._render()
        )
        button(filters, "删除选中", self._delete_selected, "Danger").pack(
            side="right"
        )
        button(
            filters,
            "比对设置",
            self._show_compare_settings,
        ).pack(side="right", padx=6)
        button(filters, "全选当前", self._select_all).pack(
            side="right", padx=6
        )

        self.tree = ttk.Treeview(
            self,
            columns=(
                "title",
                "product_id",
                "store",
                "source",
                "reason",
                "status",
                "fix",
            ),
            show="headings",
            selectmode="extended",
        )
        for key, text, width in (
            ("title", "商品标题", 300),
            ("product_id", "商品 ID", 170),
            ("store", "店铺", 140),
            ("source", "来源", 95),
            ("reason", "违规类型", 150),
            ("status", "状态", 110),
            ("fix", "整改方式", 300),
        ):
            self.tree.heading(key, text=text)
            self.tree.column(key, width=width, anchor="w")
        self.tree.grid(row=3, column=0, sticky="nsew")
        y_scroll = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        y_scroll.grid(row=3, column=1, sticky="ns")
        x_scroll = ttk.Scrollbar(self, orient="horizontal", command=self.tree.xview)
        x_scroll.grid(row=4, column=0, sticky="ew")
        self.tree.configure(
            yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set
        )

        compare_bar = ttk.Frame(self, style="TFrame")
        compare_bar.grid(row=5, column=0, sticky="ew", pady=(10, 6))
        ttk.Label(
            compare_bar,
            text="当前店铺违规比对",
            style="Subtitle.TLabel",
        ).pack(side="left")
        ttk.Label(
            compare_bar,
            textvariable=self.match_status_var,
            style="Subtitle.TLabel",
        ).pack(side="left", padx=12)
        ttk.Combobox(
            compare_bar,
            textvariable=self.action_var,
            state="readonly",
            width=14,
            values=("删除到回收站", "彻底删除", "下架"),
        ).pack(side="right")
        button(
            compare_bar,
            "删除命中商品",
            self._delete_matches,
            "Danger",
        ).pack(side="right", padx=6)
        button(
            compare_bar,
            "开始比对",
            self._compare,
            "Primary",
        ).pack(side="right")

        match_frame = ttk.Frame(self, style="Panel.TFrame")
        match_frame.grid(row=6, column=0, sticky="nsew")
        match_frame.columnconfigure(0, weight=1)
        match_frame.rowconfigure(0, weight=1)
        self.match_tree = ttk.Treeview(
            match_frame,
            columns=(
                "product_id",
                "product_title",
                "source",
                "violation",
                "match_reason",
                "status",
                "fix",
            ),
            show="headings",
            selectmode="extended",
        )
        for key, text, width in (
            ("product_id", "商品 ID", 170),
            ("product_title", "本店商品标题", 380),
            ("source", "来源", 90),
            ("violation", "命中违规", 220),
            ("match_reason", "命中原因", 100),
            ("status", "违规状态", 105),
            ("fix", "建议处理", 260),
        ):
            self.match_tree.heading(key, text=text)
            self.match_tree.column(key, width=width, anchor="w")
        self.match_tree.grid(row=0, column=0, sticky="nsew")
        ttk.Scrollbar(
            match_frame, orient="vertical", command=self.match_tree.yview
        ).grid(row=0, column=1, sticky="ns")
        self.match_tree.configure(
            yscrollcommand=match_frame.winfo_children()[-1].set
        )

    def refresh(self) -> None:
        stores = self.db.list_stores()
        self._stores = {"全部店铺": None, **{
            f"{store.name} · #{store.id}": store for store in stores
        }}
        values = list(self._stores)
        self.store_combo["values"] = values
        if self.store_var.get() not in values:
            self.store_var.set(values[0])
        try:
            loaded = json.loads(
                self.db.get_setting("violation_library", "[]")
            )
        except (TypeError, ValueError):
            loaded = []
        if not loaded:
            try:
                legacy = json.loads(
                    self.db.get_setting("violation_blacklist", "[]")
                )
            except (TypeError, ValueError):
                legacy = []
            loaded = [
                {
                    "record_id": f"local-{index}",
                    "product_id": str(row.get("product_id") or ""),
                    "title": str(row.get("title") or ""),
                    "reason": str(row.get("reason") or "本机黑名单"),
                    "source": "本机黑名单",
                    "status": "本机记录",
                    "fix": "",
                    "time": str(row.get("added") or ""),
                    "store_id": 0,
                    "store_name": "",
                }
                for index, row in enumerate(legacy)
                if isinstance(row, dict)
            ]
        self._all_rows = [
            dict(row) for row in loaded if isinstance(row, dict)
        ]
        self._render()

    def _selected_store(self):
        return self._stores.get(self.store_var.get())

    def _render(self) -> None:
        selected_store = self._selected_store()
        term = self.search_var.get().strip().lower()
        source = self.source_var.get()
        rows = []
        for row in self._all_rows:
            if selected_store is not None:
                record_app_id = str(row.get("store_app_id") or "")
                if record_app_id:
                    if record_app_id != selected_store.app_id:
                        continue
                elif int(row.get("store_id") or 0) not in {
                    0,
                    selected_store.id,
                }:
                    continue
            if source != "全部来源" and row.get("source") != source:
                continue
            text = " ".join(
                str(row.get(key) or "")
                for key in ("title", "product_id", "store_name", "reason")
            ).lower()
            if term and term not in text:
                continue
            rows.append(row)
        self.tree.delete(*self.tree.get_children())
        self._visible_rows.clear()
        for index, row in enumerate(rows):
            iid = str(index)
            self._visible_rows[iid] = row
            self.tree.insert(
                "",
                "end",
                iid=iid,
                values=(
                    row.get("title", ""),
                    row.get("product_id", "") or "—",
                    row.get("store_name", ""),
                    row.get("source", ""),
                    row.get("reason", ""),
                    row.get("status", ""),
                    row.get("fix", ""),
                ),
            )
        self.count_var.set(f"{len(rows)} 条本机违规")

    def _save(self, rows: list[dict[str, Any]]) -> None:
        self._all_rows = rows
        self.db.set_setting(
            "violation_library",
            json.dumps(rows, ensure_ascii=False),
        )
        self._render()

    def _select_all(self) -> None:
        self.tree.selection_set(tuple(self.tree.get_children()))

    def _delete_selected(self) -> None:
        ids = {
            self._visible_rows[iid].get("record_id")
            for iid in self.tree.selection()
            if iid in self._visible_rows
        }
        if not ids:
            messagebox.showinfo(
                "请选择记录", "请先选择违规记录。", parent=self
            )
            return
        if not messagebox.askyesno(
            "删除违规记录",
            f"从本机违规库删除 {len(ids)} 条记录？不会操作微信商品。",
            parent=self,
        ):
            return
        self._save(
            [
                row
                for row in self._all_rows
                if row.get("record_id") not in ids
            ]
        )

    def _add_manual(self) -> None:
        dialog = tk.Toplevel(self)
        dialog.title("手工添加违规记录")
        dialog.transient(self.winfo_toplevel())
        panel = ttk.Frame(dialog, style="Panel.TFrame", padding=18)
        panel.pack(fill="both", expand=True)
        values = {
            "product_id": tk.StringVar(),
            "title": tk.StringVar(),
            "reason": tk.StringVar(),
            "fix": tk.StringVar(),
        }
        for row, (key, label) in enumerate(
            (
                ("product_id", "商品 ID"),
                ("title", "商品标题"),
                ("reason", "违规原因"),
                ("fix", "整改方式"),
            )
        ):
            ttk.Label(panel, text=label, style="Panel.TLabel").grid(
                row=row, column=0, sticky="w", padx=(0, 12), pady=6
            )
            ttk.Entry(
                panel, textvariable=values[key], width=44
            ).grid(row=row, column=1, sticky="ew", pady=6)
        panel.columnconfigure(1, weight=1)

        def save() -> None:
            product_id = values["product_id"].get().strip()
            title = values["title"].get().strip()
            if not product_id and not title:
                messagebox.showwarning(
                    "记录为空",
                    "商品 ID 和标题至少填写一项。",
                    parent=dialog,
                )
                return
            store = self._selected_store()
            row = {
                "record_id": f"manual-{datetime.now().timestamp()}",
                "product_id": product_id,
                "title": title or product_id,
                "reason": values["reason"].get().strip() or "本机记录",
                "source": "本机黑名单",
                "status": "本机记录",
                "fix": values["fix"].get().strip(),
                "time": datetime.now().isoformat(timespec="seconds"),
                "store_id": store.id if store else 0,
                "store_name": store.name if store else "",
            }
            self._save([*self._all_rows, row])
            dialog.destroy()

        actions = ttk.Frame(panel, style="Panel.TFrame")
        actions.grid(row=4, column=0, columnspan=2, sticky="e", pady=(12, 0))
        button(actions, "取消", dialog.destroy).pack(side="left", padx=6)
        button(actions, "保存", save, "Primary").pack(side="left")
        dialog.grab_set()

    def _import_file(self) -> None:
        path = filedialog.askopenfilename(
            parent=self,
            title="导入违规清单",
            filetypes=[
                ("违规清单", "*.json *.csv *.tsv *.txt"),
                ("所有文件", "*.*"),
            ],
        )
        if not path:
            return
        source_path = Path(path)
        text = source_path.read_text(encoding="utf-8-sig")
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            dialect = csv.excel_tab
            try:
                dialect = csv.Sniffer().sniff(text[:4096])
            except csv.Error:
                pass
            parsed = list(csv.DictReader(io.StringIO(text), dialect=dialect))
        if isinstance(parsed, dict):
            parsed = parsed.get("rows") or parsed.get("list") or [parsed]
        if not isinstance(parsed, list):
            messagebox.showerror(
                "导入失败", "违规清单必须是数组或表格。", parent=self
            )
            return
        store = self._selected_store()
        existing = {row.get("record_id") for row in self._all_rows}
        added = []
        for index, row in enumerate(parsed):
            if not isinstance(row, dict):
                continue
            product_id = str(
                row.get("product_id") or row.get("productId") or row.get("商品ID") or ""
            )
            title = str(
                row.get("title") or row.get("商品标题") or product_id
            )
            record_id = str(
                row.get("record_id")
                or f"import-{source_path.name}-{product_id}-{index}"
            )
            if record_id in existing:
                continue
            added.append(
                {
                    "record_id": record_id,
                    "product_id": product_id,
                    "title": title,
                    "reason": str(
                        row.get("reason") or row.get("违规原因") or "导入清单"
                    ),
                    "source": "导入清单",
                    "status": str(row.get("status") or row.get("状态") or ""),
                    "fix": str(row.get("fix") or row.get("整改方式") or ""),
                    "time": str(row.get("time") or row.get("时间") or ""),
                    "store_id": store.id if store else int(row.get("store_id") or 0),
                    "store_name": store.name if store else str(row.get("store_name") or ""),
                }
            )
        self._save([*self._all_rows, *added])
        self.window.set_status(
            f"违规清单导入完成：新增 {len(added)} 条"
        )

    def _sync_platform(self) -> None:
        stores = (
            [self._selected_store()]
            if self._selected_store() is not None
            else self.db.list_stores()
        )
        stores = [store for store in stores if store is not None]
        if not stores:
            messagebox.showinfo(
                "没有店铺", "请先添加店铺。", parent=self
            )
            return
        self.window.set_status("正在抓取平台违规和预警…", busy=True)

        def work(_progress) -> list[str]:
            errors = []
            for store in stores:
                try:
                    self.window.service.sync_violation_library(store)
                except Exception as exc:
                    errors.append(f"{store.name}: {exc}")
            return errors

        def done(errors: list[str]) -> None:
            self.window.set_status(
                f"违规抓取完成：成功 {len(stores) - len(errors)} 家"
            )
            self.refresh()
            if errors:
                messagebox.showwarning(
                    "部分店铺抓取失败",
                    "\n".join(errors[:8]),
                    parent=self,
                )

        self.window.run_task(
            work,
            done,
            lambda error: messagebox.showerror(
                "抓取违规失败", error, parent=self
            ),
            log_title="抓取平台违规商品",
        )

    def _show_cloud_library(self) -> None:
        cloud_rows = [
            row
            for row in self._all_rows
            if row.get("source") not in {"本机黑名单", "导入清单"}
        ]
        types: dict[str, int] = {}
        for row in cloud_rows:
            reason = str(row.get("reason") or "未分类")
            types[reason] = types.get(reason, 0) + 1
        detail = "\n".join(
            f"{reason} · {count} 条"
            for reason, count in sorted(types.items())
        )
        messagebox.showinfo(
            "云违规库",
            "每日空闲任务会把各店明确违规的商品标题同步到组织云违规库。"
            "\n\n"
            + (detail or "暂无可用的云端违规类型快照。")
            + "\n\n价格错误、库存错误和已撤销/申诉成功记录不会收录。",
            parent=self,
        )

    def _show_compare_settings(self) -> None:
        dialog = tk.Toplevel(self)
        dialog.title("违规比对设置")
        dialog.transient(self.winfo_toplevel())
        dialog.resizable(False, False)
        panel = ttk.Frame(dialog, style="Panel.TFrame", padding=18)
        panel.pack(fill="both", expand=True)
        tk.Checkbutton(
            panel,
            text="比对本机违规库",
            variable=self.compare_local_var,
            bg=PANEL,
            fg=TEXT,
            activebackground=PANEL,
            selectcolor="white",
            bd=0,
        ).pack(anchor="w", pady=3)
        tk.Checkbutton(
            panel,
            text="比对平台/云违规快照",
            variable=self.compare_cloud_var,
            bg=PANEL,
            fg=TEXT,
            activebackground=PANEL,
            selectcolor="white",
            bd=0,
        ).pack(anchor="w", pady=3)
        threshold_var = tk.StringVar(value=f"{self.title_similarity:g}")
        row = ttk.Frame(panel, style="Panel.TFrame")
        row.pack(fill="x", pady=(10, 0))
        ttk.Label(
            row,
            text="标题匹配相似度 ≥",
            style="Panel.TLabel",
        ).pack(side="left")
        ttk.Spinbox(
            row,
            textvariable=threshold_var,
            from_=1,
            to=100,
            width=7,
        ).pack(side="left", padx=6)
        ttk.Label(row, text="%", style="Panel.TLabel").pack(side="left")
        ttk.Label(
            panel,
            text="商品 ID 完全相同始终命中；默认 60%，越低越容易命中。",
            style="Panel.TLabel",
            foreground=MUTED,
        ).pack(anchor="w", pady=(6, 8))

        def save() -> None:
            try:
                threshold = float(threshold_var.get())
            except ValueError:
                messagebox.showwarning(
                    "比对设置", "相似度必须是数字。", parent=dialog
                )
                return
            self.title_similarity = min(100, max(1, threshold))
            self.db.set_setting(
                "violation_compare_local",
                "1" if self.compare_local_var.get() else "0",
            )
            self.db.set_setting(
                "violation_compare_cloud",
                "1" if self.compare_cloud_var.get() else "0",
            )
            self.db.set_setting(
                "violation_title_similarity",
                f"{self.title_similarity:g}",
            )
            dialog.destroy()

        actions = ttk.Frame(panel, style="Panel.TFrame")
        actions.pack(anchor="e")
        button(actions, "取消", dialog.destroy).pack(side="left", padx=6)
        button(actions, "保存设置", save, "Primary").pack(side="left")
        dialog.bind("<Escape>", lambda _event: dialog.destroy())
        dialog.grab_set()

    def _compare(self) -> None:
        store = self._selected_store()
        if store is None:
            messagebox.showinfo(
                "请选择店铺", "违规比对前请选择一家具体店铺。", parent=self
            )
            return
        if not self.compare_local_var.get() and not self.compare_cloud_var.get():
            messagebox.showwarning(
                "比对范围",
                "本机违规库和平台违规快照至少选择一项。",
                parent=self,
            )
            return
        product_rows = {
            str(row["product_id"]): row
            for row in self.db.list_products(store_id=store.id)
        }
        with self.db.connect() as conn:
            catalog_ids = {
                str(row["product_id"])
                for row in conn.execute(
                    "SELECT product_id FROM product_catalog WHERE store_id=?",
                    (store.id,),
                ).fetchall()
            }
        violations = []
        for violation in self._all_rows:
            if int(violation.get("store_id") or 0) not in {0, store.id}:
                continue
            source = str(violation.get("source") or "")
            is_local = source in {"本机黑名单", "导入清单"}
            if is_local and not self.compare_local_var.get():
                continue
            if not is_local and not self.compare_cloud_var.get():
                continue
            violations.append(violation)
        by_product_id: dict[str, list[dict[str, Any]]] = {}
        for violation in violations:
            product_id = str(violation.get("product_id") or "")
            if product_id:
                by_product_id.setdefault(product_id, []).append(violation)
        matches: list[dict[str, Any]] = []
        for product_id in set(product_rows) | catalog_ids:
            product = product_rows.get(product_id) or {}
            product_title = str(product.get("title") or "")
            candidates = list(by_product_id.get(product_id, []))
            match_reason = "商品ID相同" if candidates else ""
            if product_title:
                scored = []
                for violation in violations:
                    other_title = str(violation.get("title") or "").strip()
                    if not other_title:
                        continue
                    ratio = (
                        SequenceMatcher(
                            None,
                            product_title.strip(),
                            other_title,
                        ).ratio()
                        * 100
                    )
                    if ratio >= self.title_similarity:
                        scored.append((ratio, violation))
                if scored:
                    _ratio, title_match = max(
                        scored, key=lambda item: item[0]
                    )
                    if title_match not in candidates:
                        candidates.append(title_match)
                    match_reason = (
                        f"标题相似 {max(item[0] for item in scored):.0f}%"
                    )
            if not candidates:
                continue
            violation = candidates[0]
            matches.append(
                {
                    **violation,
                    "product_id": product_id,
                    "product_title": product_title
                    or str(violation.get("title") or ""),
                    "match_reason": match_reason,
                }
            )
        self.match_tree.delete(*self.match_tree.get_children())
        self._matches.clear()
        for index, row in enumerate(matches):
            iid = str(index)
            self._matches[iid] = row
            self.match_tree.insert(
                "",
                "end",
                iid=iid,
                values=(
                    row.get("product_id", ""),
                    row.get("product_title", ""),
                    row.get("source", ""),
                    row.get("reason", ""),
                    row.get("match_reason", ""),
                    row.get("status", ""),
                    row.get("fix", ""),
                ),
            )
        self.match_status_var.set(
            f"比对完成：命中 {len(matches)} 个 / 扫描 "
            f"{len(set(product_rows) | catalog_ids)}"
        )

    def _delete_matches(self) -> None:
        store = self._selected_store()
        selected = [
            self._matches[iid]
            for iid in self.match_tree.selection()
            if iid in self._matches
        ]
        if store is None or not selected:
            messagebox.showinfo(
                "请选择商品",
                "请先比对并选择要处理的命中商品。",
                parent=self,
            )
            return
        way = {
            "删除到回收站": "recycle",
            "彻底删除": "real",
            "下架": "offshelf",
        }[self.action_var.get()]
        ids = list(
            dict.fromkeys(str(row.get("product_id")) for row in selected)
        )
        if not messagebox.askyesno(
            "处理命中商品",
            f"即将对 {store.name} 的 {len(ids)} 个违规命中商品执行"
            f"“{self.action_var.get()}”。这是正式平台操作，是否继续？",
            parent=self,
        ):
            return
        self.window.set_status("正在处理违规命中商品…", busy=True)

        def done(_result) -> None:
            self.window.set_status(
                f"违规命中商品处理完成：{len(ids)} 个"
            )
            self.window.store_data_changed()
            self._compare()

        self.window.run_task(
            lambda _progress: self.window.service.private_batch_product_action(
                store, ids, way
            ),
            done,
            lambda error: messagebox.showerror(
                "处理命中商品失败", error, parent=self
            ),
            log_title="批量处理违规命中商品",
        )
