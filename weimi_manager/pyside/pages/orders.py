from __future__ import annotations

import json
from datetime import datetime, time
from pathlib import Path
from typing import Any

from PySide6.QtCore import QDate
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QDateEdit,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
)

from ...exporter import export_xlsx
from ...models import is_pending_purchase_order
from ..widgets import DataTable, PrimaryButton
from .common import TaskPage

TABS = (
    ("all", "全部"),
    ("unbound", "未绑定"),
    ("pending_payment", "待付款"),
    ("gift", "礼物待收下"),
    ("purchase", "待采购"),
    ("pending_delivery", "待发货"),
    ("shipped", "已发货"),
    ("completed", "已完成"),
    ("after_sale", "售后订单"),
    ("marked", "标记"),
)


class PayloadDialog(QDialog):
    def __init__(self, title: str, payload: dict[str, Any], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(760, 560)
        from PySide6.QtWidgets import QVBoxLayout

        layout = QVBoxLayout(self)
        hint = QLabel("请核对请求内容。提交后会调用微信官方接口。")
        hint.setObjectName("页面说明")
        layout.addWidget(hint)
        self.editor = QPlainTextEdit(json.dumps(payload, ensure_ascii=False, indent=2))
        layout.addWidget(self.editor, 1)
        actions = QHBoxLayout()
        actions.addStretch(1)
        cancel = QPushButton("取消")
        confirm = PrimaryButton("提交")
        cancel.clicked.connect(self.reject)
        confirm.clicked.connect(self._accept)
        actions.addWidget(cancel)
        actions.addWidget(confirm)
        layout.addLayout(actions)

    def _accept(self) -> None:
        try:
            payload = json.loads(self.editor.toPlainText())
        except json.JSONDecodeError as exc:
            QMessageBox.warning(self, "JSON 格式错误", exc.msg)
            return
        if not isinstance(payload, dict):
            QMessageBox.warning(self, "JSON 格式错误", "请求顶层必须是对象。")
            return
        self.payload = payload
        self.accept()


class OrdersPage(TaskPage):
    def __init__(self, workspace) -> None:
        super().__init__(
            workspace,
            "订单管理",
            "微信订单、采购单号、物流、取件码和本地标记",
            stale_seconds=15,
        )
        self.active_tab_key = "all"
        self.tab_counts: dict[str, int] = {}
        self.visible_rows: list[dict[str, Any]] = []
        self._stores = list(workspace.db.list_stores())
        self._workbench: dict[tuple[int, str], dict[str, Any]] = {}
        self._after_sale_ids: set[tuple[int, str]] = set()
        self._procurement: dict[tuple[int, str], dict[str, Any]] = {}

        tabs = QHBoxLayout()
        self.tab_group = QButtonGroup(self)
        self.tab_group.setExclusive(True)
        self.tab_buttons: dict[str, QPushButton] = {}
        for key, label in TABS:
            button = QPushButton(label)
            button.setCheckable(True)
            button.clicked.connect(lambda _checked=False, tab=key: self._set_tab(tab))
            self.tab_group.addButton(button)
            self.tab_buttons[key] = button
            tabs.addWidget(button)
        self.root_layout.addLayout(tabs)

        filters = QHBoxLayout()
        self.store_combo = QComboBox()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("订单号、收件人、手机号、运单号或采购号")
        self.detail_combo = QComboBox()
        self.detail_combo.addItems(("全部细分", "待出码", "已有取件码", "已标记已发"))
        self.marker_combo = QComboBox()
        self.marker_combo.addItems(("全部标识", "已标记", "未标记"))
        self.start_date = QDateEdit(QDate.currentDate().addDays(-7))
        self.end_date = QDateEdit(QDate.currentDate())
        for widget in (self.start_date, self.end_date):
            widget.setCalendarPopup(True)
            widget.setDisplayFormat("yyyy-MM-dd")
        self.query_button = PrimaryButton("查询")
        self.sync_button = QPushButton("拉取订单")
        filters.addWidget(self.store_combo)
        filters.addWidget(self.search_input, 1)
        filters.addWidget(self.detail_combo)
        filters.addWidget(self.marker_combo)
        filters.addWidget(self.start_date)
        filters.addWidget(self.end_date)
        filters.addWidget(self.query_button)
        filters.addWidget(self.sync_button)
        self.root_layout.addLayout(filters)
        self.query_button.clicked.connect(self.refresh)
        self.sync_button.clicked.connect(self._sync_orders)
        self.store_combo.currentIndexChanged.connect(lambda _index: self.refresh())
        self.detail_combo.currentIndexChanged.connect(lambda _index: self._render())
        self.marker_combo.currentIndexChanged.connect(lambda _index: self._render())
        self.search_input.returnPressed.connect(self.refresh)

        actions = QHBoxLayout()
        self.purchase_button = QPushButton("填写采购号")
        self.pickup_button = QPushButton("复制取件码")
        self.mark_button = QPushButton("标记已发")
        self.unmark_button = QPushButton("取消已发")
        self.sync_pickup_button = QPushButton("同步取件码")
        self.detail_button = QPushButton("本地详情")
        self.decode_button = QPushButton("解密收货信息")
        self.note_button = QPushButton("修改备注")
        self.price_button = QPushButton("修改价格")
        self.address_button = QPushButton("申请改址")
        self.delivery_button = QPushButton("修改物流")
        self.official_button = QPushButton("打开官方订单")
        self.export_button = QPushButton("导出 XLSX")
        for widget in (
            self.purchase_button,
            self.pickup_button,
            self.mark_button,
            self.unmark_button,
            self.sync_pickup_button,
            self.detail_button,
            self.decode_button,
            self.note_button,
            self.price_button,
            self.address_button,
            self.delivery_button,
            self.official_button,
            self.export_button,
        ):
            actions.addWidget(widget)
        actions.addStretch(1)
        self.root_layout.addLayout(actions)
        self.purchase_button.clicked.connect(self._fill_purchase_number)
        self.pickup_button.clicked.connect(self._copy_pickup_codes)
        self.mark_button.clicked.connect(lambda: self._set_marked_sent(True))
        self.unmark_button.clicked.connect(lambda: self._set_marked_sent(False))
        self.sync_pickup_button.clicked.connect(self._sync_pickup_codes)
        self.detail_button.clicked.connect(self._show_detail)
        self.decode_button.clicked.connect(self._decode_sensitive)
        self.note_button.clicked.connect(self._edit_note)
        self.price_button.clicked.connect(self._edit_price)
        self.address_button.clicked.connect(self._request_address)
        self.delivery_button.clicked.connect(self._edit_delivery)
        self.official_button.clicked.connect(self._open_official)
        self.export_button.clicked.connect(self._export)

        self.table = DataTable(
            [
                ("sequence", "序号"),
                ("order_id", "订单号"),
                ("store", "店铺"),
                ("status", "状态"),
                ("created", "下单时间"),
                ("buyer", "买家"),
                ("amount", "实收"),
                ("product", "商品"),
                ("purchase", "采购号"),
                ("logistics", "物流"),
                ("pickup", "取件码"),
                ("note", "备注"),
                ("mark", "标记"),
            ]
        )
        self.table.setSelectionMode(DataTable.SelectionMode.ExtendedSelection)
        self.table.row_activated.connect(lambda _row: self._show_detail())
        self.root_layout.addWidget(self.table, 1)
        self.summary_label = QLabel()
        self.summary_label.setObjectName("页面说明")
        self.root_layout.addWidget(self.summary_label)
        self._reload_stores()
        self._set_tab("all")
        self.refresh()

    def _reload_stores(self) -> None:
        current = self.store_combo.currentData()
        self._stores = list(self.workspace.db.list_stores())
        self.store_combo.blockSignals(True)
        self.store_combo.clear()
        self.store_combo.addItem("全部店铺", None)
        for store in self._stores:
            self.store_combo.addItem(f"{store.name} · #{store.id}", store.id)
        self.store_combo.setCurrentIndex(max(0, self.store_combo.findData(current)))
        self.store_combo.blockSignals(False)

    def refresh(self) -> None:
        self._reload_stores()
        store_id = self.store_combo.currentData()
        start = datetime.combine(
            self.start_date.date().toPython(),
            time.min,
        ).timestamp()
        end = datetime.combine(
            self.end_date.date().toPython(),
            time.max,
        ).timestamp()
        try:
            self._all_rows = self.workspace.db.list_orders(
                store_id=store_id,
                start_time=int(start),
                end_time=int(end),
                search="",
            )
            self._workbench = self.workspace.db.get_order_workbench(store_id)
            after_sales = self.workspace.db.list_after_sales(store_id)
            self._after_sale_ids = {
                (int(row["store_id"]), str(row["order_id"]))
                for row in after_sales
                if row.get("order_id")
            }
            self._procurement = {
                (int(row["store_id"]), str(row["order_id"])): row
                for row in self.workspace.db.list_procurement_links(store_id)
            }
        except Exception as exc:
            self.notify.emit("订单加载失败", str(exc))
            self._all_rows = []
        self.tab_counts = {
            key: sum(1 for row in self._all_rows if self._matches_order_tab(row, key))
            for key, _label in TABS
        }
        for key, label in TABS:
            self.tab_buttons[key].setText(f"{label}  {self.tab_counts.get(key, 0)}")
        self._render()
        super().refresh()

    def _workbench_for(self, row: dict[str, Any]) -> dict[str, Any]:
        return self._workbench.get(
            (int(row["store_id"]), str(row["order_id"])),
            {},
        )

    def _is_marked(self, row: dict[str, Any]) -> bool:
        workbench = self._workbench_for(row)
        return bool(
            int(workbench.get("is_marked") or 0)
            or int(workbench.get("is_marked_sent") or 0)
            or str(workbench.get("note") or "").strip()
            or str(row.get("merchant_notes") or "").strip()
        )

    def _matches_order_tab(self, row: dict[str, Any], key: str) -> bool:
        workbench = self._workbench_for(row)
        status = int(row.get("status") or 0)
        pair = (int(row["store_id"]), str(row["order_id"]))
        if key == "all":
            return True
        if key == "unbound":
            return not bool(workbench.get("purchase_order_no"))
        if key == "pending_payment":
            return status == 10
        if key == "gift":
            return status == 12
        if key == "purchase":
            return is_pending_purchase_order(row, workbench, pair in self._after_sale_ids)
        if key == "pending_delivery":
            return status == 20
        if key == "shipped":
            return status in {21, 30}
        if key == "completed":
            return status == 100
        if key == "after_sale":
            return pair in self._after_sale_ids
        if key == "marked":
            return self._is_marked(row)
        return True

    def _matches_filters(self, row: dict[str, Any]) -> bool:
        workbench = self._workbench_for(row)
        term = self.search_input.text().strip().casefold()
        if term:
            searchable = " ".join(
                str(value or "")
                for value in (
                    row.get("order_id"),
                    row.get("store_name"),
                    row.get("buyer_name"),
                    row.get("phone"),
                    row.get("address"),
                    row.get("waybill_id"),
                    row.get("merchant_notes"),
                    workbench.get("purchase_order_no"),
                    workbench.get("pickup_code"),
                    workbench.get("note"),
                )
            ).casefold()
            if term not in searchable:
                return False
        marker = self.marker_combo.currentText()
        if marker == "已标记" and not self._is_marked(row):
            return False
        if marker == "未标记" and self._is_marked(row):
            return False
        detail = self.detail_combo.currentText()
        if detail == "待出码" and workbench.get("pickup_code"):
            return False
        if detail == "已有取件码" and not workbench.get("pickup_code"):
            return False
        if detail == "已标记已发" and not workbench.get("is_marked_sent"):
            return False
        return True

    def _render(self) -> None:
        rows = [
            row
            for row in getattr(self, "_all_rows", [])
            if self._matches_order_tab(row, self.active_tab_key) and self._matches_filters(row)
        ]
        self.visible_rows = rows
        display = []
        for index, row in enumerate(rows, 1):
            workbench = self._workbench_for(row)
            logistics = " ".join(
                item for item in (row.get("carrier"), row.get("waybill_id")) if item
            )
            display.append(
                {
                    **row,
                    "sequence": index,
                    "store": row.get("store_name", ""),
                    "status": row.get("status_text", ""),
                    "created": datetime.fromtimestamp(int(row.get("create_time") or 0)).strftime(
                        "%Y-%m-%d %H:%M"
                    ),
                    "buyer": row.get("buyer_name", ""),
                    "amount": f"¥{int(row.get('total_amount') or 0) / 100:.2f}",
                    "product": row.get("item_summary", ""),
                    "purchase": workbench.get("purchase_order_no", ""),
                    "logistics": logistics,
                    "pickup": workbench.get("pickup_code", ""),
                    "note": workbench.get("note") or row.get("merchant_notes", ""),
                    "mark": "已发"
                    if workbench.get("is_marked_sent")
                    else ("是" if self._is_marked(row) else ""),
                }
            )
        self.table.set_rows(display)
        selected_label = self.tab_buttons[self.active_tab_key].text()
        self.summary_label.setText(
            f"{selected_label} · 显示 {len(rows)} 条 · 订单金额按分保存，界面按人民币元展示"
        )

    def _set_tab(self, key: str) -> None:
        self.active_tab_key = key
        self.tab_buttons[key].setChecked(True)
        if hasattr(self, "_all_rows"):
            self._render()

    def _selected(self) -> dict[str, Any] | None:
        return self.table.selected_row()

    def _selected_rows(self) -> list[dict[str, Any]]:
        result = []
        for index in self.table.selectionModel().selectedRows():
            row = self.table.model_data.row_at(index.row())
            if row is not None:
                result.append(row)
        return result

    def _store_for_row(self, row: dict[str, Any]):
        return next(
            (store for store in self._stores if store.id == int(row["store_id"])),
            None,
        )

    def _sync_orders(self) -> None:
        store_id = self.store_combo.currentData()
        if store_id is None:
            self.notify.emit("选择店铺", "拉取订单前请选择具体店铺")
            return
        store = next(store for store in self._stores if store.id == store_id)
        start = int(datetime.combine(self.start_date.date().toPython(), time.min).timestamp())
        end = int(datetime.combine(self.end_date.date().toPython(), time.max).timestamp())
        if end - start > 7 * 86400:
            self.notify.emit("查询范围", "单次拉取不能超过 7 天")
            return
        self.submit(
            f"orders:sync:{store_id}:{start}:{end}",
            lambda: self.workspace.service.sync_orders(store, start, end),
            lambda result: self._after_sync(result),
            self.sync_button,
            cancelable=True,
        )

    def _after_sync(self, result) -> None:
        self.refresh()
        self.notify.emit("订单同步完成", getattr(result, "message", str(result)))

    def _fill_purchase_number(self) -> None:
        rows = self._selected_rows()
        if not rows:
            return
        value, accepted = QInputDialog.getText(self, "填写采购号", "采购订单号：")
        if not accepted:
            return

        def work() -> int:
            for row in rows:
                self.workspace.db.update_order_workbench(
                    int(row["store_id"]),
                    str(row["order_id"]),
                    purchase_order_no=value.strip(),
                )
            return len(rows)

        self.submit(
            "orders:batch-purchase-number",
            work,
            lambda _count: self.refresh(),
        )

    def _copy_pickup_codes(self) -> None:
        rows = self._selected_rows()
        values = [str(self._workbench_for(row).get("pickup_code") or "") for row in rows]
        values = [value for value in values if value]
        if not values:
            self.notify.emit("提示", "所选订单没有取件码")
            return
        from PySide6.QtWidgets import QApplication

        QApplication.clipboard().setText("\n".join(values))
        self.notify.emit("已复制", f"{len(values)} 个取件码")

    def _set_marked_sent(self, value: bool) -> None:
        rows = self._selected_rows()

        def work() -> int:
            for row in rows:
                self.workspace.db.update_order_workbench(
                    int(row["store_id"]),
                    str(row["order_id"]),
                    is_marked_sent=1 if value else 0,
                )
            return len(rows)

        self.submit(
            "orders:batch-mark-sent",
            work,
            lambda _count: self.refresh(),
        )

    def _sync_pickup_codes(self) -> None:
        store_id = self.store_combo.currentData()
        if store_id is None:
            self.notify.emit("选择店铺", "同步取件码前请选择具体店铺")
            return
        store = next(store for store in self._stores if store.id == store_id)
        self.submit(
            f"orders:pickup:{store_id}",
            lambda: self.workspace.service.match_procurement_orders(store),
            lambda _result: self._after_refresh("取件码与采购关联已刷新"),
            self.sync_pickup_button,
        )

    def _show_detail(self) -> None:
        row = self._selected()
        if not row:
            return
        order = self.workspace.db.get_order(
            int(row["store_id"]),
            str(row["order_id"]),
        )
        if order is None:
            return
        payload = {
            "order_id": order.order_id,
            "store_name": order.store_name,
            "status": order.status_text,
            "buyer": order.buyer_name,
            "phone": order.phone,
            "address": order.address,
            "amount": order.total_amount,
            "waybill_id": order.waybill_id,
            "items": [
                {
                    "title": item.title,
                    "sku": item.sku_attributes,
                    "count": item.sku_count,
                    "sale_price": item.sale_price,
                }
                for item in order.items
            ],
            "raw": order.raw,
        }
        PayloadDialog("本地订单详情", payload, self).exec()

    def _decode_sensitive(self) -> None:
        row = self._selected()
        if not row:
            return
        store = self._store_for_row(row)
        if store is None:
            return
        self.submit(
            f"orders:decode:{store.id}:{row['order_id']}",
            lambda: self.workspace.service.decode_order_sensitive(store, str(row["order_id"])),
            lambda result: PayloadDialog(
                "解密收货信息",
                result if isinstance(result, dict) else {"result": result},
                self,
            ).exec(),
            self.decode_button,
        )

    def _edit_note(self) -> None:
        row = self._selected()
        if not row:
            return
        store = self._store_for_row(row)
        if store is None:
            return
        value, accepted = QInputDialog.getText(
            self,
            "商家备注",
            "备注内容：",
            text=str(row.get("merchant_notes") or ""),
        )
        if not accepted:
            return
        self.submit(
            f"orders:note:{store.id}:{row['order_id']}",
            lambda: self.workspace.service.update_order_notes(
                store,
                str(row["order_id"]),
                value,
            ),
            lambda _result: self._after_refresh("商家备注已同步"),
            self.note_button,
        )

    def _edit_price(self) -> None:
        row = self._selected()
        store = self._store_for_row(row) if row else None
        if not row or store is None:
            return
        dialog = PayloadDialog(
            "修改订单价格",
            {"change_order_infos": [], "change_express": False, "express_fee": 0},
            self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        payload = dialog.payload
        self.submit(
            f"orders:price:{store.id}:{row['order_id']}",
            lambda: self.workspace.service.update_order_price(
                store,
                str(row["order_id"]),
                list(payload.get("change_order_infos") or []),
                bool(payload.get("change_express")),
                int(payload.get("express_fee") or 0),
            ),
            lambda _result: self._after_refresh("订单价格请求已提交"),
            self.price_button,
        )

    def _request_address(self) -> None:
        row = self._selected()
        store = self._store_for_row(row) if row else None
        if not row or store is None:
            return
        dialog = PayloadDialog(
            "申请修改收货地址",
            {
                "user_name": "",
                "tel_number": "",
                "province_name": "",
                "city_name": "",
                "county_name": "",
                "detail_info": "",
            },
            self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.submit(
            f"orders:address:{store.id}:{row['order_id']}",
            lambda: self.workspace.service.request_order_address_change(
                store,
                str(row["order_id"]),
                dialog.payload,
            ),
            lambda _result: self._after_refresh("改址申请已提交"),
            self.address_button,
        )

    def _edit_delivery(self) -> None:
        row = self._selected()
        store = self._store_for_row(row) if row else None
        if not row or store is None:
            return
        dialog = PayloadDialog("修改物流信息", {}, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.submit(
            f"orders:delivery:{store.id}:{row['order_id']}",
            lambda: self.workspace.service.update_order_delivery_info(
                store,
                str(row["order_id"]),
                dialog.payload,
            ),
            lambda _result: self._after_refresh("物流信息已提交"),
            self.delivery_button,
        )

    def _open_official(self) -> None:
        row = self._selected()
        store = self._store_for_row(row) if row else None
        if not row or store is None:
            return
        self.submit(
            f"orders:official:{store.id}:{row['order_id']}",
            lambda: self.workspace.web_session.open_official_order(store, str(row["order_id"])),
            lambda _result: self._after_refresh("官方订单页面已关闭"),
            self.official_button,
        )

    def _after_refresh(self, message: str) -> None:
        self.refresh()
        self.notify.emit("操作完成", message)

    def _export(self) -> None:
        if not self.visible_rows:
            self.notify.emit("提示", "当前没有可导出的订单")
            return
        path, _filter = QFileDialog.getSaveFileName(
            self,
            "导出订单",
            f"订单_{datetime.now():%Y%m%d}.xlsx",
            "Excel 工作簿 (*.xlsx)",
        )
        if not path:
            return
        headers = [
            "店铺",
            "订单号",
            "状态",
            "买家",
            "电话",
            "地址",
            "商品",
            "金额",
            "运单号",
            "采购号",
            "取件码",
            "备注",
        ]
        rows = [
            [
                row.get("store_name", ""),
                row.get("order_id", ""),
                row.get("status_text", ""),
                row.get("buyer_name", ""),
                row.get("phone", ""),
                row.get("address", ""),
                row.get("item_summary", ""),
                row.get("total_amount", 0),
                row.get("waybill_id", ""),
                self._workbench_for(row).get("purchase_order_no", ""),
                self._workbench_for(row).get("pickup_code", ""),
                row.get("merchant_notes", ""),
            ]
            for row in self.visible_rows
        ]
        export_xlsx(path, "订单", headers, rows)
        self.notify.emit("导出完成", str(Path(path)))
