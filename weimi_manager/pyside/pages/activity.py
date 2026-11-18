from __future__ import annotations

from datetime import datetime
from typing import Any

from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QMessageBox, QPushButton

from ..widgets import DataTable, PrimaryButton
from .common import TaskPage


class ActivityPage(TaskPage):
    def __init__(self, workspace) -> None:
        super().__init__(
            workspace,
            "平台活动",
            "读取平台活动、商品预检与报名提交",
            stale_seconds=20,
        )
        controls = QHBoxLayout()
        self.store_combo = QComboBox()
        self.load_button = PrimaryButton("读取平台活动")
        self.apply_button = QPushButton("报名选中活动")
        self.preview_button = QPushButton("加载活动商品")
        self.submit_button = QPushButton("提交已选SKU")
        self.remove_button = QPushButton("移除全部报名")
        controls.addWidget(self.store_combo)
        controls.addWidget(self.load_button)
        controls.addWidget(self.apply_button)
        controls.addWidget(self.preview_button)
        controls.addWidget(self.submit_button)
        controls.addWidget(self.remove_button)
        controls.addStretch(1)
        self.root_layout.addLayout(controls)
        self.activity_table = DataTable(
            [
                ("name", "活动名称"),
                ("activity_id", "活动ID"),
                ("state", "活动状态"),
                ("apply", "报名状态"),
                ("apply_end", "报名截止"),
            ]
        )
        self.activity_table.setMaximumHeight(240)
        self.root_layout.addWidget(self.activity_table)
        self.preview_table = DataTable(
            [
                ("store", "店铺"),
                ("product", "商品"),
                ("sku", "SKU"),
                ("current", "原价"),
                ("activity_price", "活动价"),
                ("status", "预检结果"),
            ]
        )
        self.preview_table.setSelectionMode(DataTable.SelectionMode.ExtendedSelection)
        self.root_layout.addWidget(self.preview_table, 1)
        self.summary = QLabel()
        self.summary.setObjectName("页面说明")
        self.root_layout.addWidget(self.summary)
        self._stores = list(workspace.db.list_stores())
        self._reload_stores()
        self._activities = []
        self._preview_rows: list[dict[str, Any]] = []
        self.load_button.clicked.connect(self._load)
        self.apply_button.clicked.connect(self._apply)
        self.preview_button.clicked.connect(self._preview)
        self.submit_button.clicked.connect(self._submit_items)
        self.remove_button.clicked.connect(self._remove_all)
        self.summary.setText("选择基准店铺后，点击“读取平台活动”。")

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

    def _load(self) -> None:
        self._reload_stores()
        store = self._store()
        if store is None:
            return
        self.submit(
            f"activity:catalog:{store.id}",
            lambda: self.workspace.service.platform_activity_catalog(store),
            self._after_catalog,
            self.load_button,
        )

    def _after_catalog(self, payload: dict[str, Any]) -> None:
        self._activities = list(payload.get("activities") or [])
        states = {0: "未开始", 1: "进行中", 2: "已结束"}
        applies = {0: "不可报名", 1: "可报名", 2: "已报名", 3: "已结束"}
        self.activity_table.set_rows(
            [
                {
                    **row,
                    "state": states.get(int(row.get("activity_state") or 0), ""),
                    "apply": applies.get(int(row.get("apply_state") or 0), ""),
                    "apply_end": (
                        datetime.fromtimestamp(int(row.get("apply_end_time") or 0)).strftime(
                            "%Y-%m-%d"
                        )
                        if int(row.get("apply_end_time") or 0)
                        else "—"
                    ),
                }
                for row in self._activities
            ]
        )
        self.summary.setText(
            f"活动目录来自“{payload.get('source_store_name', '')}” · {len(self._activities)} 个活动"
        )
        super().refresh()

    def _selected_activity(self) -> dict[str, Any] | None:
        return self.activity_table.selected_row()

    def _apply(self) -> None:
        row = self._selected_activity()
        store = self._store()
        if not row or store is None:
            return
        activity_id = int(row.get("activity_id") or 0)
        answer = QMessageBox.question(
            self,
            "确认报名",
            f"确定报名活动“{row.get('name', activity_id)}”吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.submit(
            f"activity:apply:{store.id}:{activity_id}",
            lambda: self.workspace.service.apply_platform_activity(store, activity_id),
            lambda result: self._after("活动报名完成", result),
            self.apply_button,
        )

    def _preview(self) -> None:
        row = self._selected_activity()
        store = self._store()
        if not row or store is None:
            return
        activity_id = int(row.get("activity_id") or 0)
        self.submit(
            f"activity:preview:{store.id}:{activity_id}",
            lambda: self.workspace.service.activity_preview(store, activity_id),
            self._after_preview,
            self.preview_button,
        )

    def _after_preview(self, payload: dict[str, Any]) -> None:
        rows = [row for row in payload.get("rows") or [] if isinstance(row, dict)]
        self._preview_rows = rows
        self.preview_table.set_rows(
            [
                {
                    **row,
                    "store": payload.get("store_name", ""),
                    "product": str(
                        row.get("productName")
                        or row.get("productTitle")
                        or row.get("productId")
                        or ""
                    ),
                    "sku": str(row.get("skuId") or row.get("skuName") or ""),
                    "current": (
                        f"¥{int(row.get('currentPrice') or 0) / 100:.2f}"
                        if row.get("currentPrice")
                        else "—"
                    ),
                    "activity_price": (
                        f"¥{int(row.get('activityPrice') or 0) / 100:.2f}"
                        if row.get("activityPrice")
                        else "—"
                    ),
                    "status": str(row.get("status") or row.get("message") or ""),
                }
                for row in rows
            ]
        )
        self.summary.setText(
            f"活动预检：可报名 SKU {payload.get('valid_sku_count', len(rows))} · "
            f"{payload.get('reason') or '请核对活动价后提交'}"
        )

    def _submit_items(self) -> None:
        rows = [
            self.preview_table.model_data.row_at(index.row())
            for index in self.preview_table.selectionModel().selectedRows()
        ]
        rows = [row for row in rows if row]
        activity = self._selected_activity()
        store = self._store()
        if not rows or not activity or store is None:
            return
        answer = QMessageBox.question(
            self,
            "确认提交活动商品",
            f"将提交 {len(rows)} 个 SKU，平台会再次校验价格和库存。是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        activity_id = int(activity.get("activity_id") or 0)
        self.submit(
            f"activity:submit:{store.id}:{activity_id}",
            lambda: self.workspace.service.activity_submit_items(
                store,
                activity_id,
                rows,
            ),
            lambda result: self._after("活动商品已提交", result),
            self.submit_button,
        )

    def _remove_all(self) -> None:
        row = self._selected_activity()
        store = self._store()
        if not row or store is None:
            return
        activity_id = int(row.get("activity_id") or 0)
        answer = QMessageBox.question(
            self,
            "移除全部报名",
            "确定从当前活动移除该店铺的全部报名商品吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.submit(
            f"activity:remove:{store.id}:{activity_id}",
            lambda: self.workspace.service.double11_remove_all(store, activity_id),
            lambda result: self._after("活动报名已移除", result),
            self.remove_button,
        )

    def _after(self, message: str, _result=None) -> None:
        self.notify.emit("操作完成", message)
        self._load()
