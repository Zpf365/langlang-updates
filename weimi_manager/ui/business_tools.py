from __future__ import annotations

import json
import tkinter as tk
from datetime import date, datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import TYPE_CHECKING

from ..exporter import export_xlsx
from .dialogs import SimpleFieldsDialog
from .pages import BasePage
from .theme import BORDER, PANEL, button

if TYPE_CHECKING:
    from .app import MainWindow


class StoreGroupsPage(BasePage):
    title = "店铺分组管理"
    subtitle = "创建分组并批量调整店铺归属"

    def __init__(self, parent: tk.Widget, window: "MainWindow"):
        super().__init__(parent, window)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)
        self._stores: dict[str, object] = {}
        self.group_var = tk.StringVar(value="全部分组")
        self.group_name_var = tk.StringVar()

        toolbar = ttk.Frame(self, style="TFrame")
        toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ttk.Label(toolbar, text="分组").pack(side="left")
        self.group_combo = ttk.Combobox(
            toolbar,
            textvariable=self.group_var,
            state="readonly",
            width=20,
        )
        self.group_combo.pack(side="left", padx=(5, 12))
        ttk.Label(toolbar, text="新分组名称").pack(side="left")
        ttk.Entry(toolbar, textvariable=self.group_name_var, width=20).pack(
            side="left",
            padx=(5, 6),
        )
        button(toolbar, "应用到所选店铺", self._apply_group, "Primary").pack(
            side="left"
        )
        button(toolbar, "新建空分组", self._create_group).pack(
            side="left",
            padx=(6, 0),
        )
        button(toolbar, "重命名分组", self._rename_group).pack(side="left", padx=6)
        button(toolbar, "删除分组", self._delete_group, "Danger").pack(side="left")

        actions = ttk.Frame(self, style="TFrame")
        actions.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        button(actions, "全选", self._select_all).pack(side="left")
        button(actions, "取消选择", self._select_none).pack(side="left", padx=6)
        ttk.Label(
            actions,
            text="选择一家或多家店铺后，可批量设置分组。",
            style="Subtitle.TLabel",
        ).pack(side="left", padx=10)

        body = tk.Frame(self, bg=PANEL, highlightbackground=BORDER, highlightthickness=1)
        body.grid(row=2, column=0, sticky="nsew")
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=1)
        self.tree = ttk.Treeview(
            body,
            columns=("name", "group", "app_id", "login"),
            show="headings",
            selectmode="extended",
        )
        for key, text, width in (
            ("name", "店铺名称", 260),
            ("group", "当前分组", 180),
            ("app_id", "AppID", 300),
            ("login", "登录状态", 140),
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
        groups = self.db.list_store_groups()
        values = ["全部分组", *groups]
        self.group_combo["values"] = values
        if self.group_var.get() not in values:
            self.group_var.set("全部分组")
        self.tree.delete(*self.tree.get_children())
        self._stores.clear()
        for store in stores:
            if (
                self.group_var.get() != "全部分组"
                and store.group_name != self.group_var.get()
            ):
                continue
            iid = str(store.id)
            self._stores[iid] = store
            self.tree.insert(
                "",
                "end",
                iid=iid,
                values=(
                    store.name,
                    store.group_name or "未分组",
                    store.app_id,
                    store.web_status,
                ),
            )

    def _selected_stores(self) -> list:
        return [
            self._stores[iid]
            for iid in self.tree.selection()
            if iid in self._stores
        ]

    def _select_all(self) -> None:
        self.tree.selection_set(tuple(self._stores))

    def _select_none(self) -> None:
        self.tree.selection_remove(*self.tree.selection())

    def _submit_group_change(
        self,
        *,
        scope: str,
        title: str,
        work,
        done,
    ) -> None:
        runner = getattr(self.window, "run_task", None)

        def on_error(error: str) -> None:
            messagebox.showerror(
                title,
                error,
                parent=self,
            )

        if callable(runner):
            runner(
                work,
                done,
                on_error,
                log_title=title,
                task_scope=scope,
            )
            return
        try:
            done(work(None))
        except Exception as exc:
            on_error(str(exc))

    def _create_group(self) -> None:
        group = self.group_name_var.get().strip()
        if not group:
            messagebox.showwarning(
                "分组名称",
                "请输入新分组名称。",
                parent=self,
            )
            return

        def done(_result: str) -> None:
            self.group_var.set(group)
            self.refresh()
            self.window.store_data_changed()
            self.window.set_status(f"已新建空分组“{group}”")

        self._submit_group_change(
            scope="stores:group:create",
            title="创建店铺分组",
            work=lambda _progress: self.db.create_store_group(group),
            done=done,
        )

    def _apply_group(self) -> None:
        stores = self._selected_stores()
        group = self.group_name_var.get().strip()
        if not stores:
            messagebox.showinfo("请选择店铺", "请先选择至少一家店铺。", parent=self)
            return
        if not group:
            messagebox.showwarning("分组名称", "请输入新分组名称。", parent=self)
            return
        def done(_result: int) -> None:
            self.refresh()
            self.window.store_data_changed()
            self.window.set_status(
                f"已将 {len(stores)} 家店铺设置到“{group}”分组"
            )

        self._submit_group_change(
            scope="stores:group:apply",
            title="调整店铺分组",
            work=lambda _progress: self.db.set_store_groups(
                [store.id for store in stores],
                group,
            ),
            done=done,
        )

    def _rename_group(self) -> None:
        old_group = self.group_var.get()
        if old_group == "全部分组":
            messagebox.showinfo("请选择分组", "请先在左侧选择要重命名的分组。", parent=self)
            return

        def save(values: dict[str, str]) -> None:
            new_group = values.get("new_group", "").strip()
            if not new_group:
                messagebox.showwarning("分组名称", "新分组名称不能为空。", parent=self)
                return
            def done(_result: None) -> None:
                self.group_var.set(new_group)
                self.refresh()
                self.window.store_data_changed()
                self.window.set_status(
                    f"分组“{old_group}”已重命名为“{new_group}”"
                )

            self._submit_group_change(
                scope="stores:group:rename",
                title="重命名店铺分组",
                work=lambda _progress: self.db.rename_store_group(
                    old_group,
                    new_group,
                ),
                done=done,
            )

        SimpleFieldsDialog(
            self,
            "重命名分组",
            (("new_group", "新分组名称"),),
            save,
        )

    def _delete_group(self) -> None:
        group = self.group_var.get()
        if group == "全部分组":
            messagebox.showinfo("请选择分组", "请先在左侧选择要删除的分组。", parent=self)
            return
        if not messagebox.askyesno(
            "删除分组",
            f"确定删除分组“{group}”吗？店铺本身不会被删除，只会变为未分组。",
            parent=self,
        ):
            return
        def done(_result: None) -> None:
            self.group_var.set("全部分组")
            self.refresh()
            self.window.store_data_changed()
            self.window.set_status(f"分组“{group}”已删除")

        self._submit_group_change(
            scope="stores:group:delete",
            title="删除店铺分组",
            work=lambda _progress: self.db.delete_store_group(group),
            done=done,
        )


class Double11PrefillPage(BasePage):
    title = "双11活动"
    subtitle = "原价校验 · 销售中 · 一键预填报"

    def __init__(self, parent: tk.Widget, window: "MainWindow"):
        super().__init__(parent, window)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)
        self._stores: dict[str, object] = {}
        self._source_rows: dict[str, dict] = {}
        self._draft_rows: dict[str, dict] = {}
        self.store_var = tk.StringVar()
        self.state_var = tk.StringVar(value="请选择店铺")

        toolbar = ttk.Frame(self, style="TFrame")
        toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ttk.Label(toolbar, text="店铺").pack(side="left")
        self.store_combo = ttk.Combobox(
            toolbar,
            textvariable=self.store_var,
            state="readonly",
            width=22,
        )
        self.store_combo.pack(side="left", padx=(5, 10))
        button(toolbar, "加载商品", self._load).pack(side="left")
        button(toolbar, "加入预填报", self._add_selected, "Primary").pack(
            side="left",
            padx=6,
        )
        button(toolbar, "编辑活动价/库存", self._edit_draft).pack(side="left")
        button(toolbar, "导出草稿", self._export).pack(side="left", padx=6)
        button(toolbar, "读取平台活动", self._read_activity).pack(side="left")
        button(toolbar, "提交到平台", self._submit_activity, "Primary").pack(
            side="left",
            padx=6,
        )
        button(
            toolbar,
            "移除全部报名",
            self._remove_all_activity,
            "Danger",
        ).pack(side="left", padx=6)
        button(toolbar, "打开官方商品后台", self._open_backend).pack(side="left")
        ttk.Label(
            toolbar,
            textvariable=self.state_var,
            style="Subtitle.TLabel",
        ).pack(side="right")

        body = ttk.Panedwindow(self, orient="horizontal")
        body.grid(row=2, column=0, sticky="nsew")
        source_frame = ttk.Frame(body, style="Panel.TFrame")
        draft_frame = ttk.Frame(body, style="Panel.TFrame")
        body.add(source_frame, weight=1)
        body.add(draft_frame, weight=1)
        self.source_tree = self._build_tree(
            source_frame,
            ("id", "title", "price", "stock", "status"),
            (
                ("id", "商品ID", 145),
                ("title", "商品标题", 260),
                ("price", "当前价", 85),
                ("stock", "库存", 75),
                ("status", "状态", 80),
            ),
        )
        self.draft_tree = self._build_tree(
            draft_frame,
            ("id", "title", "activity_price", "activity_stock"),
            (
                ("id", "商品ID", 145),
                ("title", "商品标题", 260),
                ("activity_price", "活动价", 85),
                ("activity_stock", "活动库存", 85),
            ),
        )

    @staticmethod
    def _build_tree(parent, columns, headings) -> ttk.Treeview:
        parent.rowconfigure(0, weight=1)
        parent.columnconfigure(0, weight=1)
        tree = ttk.Treeview(parent, columns=columns, show="headings")
        for key, text, width in headings:
            tree.heading(key, text=text)
            tree.column(key, width=width, anchor="w")
        tree.grid(row=0, column=0, sticky="nsew")
        ttk.Scrollbar(parent, orient="vertical", command=tree.yview).grid(
            row=0,
            column=1,
            sticky="ns",
        )
        return tree

    def refresh(self) -> None:
        self._stores = {
            f"{store.name} · #{store.id}": store
            for store in self.db.list_stores()
        }
        values = list(self._stores)
        self.store_combo["values"] = values
        if self.store_var.get() not in values:
            self.store_var.set(values[0] if values else "")
        self._load()

    def _selected_store(self):
        return self._stores.get(self.store_var.get())

    def _setting_key(self, store_id: int) -> str:
        return f"double11_prefill_{store_id}"

    def _load(self) -> None:
        store = self._selected_store()
        if store is None:
            return
        self.source_tree.delete(*self.source_tree.get_children())
        self._source_rows.clear()
        for index, row in enumerate(
            self.db.list_products(store_id=store.id)
        ):
            iid = str(index)
            self._source_rows[iid] = row
            self.source_tree.insert(
                "",
                "end",
                iid=iid,
                values=(
                    row["product_id"],
                    row["title"],
                    f"¥{(row['min_price'] or 0) / 100:.2f}",
                    row["stock_total"],
                    row["status_text"],
                ),
            )
        try:
            draft = json.loads(
                self.db.get_setting(self._setting_key(store.id), "[]")
            )
        except json.JSONDecodeError:
            draft = []
        self.draft_tree.delete(*self.draft_tree.get_children())
        self._draft_rows.clear()
        for index, item in enumerate(draft if isinstance(draft, list) else []):
            iid = str(index)
            self._draft_rows[iid] = item
            self.draft_tree.insert(
                "",
                "end",
                iid=iid,
                values=(
                    item.get("product_id", ""),
                    item.get("title", ""),
                    f"¥{int(item.get('activity_price') or 0) / 100:.2f}",
                    item.get("activity_stock", 0),
                ),
            )
        self.state_var.set(
            f"商品 {len(self._source_rows)} · 草稿 {len(self._draft_rows)}"
        )

    def _save_draft(self) -> None:
        store = self._selected_store()
        if store is None:
            return
        self.db.set_setting(
            self._setting_key(store.id),
            json.dumps(list(self._draft_rows.values()), ensure_ascii=False),
        )

    def _add_selected(self) -> None:
        store = self._selected_store()
        if store is None:
            return
        existing = {
            str(item.get("product_id") or "")
            for item in self._draft_rows.values()
        }
        blacklist = self.db.violation_blacklist_ids()
        for iid in self.source_tree.selection():
            row = self._source_rows.get(iid)
            if (
                row is None
                or str(row["product_id"]) in existing
                or str(row["product_id"]) in blacklist
            ):
                continue
            item = {
                "product_id": str(row["product_id"]),
                "title": str(row["title"]),
                "activity_price": int(row["min_price"] or 0),
                "activity_stock": int(row["stock_total"] or 0),
            }
            self._draft_rows[str(len(self._draft_rows))] = item
            existing.add(str(row["product_id"]))
        self._save_draft()
        self._load()

    def _selected_draft(self, require: bool = True) -> dict | None:
        selection = self.draft_tree.selection()
        if not selection:
            if require:
                messagebox.showinfo("请选择草稿", "请先选择一条预填报草稿。", parent=self)
            return None
        return self._draft_rows.get(selection[0])

    def _edit_draft(self) -> None:
        item = self._selected_draft()
        if item is None:
            return

        def save(values: dict[str, str]) -> None:
            try:
                item["activity_price"] = round(float(values["activity_price"]) * 100)
                item["activity_stock"] = int(values["activity_stock"])
            except (KeyError, ValueError):
                messagebox.showwarning("预填报", "活动价和库存格式不正确。", parent=self)
                return
            self._save_draft()
            self._load()

        SimpleFieldsDialog(
            self,
            "编辑预填报",
            (
                ("activity_price", "活动价（元）"),
                ("activity_stock", "活动库存"),
            ),
            save,
            initial={
                "activity_price": f"{int(item.get('activity_price') or 0) / 100:.2f}",
                "activity_stock": str(item.get("activity_stock") or 0),
            },
        )

    def _export(self) -> None:
        if not self._draft_rows:
            messagebox.showinfo("没有草稿", "当前没有可导出的预填报草稿。", parent=self)
            return
        path = filedialog.asksaveasfilename(
            parent=self,
            title="导出双十一预填报",
            defaultextension=".xlsx",
            filetypes=[("Excel 工作簿", "*.xlsx")],
            initialfile=f"双十一预填报_{date.today():%Y%m%d}.xlsx",
        )
        if not path:
            return
        headers = ["商品ID", "商品标题", "活动价", "活动库存"]
        rows = [
            [
                item.get("product_id", ""),
                item.get("title", ""),
                int(item.get("activity_price") or 0) / 100,
                int(item.get("activity_stock") or 0),
            ]
            for item in self._draft_rows.values()
        ]
        try:
            export_xlsx(path, "双十一预填报", headers, rows)
        except Exception as exc:
            messagebox.showerror("导出失败", str(exc), parent=self)
            return
        self.window.set_status(f"已导出：{Path(path).name}")

    def _open_backend(self) -> None:
        store = self._selected_store()
        if store is None:
            return
        self.window.set_status(f"正在打开“{store.name}”的微信商品后台…", busy=True)
        self.window.run_task(
            lambda _progress: self.window.web_session.open_official_product_backend(store),
            lambda _result: self.window.set_status("微信商品后台已关闭"),
            lambda error: messagebox.showerror("打开后台失败", error, parent=self),
            log_title="打开官方商品后台",
        )

    def _activity_id(self) -> int:
        try:
            return int(
                self.db.get_setting("double11_activity_id", "1323") or "1323"
            )
        except ValueError:
            return 1323

    def _read_activity(self) -> None:
        store = self._selected_store()
        if store is None:
            return
        self.window.set_status("正在读取双十一平台活动…", busy=True)

        def done(result: dict) -> None:
            self.window.set_status("双十一平台活动已读取")
            messagebox.showinfo(
                "双十一平台活动",
                json.dumps(result, ensure_ascii=False, indent=2)[:12000],
                parent=self,
            )

        self.window.run_task(
            lambda _progress: self.window.service.double11_activity_products(
                store,
                self._activity_id(),
            ),
            done,
            lambda error: messagebox.showerror(
                "读取双十一活动失败",
                error,
                parent=self,
            ),
            log_title="读取双十一活动商品",
        )

    def _submit_activity(self) -> None:
        store = self._selected_store()
        if store is None:
            return
        if not self._draft_rows:
            messagebox.showinfo("没有草稿", "请先加入预填报商品。", parent=self)
            return
        if not messagebox.askyesno(
            "提交双十一活动",
            f"将提交 {len(self._draft_rows)} 个商品的草稿到活动 "
            f"{self._activity_id()}。该操作会修改平台活动商品和价格，是否继续？",
            parent=self,
        ):
            return
        drafts = list(self._draft_rows.values())
        self.window.set_status("正在提交双十一活动…", busy=True)

        def done(result: dict) -> None:
            self.window.set_status("双十一草稿已提交到平台")
            messagebox.showinfo(
                "提交完成",
                json.dumps(result, ensure_ascii=False, indent=2)[:12000],
                parent=self,
            )

        self.window.run_task(
            lambda _progress: self.window.service.double11_submit(
                store,
                drafts,
                self._activity_id(),
            ),
            done,
            lambda error: messagebox.showerror(
                "提交双十一活动失败",
                error,
                parent=self,
            ),
            log_title="提交双十一活动商品",
        )

    def _remove_all_activity(self) -> None:
        store = self._selected_store()
        if store is None:
            messagebox.showinfo(
                "请选择店铺", "请先选择一家店铺。", parent=self
            )
            return
        if not messagebox.askyesno(
            "移除全部报名",
            f"将移除“{store.name}”当前双11活动的全部商品报名。\n"
            "这是正式平台写操作，是否继续？",
            parent=self,
        ):
            return
        self.window.set_status("正在移除全部双11报名…", busy=True)

        def done(result: dict) -> None:
            self.window.set_status("全部双11报名已移除")
            messagebox.showinfo(
                "移除完成",
                json.dumps(result, ensure_ascii=False, indent=2)[:12000],
                parent=self,
            )

        self.window.run_task(
            lambda _progress: self.window.service.double11_remove_all(
                store,
                self._activity_id(),
            ),
            done,
            lambda error: messagebox.showerror(
                "移除报名失败", error, parent=self
            ),
            log_title="移除双十一活动商品",
        )


