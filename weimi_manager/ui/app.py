from __future__ import annotations

import os
import queue
import threading
import time
import tkinter as tk
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Any

from .. import __display_version__, __version__
from ..account_store_sync import AccountStoreSync
from ..auth_client import SupabaseAuthClient
from ..auth_config import AuthConfig
from ..auth_service import AuthContext
from ..auth_session import AuthSessionStore
from ..cloud_commands import (
    AuthorizedServiceAdapter,
    AuthorizedWebSessionAdapter,
    CloudCommandClient,
    set_database_store_sync,
    wrap_database,
)
from ..config import AppPaths
from ..db import Database
from ..diagnostics import DiagnosticManager
from ..dongxiao.application.runtime import DongxiaoRuntime
from ..service import SyncService
from ..store_scope import logged_in_stores
from ..tester_channel import load_tester_channel
from ..updater import (
    DEFAULT_UPDATE_SOURCE,
    check_for_update,
    download_and_stage_update,
    is_development_preview_version,
    launch_update_installer,
)
from ..web_session import WechatWebSession
from .activity_center import ActivityCenterPage
from .business_tools import (
    StoreGroupsPage,
)
from .dongxiao_page import DongxiaoPage
from .hosted_center import HostedCenterPage
from .pages import (
    ExtractPage,
    OrdersPage,
    ParsedOrdersPage,
    ProductsPage,
    SettingsPage,
    StorePage,
)
from .skeletons import (
    AccountPage,
    AnalyticsPage,
    AutomationPage,
    BigScreenWindow,
    CustomerServicePage,
    FinancePage,
    MarketingPage,
    NoTrafficPage,
    OperationsPage,
    ProcurementPage,
    SalesSuggestionsPage,
    StorefrontPage,
    WorkbenchPage,
)
from .task_log_dialog import TaskLogDialog
from .team_page import TeamPage
from .theme import (
    BG,
    BLUE,
    MUTED,
    NAVY,
    NAVY_ALT,
    PANEL,
    SOFT_TEAL,
    TEXT,
    apply_theme,
)
from .upload_page import UploadPage
from .v2.diagnostics_page import DiagnosticsPage
from .violation_page import ViolationLibraryPage


