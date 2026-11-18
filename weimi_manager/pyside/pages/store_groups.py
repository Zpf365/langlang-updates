from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
)

from ..widgets import DataTable, PrimaryButton
from .common import TaskPage


class StoreGroupsPage(TaskPage):
    def __init__(self, workspace) -> None:
        super().__init__(
            workspace,
            "店铺分组管理",
            "创建分组并批量调整店铺归属",
            stale_seconds=0,
        )
        controls = QHBoxLayout()
        self.group_combo = QComboBox()
        self.refresh_button = QPushButton("刷新")
        self.create_button = QPushButton("新建分组")
        self.rename_button = QPushButton("重命名")
        self.delete_group_button = QPushButton("删除分组")
        self.apply_button = PrimaryButton("应用到所选店铺")
        controls.addWidget(QLabel("分组"))
        controls.addWidget(self.group_combo)
        controls.addWidget(self.refresh_button)
        controls.addWidget(self.create_button)
        controls.addWidget(self.rename_button)
        controls.addWidget(self.delete_group_button)
        controls.addStretch(1)
        controls.addWidget(self.apply_button)
        self.root_layout.addLayout(controls)
        self.table = DataTable(
            [
                ("name", "店铺名称"),
                ("group", "当前分组"),
                ("app_id", "AppID"),
                ("login", "登录状态"),
            ]
        )
        self.table.setSelectionMode(DataTable.SelectionMode.ExtendedSelection)
        self.root_layout.addWidget(self.table, 1)
        self.summary = QLabel()
        self.summary.setObjectName("页面说明")
        self.root_layout.addWidget(self.summary)
        self.refresh_button.clicked.connect(self.refresh)
        self.create_button.clicked.connect(self._create_group)
        self.rename_button.clicked.connect(self._rename_group)
        self.delete_group_button.clicked.connect(self._delete_group)
        self.apply_button.clicked.connect(self._apply_group)
        self.group_combo.currentTextChanged.connect(lambda _value: self._render())
        self.refresh()

    def refresh(self) -> None:
        groups = self.workspace.db.list_store_groups()
        current = self.group_combo.currentText()
        self.group_combo.blockSignals(True)
        self.group_combo.clear()
        self.group_combo.addItems(["全部分组", *groups])
        self.group_combo.setCurrentIndex(max(0, self.group_combo.findText(current)))
        self.group_combo.blockSignals(False)
        self._stores = list(self.workspace.db.list_stores())
        self._render()
        super().refresh()

    def _render(self) -> None:
        selected_group = self.group_combo.currentText()
        rows = []
        for store in self._stores:
            if selected_group != "全部分组" and store.group_name != selected_group:
                continue
            rows.append(
                {
                    "id": store.id,
                    "name": store.name,
                    "group": store.group_name or "未分组",
                    "app_id": store.app_id,
                    "login": store.web_status,
                }
            )
        self.table.set_rows(rows)
        self.summary.setText(f"当前显示 {len(rows)} 家店铺 · 空分组不会自动删除")

    def _create_group(self) -> None:
        name, accepted = QInputDialog.getText(self, "新建分组", "分组名称：")
        if not accepted or not name.strip():
            return
        normalized = name.strip()

        def done(_result):
            self.refresh()
            self.group_combo.setCurrentText(normalized)

        self.submit(
            "store:group:create",
            lambda: self.workspace.db.create_store_group(normalized),
            done,
        )

    def _rename_group(self) -> None:
        old = self.group_combo.currentText()
        if old == "全部分组":
            return
        new, accepted = QInputDialog.getText(self, "重命名分组", "新名称：", text=old)
        if not accepted or not new.strip():
            return
        normalized = new.strip()

        def done(_result):
            self.refresh()
            self.group_combo.setCurrentText(normalized)

        self.submit(
            "store:group:rename",
            lambda: self.workspace.db.rename_store_group(old, normalized),
            done,
        )

    def _delete_group(self) -> None:
        group = self.group_combo.currentText()
        if group == "全部分组":
            return
        answer = QMessageBox.question(
            self,
            "删除分组",
            f"确定删除“{group}”吗？店铺本身不会删除，只会变为未分组。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.submit(
            "store:group:delete",
            lambda: self.workspace.db.delete_store_group(group),
            lambda _result: self.refresh(),
        )

    def _apply_group(self) -> None:
        group = self.group_combo.currentText()
        if group == "全部分组":
            group, accepted = QInputDialog.getText(self, "设置分组", "目标分组：")
            if not accepted:
                return
        selected = [
            int(self.table.model_data.row_at(index.row())["id"])
            for index in self.table.selectionModel().selectedRows()
            if self.table.model_data.row_at(index.row())
        ]
        if not selected or not group.strip():
            self.notify.emit("提示", "请选择店铺并填写目标分组")
            return
        normalized = group.strip()

        def done(_result):
            self.refresh()
            self.notify.emit(
                "已更新",
                f"{len(selected)} 家店铺已归入“{normalized}”",
            )

        self.submit(
            "store:group:apply",
            lambda: self.workspace.db.set_store_groups(
                selected,
                normalized,
            ),
            done,
        )
