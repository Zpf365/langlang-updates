from __future__ import annotations

import json
import tkinter as tk
from datetime import date, datetime, timedelta
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import TYPE_CHECKING, Any

from ..exporter import export_csv, export_xlsx
from ..models import is_unclaimed_gift_order, order_counts_as_transaction
from .dialogs import SimpleFieldsDialog
from .pages import BasePage, OrdersPage, SettingsPage
from .skeletons import ProcurementPage

if TYPE_CHECKING:
    from .app import MainWindow


ROUTES = (
    ("orders", "订单管理"),
    ("procurement", "采购账号"),
    ("store_after_sale", "店铺售后"),
    ("transit_after_sale", "中转售后"),
    ("profit", "利润统计"),
    ("messages", "消息管理"),
    ("logs", "软件日志"),
    ("settings", "设置中心"),
)


def _money(value: int) -> str:
    return f"¥{int(value) / 100:,.2f}"


def _format_timestamp(value: int | None) -> str:
    if not value:
        return "—"
    try:
        return datetime.fromtimestamp(int(value)).strftime("%Y-%m-%d %H:%M:%S")
    except (OSError, OverflowError, ValueError):
        return "—"


class WorkbenchTablePage(BasePage):
    title = ""
    subtitle = ""

    def __init__(self, parent: tk.Widget, window: "MainWindow") -> None:
        super().__init__(parent, window)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)
        self.controls = ttk.Frame(self)
        self.controls.grid(row=0, column=0, sticky="ew", pady=(0, 7))
        self.summary_var = tk.StringVar(value="")
        self.summary_label = ttk.Label(
            self,
            textvariable=self.summary_var,
            style="Subtitle.TLabel",
        )
        self.summary_label.grid(row=1, column=0, sticky="w", pady=(0, 8))
        self.table = ttk.Treeview(self, show="headings")
        self.table.grid(row=2, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(
            self,
            orient="vertical",
            command=self.table.yview,
        )
        scrollbar.grid(row=2, column=1, sticky="ns")
        horizontal = ttk.Scrollbar(
            self,
            orient="horizontal",
            command=self.table.xview,
        )
        horizontal.grid(row=4, column=0, sticky="ew")
        self.table.configure(
            yscrollcommand=scrollbar.set,
            xscrollcommand=horizontal.set,
            selectmode="extended",
        )

    def _set_columns(
        self,
        columns: tuple[tuple[str, str, int], ...],
    ) -> None:
        keys = tuple(item[0] for item in columns)
        self.table.configure(columns=keys)
        for key, label, width in columns:
            self.table.heading(key, text=label)
            self.table.column(key, width=width, anchor="center")

    def _selected_rows(
        self,
        rows: dict[str, dict[str, Any]],
    ) -> list[dict[str, Any]]:
        return [
            rows[iid]
            for iid in self.table.selection()
            if iid in rows
        ]

    def _export_rows(
        self,
        *,
        title: str,
        headers: list[str],
        rows: list[list[Any]],
    ) -> None:
        if not rows:
            messagebox.showinfo(
                "没有数据",
                "当前筛选结果中没有可导出的记录。",
                parent=self,
            )
            return
        path = filedialog.asksaveasfilename(
            parent=self,
            title=title,
            defaultextension=".xlsx",
            filetypes=(
                ("Excel 工作簿", "*.xlsx"),
                ("CSV 文件", "*.csv"),
            ),
            initialfile=f"{title}_{date.today():%Y%m%d}.xlsx",
        )
        if not path:
            return
        target = Path(path)
        try:
            if target.suffix.lower() == ".csv":
                export_csv(str(target), headers, rows)
            else:
                export_xlsx(str(target), title[:31] or "数据", headers, rows)
        except Exception as exc:
            messagebox.showerror("导出失败", str(exc), parent=self)
            return
        self.window.set_status(f"已导出：{target.name}")


class StoreAfterSalePage(WorkbenchTablePage):
    title = "店铺售后"
    subtitle = "待处理、仅退款、退货退款、换货、投诉和自动售后"

    def __init__(self, parent, window) -> None:
        super().__init__(parent, window)
        self._rows: dict[str, dict[str, Any]] = {}
        self._filtered_rows: list[dict[str, Any]] = []
        self._set_columns(
            (
                ("order", "店铺订单信息", 260),
                ("amount", "订单金额", 100),
                ("purchase", "采购信息", 230),
                ("logistics", "物流信息", 220),
                ("state", "售后状态", 150),
                ("operation", "操作", 110),
            )
        )
        self.after_sale_tab = tk.StringVar(value="待处理")
        self.platform_var = tk.StringVar(value="全部平台")
        self.store_filter_var = tk.StringVar(value="全部店铺")
        self.order_filter_var = tk.StringVar()
        self.title_filter_var = tk.StringVar()
        self.phone_filter_var = tk.StringVar()
        self.sort_var = tk.StringVar(value="按最新申请时间排序")
        self.start_var = tk.StringVar(
            value=(date.today() - timedelta(days=7)).isoformat()
        )
        self.end_var = tk.StringVar(value=date.today().isoformat())

        actions = ttk.Frame(self.controls)
        actions.pack(fill="x")
        ttk.Button(actions, text="移除", command=self._remove_selected).pack(
            side="left", padx=(0, 6)
        )
        ttk.Button(
            actions,
            text="批量备注插旗",
            command=self._batch_note,
        ).pack(side="left", padx=(0, 6))
        operations = ttk.Menubutton(actions, text="批量操作 ▾")
        operation_menu = tk.Menu(operations, tearoff=0)
        for label, action in (
            ("同意售后", "accept"),
            ("拒绝售后", "reject"),
            ("协商售后", "negotiate"),
            ("同意换货", "accept_exchange"),
            ("拒绝换货", "reject_exchange"),
        ):
            operation_menu.add_command(
                label=label,
                command=lambda key=action, text=label: self._apply_action(
                    key,
                    text,
                ),
            )
        operations.configure(menu=operation_menu)
        operations.pack(side="left", padx=(0, 6))
        ttk.Button(
            actions,
            text="自动售后设置",
            command=self._auto_settings,
        ).pack(side="left", padx=(0, 6))
        ttk.Button(
            actions,
            text="自动售后日志",
            command=self._show_auto_log,
        ).pack(side="left", padx=(0, 6))
        ttk.Button(
            actions,
            text="同步售后列表",
            command=self._sync_after_sales,
        ).pack(side="left", padx=(0, 6))
        ttk.Button(
            actions,
            text="开始自动售后",
            command=self._start_auto_after_sale,
        ).pack(side="left", padx=(0, 6))
        ttk.Button(actions, text="导出", command=self._export).pack(
            side="left", padx=(0, 6)
        )
        ttk.Button(
            actions,
            text="下载记录",
            command=self._show_auto_log,
        ).pack(side="left", padx=(0, 6))

        filters = ttk.Frame(self.controls)
        filters.pack(fill="x", pady=(6, 0))
        ttk.Label(filters, text="平台 |").pack(side="left")
        ttk.Combobox(
            filters,
            textvariable=self.platform_var,
            values=("全部平台", "微信小店"),
            state="readonly",
            width=11,
        ).pack(side="left", padx=(3, 8))
        ttk.Label(filters, text="店铺名称 |").pack(side="left")
        self.store_combo = ttk.Combobox(
            filters,
            textvariable=self.store_filter_var,
            state="readonly",
            width=17,
        )
        self.store_combo.pack(side="left", padx=(3, 8))
        ttk.Label(filters, text="订单编号 |").pack(side="left")
        ttk.Entry(
            filters,
            textvariable=self.order_filter_var,
            width=16,
        ).pack(side="left", padx=(3, 8))
        ttk.Label(filters, text="商品标题 |").pack(side="left")
        ttk.Entry(
            filters,
            textvariable=self.title_filter_var,
            width=16,
        ).pack(side="left", padx=(3, 8))
        ttk.Label(filters, text="收货人手机号 |").pack(side="left")
        ttk.Entry(
            filters,
            textvariable=self.phone_filter_var,
            width=14,
        ).pack(side="left", padx=(3, 8))
        ttk.Button(filters, text="搜索", command=self.refresh).pack(
            side="left",
            padx=(2, 4),
        )
        filters_2 = ttk.Frame(self.controls)
        filters_2.pack(fill="x", pady=(6, 0))
        ttk.Label(filters_2, text="最近申请时间").pack(side="left")
        ttk.Entry(
            filters_2,
            textvariable=self.start_var,
            width=12,
        ).pack(side="left", padx=(5, 3))
        ttk.Label(filters_2, text="至").pack(side="left")
        ttk.Entry(
            filters_2,
            textvariable=self.end_var,
            width=12,
        ).pack(side="left", padx=(3, 10))
        ttk.Label(filters_2, text="排序 |").pack(side="left")
        ttk.Combobox(
            filters_2,
            textvariable=self.sort_var,
            values=("按最新申请时间排序", "按最早申请时间排序", "按退款金额从高到低"),
            state="readonly",
            width=18,
        ).pack(side="left", padx=(3, 8))
        ttk.Button(filters_2, text="刷新", command=self.refresh).pack(side="left")

        self.tabs = ttk.Frame(self)
        self.tabs.grid(row=1, column=0, sticky="ew", pady=(0, 5))
        self.tab_buttons: dict[str, ttk.Button] = {}
        for label in (
            "全部售后",
            "待处理",
            "待商家收货",
            "仅退款",
            "退货退款",
            "换货",
            "退款成功",
            "店铺投诉",
            "回收站",
            "拒绝售后",
        ):
            control = ttk.Button(
                self.tabs,
                text=label,
                command=lambda value=label: self._change_tab(value),
            )
            control.pack(side="left", padx=(0, 6))
            self.tab_buttons[label] = control
        self._tabs = tuple(self.tab_buttons)
        self.summary_label.grid_configure(row=2)
        self.table.grid_configure(row=3)
        self.rowconfigure(3, weight=1)
        self.table.bind(
            "<<TreeviewSelect>>",
            lambda _event: self._update_selection_summary(),
        )
        self.table.bind("<Double-1>", self._open_after_sale_detail)

    def _change_tab(self, value: str) -> None:
        self.after_sale_tab.set(value)
        self.refresh()

    @staticmethod
    def _tab_matches(row: dict[str, Any], selected: str) -> bool:
        status = str(row.get("status") or "").upper()
        text = str(row.get("status_text") or "")
        kind = str(row.get("after_sale_type") or "")
        searchable = f"{status} {text} {kind} {row.get('reason_text') or ''}".upper()
        if selected == "全部售后":
            return True
        if selected == "待处理":
            return any(
                marker in searchable
                for marker in (
                    "MERCHANT_PROCESSING",
                    "WAIT_PACKAGE_INTERCEPT",
                    "商家受理中",
                    "平台处理中",
                    "待处理",
                )
            )
        if selected == "待商家收货":
            return "MERCHANT_WAIT_RECEIPT" in status or "待商家收货" in text
        if selected == "仅退款":
            return (
                any(marker in searchable for marker in ("REFUND", "仅退款"))
                and not StoreAfterSalePage._tab_matches(row, "退货退款")
            )
        if selected == "退货退款":
            return any(
                marker in searchable
                for marker in ("RETURN", "退货退款", "退货", "待买家退货")
            )
        if selected == "换货":
            return any(
                marker in searchable
                for marker in ("EXCHANGE", "RESHIP", "换货", "补寄")
            )
        if selected == "退款成功":
            return any(
                marker in searchable
                for marker in ("REFUND_SUCCESS", "RETURN_SUCCESS", "退款完成", "退款成功")
            )
        if selected == "店铺投诉":
            return "投诉" in searchable or "COMPLAINT" in status
        if selected == "回收站":
            return "CANCEL" in status or "取消" in text or "关闭" in text
        if selected == "拒绝售后":
            return "REJECT" in status or "拒绝" in text
        return True

    @staticmethod
    def _parse_timestamp(value: Any) -> int:
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    def _base_rows(self) -> list[dict[str, Any]]:
        stores = {int(store.id): store for store in self.db.list_stores()}
        self.store_combo["values"] = [
            "全部店铺",
            *(
                f"{store.name} · #{store.id}"
                for store in stores.values()
            ),
        ]
        if self.store_filter_var.get() not in self.store_combo["values"]:
            self.store_filter_var.set("全部店铺")
        orders = {
            (int(row["store_id"]), str(row["order_id"])): row
            for row in self.db.list_orders(limit=100000)
        }
        procurement = {
            int(row["id"]): row
            for row in self.db.list_procurement_orders()
        }
        links: dict[tuple[int, str], list[dict[str, Any]]] = {}
        for link in self.db.list_procurement_links():
            key = (
                int(link["store_id"]),
                str(link["order_id"]),
            )
            links.setdefault(key, []).append(link)
        rows: list[dict[str, Any]] = []
        for raw_row in self.db.list_after_sales():
            store_id = int(raw_row.get("store_id") or 0)
            order_id = str(raw_row.get("order_id") or "")
            key = (store_id, order_id)
            order = orders.get(key, {})
            store = stores.get(store_id)
            store_name = store.name if store is not None else f"店铺 #{store_id}"
            try:
                raw = json.loads(raw_row.get("raw_json") or "{}")
            except (TypeError, ValueError):
                raw = {}
            raw = raw if isinstance(raw, dict) else {}
            order_links = links.get(key, [])
            purchases = [
                procurement.get(int(link["procurement_order_id"]), {})
                for link in order_links
            ]
            account_text = " / ".join(
                sorted(
                    {
                        str(item.get("account_label") or "").strip()
                        for item in purchases
                        if str(item.get("account_label") or "").strip()
                    }
                )
            )
            purchase_text = " / ".join(
                str(item.get("external_order_id") or "").strip()
                for item in purchases
                if str(item.get("external_order_id") or "").strip()
            )
            logistics_parts = [
                value
                for value in (
                    str(order.get("carrier") or "").strip(),
                    str(order.get("waybill_id") or "").strip(),
                )
                if value
            ]
            logistics_parts.extend(
                value
                for item in purchases
                for value in (
                    str(item.get("mail_no") or "").strip(),
                    str(item.get("pickup_code") or "").strip(),
                )
                if value
            )
            rows.append(
                {
                    **raw_row,
                    "store_name": store_name,
                    "order_status_text": str(
                        order.get("status_text")
                        or raw.get("order_status_text")
                        or ""
                    ),
                    "order_amount": int(
                        order.get("total_amount")
                        or raw.get("order_amount")
                        or raw_row.get("refund_amount")
                        or 0
                    ),
                    "buyer_name": str(
                        order.get("buyer_name")
                        or raw.get("buyer_name")
                        or ""
                    ),
                    "phone": str(order.get("phone") or ""),
                    "item_summary": str(
                        order.get("item_summary")
                        or raw.get("product_title")
                        or raw.get("title")
                        or ""
                    ),
                    "purchase_account": account_text,
                    "purchase_order": purchase_text,
                    "logistics": " · ".join(
                        dict.fromkeys(logistics_parts)
                    ),
                    "store": store,
                }
            )
        start_text = self.start_var.get().strip()
        end_text = self.end_var.get().strip()
        try:
            start_time = (
                int(datetime.strptime(start_text, "%Y-%m-%d").timestamp())
                if start_text
                else 0
            )
            end_time = (
                int(datetime.strptime(end_text, "%Y-%m-%d").timestamp())
                + 86399
                if end_text
                else 0
            )
        except ValueError:
            messagebox.showwarning(
                "申请时间",
                "日期格式应为 YYYY-MM-DD。",
                parent=self,
            )
            return []
        order_term = self.order_filter_var.get().strip().casefold()
        title_term = self.title_filter_var.get().strip().casefold()
        phone_term = self.phone_filter_var.get().strip()
        selected_store = self.store_filter_var.get()
        filtered: list[dict[str, Any]] = []
        for row in rows:
            apply_time = int(
                row.get("update_time")
                or row.get("create_time")
                or 0
            )
            if start_time and apply_time and apply_time < start_time:
                continue
            if end_time and apply_time and apply_time > end_time:
                continue
            if selected_store != "全部店铺":
                store_id_text = f"#{int(row.get('store_id') or 0)}"
                if store_id_text not in selected_store:
                    continue
            if order_term and order_term not in str(
                row.get("order_id") or ""
            ).casefold():
                continue
            if title_term and title_term not in str(
                row.get("item_summary") or ""
            ).casefold():
                continue
            if phone_term and phone_term not in str(row.get("phone") or ""):
                continue
            filtered.append(row)
        mode = self.sort_var.get()
        if mode == "按最早申请时间排序":
            filtered.sort(
                key=lambda item: int(
                    item.get("update_time")
                    or item.get("create_time")
                    or 0
                )
            )
        elif mode == "按退款金额从高到低":
            filtered.sort(
                key=lambda item: int(item.get("refund_amount") or 0),
                reverse=True,
            )
        else:
            filtered.sort(
                key=lambda item: int(
                    item.get("update_time")
                    or item.get("create_time")
                    or 0
                ),
                reverse=True,
            )
        return filtered

    def refresh(self) -> None:
        rows = self._base_rows()
        counts = {
            label: sum(self._tab_matches(row, label) for row in rows)
            for label in self._tabs
        }
        selected = self.after_sale_tab.get()
        for label, control in self.tab_buttons.items():
            count = counts.get(label, 0)
            control.configure(
                text=f"{label} ({count})",
                style=(
                    "Primary.TButton"
                    if label == selected
                    else "Secondary.TButton"
                ),
            )
        filtered = [
            row for row in rows if self._tab_matches(row, selected)
        ]
        self._filtered_rows = filtered
        self._rows.clear()
        self.table.delete(*self.table.get_children())
        for index, row in enumerate(filtered):
            iid = str(index)
            self._rows[iid] = row
            order_text = (
                f"{row.get('store_name')} / {row.get('buyer_name') or '买家待解密'}"
                f"\n订单：{row.get('order_id') or '—'}"
                f" · 店铺状态：{row.get('order_status_text') or '—'}"
                f"\n商品：{str(row.get('item_summary') or '—')[:80]}"
            )
            purchase_text = (
                f"{row.get('purchase_account') or '未匹配采购账号'}"
                f"\n{row.get('purchase_order') or '无采购单号'}"
            )
            logistics = row.get("logistics") or "暂无物流"
            self.table.insert(
                "",
                "end",
                iid=iid,
                values=(
                    order_text,
                    _money(int(row.get("order_amount") or 0)),
                    purchase_text,
                    logistics,
                    row.get("status_text") or row.get("status") or "",
                    "售后处理",
                ),
            )
        self.summary_var.set(
            f"{selected} · 共 {len(filtered)} 条售后单"
        )
        self._update_selection_summary()

    def _update_selection_summary(self) -> None:
        selected = len(self.table.selection())
        if selected:
            self.summary_var.set(
                f"{self.after_sale_tab.get()} · 共 {len(self._filtered_rows)} 条"
                f" · 已选 {selected} 条"
            )

    def _selected_after_sales(self) -> list[dict[str, Any]]:
        return self._selected_rows(self._rows)

    def _remove_selected(self) -> None:
        rows = self._selected_after_sales()
        if not rows:
            messagebox.showinfo("请选择售后单", "请先选择至少一条售后记录。", parent=self)
            return
        if not messagebox.askyesno(
            "移出售后列表",
            f"将从本机移除 {len(rows)} 条售后记录，不会修改微信平台上的售后单。"
            "是否继续？",
            parent=self,
        ):
            return
        for row in rows:
            self.db.delete_after_sale(
                int(row["store_id"]),
                str(row["after_sale_order_id"]),
            )
        self.refresh()
        self.window.set_status(f"已从本机移除 {len(rows)} 条售后记录")

    def _batch_note(self) -> None:
        rows = self._selected_after_sales()
        if not rows:
            messagebox.showinfo("请选择售后单", "请先选择至少一条售后记录。", parent=self)
            return

        def save(values: dict[str, str]) -> None:
            note = str(values.get("note") or "").strip()
            if not note:
                messagebox.showwarning("售后备注", "请输入备注。", parent=self)
                return
            def work(progress):
                changed = 0
                total = len(rows)
                for index, row in enumerate(rows, 1):
                    order_id = str(row.get("order_id") or "")
                    if not order_id:
                        continue
                    current = self.db.get_order_workbench_row(
                        int(row["store_id"]),
                        order_id,
                    ) or {}
                    previous = str(current.get("note") or "").strip()
                    combined = (
                        f"{previous}\n{note}".strip()
                        if previous
                        else note
                    )
                    self.db.update_order_workbench(
                        int(row["store_id"]),
                        order_id,
                        note=combined,
                        is_marked=1,
                    )
                    changed += 1
                    if progress:
                        progress(
                            f"正在保存第 {index}/{total} 条售后备注",
                            index,
                            total,
                        )
                return changed

            def done(changed: int) -> None:
                self.refresh()
                self.window.set_status(
                    f"已为 {changed} 条售后订单添加备注并插旗"
                )

            def failed(error: str) -> None:
                self.window.set_status("批量备注保存失败")
                messagebox.showerror(
                    "批量备注保存失败",
                    error,
                    parent=self,
                )

            self.window.set_status(
                f"正在后台保存 {len(rows)} 条售后备注…",
                busy=True,
            )
            self.window.run_task(
                work,
                done,
                failed,
                log_title="批量售后备注",
                task_scope="workbench:batch-note",
            )

        SimpleFieldsDialog(
            self,
            "批量备注插旗",
            (("note", "售后备注"),),
            save,
        )

    def _apply_action(self, action: str, label: str) -> None:
        rows = self._selected_after_sales()
        if not rows:
            messagebox.showinfo("请选择售后单", "请先选择至少一条售后记录。", parent=self)
            return
        if not messagebox.askyesno(
            label,
            f"即将向微信小店提交 {len(rows)} 条“{label}”操作。\n"
            "该操作会修改平台售后状态，是否继续？",
            parent=self,
        ):
            return
        self.window.set_status(f"正在提交{label}…", busy=True)

        def work(_progress) -> list[str]:
            errors: list[str] = []
            for index, row in enumerate(rows, 1):
                store = self.db.get_store(int(row["store_id"]))
                if store is None:
                    errors.append(f"{row.get('after_sale_order_id')}: 店铺不存在")
                    continue
                try:
                    self.window.service.aftersale_action(
                        store,
                        str(row["after_sale_order_id"]),
                        action,
                    )
                except Exception as exc:
                    errors.append(
                        f"{store.name}/{row.get('after_sale_order_id')}: {exc}"
                    )
            return errors

        def done(errors: list[str]) -> None:
            self.refresh()
            self.window.set_status(
                f"{label}完成，成功 {len(rows) - len(errors)} 条，失败 {len(errors)} 条",
                busy=False,
            )
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
            log_title=f"店铺售后：{label}",
        )

    def _auto_settings(self) -> None:
        try:
            current = json.loads(
                self.db.get_setting("after_sale_auto_config", "{}")
            )
        except (TypeError, ValueError):
            current = {}
        dialog = tk.Toplevel(self)
        dialog.title("自动售后设置")
        dialog.geometry("560x330")
        dialog.transient(self.winfo_toplevel())
        dialog.grab_set()
        panel = ttk.Frame(dialog, padding=18)
        panel.pack(fill="both", expand=True)
        enabled = tk.BooleanVar(value=bool(current.get("enabled", False)))
        action_var = tk.StringVar(value=str(current.get("action") or "accept"))
        max_amount = tk.StringVar(
            value=f"{int(current.get('max_amount') or 0) / 100:.2f}"
        )
        allowed_types = tk.StringVar(
            value=str(current.get("allowed_types") or "仅退款,退货退款")
        )
        tk.Checkbutton(
            panel,
            text="启用规则校验后的自动售后",
            variable=enabled,
        ).pack(anchor="w", pady=(0, 10))
        form = ttk.Frame(panel)
        form.pack(fill="x")
        form.columnconfigure(1, weight=1)
        ttk.Label(form, text="默认处理动作").grid(
            row=0, column=0, sticky="w", padx=(0, 10), pady=5
        )
        ttk.Combobox(
            form,
            textvariable=action_var,
            values=("accept", "reject", "negotiate"),
            state="readonly",
        ).grid(row=0, column=1, sticky="ew", pady=5)
        ttk.Label(form, text="单笔金额上限（元）").grid(
            row=1, column=0, sticky="w", padx=(0, 10), pady=5
        )
        ttk.Entry(form, textvariable=max_amount).grid(
            row=1, column=1, sticky="ew", pady=5
        )
        ttk.Label(form, text="允许的售后类型").grid(
            row=2, column=0, sticky="w", padx=(0, 10), pady=5
        )
        ttk.Entry(form, textvariable=allowed_types).grid(
            row=2, column=1, sticky="ew", pady=5
        )
        ttk.Label(
            panel,
            text="实际执行前仍会再次确认；不支持、金额超限或状态不明的售后会跳过。",
        ).pack(anchor="w", pady=(10, 0))

        def save() -> None:
            try:
                amount = int(round(float(max_amount.get() or "0") * 100))
            except ValueError:
                messagebox.showwarning("自动售后", "金额上限必须是数字。", parent=dialog)
                return
            payload = {
                "enabled": enabled.get(),
                "action": action_var.get(),
                "max_amount": max(0, amount),
                "allowed_types": allowed_types.get().strip(),
            }
            self.db.set_setting(
                "after_sale_auto_config",
                json.dumps(payload, ensure_ascii=False),
            )
            log_id = self.db.start_task_log(
                "after_sale",
                "自动售后设置",
                "配置已保存",
            )
            self.db.finish_task_log(log_id, "success", "配置已保存")
            dialog.destroy()
            self.window.set_status("自动售后设置已保存")

        actions = ttk.Frame(panel)
        actions.pack(fill="x", pady=(14, 0))
        ttk.Button(actions, text="取消", command=dialog.destroy).pack(side="right")
        ttk.Button(actions, text="保存", command=save).pack(
            side="right", padx=(0, 8)
        )

    def _show_auto_log(self) -> None:
        rows = [
            row
            for row in self.db.list_task_logs(limit=2000)
            if "售后" in str(row.get("operation") or "")
            or str(row.get("scope") or "") == "after_sale"
        ]
        dialog = tk.Toplevel(self)
        dialog.title("自动售后日志")
        dialog.geometry("980x560")
        tree = ttk.Treeview(
            dialog,
            columns=("time", "store", "operation", "status", "message"),
            show="headings",
        )
        for key, label, width in (
            ("time", "时间", 150),
            ("store", "店铺", 150),
            ("operation", "操作", 180),
            ("status", "状态", 90),
            ("message", "结果", 380),
        ):
            tree.heading(key, text=label)
            tree.column(key, width=width, anchor="w")
        for row in rows:
            tree.insert(
                "",
                "end",
                values=(
                    str(row.get("started_at") or "")[:19].replace("T", " "),
                    row.get("store_name") or "",
                    row.get("operation") or "",
                    row.get("status") or "",
                    row.get("message") or "",
                ),
            )
        tree.pack(fill="both", expand=True, padx=12, pady=12)
        ttk.Button(dialog, text="关闭", command=dialog.destroy).pack(
            pady=(0, 12)
        )

    def _sync_after_sales(self) -> None:
        stores = self.db.list_stores()
        if not stores:
            messagebox.showinfo("没有店铺", "请先添加微信小店。", parent=self)
            return
        selected = self.store_filter_var.get()
        if selected != "全部店铺":
            store_id = int(selected.rsplit("#", 1)[-1])
            stores = [store for store in stores if store.id == store_id]
        try:
            start_date = datetime.strptime(
                self.start_var.get().strip(),
                "%Y-%m-%d",
            ).date()
            end_date = datetime.strptime(
                self.end_var.get().strip(),
                "%Y-%m-%d",
            ).date()
        except ValueError:
            messagebox.showwarning(
                "日期范围",
                "申请时间格式应为 YYYY-MM-DD。",
                parent=self,
            )
            return
        if end_date < start_date:
            messagebox.showwarning("日期范围", "结束日期不能早于开始日期。", parent=self)
            return
        if (end_date - start_date).days > 31:
            messagebox.showwarning("日期范围", "单次同步最多选择 31 天。", parent=self)
            return
        days = [
            start_date + timedelta(days=offset)
            for offset in range((end_date - start_date).days + 1)
        ]
        self.window.set_status("正在同步售后列表…", busy=True)

        def work(progress) -> list[str]:
            errors: list[str] = []
            total = len(stores) * len(days)
            current = 0
            for store in stores:
                for day in days:
                    current += 1
                    progress(
                        f"正在同步 {store.name} · {day.isoformat()}",
                        current,
                        total,
                    )
                    begin = int(
                        datetime.combine(day, datetime.min.time()).timestamp()
                    )
                    finish = begin + 86399
                    try:
                        result = self.window.service.sync_after_sales(
                            store,
                            begin,
                            finish,
                            time_field="update",
                        )
                        if result.errors:
                            errors.extend(
                                f"{store.name}/{day}: {item}"
                                for item in result.errors
                            )
                    except Exception as exc:
                        errors.append(f"{store.name}/{day}: {exc}")
            return errors

        def done(errors: list[str]) -> None:
            self.refresh()
            self.window.set_status(
                f"售后同步完成，失败或跳过 {len(errors)} 项",
                busy=False,
            )
            if errors:
                messagebox.showwarning(
                    "售后同步未全部成功",
                    "\n".join(errors[:10]),
                    parent=self,
                )

        self.window.run_task(
            work,
            done,
            lambda error: messagebox.showerror(
                "售后同步失败",
                error,
                parent=self,
            ),
            lambda message, current, total: self.window.set_status(
                f"{message} {current}/{total}",
                busy=True,
            ),
            log_title="同步店铺售后",
        )

    def _start_auto_after_sale(self) -> None:
        try:
            config = json.loads(
                self.db.get_setting("after_sale_auto_config", "{}")
            )
        except (TypeError, ValueError):
            config = {}
        if not config.get("enabled"):
            messagebox.showinfo(
                "自动售后未启用",
                "请先在“自动售后设置”中启用并保存规则。",
                parent=self,
            )
            return
        allowed = {
            item.strip()
            for item in str(config.get("allowed_types") or "").split(",")
            if item.strip()
        }
        max_amount = int(config.get("max_amount") or 0)
        selected = self._selected_after_sales()
        candidates = selected or list(self._filtered_rows)
        eligible: list[dict[str, Any]] = []
        skipped = 0
        for row in candidates:
            amount = int(row.get("refund_amount") or 0)
            text = f"{row.get('after_sale_type') or ''} {row.get('status_text') or ''}"
            if max_amount and amount > max_amount:
                skipped += 1
                continue
            if allowed and not any(item in text for item in allowed):
                skipped += 1
                continue
            if not self._tab_matches(row, "待处理"):
                skipped += 1
                continue
            eligible.append(row)
        if not eligible:
            messagebox.showinfo(
                "没有可执行售后",
                f"没有符合规则的待处理售后单。已跳过 {skipped} 条。",
                parent=self,
            )
            return
        action = str(config.get("action") or "accept")
        if not messagebox.askyesno(
            "开始自动售后",
            f"将对 {len(eligible)} 条符合条件的售后执行“{action}”，"
            f"跳过 {skipped} 条。\n这会修改微信平台状态，是否继续？",
            parent=self,
        ):
            return
        self.window.set_status("正在执行自动售后…", busy=True)

        def work(progress) -> list[str]:
            errors: list[str] = []
            for index, row in enumerate(eligible, 1):
                progress(
                    f"自动售后 {index}/{len(eligible)}",
                    index,
                    len(eligible),
                )
                store = self.db.get_store(int(row["store_id"]))
                if store is None:
                    errors.append(f"{row.get('after_sale_order_id')}: 店铺不存在")
                    continue
                try:
                    self.window.service.aftersale_action(
                        store,
                        str(row["after_sale_order_id"]),
                        action,
                    )
                except Exception as exc:
                    errors.append(
                        f"{store.name}/{row.get('after_sale_order_id')}: {exc}"
                    )
            return errors

        def done(errors: list[str]) -> None:
            self.refresh()
            self.window.set_status(
                f"自动售后完成，成功 {len(eligible) - len(errors)} 条，"
                f"失败 {len(errors)} 条，规则跳过 {skipped} 条",
                busy=False,
            )
            if errors:
                messagebox.showwarning(
                    "自动售后部分失败",
                    "\n".join(errors[:10]),
                    parent=self,
                )

        self.window.run_task(
            work,
            done,
            lambda error: messagebox.showerror(
                "自动售后失败",
                error,
                parent=self,
            ),
            lambda message, current, total: self.window.set_status(
                f"{message} {current}/{total}",
                busy=True,
            ),
            log_title="自动售后",
        )

    def _open_after_sale_detail(self, _event=None) -> None:
        rows = self._selected_after_sales()
        if not rows:
            return
        row = rows[0]
        try:
            raw = json.loads(row.get("raw_json") or "{}")
        except (TypeError, ValueError):
            raw = {}
        detail = json.dumps(raw, ensure_ascii=False, indent=2)
        dialog = tk.Toplevel(self)
        dialog.title(f"售后详情 · {row.get('after_sale_order_id') or ''}")
        dialog.geometry("900x640")
        text = tk.Text(dialog, wrap="none")
        text.pack(fill="both", expand=True, padx=12, pady=12)
        text.insert("1.0", detail)
        text.configure(state="disabled")

    def _export(self) -> None:
        headers = [
            "店铺",
            "订单号",
            "售后单号",
            "售后状态",
            "售后类型",
            "订单金额",
            "退款金额",
            "买家",
            "商品",
            "采购账号",
            "最近申请时间",
        ]
        rows = [
            [
                item.get("store_name") or "",
                item.get("order_id") or "",
                item.get("after_sale_order_id") or "",
                item.get("status_text") or item.get("status") or "",
                item.get("after_sale_type") or "",
                int(item.get("order_amount") or 0) / 100,
                int(item.get("refund_amount") or 0) / 100,
                item.get("buyer_name") or "",
                item.get("item_summary") or "",
                item.get("purchase_account") or "",
                _format_timestamp(
                    int(item.get("update_time") or item.get("create_time") or 0)
                ),
            ]
            for item in self._filtered_rows
        ]
        self._export_rows(
            title="店铺售后",
            headers=headers,
            rows=rows,
        )


