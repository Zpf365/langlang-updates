from __future__ import annotations

import json
from typing import Any

from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
)

from ...team_service import TeamService
from ..widgets import DataTable, PrimaryButton
from .common import TaskPage
from .dialogs import PayloadDialog


class TeamPage(TaskPage):
    def __init__(self, workspace) -> None:
        super().__init__(workspace, "团队账号", "管理子账号、角色和店铺授权", 10)
        controls = QHBoxLayout()
        self.refresh_button = PrimaryButton("刷新团队")
        self.create_button = QPushButton("新建子账号")
        self.add_button = QPushButton("添加已有账号")
        self.edit_button = QPushButton("编辑权限")
        self.reset_button = QPushButton("重置密码")
        controls.addWidget(self.refresh_button)
        controls.addWidget(self.create_button)
        controls.addWidget(self.add_button)
        controls.addWidget(self.edit_button)
        controls.addWidget(self.reset_button)
        controls.addStretch(1)
        self.root_layout.addLayout(controls)
        self.table = DataTable(
            [
                ("username", "账号"),
                ("email", "邮箱"),
                ("role", "角色"),
                ("active", "启用"),
                ("permissions", "权限"),
                ("grants", "店铺授权"),
            ]
        )
        self.root_layout.addWidget(self.table, 1)
        self.summary = QLabel()
        self.summary.setObjectName("页面说明")
        self.root_layout.addWidget(self.summary)
        self.refresh_button.clicked.connect(self.refresh)
        self.create_button.clicked.connect(self._create)
        self.add_button.clicked.connect(self._add_existing)
        self.edit_button.clicked.connect(self._edit)
        self.reset_button.clicked.connect(self._reset)
        self.refresh()

    def _service(self) -> TeamService:
        context = getattr(self.workspace, "auth_context", None)
        client = getattr(self.workspace, "auth_client", None)
        token = getattr(getattr(context, "tokens", None), "access_token", "")
        if client is None or not token:
            raise RuntimeError("当前账户未连接团队服务")
        return TeamService(client, token)

    def refresh(self) -> None:
        if getattr(self.workspace, "auth_client", None) is None:
            self.summary.setText("当前为本地模式，团队服务未连接")
            super().refresh()
            return
        self.submit(
            "team:list",
            lambda: self._service().list_members(),
            self._after_list,
            self.refresh_button,
        )

    def _after_list(self, members: list[dict[str, Any]]) -> None:
        self.table.set_rows(
            [
                {
                    **member,
                    "active": "是" if member.get("is_active", True) else "否",
                    "permissions": json.dumps(
                        member.get("permissions") or {},
                        ensure_ascii=False,
                    ),
                    "grants": "、".join(member.get("grants") or []),
                }
                for member in members
            ]
        )
        self.summary.setText(f"团队成员 {len(members)} 人")
        super().refresh()

    def _create(self) -> None:
        dialog = PayloadDialog(
            "新建子账号",
            {
                "username": "",
                "password": "",
                "role": "operator",
                "permissions": {},
                "grants": [],
            },
            self,
            editable=True,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.submit(
            "team:create",
            lambda: self._service().create_subaccount(**dialog.payload),
            lambda _result: self._after_change("子账号已创建"),
            self.create_button,
        )

    def _add_existing(self) -> None:
        username, accepted = QInputDialog.getText(self, "添加已有账号", "账号名")
        if not accepted or not username.strip():
            return
        self.submit(
            "team:add-existing",
            lambda: self._service().add_existing_account(username.strip()),
            lambda _result: self._after_change("已有账号已加入团队"),
            self.add_button,
        )

    def _edit(self) -> None:
        row = self.table.selected_row()
        if not row:
            QMessageBox.information(self, "请选择账号", "请先选择需要编辑的成员。")
            return
        try:
            permissions = json.loads(str(row.get("permissions") or "{}"))
        except ValueError:
            permissions = {}
        dialog = PayloadDialog(
            "编辑团队成员",
            {
                "role": row.get("role") or "operator",
                "permissions": permissions,
                "grants": row.get("grants", "").split("、") if row.get("grants") else [],
                "is_active": row.get("active") != "否",
            },
            self,
            editable=True,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        payload = dialog.payload
        self.submit(
            f"team:update:{row.get('member_id')}",
            lambda: self._service().update_member(
                str(row.get("member_id") or ""),
                role=str(payload.get("role") or "operator"),
                permissions=dict(payload.get("permissions") or {}),
                grants=list(payload.get("grants") or []),
                is_active=bool(payload.get("is_active", True)),
            ),
            lambda _result: self._after_change("团队权限已更新"),
            self.edit_button,
        )

    def _reset(self) -> None:
        row = self.table.selected_row()
        if not row:
            QMessageBox.information(self, "请选择账号", "请先选择需要重置密码的成员。")
            return
        password, accepted = QInputDialog.getText(
            self,
            "重置密码",
            "新密码（至少 8 位）",
        )
        if not accepted or not password:
            return
        self.submit(
            f"team:reset:{row.get('member_id')}",
            lambda: self._service().reset_password(
                str(row.get("member_id") or ""),
                password,
            ),
            lambda _result: self._after_change("成员密码已重置"),
            self.reset_button,
        )

    def _after_change(self, message: str) -> None:
        self.notify.emit("操作完成", message)
        self.refresh()
