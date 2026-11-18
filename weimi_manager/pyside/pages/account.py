from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from PySide6.QtWidgets import (
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
)

from ..widgets import MetricCard, PrimaryButton
from .common import TaskPage


class AccountPage(TaskPage):
    def __init__(self, workspace) -> None:
        super().__init__(
            workspace,
            "账号中心",
            "本地账号、数据库备份与运行信息",
            stale_seconds=0,
        )
        cards = QGridLayout()
        self.store_card = MetricCard("店铺", "—", "当前账户")
        self.order_card = MetricCard("订单", "—", "本地记录")
        self.product_card = MetricCard("商品", "—", "本地记录")
        self.size_card = MetricCard("数据库", "—", "当前文件大小")
        for index, card in enumerate(
            (self.store_card, self.order_card, self.product_card, self.size_card)
        ):
            cards.addWidget(card, 0, index)
        self.root_layout.addLayout(cards)
        self.path_label = QLabel()
        self.path_label.setWordWrap(True)
        self.path_label.setObjectName("页面说明")
        self.root_layout.addWidget(self.path_label)
        actions = QHBoxLayout()
        self.backup_button = PrimaryButton("备份数据库")
        self.restore_button = QPushButton("恢复数据库")
        self.open_button = QPushButton("打开数据目录")
        self.clear_sessions_button = QPushButton("清空全部网页登录")
        self.refresh_button = QPushButton("刷新")
        for widget in (
            self.backup_button,
            self.restore_button,
            self.open_button,
            self.clear_sessions_button,
            self.refresh_button,
        ):
            actions.addWidget(widget)
        actions.addStretch(1)
        self.root_layout.addLayout(actions)
        self.root_layout.addStretch(1)
        context = getattr(workspace, "auth_context", None)
        role = getattr(context, "member_role", "owner")
        self.is_manager = role in {"owner", "admin"}
        if not self.is_manager:
            self.backup_button.hide()
            self.restore_button.hide()
            self.open_button.hide()
            self.clear_sessions_button.hide()
        self.backup_button.clicked.connect(self._backup)
        self.restore_button.clicked.connect(self._restore)
        self.open_button.clicked.connect(self._open_directory)
        self.clear_sessions_button.clicked.connect(self._clear_sessions)
        self.refresh_button.clicked.connect(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        counts = self.workspace.db.data_counts()
        database = self.workspace.paths.database
        size = database.stat().st_size if database.is_file() else 0
        self.store_card.set_value(f"{counts.get('stores', 0):,}")
        self.order_card.set_value(f"{counts.get('orders', 0):,}")
        self.product_card.set_value(f"{counts.get('products', 0):,}")
        self.size_card.set_value(f"{size / 1024 / 1024:.1f} MB")
        self.path_label.setText(
            f"数据库：{database}\n"
            f"数据目录：{self.workspace.paths.data_dir}\n"
            f"账号：{self.workspace.auth_context.username}\n"
            f"团队身份："
            + (
                "主账号"
                if self.is_manager
                else "子账号（由主账号分配权限）"
            )
        )
        super().refresh()

    def _backup(self) -> None:
        path, _filter = QFileDialog.getSaveFileName(
            self,
            "备份数据库",
            f"澜琅备份_{datetime.now():%Y%m%d_%H%M%S}.db",
            "SQLite 数据库 (*.db)",
        )
        if not path:
            return
        try:
            self.workspace.db.backup_to(Path(path))
        except Exception as exc:
            QMessageBox.warning(self, "备份失败", str(exc))
            return
        self.notify.emit("备份完成", path)

    def _restore(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(
            self,
            "选择数据库备份",
            "",
            "SQLite 数据库 (*.db);;所有文件 (*.*)",
        )
        if not path:
            return
        answer = QMessageBox.question(
            self,
            "确认恢复",
            "恢复会替换当前账户数据库。系统会先创建恢复前安全备份，是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            safety = (
                self.workspace.paths.data_dir
                / "backups"
                / f"恢复前备份-{datetime.now():%Y%m%d-%H%M%S}.db"
            )
            self.workspace.db.backup_to(safety)
            self.workspace.db.restore_from(Path(path))
        except Exception as exc:
            QMessageBox.warning(self, "恢复失败", str(exc))
            return
        self.refresh()
        self.notify.emit("恢复完成", "数据库完整性检查通过")

    def _open_directory(self) -> None:
        try:
            os.startfile(self.workspace.paths.data_dir)  # type: ignore[attr-defined]
        except OSError as exc:
            QMessageBox.warning(self, "无法打开目录", str(exc))

    def _clear_sessions(self) -> None:
        answer = QMessageBox.question(
            self,
            "清空网页登录",
            "确定清空全部店铺网页登录状态吗？店铺资料和业务数据会保留。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        def work():
            result = self.workspace.db.clear_all_web_logins(
                "账号中心批量清理"
            )
            self.workspace.web_session.cleanup_cache()
            return result

        def done(result: dict[str, int]) -> None:
            self.refresh()
            stores_cleared = int(result.get("stores_cleared") or 0)
            procurement_cleared = int(
                result.get("procurement_cleared") or 0
            )
            self.notify.emit(
                "已清理",
                f"已清理 {stores_cleared} 家店铺网页登录，"
                f"采购账号 {procurement_cleared} 个",
            )

        self.submit(
            "account:clear-web-logins",
            work,
            done,
            self.clear_sessions_button,
        )