class ProfitStatsPage(WorkbenchTablePage):
    title = "利润统计"
    subtitle = "按订单核对采购成本与利润，未关联采购的订单显示为待核算"

    def __init__(self, parent, window) -> None:
        super().__init__(parent, window)
        self.view_var = tk.StringVar(value="利润统计")
        self.report_view_var = tk.StringVar(value="订单报表")
        self.platform_var = tk.StringVar(value="全部平台")
        self.store_var = tk.StringVar(value="全部店铺")
        self.group_var = tk.StringVar(value="全部分组")
        self.account_var = tk.StringVar(value="全部采购账号")
        self.service_var = tk.StringVar(value="")
        self.order_no_var = tk.StringVar()
        self.product_id_var = tk.StringVar()
        self.source_var = tk.StringVar(value="全部数据")
        self.status_var = tk.StringVar(value="全部状态")
        self.start_var = tk.StringVar(
            value=(date.today() - timedelta(days=30)).isoformat()
        )
        self.end_var = tk.StringVar(value=date.today().isoformat())
        self._rows: dict[str, dict[str, Any]] = {}
        self._filtered_rows: list[dict[str, Any]] = []
        self._raw_order_rows: list[dict[str, Any]] = []

        top_tabs = ttk.Frame(self.controls)
        top_tabs.pack(fill="x")
        for label in ("利润统计", "店铺统计", "商品统计"):
            ttk.Button(
                top_tabs,
                text=label,
                command=lambda value=label: self._change_view(value),
            ).pack(
                side="left",
                padx=(0, 8),
            )
        periods = ttk.Frame(self.controls)
        periods.pack(fill="x", pady=(6, 0))
        for label in ("订单报表", "日报表", "周报表", "月报表", "年报表"):
            ttk.Radiobutton(
                periods,
                text=label,
                value=label,
                variable=self.report_view_var,
                command=self.refresh,
            ).pack(side="left", padx=(0, 14))
        filter_bar = ttk.Frame(self.controls)
        filter_bar.pack(fill="x", pady=(6, 0))
        ttk.Label(filter_bar, text="平台 |").pack(side="left")
        ttk.Combobox(
            filter_bar,
            textvariable=self.platform_var,
            values=("全部平台", "微信小店"),
            state="readonly",
            width=10,
        ).pack(side="left", padx=(3, 8))
        ttk.Label(filter_bar, text="店铺 |").pack(side="left")
        self.store_combo = ttk.Combobox(
            filter_bar,
            textvariable=self.store_var,
            state="readonly",
            width=16,
        )
        self.store_combo.pack(side="left", padx=(3, 8))
        ttk.Label(filter_bar, text="店铺分组 |").pack(side="left")
        self.group_combo = ttk.Combobox(
            filter_bar,
            textvariable=self.group_var,
            state="readonly",
            width=13,
        )
        self.group_combo.pack(side="left", padx=(3, 8))
        ttk.Label(filter_bar, text="采购账号 |").pack(side="left")
        self.account_combo = ttk.Combobox(
            filter_bar,
            textvariable=self.account_var,
            state="readonly",
            width=14,
        )
        self.account_combo.pack(side="left", padx=(3, 8))
        ttk.Label(filter_bar, text="客服名称 |").pack(side="left")
        ttk.Entry(
            filter_bar,
            textvariable=self.service_var,
            width=10,
        ).pack(side="left", padx=(3, 8))
        filter_bar_2 = ttk.Frame(self.controls)
        filter_bar_2.pack(fill="x", pady=(6, 0))
        ttk.Label(filter_bar_2, text="卖出单号 |").pack(side="left")
        ttk.Entry(
            filter_bar_2,
            textvariable=self.order_no_var,
            width=17,
        ).pack(side="left", padx=(3, 8))
        ttk.Label(filter_bar_2, text="商品ID |").pack(side="left")
        ttk.Entry(
            filter_bar_2,
            textvariable=self.product_id_var,
            width=15,
        ).pack(side="left", padx=(3, 8))
        ttk.Label(filter_bar_2, text="数据来源 |").pack(side="left")
        ttk.Combobox(
            filter_bar_2,
            textvariable=self.source_var,
            values=("全部数据", "已采购", "未采购"),
            state="readonly",
            width=10,
        ).pack(side="left", padx=(3, 8))
        ttk.Label(filter_bar_2, text="交易状态 |").pack(side="left")
        ttk.Combobox(
            filter_bar_2,
            textvariable=self.status_var,
            values=(
                "全部状态",
                "买家已付款",
                "卖家已发货",
                "待收货",
                "交易成功",
                "售后完成",
            ),
            state="readonly",
            width=12,
        ).pack(side="left", padx=(3, 8))
        ttk.Label(filter_bar_2, text="成交时间").pack(side="left")
        ttk.Entry(
            filter_bar_2,
            textvariable=self.start_var,
            width=12,
        ).pack(side="left", padx=(5, 3))
        ttk.Label(filter_bar_2, text="至").pack(side="left")
        ttk.Entry(
            filter_bar_2,
            textvariable=self.end_var,
            width=12,
        ).pack(side="left", padx=(3, 8))
        ttk.Button(
            filter_bar_2,
            text="开始统计",
            command=self.refresh,
        ).pack(side="left")
        ttk.Button(
            filter_bar_2,
            text="导出",
            command=self._export,
        ).pack(side="left", padx=5)
        ttk.Button(
            filter_bar_2,
            text="下载记录",
            command=self._show_download_log,
        ).pack(side="left")
        self._set_columns(
            (
                ("store", "店铺名称", 120),
                ("sale_order", "卖出订单号/成交时间", 180),
                ("product", "商品ID/卖出数量", 150),
                ("sub_order", "子订单编号", 140),
                ("sale_status", "交易状态", 100),
                ("buyer", "买家账户", 110),
                ("receipt", "实收款", 90),
                ("commission", "佣金", 80),
                ("platform_fee", "平台费用", 85),
                ("purchase_order", "采购订单号/采购时间", 180),
                ("purchase_item", "采购ID/采购数量", 150),
                ("purchase_status", "交易状态", 100),
                ("purchase_account", "采购账号", 130),
                ("purchase_price", "采购价", 90),
                ("profit", "利润", 80),
                ("margin", "利润率", 80),
            )
        )
        self.table.bind("<Double-1>", self._show_selected_detail)

    @staticmethod
    def _fee_value(raw: dict[str, Any], names: tuple[str, ...]) -> int:
        def walk(value: Any) -> Any:
            if isinstance(value, dict):
                for name in names:
                    if name in value and value[name] not in (None, ""):
                        return value[name]
                for nested in value.values():
                    found = walk(nested)
                    if found not in (None, ""):
                        return found
            elif isinstance(value, list):
                for nested in value:
                    found = walk(nested)
                    if found not in (None, ""):
                        return found
            return None

        value = walk(raw)
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _status_text(order: dict[str, Any]) -> str:
        return {
            17: "待核销",
            20: "买家已付款",
            21: "卖家已发货",
            30: "待收货",
            100: "交易成功",
            200: "售后完成",
        }.get(
            int(order.get("status") or 0),
            str(order.get("status_text") or ""),
        )

    def _load_filter_choices(
        self,
        stores: list[Any],
        accounts: list[str],
    ) -> None:
        self.store_combo["values"] = (
            "全部店铺",
            *(
                f"{store.name} · #{store.id}"
                for store in stores
            ),
        )
        if self.store_var.get() not in self.store_combo["values"]:
            self.store_var.set("全部店铺")
        groups = {
            store.group_name
            for store in stores
            if store.group_name
        }
        self.group_combo["values"] = (
            "全部分组",
            *sorted(groups),
        )
        if self.group_var.get() not in self.group_combo["values"]:
            self.group_var.set("全部分组")
        self.account_combo["values"] = (
            "全部采购账号",
            *sorted(set(accounts)),
        )
        if self.account_var.get() not in self.account_combo["values"]:
            self.account_var.set("全部采购账号")

    def _build_rows(self) -> list[dict[str, Any]]:
        stores = list(self.db.list_stores())
        store_lookup = {int(store.id): store for store in stores}
        orders = [
            row
            for row in self.db.list_orders(limit=100000)
            if not is_unclaimed_gift_order(row)
            and order_counts_as_transaction(row.get("status"))
        ]
        procurement = {
            int(row["id"]): row
            for row in self.db.list_procurement_orders()
        }
        links: dict[tuple[int, str], list[dict[str, Any]]] = {}
        account_values: list[str] = []
        for link in self.db.list_procurement_links():
            key = (int(link["store_id"]), str(link["order_id"]))
            item = procurement.get(int(link["procurement_order_id"]), {})
            enriched = {**link, **item}
            links.setdefault(key, []).append(enriched)
            account = str(
                item.get("account_label")
                or link.get("account_label")
                or ""
            ).strip()
            if account:
                account_values.append(account)
        self._load_filter_choices(stores, account_values)
        workbench = self.db.get_order_workbench()
        try:
            start_time = int(
                datetime.strptime(
                    self.start_var.get().strip(),
                    "%Y-%m-%d",
                ).timestamp()
            )
            end_time = int(
                datetime.strptime(
                    self.end_var.get().strip(),
                    "%Y-%m-%d",
                ).timestamp()
            ) + 86399
        except ValueError:
            messagebox.showwarning(
                "成交时间",
                "日期格式应为 YYYY-MM-DD。",
                parent=self,
            )
            return []
        if end_time < start_time:
            messagebox.showwarning(
                "成交时间",
                "结束日期不能早于开始日期。",
                parent=self,
            )
            return []
        selected_store = self.store_var.get()
        selected_group = self.group_var.get()
        selected_account = self.account_var.get()
        order_term = self.order_no_var.get().strip().casefold()
        product_term = self.product_id_var.get().strip().casefold()
        source_filter = self.source_var.get()
        status_filter = self.status_var.get()
        service_term = self.service_var.get().strip().casefold()
        rows: list[dict[str, Any]] = []
        for order in orders:
            created = int(order.get("create_time") or 0)
            if created and not start_time <= created <= end_time:
                continue
            store_id = int(order["store_id"])
            store = store_lookup.get(store_id)
            if selected_store != "全部店铺":
                if f"#{store_id}" not in selected_store:
                    continue
            if (
                selected_group != "全部分组"
                and (
                    store is None
                    or store.group_name != selected_group
                )
            ):
                continue
            key = (int(order["store_id"]), str(order["order_id"]))
            order_links = links.get(key, [])
            sale = int(order.get("total_amount") or 0)
            if source_filter == "已采购" and not order_links:
                continue
            if source_filter == "未采购" and order_links:
                continue
            status_text = self._status_text(order)
            if status_filter != "全部状态" and status_filter != status_text:
                continue
            if order_term and order_term not in str(order.get("order_id") or "").casefold():
                continue
            try:
                raw = json.loads(order.get("raw_json") or "{}")
            except (TypeError, ValueError):
                raw = {}
            raw = raw if isinstance(raw, dict) else {}
            product_ids, item_count = self._product_ids(raw, order)
            if product_term and not any(
                product_term in value.casefold()
                for value in product_ids
            ):
                continue
            workbench_row = workbench.get(key, {})
            note_text = " ".join(
                (
                    str(workbench_row.get("note") or ""),
                    str(order.get("merchant_notes") or ""),
                )
            ).casefold()
            if service_term and service_term not in note_text:
                continue
            accounts = {
                str(
                    item.get("account_label")
                    or item.get("label")
                    or ""
                ).strip()
                for item in order_links
                if str(
                    item.get("account_label")
                    or item.get("label")
                    or ""
                ).strip()
            }
            if (
                selected_account != "全部采购账号"
                and selected_account not in accounts
            ):
                continue
            cost = sum(
                int(item.get("pay_amount") or 0)
                for item in order_links
            )
            if cost and bool(workbench_row.get("cost")):
                cost = int(workbench_row.get("cost") or 0)
            commission = self._fee_value(
                raw,
                (
                    "commission",
                    "commission_fee",
                    "commission_amount",
                ),
            )
            platform_fee = self._fee_value(
                raw,
                (
                    "platform_fee",
                    "platform_service_fee",
                    "tech_service_fee",
                ),
            )
            explicit_profit = int(workbench_row.get("profit") or 0)
            pending = not order_links and not explicit_profit and not bool(
                workbench_row.get("cost")
            )
            profit = (
                explicit_profit
                if explicit_profit
                else sale - cost - commission - platform_fee
            )
            margin = profit / sale * 100 if sale else 0.0
            purchase_text = "\n".join(
                f"{item.get('external_order_id') or '—'}"
                f" · {_money(int(item.get('pay_amount') or 0))}"
                f" · {item.get('account_label') or '—'}"
                for item in order_links
            )
            product_title = str(order.get("item_summary") or "商品信息待同步")
            rows.append(
                {
                    **order,
                    "status_text": status_text,
                    "store_name": (
                        store.name
                        if store is not None
                        else order.get("store_name") or ""
                    ),
                    "product_ids": product_ids,
                    "product_title": product_title,
                    "item_count": item_count,
                    "sale_amount": sale,
                    "cost": cost,
                    "commission": commission,
                    "platform_fee": platform_fee,
                    "profit": profit,
                    "margin": margin,
                    "pending": pending,
                    "purchase_text": purchase_text,
                    "accounts": "/".join(sorted(accounts)),
                    "purchase_status": " / ".join(
                        str(item.get("status_text") or "")
                        for item in order_links
                        if str(item.get("status_text") or "").strip()
                    ),
                }
            )
        return rows

    @staticmethod
    def _product_ids(
        raw: dict[str, Any],
        order: dict[str, Any],
    ) -> tuple[set[str], int]:
        detail = raw.get("order_detail")
        products = detail.get("product_infos") if isinstance(detail, dict) else []
        ids: set[str] = set()
        count = 0
        if isinstance(products, list):
            for item in products:
                if not isinstance(item, dict):
                    continue
                product_id = str(item.get("product_id") or "").strip()
                if product_id:
                    ids.add(product_id)
                try:
                    count += int(item.get("sku_cnt") or 0)
                except (TypeError, ValueError):
                    pass
        if not ids and str(order.get("item_summary") or ""):
            ids.add(str(order.get("item_summary") or ""))
        return ids, count or 1

    def _change_view(self, value: str) -> None:
        self.view_var.set(value)
        self.refresh()

    @staticmethod
    def _bucket_time(timestamp: int, view: str) -> str:
        if not timestamp:
            return "—"
        value = datetime.fromtimestamp(timestamp)
        if view == "日报表":
            return value.strftime("%Y-%m-%d")
        if view == "周报表":
            year, week, _day = value.isocalendar()
            return f"{year}-W{week:02d}"
        if view == "月报表":
            return value.strftime("%Y-%m")
        if view == "年报表":
            return value.strftime("%Y")
        return "全部"

    def _render_order_report(self) -> None:
        self._set_columns(
            (
                ("store", "店铺名称", 120),
                ("sale_order", "卖出订单号/成交时间", 210),
                ("product", "商品ID/卖出数量", 180),
                ("sale_status", "交易状态", 110),
                ("buyer", "买家账户", 120),
                ("receipt", "实收款", 95),
                ("commission", "佣金", 85),
                ("platform_fee", "平台费用", 90),
                ("purchase_order", "采购订单/采购账号", 220),
                ("purchase_status", "采购状态", 120),
                ("purchase_price", "采购价", 95),
                ("profit", "利润", 90),
                ("margin", "利润率", 85),
            )
        )
        total_profit = 0
        pending = 0
        for index, row in enumerate(self._filtered_rows):
            iid = str(index)
            self._rows[iid] = row
            if row["pending"]:
                pending += 1
            else:
                total_profit += int(row["profit"])
            self.table.insert(
                "",
                "end",
                iid=iid,
                values=(
                    row["store_name"],
                    f"{row.get('order_id') or ''} / "
                    f"{_format_timestamp(int(row.get('create_time') or 0))}",
                    f"{', '.join(sorted(row['product_ids']))}\n"
                    f"卖出数量：{row['item_count']}",
                    row["status_text"],
                    row.get("buyer_name") or "",
                    _money(int(row["sale_amount"])),
                    _money(int(row["commission"]))
                    if row["commission"]
                    else "—",
                    _money(int(row["platform_fee"]))
                    if row["platform_fee"]
                    else "—",
                    row["purchase_text"] or "未关联采购",
                    row["purchase_status"] or "—",
                    _money(int(row["cost"]))
                    if not row["pending"]
                    else "待核算",
                    _money(int(row["profit"]))
                    if not row["pending"]
                    else "待核算",
                    f"{float(row['margin']):.2f}%"
                    if not row["pending"]
                    else "—",
                ),
            )
        self.summary_var.set(
            f"{self.view_var.get()} · 共 {len(self._filtered_rows)} 单"
            f" · 已核实利润 {_money(total_profit)}"
            f" · 待核算 {pending} 单"
        )

    def _render_aggregate(self, mode: str) -> None:
        report_view = self.report_view_var.get()
        groups: dict[tuple[str, str], dict[str, Any]] = {}
        for row in self._filtered_rows:
            period = self._bucket_time(
                int(row.get("create_time") or 0),
                report_view,
            )
            if mode == "store":
                label = str(row["store_name"])
                key = (period, label)
                group = groups.setdefault(
                    key,
                    {
                        "label": label,
                        "orders": 0,
                        "sold": 0,
                        "sale": 0,
                        "cost": 0,
                        "profit": 0,
                        "pending": 0,
                    },
                )
            else:
                label = ", ".join(sorted(row["product_ids"])) or "未知商品"
                key = (period, label)
                group = groups.setdefault(
                    key,
                    {
                        "label": label,
                        "orders": 0,
                        "sold": 0,
                        "sale": 0,
                        "cost": 0,
                        "profit": 0,
                        "pending": 0,
                    },
                )
            group["orders"] += 1
            group["sold"] += int(row["item_count"])
            group["sale"] += int(row["sale_amount"])
            if row["pending"]:
                group["pending"] += 1
            else:
                group["cost"] += int(row["cost"])
                group["profit"] += int(row["profit"])
        if mode == "store":
            self._set_columns(
                (
                    ("period", "统计周期", 110),
                    ("store", "店铺名称", 180),
                    ("orders", "订单数", 90),
                    ("sold", "商品件数", 90),
                    ("sale", "实收款", 120),
                    ("cost", "采购成本", 120),
                    ("profit", "利润", 120),
                    ("margin", "利润率", 90),
                    ("pending", "待核算订单", 100),
                )
            )
        else:
            self._set_columns(
                (
                    ("period", "统计周期", 110),
                    ("product", "商品ID/名称", 310),
                    ("orders", "订单数", 90),
                    ("sold", "商品件数", 90),
                    ("sale", "实收款", 120),
                    ("cost", "采购成本", 120),
                    ("profit", "利润", 120),
                    ("margin", "利润率", 90),
                    ("pending", "待核算订单", 100),
                )
            )
        total_profit = 0
        for index, ((period, _label), group) in enumerate(
            sorted(groups.items(), key=lambda item: item[0])
        ):
            iid = str(index)
            self._rows[iid] = {
                "period": period,
                **group,
            }
            total_profit += int(group["profit"])
            margin = (
                float(group["profit"]) / int(group["sale"]) * 100
                if group["sale"]
                else 0.0
            )
            values = (
                period,
                group["label"],
                group["orders"],
                group["sold"],
                _money(int(group["sale"])),
                _money(int(group["cost"])),
                _money(int(group["profit"])),
                f"{margin:.2f}%",
                group["pending"],
            )
            self.table.insert("", "end", iid=iid, values=values)
        self.summary_var.set(
            f"{self.view_var.get()} · {report_view}"
            f" · 共 {len(self._filtered_rows)} 单"
            f" · 利润 {_money(total_profit)}"
        )

    def refresh(self) -> None:
        self.table.delete(*self.table.get_children())
        self._rows.clear()
        self._filtered_rows = self._build_rows()
        if self.view_var.get() == "店铺统计":
            self._render_aggregate("store")
        elif self.view_var.get() == "商品统计":
            self._render_aggregate("product")
        else:
            self._render_order_report()

    def _show_selected_detail(self, _event=None) -> None:
        selection = self.table.selection()
        if not selection:
            return
        row = self._rows.get(selection[0])
        if not row:
            return
        dialog = tk.Toplevel(self)
        dialog.title("利润统计详情")
        dialog.geometry("760x500")
        text = tk.Text(dialog, wrap="word")
        text.pack(fill="both", expand=True, padx=12, pady=12)
        text.insert(
            "1.0",
            json.dumps(row, ensure_ascii=False, indent=2, default=str),
        )
        text.configure(state="disabled")

    def _show_download_log(self) -> None:
        rows = [
            row
            for row in self.db.list_task_logs(limit=1000)
            if "利润" in str(row.get("operation") or "")
            or str(row.get("scope") or "") == "profit"
        ]
        self._show_log_dialog("利润统计下载记录", rows)

    def _show_log_dialog(
        self,
        title: str,
        rows: list[dict[str, Any]],
    ) -> None:
        dialog = tk.Toplevel(self)
        dialog.title(title)
        dialog.geometry("900x520")
        tree = ttk.Treeview(
            dialog,
            columns=("time", "operation", "status", "message"),
            show="headings",
        )
        for key, label, width in (
            ("time", "时间", 155),
            ("operation", "操作", 180),
            ("status", "状态", 90),
            ("message", "结果", 420),
        ):
            tree.heading(key, text=label)
            tree.column(key, width=width, anchor="w")
        for row in rows:
            tree.insert(
                "",
                "end",
                values=(
                    str(row.get("started_at") or "")[:19].replace("T", " "),
                    row.get("operation") or "",
                    row.get("status") or "",
                    row.get("message") or "",
                ),
            )
        tree.pack(fill="both", expand=True, padx=12, pady=12)
        ttk.Button(dialog, text="关闭", command=dialog.destroy).pack(
            pady=(0, 12)
        )

    def _export(self) -> None:
        mode = self.view_var.get()
        if mode == "店铺统计":
            headers = [
                "统计周期",
                "店铺名称",
                "订单数",
                "商品件数",
                "实收款",
                "采购成本",
                "利润",
                "利润率",
                "待核算订单",
            ]
        elif mode == "商品统计":
            headers = [
                "统计周期",
                "商品ID/名称",
                "订单数",
                "商品件数",
                "实收款",
                "采购成本",
                "利润",
                "利润率",
                "待核算订单",
            ]
        else:
            headers = [
                "店铺名称",
                "订单号",
                "成交时间",
                "商品ID",
                "商品件数",
                "交易状态",
                "买家",
                "实收款",
                "佣金",
                "平台费用",
                "采购信息",
                "采购成本",
                "利润",
                "利润率",
                "待核算",
            ]
        values: list[list[Any]] = []
        for iid in self.table.get_children():
            values.append(list(self.table.item(iid, "values")))
        self._export_rows(
            title=f"利润统计_{mode}",
            headers=headers,
            rows=values,
        )


