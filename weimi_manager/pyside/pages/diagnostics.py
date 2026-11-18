from __future__ import annotations

import zipfile
from pathlib import Path

from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
)

from ..widgets import DataTable, PrimaryButton
from .common import TaskPage


class DiagnosticsPage(TaskPage):
    def __init__(self, workspace) -> None:
        super().__init__(
            workspace,
            "问题与诊断",
            "异常、卡顿和任务错误会在后台上传，补充说明可人工提交",
            stale_seconds=0,
        )
        toolbar = QHBoxLayout()
        self.summary = QLabel()
        self.summary.setObjectName("页面说明")
        self.refresh_button = QPushButton("刷新")
        self.upload_button = QPushButton("重试上传")
        self.export_button = QPushButton("导出诊断包")
        toolbar.addWidget(self.summary, 1)
        toolbar.addWidget(self.refresh_button)
        toolbar.addWidget(self.upload_button)
        toolbar.addWidget(self.export_button)
        self.root_layout.addLayout(toolbar)
        self.feedback = QPlainTextEdit()
        self.feedback.setPlaceholderText("可选：补充你看到的现象")
        self.feedback.setMaximumHeight(100)
        self.root_layout.addWidget(self.feedback)
        feedback_actions = QHBoxLayout()
        feedback_actions.addStretch(1)
        self.feedback_button = PrimaryButton("提交补充说明")
        feedback_actions.addWidget(self.feedback_button)
        self.root_layout.addLayout(feedback_actions)
        self.table = DataTable(
            [
                ("time", "时间"),
                ("severity", "级别"),
                ("category", "类型"),
                ("route", "页面"),
                ("message", "信息"),
                ("cause", "原因"),
            ]
        )
        self.root_layout.addWidget(self.table, 1)
        self.refresh_button.clicked.connect(self.refresh)
        self.upload_button.clicked.connect(self._upload)
        self.export_button.clicked.connect(self._export)
        self.feedback_button.clicked.connect(self._submit_feedback)
        self.refresh()

    def refresh(self) -> None:
        reports = self.workspace.diagnostics.recent(200)
        rows = []
        for report in reports:
            detail = (
                report.get("detail")
                if isinstance(report.get("detail"), dict)
                else {}
            )
            rows.append(
                {
                    "time": str(report.get("created_at") or "")[:19],
                    "severity": report.get("severity") or "",
                    "category": report.get("category") or "",
                    "route": report.get("route") or "",
                    "message": str(report.get("message") or "")[:300],
                    "cause": str(
                        detail.get("root_cause")
                        or detail.get("reason")
                        or ""
                    )[:300],
                }
            )
        self.table.set_rows(
            rows
        )
        ready = self.workspace.diagnostics.upload_ready
        self.summary.setText(
            f"自动采集已开启 · 待上传 {self.workspace.diagnostics.pending_count()} 条 · "
            + ("远程上传已配置" if ready else "远程接收端未配置，记录只保存在本机")
        )
        super().refresh()

    def _upload(self) -> None:
        if not self.workspace.diagnostics.upload_ready:
            self.notify.emit("提示", "远程诊断接收端尚未配置")
            return
        self.workspace.diagnostics.flush_async()
        self.notify.emit("已提交", "诊断日志正在后台上传")

    def _submit_feedback(self) -> None:
        text = self.feedback.toPlainText().strip()
        if not text:
            text = "用户补充反馈"
        self.workspace.diagnostics.record(
            "tester_feedback",
            text,
            severity="info",
        )
        self.feedback.clear()
        self.refresh()

    def _export(self) -> None:
        path, _filter = QFileDialog.getSaveFileName(
            self,
            "导出诊断包",
            "澜琅诊断包.zip",
            "ZIP 文件 (*.zip)",
        )
        if not path:
            return
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for report in self.workspace.diagnostics.outbox.glob("*.json"):
                archive.write(report, report.name)
        self.notify.emit("导出完成", str(Path(path)))
