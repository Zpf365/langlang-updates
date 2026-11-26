from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .. import __version__
from ..auth_service import AuthContext
from .pages.account import AccountPage
from .pages.activity_center import ActivityCenterPage
from .pages.analytics import AnalyticsPage
from .pages.automation import AutomationPage
from .pages.big_screen import BigScreenWindow
from .pages.customer_service import CustomerServicePage
from .pages.diagnostics import DiagnosticsPage
from .pages.dynamic import SalesSuggestionsPage
from .pages.finance import FinancePage
from .pages.hosted import HostedPage
from .pages.marketing import MarketingPage
from .pages.no_traffic import NoTrafficPage
from .pages.operations import OperationsPage
from .pages.order_extract import OrderExtractPage, ParsedOrdersPage
from .pages.orders import OrdersPage
from .pages.procurement import ProcurementPage
from .pages.products import ProductsPage
from .pages.settings import SettingsPage
from .pages.store_groups import StoreGroupsPage
from .pages.storefront import StorefrontPage
from .pages.stores import StoresPage
from .pages.team import TeamPage
from .pages.upload import UploadPage
from .pages.violation import ViolationPage
from .pages.workbench import WorkbenchPage
from .widgets import PageBase

PAGE_DEFINITIONS: dict[str, tuple[str, str]] = {
    "workbench": ("工作台", "今日状态与下一步工作"),
    "diagnostics": ("问题与诊断", "异常、卡顿和任务错误"),
    "stores": ("店铺管理", "维护店铺资料、登录态和经营数据"),
    "order_management": ("订单管理", "微信订单、采购单号、物流和取件码"),
    "orders": ("订单提取", "拉取待发货订单并解析收货信息"),
    "parsed": ("已解析订单", "查询解析和打印记录"),
    "products": ("商品管理", "查看、同步和操作微信小店商品"),
    "upload": ("上传商品", "数据包、模板和店铺上货任务"),
    "operations": ("运营中心", "售后单和经营异常"),
    "customer_service": ("客服工作台", "微信小店官方客服页面"),
    "marketing": ("营销中心", "优惠券同步和状态管理"),
    "analytics": ("数据中心", "经营概览和历史数据"),
    "storefront": ("店铺主页", "商品排序、置顶和隐藏"),
    "finance": ("资金账户", "余额、结算和资金流水"),
    "procurement": ("采购中心", "淘宝、1688采购与订单关联"),
    "automation": ("自动化", "本地定时同步和运行记录"),
    "settings": ("应用设置", "更新、安全和数据选项"),
    "account": ("账号中心", "数据备份、恢复和会话"),
    "store_groups": ("店铺分组管理", "分组和批量店铺归属"),
    "activity_center": ("活动中心", "平台活动、限时限量购、优惠券和续期"),
    "team": ("团队账号", "管理子账号、角色和店铺授权"),
    "violation_blacklist": ("违规商品库", "违规抓取、比对和安全处理"),
    "dynamic": ("动销建议", "曝光、点击和成交分析"),
    "no_traffic": ("清理无流量", "上架时间、流量和安全清理"),
    "hosted": ("托管中心", "多店运营配置和定时执行"),
}

PRIMARY_ROUTES = (
    "products",
    "upload",
    "stores",
    "dynamic",
    "no_traffic",
    "violation_blacklist",
    "hosted",
    "activity_center",
)

MORE_ROUTES = (
    "order_management",
    "store_groups",
    "violation_blacklist",
    "operations",
    "customer_service",
    "procurement",
    "automation",
    "storefront",
    "finance",
    "diagnostics",
    "settings",
    "team",
)

PAGE_PERMISSIONS = {
    "workbench": "stores_view",
    "diagnostics": "team_manage",
    "stores": "stores_view",
    "order_management": "orders_view",
    "orders": "orders_view",
    "parsed": "orders_view",
    "products": "products_view",
    "upload": "products_upload",
    "operations": "after_sales",
    "customer_service": "after_sales",
    "marketing": "activities",
    "analytics": "orders_view",
    "storefront": "stores_edit",
    "finance": "orders_view",
    "procurement": "procurement",
    "automation": "products_edit",
    "settings": "team_manage",
    "account": "",
    "store_groups": "stores_edit",
    "activity_center": "activities",
    "team": "team_manage",
    "violation_blacklist": "products_view",
    "dynamic": "products_view",
    "no_traffic": "products_edit",
    "hosted": "products_edit",
}


