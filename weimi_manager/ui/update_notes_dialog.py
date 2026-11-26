"""Responsive update-summary dialog shared by the login flow."""

from __future__ import annotations

import tkinter as tk
import webbrowser
from tkinter import ttk
from typing import Any, Callable


class UpdateNotesDialog(tk.Toplevel):
    def __init__(
        self,
        parent: tk.Misc,
        info: Any,
        *,
        on_update: Callable[[], None],
        on_close: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self.info = info
        self.on_update = on_update
        self.on_close = on_close
        self.title(f"发现新版本 v{info.latest_version}")
        self.configure(bg="#ffffff")
        self.resizable(True, True)
        self.minsize(680, 420)

        screen_width = self.winfo_screenwidth()
        screen_height = self.winfo_screenheight()
        width = min(940, max(680, screen_width - 80))
        height = min(640, max(420, int(screen_height * 0.70)))
        x = max(0, (screen_width - width) // 2)
        y = max(0, (screen_height - height) // 2)
        self.geometry(f"{width}x{height}+{x}+{y}")

        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        header = tk.Frame(self, bg="#ffffff", padx=20, pady=14)
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)
        tk.Label(
            header,
            text=f"发现新版本 v{info.latest_version}",
            bg="#ffffff",
            fg="#203443",
            font=("Microsoft YaHei UI", 16, "bold"),
            anchor="w",
        ).grid(row=0, column=0, sticky="ew")
        tk.Label(
            header,
            text=f"当前版本 v{info.current_version}",
            bg="#ffffff",
            fg="#70808d",
            font=("Microsoft YaHei UI", 10),
            anchor="w",
        ).grid(row=1, column=0, sticky="ew", pady=(5, 0))

        body = tk.Frame(self, bg="#ffffff", padx=20, pady=0)
        body.grid(row=1, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)
        self.summary = tk.Text(
            body,
            wrap="word",
            borderwidth=1,
            relief="solid",
            bg="#f8fafb",
            fg="#415665",
            padx=14,
            pady=12,
            font=("Microsoft YaHei UI", 10),
            cursor="arrow",
        )
        self.summary.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(
            body,
            orient="vertical",
            command=self.summary.yview,
        )
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.summary.configure(yscrollcommand=scrollbar.set)
        self.summary.tag_configure(
            "group",
            foreground="#253f53",
            font=("Microsoft YaHei UI", 11, "bold"),
            spacing1=7,
            spacing3=3,
        )
        self.summary.tag_configure("item", lmargin1=14, lmargin2=28, spacing3=3)
        self._render_summary()
        self.summary.configure(state="disabled")

        footer = tk.Frame(self, bg="#f7f9fa", padx=18, pady=12)
        footer.grid(row=2, column=0, sticky="ew")
        if getattr(info, "release_notes_url", ""):
            tk.Button(
                footer,
                text="查看完整更新说明",
                command=self._open_release_notes,
                bg="#f7f9fa",
                fg="#176bb1",
                activebackground="#edf5fb",
                relief="flat",
                bd=0,
                font=("Microsoft YaHei UI", 9),
                cursor="hand2",
            ).pack(side="left")
        tk.Button(
            footer,
            text="立即更新",
            command=self._accept,
            bg="#1877d3",
            fg="white",
            activebackground="#145fa8",
            activeforeground="white",
            relief="flat",
            bd=0,
            padx=18,
            pady=7,
            font=("Microsoft YaHei UI", 10, "bold"),
        ).pack(side="right", padx=(8, 0))
        tk.Button(
            footer,
            text="稍后",
            command=self.close,
            bg="#ffffff",
            fg="#3d505f",
            activebackground="#edf2f5",
            relief="solid",
            bd=1,
            padx=16,
            pady=6,
            font=("Microsoft YaHei UI", 10),
        ).pack(side="right")

        self.protocol("WM_DELETE_WINDOW", self.close)
        self.bind("<Escape>", lambda _event: self.close())
        self.transient(parent)
        self.grab_set()
        self.after_idle(self._focus_first)

    def _render_summary(self) -> None:
        groups = getattr(self.info, "summary", None) or []
        if not groups:
            groups = [{"title": "主要更新", "items": ["本次更新包含稳定性与体验改进。"]}]
        for group in groups:
            self.summary.insert(
                "end",
                f"{group.get('title') or '主要更新'}\n",
                "group",
            )
            for item in group.get("items") or []:
                self.summary.insert("end", f"• {item}\n", "item")
            self.summary.insert("end", "\n")

    def _open_release_notes(self) -> None:
        url = str(getattr(self.info, "release_notes_url", "") or "")
        if url:
            webbrowser.open(url)

    def _focus_first(self) -> None:
        if self.winfo_exists():
            self.lift()
            self.focus_force()
            self.summary.focus_set()

    def _accept(self) -> None:
        self.close()
        self.on_update()

    def close(self) -> None:
        try:
            self.grab_release()
        except tk.TclError:
            pass
        if self.winfo_exists():
            self.destroy()
        if self.on_close is not None:
            self.on_close()
