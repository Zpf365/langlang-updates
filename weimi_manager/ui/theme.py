from __future__ import annotations

import tkinter as tk
from tkinter import ttk


NAVY = "#0b292d"
NAVY_ALT = "#153d3e"
BLUE = "#118c86"
BLUE_HOVER = "#0d7772"
TEXT = "#183537"
MUTED = "#74817d"
BG = "#f4f2eb"
PANEL = "#fffefa"
BORDER = "#ded8ca"
GREEN = "#158a63"
RED = "#bb5546"
GOLD = "#d7b66a"
SOFT_TEAL = "#e3f2ee"
SOFT_GOLD = "#f4ecd8"


def apply_theme(root: tk.Tk) -> None:
    root.configure(bg=BG)
    style = ttk.Style(root)
    style.theme_use("clam")
    root.option_add("*Font", "{Microsoft YaHei UI} 9")
    style.configure("TFrame", background=BG)
    style.configure("Panel.TFrame", background=PANEL)
    style.configure(
        "Card.TFrame",
        background=PANEL,
        relief="solid",
        borderwidth=1,
        bordercolor=BORDER,
    )
    style.configure(
        "TLabel",
        background=BG,
        foreground=TEXT,
        font=("Microsoft YaHei UI", 9),
    )
    style.configure(
        "Panel.TLabel",
        background=PANEL,
        foreground=TEXT,
    )
    style.configure(
        "Title.TLabel",
        background=BG,
        foreground=TEXT,
        font=("Microsoft YaHei UI", 19, "bold"),
    )
    style.configure(
        "Subtitle.TLabel",
        background=BG,
        foreground=MUTED,
        font=("Microsoft YaHei UI", 9),
    )
    style.configure(
        "Section.TLabel",
        background=PANEL,
        foreground=TEXT,
        font=("Microsoft YaHei UI", 11, "bold"),
    )
    style.configure(
        "Primary.TButton",
        background=BLUE,
        foreground="white",
        borderwidth=0,
        padding=(14, 8),
        font=("Microsoft YaHei UI", 9, "bold"),
    )
    style.map(
        "Primary.TButton",
        background=[("active", BLUE_HOVER), ("disabled", "#9cb8ef")],
    )
    style.configure(
        "Secondary.TButton",
        background="#e8efec",
        foreground=TEXT,
        borderwidth=0,
        padding=(9, 7),
    )
    style.map("Secondary.TButton", background=[("active", "#dce7e3")])
    style.configure(
        "Danger.TButton",
        background="#f7e8e4",
        foreground=RED,
        borderwidth=0,
        padding=(9, 7),
    )
    style.map("Danger.TButton", background=[("active", "#f4d8d6")])
    style.configure(
        "Treeview",
        background=PANEL,
        fieldbackground=PANEL,
        foreground=TEXT,
        rowheight=29,
        bordercolor=BORDER,
        lightcolor=BORDER,
        darkcolor=BORDER,
    )
    style.configure(
        "Treeview.Heading",
        background="#f6f2e8",
        foreground=TEXT,
        relief="flat",
        padding=(8, 8),
        font=("Microsoft YaHei UI", 9, "bold"),
    )
    style.map(
        "Treeview",
        background=[("selected", "#d9ebe6")],
        foreground=[("selected", TEXT)],
    )
    style.map("Treeview.Heading", background=[("active", "#ece5d6")])
    style.configure("TEntry", fieldbackground="white", padding=6)
    style.configure("TCombobox", fieldbackground="white", padding=5)
    style.configure(
        "Horizontal.TProgressbar",
        background=BLUE,
        troughcolor="#e8edf4",
        borderwidth=0,
    )


def button(parent: tk.Widget, text: str, command, kind: str = "Secondary") -> ttk.Button:
    return ttk.Button(parent, text=text, command=command, style=f"{kind}.TButton")

