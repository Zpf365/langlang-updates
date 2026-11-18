from __future__ import annotations

import json
from datetime import datetime, time, timedelta
from typing import Any

from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
)

from ..widgets import DataTable, PrimaryButton
from .common import TaskPage

AFTER_SALE_ACTIONS = {
    "同意售后": "accept",
    "拒绝售后": "reject",
    "商家协商": "negotiate",
    "补寄发货": "reship",
    "更新补寄物流": "update_reship",
    "接受换货": "accept_exchange",
    "拒绝换货": "reject_exchange",
}


class OperationsPage(TaskPage):
    def __init__(self, workspace) -> None:
        super().__init__(
            workspace,
            "运营中心",
            "售后单与经营异常的统一入口",
            stale_seconds=15,
        )
        self._stores = list(workspace.db.list_stores())
        controls = QHBoxLayout()
        self.store_combo = QComboBox()
        self.period_combo = QComboBox()
        self.period_combo.addItems(("今日", "昨日"))
        self.sync_button = PrimaryButton("同步售后")
        self.refresh_button = QPushButton("刷新")
        self.action_combo = QComboBox()
        self.action_combo.addItems(AFTER_SALE_ACTIONS)
        self.execute_button = QPushButton("执行售后操作")
        self.detail_button = QPushButton("售后详情")
        controls.addWidget(QLabel("店铺"))
        controls.addWidget(self.store_combo)
        controls.addWidget(QLabel("日期"))
        controls.addWidget(self.period_combo)
        controls.addWidget(self.sync_button)
        controls.addWidget(self.refresh_button)
        controls.addStretch(1)
        controls.addWidget(self.action_combo)
        controls.addWidget(self.execute_button)
        controls.addWidget(self.detail_button)
        self.root_layout.addLayout(controls)
        self.table = DataTable(
            [
                ("after_sale_id", "售后单号"),
                ("order_id", "订单号"),
                ("type", "类型"),
                ("status", "状态"),
                ("reason", "原因"),
                ("refund", "退款金额"),
                ("updated", "更新时间"),
            ]
        )
        self.root_layout.addWidget(self.table, 1)
        self.summary = QLabel()
        self.summary.setObjectName("页面说明")
        self.root_layout.addWidget(self.summary)
        self.sync_button.clicked.connect(self._sync)
        self.refresh_button.clicked.connect(self.refresh)
        self.execute_button.clicked.connect(self._execute)
        self.detail_button.clicked.connect(self._details)
        self.store_combo.currentIndexChanged.connect(lambda _index: self.refresh())
        self._reload_stores()
        self.refresh()

    def _reload_stores(self) -> None:
        current = self.store_combo.currentData()
        self._stores = list(self.workspace.db.list_stores())
        self.store_combo.blockSignals(True)
        self.store_combo.clear()
        for store in self._stores:
            self.store_combo.addItem(store.name, store.id)
        self.store_combo.setCurrentIndex(max(0, self.store_combo.findData(current)))
        self.store_combo.blockSignals(False)

    def _selected_store(self):
        store_id = self.store_combo.currentData()
        return next((store for store in self._stores if store.id == store_id), None)

    def refresh(self) -> None:
        self._reload_stores()
        store = self._selected_store()
        if store is None:
            self.table.set_rows([])
            super().refresh()
            return
        rows = self.workspace.db.list_after_sales(store.id)
        labels = {
            "REFUND": "仅退款",
            "RETURN": "退货退款",
            "EXCHANGE": "换货",
            "RESHIP": "补寄",
        }
        display = [
            {
                **row,
                "after_sale_id": row.get("after_sale_order_id", ""),
                "type": labels.get(
                    row.get("after_sale_type", ""),
                    row.get("after_sale_type", ""),
                ),
                "status": row.get("status_text") or row.get("status", ""),
                "reason": row.get("reason_text", ""),
                "refund": f"¥{int(row.get('refund_amount') or 0) / 100:.2f}",
                "updated": datetime.fromtimestamp(int(row.get("update_time") or 0)).strftime(
                    "%Y-%m-%d %H:%M"
                ),
                "_store_id": store.id,
            }
            for row in rows
        ]
        self.table.set_rows(display)
        total_refund = sum(int(row.get("refund_amount") or 0) for row in rows)
        self.summary.setText(f"售后单 {len(rows)} 条 · 当前退款汇总 ¥{total_refund / 100:.2f}")
        super().refresh()

    def _sync(self) -> None:
        store = self._selected_store()
        if store is None:
            self.notify.emit("提示", "请先添加店铺")
            return
        day = datetime.now().date()
        if self.period_combo.currentText() == "昨日":
            day -= timedelta(days=1)
        start = int(datetime.combine(day, time.min).timestamp())
        end = int(datetime.combine(day, time.max).timestamp())
        self.submit(
            f"aftersale:sync:{store.id}:{day.isoformat()}",
            lambda: self.workspace.service.sync_after_sales(store, start, end),
            lambda result: self._after_sync(result),
            self.sync_button,
        )

    def _after_sync(self, result) -> None:
        self.refresh()
        self.notify.emit("售后同步完成", getattr(result, "message", str(result)))

    def _selected(self) -> dict[str, Any] | None:
        return self.table.selected_row()

    def _execute(self) -> None:
        row = self._selected()
        store = self._selected_store()
        if not row or store is None:
            self.notify.emit("提示", "请先选择售后单")
            return
        action = AFTER_SALE_ACTIONS[self.action_combo.currentText()]
        payload, accepted = __import__(
            "PySide6.QtWidgets",
            fromlist=["QInputDialog"],
        ).QInputDialog.getMultiLineText(
            self,
            self.action_combo.currentText(),
            "可选：输入 JSON 请求参数",
            "{}",
        )
        if not accepted:
            return
        try:
            values = json.loads(payload or "{}")
        except json.JSONDecodeError as exc:
            QMessageBox.warning(self, "JSON 格式错误", exc.msg)
            return
        answer = QMessageBox.question(
            self,
            "确认售后操作",
            f"确定对售后单 {row['after_sale_id']} 执行“{self.action_combo.currentText()}”吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.submit(
            f"aftersale:{action}:{store.id}:{row['after_sale_id']}",
            lambda: self.workspace.service.aftersale_action(
                store,
                str(row["after_sale_id"]),
                action,
                values if isinstance(values, dict) else {},
            ),
            lambda _result: self._after_sync_action("售后操作已完成"),
            self.execute_button,
        )

    def _after_sync_action(self, message: str) -> None:
        self.refresh()
        self.notify.emit("操作完成", message)

    def _details(self) -> None:
        row = self._selected()
        if not row:
            return
        from .dialogs import PayloadDialog

        try:
            raw = json.loads(row.get("raw_json") or "{}")
        except ValueError:
            raw = {}
        PayloadDialog("售后详情", raw if isinstance(raw, dict) else {}, self).exec()
