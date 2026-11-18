from __future__ import annotations

from datetime import datetime

from PySide6.QtWidgets import QComboBox, QDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton

from ..widgets import DataTable, PrimaryButton
from .common import TaskPage
from .dialogs import PayloadDialog


class MarketingPage(TaskPage):
    def __init__(self, workspace) -> None:
        super().__init__(
            workspace,
            "营销中心",
            "微信小店优惠券同步、创建、修改和状态管理",
            stale_seconds=20,
        )
        controls = QHBoxLayout()
        self.store_combo = QComboBox()
        self.sync_button = PrimaryButton("同步优惠券")
        self.refresh_button = QPushButton("刷新")
        self.create_button = QPushButton("新建")
        self.edit_button = QPushButton("编辑")
        self.stop_button = QPushButton("停用")
        self.delete_button = QPushButton("删除")
        controls.addWidget(self.store_combo)
        controls.addWidget(self.sync_button)
        controls.addWidget(self.refresh_button)
        controls.addStretch(1)
        for widget in (
            self.create_button,
            self.edit_button,
            self.stop_button,
            self.delete_button,
        ):
            controls.addWidget(widget)
        self.root_layout.addLayout(controls)
        self.table = DataTable(
            [
                ("coupon_id", "优惠券编号"),
                ("name", "名称"),
                ("type", "类型"),
                ("status", "状态"),
                ("discount", "优惠"),
                ("stock", "库存"),
                ("received", "领取"),
                ("used", "核销"),
                ("period", "有效期"),
            ]
        )
        self.root_layout.addWidget(self.table, 1)
        self.summary = QLabel()
        self.summary.setObjectName("页面说明")
        self.root_layout.addWidget(self.summary)
        self._stores = list(workspace.db.list_stores())
        self._reload_stores()
        self.sync_button.clicked.connect(self._sync)
        self.refresh_button.clicked.connect(self.refresh)
        self.create_button.clicked.connect(lambda: self._write("create"))
        self.edit_button.clicked.connect(lambda: self._write("update"))
        self.stop_button.clicked.connect(lambda: self._change_status(4))
        self.delete_button.clicked.connect(lambda: self._change_status(5))
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
        rows = self.workspace.db.list_coupons(store.id)
        types = {
            1: "商品条件折扣",
            2: "商品满减",
            3: "商品统一折扣",
            4: "商品直减",
            101: "店铺条件折扣",
            102: "店铺满减",
            103: "店铺统一折扣",
            104: "店铺直减",
        }
        statuses = {1: "编辑中", 2: "已生效", 3: "已过期", 4: "已作废", 5: "已删除"}
        self.table.set_rows(
            [
                {
                    **row,
                    "type": types.get(int(row.get("coupon_type") or 0), ""),
                    "status": statuses.get(int(row.get("status") or 0), ""),
                    "discount": (
                        f"¥{int(row.get('discount_fee') or 0) / 100:.2f}"
                        if row.get("discount_fee")
                        else "—"
                    ),
                    "stock": row.get("total_num", 0),
                    "received": row.get("receive_num", 0),
                    "used": row.get("used_num", 0),
                    "period": " - ".join(
                        datetime.fromtimestamp(int(row.get(key) or 0)).strftime("%Y-%m-%d")
                        for key in ("start_time", "end_time")
                        if int(row.get(key) or 0) > 0
                    ),
                }
                for row in rows
            ]
        )
        self.summary.setText(f"当前店铺优惠券 {len(rows)} 张")
        super().refresh()

    def _sync(self) -> None:
        store = self._store()
        if store is None:
            return
        self.submit(
            f"coupons:sync:{store.id}",
            lambda: self.workspace.service.sync_coupons(store),
            lambda result: self._after("优惠券同步完成", result),
            self.sync_button,
        )

    def _write(self, action: str) -> None:
        store = self._store()
        if store is None:
            return
        selected = self.table.selected_row()
        initial = {}
        if selected:
            import json

            try:
                initial = json.loads(selected.get("raw_json") or "{}")
            except ValueError:
                initial = {}
        dialog = PayloadDialog(f"优惠券{action}", initial, self, editable=True)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.submit(
            f"coupons:{action}:{store.id}",
            lambda: self.workspace.service.coupon_write(store, action, dialog.payload),
            lambda result: self._after("优惠券请求已提交", result),
            self.create_button if action == "create" else self.edit_button,
        )

    def _change_status(self, status: int) -> None:
        row = self.table.selected_row()
        store = self._store()
        if not row or store is None:
            return
        label = "停用" if status == 4 else "删除"
        answer = QMessageBox.question(
            self,
            f"确认{label}",
            f"确定{label}所选优惠券吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        payload = {"coupon_id": row["coupon_id"], "status": status}
        self.submit(
            f"coupons:status:{store.id}:{row['coupon_id']}:{status}",
            lambda: self.workspace.service.coupon_write(store, "status", payload),
            lambda result: self._after(f"优惠券已{label}", result),
            self.stop_button if status == 4 else self.delete_button,
        )

    def _after(self, message: str, _result=None) -> None:
        self.refresh()
        self.notify.emit("操作完成", message)
