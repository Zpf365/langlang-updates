from __future__ import annotations

from datetime import date

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QDialog, QGridLayout, QHBoxLayout, QLabel, QVBoxLayout

from ..widgets import DataTable, MetricCard


class BigScreenWindow(QDialog):
    def __init__(self, workspace, parent=None) -> None:
        super().__init__(parent)
        self.workspace = workspace
        self.setWindowTitle("澜琅电商数据大屏")
        self.resize(1180, 760)
        self.setModal(False)
        layout = QVBoxLayout(self)
        title = QLabel("澜琅电商数据大屏")
        title.setStyleSheet("font-size:25pt;font-weight:800;")
        layout.addWidget(title)
        cards = QGridLayout()
        self.store_card = MetricCard("店铺", "—")
        self.gmv_card = MetricCard("今日支付", "—")
        self.order_card = MetricCard("今日订单", "—")
        self.refund_card = MetricCard("今日退款", "—")
        for index, card in enumerate(
            (self.store_card, self.gmv_card, self.order_card, self.refund_card)
        ):
            cards.addWidget(card, 0, index)
        layout.addLayout(cards)
        self.table = DataTable(
            [
                ("name", "店铺"),
                ("gmv", "今日支付"),
                ("orders", "订单"),
                ("refund", "退款"),
                ("status", "登录状态"),
            ]
        )
        layout.addWidget(self.table, 1)
        footer = QHBoxLayout()
        footer.addWidget(QLabel("Esc 关闭 · 每 60 秒刷新本地汇总"))
        footer.addStretch(1)
        layout.addLayout(footer)
        self.timer = QTimer(self)
        self.timer.setInterval(60000)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()
        self.refresh()

    def refresh(self) -> None:
        stores = list(self.workspace.db.list_stores())
        rows = []
        total_gmv = total_orders = total_refund = 0
        today = date.today().isoformat()
        for store in stores:
            data = self.workspace.db.get_analytics_day(store.id, today) or {}
            gmv = int(data.get("pay_gmv") or 0)
            orders = int(data.get("pay_order_cnt") or 0)
            refund = int(data.get("pay_refund_gmv") or 0)
            total_gmv += gmv
            total_orders += orders
            total_refund += refund
            rows.append(
                {
                    "name": store.name,
                    "gmv": f"¥{gmv / 100:.2f}",
                    "orders": orders,
                    "refund": f"¥{refund / 100:.2f}",
                    "status": store.web_status,
                }
            )
        self.store_card.set_value(str(len(stores)))
        self.gmv_card.set_value(f"¥{total_gmv / 100:.2f}")
        self.order_card.set_value(str(total_orders))
        self.refund_card.set_value(f"¥{total_refund / 100:.2f}")
        self.table.set_rows(rows)

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt event name.
        if event.key() == Qt.Key.Key_Escape:
            self.close()
            return
        super().keyPressEvent(event)
