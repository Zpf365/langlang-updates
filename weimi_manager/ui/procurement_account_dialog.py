from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk
from typing import Callable


class ProcurementAccountDialog(tk.Toplevel):
    def __init__(
        self,
        parent: tk.Widget,
        *,
        platform_label: str,
        on_save: Callable[[dict[str, str]], None],
    ) -> None:
        super().__init__(parent)
        self.on_save = on_save
        self.title(f"新增{platform_label}账号")
        self.geometry("450x450")
        self.resizable(False, False)
        self.transient(parent.winfo_toplevel())
        self.grab_set()

        self.account_var = tk.StringVar()
        self.password_var = tk.StringVar()
        self.remark_var = tk.StringVar()
        panel = ttk.Frame(self, padding=(26, 20))
        panel.pack(fill="both", expand=True)
        panel.columnconfigure(1, weight=1)

        ttk.Label(panel, text="*", foreground="#e5484d").grid(
            row=0,
            column=0,
            sticky="w",
        )
        ttk.Label(panel, text="账号：").grid(
            row=0,
            column=0,
            sticky="w",
            padx=(12, 0),
        )
        account_entry = ttk.Entry(panel, textvariable=self.account_var)
        account_entry.grid(row=0, column=1, sticky="ew", pady=9)

        ttk.Label(panel, text="*", foreground="#e5484d").grid(
            row=1,
            column=0,
            sticky="w",
        )
        ttk.Label(panel, text="密码：").grid(
            row=1,
            column=0,
            sticky="w",
            padx=(12, 0),
        )
        ttk.Entry(
            panel,
            textvariable=self.password_var,
            show="●",
        ).grid(row=1, column=1, sticky="ew", pady=9)

        ttk.Label(panel, text="备注：").grid(
            row=2,
            column=0,
            sticky="nw",
            pady=(10, 0),
        )
        self.remark_text = tk.Text(
            panel,
            height=7,
            wrap="word",
            relief="solid",
            bd=1,
            padx=7,
            pady=6,
            font=("Microsoft YaHei UI", 9),
        )
        self.remark_text.grid(
            row=2,
            column=1,
            sticky="nsew",
            pady=(9, 0),
        )
        panel.rowconfigure(2, weight=1)

        actions = ttk.Frame(panel)
        actions.grid(
            row=3,
            column=0,
            columnspan=2,
            sticky="e",
            pady=(18, 0),
        )
        ttk.Button(actions, text="保存", command=self._save).pack(
            side="right",
        )
        ttk.Button(actions, text="取消", command=self.destroy).pack(
            side="right",
            padx=(0, 8),
        )
        account_entry.focus_set()

    def _save(self) -> None:
        account = self.account_var.get().strip()
        password = self.password_var.get()
        if not account:
            messagebox.showwarning("账号", "请输入账号。", parent=self)
            return
        if not password:
            messagebox.showwarning("密码", "请输入密码。", parent=self)
            return
        self.on_save(
            {
                "account": account,
                "password": password,
                "remark": self.remark_text.get("1.0", "end-1c").strip(),
            }
        )
        self.destroy()


class ProcurementPasswordDialog(tk.Toplevel):
    def __init__(
        self,
        parent: tk.Widget,
        *,
        title: str,
        field_label: str,
        on_save: Callable[[str], None],
    ) -> None:
        super().__init__(parent)
        self.on_save = on_save
        self.title(title)
        self.geometry("420x190")
        self.resizable(False, False)
        self.transient(parent.winfo_toplevel())
        self.grab_set()
        self.value_var = tk.StringVar()
        panel = ttk.Frame(self, padding=20)
        panel.pack(fill="both", expand=True)
        panel.columnconfigure(1, weight=1)
        ttk.Label(panel, text=field_label).grid(
            row=0,
            column=0,
            sticky="w",
            padx=(0, 12),
        )
        entry = ttk.Entry(
            panel,
            textvariable=self.value_var,
            show="●",
        )
        entry.grid(row=0, column=1, sticky="ew")
        actions = ttk.Frame(panel)
        actions.grid(
            row=1,
            column=0,
            columnspan=2,
            sticky="e",
            pady=(24, 0),
        )
        ttk.Button(actions, text="保存", command=self._save).pack(
            side="right",
        )
        ttk.Button(actions, text="取消", command=self.destroy).pack(
            side="right",
            padx=(0, 8),
        )
        entry.focus_set()

    def _save(self) -> None:
        value = self.value_var.get()
        if not value:
            messagebox.showwarning("密码", "请输入密码。", parent=self)
            return
        self.on_save(value)
        self.destroy()


__all__ = [
    "ProcurementAccountDialog",
    "ProcurementPasswordDialog",
]
