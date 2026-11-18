from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QPushButton

from ..widgets import DataTable, PrimaryButton
from .common import TaskPage


class SalesSuggestionsPage(TaskPage):
    def __init__(self, workspace) -> None:
        super().__init__(
            workspace,
            "动销建议",
            "曝光、点击、成交与商品动销建议",
            stale_seconds=20,
        )
        controls = QHBoxLayout()
        self.store_combo = QComboBox()
        self.refresh_button = QPushButton("查询")
        self.analyze_button = PrimaryButton("智能分析")
        controls.addWidget(self.store_combo)
        controls.addWidget(self.refresh_button)
        controls.addWidget(self.analyze_button)
        controls.addStretch(1)
        self.root_layout.addLayout(controls)
        self.table = DataTable(
            [
                ("store", "店铺"),
                ("product_id", "商品ID"),
                ("title", "商品名称"),
                ("status", "状态"),
                ("views", "30日曝光"),
                ("sold", "30日销量"),
                ("clicks", "点击"),
                ("pay", "支付"),
            ]
        )
        self.root_layout.addWidget(self.table, 1)
        self.summary = QLabel()
        self.summary.setObjectName("页面说明")
        self.root_layout.addWidget(self.summary)
        self._stores = list(workspace.db.list_stores())
        self._reload_stores()
        self.refresh_button.clicked.connect(self.refresh)
        self.analyze_button.clicked.connect(self._analyze)
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
        products = self.workspace.db.list_products(store_id=store.id)
        metrics = self.workspace.db.get_product_metrics_map(store.id)
        self._products = products
        self.table.set_rows(
            [
                {
                    **row,
                    "store": store.name,
                    "status": row.get("status_text", ""),
                    "views": int((metrics.get(row["product_id"]) or {}).get("views30") or 0),
                    "sold": int((metrics.get(row["product_id"]) or {}).get("sold30") or 0),
                    "clicks": int((metrics.get(row["product_id"]) or {}).get("click_cnt") or 0),
                    "pay": int((metrics.get(row["product_id"]) or {}).get("pay_cnt") or 0),
                }
                for row in products
            ]
        )
        self.summary.setText(f"商品 {len(products)} 条 · 有经营指标 {len(metrics)} 条")
        super().refresh()

    def _analyze(self) -> None:
        store = self._store()
        if store is None:
            return
        ids = [str(row["product_id"]) for row in getattr(self, "_products", [])]
        self.submit(
            f"dynamic:analyze:{store.id}",
            lambda: self.workspace.service.sync_product_performance(store, ids),
            lambda result: self._after(getattr(result, "message", "分析完成")),
            self.analyze_button,
            cancelable=True,
        )

    def _after(self, message: str) -> None:
        self.refresh()
        self.notify.emit("分析完成", message)
