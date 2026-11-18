from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ...sales_related import (
    SalesRelatedRepository,
    SalesRelatedService,
    SkuSelectionMode,
)
from ..widgets import DataTable, PrimaryButton
from .common import TaskPage


def _permission_checker(workspace):
    def allowed(permission: str) -> bool:
        context = getattr(workspace, "auth_context", None)
        if context is None:
            return True
        role = getattr(context, "member_role", "")
        if role in {"owner", "admin"}:
            return True
        permissions = getattr(context, "permissions", {})
        return bool(permissions.get(permission)) if isinstance(permissions, dict) else False

    return allowed


class PurchaseTasksTab(TaskPage):
    def __init__(self, workspace) -> None:
        super().__init__(workspace, "采购任务", "批量导入链接并创建本地任务", 0)
        self.repository = SalesRelatedRepository(workspace.db.path)
        self.service = SalesRelatedService(
            self.repository,
            permission_checker=_permission_checker(workspace),
        )
        self._stores = list(workspace.db.list_stores())

        controls = QHBoxLayout()
        self.store_combo = QComboBox()
        self.mode_combo = QComboBox()
        for mode, label in (
            (SkuSelectionMode.ANY, "任意可售 SKU"),
            (SkuSelectionMode.LOWEST_PRICE, "最低价可售 SKU"),
            (SkuSelectionMode.HIGHEST_PRICE, "最高价可售 SKU"),
            (SkuSelectionMode.SPECIFIED, "指定 SKU 或规格"),
            (SkuSelectionMode.PRODUCT_DEFAULT, "商品默认 SKU"),
        ):
            self.mode_combo.addItem(label, mode.value)
        self.quantity = QSpinBox()
        self.quantity.setRange(1, 999)
        self.quantity.setValue(1)
        self.price_cap = QDoubleSpinBox()
        self.price_cap.setRange(0, 1_000_000)
        self.price_cap.setDecimals(2)
        self.price_cap.setSpecialValueText("不限")
        controls.addWidget(QLabel("目标店铺"))
        controls.addWidget(self.store_combo)
        controls.addWidget(QLabel("SKU 规则"))
        controls.addWidget(self.mode_combo)
        controls.addWidget(QLabel("数量"))
        controls.addWidget(self.quantity)
        controls.addWidget(QLabel("价格上限"))
        controls.addWidget(self.price_cap)
        controls.addStretch(1)
        self.root_layout.addLayout(controls)

        self.links = QPlainTextEdit()
        self.links.setPlaceholderText(
            "每行粘贴一条微信小店商品链接；也可粘贴简单 CSV 内容。"
        )
        self.links.setMaximumHeight(120)
        self.root_layout.addWidget(self.links)
        actions = QHBoxLayout()
        self.import_button = PrimaryButton("导入并创建任务")
        self.refresh_button = QPushButton("刷新任务")
        self.summary = QLabel()
        self.summary.setObjectName("页面说明")
        actions.addWidget(self.import_button)
        actions.addWidget(self.refresh_button)
        actions.addStretch(1)
        actions.addWidget(self.summary)
        self.root_layout.addLayout(actions)
        self.table = DataTable(
            [
                ("task_id", "任务ID"),
                ("store_app_id", "店铺"),
                ("sku_selection_mode", "SKU 规则"),
                ("purchase_quantity", "数量"),
                ("status", "状态"),
                ("status_reason", "说明"),
                ("created_at", "创建时间"),
            ]
        )
        self.root_layout.addWidget(self.table, 1)
        self.import_button.clicked.connect(self._import)
        self.refresh_button.clicked.connect(self.refresh)
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
        tasks = self.repository.list_tasks(limit=1000)
        self.table.set_rows(tasks)
        self.summary.setText(f"本地任务 {len(tasks)} 条")
        super().refresh()

    def _store(self):
        store_id = self.store_combo.currentData()
        return next((store for store in self._stores if store.id == store_id), None)

    def _import(self) -> None:
        store = self._store()
        text = self.links.toPlainText()
        if store is None or not text.strip():
            self.notify.emit("提示", "请选择店铺并粘贴至少一条商品链接")
            return
        self.submit(
            "sales-related:import",
            lambda: self.service.create_tasks_from_text(
                text,
                store_id=store.id,
                store_app_id=store.app_id,
                sku_selection_mode=str(self.mode_combo.currentData()),
                purchase_quantity=self.quantity.value(),
                price_cap_cents=int(round(self.price_cap.value() * 100)),
                created_by=getattr(
                    getattr(self.workspace, "auth_context", None),
                    "username",
                    "",
                ),
                simulation_mode=True,
            ),
            self._after_import,
            self.import_button,
        )

    def _after_import(self, result) -> None:
        parsed, tasks = result
        self.links.clear()
        self.refresh()
        message = f"新建 {len(tasks)} 条任务"
        if parsed.duplicates:
            message += f"，跳过重复 {len(parsed.duplicates)} 条"
        if parsed.invalid:
            message += f"，无效 {len(parsed.invalid)} 条"
        self.notify.emit("导入完成", message)


