from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel

from ..widgets import PrimaryButton
from .common import TaskPage


class CustomerServicePage(TaskPage):
    def __init__(self, workspace) -> None:
        super().__init__(
            workspace,
            "客服工作台",
            "在澜琅内打开微信小店官方客服页面，保持平台原生实时会话",
            stale_seconds=10,
        )
        row = QHBoxLayout()
        self.store_combo = QComboBox()
        self.open_button = PrimaryButton("打开官方客服工作台")
        row.addWidget(self.store_combo)
        row.addWidget(self.open_button)
        row.addStretch(1)
        self.root_layout.addLayout(row)
        hint = QLabel(
            "客服会话由微信官方页面提供。本机只复用已加密保存的网页登录态，不会复制或保存聊天内容。"
        )
        hint.setWordWrap(True)
        hint.setObjectName("页面说明")
        self.root_layout.addWidget(hint)
        self.root_layout.addStretch(1)
        self.open_button.clicked.connect(self._open)
        self.refresh()

    def refresh(self) -> None:
        current = self.store_combo.currentData()
        self.store_combo.clear()
        self._stores = list(self.workspace.db.list_stores())
        for store in self._stores:
            self.store_combo.addItem(store.name, store.id)
        self.store_combo.setCurrentIndex(max(0, self.store_combo.findData(current)))
        super().refresh()

    def _open(self) -> None:
        store_id = self.store_combo.currentData()
        store = next((item for item in self._stores if item.id == store_id), None)
        if store is None:
            return
        self.submit(
            f"customer-service:{store.id}",
            lambda: self.workspace.web_session.open_official_customer_console(store),
            lambda _result: self.notify.emit("客服工作台已关闭", "网页会话已结束"),
            self.open_button,
        )