class MessageCenterPage(WorkbenchTablePage):
    title = "消息管理"
    subtitle = "取件码、收件码通知记录；复制即记录为已发送"

    def __init__(self, parent, window) -> None:
        super().__init__(parent, window)
        self._set_columns(
            (
                ("account", "采购账号", 150),
                ("platform", "平台", 80),
                ("purchase", "采购单号", 170),
                ("pickup", "取件码", 130),
                ("store", "关联店铺", 150),
                ("order", "店铺订单", 170),
                ("sent", "发送状态", 90),
            )
        )
        controls = ttk.Frame(self)
        controls.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        ttk.Button(
            controls,
            text="复制选中取件码",
            command=self._copy_codes,
        ).pack(side="left")
        ttk.Button(
            controls,
            text="刷新",
            command=self.refresh,
        ).pack(side="left", padx=6)
        self._rows: dict[str, dict[str, Any]] = {}

    def refresh(self) -> None:
        self.table.delete(*self.table.get_children())
        self._rows.clear()
        links_by_purchase: dict[int, list[dict[str, Any]]] = {}
        for link in self.db.list_procurement_links():
            links_by_purchase.setdefault(
                int(link["procurement_order_id"]),
                [],
            ).append(link)
        workbench = self.db.get_order_workbench()
        rows = [
            row
            for row in self.db.list_procurement_orders()
            if str(row.get("pickup_code") or "").strip()
        ]
        for index, row in enumerate(rows):
            links = links_by_purchase.get(int(row["id"]), [])
            link = links[0] if links else {}
            sent = any(
                bool(
                    (workbench.get(
                        (int(item["store_id"]), str(item["order_id"]))
                    ) or {}).get("is_marked_sent")
                )
                for item in links
            )
            iid = str(index)
            self._rows[iid] = {**row, "_links": links}
            self.table.insert(
                "",
                "end",
                iid=iid,
                values=(
                    row.get("account_label", ""),
                    row.get("platform", ""),
                    row.get("external_order_id", ""),
                    row.get("pickup_code", ""),
                    link.get("store_id", ""),
                    link.get("order_id", ""),
                    "已发送" if sent else "待发送",
                ),
            )
        self.summary_var.set(f"共 {len(rows)} 条取件码通知")

    def _copy_codes(self) -> None:
        selected = [
            self._rows[iid]
            for iid in self.table.selection()
            if iid in self._rows
        ]
        codes = [
            str(row.get("pickup_code") or "").strip()
            for row in selected
            if str(row.get("pickup_code") or "").strip()
        ]
        if not codes:
            messagebox.showinfo("没有取件码", "请先选择带取件码的通知。", parent=self)
            return
        self.clipboard_clear()
        self.clipboard_append("\n".join(codes))
        links = [
            (
                int(link["store_id"]),
                str(link["order_id"]),
            )
            for row in selected
            for link in row.get("_links") or []
        ]

        def work(progress):
            for index, (store_id, order_id) in enumerate(links, 1):
                self.db.update_order_workbench(
                    store_id,
                    order_id,
                    is_marked_sent=1,
                )
                if progress:
                    progress(
                        f"正在记录第 {index}/{len(links)} 条取件码",
                        index,
                        len(links),
                    )
            return len(links)

        def done(count: int) -> None:
            self.refresh()
            self.window.set_status(
                f"已复制 {len(codes)} 条取件码，并记录 {count} 条为已发送"
            )

        def failed(error: str) -> None:
            self.window.set_status("取件码发送状态保存失败")
            messagebox.showerror(
                "取件码发送状态保存失败",
                error,
                parent=self,
            )

        self.window.set_status(
            f"已复制 {len(codes)} 条取件码，正在后台记录发送状态…",
            busy=True,
        )
        self.window.run_task(
            work,
            done,
            failed,
            log_title="记录取件码已发送",
            task_scope="workbench:mark-pickup-codes",
        )


