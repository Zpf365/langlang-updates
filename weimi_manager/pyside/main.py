from __future__ import annotations

import logging
import sys
import traceback

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication, QMessageBox

from ..auth_config import AuthConfig
from ..auth_service import build_auth_service
from ..config import AppPaths
from ..diagnostics import enable_faulthandler
from ..single_instance import SingleInstanceGuard
from ..tester_auth import TesterAuthService
from .auth_window import AuthWindow
from .logging_config import configure_logging
from .main_window import MainWindow
from .task_manager import TaskManager
from .theme_manager import ThemeManager
from .workspace import build_workspace, pull_account_stores


def _show_already_running() -> None:
    QMessageBox.information(
        None,
        "澜琅",
        "澜琅已经从当前文件夹启动。\n请切换到已打开的窗口。",
    )


def main() -> int:
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    application = QApplication(sys.argv)
    application.setApplicationName("澜琅")
    application.setOrganizationName("LangLang")
    application.setFont(QFont("Microsoft YaHei UI", 9))
    base_paths = AppPaths.discover()
    configure_logging(base_paths.data_dir)
    enable_faulthandler(base_paths.data_dir)
    guard = SingleInstanceGuard(base_paths.project_dir)
    if not guard.acquire():
        _show_already_running()
        return 0
    manager = TaskManager(max_workers=8)
    theme_manager = ThemeManager(
        application,
        base_paths.data_dir.parent / "pyside_preferences.json",
    )
    state: dict[str, object] = {
        "auth_window": None,
        "main_window": None,
        "workspace": None,
    }

    def auth_service():
        config = AuthConfig.load(base_paths)
        remote_service = (
            build_auth_service(config, base_paths.data_dir.parent / "auth")
            if config.configured
            else None
        )
        tester_path = base_paths.resource_dir / "assets" / "tester_accounts.json"
        universal_path = (
            base_paths.resource_dir / "assets" / "universal_login.json"
        )
        if tester_path.is_file() or universal_path.is_file():
            tester = TesterAuthService(
                base_paths.resource_dir,
                base_paths.data_dir,
                remote_service,
            )
            if tester.enabled:
                return tester, tester.client
        if remote_service is None:
            raise RuntimeError(
                "账号服务尚未配置。请提供 assets/auth_config.json 或 "
                "LANGLANG_SUPABASE_URL、LANGLANG_SUPABASE_PUBLISHABLE_KEY。"
            )
        return remote_service, remote_service.client

    try:
        service, auth_client = auth_service()
    except Exception as exc:
        QMessageBox.critical(None, "澜琅启动失败", str(exc))
        guard.close()
        return 2

    def show_login() -> None:
        previous = state.get("main_window")
        if isinstance(previous, MainWindow):
            previous.close()
            previous.deleteLater()
        previous_workspace = state.get("workspace")
        if previous_workspace is not None:
            try:
                previous_workspace.close()
            except Exception:
                logging.getLogger("langlang").exception("关闭旧工作区失败")
        state["main_window"] = None
        state["workspace"] = None
        auth_window = AuthWindow(service, manager)
        state["auth_window"] = auth_window
        auth_window.authenticated.connect(open_workspace)
        auth_window.closed.connect(application.quit)
        auth_window.show()

    def open_workspace(context) -> None:
        try:
            paths = base_paths.for_account(context.account_id)
            workspace = build_workspace(
                paths,
                context,
                manager,
                auth_client=auth_client,
                theme_manager=theme_manager,
            )
            window = MainWindow(
                workspace,
                lambda _context: show_login(),
                lambda: forget_and_logout(context),
            )
            window.account_switch_requested.connect(show_login)
            window.logout_requested.connect(lambda: forget_and_logout(context))
            state["workspace"] = workspace
            state["main_window"] = window
            window.show()
            if workspace.account_store_sync is not None:
                manager.submit(
                    pull_account_stores,
                    workspace,
                    key="account-store-pull",
                )
        except Exception as exc:
            logging.getLogger("langlang").exception("PySide6 工作台初始化失败")
            QMessageBox.critical(
                None,
                "工作台初始化失败",
                f"{exc}\n\n原数据库未被删除：{base_paths.data_dir}",
            )

    def forget_and_logout(context) -> None:
        try:
            service.logout(context, forget_local=True)
        except Exception:
            logging.getLogger("langlang").exception("退出登录失败")
        application.quit()

    show_login()
    try:
        return application.exec()
    except Exception:
        path = base_paths.data_dir / "pyside_startup_error.log"
        path.write_text(traceback.format_exc(), encoding="utf-8")
        raise
    finally:
        workspace = state.get("workspace")
        if workspace is not None:
            try:
                workspace.close()
            except Exception:
                logging.getLogger("langlang").exception("工作区清理失败")
        manager.shutdown()
        guard.close()


if __name__ == "__main__":
    raise SystemExit(main())
