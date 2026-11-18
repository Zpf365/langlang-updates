from __future__ import annotations

import ctypes
import json
import logging
import os
import sys
import traceback
from datetime import UTC, datetime
from pathlib import Path

from .auth_client import AuthError
from .auth_config import AuthConfig
from .auth_service import AuthContext, build_auth_service
from .config import AppPaths
from .diagnostics import enable_faulthandler, redact_text
from .single_instance import SingleInstanceGuard
from .tester_auth import TesterAuthService
from .ui.app import MainWindow, show_fatal_error
from .ui.auth_window import AuthWindow
from .ui.store_page import StorePage as RuntimeStorePage


def _enable_windows_dpi_awareness() -> None:
    if sys.platform == "win32":
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass


def _show_already_running() -> None:
    if sys.platform == "win32":
        ctypes.windll.user32.MessageBoxW(
            0,
            "澜琅已经从当前文件夹启动。\n"
            "请切换到已打开的窗口，避免重复运行。",
            "澜琅",
            0x40,
        )


def _force_store_page_startup(window: MainWindow, paths: AppPaths) -> None:
    record: dict[str, object] = {"database": str(paths.database)}
    try:
        stores = window.db.list_stores(
            on_database_opened=lambda filename: record.update(sqlite_database=filename),
        )
        record["store_count"] = len(stores)
        page = window.store_page
        if not isinstance(page, RuntimeStorePage):
            old_page = page
            page = RuntimeStorePage(window.page_host, window)
            window.store_page = page
            window._pages["stores"] = page
            try:
                old_page.destroy()
            except Exception:
                pass
        window.show_page("stores")
        page.refresh()
        window.update_idletasks()
        record["store_page_class"] = f"{type(page).__module__}.{type(page).__name__}"
        record["rows"] = len(page.tree.get_children())
    except Exception as exc:
        record["error"] = redact_text(f"{type(exc).__name__}: {exc}")
        raise
    finally:
        _write_store_startup_log(paths.data_dir, "startup_refresh", **record)


def _write_store_startup_log(data_dir: Path, event: str, **detail: object) -> None:
    try:
        record = {
            "at": datetime.now(UTC).isoformat(timespec="seconds"),
            "pid": os.getpid(),
            "event": event,
            **detail,
        }
        log_path = data_dir / "startup_store_refresh.log"
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as exc:
        logging.getLogger(__name__).warning(
            "Could not write store startup log: %s", redact_text(str(exc))
        )


def _allow_unconfigured_demo() -> bool:
    """Allow the local demo only when a source checkout explicitly opts in."""
    if getattr(sys, "frozen", False):
        return False
    value = os.environ.get(
        "LANGLANG_ALLOW_UNCONFIGURED_DEMO",
        "",
    ).strip().casefold()
    return value in {"1", "true", "yes", "on"}


def _resume_saved_tester_account(service: TesterAuthService) -> AuthContext | None:
    for argument in sys.argv[1:]:
        option, _, username = argument.partition("=")
        if option == "--resume-saved-account" and username:
            saved = service.store.find(username)
            if saved is not None and saved.password:
                try:
                    context = service.login(saved.username, saved.password, False)
                except AuthError:
                    return None
                if context.account_id == saved.account_id:
                    return context
            return None
    return None


def main() -> int:
    _enable_windows_dpi_awareness()
    instance_guard: SingleInstanceGuard | None = None
    try:
        base_paths = AppPaths.discover()
        enable_faulthandler(base_paths.data_dir)
        instance_guard = SingleInstanceGuard(base_paths.project_dir)
        if not instance_guard.acquire():
            _show_already_running()
            return 0
        config = AuthConfig.load(base_paths)
        service = None
        auth_context: AuthContext | None = None
        auth_client = None
        remote_service = (
            build_auth_service(config, base_paths.data_dir.parent / "auth")
            if config.configured
            else None
        )
        if isinstance(base_paths.resource_dir, Path) and (
            (
                base_paths.resource_dir / "assets" / "tester_accounts.json"
            ).is_file()
            or (
                base_paths.resource_dir / "assets" / "universal_login.json"
            ).is_file()
        ):
            tester_service = TesterAuthService(
                base_paths.resource_dir,
                base_paths.data_dir,
                remote_service,
            )
            if tester_service.enabled:
                service = tester_service
                auth_context = _resume_saved_tester_account(tester_service)
        if service is None and remote_service is not None:
            service = remote_service
        auth_client = getattr(service, "client", None) if service is not None else None
        if service is None and not _allow_unconfigured_demo():
            raise RuntimeError(
                "发行版账号授权服务未配置，已拒绝进入工作台。"
                "源码开发环境如需演示，请显式设置 "
                "LANGLANG_ALLOW_UNCONFIGURED_DEMO=1。"
            )
        while True:
            if service is not None and auth_context is None:
                auth_window = AuthWindow(base_paths, service)  # type: ignore[arg-type]
                auth_window.mainloop()
                auth_context = auth_window.result
                if auth_context is None:
                    return 0
            paths = (
                base_paths.for_account(auth_context.account_id)
                if auth_context is not None
                else base_paths
            )
            _write_store_startup_log(
                base_paths.data_dir,
                "database_selected",
                account_data_dir=str(paths.data_dir),
                database=str(paths.database),
            )
            window = (
                MainWindow(paths, auth_context=auth_context, auth_client=auth_client)
                if auth_client is not None
                else MainWindow(paths, auth_context=auth_context)
            )
            if service is not None:
                window.saved_account_store = service.store
            _force_store_page_startup(window, paths)
            window.mainloop()
            if service is None or window.return_to_login is not True:
                return 0
            auth_context = None
    except Exception:
        details = traceback.format_exc()
        log_path = AppPaths.discover().data_dir / "startup_error.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(details, encoding="utf-8")
        show_fatal_error(f"程序启动失败，详情已写入：\n{log_path}")
        return 1
    finally:
        if instance_guard is not None:
            instance_guard.close()


if __name__ == "__main__":
    raise SystemExit(main())
