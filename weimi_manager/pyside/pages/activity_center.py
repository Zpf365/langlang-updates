from __future__ import annotations

import time
from datetime import datetime
from typing import Any

from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTabWidget,
)

from ..widgets import DataTable, PrimaryButton
from .activity import ActivityPage
from .common import TaskPage
from .marketing import MarketingPage


def _selected_rows(table: DataTable) -> list[dict[str, Any]]:
    return [
        row
        for index in table.selectionModel().selectedRows()
        if (row := table.model_data.row_at(index.row())) is not None
    ]


def _record_activity_id(value: Any) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


class LimitedPurchasePage(TaskPage):
    def __init__(self, workspace) -> None:
        super().__init__(
            workspace,
            "限时限量购活动",
            "选择店铺、设置折扣并创建限时限量购",
            stale_seconds=20,
        )
        self._stores = list(workspace.db.list_stores())
        self._preview_rows: list[dict[str, Any]] = []

        controls = QHBoxLayout()
        self.store_combo = QComboBox()
        self.discount = QDoubleSpinBox()
        self.discount.setRange(1.0, 8.5)
        self.discount.setDecimals(2)
        self.discount.setValue(8.5)
        self.discount.setSuffix(" 折")
        self.minimum = QDoubleSpinBox()
        self.minimum.setRange(1.0, 1_000_000.0)
        self.minimum.setDecimals(2)
        self.minimum.setValue(1.0)
        self.minimum.setSuffix(" 元")
        self.duration = QSpinBox()
        self.duration.setRange(1, 168)
        self.duration.setValue(168)
        self.duration.setSuffix(" 小时")
        self.preview_button = PrimaryButton("预览在售商品")
        self.sync_button = QPushButton("同步商品目录")
        controls.addWidget(QLabel("店铺"))
        controls.addWidget(self.store_combo)
        controls.addWidget(QLabel("折扣"))
        controls.addWidget(self.discount)
        controls.addWidget(QLabel("最低优惠"))
        controls.addWidget(self.minimum)
        controls.addWidget(QLabel("持续"))
        controls.addWidget(self.duration)
        controls.addWidget(self.preview_button)
        controls.addWidget(self.sync_button)
        controls.addStretch(1)
        self.root_layout.addLayout(controls)

        self.table = DataTable(
            [
                ("store", "店铺"),
                ("product_id", "商品ID"),
                ("title", "商品标题"),
                ("price", "当前价"),
                ("stock", "库存"),
            ]
        )
        self.table.setSelectionMode(DataTable.SelectionMode.ExtendedSelection)
        self.root_layout.addWidget(self.table, 1)

        actions = QHBoxLayout()
        self.selected_button = PrimaryButton("为选中商品创建")
        self.all_button = QPushButton("整店在售商品创建")
        self.summary = QLabel()
        self.summary.setObjectName("页面说明")
        actions.addWidget(self.selected_button)
        actions.addWidget(self.all_button)
        actions.addStretch(1)
        actions.addWidget(self.summary)
        self.root_layout.addLayout(actions)
        self.preview_button.clicked.connect(self._preview)
        self.sync_button.clicked.connect(self._sync)
        self.selected_button.clicked.connect(lambda: self._create(True))
        self.all_button.clicked.connect(lambda: self._create(False))
        self.refresh()

    def refresh(self) -> None:
        current = self.store_combo.currentData()
        self._stores = list(self.workspace.db.list_stores())
        self.store_combo.blockSignals(True)
        self.store_combo.clear()
        for store in self._stores:
            self.store_combo.addItem(store.name, store.id)
        self.store_combo.setCurrentIndex(max(0, self.store_combo.findData(current)))
        self.store_combo.blockSignals(False)
        self.summary.setText(
            f"店铺 {len(self._stores)} 家 · "
            f"已预览 {len(self._preview_rows)} 个商品"
        )
        super().refresh()

    def _store(self):
        store_id = self.store_combo.currentData()
        return next((store for store in self._stores if store.id == store_id), None)

    def _catalog(self, store, *, sync_if_empty: bool):
        products = self.workspace.db.list_products(store_id=store.id)
        if not products and sync_if_empty:
            self.workspace.service.sync_products(store, refresh_catalog=True)
            products = self.workspace.db.list_products(store_id=store.id)
        return [row for row in products if int(row.get("status") or 0) == 5]

    def _sync(self) -> None:
        store = self._store()
        if store is None:
            return
        self.submit(
            f"activity-center:limited:sync:{store.id}",
            lambda: self.workspace.service.sync_products(store, refresh_catalog=True),
            lambda _result: self._after_sync(),
            self.sync_button,
        )

    def _after_sync(self) -> None:
        self.refresh()
        self._preview()

    def _preview(self) -> None:
        store = self._store()
        if store is None:
            return
        self.submit(
            f"activity-center:limited:preview:{store.id}",
            lambda: self._catalog(store, sync_if_empty=True),
            self._after_preview,
            self.preview_button,
        )

    def _after_preview(self, products: list[dict[str, Any]]) -> None:
        store = self._store()
        self._preview_rows = [
            {
                "store_id": store.id if store else 0,
                "store": store.name if store else "",
                "product_id": str(row.get("product_id") or ""),
                "title": str(row.get("title") or ""),
                "price": f"¥{int(row.get('min_price') or 0) / 100:.2f}",
                "stock": int(row.get("stock_total") or 0),
            }
            for row in products
        ]
        self.table.set_rows(self._preview_rows)
        self.summary.setText(f"已预览 {len(self._preview_rows)} 个在售商品")

    def _create(self, individual: bool) -> None:
        store = self._store()
        if store is None:
            return
        rows = _selected_rows(self.table)
        if individual and not rows:
            QMessageBox.information(self, "请选择商品", "请先选中要创建活动的商品。")
            return
        scope = f"{len(rows)} 个商品" if individual else "整店在售商品"
        if QMessageBox.question(
            self,
            "确认创建限时限量购",
            f"将为{scope}创建 {self.discount.value():g} 折活动，最低优惠 "
            f"{self.minimum.value():.2f} 元，持续 {self.duration.value()} 小时。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return

        def work():
            products = self._catalog(store, sync_if_empty=True)
            ids = (
                [str(row["product_id"]) for row in rows]
                if individual
                else [str(row.get("product_id") or "") for row in products]
            )
            ids = [product_id for product_id in ids if product_id]
            if not ids:
                raise RuntimeError("没有可参与活动的在售商品")
            start_time = int(time.time()) + 300
            end_time = start_time + self.duration.value() * 3600
            result = self.workspace.service.create_snapup_activity(
                store,
                ids,
                self.discount.value() / 10,
                start_time=start_time,
                end_time=end_time,
                min_discount_cents=int(round(self.minimum.value() * 100)),
                create_all=True,
            )
            product_count = int(result.get("product_count") or 0)
            activity_count = int(result.get("activity_count") or 0)
            self.workspace.db.record_activity_operation(
                "限时限量购活动",
                "success",
                store.id,
                store.name,
                _record_activity_id(result.get("activity_id")),
                product_count,
                f"创建 {activity_count} 场活动，共加入 {product_count} 个商品",
                result,
            )
            return result

        self.submit(
            f"activity-center:limited:create:{store.id}",
            work,
            self._after_create,
            self.selected_button if individual else self.all_button,
        )

    def _after_create(self, result: dict[str, Any]) -> None:
        self.notify.emit(
            "创建完成",
            f"已创建 {int(result.get('activity_count') or 0)} 场活动，"
            f"共加入 {int(result.get('product_count') or 0)} 个商品",
        )


class LimitedRenewalPage(TaskPage):
    def __init__(self, workspace) -> None:
        super().__init__(workspace, "限时限量购续期", "读取并复制现有活动", 20)
        self._stores = list(workspace.db.list_stores())
        self.store_combo = QComboBox()
        self.load_button = PrimaryButton("读取限时活动")
        self.renew_button = QPushButton("复制并续期 7 天")
        controls = QHBoxLayout()
        controls.addWidget(self.store_combo)
        controls.addWidget(self.load_button)
        controls.addWidget(self.renew_button)
        controls.addStretch(1)
        self.root_layout.addLayout(controls)
        self.table = DataTable(
            [
                ("title", "活动名称"),
                ("activity_id", "活动ID"),
                ("status", "状态"),
                ("start_time", "开始时间"),
                ("end_time", "结束时间"),
                ("product_count", "商品数"),
            ]
        )
        self.table.setSelectionMode(DataTable.SelectionMode.ExtendedSelection)
        self.root_layout.addWidget(self.table, 1)
        self.load_button.clicked.connect(self._load)
        self.renew_button.clicked.connect(self._renew)
        self.refresh()

    def refresh(self) -> None:
        current = self.store_combo.currentData()
        self._stores = list(self.workspace.db.list_stores())
        self.store_combo.blockSignals(True)
        self.store_combo.clear()
        for store in self._stores:
            self.store_combo.addItem(store.name, store.id)
        self.store_combo.setCurrentIndex(max(0, self.store_combo.findData(current)))
        self.store_combo.blockSignals(False)
        super().refresh()

    def _store(self):
        store_id = self.store_combo.currentData()
        return next((store for store in self._stores if store.id == store_id), None)

    def _load(self) -> None:
        store = self._store()
        if store is None:
            return
        self.submit(
            f"activity-center:renew:load:{store.id}",
            lambda: self.workspace.service.snapup_activities(store),
            self._after_load,
            self.load_button,
        )

    def _after_load(self, rows: list[dict[str, Any]]) -> None:
        self.table.set_rows(
            [
                {
                    **row,
                    "status": {0: "待开始", 1: "生效中", 2: "已结束"}.get(
                        int(row.get("status") or 0), ""
                    ),
                    "start_time": datetime.fromtimestamp(
                        int(row.get("startTime") or 0)
                    ).strftime("%Y-%m-%d %H:%M") if row.get("startTime") else "—",
                    "end_time": datetime.fromtimestamp(
                        int(row.get("endTime") or 0)
                    ).strftime("%Y-%m-%d %H:%M") if row.get("endTime") else "—",
                    "product_count": int(row.get("product_count") or 0),
                }
                for row in rows
            ]
        )

    def _renew(self) -> None:
        store = self._store()
        rows = _selected_rows(self.table)
        if store is None or not rows:
            QMessageBox.information(self, "请选择活动", "请先选择需要续期的活动。")
            return

        def work():
            results = []
            for row in rows:
                old_end = int(row.get("endTime") or 0)
                start_time = max(int(time.time()) + 60, old_end + 1)
                try:
                    self.workspace.web_session.renew_snapup_activity(
                        store,
                        str(row["activity_id"]),
                        start_time,
                        7,
                    )
                    results.append(True)
                except Exception as exc:
                    results.append(str(exc))
            return results

        self.submit(
            f"activity-center:renew:create:{store.id}",
            work,
            lambda results: QMessageBox.information(
                self,
                "续期完成",
                f"成功 {sum(item is True for item in results)} 场。",
            ),
            self.renew_button,
        )


class ActivityRecordsPage(TaskPage):
    def __init__(self, workspace) -> None:
        super().__init__(workspace, "执行记录", "活动、限时活动和优惠券执行结果", 0)
        controls = QHBoxLayout()
        refresh = QPushButton("刷新执行记录")
        controls.addWidget(refresh)
        controls.addStretch(1)
        self.root_layout.addLayout(controls)
        self.table = DataTable(
            [
                ("created_at", "执行时间"),
                ("action", "操作"),
                ("store_name", "店铺"),
                ("activity_id", "活动ID"),
                ("status", "结果"),
                ("item_count", "数量"),
                ("message", "说明"),
            ]
        )
        self.root_layout.addWidget(self.table, 1)
        refresh.clicked.connect(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        self.table.set_rows(self.workspace.db.list_activity_operations(limit=500))
        super().refresh()


class ActivityCenterPage(TaskPage):
    TAB_NAMES = (
        "平台活动",
        "限时限量购活动",
        "优惠券活动",
        "限时限量购续期",
        "优惠券管理",
        "执行记录",
    )

    def __init__(self, workspace) -> None:
        super().__init__(workspace, "活动中心", "平台活动、限时活动、优惠券和续期", 20)
        self.tabs = QTabWidget()
        self.root_layout.addWidget(self.tabs, 1)
        self.platform_page = ActivityPage(workspace)
        self.limited_page = LimitedPurchasePage(workspace)
        self.coupon_activity_page = MarketingPage(workspace)
        self.renewal_page = LimitedRenewalPage(workspace)
        self.coupon_management_page = MarketingPage(workspace)
        self.records_page = ActivityRecordsPage(workspace)
        for name, page in zip(
            self.TAB_NAMES,
            (
                self.platform_page,
                self.limited_page,
                self.coupon_activity_page,
                self.renewal_page,
                self.coupon_management_page,
                self.records_page,
            ),
            strict=True,
        ):
            self.tabs.addTab(page, name)
            page.notify.connect(self.notify.emit)

    def refresh(self) -> None:
        self.limited_page.refresh()
        self.coupon_activity_page.refresh()
        self.renewal_page.refresh()
        self.coupon_management_page.refresh()
        self.records_page.refresh()
        super().refresh()
