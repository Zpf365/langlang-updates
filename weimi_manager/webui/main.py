from __future__ import annotations

import json
import sys
import tempfile
import os
import subprocess
import ctypes
import time
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from .. import __version__
from ..config import AppPaths
from ..db import Database
from ..models import Order, OrderItem
from ..runtime_logging import (
    configure_runtime_logging,
    get_runtime_log_manager,
    log_event,
)
from ..single_instance import SingleInstanceGuard
from ..update_health import write_update_health
from .controller import NextUiController
from .desktop import run_desktop


def _acceptance_saved_account(
    arguments: list[str],
    base_paths: AppPaths,
) -> str:
    flag = "--acceptance-saved-account"
    if flag not in arguments:
        return ""
    if os.environ.get("LANGLANG_ACCEPTANCE_MODE") != "candidate-exe":
        raise RuntimeError(
            "候选验收账号入口必须显式设置 LANGLANG_ACCEPTANCE_MODE=candidate-exe"
        )
    if not any(
        part.casefold() == "candidate-exe"
        for part in base_paths.data_dir.resolve().parts
    ):
        raise RuntimeError(
            "候选验收账号入口只允许隔离的 candidate-exe 数据目录"
        )
    index = arguments.index(flag)
    if index + 1 >= len(arguments):
        raise RuntimeError(f"{flag} 缺少账号名称")
    username = str(arguments[index + 1]).strip()
    if not username:
        raise RuntimeError(f"{flag} 缺少账号名称")
    return username


def _smoke_controller() -> tuple[NextUiController, Path]:
    root = Path(tempfile.mkdtemp(prefix="langlang-webui-smoke-"))
    database = Database(root / "weimi_manager.db")
    stores = []
    for index in range(1, 7):
        store_id = database.add_store(
            f"界面测试店铺 {index}",
            f"wx-webui-demo-{index:02d}",
            "demo-secret",
            "界面验收",
        )
        database.update_web_status(
            store_id,
            "已登录" if index <= 3 else "未登录",
            "",
        )
        current = {
            "gmv_today": 5696 + index,
            "gmv_yesterday": 5696 - index,
            "gmv_7d": 14282 + index,
            "gmv_30d": 14282 + index,
            "cash": 300 + index,
            "settle": 57928 + index,
            "dsr": 4.8 + index / 100,
            "score_product": 4.7 - index / 100,
            "score_deliver": 4.6,
            "score_service": 4.9,
            "stats_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        previous = {
            **current,
            "dsr": current["dsr"] - 0.1,
            "score_product": current["score_product"] + 0.1,
        }
        database.set_setting(
            f"store_metrics_{store_id}",
            json.dumps(current, ensure_ascii=False),
        )
        database.set_setting(
            f"webui_previous_store_metrics_{store_id}",
            json.dumps(previous, ensure_ascii=False),
        )
        today = datetime.now().date()
        yesterday = today - timedelta(days=1)
        database.set_setting(
            f"webui_store_score_state_{store_id}",
            json.dumps(
                {
                    "version": 3,
                    "values": {},
                    "changes": {},
                    "daily": {
                        yesterday.isoformat(): {
                            key: {
                                "value": previous[key],
                                "updated_at": (
                                    f"{yesterday.isoformat()} 22:30:00"
                                ),
                            }
                            for key in (
                                "dsr",
                                "score_product",
                                "score_deliver",
                                "score_service",
                            )
                        }
                    },
                    "refresh": {},
                    "last_attempt_at": "",
                },
                ensure_ascii=False,
            ),
        )
        stores.append((store_id, f"界面测试店铺 {index}"))
    now = int(time.time())
    for index, (store_id, store_name) in enumerate(stores[:4], start=1):
        status = 20 if index % 2 else 30
        database.upsert_order(
            Order(
                store_id=store_id,
                store_name=store_name,
                order_id=f"SMOKE-ORDER-{index:03d}",
                status=status,
                status_text="买家已付款" if status == 20 else "卖家已发货",
                create_time=now - index * 3600,
                update_time=now - index * 1800,
                pay_time=now - index * 3500,
                total_amount=2160 + index * 137,
                product_amount=2160 + index * 137,
                freight_amount=0,
                discount_amount=0,
                buyer_name="测试买家",
                phone="13800138000",
                address="广东省揭阳市普宁市测试地址 1 号",
                waybill_id="" if index % 2 else f"YT2026112100{index}",
                carrier="" if index % 2 else "圆通速递",
                merchant_notes="此单不发货" if index == 4 else "",
                items=[
                    OrderItem(
                        product_id=f"10001956{index:04d}",
                        sku_id=f"SKU-{index}",
                        title=f"界面验收商品 {index}：冬季保暖商品测试标题",
                        sku_count=1,
                        sale_price=2160 + index * 137,
                    )
                ],
                raw={},
            )
        )
    controller = NextUiController(
        database,
        native_action=_legacy_workbench_action(root),
        auth_context=SimpleNamespace(
            account_id="220a9616-2346-441b-9e3f-f5f4f5b824fd",
            username="zpfly",
            member_role="owner",
            permissions={},
            offline=False,
        ),
    )
    return controller, root


