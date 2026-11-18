from __future__ import annotations

from PySide6.QtCore import QDate
from PySide6.QtWidgets import QComboBox, QDateEdit, QGridLayout, QHBoxLayout, QPushButton

from ..widgets import DataTable, MetricCard, PrimaryButton
from .common import TaskPage


class AnalyticsPage(TaskPage):
    def __init__(self, workspace) -> None:
        super().__init__(
            workspace,
            "数据中心",
            "微信小店罗盘经营概览与本地历史记录",
            stale_seconds=20,
        )
        controls = QHBoxLayout()
        self.store_combo = QComboBox()
        self.day_edit = QDateEdit(QDate.currentDate())
        self.day_edit.setCalendarPopup(True)
        self.day_edit.setDisplayFormat("yyyy-MM-dd")
        self.sync_button = PrimaryButton("同步当日")
        self.refresh_button = QPushButton("刷新")
        controls.addWidget(self.store_combo)
        controls.addWidget(self.day_edit)
        controls.addWidget(self.sync_button)
        controls.addWidget(self.refresh_button)
        controls.addStretch(1)
        self.root_layout.addLayout(controls)
        cards = QGridLayout()
        self.gmv = MetricCard("支付金额", "—")
        self.orders = MetricCard("支付订单", "—")
        self.uv = MetricCard("支付人数", "—")
        self.refund = MetricCard("退款金额", "—")
        for index, card in enumerate((self.gmv, self.orders, self.uv, self.refund)):
            cards.addWidget(card, 0, index)
        self.root_layout.addLayout(cards)
        self.table = DataTable(
            [
                ("business_date", "日期"),
                ("gmv", "支付金额"),
                ("orders", "支付订单"),
                ("uv", "支付人数"),
                ("refund", "退款金额"),
            ]
        )
        self.root_layout.addWidget(self.table, 1)
        self._stores = list(workspace.db.list_stores())
        self._reload_stores()
        self.sync_button.clicked.connect(self._sync)
        self.refresh_button.clicked.connect(self.refresh)
        self.store_combo.currentIndexChanged.connect(lambda _index: self.refresh())
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
        rows = self.workspace.db.list_analytics_days(store.id)
        self.table.set_rows(
            [
                {
                    "business_date": row.get("business_date", ""),
                    "gmv": f"¥{int(row.get('pay_gmv') or 0) / 100:.2f}",
                    "orders": row.get("pay_order_cnt", 0),
                    "uv": row.get("pay_uv", 0),
                    "refund": f"¥{int(row.get('pay_refund_gmv') or 0) / 100:.2f}",
                }
                for row in rows
            ]
        )
        selected = (
            self.workspace.db.get_analytics_day(
                store.id,
                self.day_edit.date().toString("yyyy-MM-dd"),
            )
            or {}
        )
        self.gmv.set_value(f"¥{int(selected.get('pay_gmv') or 0) / 100:.2f}")
        self.orders.set_value(str(selected.get("pay_order_cnt") or 0))
        self.uv.set_value(str(selected.get("pay_uv") or 0))
        self.refund.set_value(f"¥{int(selected.get('pay_refund_gmv') or 0) / 100:.2f}")
        super().refresh()

    def _sync(self) -> None:
        store = self._store()
        if store is None:
            return
        day = self.day_edit.date().toString("yyyy-MM-dd")
        self.submit(
            f"analytics:sync:{store.id}:{day}",
            lambda: self.workspace.service.sync_analytics_day(store, day),
            lambda result: self._after_sync(result),
            self.sync_button,
        )

    def _after_sync(self, result) -> None:
        self.refresh()
        self.notify.emit("经营数据同步完成", getattr(result, "message", str(result)))