class SimpleDataTab(QWidget):
    def __init__(self, title: str, description: str, table: DataTable) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        heading = QLabel(title)
        heading.setStyleSheet("font-size:17pt;font-weight:700;")
        detail = QLabel(description)
        detail.setWordWrap(True)
        detail.setObjectName("页面说明")
        layout.addWidget(heading)
        layout.addWidget(detail)
        layout.addWidget(table, 1)


class SalesRelatedPage(TaskPage):
    TAB_NAMES = (
        "采购任务",
        "SKU 与数量规则",
        "订单记录",
        "售后协同",
        "买家白名单",
        "规则设置",
        "执行日志",
    )

    def __init__(self, workspace) -> None:
        super().__init__(
            workspace,
            "动销相关",
            "采购任务、SKU 规则、订单核验与售后协同",
            20,
        )
        self.repository = SalesRelatedRepository(workspace.db.path)
        self.tabs = QTabWidget()
        self.root_layout.addWidget(self.tabs, 1)

        self.tasks_tab = PurchaseTasksTab(workspace)
        self.tasks_tab.notify.connect(self.notify.emit)
        self.rules_table = DataTable(
            [("mode", "SKU 选择方式"), ("rule", "执行规则")]
        )
        self.rules_table.set_rows(
            [
                {"mode": "任意可售 SKU", "rule": "选择第一个满足库存、限购和价格条件的 SKU"},
                {"mode": "最低价可售 SKU", "rule": "先过滤不可售 SKU，再按完整价格选择最低价"},
                {"mode": "最高价可售 SKU", "rule": "先过滤不可售 SKU，再按完整价格选择最高价"},
                {"mode": "指定 SKU / 规格", "rule": "必须匹配 SKU ID 或完整规格，歧义时暂停"},
                {"mode": "商品默认 SKU", "rule": "只使用页面明确标记的默认 SKU，不猜测"},
            ]
        )
        self.orders_table = DataTable(
            [
                ("task_id", "任务ID"),
                ("order_id", "订单号"),
                ("submission_id", "提交编号"),
                ("quantity", "数量"),
                ("payable_cents", "实付分"),
                ("order_status", "订单状态"),
                ("created_at", "创建时间"),
            ]
        )
        self.whitelist_table = DataTable(
            [
                ("store_app_id", "店铺"),
                ("buyer_identifier_type", "标识类型"),
                ("buyer_identifier", "买家标识"),
                ("enabled", "启用"),
                ("single_refund_limit_cents", "单笔上限分"),
                ("daily_refund_limit_cents", "每日上限分"),
                ("valid_from", "生效时间"),
            ]
        )
        self.logs_table = DataTable(
            [
                ("created_at", "时间"),
                ("action", "操作"),
                ("actor", "操作人"),
                ("task_id", "任务ID"),
                ("device_id", "设备"),
                ("decision", "结果"),
            ]
        )

        pages = (
            self.tasks_tab,
            SimpleDataTab(
                "SKU 与数量规则",
                "五种 SKU 模式和单任务购买数量。数量不足时暂停，不自动拆单。",
                self.rules_table,
            ),
            SimpleDataTab(
                "订单记录",
                "只显示已经通过可信订单数据核验的订单；一个任务最多关联一笔订单。",
                self.orders_table,
            ),
            SimpleDataTab(
                "售后协同",
                "当前仅实现规则预检和模拟审核，不执行真实退款。退款申请、商家同意、"
                "支付受理和实际到账分别记录。",
                DataTable(
                    [
                        ("state", "当前能力"),
                        ("description", "说明"),
                    ],
                    [
                        {"state": "模拟审核", "description": "已实现"},
                        {"state": "真实退款", "description": "未接入，等待平台授权与实机验证"},
                    ],
                ),
            ),
            SimpleDataTab(
                "买家白名单",
                "按店铺和经过验证的买家唯一标识匹配；昵称、姓名和手机号不能作为自动批准依据。",
                self.whitelist_table,
            ),
            SimpleDataTab(
                "规则设置",
                "紧急停用、售后类型、单笔、每日和每月额度均由本地规则校验；"
                "真实自动退款默认关闭。",
                DataTable(
                    [("setting", "设置项"), ("value", "当前状态")],
                    [
                        {"setting": "售后自动退款", "value": "关闭"},
                        {"setting": "模拟审核", "value": "可用"},
                        {"setting": "设备任务租约", "value": "已启用"},
                    ],
                ),
            ),
            SimpleDataTab(
                "执行日志",
                "任务、设备租约、订单核验和白名单操作均写入不可修改的审计日志。",
                self.logs_table,
            ),
        )
        for name, page in zip(self.TAB_NAMES, pages, strict=True):
            self.tabs.addTab(page, name)

    def refresh(self) -> None:
        self.tasks_tab.refresh()
        self.orders_table.set_rows(self.repository.list_orders())
        self.whitelist_table.set_rows(self.repository.list_whitelist())
        self.logs_table.set_rows(self.repository.list_audit_logs())
        super().refresh()
