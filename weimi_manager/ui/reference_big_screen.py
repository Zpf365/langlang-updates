from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import TYPE_CHECKING, Any

from ..dashboard_metrics import DashboardRankRow, DashboardSnapshot, build_dashboard_snapshot
from .pages import BasePage

if TYPE_CHECKING:
    from .app import MainWindow


BG = "#071326"
PANEL = "#14234d"
PANEL_ALT = "#1a2b59"
GRID = "#38508a"
TEXT = "#edf6ff"
MUTED = "#9db5d4"
ORANGE = "#ff9d2d"
ORANGE_ALT = "#ffbd52"
PURPLE = "#913cff"
PURPLE_ALT = "#b24fff"
RED = "#f34f63"
RED_ALT = "#ff6b72"
GREEN = "#29cfa0"
GREEN_ALT = "#4bdfb8"
BLUE = "#6cb8ff"


def _money(cents: int, *, hidden: bool = False) -> str:
    return "******" if hidden else f"¥{int(cents) / 100:,.2f}"


def _number(value: int, *, hidden: bool = False) -> str:
    return "******" if hidden else f"{int(value):,}"


class ReferenceBigScreenPage(BasePage):
    title = "澜琅数据大屏"
    subtitle = "店铺经营、采购执行与利润实时概览"

    def __init__(
        self,
        parent: tk.Widget,
        window: "MainWindow",
        on_close=None,
        refresh_interval_var: tk.StringVar | None = None,
        on_refresh_interval=None,
    ) -> None:
        super().__init__(parent, window)
        self.configure(style="FullScreen.TFrame")
        self.on_close = on_close
        self.on_refresh_interval = on_refresh_interval
        self.refresh_interval_var = refresh_interval_var or tk.StringVar(value="1 分钟")
        self.period_var = tk.StringVar(value="近30天")
        self.data_source_var = tk.StringVar(
            value=window.db.get_setting("big_screen_source", "全部数据")
        )
        if self.data_source_var.get() not in {"全部数据", "店铺数据", "采购数据"}:
            self.data_source_var.set("全部数据")
        self.privacy_var = tk.BooleanVar(
            value=window.db.get_setting("big_screen_privacy", "0") == "1"
        )
        self.updated_var = tk.StringVar(value="正在读取真实经营数据…")
        self.snapshot = DashboardSnapshot()
        self._store_rows: list[Any] = []
        self._card_values: dict[str, tk.Label] = {}
        self._status_labels: dict[str, tk.Label] = {}
        self.period_buttons: dict[str, tk.Button] = {}

        style = ttk.Style(self)
        style.configure(
            "Reference.Treeview",
            background=PANEL_ALT,
            fieldbackground=PANEL_ALT,
            foreground=TEXT,
            rowheight=30,
            borderwidth=0,
        )
        style.configure(
            "Reference.Treeview.Heading",
            background=PANEL,
            foreground=TEXT,
            relief="flat",
            padding=(6, 7),
            font=("Microsoft YaHei UI", 9, "bold"),
        )
        style.map(
            "Reference.Treeview",
            background=[("selected", "#326a9c")],
            foreground=[("selected", "white")],
        )

        self.rowconfigure(3, weight=1)
        self.columnconfigure(0, weight=1)
        self._build_header()
        self._build_period_bar()
        self._build_cards()
        self._build_middle()
        self._build_rankings()

    def _top(self, parent: tk.Widget) -> tk.Frame:
        return tk.Frame(parent, bg=BG)

    def _build_header(self) -> None:
        header = self._top(self)
        header.grid(row=0, column=0, sticky="ew", padx=16, pady=(12, 4))
        header.columnconfigure(0, weight=1)
        tk.Label(
            header,
            text="澜琅数据大屏",
            bg=BG,
            fg="#e7fbff",
            font=("Microsoft YaHei UI", 22, "bold"),
        ).grid(row=0, column=0)
        tools = self._top(header)
        tools.grid(row=0, column=1, sticky="e")
        tk.Label(
            tools,
            text="数据来源",
            bg=BG,
            fg=MUTED,
            font=("Microsoft YaHei UI", 9),
        ).pack(side="left", padx=(0, 5))
        source = ttk.Combobox(
            tools,
            textvariable=self.data_source_var,
            values=("全部数据", "店铺数据", "采购数据"),
            state="readonly",
            width=11,
        )
        source.pack(side="left", padx=(0, 8))
        source.bind("<<ComboboxSelected>>", lambda _event: self._change_source())
        self.privacy_button = tk.Button(
            tools,
            text="显示信息" if self.privacy_var.get() else "隐藏信息",
            command=self._toggle_privacy,
            bg="#1b315e",
            fg=TEXT,
            activebackground="#294477",
            activeforeground="white",
            relief="flat",
            bd=0,
            padx=9,
            pady=5,
            cursor="hand2",
        )
        self.privacy_button.pack(side="left", padx=3)
        tk.Button(
            tools,
            text="刷新",
            command=self._sync,
            bg="#3478f6",
            fg="white",
            activebackground="#245dcc",
            activeforeground="white",
            relief="flat",
            bd=0,
            padx=12,
            pady=5,
            cursor="hand2",
        ).pack(side="left", padx=3)
        interval = ttk.Combobox(
            tools,
            textvariable=self.refresh_interval_var,
            values=("关闭", "30 秒", "1 分钟", "5 分钟"),
            state="readonly",
            width=8,
        )
        interval.pack(side="left", padx=3)
        interval.bind(
            "<<ComboboxSelected>>",
            lambda _event: self.on_refresh_interval()
            if callable(self.on_refresh_interval)
            else None,
        )
        if self.on_close is not None:
            tk.Button(
                tools,
                text="关闭大屏",
                command=self.on_close,
                bg="#263e69",
                fg=TEXT,
                activebackground="#b74444",
                activeforeground="white",
                relief="flat",
                bd=0,
                padx=12,
                pady=5,
                cursor="hand2",
            ).pack(side="left", padx=(8, 0))
        tk.Label(
            header,
            textvariable=self.updated_var,
            bg=BG,
            fg="#7894bc",
            font=("Microsoft YaHei UI", 8),
        ).grid(row=1, column=0, columnspan=2, sticky="e", pady=(2, 0))

    def _build_period_bar(self) -> None:
        bar = self._top(self)
        bar.grid(row=1, column=0, sticky="ew", padx=16, pady=(2, 8))
        for label in ("今日", "昨日", "近7天", "近15天", "近30天"):
            control = tk.Button(
                bar,
                text=label,
                command=lambda selected=label: self._change_period(selected),
                bg="#245be6" if label == self.period_var.get() else "#233763",
                fg="white",
                activebackground="#3478f6",
                activeforeground="white",
                relief="flat",
                bd=0,
                padx=14,
                pady=4,
                cursor="hand2",
                font=("Microsoft YaHei UI", 8),
            )
            control.pack(side="left", padx=(0, 2))
            self.period_buttons[label] = control

    def _build_cards(self) -> None:
        cards = self._top(self)
        cards.grid(row=2, column=0, sticky="ew", padx=16, pady=(0, 9))
        for index in range(4):
            cards.columnconfigure(index, weight=1, uniform="reference-card")
        specs = (
            ("store", ORANGE, ORANGE_ALT, "店铺主订单数", "店铺实收款"),
            ("purchase", PURPLE, PURPLE_ALT, "采购订单", "采购金额"),
            ("profit", RED, RED_ALT, "采购订单利润", ""),
            ("margin", GREEN, GREEN_ALT, "采购订单利润率", ""),
        )
        for column, (key, color, alt, primary, secondary) in enumerate(specs):
            card = tk.Frame(cards, bg=color, padx=18, pady=12)
            card.grid(
                row=0,
                column=column,
                sticky="nsew",
                padx=(0 if column == 0 else 7, 0),
            )
            card.columnconfigure(0, weight=1)
            tk.Label(
                card,
                text=primary,
                bg=color,
                fg="white",
                font=("Microsoft YaHei UI", 10, "bold"),
            ).grid(row=0, column=0, sticky="w")
            primary_value = tk.Label(
                card,
                text="0",
                bg=color,
                fg="white",
                font=("Microsoft YaHei UI", 22, "bold"),
            )
            primary_value.grid(row=1, column=0, sticky="w", pady=(7, 0))
            self._card_values[f"{key}_primary"] = primary_value
            if secondary:
                tk.Label(
                    card,
                    text=secondary,
                    bg=alt,
                    fg="white",
                    font=("Microsoft YaHei UI", 9, "bold"),
                    padx=7,
                    pady=3,
                ).grid(row=2, column=0, sticky="w", pady=(8, 2))
                secondary_value = tk.Label(
                    card,
                    text="0",
                    bg=alt,
                    fg="white",
                    font=("Microsoft YaHei UI", 17, "bold"),
                    padx=7,
                    pady=3,
                )
                secondary_value.grid(row=3, column=0, sticky="w")
                self._card_values[f"{key}_secondary"] = secondary_value

    def _panel(self, parent: tk.Widget, title: str) -> tuple[tk.Frame, tk.Frame]:
        panel = tk.Frame(
            parent,
            bg=PANEL,
            highlightbackground="#2b4275",
            highlightthickness=1,
        )
        heading = tk.Frame(panel, bg=PANEL)
        heading.pack(fill="x", padx=10, pady=(8, 4))
        tk.Label(
            heading,
            text="●",
            bg=PANEL,
            fg=BLUE,
            font=("Segoe UI Symbol", 10),
        ).pack(side="left")
        tk.Label(
            heading,
            text=title,
            bg=PANEL,
            fg=TEXT,
            font=("Microsoft YaHei UI", 11, "bold"),
        ).pack(side="left", padx=(4, 0))
        body = tk.Frame(panel, bg=PANEL)
        body.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        return panel, body

    def _build_middle(self) -> None:
        middle = self._top(self)
        middle.grid(row=3, column=0, sticky="nsew", padx=16, pady=(0, 8))
        middle.columnconfigure(0, weight=55, uniform="middle")
        middle.columnconfigure(1, weight=45, uniform="middle")
        middle.rowconfigure(0, weight=1)

        chart_panel, chart_body = self._panel(middle, "总交易实时概况")
        chart_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 7))
        legend = self._top(chart_body)
        legend.pack(fill="x")
        tk.Label(
            legend,
            text="━━ 今日支付子订单数",
            bg=PANEL,
            fg="#ffd84f",
            font=("Microsoft YaHei UI", 8),
        ).pack(side="left")
        tk.Label(
            legend,
            text="━━ 昨日支付子订单数",
            bg=PANEL,
            fg="#54c8ff",
            font=("Microsoft YaHei UI", 8),
        ).pack(side="left", padx=12)
        self.chart = tk.Canvas(
            chart_body,
            bg=PANEL,
            highlightthickness=0,
            height=260,
        )
        self.chart.pack(fill="both", expand=True)
        self.chart.bind("<Configure>", lambda _event: self._draw_chart())

        board_panel, board_body = self._panel(middle, "总订单看板")
        board_panel.grid(row=0, column=1, sticky="nsew")
        board_body.columnconfigure(0, weight=1)
        board_body.columnconfigure(1, weight=1)
        board_body.columnconfigure(2, weight=1)
        for index, label in enumerate(
            ("待采购", "待发货", "已发货", "退款中", "交易成功", "异常订单")
        ):
            tile = tk.Frame(board_body, bg=PANEL_ALT, padx=9, pady=8)
            tile.grid(
                row=index // 3,
                column=index % 3,
                sticky="nsew",
                padx=4,
                pady=4,
            )
            tk.Label(
                tile,
                text=label,
                bg=PANEL_ALT,
                fg=MUTED,
                font=("Microsoft YaHei UI", 8),
            ).pack(anchor="w")
            value = tk.Label(
                tile,
                text="0",
                bg=PANEL_ALT,
                fg=TEXT,
                font=("Microsoft YaHei UI", 18, "bold"),
            )
            value.pack(anchor="w", pady=(5, 0))
            self._status_labels[label] = value
        self.board_empty = tk.Label(
            board_body,
            text="暂无数据",
            bg=PANEL,
            fg="#d9e8ff",
            font=("Microsoft YaHei UI", 10),
        )
        self.board_empty.grid(row=2, column=0, columnspan=3, pady=(12, 4))

    def _build_rankings(self) -> None:
        bottom = self._top(self)
        bottom.grid(row=4, column=0, sticky="nsew", padx=16, pady=(0, 12))
        bottom.rowconfigure(0, weight=1)
        for index in range(3):
            bottom.columnconfigure(index, weight=1, uniform="rank")
        definitions = (
            (
                "店铺交易排行榜",
                (("label", "店铺旺旺", 150), ("amount", "支付金额", 110),
                 ("orders", "支付子订单数", 100), ("procured", "已采购子订单数", 120)),
                "store",
            ),
            (
                "采购下单排行榜",
                (("label", "下单账号", 170), ("orders", "已下单子订单数", 130)),
                "purchase",
            ),
            (
                "店铺下单利润榜",
                (("label", "店铺旺旺", 150), ("orders", "下单订单数", 110),
                 ("profit", "下单利润", 120)),
                "profit",
            ),
        )
        self.rank_trees: dict[str, ttk.Treeview] = {}
        self.rank_more_buttons: dict[str, tk.Button] = {}
        for column, (title, columns, key) in enumerate(definitions):
            panel, body = self._panel(bottom, title)
            panel.grid(
                row=0,
                column=column,
                sticky="nsew",
                padx=(0 if column == 0 else 6, 0),
            )
            heading = panel.winfo_children()[0]
            more_button = tk.Button(
                heading,
                text="更多数据",
                command=lambda route=key: self._show_more(route),
                bg=PANEL,
                fg="#ffd66b",
                activebackground=PANEL,
                activeforeground="white",
                relief="flat",
                bd=0,
                cursor="hand2",
                font=("Microsoft YaHei UI", 8),
            )
            more_button.pack(side="right")
            self.rank_more_buttons[key] = more_button
            tree = ttk.Treeview(
                body,
                columns=tuple(item[0] for item in columns),
                show="headings",
                height=6,
                style="Reference.Treeview",
            )
            for column_key, text, width in columns:
                tree.heading(column_key, text=text)
                tree.column(column_key, width=width, anchor="center")
            tree.pack(fill="both", expand=True)
            self.rank_trees[key] = tree
        self.tree = self.rank_trees["store"]

    def _change_period(self, period: str) -> None:
        self.period_var.set(period)
        for label, control in self.period_buttons.items():
            control.configure(
                bg="#245be6" if label == period else "#233763"
            )
        self.refresh()

    def _change_source(self) -> None:
        self.window.db.set_setting(
            "big_screen_source",
            self.data_source_var.get(),
        )
        self.refresh()

    def _toggle_privacy(self) -> None:
        self.window.db.set_setting(
            "big_screen_privacy",
            "0" if self.privacy_var.get() else "1",
        )
        self.privacy_var.set(not self.privacy_var.get())
        self.privacy_button.configure(
            text="显示信息" if self.privacy_var.get() else "隐藏信息"
        )
        self.refresh()

    def refresh(self) -> None:
        self.snapshot = build_dashboard_snapshot(
            self.db,
            self.period_var.get(),
            self.data_source_var.get(),
        )
        self._render_snapshot()

    def _render_snapshot(self) -> None:
        hidden = self.privacy_var.get()
        snap = self.snapshot
        self._card_values["store_primary"].configure(
            text=_number(snap.store_orders, hidden=hidden)
        )
        self._card_values["store_secondary"].configure(
            text=_money(snap.store_receipt, hidden=hidden)
        )
        self._card_values["purchase_primary"].configure(
            text=_number(snap.procurement_orders, hidden=hidden)
        )
        self._card_values["purchase_secondary"].configure(
            text=_money(snap.procurement_amount, hidden=hidden)
        )
        self._card_values["profit_primary"].configure(
            text=_money(snap.procurement_profit, hidden=hidden)
        )
        self._card_values["margin_primary"].configure(
            text="******" if hidden else f"{snap.procurement_margin:.2f}%"
        )
        total_orders = sum(snap.status_counts.values())
        for label, value in self._status_labels.items():
            value.configure(
                text=_number(snap.status_counts.get(label, 0), hidden=hidden)
            )
        if total_orders:
            self.board_empty.grid_remove()
        else:
            self.board_empty.grid()
        self._render_rank("store", snap.store_rank, hidden)
        self._render_rank("purchase", snap.procurement_rank, hidden)
        self._render_rank("profit", snap.profit_rank, hidden)
        self._draw_chart()
        self.updated_var.set(
            f"数据更新于 {self._now_text()} · {self.period_var.get()} · "
            f"{self.data_source_var.get()}"
        )
        self.window.set_status(
            f"数据大屏已刷新：{self.period_var.get()} · {self.data_source_var.get()}"
        )

    @staticmethod
    def _now_text() -> str:
        from datetime import datetime

        return datetime.now().strftime("%m/%d %H:%M")

    def _render_rank(
        self,
        key: str,
        rows: list[DashboardRankRow],
        hidden: bool,
    ) -> None:
        tree = self.rank_trees[key]
        tree.delete(*tree.get_children())
        for row in rows:
            if hidden:
                prefix = "采购账号" if key == "purchase" else "店铺"
                values = [f"{prefix} ******"]
            else:
                values = [row.label]
            if key == "store":
                values.extend(
                    (
                        _money(int(row.values[0]), hidden=hidden),
                        _number(int(row.values[1]), hidden=hidden),
                        _number(int(row.values[2]), hidden=hidden),
                    )
                )
            elif key == "profit":
                values.extend(
                    (
                        _number(int(row.values[0]), hidden=hidden),
                        _money(int(row.values[1]), hidden=hidden),
                    )
                )
            else:
                values.append(_number(int(row.values[0]), hidden=hidden))
            tree.insert("", "end", values=values)
        if rows:
            if not tree.winfo_manager():
                tree.pack(fill="both", expand=True)
            more_button = getattr(self, "rank_more_buttons", {}).get(key)
            if more_button is not None and not more_button.winfo_manager():
                more_button.pack(side="right")
        else:
            tree.pack_forget()
            more_button = getattr(self, "rank_more_buttons", {}).get(key)
            if more_button is not None:
                more_button.pack_forget()

    def _draw_chart(self) -> None:
        if not hasattr(self, "chart"):
            return
        canvas = self.chart
        canvas.delete("all")
        width = max(canvas.winfo_width(), 420)
        height = max(canvas.winfo_height(), 220)
        left, top, right, bottom = 35, 12, width - 12, height - 28
        for index in range(6):
            ratio = index / 5
            y = top + (bottom - top) * ratio
            canvas.create_line(left, y, right, y, fill=GRID, dash=(2, 5))
        for index in range(0, 25, 4):
            x = left + (right - left) * index / 24
            canvas.create_line(x, top, x, bottom, fill="#273e72")
            canvas.create_text(
                x,
                bottom + 12,
                text=f"{index:02d}:00",
                fill="#d4e4ff",
                font=("Microsoft YaHei UI", 7),
            )
        max_value = max(
            1,
            max(self.snapshot.today_hourly, default=0),
            max(self.snapshot.yesterday_hourly, default=0),
        )
        self._draw_series(
            canvas,
            self.snapshot.yesterday_hourly,
            max_value,
            left,
            top,
            right,
            bottom,
            "#54c8ff",
        )
        self._draw_series(
            canvas,
            self.snapshot.today_hourly,
            max_value,
            left,
            top,
            right,
            bottom,
            "#ffd84f",
        )
        if max_value == 1 and not any(self.snapshot.today_hourly) and not any(
            self.snapshot.yesterday_hourly
        ):
            canvas.create_text(
                (left + right) / 2,
                (top + bottom) / 2,
                text="暂无数据",
                fill="#d9e8ff",
                font=("Microsoft YaHei UI", 10),
            )

    def _draw_series(
        self,
        canvas: tk.Canvas,
        values: list[int],
        max_value: int,
        left: float,
        top: float,
        right: float,
        bottom: float,
        color: str,
    ) -> None:
        if not values:
            return
        points: list[float] = []
        for hour, value in enumerate(values):
            x = left + (right - left) * hour / 23
            y = bottom - (bottom - top) * value / max_value
            points.extend((x, y))
        if len(points) >= 4:
            canvas.create_line(*points, fill=color, width=2, smooth=True)

    def _show_more(self, key: str) -> None:
        dialog = tk.Toplevel(self)
        dialog.title(
            {
                "store": "店铺交易明细",
                "purchase": "采购下单明细",
                "profit": "店铺下单利润明细",
            }.get(key, "更多数据")
        )
        dialog.geometry("920x560")
        dialog.transient(self.winfo_toplevel())
        tree = self.rank_trees[key]
        columns = tree["columns"]
        detail = ttk.Treeview(
            dialog,
            columns=columns,
            show="headings",
            style="Reference.Treeview",
        )
        for column in columns:
            detail.heading(column, text=tree.heading(column)["text"])
            detail.column(column, width=180, anchor="center")
        for item in tree.get_children():
            detail.insert("", "end", values=tree.item(item, "values"))
        detail.pack(fill="both", expand=True, padx=12, pady=12)

    def _sync(self) -> None:
        stores = self.db.list_stores()
        if not stores:
            self.refresh()
            return
        self.window.set_status("正在刷新大屏经营数据…", busy=True)

        def work(progress) -> list[str]:
            errors: list[str] = []
            for index, store in enumerate(stores, 1):
                try:
                    progress(
                        f"正在同步 {store.name} 的经营数据",
                        index - 1,
                        len(stores),
                    )
                    self.window.service.refresh_store_metrics(store)
                except Exception as exc:
                    errors.append(f"{store.name}: {exc}")
            return errors

        def done(errors: list[str]) -> None:
            self.refresh()
            if errors:
                self.window.set_status(
                    f"大屏刷新完成，{len(errors)} 家店铺同步失败",
                    busy=False,
                )
            else:
                self.window.set_status("大屏数据刷新完成", busy=False)

        self.window.run_task(
            work,
            done,
            lambda error: self.window.set_status(
                f"大屏刷新失败：{error}",
                busy=False,
            ),
            lambda message, current, total: self.window.set_status(
                f"{message} {current}/{total}",
                busy=True,
            ),
            log_title="刷新数据大屏",
        )
