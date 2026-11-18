from __future__ import annotations

import tkinter as tk
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from tkinter import messagebox, ttk
from typing import TYPE_CHECKING, Any

from .pages import BasePage
from .theme import button

if TYPE_CHECKING:
    from .app import MainWindow


WECHAT_SHOP_LINK_PREFIXES = (
    "#微信小店://",
    "https://",
)


def parse_mobile_task_links(text: str) -> tuple[list[str], list[tuple[int, str]]]:
    """Return one untouched WeChat-shop link per non-empty input line."""
    links: list[str] = []
    invalid: list[tuple[int, str]] = []
    for line_number, raw_line in enumerate(str(text or "").splitlines(), 1):
        line = raw_line.strip()
        if not line:
            continue
        if not line.startswith(WECHAT_SHOP_LINK_PREFIXES):
            invalid.append(
                (
                    line_number,
                    "每行必须是原始微信小店链接或 HTTPS 链接",
                )
            )
            continue
        links.append(line)
    return links, invalid


def mobile_lease_is_active(value: Any) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    try:
        expires = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return False
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=UTC)
    return expires.astimezone(UTC) > datetime.now(UTC)


class SalesRelatedPage(BasePage):
    title = "动销相关"
    subtitle = "一行一条微信小店链接，按顺序发送到手机执行"

    def __init__(self, parent: tk.Widget, window: "MainWindow") -> None:
        super().__init__(parent, window)
        self.window = window
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)
        self._devices: list[dict[str, Any]] = []
        self._device_by_label: dict[str, dict[str, Any]] = {}
        self._queue_watch_started = False
        self._queue_polling = False
        self._queue_automation_running = False
        self._failed_active_task_id = ""

        intro = ttk.Frame(self, style="Panel.TFrame", padding=(22, 18))
        intro.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        ttk.Label(
            intro,
            text="动销相关",
            font=("Microsoft YaHei UI", 16, "bold"),
        ).pack(anchor="w")
        ttk.Label(
            intro,
            text=(
                "电脑端一行一条下发。手机点“开始逐条执行”后，"
                "澜琅会通过 ADB 自动打开微信搜索、文件传输助手、商品规格并加入购物车。"
                "不点击购买、送朋友或支付入口。"
            ),
            style="Subtitle.TLabel",
            wraplength=980,
            justify="left",
        ).pack(anchor="w", pady=(8, 0))

        cards = ttk.Frame(self, style="TFrame")
        cards.grid(row=1, column=0, sticky="ew")
        for column in range(2):
            cards.columnconfigure(column, weight=1)
        sections = []
        if self.window._can_permission("procurement"):
            sections.extend(
                [
                    ("采购任务", "批量导入商品链接、云端任务队列和设备分配"),
                    ("SKU 与数量规则", "任意、最低价、最高价、指定规格及购买数量"),
                ]
            )
        if self.window._can_permission("after_sales"):
            sections.extend(
                [
                    ("订单核验", "客户端回传仅作为线索，以可信订单数据为准"),
                    ("售后协同", "白名单、模拟审核和退款结果边界"),
                ]
            )
        for index, (title, description) in enumerate(sections):
            card = ttk.Frame(cards, style="Panel.TFrame", padding=(18, 14))
            card.grid(
                row=index // 2,
                column=index % 2,
                sticky="nsew",
                padx=(0, 8) if index % 2 == 0 else (8, 0),
                pady=5,
            )
            ttk.Label(
                card,
                text=title,
                font=("Microsoft YaHei UI", 11, "bold"),
            ).pack(anchor="w")
            ttk.Label(
                card,
                text=description,
                style="Subtitle.TLabel",
                wraplength=440,
                justify="left",
            ).pack(anchor="w", pady=(6, 0))

        mobile = ttk.Frame(self, style="Panel.TFrame", padding=(18, 14))
        mobile.grid(row=2, column=0, sticky="nsew", pady=(12, 0))
        mobile.columnconfigure(0, weight=1)
        mobile.rowconfigure(4, weight=1)
        heading = ttk.Frame(mobile, style="Panel.TFrame")
        heading.grid(row=0, column=0, sticky="ew")
        ttk.Label(
            heading,
            text="手机采购任务",
            font=("Microsoft YaHei UI", 12, "bold"),
        ).pack(side="left")
        button(
            heading,
            "刷新手机设备",
            self._refresh_devices,
            "Primary",
        ).pack(side="right")

        controls = ttk.Frame(mobile, style="Panel.TFrame")
        controls.grid(row=1, column=0, sticky="ew", pady=(10, 8))
        ttk.Label(controls, text="目标设备").pack(side="left")
        self.device_var = tk.StringVar()
        self.device_combo = ttk.Combobox(
            controls,
            textvariable=self.device_var,
            state="readonly",
            width=28,
        )
        self.device_combo.pack(side="left", padx=(5, 14))
        ttk.Label(controls, text="数量").pack(side="left")
        self.quantity_var = tk.StringVar(value="1")
        ttk.Entry(
            controls,
            textvariable=self.quantity_var,
            width=6,
        ).pack(side="left", padx=(5, 14))
        ttk.Label(controls, text="SKU 规则").pack(side="left")
        self.mode_var = tk.StringVar(value="任意可售 SKU")
        ttk.Combobox(
            controls,
            textvariable=self.mode_var,
            values=(
                "任意可售 SKU",
                "最低价可售 SKU",
                "最高价可售 SKU",
                "指定 SKU / 规格",
                "商品默认 SKU",
            ),
            state="readonly",
            width=18,
        ).pack(side="left", padx=5)

        link_row = ttk.Frame(mobile, style="Panel.TFrame")
        link_row.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        link_row.columnconfigure(0, weight=1)
        ttk.Label(
            link_row,
            text="微信小店链接（一行一条，按输入顺序执行）",
            style="Subtitle.TLabel",
        ).grid(row=0, column=0, sticky="w", pady=(0, 5))
        self.links_text = tk.Text(
            link_row,
            height=8,
            wrap="none",
            undo=True,
        )
        self.links_text.grid(row=1, column=0, sticky="nsew", padx=(0, 8))
        button(
            link_row,
            "逐条下发到手机",
            self._push_task,
            "Primary",
        ).grid(row=1, column=1, sticky="n")

        self.mobile_status_var = tk.StringVar(value="尚未读取手机设备")
        ttk.Label(
            mobile,
            textvariable=self.mobile_status_var,
            style="Subtitle.TLabel",
        ).grid(row=3, column=0, sticky="w", pady=(0, 6))

        self.mobile_tree = ttk.Treeview(
            mobile,
            columns=("device", "status", "version", "last_seen"),
            show="headings",
            height=8,
        )
        for key, label, width in (
            ("device", "设备", 260),
            ("status", "状态", 120),
            ("version", "版本", 100),
            ("last_seen", "最后在线", 220),
        ):
            self.mobile_tree.heading(key, text=label)
            self.mobile_tree.column(key, width=width, anchor="w")
        self.mobile_tree.grid(row=4, column=0, sticky="nsew")

        if self._can_use_cloud():
            self.after(100, self._refresh_devices)
            self.after(1500, self._ensure_queue_watch)

    def _can_use_cloud(self) -> bool:
        return bool(
            getattr(self.window, "auth_context", None)
            and getattr(self.window, "auth_client", None)
        )

    def _token(self) -> str:
        context = getattr(self.window, "auth_context", None)
        return str(
            getattr(getattr(context, "tokens", None), "access_token", "") or ""
        )

    def _apply_device_rows(self, rows: list[dict[str, Any]]) -> None:
        self._devices = rows
        labels = []
        self._device_by_label.clear()
        self.mobile_tree.delete(*self.mobile_tree.get_children())
        if not rows:
            self.device_combo.configure(values=[])
            self.device_var.set("")
            self.mobile_tree.insert(
                "",
                "end",
                iid="__no_mobile_devices__",
                values=(
                    "暂无已绑定手机设备",
                    "未绑定",
                    "—",
                    "请先在安卓端使用同一账号登录绑定",
                ),
            )
            self.mobile_status_var.set(
                "刷新完成：未发现已绑定手机设备，"
                "请先在安卓端使用同一账号登录绑定"
            )
            return
        for row in rows:
            device_id = str(row.get("device_id") or "")
            name = str(row.get("display_name") or device_id)
            label = f"{name} · {device_id[:8]}"
            labels.append(label)
            self._device_by_label[label] = row
            self.mobile_tree.insert(
                "",
                "end",
                iid=device_id,
                values=(
                    label,
                    row.get("status") or "",
                    row.get("app_version") or "",
                    row.get("last_seen_at") or "",
                ),
            )
        self.device_combo.configure(values=labels)
        if labels and not self.device_var.get():
            self.device_var.set(labels[0])
        self.mobile_status_var.set(f"已绑定 {len(rows)} 台手机设备")
        self._ensure_queue_watch()

    def _refresh_devices(self) -> None:
        self._failed_active_task_id = ""
        if not self._can_use_cloud():
            self.mobile_status_var.set("当前未登录云端，无法读取手机设备")
            return
        token = self._token()
        if not token:
            self.mobile_status_var.set("当前登录会话没有访问令牌")
            return
        self.mobile_status_var.set("正在读取手机设备…")

        def work(_progress):
            return self.window.auth_client.mobile_sales_list_devices(token)

        def done(rows: list[dict[str, Any]]) -> None:
            self._apply_device_rows(rows)

        self.window.run_task(
            work,
            done,
            lambda error: messagebox.showerror(
                "读取手机设备失败",
                error,
                parent=self,
            ),
            log_title="读取手机设备",
        )

    def _push_task(self) -> None:
        if not self._can_use_cloud():
            messagebox.showwarning("未登录", "请先登录云端账号。", parent=self)
            return
        device = self._device_by_label.get(self.device_var.get())
        if device is None:
            messagebox.showinfo("请选择设备", "请先刷新并选择目标手机。", parent=self)
            return
        links, invalid = parse_mobile_task_links(
            self.links_text.get("1.0", "end")
        )
        if invalid:
            line_number, reason = invalid[0]
            messagebox.showwarning(
                "链接无效",
                f"第 {line_number} 行：{reason}。\n"
                "请一行只放一条原始微信小店链接。",
                parent=self,
            )
            return
        if not links:
            messagebox.showwarning(
                "没有链接",
                "请先粘贴至少一条微信小店链接，一行一条。",
                parent=self,
            )
            return
        try:
            quantity = max(1, int(self.quantity_var.get().strip()))
        except ValueError:
            messagebox.showwarning("数量无效", "购买数量必须是正整数。", parent=self)
            return
        mode_map = {
            "任意可售 SKU": "any",
            "最低价可售 SKU": "lowest_price",
            "最高价可售 SKU": "highest_price",
            "指定 SKU / 规格": "specified",
            "商品默认 SKU": "product_default",
        }
        token = self._token()
        device_id = str(device.get("device_id") or "")
        sku_selection_mode = mode_map.get(self.mode_var.get(), "any")

        def work(_progress):
            succeeded = 0
            failed: list[tuple[int, str, str]] = []
            for index, source_url in enumerate(links, 1):
                try:
                    self.window.auth_client.mobile_sales_owner_enqueue_task(
                        token,
                        device_id=device_id,
                        task_id=uuid.uuid4().hex,
                        source_url=source_url,
                        title=f"第 {index} 条",
                        sku_selection_mode=sku_selection_mode,
                        purchase_quantity=quantity,
                        price_cap_cents=0,
                    )
                    succeeded += 1
                except Exception as error:
                    failed.append((index, source_url, str(error)))
            return succeeded, failed

        def done(result: tuple[int, list[tuple[int, str, str]]]) -> None:
            succeeded, failed = result
            if failed:
                self.links_text.delete("1.0", "end")
                self.links_text.insert(
                    "1.0",
                    "\n".join(item[1] for item in failed),
                )
            else:
                self.links_text.delete("1.0", "end")
            self.mobile_status_var.set(
                f"已按顺序下发 {succeeded} 条"
                + (f"，{len(failed)} 条失败" if failed else "")
            )
            detail = (
                f"已按顺序下发 {succeeded} 条到{device.get('display_name') or device_id}。\n"
                "手机端点击“开始逐条执行”，一次只复制当前链接。"
            )
            if failed:
                lines = "\n".join(
                    f"第 {index} 条：{error}"
                    for index, _url, error in failed[:5]
                )
                detail += f"\n\n失败：\n{lines}"
            (messagebox.showwarning if failed else messagebox.showinfo)(
                "逐条下发结果",
                detail,
                parent=self,
            )
            self._ensure_queue_watch()

        self.window.run_task(
            work,
            done,
            lambda error: messagebox.showerror(
                "下发手机任务失败",
                error,
                parent=self,
            ),
            log_title="逐条下发微信小店任务",
        )

    def _watched_device(self) -> dict[str, Any] | None:
        selected = self._device_by_label.get(self.device_var.get())
        return selected if selected is not None else None

    def _ensure_queue_watch(self) -> None:
        if self._queue_watch_started or not self._can_use_cloud():
            return
        if self._watched_device() is None:
            return
        self._queue_watch_started = True
        self.after(1000, self._poll_mobile_queue)

    def _poll_mobile_queue(self) -> None:
        if not self.winfo_exists():
            return
        device = self._watched_device()
        token = self._token()
        if (
            self._queue_automation_running
            or self._queue_polling
            or device is None
            or not token
        ):
            self.after(2000, self._poll_mobile_queue)
            return

        device_id = str(device.get("device_id") or "")
        self._queue_polling = True

        def fetch() -> None:
            try:
                rows = self.window.auth_client.mobile_sales_owner_list_tasks(
                    token,
                    device_id=device_id,
                )
                self.after(0, self._apply_queue_poll, device_id, rows)
            except Exception as error:
                self.after(0, self._fail_queue_poll, str(error))

        threading.Thread(target=fetch, daemon=True).start()
        self.after(2200, self._poll_mobile_queue)

    def _fail_queue_poll(self, error: str) -> None:
        self._queue_polling = False
        if not self._queue_automation_running:
            self.mobile_status_var.set(f"自动队列状态读取失败：{error}")

    def _apply_queue_poll(
        self,
        device_id: str,
        rows: list[dict[str, Any]],
    ) -> None:
        self._queue_polling = False
        done_statuses = {"cart_added", "completed", "closed", "failed"}
        active = next(
            (
                row for row in rows
                if str(row.get("assigned_device_id") or "") == device_id
                and mobile_lease_is_active(row.get("lease_expires_at"))
                and str(row.get("status") or "") not in done_statuses
            ),
            None,
        )
        if active is None:
            return
        active_task_id = str(active.get("task_id") or "")
        if (
            not active_task_id
            or active_task_id == self._failed_active_task_id
        ):
            return
        queued = [
            row for row in rows
            if str(row.get("assigned_device_id") or "") == device_id
            and str(row.get("status") or "") not in done_statuses
        ]
        links = [
            str(row.get("source_url") or "").strip()
            for row in queued
            if str(row.get("source_url") or "").strip()
        ]
        if not links:
            return
        quantity = max(1, int(queued[0].get("purchase_quantity") or 1))
        self._queue_automation_running = True
        self.mobile_status_var.set(
            f"手机已开始，电脑正在自动执行 {len(links)} 条任务"
        )

        def work(_progress):
            from ..mobile_automation import MobileWechatAutomation

            automation = MobileWechatAutomation(
                progress=lambda message: self.after(
                    0,
                    self.mobile_status_var.set,
                    message,
                ),
                debug_dir=Path("data") / "automation_debug",
            )
            return automation.run_queue(links, quantity=quantity)

        def done(result: dict[str, object]) -> None:
            self._queue_automation_running = False
            self._failed_active_task_id = ""
            self.mobile_status_var.set(
                f"自动执行完成：{result.get('completed', 0)} 条"
            )
            messagebox.showinfo(
                "手机自动执行完成",
                f"已完成 {result.get('completed', 0)} 条加入购物车任务。",
                parent=self,
            )

        def failed(error: str) -> None:
            self._queue_automation_running = False
            self._failed_active_task_id = active_task_id
            self.mobile_status_var.set(f"自动执行已暂停：{error}")
            messagebox.showerror(
                "手机自动执行已暂停",
                error,
                parent=self,
            )

        self.window.run_task(
            work,
            done,
            failed,
            log_title="自动执行手机微信加购",
        )

    def refresh(self) -> None:
        if self._can_use_cloud():
            self._refresh_devices()
            self._ensure_queue_watch()