class TransitAfterSalePage(WorkbenchTablePage):
    title = "中转售后"
    subtitle = "内部中转、外部中转、招领包裹、滞留和理赔统一管理"

    def __init__(self, parent, window) -> None:
        super().__init__(parent, window)
        self.case_type_var = tk.StringVar(value="internal")
        self.status_filter_var = tk.StringVar(value="全部")
        self.state_filter_var = tk.StringVar(value="全部")
        self.store_filter_var = tk.StringVar()
        self.order_filter_var = tk.StringVar()
        self.source_filter_var = tk.StringVar()
        self.transfer_filter_var = tk.StringVar()
        self.work_order_filter_var = tk.StringVar()
        self._rows: dict[str, dict[str, Any]] = {}
        self._filtered_rows: list[dict[str, Any]] = []
        self._status_buttons: dict[str, ttk.Radiobutton] = {}
        self._set_columns(
            (
                ("identity", "店铺订单 / 商品信息", 300),
                ("transfer", "采购 / 中转信息", 250),
                ("logistics", "物流信息", 230),
                ("status", "状态", 130),
                ("time", "更新时间", 170),
                ("operation", "操作", 110),
            )
        )
        top_tabs = ttk.Frame(self.controls)
        top_tabs.pack(fill="x")
        for key, label in (
            ("internal", "内部中转单"),
            ("external", "外部中转单"),
            ("claim", "招领包裹"),
            ("detention", "滞留管理"),
            ("compensation", "理赔管理"),
        ):
            ttk.Button(
                top_tabs,
                text=label,
                command=lambda value=key: self._change_case_type(value),
            ).pack(
                side="left",
                padx=(0, 8),
            )
        actions = ttk.Frame(self.controls)
        actions.pack(fill="x", pady=(6, 0))
        ttk.Button(
            actions,
            text="新建中转退货订单",
            command=self._create_ticket,
        ).pack(side="left")
        ttk.Button(
            actions,
            text="更新状态",
            command=self._update_ticket,
        ).pack(side="left", padx=6)
        ttk.Button(
            actions,
            text="招领登记",
            command=self._register_pickup,
        ).pack(side="left")
        ttk.Button(
            actions,
            text="理赔登记",
            command=self._register_compensation,
        ).pack(side="left", padx=6)
        ttk.Button(
            actions,
            text="导出",
            command=self._export,
        ).pack(side="left")
        filter_bar = ttk.Frame(self.controls)
        filter_bar.pack(fill="x", pady=(6, 0))
        for label, variable, width in (
            ("店铺名称", self.store_filter_var, 15),
            ("订单编号", self.order_filter_var, 16),
            ("源单号", self.source_filter_var, 16),
            ("中转单号", self.transfer_filter_var, 16),
            ("工单编号", self.work_order_filter_var, 16),
        ):
            ttk.Label(filter_bar, text=label).pack(side="left")
            ttk.Entry(
                filter_bar,
                textvariable=variable,
                width=width,
            ).pack(side="left", padx=(4, 8))
        ttk.Button(filter_bar, text="搜索", command=self.refresh).pack(side="left")
        ttk.Button(filter_bar, text="重置", command=self.refresh).pack(
            side="left",
            padx=5,
        )
        state_bar = ttk.Frame(self, style="TFrame")
        state_bar.grid(row=1, column=0, sticky="ew", pady=(0, 5))
        self.state_bar = state_bar
        self._build_state_buttons("internal")
        self.summary_label.grid_configure(row=2)
        self.table.grid_configure(row=3)
        self.rowconfigure(3, weight=1)
        self.table.bind(
            "<<TreeviewSelect>>",
            lambda _event: self._update_selection_summary(),
        )
        self.table.bind("<Double-1>", lambda _event: self._update_ticket())

    def _build_state_buttons(self, case_type: str) -> None:
        for child in self.state_bar.winfo_children():
            child.destroy()
        self._status_buttons.clear()
        values = {
            "internal": (
                "全部",
                "退货待入仓",
                "退货已入仓",
                "退货已发出",
            ),
            "external": (
                "全部",
                "待处理",
                "已创建",
                "已发出",
            ),
            "claim": (
                "全部",
                "待招领",
                "已领取",
            ),
            "detention": (
                "全部",
                "无头件",
                "待招领",
                "已领取",
            ),
            "compensation": (
                "全部",
                "待处理",
                "处理中",
                "已完成",
            ),
        }.get(case_type, ("全部",))
        self.status_filter_var.set("全部")
        for label in values:
            control = ttk.Radiobutton(
                self.state_bar,
                text=label,
                value=label,
                variable=self.status_filter_var,
                command=self.refresh,
            )
            control.pack(side="left", padx=(0, 12))
            self._status_buttons[label] = control

    def _change_case_type(self, value: str) -> None:
        self.case_type_var.set(value)
        self._build_state_buttons(value)
        self.refresh()

    def _local_case_rows(self) -> list[dict[str, Any]]:
        rows = self.db.list_transfer_cases(
            case_type=self.case_type_var.get(),
        )
        return [
            {
                **row,
                "case_id": int(row["id"]),
                "product_title": str(row.get("goods_title") or ""),
                "amount": int(row.get("pay_amount") or 0),
                "account": str(row.get("account_label") or ""),
                "derived": False,
            }
            for row in rows
        ]

    def _derived_rows(self) -> list[dict[str, Any]]:
        case_type = self.case_type_var.get()
        stores = {store.id: store for store in self.db.list_stores()}
        orders = {
            (int(row["store_id"]), str(row["order_id"])): row
            for row in self.db.list_orders(limit=100000)
        }
        procurement = self.db.list_procurement_orders()
        links = self.db.list_procurement_links()
        links_by_purchase: dict[int, list[dict[str, Any]]] = {}
        linked_purchase_ids: set[int] = set()
        for link in links:
            purchase_id = int(link["procurement_order_id"])
            links_by_purchase.setdefault(purchase_id, []).append(link)
            linked_purchase_ids.add(purchase_id)
        local_purchase_ids = {
            int(row["procurement_order_id"])
            for row in self._local_case_rows()
            if row.get("procurement_order_id")
        }
        rows: list[dict[str, Any]] = []
        now = datetime.now()
        for item in procurement:
            purchase_id = int(item["id"])
            if purchase_id in local_purchase_ids:
                continue
            text = f"{item.get('status') or ''} {item.get('status_text') or ''}"
            linked = purchase_id in linked_purchase_ids
            transfer_marker = any(
                marker in text
                for marker in ("退", "拒收", "拦截", "售后", "异常")
            )
            if case_type == "internal" and (not linked or not transfer_marker):
                continue
            if case_type == "external" and (linked or not transfer_marker):
                continue
            if case_type == "claim" and not str(item.get("pickup_code") or "").strip():
                continue
            if case_type == "detention":
                if not str(item.get("mail_no") or "").strip():
                    continue
                detention_marker = any(
                    marker in text
                    for marker in ("滞留", "无头", "待招领", "超时")
                )
                if not detention_marker:
                    continue
            if case_type == "compensation" and not any(
                marker in text
                for marker in ("退", "售后", "拒收", "拦截", "理赔", "异常")
            ):
                continue
            purchase_links = links_by_purchase.get(purchase_id, [])
            link_row = purchase_links[0] if purchase_links else {}
            store_id = int(link_row.get("store_id") or 0)
            order_id = str(link_row.get("order_id") or "")
            store = stores.get(store_id)
            order = orders.get((store_id, order_id), {})
            updated_text = str(
                item.get("updated_at")
                or item.get("fetched_at")
                or ""
            )
            days = 0
            try:
                updated_dt = datetime.fromisoformat(updated_text)
                days = max(0, (now - updated_dt.replace(tzinfo=None)).days)
            except (TypeError, ValueError):
                pass
            rows.append(
                {
                    "case_id": None,
                    "case_type": case_type,
                    "store_id": store_id,
                    "store_name": (
                        store.name
                        if store is not None
                        else "未关联店铺"
                    ),
                    "order_id": order_id,
                    "procurement_order_id": purchase_id,
                    "source_order_no": order_id,
                    "transfer_order_no": "",
                    "work_order_no": "",
                    "tracking_no": str(item.get("mail_no") or ""),
                    "pickup_code": str(item.get("pickup_code") or ""),
                    "warehouse": str(item.get("station_name") or ""),
                    "status": str(item.get("status") or ""),
                    "status_text": str(item.get("status_text") or ""),
                    "note": "",
                    "source_created_at": updated_text,
                    "updated_at": updated_text,
                    "product_title": str(
                        item.get("goods_title")
                        or order.get("item_summary")
                        or ""
                    ),
                    "amount": int(
                        item.get("pay_amount")
                        or order.get("total_amount")
                        or 0
                    ),
                    "account": str(item.get("account_label") or ""),
                    "platform": str(item.get("platform") or ""),
                    "external_order_id": str(
                        item.get("external_order_id") or ""
                    ),
                    "detention_days": days,
                    "derived": True,
                }
            )
        return rows

    def _matches_status(self, row: dict[str, Any], selected: str) -> bool:
        if selected == "全部":
            return True
        text = f"{row.get('status') or ''} {row.get('status_text') or ''}"
        if selected in text:
            return True
        if selected == "待处理":
            return any(
                marker in text
                for marker in ("待", "异常", "退货", "退款", "拦截")
            )
        if selected == "已创建":
            return bool(row.get("case_id")) and not any(
                marker in text for marker in ("入仓", "发出", "领取", "完成")
            )
        if selected == "已发出":
            return "发出" in text or "寄出" in text
        if selected == "待招领":
            return bool(row.get("pickup_code")) and "领取" not in text
        if selected == "已领取":
            return "领取" in text
        if selected == "无头件":
            return "无头" in text or not row.get("order_id")
        if selected == "处理中":
            return any(marker in text for marker in ("处理", "待"))
        if selected == "已完成":
            return "完成" in text or "成功" in text
        return False

    def _matches_filters(self, row: dict[str, Any]) -> bool:
        checks = (
            (self.store_filter_var.get(), row.get("store_name")),
            (self.order_filter_var.get(), row.get("order_id")),
            (self.source_filter_var.get(), row.get("source_order_no")),
            (self.transfer_filter_var.get(), row.get("transfer_order_no")),
            (self.work_order_filter_var.get(), row.get("work_order_no")),
        )
        for term, value in checks:
            normalized = str(term or "").strip().casefold()
            if normalized and normalized not in str(value or "").casefold():
                return False
        return self._matches_status(row, self.status_filter_var.get())

    def refresh(self) -> None:
        rows = self._local_case_rows() + self._derived_rows()
        rows = [row for row in rows if self._matches_filters(row)]
        rows.sort(
            key=lambda row: str(
                row.get("updated_at")
                or row.get("source_created_at")
                or ""
            ),
            reverse=True,
        )
        self._filtered_rows = rows
        self._rows.clear()
        self.table.delete(*self.table.get_children())
        for index, row in enumerate(rows):
            iid = str(index)
            self._rows[iid] = row
            identity = (
                f"{row.get('store_name') or '未关联店铺'}"
                f"\n订单：{row.get('order_id') or '—'}"
                f"\n商品：{str(row.get('product_title') or '—')[:72]}"
            )
            transfer = (
                f"采购：{row.get('external_order_id') or row.get('account') or '—'}"
                f"\n中转单：{row.get('transfer_order_no') or '—'}"
                f"\n工单：{row.get('work_order_no') or '—'}"
            )
            logistics = (
                f"物流：{row.get('tracking_no') or '—'}"
                f"\n取件码：{row.get('pickup_code') or '—'}"
                f"\n仓库：{row.get('warehouse') or '—'}"
            )
            if int(row.get("detention_days") or 0):
                logistics += f"\n滞留：{int(row['detention_days'])} 天"
            self.table.insert(
                "",
                "end",
                iid=iid,
                values=(
                    identity,
                    transfer,
                    logistics,
                    row.get("status_text")
                    or row.get("status")
                    or "待处理",
                    str(row.get("updated_at") or "")[:19].replace("T", " "),
                    "编辑工单" if row.get("case_id") else "创建工单",
                ),
            )
        self.summary_var.set(
            f"{self._case_label()} · 共 {len(rows)} 条"
        )
        self._update_selection_summary()

    def _case_label(self) -> str:
        return {
            "internal": "内部中转单",
            "external": "外部中转单",
            "claim": "招领包裹",
            "detention": "滞留管理",
            "compensation": "理赔管理",
        }.get(self.case_type_var.get(), "中转售后")

    def _update_selection_summary(self) -> None:
        selected = len(self.table.selection())
        if selected:
            self.summary_var.set(
                f"{self._case_label()} · 共 {len(self._filtered_rows)} 条"
                f" · 已选 {selected} 条"
            )

    def _selected_ticket(self) -> dict[str, Any] | None:
        rows = self._selected_rows(self._rows)
        if not rows:
            messagebox.showinfo(
                "请选择记录",
                "请先选择一条中转或售后记录。",
                parent=self,
            )
            return None
        return rows[0]

    def _create_ticket(self) -> None:
        stores = self.db.list_stores()
        if not stores:
            messagebox.showinfo("没有店铺", "请先添加店铺。", parent=self)
            return
        default_store = f"{stores[0].name} · #{stores[0].id}"

        def save(values: dict[str, str]) -> None:
            store_text = values.get("store", default_store)
            try:
                store_id = int(store_text.rsplit("#", 1)[-1])
            except ValueError:
                messagebox.showwarning(
                    "店铺",
                    "请填写“店铺名称 · #店铺ID”。",
                    parent=self,
                )
                return
            transfer_no = values.get("transfer_order_no", "").strip()
            if not transfer_no:
                messagebox.showwarning(
                    "中转单号",
                    "请填写中转单号。",
                    parent=self,
                )
                return
            try:
                self.db.create_transfer_case(
                    {
                        "case_type": self.case_type_var.get()
                        if self.case_type_var.get()
                        in {"internal", "external"}
                        else "internal",
                        "store_id": store_id,
                        "order_id": values.get("order_id", "").strip(),
                        "source_order_no": values.get(
                            "source_order_no",
                            "",
                        ).strip(),
                        "transfer_order_no": transfer_no,
                        "work_order_no": values.get(
                            "work_order_no",
                            "",
                        ).strip(),
                        "tracking_no": values.get(
                            "tracking_no",
                            "",
                        ).strip(),
                        "pickup_code": values.get(
                            "pickup_code",
                            "",
                        ).strip(),
                        "warehouse": values.get(
                            "warehouse",
                            "",
                        ).strip(),
                        "status": values.get("status", "退货待入仓").strip(),
                        "status_text": values.get(
                            "status",
                            "退货待入仓",
                        ).strip(),
                        "note": values.get("note", "").strip(),
                    }
                )
            except Exception as exc:
                messagebox.showerror("创建中转单失败", str(exc), parent=self)
                return
            self.refresh()
            self.window.set_status(f"已创建中转单：{transfer_no}")

        SimpleFieldsDialog(
            self,
            "新建中转退货订单",
            (
                ("store", "店铺名称 · #ID"),
                ("order_id", "店铺订单号"),
                ("source_order_no", "源单号"),
                ("transfer_order_no", "中转单号"),
                ("work_order_no", "工单编号"),
                ("tracking_no", "物流单号"),
                ("pickup_code", "取件码"),
                ("warehouse", "中转仓库"),
                ("status", "状态"),
                ("note", "备注"),
            ),
            save,
            {
                "store": default_store,
                "status": (
                    "退货待入仓"
                    if self.case_type_var.get() == "internal"
                    else "待处理"
                ),
            },
        )

    def _register_pickup(self) -> None:
        row = self._selected_ticket()
        if row is None:
            return
        self._apply_case_update(
            row,
            {
                "case_type": "claim",
                "status": "待招领",
                "status_text": "待招领",
            },
            "招领登记",
        )

    def _register_compensation(self) -> None:
        row = self._selected_ticket()
        if row is None:
            return
        self._apply_case_update(
            row,
            {
                "case_type": "compensation",
                "status": "待处理",
                "status_text": "待处理",
            },
            "理赔登记",
        )

    def _update_ticket(self) -> None:
        row = self._selected_ticket()
        if row is None:
            return

        def save(values: dict[str, str]) -> None:
            self._apply_case_update(
                row,
                {
                    "status": values.get("status", "").strip() or "待处理",
                    "status_text": values.get("status", "").strip() or "待处理",
                    "warehouse": values.get("warehouse", "").strip(),
                    "tracking_no": values.get("tracking_no", "").strip(),
                    "pickup_code": values.get("pickup_code", "").strip(),
                    "note": values.get("note", "").strip(),
                },
                "更新中转工单",
            )

        SimpleFieldsDialog(
            self,
            "更新中转工单",
            (
                ("status", "状态"),
                ("warehouse", "中转仓库"),
                ("tracking_no", "物流单号"),
                ("pickup_code", "取件码"),
                ("note", "备注"),
            ),
            save,
            {
                "status": str(
                    row.get("status_text")
                    or row.get("status")
                    or "待处理"
                ),
                "warehouse": str(row.get("warehouse") or ""),
                "tracking_no": str(row.get("tracking_no") or ""),
                "pickup_code": str(row.get("pickup_code") or ""),
                "note": str(row.get("note") or ""),
            },
        )

    def _apply_case_update(
        self,
        row: dict[str, Any],
        changes: dict[str, Any],
        label: str,
    ) -> None:
        case_id = row.get("case_id")
        try:
            if case_id:
                self.db.update_transfer_case(int(case_id), **changes)
            else:
                transfer_no = (
                    f"LL-{datetime.now():%Y%m%d%H%M%S}-"
                    f"{int(row.get('procurement_order_id') or 0)}"
                )
                payload = {
                    "case_type": changes.get(
                        "case_type",
                        self.case_type_var.get(),
                    ),
                    "store_id": int(row.get("store_id") or 0) or None,
                    "order_id": str(row.get("order_id") or ""),
                    "procurement_order_id": row.get(
                        "procurement_order_id"
                    ),
                    "source_order_no": str(
                        row.get("source_order_no") or ""
                    ),
                    "transfer_order_no": transfer_no,
                    "work_order_no": str(row.get("work_order_no") or ""),
                    "tracking_no": str(row.get("tracking_no") or ""),
                    "pickup_code": str(row.get("pickup_code") or ""),
                    "warehouse": str(row.get("warehouse") or ""),
                    "status": str(row.get("status") or "待处理"),
                    "status_text": str(row.get("status_text") or ""),
                    "note": str(row.get("note") or ""),
                    "source_created_at": str(
                        row.get("source_created_at") or ""
                    ),
                    **changes,
                }
                self.db.create_transfer_case(payload)
        except Exception as exc:
            messagebox.showerror(f"{label}失败", str(exc), parent=self)
            return
        self.refresh()
        self.window.set_status(f"{label}已保存")

    def _export(self) -> None:
        headers = [
            "类型",
            "店铺",
            "订单号",
            "源单号",
            "中转单号",
            "工单编号",
            "采购单号",
            "物流单号",
            "取件码",
            "仓库",
            "状态",
            "商品",
            "金额",
            "更新时间",
            "备注",
        ]
        rows = [
            [
                self._case_label(),
                item.get("store_name") or "",
                item.get("order_id") or "",
                item.get("source_order_no") or "",
                item.get("transfer_order_no") or "",
                item.get("work_order_no") or "",
                item.get("external_order_id") or "",
                item.get("tracking_no") or "",
                item.get("pickup_code") or "",
                item.get("warehouse") or "",
                item.get("status_text") or item.get("status") or "",
                item.get("product_title") or "",
                int(item.get("amount") or 0) / 100,
                str(item.get("updated_at") or ""),
                item.get("note") or "",
            ]
            for item in self._filtered_rows
        ]
        self._export_rows(
            title=f"中转售后_{self._case_label()}",
            headers=headers,
            rows=rows,
        )