class FeaturePage(PageBase):
    """Temporary route shell that never pretends an unfinished action is usable."""

    def __init__(self, workspace, route: str, title: str, description: str) -> None:
        super().__init__(title, description, stale_seconds=0)
        self.workspace = workspace
        self.route = route
        card = QFrame()
        card.setObjectName("内容卡片")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(24, 22, 24, 22)
        card_layout.setSpacing(10)
        heading = QLabel(title)
        heading.setStyleSheet("font-size:18pt;font-weight:700;")
        state = QLabel("1.7.0 页面迁移中")
        state.setStyleSheet("color:#C77912;font-size:11pt;font-weight:700;")
        detail = QLabel(
            "该入口来自 1.6.63 完整业务界面。"
            "在对应业务用例和等价测试完成前，这里不会显示模拟数据或把未完成动作标为可用。"
        )
        detail.setWordWrap(True)
        detail.setObjectName("页面说明")
        card_layout.addWidget(heading)
        card_layout.addWidget(state)
        card_layout.addWidget(detail)
        card_layout.addStretch(1)
        self.root_layout.addWidget(card, 1)


class MainWindow(QMainWindow):
    """Cached PySide6 shell whose routes mirror the proven 1.6.x workspace."""

    account_switch_requested = Signal()
    logout_requested = Signal()

    def __init__(
        self,
        workspace,
        on_reauthenticate: Callable[[AuthContext], None],
        on_logout: Callable[[], None],
    ) -> None:
        super().__init__()
        self.workspace = workspace
        self.on_reauthenticate = on_reauthenticate
        self.on_logout = on_logout
        self._pages: dict[str, QWidget] = {}
        self._nav_buttons: dict[str, QPushButton] = {}
        self._active_route = ""
        self.setWindowTitle(f"澜琅 · 微信小店版 {__version__}")
        self.resize(1400, 850)
        self.setMinimumSize(1180, 720)
        self._build_ui()
        self._connect_signals()
        self.navigate("stores")
        self._status_timer = QTimer(self)
        self._status_timer.setInterval(1000)
        self._status_timer.timeout.connect(self._update_task_status)
        self._status_timer.start()
        self._update_task_status()
        self._diagnostic_timer = QTimer(self)
        self._diagnostic_timer.setInterval(1000)
        self._diagnostic_timer.timeout.connect(self._diagnostic_heartbeat)
        self._diagnostic_timer.start()

    def _build_ui(self) -> None:
        root = QWidget(self)
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        sidebar = QFrame()
        sidebar.setObjectName("侧边栏")
        sidebar.setFixedWidth(218)
        side_layout = QVBoxLayout(sidebar)
        side_layout.setContentsMargins(16, 20, 16, 16)
        side_layout.setSpacing(5)
        brand = QLabel("澜琅")
        brand.setStyleSheet("color:white;font-size:21pt;font-weight:800;padding:4px 6px;")
        subtitle = QLabel("多店铺运营工作台")
        subtitle.setStyleSheet("color:#C7D5E3;font-size:8pt;padding-left:8px;")
        side_layout.addWidget(brand)
        side_layout.addWidget(subtitle)
        side_layout.addSpacing(18)

        for route in PRIMARY_ROUTES:
            if not self._can_permission(PAGE_PERMISSIONS.get(route, "")):
                continue
            title = PAGE_DEFINITIONS[route][0]
            side_layout.addWidget(self._navigation_button(route, title))
        if self._can_permission("orders_view"):
            big_screen = QPushButton("数据大屏")
            big_screen.setProperty("导航按钮", True)
            big_screen.setCursor(Qt.CursorShape.PointingHandCursor)
            big_screen.setMinimumHeight(38)
            big_screen.clicked.connect(self._open_big_screen)
            side_layout.addWidget(big_screen)
        more = QPushButton("更多")
        more.setProperty("导航按钮", True)
        more.setCursor(Qt.CursorShape.PointingHandCursor)
        more.clicked.connect(self._show_more_menu)
        side_layout.addWidget(more)
        side_layout.addWidget(self._navigation_button("account", "账号"))
        side_layout.addStretch(1)
        version = QLabel(f"WECHAT SHOP · {__version__}\n安全存储 · 可追溯操作")
        version.setStyleSheet("color:#AFC2D5;font-size:8pt;padding:4px 6px;")
        side_layout.addWidget(version)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)
        topbar = QFrame()
        topbar.setObjectName("顶部栏")
        top_layout = QHBoxLayout(topbar)
        top_layout.setContentsMargins(24, 12, 24, 12)
        heading_box = QVBoxLayout()
        heading_box.setSpacing(2)
        self.page_title = QLabel()
        self.page_title.setObjectName("页面标题")
        self.page_description = QLabel()
        self.page_description.setObjectName("页面说明")
        heading_box.addWidget(self.page_title)
        heading_box.addWidget(self.page_description)
        top_layout.addLayout(heading_box, 1)
        self.task_status = QPushButton("后台任务：0")
        self.account_label = QLabel()
        self.account_label.setObjectName("页面说明")
        top_layout.addWidget(self.task_status)
        top_layout.addSpacing(12)
        top_layout.addWidget(self.account_label)
        content_layout.addWidget(topbar)
        self.stack = QStackedWidget()
        content_layout.addWidget(self.stack, 1)
        self.statusBar().showMessage("页面缓存已启用 · 业务操作在后台执行")
        layout.addWidget(sidebar)
        layout.addWidget(content, 1)
        self.setCentralWidget(root)
        context = getattr(self.workspace, "auth_context", None)
        username = getattr(context, "username", "") or "当前账户"
        license_status = getattr(getattr(context, "license", None), "status_text", "")
        self.account_label.setText(f"{username} · {license_status or '授权状态未知'}")
        self._big_screen = None

    def _open_big_screen(self) -> None:
        if self._big_screen is None:
            self._big_screen = BigScreenWindow(self.workspace, self)
        self._big_screen.refresh()
        self._big_screen.show()
        self._big_screen.raise_()
        self._big_screen.activateWindow()

    def _navigation_button(self, route: str, title: str) -> QPushButton:
        widget = QPushButton(title)
        widget.setProperty("导航按钮", True)
        widget.setCursor(Qt.CursorShape.PointingHandCursor)
        widget.setMinimumHeight(38)
        widget.clicked.connect(lambda _checked=False, key=route: self.navigate(key))
        self._nav_buttons[route] = widget
        return widget

    def _show_more_menu(self) -> None:
        menu = QMenu(self)
        for route in MORE_ROUTES:
            if not self._can_permission(PAGE_PERMISSIONS.get(route, "")):
                continue
            title = PAGE_DEFINITIONS[route][0]
            menu.addAction(title, lambda key=route: self.navigate(key))
        menu.addSeparator()
        menu.addAction("切换账号", self.account_switch_requested.emit)
        menu.addAction("退出登录", self.logout_requested.emit)
        menu.exec(self.cursor().pos())

    def _can_permission(self, permission: str) -> bool:
        if not permission:
            return True
        context = getattr(self.workspace, "auth_context", None)
        if context is None:
            return True
        role = getattr(context, "member_role", "")
        if permission == "team_manage":
            return role in {"owner", "admin"}
        if role in {"owner", "admin"}:
            return True
        permissions = getattr(context, "permissions", {})
        return bool(
            permissions.get(permission)
            if isinstance(permissions, dict)
            else False
        )

    def _connect_signals(self) -> None:
        manager = getattr(self.workspace, "task_manager", None)
        if manager is None:
            return
        for signal_name in ("task_started", "task_completed", "task_failed"):
            signal = getattr(manager, signal_name, None)
            if signal is not None and hasattr(signal, "connect"):
                signal.connect(lambda *_args: self._update_task_status())

    def _page_factory(self, route: str) -> QWidget:
        title, description = PAGE_DEFINITIONS[route]
        if route == "workbench":
            page = WorkbenchPage(self.workspace)
            page.navigate_requested.connect(self.navigate)
            return page
        if route == "stores":
            page = StoresPage(self.workspace)
            page.notify.connect(self._show_notification)
            return page
        if route == "products":
            page = ProductsPage(self.workspace)
            page.notify.connect(self._show_notification)
            return page
        if route == "order_management":
            page = OrdersPage(self.workspace)
            page.notify.connect(self._show_notification)
            return page
        if route == "store_groups":
            page = StoreGroupsPage(self.workspace)
            page.notify.connect(self._show_notification)
            return page
        if route == "account":
            page = AccountPage(self.workspace)
            page.notify.connect(self._show_notification)
            return page
        if route == "diagnostics":
            page = DiagnosticsPage(self.workspace)
            page.notify.connect(self._show_notification)
            return page
        if route == "settings":
            page = SettingsPage(self.workspace, self.on_logout)
            page.notify.connect(self._show_notification)
            return page
        if route == "marketing":
            page = MarketingPage(self.workspace)
            page.notify.connect(self._show_notification)
            return page
        if route == "analytics":
            page = AnalyticsPage(self.workspace)
            page.notify.connect(self._show_notification)
            return page
        if route == "storefront":
            page = StorefrontPage(self.workspace)
            page.notify.connect(self._show_notification)
            return page
        if route == "finance":
            page = FinancePage(self.workspace)
            page.notify.connect(self._show_notification)
            return page
        if route == "procurement":
            page = ProcurementPage(self.workspace)
            page.notify.connect(self._show_notification)
            return page
        if route == "customer_service":
            page = CustomerServicePage(self.workspace)
            page.notify.connect(self._show_notification)
            return page
        if route == "automation":
            page = AutomationPage(self.workspace)
            page.notify.connect(self._show_notification)
            return page
        if route == "dynamic":
            page = SalesSuggestionsPage(self.workspace)
            page.notify.connect(self._show_notification)
            return page
        if route == "no_traffic":
            page = NoTrafficPage(self.workspace)
            page.notify.connect(self._show_notification)
            return page
        if route == "violation_blacklist":
            page = ViolationPage(self.workspace)
            page.notify.connect(self._show_notification)
            return page
        if route == "activity_center":
            page = ActivityCenterPage(self.workspace)
            page.notify.connect(self._show_notification)
            return page
        if route == "team":
            page = TeamPage(self.workspace)
            page.notify.connect(self._show_notification)
            return page
        if route == "hosted":
            page = HostedPage(self.workspace)
            page.notify.connect(self._show_notification)
            return page
        if route == "upload":
            page = UploadPage(self.workspace)
            page.notify.connect(self._show_notification)
            return page
        if route == "orders":
            page = OrderExtractPage(self.workspace)
            page.notify.connect(self._show_notification)
            return page
        if route == "parsed":
            page = ParsedOrdersPage(self.workspace)
            page.notify.connect(self._show_notification)
            return page
        if route == "operations":
            page = OperationsPage(self.workspace)
            page.notify.connect(self._show_notification)
            return page
        return FeaturePage(self.workspace, route, title, description)

    def _show_notification(self, title: str, message: str) -> None:
        self.statusBar().showMessage(f"{title}：{message}", 8000)

    def _ensure_page(self, route: str) -> QWidget:
        page = self._pages.get(route)
        if page is None:
            page = self._page_factory(route)
            self._pages[route] = page
            self.stack.addWidget(page)
        return page

    def navigate(self, route: str) -> None:
        route = {"double11": "activity_center"}.get(route, route)
        if route not in PAGE_DEFINITIONS:
            self.statusBar().showMessage(f"未知页面：{route}", 8000)
            return
        if not self._can_permission(PAGE_PERMISSIONS.get(route, "")):
            self.statusBar().showMessage("当前子账号没有此功能权限", 8000)
            return
        self._active_route = route
        page = self._ensure_page(route)
        self.stack.setCurrentWidget(page)
        self.page_title.setText(getattr(page, "title", PAGE_DEFINITIONS[route][0]))
        self.page_description.setText(getattr(page, "description", PAGE_DEFINITIONS[route][1]))
        for key, widget in self._nav_buttons.items():
            widget.setProperty("选中", key == route)
            widget.style().unpolish(widget)
            widget.style().polish(widget)
        if hasattr(page, "on_enter"):
            page.on_enter()
        elif hasattr(page, "refresh"):
            page.refresh()
        self.statusBar().showMessage(
            f"{self.page_title.text()} · {datetime.now().strftime('%H:%M:%S')}"
        )

    def _update_task_status(self) -> None:
        manager = getattr(self.workspace, "task_manager", None)
        active = len(manager.active_task_ids()) if manager is not None else 0
        self.task_status.setText(f"后台任务：{active}")

    def _diagnostic_heartbeat(self) -> None:
        diagnostics = getattr(self.workspace, "diagnostics", None)
        if diagnostics is not None:
            diagnostics.heartbeat(route=self._active_route)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt event name.
        self._status_timer.stop()
        self._diagnostic_timer.stop()
        diagnostics = getattr(self.workspace, "diagnostics", None)
        if diagnostics is not None:
            diagnostics.mark_clean_exit()
        event.accept()
