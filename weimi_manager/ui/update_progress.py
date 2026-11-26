from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Mapping


def format_bytes(value: int) -> str:
    size = max(0, int(value))
    if size < 1024:
        return f"{size} B"
    units = ("KB", "MB", "GB", "TB")
    amount = float(size)
    for unit in units:
        amount /= 1024.0
        if amount < 1024.0 or unit == units[-1]:
            return f"{amount:.1f} {unit}"
    return f"{size} B"


def format_update_progress(
    message: str,
    current: int,
    total: int,
) -> tuple[str, str, bool, float]:
    """Return stage, detail, determinate, and percentage for real progress."""
    current = max(0, int(current))
    total = max(0, int(total))
    if total > 0:
        percentage = min(100.0, max(0.0, current * 100.0 / total))
        detail = (
            f"{format_bytes(current)} / {format_bytes(total)}"
            f" · {percentage:.0f}%"
        )
        return message, detail, True, percentage
    if current > 0:
        verb = "已下载" if "下载" in message else "已处理"
        return message, f"{verb} {format_bytes(current)}", False, 0.0
    return message, "正在连接更新服务器…", False, 0.0


class UpdateProgressPanel(tk.Frame):
    def __init__(
        self,
        parent: tk.Misc,
        *,
        current_version: str,
        latest_version: str,
        colors: Mapping[str, str],
    ) -> None:
        super().__init__(parent, bg=colors["panel"], padx=28, pady=24)
        self._colors = colors
        self._indeterminate = False
        tk.Label(
            self,
            text=f"正在更新到 {latest_version}",
            bg=colors["panel"],
            fg=colors["text"],
            font=("Microsoft YaHei UI", 17, "bold"),
            anchor="w",
        ).pack(fill="x")
        tk.Label(
            self,
            text=f"当前版本 {current_version} · 更新期间登录页不可操作",
            bg=colors["panel"],
            fg=colors["muted"],
            font=("Microsoft YaHei UI", 9),
            anchor="w",
        ).pack(fill="x", pady=(6, 16))

        self.stage_var = tk.StringVar(value="正在准备更新…")
        tk.Label(
            self,
            textvariable=self.stage_var,
            bg=colors["panel"],
            fg=colors["text"],
            font=("Microsoft YaHei UI", 10, "bold"),
            anchor="w",
        ).pack(fill="x")
        self.detail_var = tk.StringVar(value="")
        tk.Label(
            self,
            textvariable=self.detail_var,
            bg=colors["panel"],
            fg=colors["muted"],
            font=("Microsoft YaHei UI", 9),
            anchor="w",
            justify="left",
            wraplength=470,
        ).pack(fill="x", pady=(5, 10))
        self.progress = ttk.Progressbar(
            self,
            mode="indeterminate",
            maximum=100,
            length=430,
        )
        self.progress.pack(fill="x")
        self.progress.start(12)
        self._indeterminate = True
        tk.Label(
            self,
            text="请保持软件运行，完成后会自动安装并重启。",
            bg=colors["panel"],
            fg=colors["muted"],
            font=("Microsoft YaHei UI", 8),
            anchor="w",
        ).pack(fill="x", pady=(14, 0))

    def set_progress(self, message: str, current: int, total: int) -> None:
        stage, detail, determinate, percentage = format_update_progress(
            message,
            current,
            total,
        )
        self.stage_var.set(stage)
        self.detail_var.set(detail)
        if determinate:
            if self._indeterminate:
                self.progress.stop()
                self.progress.configure(mode="determinate")
                self._indeterminate = False
            self.progress["value"] = percentage
        elif not self._indeterminate:
            self.progress.configure(mode="indeterminate")
            self.progress.start(12)
            self._indeterminate = True

    def mark_installing(self) -> None:
        if self._indeterminate:
            self.progress.stop()
            self.progress.configure(mode="determinate")
            self._indeterminate = False
        self.progress["value"] = 100
        self.stage_var.set("更新包已下载并通过校验")
        self.detail_var.set("正在自动安装并重启，请等待完成")

    def mark_error(self, message: str) -> None:
        if self._indeterminate:
            self.progress.stop()
            self._indeterminate = False
        self.stage_var.set("更新失败")
        self.detail_var.set(message)
        self.progress.configure(mode="determinate")
        self.progress["value"] = 0