class SoftwareLogsPage(WorkbenchTablePage):
    title = "软件日志"
    subtitle = "任务、同步、异常和操作结果"

    def __init__(self, parent, window) -> None:
        super().__init__(parent, window)
        self._set_columns(
            (
                ("time", "开始时间", 145),
                ("scope", "范围", 120),
                ("operation", "操作", 180),
                ("status", "状态", 80),
                ("message", "结果", 360),
            )
        )

    def refresh(self) -> None:
        self.table.delete(*self.table.get_children())
        rows = self.db.list_task_logs(limit=1000)
        for index, row in enumerate(rows):
            self.table.insert(
                "",
                "end",
                iid=str(index),
                values=(
                    str(row.get("started_at") or "")[:19],
                    row.get("store_name") or row.get("scope") or "",
                    row.get("operation") or "",
                    row.get("status") or "",
                    row.get("message") or "",
                ),
            )
        self.summary_var.set(f"最近 {len(rows)} 条任务与诊断日志")


class OrderWorkbenchWindow(tk.Toplevel):
    def __init__(self, parent: tk.Misc, window: "MainWindow") -> None:
        super().__init__(parent)
        self.window = window
        self.title("澜琅 · 订单工作台")
        self.geometry("1420x860")
        self.minsize(1180, 720)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.configure(bg="#f4f7fa")
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)
        side = tk.Frame(self, bg="#102b50", width=190)
        side.grid(row=0, column=0, sticky="ns")
        side.grid_propagate(False)
        tk.Label(
            side,
            text="订单工作台",
            bg="#102b50",
            fg="white",
            font=("Microsoft YaHei UI", 16, "bold"),
            anchor="w",
            padx=18,
            pady=22,
        ).pack(fill="x")
        self.nav_buttons: dict[str, tk.Button] = {}
        for key, label in ROUTES:
            button = tk.Button(
                side,
                text=label,
                command=lambda route=key: self.show(route),
                bg="#102b50",
                fg="#b9c9db",
                activebackground="#1b4775",
                activeforeground="white",
                relief="flat",
                bd=0,
                anchor="w",
                padx=20,
                pady=10,
                cursor="hand2",
                font=("Microsoft YaHei UI", 10),
            )
            button.pack(fill="x", padx=8, pady=1)
            self.nav_buttons[key] = button
        self.host = ttk.Frame(self, padding=14)
        self.host.grid(row=0, column=1, sticky="nsew")
        self.host.columnconfigure(0, weight=1)
        self.host.rowconfigure(1, weight=1)
        self.heading_var = tk.StringVar()
        self.subtitle_var = tk.StringVar()
        ttk.Label(
            self.host,
            textvariable=self.heading_var,
            style="Title.TLabel",
        ).grid(row=0, column=0, sticky="w", pady=(0, 8))
        self.page_host = ttk.Frame(self.host)
        self.page_host.grid(row=1, column=0, sticky="nsew")
        self.page_host.columnconfigure(0, weight=1)
        self.page_host.rowconfigure(0, weight=1)
        self.pages: dict[str, BasePage] = {}
        self.show("orders")

    def _create_page(self, route: str) -> BasePage:
        if route == "orders":
            return OrdersPage(self.page_host, self.window)
        if route == "procurement":
            return ProcurementPage(self.page_host, self.window)
        if route == "store_after_sale":
            return StoreAfterSalePage(self.page_host, self.window)
        if route == "transit_after_sale":
            return TransitAfterSalePage(self.page_host, self.window)
        if route == "profit":
            return ProfitStatsPage(self.page_host, self.window)
        if route == "messages":
            return MessageCenterPage(self.page_host, self.window)
        if route == "logs":
            return SoftwareLogsPage(self.page_host, self.window)
        return SettingsPage(self.page_host, self.window)

    def show(self, route: str) -> None:
        page = self.pages.get(route)
        if page is None:
            page = self._create_page(route)
            self.pages[route] = page
            page.grid(row=0, column=0, sticky="nsew")
        for item in self.pages.values():
            item.grid_remove()
        page.grid()
        self.heading_var.set(f"{page.title} · {page.subtitle}")
        for key, button in self.nav_buttons.items():
            button.configure(
                bg="#2567d8" if key == route else "#102b50",
                fg="white" if key == route else "#b9c9db",
            )
        page.refresh()

    def close(self) -> None:
        self.destroy()
        if getattr(self.window, "_order_workbench_window", None) is self:
            self.window._order_workbench_window = None


def open_order_workbench(parent: tk.Misc, window: "MainWindow") -> OrderWorkbenchWindow:
    current = getattr(window, "_order_workbench_window", None)
    if current is not None and current.winfo_exists():
        current.deiconify()
        current.lift()
        current.focus_force()
        return current
    current = OrderWorkbenchWindow(parent, window)
    window._order_workbench_window = current
    return current
