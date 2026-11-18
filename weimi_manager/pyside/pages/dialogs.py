from __future__ import annotations

import json
from typing import Any

from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from ..widgets import PrimaryButton


class PayloadDialog(QDialog):
    def __init__(self, title: str, payload: dict[str, Any], parent=None, *, editable: bool = False):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(780, 600)
        layout = QVBoxLayout(self)
        hint = QLabel("JSON 请求或平台返回内容")
        hint.setObjectName("页面说明")
        layout.addWidget(hint)
        self.editor = QPlainTextEdit(json.dumps(payload, ensure_ascii=False, indent=2))
        self.editor.setReadOnly(not editable)
        layout.addWidget(self.editor, 1)
        actions = QHBoxLayout()
        actions.addStretch(1)
        if editable:
            cancel = QPushButton("取消")
            confirm = PrimaryButton("提交")
            cancel.clicked.connect(self.reject)
            confirm.clicked.connect(self._accept)
            actions.addWidget(cancel)
            actions.addWidget(confirm)
        else:
            close = QPushButton("关闭")
            close.clicked.connect(self.accept)
            actions.addWidget(close)
        layout.addLayout(actions)

    def _accept(self) -> None:
        try:
            payload = json.loads(self.editor.toPlainText())
        except json.JSONDecodeError as exc:
            QMessageBox.warning(self, "JSON 格式错误", exc.msg)
            return
        if not isinstance(payload, dict):
            QMessageBox.warning(self, "JSON 格式错误", "顶层必须是对象。")
            return
        self.payload = payload
        self.accept()