class ViolationBlacklistPage(BasePage):
    title = "违规商品黑名单"
    subtitle = "记录本地黑名单，防止违规商品再次进入批量运营"

    def __init__(self, parent: tk.Widget, window: "MainWindow"):
        super().__init__(parent, window)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)
        self._rows: list[dict] = []
        self.search_var = tk.StringVar()

        toolbar = ttk.Frame(self, style="TFrame")
        toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ttk.Entry(toolbar, textvariable=self.search_var, width=24).pack(side="left")
        button(toolbar, "搜索", self._load).pack(side="left", padx=6)
        button(toolbar, "新增黑名单", self._add, "Primary").pack(side="left")
        button(toolbar, "同步平台违规", self._sync_platform).pack(
            side="left",
            padx=6,
        )
        button(toolbar, "从商品库添加", self._add_from_catalog).pack(
            side="left",
            padx=6,
        )
        button(toolbar, "移出黑名单", self._remove, "Danger").pack(side="left")
        button(toolbar, "导出", self._export).pack(side="left", padx=6)
        ttk.Label(
            toolbar,
            text="黑名单仅保存在本机，不会自动修改平台状态。",
            style="Subtitle.TLabel",
        ).pack(side="right")

        body = tk.Frame(self, bg=PANEL, highlightbackground=BORDER, highlightthickness=1)
        body.grid(row=2, column=0, sticky="nsew")
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=1)
        self.tree = ttk.Treeview(
            body,
            columns=("product_id", "title", "reason", "added"),
            show="headings",
        )
        for key, text, width in (
            ("product_id", "商品ID", 190),
            ("title", "商品标题", 430),
            ("reason", "违规/拉黑原因", 260),
            ("added", "加入日期", 140),
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
        self._load()

    def _load(self) -> None:
        try:
            rows = json.loads(self.db.get_setting("violation_blacklist", "[]"))
        except json.JSONDecodeError:
            rows = []
        term = self.search_var.get().strip().lower()
        self._rows = [
            row
            for row in rows
            if isinstance(row, dict)
            and (
                not term
                or term
                in (
                    f"{row.get('product_id', '')} "
                    f"{row.get('title', '')} "
                    f"{row.get('reason', '')}"
                ).lower()
            )
        ]
        self.tree.delete(*self.tree.get_children())
        for index, row in enumerate(self._rows):
            self.tree.insert(
                "",
                "end",
                iid=str(index),
                values=(
                    row.get("product_id", ""),
                    row.get("title", ""),
                    row.get("reason", ""),
                    row.get("added", ""),
                ),
            )

    def _all_rows(self) -> list[dict]:
        try:
            rows = json.loads(self.db.get_setting("violation_blacklist", "[]"))
        except json.JSONDecodeError:
            return []
        return [row for row in rows if isinstance(row, dict)]

    def _save_rows(self, rows: list[dict]) -> None:
        self.db.set_setting(
            "violation_blacklist",
            json.dumps(rows, ensure_ascii=False),
        )
        self._load()

    def _add(self) -> None:
        def save(values: dict[str, str]) -> None:
            product_id = values.get("product_id", "").strip()
            if not product_id:
                messagebox.showwarning("商品ID", "商品ID不能为空。", parent=self)
                return
            self._add_item(
                product_id,
                values.get("title", "").strip(),
                values.get("reason", "").strip(),
            )

        SimpleFieldsDialog(
            self,
            "新增黑名单",
            (
                ("product_id", "商品ID"),
                ("title", "商品标题"),
                ("reason", "违规/拉黑原因"),
            ),
            save,
        )

    def _sync_platform(self) -> None:
        stores = self.db.list_stores()
        if not stores:
            messagebox.showinfo("没有店铺", "请先添加微信店铺。", parent=self)
            return
        if len(stores) == 1:
            store = stores[0]
        else:
            def choose(values: dict[str, str]) -> None:
                try:
                    store_id = int(values.get("store_id", ""))
                except ValueError:
                    messagebox.showwarning("店铺ID", "请输入有效店铺ID。", parent=self)
                    return
                selected = self.db.get_store(store_id)
                if selected is None:
                    messagebox.showwarning("店铺不存在", "没有该店铺。", parent=self)
                    return
                self._run_platform_sync(selected)

            SimpleFieldsDialog(
                self,
                "同步平台违规",
                (("store_id", "店铺ID"),),
                choose,
                initial={"store_id": str(stores[0].id)},
            )
            return
        self._run_platform_sync(store)

    def _run_platform_sync(self, store) -> None:
        self.window.set_status("正在同步微信平台违规商品…", busy=True)
        self.window.run_task(
            lambda _progress: self.window.service.sync_platform_violation_blacklist(
                store
            ),
            lambda result: (self._load(), self.window.set_status(result.message)),
            lambda error: messagebox.showerror(
                "同步平台违规失败",
                error,
                parent=self,
            ),
            log_title="同步平台违规商品",
        )

    def _add_from_catalog(self) -> None:
        products = self.db.list_products()
        if not products:
            messagebox.showinfo("没有商品", "本地商品库为空。", parent=self)
            return
        dialog = tk.Toplevel(self)
        dialog.title("从商品库添加黑名单")
        dialog.transient(self.winfo_toplevel())
        dialog.geometry("880x520")
        tree = ttk.Treeview(
            dialog,
            columns=("id", "title", "store", "status"),
            show="headings",
            selectmode="extended",
        )
        for key, text, width in (
            ("id", "商品ID", 170),
            ("title", "商品标题", 400),
            ("store", "店铺", 150),
            ("status", "状态", 100),
        ):
            tree.heading(key, text=text)
            tree.column(key, width=width, anchor="w")
        for index, product in enumerate(products):
            tree.insert(
                "",
                "end",
                iid=str(index),
                values=(
                    product["product_id"],
                    product["title"],
                    product["store_name"],
                    product["status_text"],
                ),
            )
        tree.pack(fill="both", expand=True, padx=12, pady=12)

        def add_selected() -> None:
            for iid in tree.selection():
                product = products[int(iid)]
                self._add_item(
                    str(product["product_id"]),
                    str(product["title"]),
                    "商品库手工加入",
                )
            dialog.destroy()

        ttk.Button(dialog, text="加入黑名单", command=add_selected).pack(pady=8)

    def _add_item(self, product_id: str, title: str, reason: str) -> None:
        rows = self._all_rows()
        if any(str(row.get("product_id")) == product_id for row in rows):
            messagebox.showinfo("已在黑名单", "该商品已经在黑名单中。", parent=self)
            return
        rows.append(
            {
                "product_id": product_id,
                "title": title,
                "reason": reason,
                "added": datetime.now().strftime("%Y-%m-%d %H:%M"),
            }
        )
        self._save_rows(rows)

    def _remove(self) -> None:
        selection = self.tree.selection()
        if not selection:
            messagebox.showinfo("请选择商品", "请先选择黑名单商品。", parent=self)
            return
        ids = {
            str(self._rows[int(iid)].get("product_id"))
            for iid in selection
            if iid.isdigit() and int(iid) < len(self._rows)
        }
        rows = [
            row
            for row in self._all_rows()
            if str(row.get("product_id")) not in ids
        ]
        self._save_rows(rows)

    def _export(self) -> None:
        if not self._rows:
            messagebox.showinfo("没有数据", "当前黑名单为空。", parent=self)
            return
        path = filedialog.asksaveasfilename(
            parent=self,
            title="导出违规商品黑名单",
            defaultextension=".xlsx",
            filetypes=[("Excel 工作簿", "*.xlsx")],
            initialfile=f"违规商品黑名单_{date.today():%Y%m%d}.xlsx",
        )
        if not path:
            return
        headers = ["商品ID", "商品标题", "违规/拉黑原因", "加入日期"]
        rows = [
            [
                row.get("product_id", ""),
                row.get("title", ""),
                row.get("reason", ""),
                row.get("added", ""),
            ]
            for row in self._rows
        ]
        try:
            export_xlsx(path, "违规黑名单", headers, rows)
        except Exception as exc:
            messagebox.showerror("导出失败", str(exc), parent=self)
            return
        self.window.set_status(f"已导出：{Path(path).name}")
