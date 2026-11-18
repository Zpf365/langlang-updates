from __future__ import annotations

import sys

from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
)

from ... import __version__
from ...updater import (
    DEFAULT_UPDATE_SOURCE,
    UpdateInfo,
    check_for_update,
    download_and_stage_update,
    is_development_preview_version,
    launch_update_installer,
)
from ..theme import THEMES
from ..widgets import PrimaryButton
from .common import TaskPage


class SettingsPage(TaskPage):
    def __init__(self, workspace, logout_callback) -> None:
        super().__init__(
            workspace,
            "应用设置",
            "更新、刷新、安全和本地数据选项",
            stale_seconds=0,
        )
        self.logout_callback = logout_callback
        self._update_task_id = ""
        self._update_action = ""
        self._available_update: UpdateInfo | None = None
        theme_group = QGroupBox("外观")
        theme_form = QFormLayout(theme_group)
        self.theme_combo = QComboBox()
        for code, palette in THEMES.items():
            self.theme_combo.addItem(palette.name, code)
        manager = getattr(workspace, "theme_manager", None)
        current = manager.current_theme if manager is not None else "cloud_blue"
        self.theme_combo.setCurrentIndex(max(0, self.theme_combo.findData(current)))
        apply_theme = QPushButton("应用主题")
        apply_theme.clicked.connect(self._apply_theme)
        theme_form.addRow("主题：", self.theme_combo)
        theme_form.addRow("", apply_theme)
        self.root_layout.addWidget(theme_group)

        refresh_group = QGroupBox("多店铺自动检查")
        refresh_form = QFormLayout(refresh_group)
        self.batch_size = QComboBox()
        self.batch_size.addItems(("10", "20", "50", "100", "200", "500"))
        self.batch_size.setCurrentText(
            self.workspace.db.get_setting("auto_refresh_batch_size", "50")
        )
        self.auto_refresh = QCheckBox("每分钟自动轮转检查登录与经营状态")
        self.auto_refresh.setChecked(self.workspace.db.get_setting("auto_refresh", "1") == "1")
        save_refresh = QPushButton("保存刷新设置")
        save_refresh.clicked.connect(self._save_refresh)
        refresh_form.addRow("每批店铺数：", self.batch_size)
        refresh_form.addRow("", self.auto_refresh)
        refresh_form.addRow("", save_refresh)
        self.root_layout.addWidget(refresh_group)

        update_group = QGroupBox("软件更新")
        update_form = QFormLayout(update_group)
        self.update_source = QLineEdit(
            self.workspace.db.get_setting("update_source", DEFAULT_UPDATE_SOURCE)
        )
        self.update_auto = QCheckBox("启动时检查更新")
        self.update_auto.setChecked(self.workspace.db.get_setting("update_auto_check", "1") == "1")
        update_actions = QHBoxLayout()
        self.update_button = PrimaryButton("保存并检查更新")
        self.update_button.clicked.connect(self._check_update)
        update_actions.addWidget(self.update_button)
        update_actions.addStretch(1)
        self.update_status = QLabel("尚未检查")
        self.update_status.setObjectName("页面说明")
        update_form.addRow("更新清单：", self.update_source)
        update_form.addRow("", self.update_auto)
        update_form.addRow("", update_actions)
        update_form.addRow("", self.update_status)
        self.root_layout.addWidget(update_group)

        account_group = QGroupBox("当前账户")
        account_layout = QHBoxLayout(account_group)
        context = workspace.auth_context
        account_layout.addWidget(
            QLabel(
                f"{context.username} · {context.license.status_text} · "
                f"剩余 {context.license.remaining_days} 天"
            ),
            1,
        )
        logout = QPushButton("退出登录")
        logout.setObjectName("危险按钮")
        logout.clicked.connect(self.logout_callback)
        account_layout.addWidget(logout)
        self.root_layout.addWidget(account_group)
        self.root_layout.addStretch(1)

    def _apply_theme(self) -> None:
        manager = getattr(self.workspace, "theme_manager", None)
        if manager is None:
            self.notify.emit("提示", "主题管理器未初始化")
            return
        manager.apply(str(self.theme_combo.currentData()))
        self.notify.emit("主题已切换", self.theme_combo.currentText())

    def _save_refresh(self) -> None:
        self.workspace.db.set_setting(
            "auto_refresh_batch_size",
            self.batch_size.currentText(),
        )
        self.workspace.db.set_setting(
            "auto_refresh",
            "1" if self.auto_refresh.isChecked() else "0",
        )
        self.notify.emit("已保存", "多店铺自动检查设置已更新")

    def _check_update(self) -> None:
        source = self.update_source.text().strip()
        if not source.startswith(("https://", "http://", "file://")):
            QMessageBox.warning(self, "更新地址", "更新清单地址无效。")
            return
        self.workspace.db.set_setting("update_source", source)
        self.workspace.db.set_setting(
            "update_auto_check",
            "1" if self.update_auto.isChecked() else "0",
        )
        if is_development_preview_version(__version__):
            return
        self._update_action = "check"
        self.update_button.setEnabled(False)
        self.update_status.setText("正在检查更新…")
        self._update_task_id = self.workspace.task_manager.submit(
            check_for_update,
            source,
            self.workspace.paths.resource_dir,
            key="updater:check",
        )

    def _on_task_completed(self, task_id: str, result: object) -> None:
        super()._on_task_completed(task_id, result)
        if task_id != self._update_task_id:
            return
        self.update_button.setEnabled(True)
        if self._update_action == "check" and isinstance(result, UpdateInfo):
            if result.available:
                self._available_update = result
                self.update_status.setText(f"发现新版本 {result.latest_version}")
                answer = QMessageBox.question(
                    self,
                    "下载更新",
                    f"发现澜琅 {result.latest_version}。是否下载并校验？",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.Yes,
                )
                if answer == QMessageBox.StandardButton.Yes:
                    self._download_update()
            else:
                self.update_status.setText("当前已是最新版本")
        elif self._update_action == "download":
            staged = result
            if not bool(getattr(sys, "frozen", False)):
                QMessageBox.information(self, "源码模式", "自动替换仅支持打包后的 Windows EXE。")
                self.update_button.setEnabled(True)
                return
            script = launch_update_installer(
                staged,
                self.workspace.paths.project_dir,
            )
            self.update_status.setText(f"安装器已启动：{script}")
            QApplication.instance().quit()

    def _download_update(self) -> None:
        if self._available_update is None:
            return
        self._update_action = "download"
        self.update_button.setEnabled(False)
        self.update_status.setText("正在下载并校验更新包…")
        self._update_task_id = self.workspace.task_manager.submit(
            download_and_stage_update,
            self._available_update,
            self.workspace.paths.project_dir,
            key="updater:download",
        )

    def _on_task_failed(self, task_id: str, message: str) -> None:
        super()._on_task_failed(task_id, message)
        if task_id == self._update_task_id:
            self.update_button.setEnabled(True)
            self.update_status.setText(f"更新失败：{message}")