class MainWindow(tk.Tk):
    _PAGE_PERMISSIONS = {
        "workbench": "stores_view",
        "diagnostics": "team_manage",
        "orders": "orders_view",
        "order_management": "orders_view",
        "parsed": "orders_view",
        "products": "products_view",
        "upload": "products_upload",
        "stores": "stores_view",
        "store_groups": "stores_view",
        "customer_service": "after_sales",
        "operations": "after_sales",
        "dynamic": "products_view",
        "no_traffic": "products_edit",
        "violation_blacklist": "products_view",
        "hosted": "products_edit",
        "procurement": "procurement",
        "marketing": "activities",
        "double11": "activities",
        "storefront": "stores_edit",
        "dashboard": "orders_view",
        "analytics": "orders_view",
        "finance": "orders_view",
        "automation": "products_edit",
        "settings": "team_manage",
        "account": "",
        "team": "team_manage",
        # 新模块显示给具备采购或售后权限的账号；页面内部再按功能细分权限。
        "sales_related": "",
    }
    _PAGE_ATTRIBUTES = {
        "workbench_page": "workbench",
        "diagnostics_page": "diagnostics",
        "store_page": "stores",
        "orders_page": "orders",
        "products_page": "products",
        "upload_page": "upload",
        "operations_page": "operations",
        "customer_service_page": "customer_service",
        "marketing_page": "marketing",
        "storefront_page": "storefront",
        "finance_page": "finance",
        "procurement_page": "procurement",
        "analytics_page": "analytics",
        "automation_page": "automation",
        "settings_page": "settings",
        "account_page": "account",
        "parsed_page": "parsed",
        "order_management_page": "order_management",
        "store_groups_page": "store_groups",
        "activity_center_page": "double11",
        "double11_page": "double11",
        "violation_page": "violation_blacklist",
        "dynamic_page": "dynamic",
        "no_traffic_page": "no_traffic",
        "hosted_page": "hosted",
        "team_page": "team",
        "sales_related_page": "sales_related",
    }

    def __getattr__(self, name: str):
        route = type(self)._PAGE_ATTRIBUTES.get(name)
        if route is not None:
            return self._ensure_page(route)
        raise AttributeError(name)

    def _can_sales_related(self) -> bool:
        """采购或售后权限任一满足即可显示入口；页面内部再做细粒度控制。"""
        return self._can_permission("procurement") or self._can_permission("after_sales")

    def _can_permission(self, permission: str) -> bool:
        context = getattr(self, "auth_context", None)
        if context is None:
            return True
        if permission == "team_manage":
            return getattr(context, "member_role", "") in {"owner", "admin"}
        if getattr(context, "member_role", "owner") in {"owner", "admin"}:
            return True
        permissions = getattr(context, "permissions", {})
        return bool(
            permissions.get(permission)
            if isinstance(permissions, dict)
            else False
        )

    def __init__(
        self,
        paths: AppPaths,
        auth_context: AuthContext | None = None,
        auth_client: SupabaseAuthClient | None = None,
    ):
        super().__init__()
        self.paths = paths
        self.auth_context = auth_context
        self.auth_client = auth_client
        self.cloud_commands = (
            CloudCommandClient(
                auth_client,
                auth_context.tokens.access_token,
            )
            if auth_client is not None
            and auth_context is not None
            and auth_context.tokens.access_token
            else None
        )
        self.saved_account_store: AuthSessionStore | None = None
        self.return_to_login = False
        self.base_db = Database(paths.database)
        self.db = wrap_database(self.base_db, self.cloud_commands)
        auth_config = AuthConfig.load(paths)
        tester_channel = load_tester_channel(paths.resource_dir)
        self.diagnostics = DiagnosticManager(
            paths.data_dir,
            __version__,
            account_id=(
                auth_context.tokens.account_id
                if auth_context is not None
                else ""
            ),
            username=(
                auth_context.username if auth_context is not None else ""
            ),
            device_hash=(
                auth_context.device_hash
                if auth_context is not None
                else ""
            ),
            upload_url=str(
                tester_channel.get("diagnostic_upload_url")
                or (
                    auth_config.function_url("submit-diagnostic-report")
                    if auth_config.configured
                    else ""
                )
            ),
            access_token=(
                auth_context.tokens.access_token
                if auth_context is not None
                else ""
            ),
            access_token_provider=(
                lambda: str(auth_client.current_access_token or "")
                if auth_client is not None
                else ""
            ),
            access_token_refresher=(
                lambda failed: auth_client.refresh_current_session(
                    failed
                ).access_token
                if auth_client is not None
                else ""
            ),
            tester_key=str(
                tester_channel.get("diagnostic_upload_key") or ""
            ),
            crash_log_path=self._global_crash_log_path(paths),
        )
        self.diagnostics.context_provider = self._diagnostic_context
        self.diagnostics.install_exception_hooks(self)
        try:
            self.db.recover_interrupted_task_logs()
        except Exception:
            pass
        pull_started = time.monotonic()
        try:
            pull_log_id = self.db.start_task_log(
                scope="account_sync",
                operation="恢复账户店铺和登录状态",
                message="开始读取账户加密店铺保险箱",
            )
        except Exception:
            pull_log_id = None
        self.account_store_sync: AccountStoreSync | None = None
        self._account_sync_job: str | None = None
        self._account_sync_running = False
        self._account_sync_notice = ""
        self._closing = False
        if (
            auth_context is not None
            and auth_client is not None
            and auth_context.vault_key
        ):
            self.account_store_sync = AccountStoreSync(
                db=self.base_db,
                auth_client=auth_client,
                tokens=auth_context.tokens,
                vault_key=auth_context.vault_key,
                organization_id=auth_context.organization_id,
                member_role=auth_context.member_role,
                member_permissions=dict(auth_context.permissions),
            )
            set_database_store_sync(
                self.db,
                self.account_store_sync,
            )
            self._account_sync_notice = (
                "云端店铺将在点击“刷新数据”时联网同步"
            )
            self._finish_task_log(
                pull_log_id,
                pull_started,
                "success",
                self._account_sync_notice,
            )
        else:
            self._finish_task_log(
                pull_log_id,
                pull_started,
                "success",
                "当前登录账户没有可恢复的店铺保险箱",
            )
        if auth_context is not None:
            auth_started = time.monotonic()
            try:
                auth_log_id = self.db.start_task_log(
                    scope="account",
                    operation="账户授权联网校验",
                    message="软件启动时核对账户授权状态",
                )
            except Exception:
                auth_log_id = None
            license_info = auth_context.license
            self._finish_task_log(
                auth_log_id,
                auth_started,
                "partial" if auth_context.offline else "success",
                (
                    "网络不可用，使用最近一次联网验证后的离线宽限"
                    if auth_context.offline
                    else "账户授权联网校验完成"
                ),
                (
                    f"授权状态：{license_info.status_text or '未知'}\n"
                    f"剩余天数：{license_info.remaining_days}\n"
                    f"离线宽限：{'是' if auth_context.offline else '否'}"
                ),
            )
        base_service = SyncService(self.db)
        base_web_session = WechatWebSession(self.db, paths.data_dir)
        if self.cloud_commands is not None:
            self.service = AuthorizedServiceAdapter(
                base_service,
                self.cloud_commands,
            )
            self.web_session = AuthorizedWebSessionAdapter(
                base_web_session,
                self.cloud_commands,
            )
        else:
            self.service = base_service
            self.web_session = base_web_session
        self.dongxiao_runtime = None
        self.dongxiao_runtime_error = ""
        try:
            self.dongxiao_runtime = DongxiaoRuntime(
                self.base_db.path,
                self.service,
            )
        except Exception as exc:
            self.dongxiao_runtime_error = str(exc)
            self.diagnostics.capture_exception(
                exc,
                category="dongxiao_runtime",
                detail={"operation": "initialize"},
            )
        self._task_queue: queue.Queue[tuple[Callable[..., Any], tuple[Any, ...]]] = queue.Queue()
        self._nav_buttons: dict[str, tk.Button] = {}
        self._page_icon_names: dict[str, str] = {}
        self._nav_icon_maps: dict[str, tuple[tk.PhotoImage, tk.PhotoImage]] = {}
        self._pages: dict[str, ttk.Frame] = {}
        self._active_page = ""
        self._task_running = False
        self._running_tasks: dict[str, set[object]] = {}
        self._update_check_running = False
        self._auto_refresh_running = False
        self._violation_daily_running = False
        self._auto_tick_count = 0
        try:
            self._auto_refresh_batch_size = int(
                self.db.get_setting("auto_refresh_batch_size", "50")
            )
        except ValueError:
            self._auto_refresh_batch_size = 50
        self._auto_refresh_batch_size = min(
            500, max(5, self._auto_refresh_batch_size)
        )
        try:
            self._auto_refresh_workers = int(
                self.db.get_setting("auto_refresh_workers", "5")
            )
        except ValueError:
            self._auto_refresh_workers = 5
        self._auto_refresh_workers = min(
            12, max(1, self._auto_refresh_workers)
        )
        self._big_screen_window: BigScreenWindow | None = None
        self._status_busy = False
        self.auto_refresh_enabled = self.db.get_setting("auto_refresh", "1") == "1"

        apply_theme(self)
        self.title(f"澜琅 · 微信小店版 {__display_version__}")
        self.geometry("1400x850")
        self.minsize(1180, 720)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._load_brand_assets()

        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)
        self._build_sidebar()
        self._build_content()
        self.after(80, self._drain_task_queue)
        self.after(500, self._start_dongxiao_runtime)
        self.after(1000, self._diagnostic_heartbeat)
        self.after(5 * 60 * 1000, self._diagnostic_upload_tick)
        self.diagnostics.flush_async()
        self.after(60000, self._auto_refresh_tick)
        self.after(90 * 1000, self._violation_daily_tick)
        self._center_window()
        self._refresh_nav_alerts()
        start_page = os.environ.get(
            "LANGLANG_START_PAGE",
            os.environ.get("WEIMI_START_PAGE", "stores"),
        )
        if (
            self._PAGE_PERMISSIONS.get(start_page)
            and not self._can_permission(
                self._PAGE_PERMISSIONS[start_page]
            )
        ):
            start_page = next(
                (
                    key
                    for key in (
                        "products",
                        "orders",
                        "upload",
                        "procurement",
                        "account",
                    )
                    if key in self._PAGE_PERMISSIONS
                    and (
                        not self._PAGE_PERMISSIONS.get(key)
                        or self._can_permission(
                            self._PAGE_PERMISSIONS[key]
                        )
                    )
                ),
                "account",
            )
        self.show_page(
            start_page
            if start_page in self._PAGE_PERMISSIONS
            and start_page != "dashboard"
            else "stores"
        )
        self.after(500, self._ensure_store_page_loaded)
        self._start_automatic_backup()
        self.after(5000, self._auto_check_updates)
        self.after(30 * 60 * 1000, self._periodic_update_check)
        if self._account_sync_notice:
            self.after(
                300,
                lambda: self.set_status(self._account_sync_notice),
            )
        if self.account_store_sync is not None:
            self.after(1200, self._push_account_store_sync)

    @staticmethod
    def _global_crash_log_path(paths: AppPaths) -> Path:
        if paths.data_dir.parent.parent.name.casefold() == "accounts":
            base_data_dir = paths.data_dir.parent.parent.parent / "data"
        else:
            base_data_dir = paths.data_dir
        return base_data_dir / "diagnostics" / "faulthandler.log"

    def _diagnostic_context(self) -> dict[str, Any]:
        upload = getattr(self, "_pages", {}).get("upload")
        tasks = getattr(upload, "_tasks", []) if upload is not None else []
        return {
            "route": getattr(self, "_active_page", ""),
            "page": getattr(self, "_active_page", ""),
            "task": {
                "running": bool(getattr(upload, "_running", False)),
                "pending": sum(
                    isinstance(task, dict)
                    and task.get("status") != "completed"
                    for task in tasks
                ),
            },
        }

    def _diagnostic_heartbeat(self) -> None:
        try:
            self.diagnostics.heartbeat(
                route=getattr(self, "_active_page", ""),
            )
            if self.winfo_exists():
                self.after(1000, self._diagnostic_heartbeat)
        except tk.TclError:
            pass

    def reload_account_database(self) -> Any | None:
        """Reopen the account database when a stale instance renders empty."""
        fresh_db = Database(self.paths.database)
        if not fresh_db.list_stores():
            return None
        self.base_db = fresh_db
        self.db = wrap_database(
            fresh_db,
            self.cloud_commands,
            self.account_store_sync,
        )
        self.service.db = self.db
        self.web_session.db = self.db
        for page in self._pages.values():
            if hasattr(page, "db"):
                page.db = self.db
        account_sync = getattr(self, "account_store_sync", None)
        if account_sync is not None and hasattr(account_sync, "db"):
            account_sync.db = fresh_db
        return fresh_db

    def _ensure_store_page_loaded(self, attempt: int = 0) -> None:
        pages = getattr(self, "_pages", None)
        if pages is not None and "stores" not in pages:
            return
        page = (
            pages.get("stores")
            if pages is not None
            else getattr(self, "store_page", None)
        )
        tree = getattr(page, "tree", None)
        if page is None or tree is None:
            return
        try:
            page.refresh()
        except Exception:
            pass
        if tree.get_children() or attempt >= 5:
            return
        self.after(
            800,
            lambda: self._ensure_store_page_loaded(attempt + 1),
        )

    def _diagnostic_upload_tick(self) -> None:
        self.diagnostics.flush_async()
        try:
            if self.winfo_exists():
                self.after(5 * 60 * 1000, self._diagnostic_upload_tick)
        except tk.TclError:
            pass

    def _start_automatic_backup(self) -> None:
        if os.environ.get("WEIMI_DISABLE_AUTO_BACKUP") == "1":
            return

        def worker() -> None:
            backup_dir = self.paths.data_dir / "backups"
            try:
                self.db.auto_backup(backup_dir)
            except Exception as exc:
                try:
                    backup_dir.mkdir(parents=True, exist_ok=True)
                    (backup_dir / "backup_error.log").write_text(
                        f"{datetime.now().isoformat(timespec='seconds')}\n{exc}\n",
                        encoding="utf-8",
                    )
                except OSError:
                    pass

        threading.Thread(
            target=worker,
            daemon=True,
            name="langlang-daily-backup",
        ).start()
        self.after(4 * 60 * 60 * 1000, self._start_automatic_backup)

    def _load_brand_assets(self) -> None:
        assets = self.paths.resource_dir / "assets"
        logo_path = assets / "logo.png"
        self.brand_logo = None
        self.app_icon = None
        if logo_path.exists():
            try:
                source = tk.PhotoImage(file=str(logo_path))
                self.brand_logo = source.subsample(4, 4)
                self.app_icon = source.subsample(7, 7)
                self.iconphoto(True, self.app_icon)
            except (OSError, tk.TclError) as exc:
                self.diagnostics.capture_exception(
                    exc,
                    category="ui_asset",
                    detail={"asset": str(logo_path)},
                )
        for key in (
            "home",
            "store",
            "orders",
            "shipping",
            "products",
            "operations",
            "procurement",
            "analytics",
            "automation",
            "settings",
            "account",
            "service",
            "marketing",
        ):
            normal_path = assets / "icons" / f"{key}_normal.png"
            active_path = assets / "icons" / f"{key}_active.png"
            if normal_path.exists() and active_path.exists():
                try:
                    normal = tk.PhotoImage(
                        file=str(normal_path)
                    ).subsample(2, 2)
                    active = tk.PhotoImage(
                        file=str(active_path)
                    ).subsample(2, 2)
                    self._nav_icon_maps[key] = (normal, active)
                except (OSError, tk.TclError) as exc:
                    self.diagnostics.capture_exception(
                        exc,
                        category="ui_asset",
                        detail={
                            "asset": str(active_path),
                            "normal": str(normal_path),
                        },
                    )

    def _build_sidebar(self) -> None:
        sidebar = tk.Frame(self, bg=NAVY, width=218)
        sidebar.grid(row=0, column=0, rowspan=2, sticky="ns")
        sidebar.grid_propagate(False)
        sidebar.columnconfigure(0, weight=1)
        sidebar.rowconfigure(11, weight=1)

        brand = tk.Frame(sidebar, bg=NAVY, height=104)
        brand.grid(row=0, column=0, sticky="ew")
        brand_inner = tk.Frame(brand, bg=NAVY)
        brand_inner.pack(anchor="w", padx=22, pady=(22, 14))
        if self.brand_logo is not None:
            tk.Label(brand_inner, image=self.brand_logo, bg=NAVY).pack(side="left")
        wordmark = tk.Frame(brand_inner, bg=NAVY)
        wordmark.pack(side="left", padx=(12, 0))
        tk.Label(
            wordmark,
            text="澜琅",
            bg=NAVY,
            fg="#f7fbf8",
            font=("Microsoft YaHei UI", 18, "bold"),
        ).pack(anchor="w")
        tk.Label(
            wordmark,
            text="LANG LANG",
            bg=NAVY,
            fg="#8fa8a4",
            font=("Segoe UI", 7),
        ).pack(anchor="w", pady=(1, 0))

        nav = [
            ("products", "商品管理", "products"),
            ("upload", "上传商品", "shipping"),
            ("stores", "店铺管理", "store"),
            ("dynamic", "动销分析", "operations"),
            ("no_traffic", "无流量清理", "operations"),
            ("order_management", "订单管理", "orders"),
            ("hosted", "托管中心", "automation"),
            ("double11", "活动中心", "marketing"),
            ("dashboard", "数据大屏", "analytics"),
        ]
        # 用“动销中心”替换原团队账号侧边栏位置；团队账号本身仍保留，移入“更多”。
        if self._can_sales_related():
            nav.append(("sales_related", "动销中心", "procurement"))
        nav = [
            item
            for item in nav
            if item[0] == "sales_related"
            or not self._PAGE_PERMISSIONS.get(item[0])
            or self._can_permission(self._PAGE_PERMISSIONS[item[0]])
        ]
        for index, (key, label, icon_name) in enumerate(nav, 1):
            self._page_icon_names[key] = icon_name
            images = self._nav_icon_maps.get(icon_name)
            button = tk.Button(
                sidebar,
                text=label,
                image=images[0] if images else "",
                command=(
                    self._show_more_menu
                    if key == "more"
                    else (
                        self._open_big_screen
                        if key == "dashboard"
                        else lambda page_key=key: self.show_page(page_key)
                    )
                ),
                bg=NAVY,
                fg="#a9bdb9",
                activebackground=NAVY_ALT,
                activeforeground="#ffffff",
                relief="flat",
                bd=0,
                anchor="w",
                justify="left",
                compound="left",
                padx=18,
                pady=8,
                font=("Microsoft YaHei UI", 9),
                cursor="hand2",
            )
            button.grid(row=index, column=0, sticky="ew", padx=10, pady=1)
            self._nav_buttons[key] = button

        for key, label, icon_name, row in (
            ("more", "更多", "settings", 13),
            ("account", "账号", "account", 14),
        ):
            self._page_icon_names[key] = icon_name
            images = self._nav_icon_maps.get(icon_name)
            nav_button = tk.Button(
                sidebar,
                text=label,
                image=images[0] if images else "",
                command=(
                    self._show_more_menu
                    if key == "more"
                    else lambda: self.show_page("account")
                ),
                bg=NAVY,
                fg="#a9bdb9",
                activebackground=NAVY_ALT,
                activeforeground="#ffffff",
                relief="flat",
                bd=0,
                anchor="w",
                justify="left",
                compound="left",
                padx=18,
                pady=7,
                font=("Microsoft YaHei UI", 9),
                cursor="hand2",
            )
            nav_button.grid(row=row, column=0, sticky="ew", padx=10, pady=1)
            self._nav_buttons[key] = nav_button

        tk.Label(
            sidebar,
            text=f"WECHAT SHOP · {__display_version__}\n安全存储 · 可追溯操作",
            bg=NAVY,
            fg="#68827e",
            justify="left",
            font=("Microsoft YaHei UI", 8),
        ).grid(row=15, column=0, sticky="sw", padx=22, pady=18)

    def _build_content(self) -> None:
        content = tk.Frame(self, bg=BG)
        content.grid(row=0, column=1, sticky="nsew")
        content.columnconfigure(0, weight=1)
        content.rowconfigure(1, weight=1)

        self.header = ttk.Frame(content, style="TFrame", padding=(28, 20, 28, 8))
        self.header.grid(row=0, column=0, sticky="ew")
        self.header.columnconfigure(0, weight=1)
        self.title_var = tk.StringVar()
        self.subtitle_var = tk.StringVar()
        ttk.Label(self.header, textvariable=self.title_var, style="Title.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(self.header, textvariable=self.subtitle_var, style="Subtitle.TLabel").grid(
            row=1, column=0, sticky="w", pady=(4, 0)
        )
        header_info = tk.Frame(self.header, bg=BG)
        header_info.grid(row=0, column=1, rowspan=2, sticky="e", padx=(0, 12))
        if self.auth_context is not None:
            account_button = ttk.Menubutton(
                header_info, text=f"账户 · {self.auth_context.username}"
            )
            account_menu = tk.Menu(account_button, tearoff=False)
            account_menu.add_command(label="切换账号", command=self._return_to_login)
            account_menu.add_command(
                label="退出登录",
                command=lambda: self._return_to_login(forget_account=True),
            )
            account_button.configure(menu=account_menu)
            account_button.pack(side="left", padx=(0, 8))
        ttk.Button(
            header_info,
            text="任务日志",
            command=self._show_task_logs,
        ).pack(side="left", padx=(0, 8))
        self.auto_refresh_var = tk.BooleanVar(value=self.auto_refresh_enabled)
        auto = tk.Checkbutton(
            header_info,
            text="自动检查 60s",
            variable=self.auto_refresh_var,
            command=self._toggle_auto_refresh,
            bg=BG,
            fg=MUTED,
            activebackground=BG,
            activeforeground=TEXT,
            selectcolor=PANEL,
            bd=0,
            font=("Microsoft YaHei UI", 8),
        )
        auto.pack(side="left", padx=(0, 10))
        version_badge = tk.Frame(
            header_info,
            bg="#f5f7fc",
            padx=10,
            pady=5,
            highlightbackground="#e4e8f0",
            highlightthickness=1,
        )
        version_badge.pack(side="left", padx=(0, 6))
        tk.Label(
            version_badge,
            text=f"◉ v{__display_version__}",
            bg="#f5f7fc",
            fg="#4f5e74",
            font=("Microsoft YaHei UI", 8, "bold"),
        ).pack()
        status_badge = tk.Frame(
            header_info,
            bg=SOFT_TEAL,
            padx=11,
            pady=5,
        )
        status_badge.pack(side="left")
        self.header_store_var = tk.StringVar(value="0 家店铺")
        tk.Label(
            status_badge,
            textvariable=self.header_store_var,
            bg=SOFT_TEAL,
            fg=BLUE,
            font=("Microsoft YaHei UI", 8, "bold"),
        ).pack()

        self.page_host = ttk.Frame(content, style="TFrame")
        self.page_host.grid(row=1, column=0, sticky="nsew")
        self.page_host.columnconfigure(0, weight=1)
        self.page_host.rowconfigure(0, weight=1)

        self._pages = {}

        status = tk.Frame(content, bg=PANEL, highlightbackground="#e3e7ee", highlightthickness=1)
        status.grid(row=2, column=0, sticky="ew")
        status.columnconfigure(0, weight=1)
        self.status_var = tk.StringVar(value="准备就绪")
        self.status_label = tk.Label(
            status,
            textvariable=self.status_var,
            bg=PANEL,
            fg=MUTED,
            anchor="w",
            padx=14,
            pady=7,
            font=("Microsoft YaHei UI", 9),
        )
        self.status_label.grid(row=0, column=0, sticky="ew")
        self.status_progress = ttk.Progressbar(status, mode="indeterminate", length=160)
        self.status_progress.grid(row=0, column=1, padx=14, pady=5)
        self.status_progress.grid_remove()

    def _create_page(self, key: str) -> ttk.Frame | None:
        if key == "workbench":
            return WorkbenchPage(self.page_host, self)
        if key == "diagnostics":
            return DiagnosticsPage(self.page_host, self)
        if key == "stores":
            return StorePage(self.page_host, self)
        if key == "orders":
            return ExtractPage(self.page_host, self)
        if key == "products":
            return ProductsPage(self.page_host, self)
        if key == "upload":
            return UploadPage(self.page_host, self)
        if key == "operations":
            return OperationsPage(self.page_host, self)
        if key == "customer_service":
            return CustomerServicePage(self.page_host, self)
        if key == "marketing":
            return MarketingPage(self.page_host, self)
        if key == "storefront":
            return StorefrontPage(self.page_host, self)
        if key == "finance":
            return FinancePage(self.page_host, self)
        if key == "procurement":
            return ProcurementPage(self.page_host, self)
        if key == "analytics":
            page = AnalyticsPage(self.page_host, self)
            page.title = "大屏"
            return page
        if key == "automation":
            return AutomationPage(self.page_host, self)
        if key == "settings":
            return SettingsPage(self.page_host, self)
        if key == "account":
            return AccountPage(self.page_host, self)
        if key == "parsed":
            return ParsedOrdersPage(self.page_host, self)
        if key == "order_management":
            page = OrdersPage(self.page_host, self)
            page.title = "订单管理"
            page.subtitle = (
                "微信订单、采购单号、物流、取件码和本地标记"
            )
            return page
        if key == "store_groups":
            return StoreGroupsPage(self.page_host, self)
        if key == "double11":
            return ActivityCenterPage(self.page_host, self)
        if key == "violation_blacklist":
            return ViolationLibraryPage(self.page_host, self)
        if key == "dynamic":
            return SalesSuggestionsPage(self.page_host, self)
        if key == "no_traffic":
            return NoTrafficPage(self.page_host, self)
        if key == "hosted":
            return HostedCenterPage(self.page_host, self)
        if key == "team":
            return TeamPage(self.page_host, self)
        if key == "sales_related":
            return DongxiaoPage(self.page_host, self)
        return None

    def _ensure_page(self, key: str) -> ttk.Frame | None:
        page = self._pages.get(key)
        if page is not None:
            return page
        page = self._create_page(key)
        if page is None:
            return None
        self._pages[key] = page
        page.grid(row=0, column=0, sticky="nsew")
        page.grid_remove()
        return page

    def show_page(self, key: str) -> None:
        if key not in self._PAGE_PERMISSIONS or key == "dashboard":
            return
        if key == "sales_related" and not self._can_sales_related():
            messagebox.showwarning(
                "没有功能权限",
                "当前账号没有采购或售后相关权限。",
                parent=self,
            )
            return
        permission = self._PAGE_PERMISSIONS.get(key)
        if permission and not self._can_permission(permission):
            messagebox.showwarning(
                "没有功能权限",
                "当前子账号没有被分配此功能权限。",
                parent=self,
            )
            return
        page = self._ensure_page(key)
        if page is None:
            return
        for page_key, page_item in self._pages.items():
            page_item.grid_remove()
            nav_key = page_key if page_key in self._nav_buttons else "more"
            images = self._nav_icon_maps.get(self._page_icon_names.get(nav_key, nav_key))
            self._nav_buttons[nav_key].configure(
                bg=NAVY,
                fg="#a9bdb9",
                image=images[0] if images else "",
            )
        page = self._pages[key]
        if not self._task_running:
            self.set_status("准备就绪", busy=False)
        self._active_page = key
        page.grid()
        self.title_var.set(getattr(page, "title", ""))
        self.subtitle_var.set(getattr(page, "subtitle", ""))
        page_title = str(getattr(page, "title", key) or key)
        if not self._task_running:
            self.set_status(f"正在加载{page_title}…", busy=True)
        self.update_idletasks()
        if key != "team" or (
            self.auth_context is not None
            and self.auth_client is not None
        ):
            try:
                page.refresh()
            except Exception as exc:
                self.set_status(f"页面刷新失败：{exc}")
                messagebox.showerror(
                    "页面刷新失败",
                    f"{getattr(page, 'title', key)} 加载失败：\n{exc}",
                    parent=self,
                )
        if not self._task_running:
            self.set_status(f"{page_title}已打开", busy=False)
        nav_key = key if key in self._nav_buttons else "more"
        images = self._nav_icon_maps.get(self._page_icon_names.get(nav_key, nav_key))
        self._nav_buttons[nav_key].configure(
            bg=NAVY_ALT,
            fg="white",
            image=images[1] if images else "",
        )

    def _open_big_screen(self) -> None:
        if (
            self._big_screen_window is not None
            and self._big_screen_window.winfo_exists()
        ):
            self._big_screen_window.deiconify()
            self._big_screen_window.lift()
            self._big_screen_window.focus_force()
            return
        self._big_screen_window = BigScreenWindow(self, self)
        self._big_screen_window.focus_force()

    def _show_more_menu(self) -> None:
        menu = tk.Menu(self, tearoff=0)
        items = (
            ("店铺分组管理", "store_groups", "stores_edit"),
            ("违规商品库", "violation_blacklist", "products_view"),
            ("售后管理", "operations", "after_sales"),
            ("客服工作台", "customer_service", "after_sales"),
            ("采购中心", "procurement", "procurement"),
            ("自动化", "automation", "products_edit"),
            ("店铺主页", "storefront", "stores_edit"),
            ("资金账户", "finance", "orders_view"),
            ("团队账号", "team", "team_manage"),
            ("问题与诊断", "diagnostics", "team_manage"),
            ("使用说明", "settings", "team_manage"),
        )
        has_visible_items = False
        for label, page, permission in items:
            if self._can_permission(permission):
                menu.add_command(
                    label=label,
                    command=lambda key=page: self.show_page(key),
                )
                has_visible_items = True
        if has_visible_items:
            menu.add_separator()
        menu.add_command(
            label="检查更新",
            command=lambda: self._check_updates(manual=True),
        )
        try:
            menu.tk_popup(
                self._nav_buttons["more"].winfo_rootx() + 70,
                self._nav_buttons["more"].winfo_rooty(),
            )
        finally:
            menu.grab_release()

    def _auto_check_updates(self) -> None:
        try:
            if not self.winfo_exists():
                return
        except tk.TclError:
            return
        if os.environ.get("WEIMI_DISABLE_AUTO_UPDATE") == "1":
            return
        if self.db.get_setting("update_auto_check", "1") != "1":
            return
        self._check_updates(manual=False)

    def _periodic_update_check(self) -> None:
        if self.winfo_exists():
            self._auto_check_updates()
            self.after(30 * 60 * 1000, self._periodic_update_check)

    def _check_updates(self, manual: bool = True) -> None:
        if self._update_check_running:
            if manual:
                self.set_status("正在检查更新，请稍候")
            return
        source = self.db.get_setting(
            "update_source",
            os.environ.get(
                "LANGLANG_UPDATE_SOURCE",
                DEFAULT_UPDATE_SOURCE,
            ),
        )
        channel = load_tester_channel(self.paths.resource_dir)
        if is_development_preview_version(__version__) and not (
            channel.get("enabled") and channel.get("manifest_url")
        ):
            if manual:
                self.set_status("当前为测试版，未配置测试更新渠道")
            return
        if channel.get("enabled") and channel.get("manifest_url"):
            source = self.db.get_setting(
                "tester_update_source",
                str(channel["manifest_url"]),
            )
        if not manual and not source:
            return
        self._update_check_running = True
        self.set_status("正在检查更新…", busy=True)

        def work(_progress):
            return check_for_update(source, self.paths.resource_dir)

        def done(info) -> None:
            self._update_check_running = False
            self.set_status(
                f"当前版本 {info.current_version} · 最新版本 {info.latest_version}"
            )
            message = (
                f"当前版本：{info.current_version}\n"
                f"最新版本：{info.latest_version}\n\n"
                f"{info.notes or '没有更新说明'}"
            )
            if not info.available:
                if manual:
                    messagebox.showinfo("检查更新", "当前已经是最新版本。")
                return
            if not info.download_url:
                if manual:
                    messagebox.showwarning(
                        "无法自动更新",
                        message + "\n\n更新清单没有配置 download_url。",
                    )
                return
            skipped = self.db.get_setting(
                "skipped_update_version", ""
            )
            if not manual and not info.mandatory and skipped == info.latest_version:
                return
            if not self._show_update_prompt(info, message):
                if not info.mandatory:
                    self.db.set_setting(
                        "skipped_update_version",
                        info.latest_version,
                    )
                return
            self._install_update(info)

        self.run_task(
            work,
            done,
            lambda error: self._update_check_failed(error, manual),
            progress=lambda message, _current, _total: self.set_status(
                message, busy=True
            ),
            log_title="检查软件更新",
        )

    def _update_check_failed(self, error: str, manual: bool) -> None:
        self._update_check_running = False
        self.set_status("检查更新失败")
        if manual:
            messagebox.showerror("检查更新失败", error)

    def _show_update_prompt(self, info, message: str) -> bool:
        dialog = tk.Toplevel(self)
        dialog.title("必须更新" if info.mandatory else "发现新版本")
        dialog.transient(self)
        dialog.resizable(False, False)
        dialog.configure(bg="#f7f9fc")
        accepted = {"value": False}
        if info.mandatory:
            dialog.protocol("WM_DELETE_WINDOW", lambda: None)
            dialog.bind("<Escape>", lambda _event: "break")
        else:
            dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)
            dialog.bind("<Escape>", lambda _event: dialog.destroy())
        panel = ttk.Frame(
            dialog,
            style="Panel.TFrame",
            padding=24,
        )
        panel.pack(fill="both", expand=True)
        ttk.Label(
            panel,
            text=(
                "当前版本必须更新后继续使用"
                if info.mandatory
                else "发现澜琅新版本"
            ),
            style="Section.TLabel",
        ).pack(anchor="w")
        ttk.Label(
            panel,
            text=message,
            style="Panel.TLabel",
            wraplength=520,
            justify="left",
        ).pack(anchor="w", fill="x", pady=(12, 16))
        if info.mandatory:
            ttk.Label(
                panel,
                text=(
                    "此版本包含重要修复。当前窗口不能关闭，"
                    "点击“立即更新”后软件会自动下载、安装并重启。"
                ),
                style="Panel.TLabel",
                foreground="#b45a37",
                wraplength=520,
                justify="left",
            ).pack(anchor="w", pady=(0, 16))
        actions = ttk.Frame(panel, style="Panel.TFrame")
        actions.pack(fill="x")
        if not info.mandatory:
            ttk.Button(
                actions,
                text="关闭弹窗",
                command=dialog.destroy,
            ).pack(side="right")

        def accept() -> None:
            accepted["value"] = True
            dialog.destroy()

        ttk.Button(
            actions,
            text="立即更新",
            command=accept,
            style="Primary.TButton",
        ).pack(side="right", padx=(0, 8))
        dialog.update_idletasks()
        width = max(560, dialog.winfo_reqwidth())
        height = max(260, dialog.winfo_reqheight())
        x = self.winfo_rootx() + max(
            0, (self.winfo_width() - width) // 2
        )
        y = self.winfo_rooty() + max(
            0, (self.winfo_height() - height) // 2
        )
        dialog.geometry(f"{width}x{height}+{x}+{y}")
        dialog.grab_set()
        dialog.focus_force()
        self.wait_window(dialog)
        return accepted["value"]

    def _install_update(self, info) -> None:
        self.set_status(
            f"正在准备更新 {info.latest_version}…",
            busy=True,
        )
        progress_dialog = None
        progress_var = None
        if info.mandatory:
            progress_dialog = tk.Toplevel(self)
            progress_dialog.title("正在更新（不可关闭）")
            progress_dialog.transient(self)
            progress_dialog.resizable(False, False)
            progress_dialog.protocol("WM_DELETE_WINDOW", lambda: None)
            progress_dialog.bind(
                "<Escape>",
                lambda _event: "break",
            )
            progress_var = tk.StringVar(
                value=f"正在准备 {info.latest_version}…"
            )
            frame = ttk.Frame(
                progress_dialog,
                style="Panel.TFrame",
                padding=24,
            )
            frame.pack(fill="both", expand=True)
            ttk.Label(
                frame,
                text="软件必须完成本次更新",
                style="Section.TLabel",
            ).pack(anchor="w")
            ttk.Label(
                frame,
                textvariable=progress_var,
                style="Panel.TLabel",
            ).pack(anchor="w", pady=(12, 10))
            bar = ttk.Progressbar(
                frame,
                mode="indeterminate",
                length=460,
            )
            bar.pack(fill="x")
            bar.start(12)
            progress_dialog.update_idletasks()
            width = max(520, progress_dialog.winfo_reqwidth())
            height = max(180, progress_dialog.winfo_reqheight())
            x = self.winfo_rootx() + max(
                0, (self.winfo_width() - width) // 2
            )
            y = self.winfo_rooty() + max(
                0, (self.winfo_height() - height) // 2
            )
            progress_dialog.geometry(f"{width}x{height}+{x}+{y}")
            progress_dialog.grab_set()

        def work(progress):
            return download_and_stage_update(
                info,
                self.paths.project_dir,
                progress,
            )

        def progress(message: str, current: int, total: int) -> None:
            suffix = ""
            if total:
                suffix = f" {min(100, int(current * 100 / total))}%"
            self.set_status(message + suffix, busy=True)
            if progress_var is not None:
                progress_var.set(message + suffix)

        def done(staged) -> None:
            if progress_dialog is not None:
                progress_dialog.destroy()
            messagebox.showinfo(
                "自动更新",
                "更新包已下载并校验完成。\n\n"
                "点击“确定”后澜琅会启动更新器、关闭当前程序，"
                "自动替换程序并重新启动。\n"
                "店铺、密钥、Cookie 和业务数据不会被覆盖。\n\n"
                "更新日志：%LOCALAPPDATA%\\LangLang\\updates\\update.log",
            )
            installer_started = time.monotonic()
            try:
                installer_log_id = self.db.start_task_log(
                    scope="updater",
                    operation=f"启动更新安装器 {staged.version}",
                    message="更新包已下载并通过校验",
                )
            except Exception:
                installer_log_id = None
            try:
                log_path = launch_update_installer(
                    staged,
                    self.paths.project_dir,
                )
            except Exception as exc:
                self._finish_task_log(
                    installer_log_id,
                    installer_started,
                    "failed",
                    str(exc),
                    f"更新安装器启动失败：{exc}",
                )
                self.set_status("自动更新准备失败")
                messagebox.showerror("自动更新失败", str(exc))
                return
            self._finish_task_log(
                installer_log_id,
                installer_started,
                "submitted",
                f"已启动 {staged.version} 更新安装器",
                (
                    f"更新日志：{log_path}\n"
                    "安装器将等待旧进程退出、备份当前目录、替换文件并重启；"
                    "新版启动失败时会自动恢复旧版。"
                ),
            )
            self.set_status("更新已准备完成，软件将自动重启…")
            diagnostics = getattr(self, "diagnostics", None)
            if diagnostics is not None:
                diagnostics.record(
                    "session_end_update",
                    "软件为安装更新正常退出",
                    severity="info",
                )
                diagnostics.mark_clean_exit()
            force_exit = threading.Timer(2.0, lambda: os._exit(0))
            force_exit.daemon = True
            force_exit.start()
            self.after(250, self.destroy)

        self.run_task(
            work,
            done,
            lambda error: self._update_install_failed(
                info,
                error,
                progress_dialog,
            ),
            progress=progress,
            log_title=f"下载并校验更新 {info.latest_version}",
        )

    def _update_install_failed(
        self,
        info,
        error: str,
        progress_dialog=None,
    ) -> None:
        if progress_dialog is not None and progress_dialog.winfo_exists():
            progress_dialog.destroy()
        self.set_status("自动更新失败")
        if info.mandatory:
            messagebox.showerror(
                "必须更新",
                "更新下载或校验失败，当前版本不能继续使用。\n\n"
                + str(error),
            )
            self.after(300, lambda: self._install_update(info))
            return
        messagebox.showerror("自动更新失败", str(error))

    def store_data_changed(self) -> None:
        if self._active_page == "stores" and "stores" in self._pages:
            self.store_page.invalidate_metrics_cache()
            self.store_page.refresh()
        if self._active_page in {"orders", "products"}:
            self._pages[self._active_page].refresh()
        self._refresh_nav_alerts()
        self._schedule_account_store_sync()

    def store_login_updated(self) -> None:
        """Refresh scan-login rows without scheduling a vault upload per store."""
        if self._active_page == "stores" and "stores" in self._pages:
            self.store_page.invalidate_metrics_cache()
            self.store_page.refresh()
        self._refresh_nav_alerts()

    def queue_store_deletions(self, app_ids: list[str]) -> None:
        if self.account_store_sync is None:
            return
        self.account_store_sync.queue_deletions(app_ids)

    def _schedule_account_store_sync(self) -> None:
        if self.account_store_sync is None:
            return
        if self._account_sync_job is not None:
            try:
                self.after_cancel(self._account_sync_job)
            except tk.TclError:
                pass
        self._account_sync_job = self.after(
            1200,
            self._push_account_store_sync,
        )

    def _push_account_store_sync(self) -> None:
        self._account_sync_job = None
        if (
            self.account_store_sync is None
            or self._account_sync_running
        ):
            return
        if (
            getattr(self, "_task_running", False)
            or self.has_running_task("stores", "account")
        ):
            if self.winfo_exists():
                self.after(5000, self._push_account_store_sync)
            return
        self._account_sync_running = True
        sync = self.account_store_sync
        started = time.monotonic()
        try:
            log_id = self.db.start_task_log(
                scope="account_sync",
                operation="同步账户店铺和登录状态",
                message="开始同步加密店铺保险箱",
            )
        except Exception:
            log_id = None

        def worker() -> None:
            try:
                sync.push()
            except Exception as exc:
                self._post(
                    self._account_sync_finished,
                    str(exc),
                    log_id,
                    started,
                )
            else:
                self._post(
                    self._account_sync_finished,
                    "",
                    log_id,
                    started,
                )

        threading.Thread(
            target=worker,
            daemon=True,
            name="langlang-account-store-sync",
        ).start()

    def _account_sync_finished(
        self,
        error: str,
        log_id: int | None = None,
        started: float | None = None,
    ) -> None:
        self._account_sync_running = False
        if error:
            self.db.set_setting(
                "account_store_sync_error",
                error[:500],
            )
            self.set_status(f"账户店铺同步失败：{error}")
        else:
            self.db.set_setting("account_store_sync_error", "")
        self._finish_task_log(
            log_id,
            started or time.monotonic(),
            "failed" if error else "success",
            error or "账户店铺保险箱同步完成",
            error,
        )

    def _show_task_logs(self) -> None:
        TaskLogDialog(self, self.db)

    def set_status(self, message: str, busy: bool = False) -> None:
        self.status_var.set(message)
        if busy or self._task_running:
            self.status_progress.grid()
            if not self._status_busy:
                self.status_progress.start(12)
            self._status_busy = True
        else:
            if self._status_busy:
                self.status_progress.stop()
            self._status_busy = False
            self.status_progress.grid_remove()

    def run_task(
        self,
        work: Callable[[Callable[[str, int, int], None]], Any],
        on_done: Callable[[Any], None],
        on_error: Callable[[str], None],
        progress: Callable[[str, int, int], None] | None = None,
        log_title: str = "",
        task_scope: str | None = None,
        command: str | None = None,
        store_app_id: str = "",
        store_app_ids: list[str] | None = None,
    ) -> bool:
        page_scope = str(self._active_page or "app")
        action_scope = str(log_title or "").strip()
        scope_key = str(task_scope or "").strip()
        if not scope_key:
            scope_key = (
                f"{page_scope}:{action_scope}"
                if action_scope
                else page_scope
            )
        if self.has_running_task(scope_key):
            self.set_status(
                "相同操作正在执行，请勿重复提交。"
                "其他页面和其他业务操作可以继续使用。"
            )
            return False
        task_token = object()
        self._running_tasks.setdefault(scope_key, set()).add(task_token)
        self._task_running = True
        page = self._pages.get(self._active_page)
        scope = self._active_page or "app"
        page_title = str(getattr(page, "title", "") or scope)
        operation = log_title or f"{page_title}任务"
        started = time.monotonic()
        last_progress = {"message": ""}
        try:
            task_log_id = self.db.start_task_log(
                scope=scope,
                operation=operation,
                message="任务开始",
            )
        except Exception:
            task_log_id = None

        def finish_success(result: Any) -> None:
            self._release_task(scope_key, task_token)
            status, message, detail = self._task_result_log(result)
            self._finish_task_log(
                task_log_id,
                started,
                status,
                message,
                detail
                + (
                    f"\n最后进度：{last_progress['message']}"
                    if last_progress["message"]
                    else ""
                ),
            )
            on_done(result)

        def finish_error(error: str) -> None:
            self._release_task(scope_key, task_token)
            self._finish_task_log(
                task_log_id,
                started,
                "failed",
                error,
                (
                    f"失败原因：{error}\n最后进度："
                    f"{last_progress['message'] or '—'}"
                ),
            )
            on_error(error)

        def worker() -> None:
            try:
                def report(
                    message: str,
                    current: int,
                    total: int,
                ) -> None:
                    last_progress["message"] = message
                    self._post(progress, message, current, total)

                if command:
                    if self.cloud_commands is None:
                        raise RuntimeError("当前账户无法执行联网授权操作")
                    result = self.cloud_commands.execute_many(
                        command,
                        store_app_ids
                        or ([store_app_id] if store_app_id else []),
                        lambda: work(report),
                    )
                else:
                    result = work(report)
            except Exception as exc:
                try:
                    self.diagnostics.capture_exception(
                        exc,
                        category="background_task",
                        detail={
                            "operation": operation,
                            "scope": scope_key,
                            "page": scope,
                        },
                    )
                except Exception:
                    pass
                self._post(self.set_status, f"操作失败：{exc}")
                self._post(finish_error, str(exc))
            else:
                self._post(finish_success, result)

        threading.Thread(target=worker, daemon=True, name="weimi-task").start()
        return True

    def has_running_task(self, *task_scopes: str) -> bool:
        if not task_scopes:
            return self._task_running
        return any(
            bool(
                self._running_tasks.get(str(scope))
                or any(
                    bool(tokens)
                    for key, tokens in self._running_tasks.items()
                    if key.startswith(f"{scope}:")
                )
            )
            for scope in task_scopes
        )

    def _release_task(self, task_scope: str, token: object) -> None:
        tokens = self._running_tasks.get(task_scope)
        if tokens is not None:
            tokens.discard(token)
            if not tokens:
                self._running_tasks.pop(task_scope, None)
        self._task_running = bool(self._running_tasks)

    def run_logged_call(
        self,
        operation: str,
        work: Callable[[], Any],
        *,
        scope: str = "network",
        store_id: int | None = None,
        success_message: str = "联网调用完成",
    ) -> Any:
        started = time.monotonic()
        try:
            log_id = self.db.start_task_log(
                scope=scope,
                operation=operation,
                message="联网调用开始",
                store_id=store_id,
            )
        except Exception:
            log_id = None
        try:
            result = work()
        except Exception as exc:
            self._finish_task_log(
                log_id,
                started,
                "failed",
                str(exc),
                f"联网调用失败：{exc}",
            )
            raise
        detail = ""
        if isinstance(result, (list, tuple, set, dict)):
            detail = f"返回记录数：{len(result)}"
        self._finish_task_log(
            log_id,
            started,
            "success",
            success_message,
            detail,
        )
        return result

    @staticmethod
    def _task_result_log(result: Any) -> tuple[str, str, str]:
        if isinstance(result, list):
            if not result:
                return "success", "任务完成，没有发现问题", ""
            if all(isinstance(item, str) for item in result):
                return (
                    "partial",
                    f"完成，发现 {len(result)} 个问题",
                    "\n".join(result),
                )
            return (
                "success",
                f"任务完成，共返回 {len(result)} 条数据",
                f"返回记录数：{len(result)}",
            )
        errors = getattr(result, "errors", None)
        if errors:
            detail = getattr(result, "detail", "")
            return (
                "partial",
                str(getattr(result, "message", "") or "任务部分失败"),
                (
                    str(detail)
                    if detail
                    else "\n".join(str(item) for item in errors)
                ),
            )
        message = getattr(result, "message", None)
        if message is not None:
            detail = getattr(result, "detail", "")
            return (
                "success",
                str(message),
                str(detail) if detail else repr(result),
            )
        if result is None:
            return "success", "任务完成", ""
        return "success", "任务完成", str(result)[:12000]

    def _finish_task_log(
        self,
        log_id: int | None,
        started: float,
        status: str,
        message: str,
        detail: str = "",
    ) -> None:
        if log_id is None:
            return
        try:
            self.db.finish_task_log(
                log_id,
                status=status,
                message=message,
                detail=detail,
                duration_ms=int((time.monotonic() - started) * 1000),
            )
        except Exception:
            pass

    def _post(self, callback: Callable[..., Any] | None, *args: Any) -> None:
        if callback is not None:
            self._task_queue.put((callback, args))

    def _drain_task_queue(self) -> None:
        deadline = time.monotonic() + 0.008
        processed = 0
        while processed < 100 and time.monotonic() < deadline:
            try:
                callback, args = self._task_queue.get_nowait()
            except queue.Empty:
                break
            processed += 1
            try:
                callback(*args)
            except tk.TclError:
                continue
            except Exception as exc:
                try:
                    self.diagnostics.capture_exception(
                        exc,
                        category="ui_callback",
                    )
                except Exception:
                    pass
                try:
                    self.set_status(
                        f"后台消息处理失败：{type(exc).__name__}"
                    )
                except tk.TclError:
                    pass
        try:
            if self.winfo_exists():
                self.after(80, self._drain_task_queue)
        except tk.TclError:
            pass

    def _center_window(self) -> None:
        self.update_idletasks()
        width = self.winfo_width()
        height = self.winfo_height()
        x = max(0, (self.winfo_screenwidth() - width) // 2)
        y = max(0, (self.winfo_screenheight() - height) // 2)
        self.geometry(f"{width}x{height}+{x}+{y}")

    def _return_to_login(self, *, forget_account: bool = False) -> None:
        if self._closing or self.auth_context is None:
            return
        if self._task_running or getattr(self._pages.get("upload"), "_running", False):
            if not messagebox.askyesno(
                "切换账号",
                "当前仍有后台任务在运行。\n\n"
                "可以继续切换账号或退出登录；未完成任务会在后台收尾，"
                "但结果不会带入下一个账户。是否继续？",
                parent=self,
            ):
                self.set_status("已取消切换，当前后台任务继续运行")
                return
        if forget_account and self.saved_account_store is not None:
            try:
                self.saved_account_store.remove(self.auth_context.account_id)
            except OSError:
                messagebox.showerror(
                    "退出登录失败", "无法更新本机保存的登录信息。", parent=self
                )
                return
        self.return_to_login = True
        for callback in self.tk.splitlist(self.tk.call("after", "info")):
            # The widget that registered the callback owns its Tcl command.
            self.tk.call("after", "cancel", callback)
        self._on_close()

    def _on_close(self) -> None:
        if self._closing:
            return
        self._closing = True
        upload_page = getattr(self, "_pages", {}).get("upload")
        prepare_upload = getattr(upload_page, "prepare_for_close", None)
        if callable(prepare_upload):
            try:
                prepare_upload()
            except Exception:
                pass
        diagnostics = getattr(self, "diagnostics", None)
        runtime = getattr(self, "dongxiao_runtime", None)
        if runtime is not None:
            try:
                runtime.stop()
            except Exception:
                pass
        try:
            diagnostics.record(
                "session_end",
                "软件正常关闭",
                severity="info",
            )
            diagnostics.flush(timeout=2.0)
        except Exception:
            pass
        if diagnostics is not None:
            diagnostics.close()
        try:
            self.withdraw()
        except tk.TclError:
            pass
        self.destroy()

    def _start_dongxiao_runtime(self) -> None:
        runtime = self.ensure_dongxiao_runtime()
        if runtime is None:
            return
        try:
            runtime.start_if_needed()
        except Exception as exc:
            diagnostics = getattr(self, "diagnostics", None)
            if diagnostics is not None:
                diagnostics.capture_exception(
                    exc,
                    category="dongxiao_runtime",
                    detail={"operation": "start"},
                )

    def ensure_dongxiao_runtime(self):
        runtime = getattr(self, "dongxiao_runtime", None)
        if runtime is not None:
            return runtime
        try:
            runtime = DongxiaoRuntime(
                self.base_db.path,
                self.service,
            )
            runtime.start_if_needed()
        except Exception as exc:
            self.dongxiao_runtime = None
            self.dongxiao_runtime_error = str(exc)
            diagnostics = getattr(self, "diagnostics", None)
            if diagnostics is not None:
                diagnostics.capture_exception(
                    exc,
                    category="dongxiao_runtime",
                    detail={"operation": "retry_initialize"},
                )
            return None
        self.dongxiao_runtime = runtime
        self.dongxiao_runtime_error = ""
        return runtime

    def _toggle_auto_refresh(self) -> None:
        self.auto_refresh_enabled = bool(self.auto_refresh_var.get())
        self.db.set_setting("auto_refresh", "1" if self.auto_refresh_enabled else "0")
        self.set_status(
            "已开启 60 秒自动检查登录状态"
            if self.auto_refresh_enabled
            else "已关闭自动检查"
        )

    def _auto_refresh_tick(self) -> None:
        if (
            self.auto_refresh_enabled
            and not self._auto_refresh_running
            and not self.has_running_task("stores:scan-login")
            and not self.has_running_task(
                "stores:offline-selected",
                "account:clear-web-logins",
            )
            and not getattr(self, "_task_running", False)
            and not getattr(self, "_account_sync_running", False)
        ):
            self._auto_refresh_running = True
            self._auto_tick_count += 1
            tick = self._auto_tick_count
            stores = logged_in_stores(self.db.list_stores())
            if stores:
                try:
                    cursor = int(
                        self.db.get_setting("auto_refresh_cursor", "0")
                    )
                except ValueError:
                    cursor = 0
                position = next(
                    (
                        index
                        for index, store in enumerate(stores)
                        if store.id >= cursor
                    ),
                    0,
                )
                batch_size = min(len(stores), self._auto_refresh_batch_size)
                rotating = [
                    stores[(position + offset) % len(stores)]
                    for offset in range(batch_size)
                ]
                next_cursor = stores[
                    (position + batch_size) % len(stores)
                ].id
            else:
                rotating = []
                next_cursor = 0
            refresh_started = time.monotonic()
            try:
                refresh_log_id = self.db.start_task_log(
                    scope="auto_refresh",
                    operation="自动轮询店铺登录和经营数据",
                    message=f"本批 {len(rotating)} 家店铺",
                )
            except Exception:
                refresh_log_id = None

            def worker() -> None:
                now = int(time.time())

                def inspect(
                    store: Any,
                ) -> tuple[int, str, str, list[str]]:
                    sync_errors: list[str] = []
                    try:
                        status, detail = self.web_session.probe(store)
                    except Exception as exc:
                        status, detail = "连接异常", str(exc)
                        self.db.update_web_status(store.id, status, detail)
                    if store.app_id != "demo-store":
                        try:
                            self.service.sync_recent_order_updates(
                                store,
                                now - 300,
                                now,
                            )
                        except Exception as exc:
                            sync_errors.append(f"最近订单同步失败：{exc}")
                        try:
                            self.service.sync_after_sales(
                                store,
                                now - 300,
                                now,
                                time_field="update",
                            )
                        except Exception as exc:
                            sync_errors.append(f"售后同步失败：{exc}")
                        if tick % 5 == 0:
                            try:
                                self.service.sync_analytics_day(
                                    store,
                                    date.today().isoformat(),
                                )
                            except Exception as exc:
                                sync_errors.append(
                                    f"经营数据同步失败：{exc}"
                                )
                    return store.id, status, detail, sync_errors

                try:
                    if rotating:
                        with ThreadPoolExecutor(
                            max_workers=min(
                                self._auto_refresh_workers, len(rotating)
                            ),
                            thread_name_prefix="langlang-store-check",
                        ) as executor:
                            results = list(executor.map(inspect, rotating))
                    else:
                        results = []
                except Exception as exc:
                    self._post(
                        self._auto_refresh_failed,
                        str(exc),
                        refresh_log_id,
                        refresh_started,
                    )
                else:
                    self._post(
                        self._auto_refresh_finished,
                        results,
                        next_cursor,
                        refresh_log_id,
                        refresh_started,
                    )

            threading.Thread(
                target=worker,
                daemon=True,
                name="langlang-auto-refresh",
            ).start()
        if self.winfo_exists():
            self.after(60000, self._auto_refresh_tick)

    def _violation_daily_tick(self) -> None:
        if self._closing:
            return
        if self.winfo_exists():
            self.after(
                10 * 60 * 1000,
                self._violation_daily_tick,
            )
        if self._violation_daily_running or self._task_running:
            return
        if self._account_sync_running or self._auto_refresh_running:
            return
        stores = logged_in_stores(self.db.list_stores())
        if not stores:
            return
        today = date.today().isoformat()
        if self.db.get_setting("violation_daily_last_success", "") == today:
            return
        try:
            last_attempt = float(
                self.db.get_setting("violation_daily_attempt_at", "0")
            )
        except ValueError:
            last_attempt = 0
        now_timestamp = time.time()
        if now_timestamp - last_attempt < 30 * 60:
            return
        self._violation_daily_running = True
        try:
            self.db.set_setting(
                "violation_daily_attempt_at",
                str(int(now_timestamp)),
            )
        except Exception:
            pass
        started = time.monotonic()
        try:
            log_id = self.db.start_task_log(
                scope="violation_daily",
                operation="每日空闲违规同步",
                message="等待后台逐店读取明确违规商品",
            )
        except Exception:
            log_id = None

        def worker() -> None:
            processed: list[str] = []
            errors: list[str] = []
            paused = False
            for store in stores:
                if self._task_running:
                    paused = True
                    break
                try:
                    self.service.sync_violation_library(store)
                except Exception as exc:
                    errors.append(
                        f"{store.name}: {type(exc).__name__}: {exc}"
                    )
                else:
                    processed.append(store.name)
            self._post(
                self._violation_daily_finished,
                stores,
                processed,
                errors,
                paused,
                log_id,
                started,
            )

        threading.Thread(
            target=worker,
            daemon=True,
            name="langlang-daily-violation-sync",
        ).start()

    def _violation_daily_finished(
        self,
        stores: list[Any],
        processed: list[str],
        errors: list[str],
        paused: bool,
        log_id: int | None,
        started: float,
    ) -> None:
        self._violation_daily_running = False
        complete = (
            not paused
            and not errors
            and len(processed) == len(stores)
        )
        if complete:
            try:
                self.db.set_setting(
                    "violation_daily_last_success",
                    date.today().isoformat(),
                )
            except Exception:
                pass
        self._finish_task_log(
            log_id,
            started,
            "success" if complete else "partial",
            (
                f"明确违规同步完成：{len(processed)} 家店铺"
                if complete
                else f"明确违规同步未完成：成功 {len(processed)} 家"
            ),
            "\n".join([*errors, "用户任务进行中，已暂停" if paused else ""]).strip(),
        )
        if "violation_blacklist" in self._pages:
            self._pages["violation_blacklist"].refresh()
        self._refresh_nav_alerts()

    def _auto_refresh_finished(
        self,
        results: list[tuple[int, str, str, list[str]]],
        next_cursor: int = 0,
        log_id: int | None = None,
        started: float | None = None,
    ) -> None:
        self._auto_refresh_running = False
        store_names = {
            store.id: store.name
            for store in self.db.list_stores()
        }
        try:
            self.db.set_setting("auto_refresh_cursor", str(next_cursor))
        except Exception:
            pass
        offline = sum(
            status in {"已掉线", "需重新扫码"}
            for _, status, _, _ in results
        )
        network = sum(
            status in {"网络异常", "连接异常", "平台异常"}
            for _, status, _, _ in results
        )
        sync_failures = sum(
            len(sync_errors)
            for _, _, _, sync_errors in results
        )
        detail_lines = []
        for store_id, status, detail, sync_errors in results:
            name = store_names.get(store_id, f"店铺 #{store_id}")
            detail_lines.append(f"{name}：{status} · {detail or '—'}")
            detail_lines.extend(
                f"{name}：{message}"
                for message in sync_errors
            )
        if results:
            message = f"自动轮转检查完成：本批 {len(results)} 家店铺"
            if offline:
                message += f"，{offline} 家已掉线"
            if network:
                message += f"，{network} 家连接异常"
            if sync_failures:
                message += f"，{sync_failures} 项数据同步失败"
            self.set_status(message)
        else:
            message = "自动轮转检查完成：没有待检查店铺"
        self._finish_task_log(
            log_id,
            started or time.monotonic(),
            (
                "partial"
                if offline or network or sync_failures
                else "success"
            ),
            message,
            "\n".join(detail_lines),
        )
        if self._active_page == "stores":
            self.store_page.refresh()
        elif self._active_page == "workbench":
            self.workbench_page.refresh()
        elif self._active_page == "orders":
            self.orders_page._query()
        elif self._active_page == "operations":
            self.operations_page._load()
        elif self._active_page == "analytics":
            self.analytics_page._load()
        self._refresh_nav_alerts()

    def _auto_refresh_failed(
        self,
        error: str,
        log_id: int | None = None,
        started: float | None = None,
    ) -> None:
        self._auto_refresh_running = False
        self.set_status(f"自动轮转检查失败：{error}")
        self._finish_task_log(
            log_id,
            started or time.monotonic(),
            "failed",
            error,
            f"自动轮转检查失败：{error}",
        )

    def _refresh_nav_alerts(self) -> None:
        if "stores" not in self._nav_buttons:
            return
        stores = self.db.list_stores()
        if hasattr(self, "header_store_var"):
            self.header_store_var.set(f"{len(stores)} 家店铺")
        offline = sum(
            store.web_status in {"已掉线", "需重新扫码"}
            for store in stores
        )
        label = "店铺管理"
        if offline:
            label = f"店铺管理   ●{offline}"
        self._nav_buttons["stores"].configure(text=label)


def show_fatal_error(message: str) -> None:
    root = tk.Tk()
    root.withdraw()
    messagebox.showerror("启动失败", message)
    root.destroy()

