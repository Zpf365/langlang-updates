from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from PySide6.QtWidgets import QComboBox, QFileDialog, QGridLayout, QHBoxLayout, QLabel, QPushButton

from ...exporter import export_xlsx
from ..widgets import DataTable, MetricCard, PrimaryButton
from .common import TaskPage


class FinancePage(TaskPage):
    def __init__(self, workspace) -> None:
        super().__init__(
            workspace,
            "资金账户",
            "微信小店余额、结算和资金流水",
            stale_seconds=10,
        )
        controls = QHBoxLayout()
        self.store_combo = QComboBox()
        self.refresh_button = PrimaryButton("刷新资金流水")
        self.export_button = QPushButton("导出流水")
        controls.addWidget(self.store_combo)
        controls.addWidget(self.refresh_button)
        controls.addWidget(self.export_button)
        controls.addStretch(1)
        self.root_layout.addLayout(controls)
        cards = QGridLayout()
        self.available = MetricCard("可提现", "—")
        self.frozen = MetricCard("暂预留", "—")
        self.settle = MetricCard("待结算", "—")
        self.estimated = MetricCard("预计结算", "—")
        for index, card in enumerate((self.available, self.frozen, self.settle, self.estimated)):
            cards.addWidget(card, 0, index)
        self.root_layout.addLayout(cards)
        self.table = DataTable(
            [
                ("flow_id", "流水编号"),
                ("type", "类型"),
                ("amount", "金额"),
                ("create_time", "发生时间"),
                ("status", "状态"),
            ]
        )
        self.root_layout.addWidget(self.table, 1)
        self.summary = QLabel()
        self.summary.setObjectName("页面说明")
        self.root_layout.addWidget(self.summary)
        self._stores = list(workspace.db.list_stores())
        self._reload_stores()
        self.refresh_button.clicked.connect(self.refresh)
        self.export_button.clicked.connect(self._export)
        self.store_combo.currentIndexChanged.connect(lambda _index: self.refresh())
        self._rows = []
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

    def _store(self):
        store_id = self.store_combo.currentData()
        return next((store for store in self._stores if store.id == store_id), None)

    def refresh(self) -> None:
        self._reload_stores()
        store = self._store()
        if store is None:
            return super().refresh()
        end = datetime.now()
        start = end - timedelta(days=30)
        self.submit(
            f"finance:load:{store.id}",
            lambda: self.workspace.service.fetch_funds_summary(
                store,
                int(start.timestamp()),
                int(end.timestamp()),
            ),
            self._after_load,
            self.refresh_button,
        )

    def _after_load(self, data: dict) -> None:
        rows = [row for row in data.get("flow_list", []) if isinstance(row, dict)]
        self._rows = rows
        for card, key in (
            (self.available, "available_amount"),
            (self.frozen, "pending_amount"),
            (self.settle, "settle_amount"),
            (self.estimated, "estimated_amount"),
        ):
            value = data.get(key)
            card.set_value(f"¥{int(value or 0) / 100:.2f}" if value is not None else "—")
        self.table.set_rows(
            [
                {
                    **row,
                    "amount": f"¥{int(row.get('amount') or 0) / 100:.2f}",
                    "create_time": str(row.get("create_time") or ""),
                }
                for row in rows
            ]
        )
        self.summary.setText(f"最近 30 天资金流水 {len(rows)} 条")
        super().refresh()

    def _export(self) -> None:
        if not self._rows:
            return
        path, _filter = QFileDialog.getSaveFileName(
            self,
            "导出资金流水",
            f"资金流水_{datetime.now():%Y%m%d}.xlsx",
            "Excel 工作簿 (*.xlsx)",
        )
        if not path:
            return
        headers = ["流水编号", "类型", "金额（分）", "发生时间", "状态"]
        rows = [
            [
                row.get("flow_id", ""),
                row.get("type", ""),
                row.get("amount", 0),
                row.get("create_time", ""),
                row.get("status", ""),
            ]
            for row in self._rows
        ]
        export_xlsx(path, "资金流水", headers, rows)
        self.notify.emit("导出完成", str(Path(path)))
