from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from ..dongxiao import DONGXIAO_SECTIONS
from .pages import BasePage
from .theme import BLUE, BORDER, MUTED, PANEL, SOFT_TEAL, TEXT


class DongxiaoPage(BasePage):
    title = "动销中心"
    subtitle = "店铺、商品与真实经营任务的统一工作台"

    def __init__(self, parent: tk.Widget, window) -> None:
        super().__init__(parent, window)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        self.section_buttons: dict[str, tk.Button] = {}
        self._active_section = "overview"
        self.store_count_var = tk.StringVar(value="店铺待读取")
        self.active_store_var = tk.StringVar(value="当前店铺：请在店铺管理中添加")
        self.section_title_var = tk.StringVar()
        self.section_description_var = tk.StringVar()
        self.section_stage_var = tk.StringVar()

        self._build_header()
        self._build_workspace()
        self.show_section("overview")

    def _build_header(self) -> None:
        header = ttk.Frame(self, style="Panel.TFrame", padding=(20, 15))
        header.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        header.columnconfigure(0, weight=1)

        title_box = ttk.Frame(header, style="Panel.TFrame")
        title_box.grid(row=0, column=0, sticky="w")
        ttk.Label(
            title_box,
            text="动销中心",
            font=("Microsoft YaHei UI", 16, "bold"),
        ).pack(anchor="w")
        ttk.Label(
            title_box,
            text="复用澜琅店铺、权限和经营数据，统一管理可追溯的动销任务",
            style="Subtitle.TLabel",
        ).pack(anchor="w", pady=(5, 0))

        status_box = tk.Frame(header, bg=SOFT_TEAL, padx=12, pady=7)
        status_box.grid(row=0, column=1, sticky="e")
        tk.Label(
            status_box,
            text="阶段 2 · 核心模型",
            bg=SOFT_TEAL,
            fg=BLUE,
            font=("Microsoft YaHei UI", 9, "bold"),
        ).pack()

    def _build_workspace(self) -> None:
        workspace = ttk.Frame(self, style="TFrame")
        workspace.grid(row=1, column=0, sticky="nsew")
        workspace.columnconfigure(1, weight=1)
        workspace.rowconfigure(0, weight=1)

        navigation = tk.Frame(
            workspace,
            bg=PANEL,
            width=188,
            highlightbackground=BORDER,
            highlightthickness=1,
        )
        navigation.grid(row=0, column=0, sticky="ns", padx=(0, 12))
        navigation.grid_propagate(False)
        tk.Label(
            navigation,
            text="功能导航",
            bg=PANEL,
            fg=MUTED,
            anchor="w",
            padx=16,
            pady=12,
            font=("Microsoft YaHei UI", 9, "bold"),
        ).pack(fill="x")
        for section in DONGXIAO_SECTIONS:
            nav_button = tk.Button(
                navigation,
                text=section.label,
                command=lambda key=section.key: self.show_section(key),
                bg=PANEL,
                fg=TEXT,
                activebackground=SOFT_TEAL,
                activeforeground=BLUE,
                relief="flat",
                bd=0,
                anchor="w",
                justify="left",
                padx=16,
                pady=9,
                cursor="hand2",
                font=("Microsoft YaHei UI", 9),
            )
            nav_button.pack(fill="x", padx=7, pady=1)
            self.section_buttons[section.key] = nav_button

        tk.Label(
            navigation,
            text="本阶段只提供入口与导航，\n不会发起平台写操作。",
            bg=PANEL,
            fg=MUTED,
            justify="left",
            anchor="w",
            padx=16,
            pady=14,
            font=("Microsoft YaHei UI", 8),
        ).pack(side="bottom", fill="x")

        content = ttk.Frame(workspace, style="Panel.TFrame", padding=(22, 18))
        content.grid(row=0, column=1, sticky="nsew")
        content.columnconfigure(0, weight=1)
        content.rowconfigure(3, weight=1)

        summary = ttk.Frame(content, style="Panel.TFrame")
        summary.grid(row=0, column=0, sticky="ew")
        summary.columnconfigure(0, weight=1)
        ttk.Label(
            summary,
            textvariable=self.active_store_var,
            style="Section.TLabel",
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(
            summary,
            textvariable=self.store_count_var,
            style="Subtitle.TLabel",
        ).grid(row=0, column=1, sticky="e")

        metrics = ttk.Frame(content, style="Panel.TFrame")
        metrics.grid(row=1, column=0, sticky="ew", pady=(14, 18))
        for index, (label, value, source) in enumerate(
            (
                ("商品总数", "—", "等待商品目录同步"),
                ("商品池", "—", "第 3 阶段接入"),
                ("执行任务", "—", "第 4 阶段接入"),
                ("异常任务", "—", "第 4 阶段接入"),
            )
        ):
            metrics.columnconfigure(index, weight=1)
            card = ttk.Frame(
                metrics,
                style="Card.TFrame",
                padding=(15, 12),
            )
            card.grid(
                row=0,
                column=index,
                sticky="nsew",
                padx=(0, 8) if index < 3 else 0,
            )
            ttk.Label(card, text=label, style="Subtitle.TLabel").pack(anchor="w")
            ttk.Label(
                card,
                text=value,
                style="Section.TLabel",
                font=("Microsoft YaHei UI", 18, "bold"),
            ).pack(anchor="w", pady=(7, 2))
            ttk.Label(card, text=source, style="Subtitle.TLabel").pack(anchor="w")

        divider = tk.Frame(content, bg=BORDER, height=1)
        divider.grid(row=2, column=0, sticky="ew", pady=(0, 18))

        body = ttk.Frame(content, style="Panel.TFrame")
        body.grid(row=3, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)
        ttk.Label(
            body,
            textvariable=self.section_title_var,
            font=("Microsoft YaHei UI", 15, "bold"),
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(
            body,
            textvariable=self.section_description_var,
            style="Subtitle.TLabel",
            wraplength=760,
            justify="left",
        ).grid(row=1, column=0, sticky="w", pady=(7, 16))
        ttk.Label(
            body,
            textvariable=self.section_stage_var,
            style="Subtitle.TLabel",
            wraplength=760,
            justify="left",
        ).grid(row=2, column=0, sticky="nw")

    def show_section(self, key: str) -> None:
        section = next(
            (item for item in DONGXIAO_SECTIONS if item.key == key),
            DONGXIAO_SECTIONS[0],
        )
        self._active_section = section.key
        self.section_title_var.set(section.label)
        self.section_description_var.set(section.description)
        self.section_stage_var.set(
            "第 2 阶段已完成核心模型、规则、状态机和独立数据库。商品同步、"
            "任务执行和平台操作将在后续阶段按任务书逐项实现；未完成能力不会"
            "显示为可用操作。"
        )
        for section_key, nav_button in self.section_buttons.items():
            selected = section_key == section.key
            nav_button.configure(
                bg=SOFT_TEAL if selected else PANEL,
                fg=BLUE if selected else TEXT,
            )

    def refresh(self) -> None:
        stores = self.db.list_stores()
        self.store_count_var.set(f"{len(stores)} 家店铺")
        if stores:
            first = stores[0]
            self.active_store_var.set(
                f"当前店铺：{first.name} · 共 {len(stores)} 家可选"
            )
        else:
            self.active_store_var.set("当前店铺：尚未添加店铺")
