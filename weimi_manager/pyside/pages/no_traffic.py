from __future__ import annotations

from datetime import datetime

from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
)

from ...ui.skeletons import _hosted_no_traffic_product_ids
from ..widgets import DataTable, PrimaryButton
from .common import TaskPage


class NoTrafficPage(TaskPage):
    def __init__(self, workspace) -> None:
        super().__init__(
            workspace,
            "清理无流量",
            "上架时间、流量与安全清理",
            stale_seconds=20,
        )
        controls = QHBoxLayout()
        self.store_combo = QComboBox()
        self.days = QSpinBox()
        self.days.setRange(1, 365)
        self.days.setValue(7)
        self.max_sales = QSpinBox()
        self.max_sales.setRange(0, 100000)
        self.method = QComboBox()
        self.method.addItems(("移入回收站", "下架", "彻底删除"))
        self.scan_button = PrimaryButton("扫描预览")
        self.metrics_button = QPushButton("同步经营指标")
        self.execute_button = QPushButton("执行清理")
        controls.addWidget(self.store_combo)
        controls.addWidget(QLabel("上架天数"))
        controls.addWidget(self.days)
        controls.addWidget(QLabel("最高销量"))
        controls.addWidget(self.max_sales)
        controls.addWidget(self.method)
        controls.addWidget(self.scan_button)
        controls.addWidget(self.metrics_button)
        controls.addWidget(self.execute_button)
        controls.addStretch(1)
        self.root_layout.addLayout(controls)
        self.table = DataTable(
            [
                ("product_id", "商品ID"),
                ("title", "商品名称"),
                ("listed_at", "上架时间"),
                ("views", "30日曝光"),
                ("clicks", "点击"),
                ("sold", "30日销量"),
            ]
        )
        self.table.setSelectionMode(DataTable.SelectionMode.ExtendedSelection)
        self.root_layout.addWidget(self.table, 1)
        self.summary = QLabel()
        self.summary.setObjectName("页面说明")
        self.root_layout.addWidget(self.summary)
        self._stores = list(workspace.db.list_stores())
        self._reload_stores()
        self.scan_button.clicked.connect(self._scan)
        self.metrics_button.clicked.connect(self._sync_metrics)
        self.execute_button.clicked.connect(self._execute)
        self.store_combo.currentIndexChanged.connect(lambda _index: self._scan())
        self._scan()

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

    def _scan(self) -> None:
        self._reload_stores()
        store = self._store()
        if store is None:
            return
        products = self.workspace.db.list_products(store_id=store.id)
        metrics = self.workspace.db.get_product_metrics_map(store.id)
        ids = set(
            _hosted_no_traffic_product_ids(
                products,
                metrics,
                days=self.days.value(),
                max_sales=self.max_sales.value(),
            )
        )
        rows = []
        for product in products:
            if str(product["product_id"]) not in ids:
                continue
            raw = product.get("raw_json") or "{}"
            import json

            try:
                raw_data = json.loads(raw)
            except ValueError:
                raw_data = {}
            listed = int(
                raw_data.get("listing_time")
                or raw_data.get("onsale_time")
                or raw_data.get("create_time")
                or 0
            )
            metric = metrics.get(product["product_id"], {})
            rows.append(
                {
                    **product,
                    "listed_at": (
                        datetime.fromtimestamp(listed).strftime("%Y-%m-%d %H:%M") if listed else "—"
                    ),
                    "views": int(metric.get("views30") or 0),
                    "clicks": int(metric.get("click_cnt") or 0),
                    "sold": int(metric.get("sold30") or 0),
                }
            )
        self.table.set_rows(rows)
        self.summary.setText(f"安全扫描结果 {len(rows)} 个商品")
        super().refresh()

    def _sync_metrics(self) -> None:
        store = self._store()
        if store is None:
            return
        products = self.workspace.db.list_products(store_id=store.id)
        ids = [str(row["product_id"]) for row in products]
        self.submit(
            f"no-traffic:metrics:{store.id}",
            lambda: self.workspace.service.sync_product_performance(store, ids),
            lambda result: self._after(result.message),
            self.metrics_button,
        )

    def _after(self, message: str) -> None:
        self._scan()
        self.notify.emit("操作完成", message)

    def _execute(self) -> None:
        store = self._store()
        rows = [
            self.table.model_data.row_at(index.row())
            for index in self.table.selectionModel().selectedRows()
        ]
        rows = [row for row in rows if row]
        if store is None or not rows:
            return
        way = {"移入回收站": "recycle", "下架": "offshelf", "彻底删除": "real"}[
            self.method.currentText()
        ]
        answer = QMessageBox.question(
            self,
            "确认清理",
            f"确定对 {len(rows)} 个商品执行“{self.method.currentText()}”吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        ids = [str(row["product_id"]) for row in rows]
        self.submit(
            f"no-traffic:execute:{store.id}:{way}",
            lambda: self.workspace.service.private_batch_product_action(
                store,
                ids,
                way,
            ),
            lambda result: self._after(f"已处理 {len(ids)} 个商品"),
            self.execute_button,
        )
