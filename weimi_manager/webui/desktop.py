from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from ..runtime_logging import log_event
from .controller import NextUiController
from .server import FrontendServer


def _frontend_directory(resource_dir: Path) -> Path:
    candidates = (
        Path(resource_dir) / "webui" / "dist",
        Path(resource_dir) / "webui",
    )
    for candidate in candidates:
        if (candidate / "index.html").is_file():
            return candidate
    raise RuntimeError("未找到 Web UI 构建产物，请先在 webui 目录执行 npm run build")


def run_desktop(
    controller: NextUiController,
    *,
    resource_dir: Path,
    title: str = "澜琅 · 微信小店版",
) -> str:
    os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-features=UseSkiaRenderer")

    from PySide6.QtCore import QTimer, Qt, QUrl
    from PySide6.QtGui import QColor, QIcon
    from PySide6.QtWebEngineCore import QWebEngineProfile
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from PySide6.QtWidgets import QApplication, QMainWindow, QMessageBox

    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("澜琅")
    app.setOrganizationName("LangLang")

    static_dir = _frontend_directory(resource_dir)
    server = FrontendServer(static_dir, controller)
    server.start()
    controller.start_automation_scheduler()
    controller.start_activity_renewal_scheduler()
    controller.start_analytics_reporter()
    account_action: dict[str, object] = {"value": "", "ready_at": 0.0}

    profile = QWebEngineProfile.defaultProfile()
    profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.NoCache)

    class MainWindow(QMainWindow):
        def closeEvent(self, event) -> None:  # noqa: N802
            if (
                controller.update_install_in_progress()
                and account_action["value"] not in {
                    "update",
                    "switch",
                    "logout",
                }
            ):
                QMessageBox.warning(
                    self,
                    "正在更新",
                    "更新正在下载、校验或安装，请等待软件自动重启。",
                )
                event.ignore()
                return
            super().closeEvent(event)

    window = MainWindow()
    window.setWindowTitle(title)
    window.resize(1780, 1100)
    window.setMinimumSize(1100, 700)
    icon_path = Path(resource_dir) / "assets" / "icon.ico"
    if icon_path.is_file():
        window.setWindowIcon(QIcon(str(icon_path)))

    view = QWebEngineView(window)
    view.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
    page = view.page()
    page.setBackgroundColor(QColor("#f8f9fb"))
    window.setCentralWidget(view)
    window.show()
    view.show()

    def close_server() -> None:
        controller.stop_analytics_reporter()
        controller.stop_automation_scheduler()
        controller.stop_activity_renewal_scheduler()
        server.stop()

    def receive_account_action(action: str) -> None:
        # HTTP RPC runs on a worker thread. The QTimer below performs the
        # actual window close on the Qt main thread.
        account_action["value"] = action
        account_action["ready_at"] = time.monotonic() + 0.35

    controller.account_action_handler = receive_account_action

    def reveal_loaded_page(ok: bool) -> None:
        if not ok:
            return
        log_event("webui.page_loaded", url=server.base_url)
        window.showNormal()
        window.raise_()
        window.activateWindow()
        view.update()
        QTimer.singleShot(120, view.update)

    window.destroyed.connect(close_server)
    app.aboutToQuit.connect(close_server)
    page.loadFinished.connect(reveal_loaded_page)
    action_timer = QTimer(window)
    action_timer.setInterval(80)

    def close_for_account_action() -> None:
        if (
            not account_action["value"]
            or time.monotonic() < float(account_action["ready_at"])
        ):
            return
        action_timer.stop()
        window.close()

    action_timer.timeout.connect(close_for_account_action)
    action_timer.start()
    view.load(QUrl(server.entry_url))
    try:
        app.exec()
        return str(account_action["value"] or "closed")
    finally:
        controller.account_action_handler = None
        controller.stop_analytics_reporter()
        controller.stop_automation_scheduler()
        controller.stop_activity_renewal_scheduler()
        server.stop()
        profile.clearHttpCache()
        profile.cookieStore().deleteAllCookies()
