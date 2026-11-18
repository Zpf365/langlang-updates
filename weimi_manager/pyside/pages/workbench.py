from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ..widgets import MetricCard, PageBase, SectionHeader


class WorkbenchPage(PageBase):
    navigate_requested = Signal(str)

    def __init__(self, workspace) -> None:
        super().__init__(
            "工作台",
            "今日状态、店铺提醒与下一步工作",
            stale_seconds=10,
        )
        self.workspace = workspace
        cards = QGridLayout()
        cards.setHorizontalSpacing(12)
        cards.setVerticalSpacing(12)
        self.store_card = MetricCard("店铺", "—", "已接入工作区")
        self.shipping_card = MetricCard("待发货", "—", "等待物流处理")
        self.product_card = MetricCard("商品详情", "—", "本地可检索商品")
        self.order_card = MetricCard("今日订单", "—", "本地已有订单")
        for index, card in enumerate(
            (self.store_card, self.shipping_card, self.product_card, self.order_card)
        ):
            cards.addWidget(card, 0, index)
        self.root_layout.addLayout(cards)

        self.session_alert = QLabel()
        self.session_alert.setWordWrap(True)
        self.session_alert.setStyleSheet(
            "background:#F7E8E4;color:#A33F33;padding:10px;font-weight:600;"
        )
        self.root_layout.addWidget(self.session_alert)

        body = QWidget()
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(14)
        workflow = QWidget()
        workflow_layout = QVBoxLayout(workflow)
        workflow_layout.setContentsMargins(0, 0, 0, 0)
        workflow_layout.addWidget(SectionHeader("今日工作流", "从店铺检查到发货的推荐顺序"))
        for route, title, detail in (
            ("stores", "01  检查店铺连接", "确认凭据、网页会话和登录状态"),
            ("order_management", "02  同步订单", "拉取订单并解密待发货收货信息"),
            ("products", "03  补齐商品", "同步商品目录和详情"),
            ("operations", "04  处理售后", "检查待处理售后和经营异常"),
        ):
            button = QPushButton(f"{title}\n{detail}")
            button.setProperty("导航按钮", True)
            button.setMinimumHeight(62)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(
                lambda _checked=False, target=route: self.navigate_requested.emit(target)
            )
            workflow_layout.addWidget(button)
        workflow_layout.addStretch(1)

        boundary = QWidget()
        boundary_layout = QVBoxLayout(boundary)
        boundary_layout.setContentsMargins(0, 0, 0, 0)
        boundary_layout.addWidget(SectionHeader("迁移状态", "旧版功能不会被缩减"))
        self.boundary_text = QLabel()
        self.boundary_text.setWordWrap(True)
        self.boundary_text.setObjectName("页面说明")
        boundary_layout.addWidget(self.boundary_text)
        boundary_layout.addStretch(1)
        body_layout.addWidget(workflow, 3)
        body_layout.addWidget(boundary, 2)
        self.root_layout.addWidget(body, 1)
        self.refresh()

    def refresh(self) -> None:
        try:
            counts = self.workspace.db.data_counts()
            stores = self.workspace.db.list_stores()
        except Exception as exc:
            self.notify.emit("工作台加载失败", str(exc))
            super().refresh()
            return
        values = (
            counts.get("stores", 0),
            counts.get("pending_shipping", 0),
            counts.get("catalog_done", 0),
            counts.get("today_orders", 0),
        )
        for card, value in zip(
            (self.store_card, self.shipping_card, self.product_card, self.order_card),
            values,
            strict=True,
        ):
            card.set_value(f"{int(value or 0):,}")
        offline = [
            str(getattr(store, "name", ""))
            for store in stores
            if getattr(store, "web_status", "") in {"已掉线", "需重新扫码"}
        ]
        attention = [
            str(getattr(store, "name", ""))
            for store in stores
            if getattr(store, "web_status", "") in {"网络异常", "连接异常", "平台异常"}
        ]
        if offline:
            self.session_alert.setText(
                "登录提醒：" + "、".join(offline[:6]) + " 已掉线，请重新扫码。"
            )
            self.session_alert.show()
        elif attention:
            self.session_alert.setText("连接提醒：" + "、".join(attention[:6]) + " 当前连接异常。")
            self.session_alert.setStyleSheet(
                "background:#FFF3D8;color:#7A5D27;padding:10px;font-weight:600;"
            )
            self.session_alert.show()
        else:
            self.session_alert.hide()
        self.boundary_text.setText(
            "当前为 1.7.0 迁移分支。"
            "\n\n业务数据仍使用 1.6.63 原有 SQLite、账户目录和接口实现。"
            "\n\n页面只有在旧功能等价测试通过后才切换到 Qt；未迁移操作不会显示模拟结果。"
            f"\n\n更新时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        super().refresh()
