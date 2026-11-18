from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QMessageBox, QPushButton

from ..widgets import DataTable, PrimaryButton
from .common import TaskPage


class StorefrontPage(TaskPage):
    def __init__(self, workspace) -> None:
        super().__init__(
            workspace,
            "店铺主页",
            "微信小店官方主页商品排序、置顶和隐藏",
            stale_seconds=20,
        )
        controls = QHBoxLayout()
        self.store_combo = QComboBox()
        self.refresh_button = PrimaryButton("刷新主页商品")
        self.top_button = QPushButton("置顶")
        self.untop_button = QPushButton("取消置顶")
        self.hide_button = QPushButton("隐藏")
        self.show_button = QPushButton("取消隐藏")
        controls.addWidget(self.store_combo)
        controls.addWidget(self.refresh_button)
        controls.addStretch(1)
        for widget in (
            self.top_button,
            self.untop_button,
            self.hide_button,
            self.show_button,
        ):
            controls.addWidget(widget)
        self.root_layout.addLayout(controls)
        self.table = DataTable(
            [
                ("index", "序号"),
                ("product_id", "商品ID"),
                ("title", "商品名称"),
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
        self.top_button.clicked.connect(lambda: self._action("top", 1))
        self.untop_button.clicked.connect(lambda: self._action("top", 0))
        self.hide_button.clicked.connect(lambda: self._action("hide", 1))
        self.show_button.clicked.connect(lambda: self._action("hide", 0))
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
        self.submit(
            f"storefront:load:{store.id}",
            lambda: self.workspace.service.sync_storefront(store),
            self._after_load,
            self.refresh_button,
        )

    def _after_load(self, rows: list[dict[str, Any]]) -> None:
        self.table.set_rows(
            [
                {
                    **row,
                    "index": index,
                    "status": row.get("status_text", ""),
                }
                for index, row in enumerate(rows, 1)
            ]
        )
        self.summary.setText(f"店铺主页商品 {len(rows)} 个")
        super().refresh()

    def _action(self, action: str, value: int) -> None:
        row = self.table.selected_row()
        store = self._store()
        if not row or store is None:
            return
        answer = QMessageBox.question(
            self,
            "确认主页操作",
            f"确定对商品“{row.get('title', '')}”执行此操作吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        index = int(row.get("index") or 0)
        self.submit(
            f"storefront:{action}:{store.id}:{row['product_id']}",
            lambda: self.workspace.service.storefront_action(
                store,
                action,
                str(row["product_id"]),
                index if action == "reorder" else value,
            ),
            lambda _result: self.refresh(),
            None,
        )
