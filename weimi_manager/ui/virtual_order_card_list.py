from __future__ import annotations

import tkinter as tk
import tkinter.font as tkfont
from bisect import bisect_left, bisect_right
from tkinter import ttk
from typing import Any, Callable

from .image_viewer import (
    open_image_viewer,
    photo_from_bytes,
    request_image_bytes,
    valid_remote_url,
)

_CARD_HEIGHT = 184
_CARD_GAP = 6
_CARD_OVERSCAN = 4
_FONT = "Microsoft YaHei UI"
ORDER_COLUMN_BOUNDARIES = (0.0, 0.30, 0.38, 0.57, 0.74, 0.87, 1.0)


class OrderCardList(tk.Frame):
    """High-performance virtual canvas list with lazy order-card rendering."""

    def __init__(
        self,
        parent: tk.Widget,
        on_action: Callable[[str, dict[str, Any]], None],
    ) -> None:
        super().__init__(parent)
        self.on_action = on_action
        self._rows: dict[str, dict[str, Any]] = {}
        self._selected: set[str] = set()
        self._double_click_callback: Callable[[Any], None] | None = None
        self._right_click_callback: Callable[[Any], None] | None = None
        self._rendered: dict[str, list[int]] = {}
        self._item_cards: dict[int, str] = {}
        self._item_actions: dict[int, tuple[str, str]] = {}
        self._action_buttons: dict[int, tuple[str, str, int, str]] = {}
        self._image_urls: dict[int, str] = {}
        self._image_photos: dict[int, Any] = {}
        self._hovered_button: int | None = None
        self._hovered_image: str = ""
        self._hover_after_id: str | None = None
        self._hover_popup: tk.Toplevel | None = None
        self._render_job: str | None = None
        self._scroll_job: str | None = None
        self._pending_scroll_args: tuple[str, ...] | None = None
        self._pending_wheel_steps = 0
        self._rendered_range: tuple[int, int] | None = None
        self._render_width: int | None = None
        self._action_labels: set[str] = set()
        self._font_cache: dict[tuple[int, bool], tkfont.Font] = {}
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        self.canvas = tk.Canvas(
            self,
            bg="#ffffff",
            highlightthickness=0,
            yscrollincrement=54,
        )
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.scrollbar = ttk.Scrollbar(
            self,
            orient="vertical",
            command=self._on_scrollbar,
        )
        self.scrollbar.grid(row=0, column=1, sticky="ns")
        self.canvas.configure(yscrollcommand=self.scrollbar.set)

        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.canvas.bind("<MouseWheel>", self._on_mousewheel)
        self.canvas.bind("<Button-4>", self._on_mousewheel)
        self.canvas.bind("<Button-5>", self._on_mousewheel)
        self.canvas.bind("<Button-1>", self._on_left_click)
        self.canvas.bind("<Double-1>", self._on_double_click)
        self.canvas.bind("<Button-3>", self._on_right_click)
        self.canvas.bind("<Motion>", self._on_motion)
        self.canvas.bind("<Leave>", self._on_leave)

    def _on_canvas_configure(self, _event=None) -> None:
        self._schedule_render(force=True)

    def _on_mousewheel(self, event) -> str:
        if getattr(event, "num", None) == 4:
            steps = -1
        elif getattr(event, "num", None) == 5:
            steps = 1
        else:
            delta = int(getattr(event, "delta", 0) or 0)
            if delta == 0:
                return "break"
            steps = -max(1, abs(delta) // 120) if delta > 0 else max(
                1,
                abs(delta) // 120,
            )
        first, last = self.canvas.yview()
        if (steps < 0 and first <= 0.0) or (steps > 0 and last >= 1.0):
            return "break"
        self._queue_scroll(wheel_steps=steps)
        return "break"

    def _on_scrollbar(self, *args: str) -> None:
        self._queue_scroll(args=args)

    def _queue_scroll(
        self,
        *,
        args: tuple[str, ...] | None = None,
        wheel_steps: int = 0,
    ) -> None:
        if args:
            self._pending_scroll_args = tuple(args)
        self._pending_wheel_steps += wheel_steps
        if self._scroll_job is None:
            self._scroll_job = self.after(8, self._flush_scroll)

    def _flush_scroll(self) -> None:
        if self._scroll_job is not None:
            try:
                self.after_cancel(self._scroll_job)
            except tk.TclError:
                pass
        self._scroll_job = None
        if not self.winfo_exists():
            return
        args = self._pending_scroll_args
        wheel_steps = self._pending_wheel_steps
        self._pending_scroll_args = None
        self._pending_wheel_steps = 0
        if args:
            self.canvas.yview(*args)
        if wheel_steps:
            self.canvas.yview_scroll(wheel_steps, "units")
        self._schedule_render_if_needed()

    def _schedule_render_if_needed(self) -> None:
        if not self._rows:
            return
        core_start, core_end = self._core_range()
        rendered = self._rendered_range
        if (
            rendered is not None
            and rendered[0] <= core_start
            and rendered[1] >= core_end
        ):
            return
        self._schedule_render()

    def _schedule_render(self, *, force: bool = False) -> None:
        if force and self._render_job is not None:
            try:
                self.after_cancel(self._render_job)
            except tk.TclError:
                pass
            self._render_job = None
        if self._render_job is None:
            self._render_job = self.after_idle(self._render_visible)

    def _row_offsets(self) -> list[int]:
        step = _CARD_HEIGHT + _CARD_GAP
        return [index * step for index in range(len(self._rows) + 1)]

    def _core_range(self) -> tuple[int, int]:
        if not self._rows:
            return 0, 0
        offsets = self._row_offsets()
        viewport_height = max(1, self.canvas.winfo_height())
        top = max(0.0, float(self.canvas.canvasy(0)))
        bottom = top + viewport_height
        start = max(0, bisect_right(offsets, top) - 1)
        end = min(len(self._rows), bisect_left(offsets, bottom))
        return start, max(start, end)

    def _visible_range(self) -> tuple[int, int]:
        start, end = self._core_range()
        return (
            max(0, start - _CARD_OVERSCAN),
            min(len(self._rows), end + _CARD_OVERSCAN),
        )

    def _render_visible(self) -> None:
        self._render_job = None
        if not self.winfo_exists():
            return
        self.canvas.delete("empty")
        if not self._rows:
            self._clear_rendered()
            self._rendered_range = None
            self.canvas.create_text(
                24,
                80,
                anchor="nw",
                text="暂无数据",
                fill="#95a1b2",
                font=(_FONT, 11),
                tags=("empty",),
            )
            self.canvas.configure(
                scrollregion=(
                    0,
                    0,
                    max(1, self.canvas.winfo_width()),
                    max(1, self.canvas.winfo_height()),
                )
            )
            return

        start, end = self._visible_range()
        self._rendered_range = (start, end)
        width = max(320, self.canvas.winfo_width())
        if self._render_width is not None and width != self._render_width:
            self._clear_rendered()
            self._rendered_range = (start, end)
        self._render_width = width
        desired = set(tuple(self._rows)[start:end])
        for iid in tuple(self._rendered):
            if iid not in desired:
                self._remove_card(iid)
        for index in range(start, end):
            iid = tuple(self._rows)[index]
            if iid not in self._rendered:
                self._draw_card(iid, self._rows[iid], index, width)
        total_height = max(
            len(self._rows) * (_CARD_HEIGHT + _CARD_GAP),
            self.canvas.winfo_height(),
        )
        self.canvas.configure(scrollregion=(0, 0, width, total_height))

    def _clear_rendered(self) -> None:
        for iid in tuple(self._rendered):
            self._remove_card(iid)

    def _remove_card(self, iid: str) -> None:
        for item in self._rendered.pop(iid, []):
            self.canvas.delete(item)
            self._item_cards.pop(item, None)
            self._item_actions.pop(item, None)
            self._action_buttons.pop(item, None)
            self._image_urls.pop(item, None)
            self._image_photos.pop(item, None)

    def _add_item(self, iid: str, item: int) -> int:
        self._rendered.setdefault(iid, []).append(item)
        self._item_cards[item] = iid
        return item

    def _text(
        self,
        iid: str,
        x: int,
        y: int,
        text: Any,
        *,
        fill: str = "#34445b",
        font: tuple = (_FONT, 8),
        anchor: str = "nw",
        width: int = 0,
        justify: str = "left",
    ) -> int:
        item = self.canvas.create_text(
            x,
            y,
            anchor=anchor,
            text=str(text or ""),
            fill=fill,
            font=font,
            width=width,
            justify=justify,
        )
        return self._add_item(iid, item)

    def _font(self, size: int, *, bold: bool = False) -> tkfont.Font:
        key = (size, bold)
        cached = self._font_cache.get(key)
        if cached is None:
            cached = tkfont.Font(
                root=self,
                family=_FONT,
                size=size,
                weight="bold" if bold else "normal",
            )
            self._font_cache[key] = cached
        return cached

    @staticmethod
    def _ellipsize(text: Any, font: tkfont.Font, max_width: int) -> str:
        value = str(text or "")
        if font.measure(value) <= max_width:
            return value
        suffix = "…"
        low = 0
        high = len(value)
        while low < high:
            middle = (low + high + 1) // 2
            if font.measure(value[:middle] + suffix) <= max_width:
                low = middle
            else:
                high = middle - 1
        return value[:low] + suffix

    @staticmethod
    def _wrap_text(
        text: Any,
        font: tkfont.Font,
        max_width: int,
        max_lines: int,
    ) -> list[str]:
        value = str(text or "")
        if not value:
            return [""]
        lines: list[str] = []
        current = ""
        for character in value:
            candidate = current + character
            if current and font.measure(candidate) > max_width:
                lines.append(current)
                current = character
                if len(lines) == max_lines:
                    break
            else:
                current = candidate
        if len(lines) < max_lines and current:
            lines.append(current)
        if not lines:
            lines = [value]
        consumed = "".join(lines)
        if len(consumed) < len(value):
            lines[-1] = OrderCardList._ellipsize(
                lines[-1] + value[len(consumed) :],
                font,
                max_width,
            )
        return lines[:max_lines]

    def _button(
        self,
        iid: str,
        action: str,
        label: str,
        x: int,
        y: int,
        width: int,
        *,
        height: int = 20,
        fill: str = "#f8fbff",
        active_fill: str = "#e7effa",
        text_fill: str = "#3f70bd",
        font: tuple = (_FONT, 8),
    ) -> None:
        rect = self.canvas.create_rectangle(
            x,
            y,
            x + width,
            y + height,
            fill=fill,
            outline="",
        )
        text = self.canvas.create_text(
            x + width / 2,
            y + height / 2,
            text=label,
            fill=text_fill,
            font=font,
            anchor="center",
        )
        self._add_item(iid, rect)
        self._add_item(iid, text)
        self._item_actions[rect] = (action, iid)
        self._item_actions[text] = (action, iid)
        self._action_buttons[rect] = (fill, active_fill, text, text_fill)
        self._action_buttons[text] = (fill, active_fill, text, text_fill)
        self._action_labels.add(label)

    def _draw_card(
        self,
        iid: str,
        item: dict[str, Any],
        index: int,
        width: int,
    ) -> None:
        y = index * (_CARD_HEIGHT + _CARD_GAP)
        selected = iid in self._selected
        background = "#f7faff" if selected else "#ffffff"
        outline = "#5e8fe8" if selected else "#dce3eb"
        card = self.canvas.create_rectangle(
            1,
            y + 1,
            width - 2,
            y + _CARD_HEIGHT,
            fill=background,
            outline=outline,
            width=2 if selected else 1,
        )
        header = self.canvas.create_rectangle(
            2,
            y + 2,
            width - 3,
            y + 34,
            fill="#f8fafc",
            outline="",
        )
        self._add_item(iid, card)
        self._add_item(iid, header)
        content_width = max(1, width - 16)
        column_edges = [
            8 + int(content_width * fraction)
            for fraction in ORDER_COLUMN_BOUNDARIES
        ]

        order = item["row"]
        checkbox = self.canvas.create_rectangle(
            12,
            y + 11,
            25,
            y + 24,
            fill="#ffffff",
            outline="#7c8da2",
        )
        self._add_item(iid, checkbox)
        self._item_actions[checkbox] = ("toggle_selection", iid)
        if selected:
            check = self.canvas.create_line(
                15,
                y + 18,
                18,
                y + 21,
                23,
                y + 14,
                fill="#2f6fca",
                width=2,
            )
            self._add_item(iid, check)
            self._item_actions[check] = ("toggle_selection", iid)

        order_id = str(order.get("order_id") or "")
        order_font = self._font(9)
        order_text = self._ellipsize(
            f"订单编号：{order_id}",
            order_font,
            max(170, int(width * 0.25)),
        )
        self._text(
            iid,
            32,
            y + 11,
            order_text,
            font=order_font,
        )
        copy_x = min(
            column_edges[1] - 44,
            34 + int(order_font.measure(order_text)),
        )
        self._button(
            iid,
            "copy_order",
            "复制",
            copy_x,
            y + 8,
            36,
            height=20,
            fill="#f8fafc",
            active_fill="#e8eef6",
            text_fill="#3478f6",
            font=(_FONT, 7),
        )

        created_x = max(
            copy_x + 50,
            int(width * 0.34),
        )
        status_x = int(width * 0.49)
        created_width = max(120, status_x - created_x - 12)
        self._text(
            iid,
            created_x,
            y + 11,
            self._ellipsize(
                f"成交时间：{item.get('created_text', '')}",
                self._font(8),
                created_width,
            ),
            fill="#607086",
            font=(_FONT, 8),
        )
        store_x = int(width * 0.68)
        status_width = max(130, store_x - status_x - 16)
        self._text(
            iid,
            status_x,
            y + 11,
            self._ellipsize(
                f"订单状态：{item.get('status_text', '')}",
                self._font(8),
                status_width,
            ),
            fill="#4d5f73",
            font=(_FONT, 8),
        )
        if item.get("gift"):
            gift_x = max(status_x, column_edges[3] - 48)
            self._button(
                iid,
                "menu",
                "礼物单",
                gift_x,
                y + 7,
                42,
                height=15,
                fill="#fff2df",
                active_fill="#ffe8c4",
                text_fill="#a66313",
                font=(_FONT, 7),
            )

        store_text = f"店铺：{item.get('store', '')}"
        deadline_x = int(width * 0.82)
        self.canvas.create_rectangle(
            store_x - 4,
            y + 7,
            min(deadline_x - 10, store_x + 128),
            y + 27,
            fill="#edf8f3",
            outline="",
        )
        self._text(
            iid,
            store_x,
            y + 11,
            self._ellipsize(
                store_text,
                self._font(8, bold=True),
                max(70, deadline_x - store_x - 16),
            ),
            fill="#18705a",
            font=self._font(8, bold=True),
        )
        deadline_width = max(105, width - 128 - deadline_x)
        self._text(
            iid,
            deadline_x,
            y + 11,
            item.get("deadline", ""),
            fill="#de4c62" if item.get("overdue") else "#b87926",
            font=(_FONT, 8),
            width=deadline_width,
        )

        icon_actions = (
            ("↻", "refresh_order"),
            ("◉", "decode"),
            ("☆", "mark"),
            ("⚑", "flag"),
        )
        flag_color = str(item.get("flag_color") or "")
        flag_palette = {
            "红": "#e5484d",
            "黄": "#e8b11b",
            "蓝": "#3478f6",
            "绿": "#22a06b",
            "紫": "#8e4ec6",
            "橙": "#f0761b",
        }
        icon_start = width - 112
        for offset, (label, action) in enumerate(icon_actions):
            color = (
                flag_palette.get(flag_color, "#64748b")
                if action == "flag"
                else "#64748b"
            )
            self._button(
                iid,
                action,
                label,
                icon_start + offset * 23,
                y + 7,
                21,
                height=19,
                fill="#f8fafc",
                active_fill="#e8eef6",
                text_fill=color,
                font=(
                    ("Segoe UI Emoji", 9)
                    if action == "decode"
                    else ("Segoe UI Symbol", 9)
                ),
            )
        self._action_labels.update(
            {
                "订单旗帜",
                "订单菜单",
                "星标订单",
                "解密收货信息",
                "修改收货地址",
                "同步订单",
            }
        )

        body_top = y + 42
        body_bottom = y + _CARD_HEIGHT - 8
        body_center_y = (body_top + body_bottom) // 2
        content_width = max(1, width - 16)
        column_edges = [
            8 + int(content_width * fraction)
            for fraction in ORDER_COLUMN_BOUNDARIES
        ]
        for column_edge in column_edges[1:-1]:
            divisor = self.canvas.create_line(
                column_edge,
                y + 42,
                column_edge,
                y + _CARD_HEIGHT - 8,
                fill="#eef2f6",
            )
            self._add_item(iid, divisor)
        image_url = str(item.get("image_url") or "")
        image_center_x = column_edges[0] + 42
        image_center_y = body_center_y
        image_box = self.canvas.create_rectangle(
            image_center_x - 38,
            image_center_y - 38,
            image_center_x + 38,
            image_center_y + 38,
            fill="#eef2f7",
            outline="#d7e0ea",
        )
        image_placeholder = self.canvas.create_text(
            image_center_x,
            image_center_y,
            text="商品\n图片",
            fill="#9aa8b8",
            font=(_FONT, 8),
            justify="center",
        )
        image_item = self.canvas.create_image(
            image_center_x,
            image_center_y,
            anchor="center",
        )
        for image_canvas_item in (image_box, image_placeholder, image_item):
            self._add_item(iid, image_canvas_item)
        if valid_remote_url(image_url):
            for image_canvas_item in (image_box, image_placeholder, image_item):
                self._image_urls[image_canvas_item] = image_url
                self._item_actions[image_canvas_item] = ("open_image", iid)
            self._request_canvas_image(
                image_url,
                image_item,
                image_placeholder,
            )

        title_x = column_edges[0] + 90
        title_width = max(100, column_edges[1] - title_x - 8)
        title_font = self._font(9)
        detail_font = self._font(8)
        title_lines = self._wrap_text(
            item.get("product_title", ""),
            title_font,
            title_width,
            2,
        )
        detail_lines = [
            self._ellipsize(line, detail_font, title_width)
            for line in item.get("product_lines", [])[:4]
        ]
        title_line_height = int(title_font.metrics("linespace"))
        detail_line_height = int(detail_font.metrics("linespace"))
        detail_gap = 2
        group_height = (
            len(title_lines) * title_line_height
            + 5
            + len(detail_lines) * detail_line_height
            + max(0, len(detail_lines) - 1) * detail_gap
        )
        group_y = max(body_top + 2, body_center_y - group_height // 2)
        title_start_y = group_y
        for title in title_lines:
            title_item = self._text(
                iid,
                title_x,
                group_y,
                title,
                fill="#26364b",
                font=title_font,
            )
            self._item_actions[title_item] = ("open_order", iid)
            group_y += title_line_height
        title_edit_x = min(
            title_x + title_width - 18,
            title_x + title_font.measure(title_lines[0]) + 6,
        )
        self._button(
            iid,
            "edit_product_title",
            "✎",
            int(title_edit_x),
            int(title_start_y),
            17,
            height=17,
            fill="#ffffff",
            active_fill="#eef4fb",
            text_fill="#4b9b72",
            font=("Microsoft YaHei UI", 8),
        )
        group_y += 5
        detail_start_y: list[int] = []
        for line_index, line in enumerate(detail_lines):
            detail_start_y.append(group_y)
            line_item = self._text(
                iid,
                title_x,
                group_y,
                line,
                fill="#7d899a",
                font=detail_font,
            )
            if line_index == 0:
                self._item_actions[line_item] = ("copy_merchant_code", iid)
            elif line_index == 1:
                self._item_actions[line_item] = ("copy_product_id", iid)
            group_y += detail_line_height + detail_gap
        for line_index, action in (
            (0, "copy_merchant_code"),
            (1, "copy_product_id"),
        ):
            if line_index < len(detail_start_y):
                self._button(
                    iid,
                    action,
                    "⧉",
                    column_edges[1] - 25,
                    detail_start_y[line_index],
                    17,
                    height=16,
                    fill="#ffffff",
                    active_fill="#eef4fb",
                    text_fill="#8a97a8",
                    font=("Segoe UI Symbol", 8),
                )
        product_action_y = min(
            body_bottom - 22,
            group_y + 2,
        )
        product_actions = (
            ("◉", "open_image", 18),
            ("⧉", "copy_product_title", 18),
            ("↻", "open_product_link", 18),
            ("✎", "edit_product_title", 18),
        )
        for action_index, (label, action, action_width) in enumerate(
            product_actions
        ):
            self._button(
                iid,
                action,
                label,
                title_x + action_index * 23,
                product_action_y,
                action_width,
                height=18,
                fill="#ffffff",
                active_fill="#eef4fb",
                text_fill="#6d7b8d",
                font=(
                    ("Segoe UI Emoji", 8)
                    if action == "open_image"
                    else ("Segoe UI Symbol", 8)
                ),
            )

        self._text(
            iid,
            (column_edges[1] + column_edges[2]) // 2,
            body_center_y,
            item.get("amount", ""),
            fill="#1f2c3d",
            font=(_FONT, 11),
            anchor="center",
            width=max(80, column_edges[2] - column_edges[1] - 8),
        )

        buyer_x = column_edges[2] + 8
        buyer_width = max(100, column_edges[3] - buyer_x - 8)
        buyer_line_height = int(detail_font.metrics("linespace"))
        buyer_gap = 5
        buyer_height = buyer_line_height * 2 + buyer_gap
        buyer_y = body_center_y - buyer_height // 2
        self._text(
            iid,
            buyer_x,
            buyer_y,
            item.get("buyer_name", ""),
            fill="#2477c9",
            font=detail_font,
            width=buyer_width,
        )
        self._text(
            iid,
            buyer_x,
            buyer_y + buyer_line_height + buyer_gap,
            item.get("buyer_detail", ""),
            fill="#6f7d8f",
            font=detail_font,
            width=buyer_width,
        )
        self._button(
            iid,
            "copy_address",
            "⧉",
            column_edges[3] - 25,
            buyer_y + buyer_line_height + buyer_gap,
            17,
            height=16,
            fill="#ffffff",
            active_fill="#eef4fb",
            text_fill="#6b87a6",
            font=("Segoe UI Symbol", 8),
        )

        purchase_x = column_edges[3] + 8
        purchase_width = max(110, column_edges[4] - purchase_x - 8)
        purchase_text_height = buyer_line_height * 2 + 10
        purchase_button_height = 3 * 27
        purchase_height = purchase_text_height + purchase_button_height
        purchase_y = body_center_y - purchase_height // 2
        self._text(
            iid,
            purchase_x,
            purchase_y,
            self._truncate(
                item.get("purchase_account", "未匹配采购账号"),
                20,
            ),
            fill="#e97d20",
            font=detail_font,
            width=purchase_width,
        )
        self._text(
            iid,
            purchase_x,
            purchase_y + buyer_line_height + 3,
            self._truncate(item.get("purchase_no", "") or "—", 22),
            fill="#617084",
            font=detail_font,
            width=purchase_width,
        )
        action_width = min(96, max(74, purchase_width // 3))
        action_x = purchase_x + min(12, max(0, purchase_width - action_width))
        purchase_buttons = (
            ("🛒 下单", "purchase"),
            ("极速下单", "fast_purchase"),
            ("补录采购订单", "manual_purchase"),
        )
        for button_index, (label, action) in enumerate(purchase_buttons):
            primary = action == "purchase"
            self._button(
                iid,
                action,
                label,
                action_x,
                purchase_y + purchase_text_height + button_index * 27,
                action_width,
                height=24,
                fill="#3f7de8" if primary else "#f8fbff",
                active_fill="#285fbd" if primary else "#e7effa",
                text_fill="#ffffff" if primary else "#3f70bd",
                font=(_FONT, 8),
            )
            if primary and int(item.get("dispatch_count") or 0) > 0:
                badge_x = action_x + action_width - 5
                badge_y = purchase_y + purchase_text_height + 1
                badge = self.canvas.create_oval(
                    badge_x - 7,
                    badge_y - 7,
                    badge_x + 7,
                    badge_y + 7,
                    fill="#1685e5",
                    outline="#ffffff",
                    width=1,
                )
                self._add_item(iid, badge)
                self._text(
                    iid,
                    badge_x,
                    badge_y,
                    str(int(item.get("dispatch_count") or 0)),
                    fill="#ffffff",
                    font=(_FONT, 6),
                    anchor="center",
                )

        logistics_x = column_edges[4] + 8
        logistics_width = max(90, column_edges[5] - logistics_x - 8)
        logistics_height = buyer_line_height + 16 + 20
        logistics_y = body_center_y - logistics_height // 2
        self._text(
            iid,
            logistics_x,
            logistics_y,
            self._truncate(item.get("logistics", "暂无物流"), 28),
            fill="#617084",
            font=detail_font,
            width=logistics_width,
        )
        self._button(
            iid,
            "ship",
            "手动发货",
            logistics_x,
            logistics_y + buyer_line_height + 16,
            min(82, logistics_width),
            height=20,
        )

        notes_x = column_edges[5] + 8
        notes_width = max(90, column_edges[6] - notes_x)
        notes = list(item.get("notes", []))[:5]
        if flag_color:
            notes.append(("旗帜", flag_color))
        note_line_height = int(self._font(7).metrics("linespace")) + 4
        notes_y = body_center_y - len(notes) * note_line_height // 2
        for note_index, (label, value) in enumerate(notes):
            self._text(
                iid,
                notes_x,
                notes_y + note_index * note_line_height,
                self._truncate(f"{label}：{value or ''}", 26),
                fill="#6f7d8f",
                font=(_FONT, 7 if note_index else 8),
                width=notes_width,
            )

    @staticmethod
    def _truncate(value: Any, limit: int) -> str:
        text = str(value or "")
        return text if len(text) <= limit else f"{text[: max(1, limit - 1)]}…"

    def _item_at(self, x: int, y: int) -> tuple[str, tuple[str, str] | None]:
        for item in reversed(self.canvas.find_overlapping(x, y, x, y)):
            action = self._item_actions.get(item)
            if action is not None:
                return action[1], action
            iid = self._item_cards.get(item)
            if iid is not None:
                return iid, None
        return "", None

    def _on_left_click(self, event) -> None:
        iid, action = self._item_at(event.x, event.y)
        if not iid:
            return
        if action is not None:
            key = action[0]
            if key == "toggle_selection":
                self._toggle_selection(
                    iid,
                    iid not in self._selected,
                )
                return
            if key == "open_image":
                url = self._image_url_at(event.x, event.y)
                if not url:
                    url = str(self._rows.get(iid, {}).get("image_url") or "")
                if url:
                    open_image_viewer(
                        self,
                        url,
                        title="订单商品图片",
                    )
                return
            self._action(key, iid)
            return
        self._selected = {iid}
        self._refresh_selection()
        self._action("select", iid)

    def _on_double_click(self, event) -> None:
        iid, action = self._item_at(event.x, event.y)
        if not iid or action is not None:
            return
        self._selected = {iid}
        self._refresh_selection()
        self._action("select", iid)
        if self._double_click_callback is not None:
            self._double_click_callback(event)

    def _on_right_click(self, event) -> None:
        iid, _action = self._item_at(event.x, event.y)
        if not iid:
            return
        self._selected = {iid}
        self._refresh_selection()
        if self._right_click_callback is not None:
            self._right_click_callback(event)

    def _on_motion(self, event) -> None:
        image_url = self._image_url_at(event.x, event.y)
        if image_url != self._hovered_image:
            self._schedule_hover_preview(image_url)
        if image_url:
            self._restore_hovered_button()
            self.canvas.configure(cursor="hand2")
            return
        _iid, action = self._item_at(event.x, event.y)
        hovered = self._action_item_rect(action)
        if hovered == self._hovered_button:
            return
        self._restore_hovered_button()
        if hovered is not None:
            normal, active, text, text_fill = self._action_buttons[hovered]
            self.canvas.itemconfigure(hovered, fill=active)
            self.canvas.itemconfigure(
                text,
                fill="#ffffff" if text_fill == "#ffffff" else "#24538f",
            )
            self.canvas.configure(cursor="hand2")
            self._hovered_button = hovered
        else:
            self.canvas.configure(cursor="")

    def _image_url_at(self, x: int, y: int) -> str:
        for item in reversed(self.canvas.find_overlapping(x, y, x, y)):
            url = self._image_urls.get(item, "")
            if url:
                return url
        return ""

    def _action_item_rect(
        self,
        action: tuple[str, str] | None,
    ) -> int | None:
        if action is None:
            return None
        for item, value in self._item_actions.items():
            if value == action and item in self._action_buttons:
                return item
        return None

    def _restore_hovered_button(self) -> None:
        if self._hovered_button is None:
            return
        values = self._action_buttons.get(self._hovered_button)
        if values is not None:
            normal, _active, text, text_fill = values
            self.canvas.itemconfigure(self._hovered_button, fill=normal)
            self.canvas.itemconfigure(text, fill=text_fill)
        self._hovered_button = None

    def _on_leave(self, _event=None) -> None:
        self._restore_hovered_button()
        self._hide_hover_preview()
        self.canvas.configure(cursor="")

    def _request_canvas_image(
        self,
        url: str,
        image_item: int,
        placeholder_item: int,
    ) -> None:
        def loaded(data: bytes | None) -> None:
            if not data or not self.winfo_exists():
                return
            if image_item not in self._image_urls:
                return
            try:
                photo = photo_from_bytes(data, (76, 76))
                self.canvas.itemconfigure(image_item, image=photo)
                self.canvas.itemconfigure(placeholder_item, state="hidden")
                self._image_photos[image_item] = photo
            except Exception:
                return

        request_image_bytes(self, url, loaded)

    def _schedule_hover_preview(self, url: str) -> None:
        self._hide_hover_preview()
        self._hovered_image = url
        if not url:
            return
        self._hover_after_id = self.after(
            280,
            lambda current=url: self._show_hover_preview(current),
        )

    def _show_hover_preview(self, url: str) -> None:
        self._hover_after_id = None
        if not self.winfo_exists() or self._hovered_image != url:
            return
        popup = tk.Toplevel(self)
        popup.overrideredirect(True)
        popup.attributes("-topmost", True)
        popup.configure(bg="#111827")
        popup.geometry(
            f"+{self.winfo_pointerx() + 18}+{self.winfo_pointery() + 18}"
        )
        label = tk.Label(
            popup,
            text="正在加载…",
            bg="#111827",
            fg="white",
            width=32,
            height=12,
        )
        label.pack(padx=2, pady=2)
        self._hover_popup = popup

        def loaded(data: bytes | None) -> None:
            if not popup.winfo_exists():
                return
            if not data:
                popup.destroy()
                return
            try:
                photo = photo_from_bytes(data, (300, 300))
            except Exception:
                popup.destroy()
                return
            label.configure(
                image=photo,
                text="",
                width=photo.width(),
                height=photo.height(),
            )
            label._preview_photo = photo  # type: ignore[attr-defined]

        request_image_bytes(popup, url, loaded)

    def _hide_hover_preview(self) -> None:
        if self._hover_after_id is not None:
            try:
                self.after_cancel(self._hover_after_id)
            except tk.TclError:
                pass
        self._hover_after_id = None
        self._hovered_image = ""
        if self._hover_popup is not None:
            try:
                if self._hover_popup.winfo_exists():
                    self._hover_popup.destroy()
            except tk.TclError:
                pass
        self._hover_popup = None

    def get_children(self) -> tuple[str, ...]:
        return tuple(self._rows)

    def rendered_iids(self) -> tuple[str, ...]:
        order = {iid: index for index, iid in enumerate(self._rows)}
        return tuple(
            sorted(
                self._rendered,
                key=lambda iid: order.get(iid, len(order)),
            )
        )

    def action_labels(self) -> frozenset[str]:
        return frozenset(self._action_labels)

    def selection(self) -> tuple[str, ...]:
        return tuple(self._selected)

    def selection_set(self, values) -> None:
        self._selected = {
            str(value) for value in values if str(value) in self._rows
        }
        self._refresh_selection()

    def selection_remove(self, *values) -> None:
        for value in values:
            self._selected.discard(str(value))
        self._refresh_selection()

    def focus_set(self) -> None:
        self.canvas.focus_set()

    def identify_row(self, _y: int) -> str:
        x = self.canvas.winfo_pointerx() - self.canvas.winfo_rootx()
        y = self.canvas.winfo_pointery() - self.canvas.winfo_rooty()
        iid, _action = self._item_at(x, y)
        return iid

    def bind(self, sequence=None, func=None, add=None):  # noqa: A003
        if sequence == "<Double-1>":
            self._double_click_callback = func
            return ""
        if sequence == "<Button-3>":
            self._right_click_callback = func
            return ""
        return super().bind(sequence, func, add)

    def delete(self, *items) -> None:
        if not items:
            self.set_rows([])
            return
        removing = {str(item) for item in items}
        for iid in removing:
            self._rows.pop(iid, None)
            self._selected.discard(iid)
            self._remove_card(iid)
        self._schedule_render(force=True)

    def set_rows(self, rows: list[dict[str, Any]]) -> None:
        self._rows = {str(row["iid"]): row for row in rows}
        self._selected.intersection_update(self._rows)
        self._clear_rendered()
        self._rendered_range = None
        self._render_width = None
        self.canvas.yview_moveto(0)
        self._schedule_render(force=True)

    def _toggle_selection(self, iid: str, selected: bool) -> None:
        if selected:
            self._selected.add(iid)
        else:
            self._selected.discard(iid)
        self._refresh_selection()
        self._action("selection", iid)

    def _refresh_selection(self) -> None:
        self._schedule_render(force=True)

    def _action(self, action: str, iid: str) -> None:
        row = self._rows.get(iid)
        if row is not None:
            self.on_action(action, row)

    def destroy(self) -> None:
        self._hide_hover_preview()
        if self._render_job is not None:
            try:
                self.after_cancel(self._render_job)
            except tk.TclError:
                pass
        if self._scroll_job is not None:
            try:
                self.after_cancel(self._scroll_job)
            except tk.TclError:
                pass
        self._render_job = None
        self._scroll_job = None
        super().destroy()