def _legacy_workbench_action(
    data_dir: Path,
):
    def open_workbench(_action: str, _payload: dict) -> dict:
        environment = os.environ.copy()
        environment["WEIMI_DATA_DIR"] = str(data_dir)
        if getattr(sys, "frozen", False):
            command = [sys.executable, "--legacy-workbench"]
        else:
            command = [
                sys.executable,
                "-m",
                "weimi_manager.webui.legacy_workbench",
            ]
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        process = subprocess.Popen(
            command,
            env=environment,
            creationflags=flags,
        )
        return {"opened": True, "pid": process.pid}

    return open_workbench


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    base_paths = AppPaths.discover()
    manager = get_runtime_log_manager() or configure_runtime_logging(
        base_paths.project_dir
    )
    manager.update_session(
        data_dir=str(base_paths.data_dir),
        resource_dir=str(base_paths.resource_dir),
    )
    if "--smoke-data" in arguments:
        controller, _data_dir = _smoke_controller()
        run_desktop(controller, resource_dir=base_paths.resource_dir)
        return 0

    if "--legacy-workbench" in arguments:
        from .legacy_workbench import main as legacy_main

        return legacy_main()

    from ..auth_config import AuthConfig
    from ..auth_service import build_auth_service
    from ..ui.auth_window import AuthWindow

    guard = SingleInstanceGuard(base_paths.project_dir)
    if not guard.acquire():
        if sys.platform == "win32":
            ctypes.windll.user32.MessageBoxW(
                0,
                "澜琅已经从当前文件夹启动。\n请切换到已打开的窗口。",
                "澜琅",
                0x40,
            )
        return 0
    try:
        config = AuthConfig.load(base_paths)
        service = build_auth_service(
            config,
            base_paths.data_dir.parent / "auth",
        )
        if service is None:
            raise RuntimeError("发行版账号授权服务未配置。")
        if getattr(sys, "frozen", False):
            try:
                write_update_health(base_paths.project_dir, __version__)
            except OSError as exc:
                log_event("update.health.write_failed", error=str(exc))

        def run_account_webui(auth_context) -> str:
            paths = base_paths.for_account(auth_context.account_id)
            manager.update_session(
                account=auth_context.username,
                account_id=auth_context.account_id,
                data_dir=str(paths.data_dir),
            )
            log_event(
                "auth.account_entered",
                account=auth_context.username,
                account_id=auth_context.account_id,
                data_dir=str(paths.data_dir),
            )
            from ..web_session import WechatWebSession
            from ..service import SyncService
            from ..team_service import TeamService

            database = Database(paths.database)
            team_service = TeamService(
                service.client,
                auth_context.tokens.access_token,
            )
            controller = NextUiController(
                database,
                account_label=auth_context.username,
                project_dir=getattr(
                    paths,
                    "project_dir",
                    base_paths.project_dir,
                ),
                web_session=WechatWebSession(database, paths.data_dir),
                product_service=SyncService(database),
                native_action=_legacy_workbench_action(paths.data_dir),
                auth_context=auth_context,
                auth_client=service.client,
                auth_service=service,
                team_service=team_service,
            )
            result = run_desktop(
                controller,
                resource_dir=paths.resource_dir,
                title=f"澜琅 · 微信小店版 {auth_context.username or ''}".strip(),
            )
            log_event(
                "webui.desktop_finished",
                result=result,
                account=auth_context.username,
            )
            return result

        acceptance_username = _acceptance_saved_account(
            arguments,
            base_paths,
        )
        if acceptance_username:
            saved = next(
                (
                    account
                    for account in service.store.list_accounts()
                    if str(account.username).casefold()
                    == acceptance_username.casefold()
                ),
                None,
            )
            if saved is None:
                raise RuntimeError(
                    f"隔离验收账号不存在：{acceptance_username}"
                )
            acceptance_context = service.restore_account(saved)
            run_account_webui(acceptance_context)
            return 0

        while True:
            auth_window = AuthWindow(base_paths, service)
            auth_window.mainloop()
            auth_context = auth_window.result
            if auth_context is None:
                return 0
            result = run_account_webui(auth_context)
            if result == "closed":
                return 0
            if result == "update":
                log_event(
                    "webui.update_process_exit",
                    account=auth_context.username,
                )
                return 0
            # switch and logout both return to the login window. Logout has
            # already removed the saved local account in the controller.
    finally:
        guard.close()
