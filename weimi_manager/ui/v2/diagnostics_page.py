from __future__ import annotations

import tkinter as tk
import zipfile
from tkinter import filedialog, messagebox, ttk
from typing import TYPE_CHECKING

from ..theme import button

if TYPE_CHECKING:
    from ..app import MainWindow


class DiagnosticsPage(ttk.Frame):
    title = "问题与诊断"
    subtitle = "异常、卡顿和任务错误会在后台上传，无需逐条提交；下方仅用于补充说明"

    def __init__(self, parent: tk.Widget, window: "MainWindow") -> None:
        super().__init__(parent, style="TFrame", padding=(24, 16, 24, 14))
        self.window = window
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)
        toolbar = ttk.Frame(self, style="TFrame")
        toolbar.grid(row=0, column=0, sticky="ew")
        self.summary_var = tk.StringVar()
        ttk.Label(
            toolbar,
            textvariable=self.summary_var,
            style="Subtitle.TLabel",
        ).pack(side="left")
        button(toolbar, "刷新", self.refresh).pack(side="right", padx=5)
        button(toolbar, "重试上传", self._upload).pack(side="right")
        button(toolbar, "导出诊断包", self._export).pack(side="right", padx=5)
        feedback = ttk.Frame(self, style="Panel.TFrame", padding=12)
        feedback.grid(row=1, column=0, sticky="ew", pady=10)
        feedback.columnconfigure(0, weight=1)
        self.feedback_text = tk.Text(feedback, height=3, wrap="word")
        self.feedback_text.insert(
            "1.0",
            "可选：补充你看到的现象，例如“点击同步后卡住 5 秒”。",
        )
        self.feedback_text.grid(row=0, column=0, sticky="ew")
        button(
            feedback,
            "补充说明（可选）",
            self._submit_feedback,
        ).grid(row=0, column=1, sticky="ns", padx=(10, 0))
        table = ttk.Frame(self, style="TFrame")
        table.grid(row=2, column=0, sticky="nsew")
        table.columnconfigure(0, weight=1)
        table.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(
            table,
            columns=(
                "time",
                "severity",
                "category",
                "route",
                "message",
                "cause",
            ),
            show="headings",
        )
        for column, heading, width in (
            ("time", "时间", 145),
            ("severity", "级别", 75),
            ("category", "类型", 120),
            ("route", "页面", 170),
            ("message", "信息", 520),
            ("cause", "原因", 320),
        ):
            self.tree.heading(column, text=heading)
            self.tree.column(column, width=width, minwidth=60)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scroll.set)

    def refresh(self) -> None:
        reports = self.window.diagnostics.recent(200)
        account = (
            self.window.auth_context.username
            if self.window.auth_context
            else "开发测试"
        )
        self.tree.delete(*self.tree.get_children())
        for report in reports:
            detail = (
                report.get("detail")
                if isinstance(report.get("detail"), dict)
                else {}
            )
            cause = str(
                detail.get("root_cause")
                or detail.get("reason")
                or ""
            )
            self.tree.insert(
                "",
                "end",
                values=(
                    str(report.get("created_at") or "")[:19],
                    report.get("severity") or "",
                    report.get("category") or "",
                    report.get("route") or "",
                    str(report.get("message") or "")[:300],
                    cause[:300],
                ),
            )
        self.summary_var.set(
            f"自动采集已开启 · 待上传 "
            f"{self.window.diagnostics.pending_count()} 条 · "
            f"账号 {account} · "
            f"后台自动上传 "
            + (
                "已启用"
                if self.window.diagnostics.upload_ready
                else "等待服务配置"
            )
        )

    def _upload(self) -> None:
        if not self.window.diagnostics.upload_ready:
            self.window.set_status(
                "自动上报接收端尚未配置，诊断记录已安全保存在本机"
            )
            return
        self.window.diagnostics.flush_async()
        self.window.set_status("诊断日志已在后台上传")
        self.after(800, self.refresh)

    def _submit_feedback(self) -> None:
        description = self.feedback_text.get("1.0", "end").strip()
        if not description or description.startswith("可选："):
            description = "测试人员补充反馈"
        self.window.diagnostics.record(
            "tester_feedback",
            description,
            severity="info",
        )
        self.feedback_text.delete("1.0", "end")
        self.window.diagnostics.flush_async()
        self.refresh()
        self.window.set_status(
            "补充说明已保存"
            + (
                "并进入后台上传队列"
                if self.window.diagnostics.upload_ready
                else "；接收端配置后会自动补传"
            )
        )

    def _export(self) -> None:
        path = filedialog.asksaveasfilename(
            parent=self,
            title="导出测试诊断包",
            defaultextension=".zip",
            filetypes=[("ZIP 文件", "*.zip")],
        )
        if not path:
            return
        with zipfile.ZipFile(
            path,
            "w",
            compression=zipfile.ZIP_DEFLATED,
        ) as archive:
            for report in self.window.diagnostics.outbox.glob("*.json"):
                archive.write(report, report.name)
        messagebox.showinfo(
            "导出完成",
            "诊断包已生成，请发送给管理员。",
            parent=self,
        )
