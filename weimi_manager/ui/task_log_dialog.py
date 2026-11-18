from __future__ import annotations

import json
import tkinter as tk
from datetime import datetime
from tkinter import ttk

from ..db import Database
from .theme import BORDER, MUTED, PANEL, TEXT, button


STATUS_LABELS = {
    "running": "进行中",
    "success": "成功",
    "failed": "失败",
    "partial": "部分失败",
    "submitted": "已提交",
    "cancelled": "已取消",
}


class TaskLogDialog(tk.Toplevel):
    def __init__(self, parent: tk.Misc, db: Database):
        super().__init__(parent)
        self.db = db
        self.title("任务日志")
        self.geometry("1100x680")
        self.minsize(860, 520)
        self.transient(parent.winfo_toplevel())
        self.rowconfigure(1, weight=1)
        self.columnconfigure(0, weight=1)

        toolbar = ttk.Frame(self, padding=(14, 12, 14, 8))
        toolbar.grid(row=0, column=0, sticky="ew")
        ttk.Label(
            toolbar,
            text="联网功能、任务结果和失败原因",
            style="Section.TLabel",
        ).pack(side="left")
        ttk.Label(toolbar, text="状态").pack(side="left", padx=(20, 5))
        self.status_var = tk.StringVar(value="全部状态")
        status_combo = ttk.Combobox(
            toolbar,
            textvariable=self.status_var,
            values=("全部状态", *STATUS_LABELS.values()),
            state="readonly",
            width=12,
        )
        status_combo.pack(side="left")
        status_combo.bind(
            "<<ComboboxSelected>>",
            lambda _event: self._refresh(),
        )
        button(toolbar, "刷新日志", self._refresh).pack(side="right")

        body = ttk.Panedwindow(self, orient="vertical")
        body.grid(
            row=1,
            column=0,
            sticky="nsew",
            padx=14,
            pady=(0, 14),
        )
        table_frame = ttk.Frame(body, style="Panel.TFrame")
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(
            table_frame,
            columns=(
                "time",
                "scope",
                "operation",
                "status",
                "duration",
                "store",
                "message",
            ),
            show="headings",
        )
        for key, label, width in (
            ("time", "时间", 145),
            ("scope", "模块", 100),
            ("operation", "功能", 210),
            ("status", "状态", 85),
            ("duration", "耗时", 75),
            ("store", "店铺", 140),
            ("message", "结果/原因", 330),
        ):
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width, anchor="w")
        self.tree.grid(row=0, column=0, sticky="nsew")
        table_scroll = ttk.Scrollbar(
            table_frame,
            orient="vertical",
            command=self.tree.yview,
        )
        table_scroll.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=table_scroll.set)
        self.tree.bind("<<TreeviewSelect>>", self._show_detail)
        body.add(table_frame, weight=3)

        detail_frame = ttk.Frame(body, style="Panel.TFrame", padding=8)
        detail_frame.columnconfigure(0, weight=1)
        detail_frame.rowconfigure(0, weight=1)
        self.detail = tk.Text(
            detail_frame,
            wrap="word",
            height=10,
            bg="#fbfcfe",
            fg=TEXT,
            relief="solid",
            bd=1,
            padx=10,
            pady=8,
            font=("Consolas", 9),
        )
        self.detail.grid(row=0, column=0, sticky="nsew")
        self.detail.configure(state="disabled")
        body.add(detail_frame, weight=1)
        self._rows: dict[str, dict] = {}
        self._refresh()

    @staticmethod
    def _format_time(value: str) -> str:
        try:
            return datetime.fromisoformat(value).strftime("%m-%d %H:%M:%S")
        except (TypeError, ValueError):
            return str(value or "")[:19]

    @staticmethod
    def _format_duration(value: int) -> str:
        milliseconds = max(0, int(value or 0))
        if milliseconds < 1000:
            return f"{milliseconds} ms"
        return f"{milliseconds / 1000:.1f} s"

    def _refresh(self) -> None:
        selected_status = self.status_var.get()
        status = next(
            (
                key
                for key, label in STATUS_LABELS.items()
                if label == selected_status
            ),
            "",
        )
        rows = self.db.list_task_logs(limit=1000, status=status)
        self.tree.delete(*self.tree.get_children())
        self._rows.clear()
        for index, row in enumerate(rows):
            iid = str(index)
            self._rows[iid] = row
            self.tree.insert(
                "",
                "end",
                iid=iid,
                values=(
                    self._format_time(str(row.get("started_at") or "")),
                    row.get("scope", ""),
                    row.get("operation", ""),
                    STATUS_LABELS.get(
                        str(row.get("status") or ""),
                        row.get("status", ""),
                    ),
                    self._format_duration(
                        int(row.get("duration_ms") or 0)
                    ),
                    row.get("store_name", ""),
                    row.get("message", ""),
                ),
            )

    def _show_detail(self, _event=None) -> None:
        selection = self.tree.selection()
        if not selection:
            return
        row = self._rows.get(selection[0], {})
        metadata = {
            "id": row.get("id"),
            "scope": row.get("scope"),
            "operation": row.get("operation"),
            "status": row.get("status"),
            "started_at": row.get("started_at"),
            "finished_at": row.get("finished_at"),
            "duration_ms": row.get("duration_ms"),
            "store": row.get("store_name"),
        }
        text = json.dumps(metadata, ensure_ascii=False, indent=2)
        detail = str(row.get("detail") or "").strip()
        if detail:
            text += "\n\n" + detail
        self.detail.configure(state="normal")
        self.detail.delete("1.0", "end")
        self.detail.insert("1.0", text)
        self.detail.configure(state="disabled")
