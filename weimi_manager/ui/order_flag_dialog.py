from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable

FLAG_COLORS = {
    "红": "#e5484d",
    "黄": "#e8b11b",
    "蓝": "#3478f6",
    "绿": "#22a06b",
    "紫": "#8e4ec6",
    "橙": "#f0761b",
}


class OrderFlagNoteDialog(tk.Toplevel):
    def __init__(
        self,
        parent: tk.Widget,
        *,
        count: int,
        current_flag: str,
        current_software_note: str,
        current_store_note: str,
        on_save: Callable[[dict[str, object]], None],
    ) -> None:
        super().__init__(parent)
        self.title("标记旗帜与备注")
        self.geometry("590x500")
        self.minsize(540, 450)
        self.transient(parent.winfo_toplevel())
        self.grab_set()
        self.on_save = on_save
        self.software_note = current_software_note
        self.store_note = current_store_note
        self.flag_var = tk.StringVar(value=current_flag)
        self.note_type_var = tk.StringVar(value="软件备注")
        self.sync_store_var = tk.BooleanVar(value=False)

        panel = ttk.Frame(self, padding=18)
        panel.pack(fill="both", expand=True)
        panel.columnconfigure(0, weight=1)
        panel.rowconfigure(4, weight=1)
        ttk.Label(
            panel,
            text=f"已选择 {count} 条订单",
            style="Section.TLabel",
        ).grid(row=0, column=0, sticky="w", pady=(0, 10))

        type_row = ttk.Frame(panel)
        type_row.grid(row=1, column=0, sticky="w", pady=(0, 10))
        ttk.Label(type_row, text="备注类型：").pack(side="left")
        for label in ("软件备注", "店铺备注"):
            ttk.Radiobutton(
                type_row,
                text=label,
                value=label,
                variable=self.note_type_var,
                command=self._switch_note_type,
            ).pack(side="left", padx=(0, 14))

        flag_row = ttk.Frame(panel)
        flag_row.grid(row=2, column=0, sticky="ew", pady=(0, 10))
        ttk.Label(flag_row, text="标记：").pack(side="left", padx=(0, 8))
        ttk.Radiobutton(
            flag_row,
            text="无",
            value="",
            variable=self.flag_var,
        ).pack(side="left", padx=(0, 4))
        for color, value in FLAG_COLORS.items():
            tk.Radiobutton(
                flag_row,
                text=f"⚑ {color}",
                value=color,
                variable=self.flag_var,
                indicatoron=False,
                bg=value,
                fg="white",
                selectcolor=value,
                activebackground=value,
                activeforeground="white",
                relief="flat",
                bd=0,
                padx=7,
                pady=4,
                cursor="hand2",
            ).pack(side="left", padx=2)

        ttk.Label(panel, text="备注内容：").grid(
            row=3,
            column=0,
            sticky="w",
            pady=(4, 5),
        )
        self.text = tk.Text(
            panel,
            wrap="word",
            height=9,
            bg="#fbfcfe",
            fg="#1f2c3d",
            relief="solid",
            bd=1,
            padx=9,
            pady=7,
        )
        self.text.grid(row=4, column=0, sticky="nsew")
        self.text.insert("1.0", current_software_note)

        options = ttk.Frame(panel)
        options.grid(row=5, column=0, sticky="w", pady=(8, 0))
        ttk.Checkbutton(
            options,
            text="同时同步到店铺备注",
            variable=self.sync_store_var,
        ).pack(side="left")
        ttk.Label(
            options,
            text="默认只保存本机软件备注",
        ).pack(side="left", padx=(12, 0))

        actions = ttk.Frame(panel)
        actions.grid(row=6, column=0, sticky="e", pady=(14, 0))
        ttk.Button(actions, text="取消", command=self.destroy).pack(
            side="left",
            padx=(0, 8),
        )
        ttk.Button(
            actions,
            text="保存",
            command=self._save,
            style="Primary.TButton",
        ).pack(side="left")

    def _switch_note_type(self) -> None:
        self.text.delete("1.0", "end")
        self.text.insert(
            "1.0",
            self.software_note
            if self.note_type_var.get() == "软件备注"
            else self.store_note,
        )

    def _save(self) -> None:
        self.on_save(
            {
                "flag": self.flag_var.get(),
                "scope": self.note_type_var.get(),
                "note": self.text.get("1.0", "end").strip(),
                "sync_store": self.sync_store_var.get(),
            }
        )
        self.destroy()
