from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from .. import __version__
from ..hot_update_store import HotUpdateStore
from ..runtime_logging import log_event
from ..update_health import update_root_for_install
from ..update_protocol import load_trust_config
from ..update_rules import rule_validators
from ..update_runtime import (
    HotUpdateRuntime,
    fetch_signed_channel,
    load_or_create_install_id,
)
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

    from PySide6.QtCore import Qt, QTimer, QUrl
    from PySide6.QtGui import QColor, QIcon
    from PySide6.QtWebEngineCore import QWebEngineProfile
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from PySide6.QtWidgets import QApplication, QMainWindow, QMessageBox

    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("澜琅")
    app.setOrganizationName("LangLang")

    embedded_static_dir = _frontend_directory(resource_dir)
    trust = load_trust_config(resource_dir)
    hot_store: HotUpdateStore | None = None
    if trust.enabled:
        hot_updates_root = update_root_for_install(controller.project_dir)
        hot_store = HotUpdateStore(
            hot_updates_root / "hot",
            public_keys=trust.public_keys,
            current_version=__version__,
            backend_contract=1,
            install_id=load_or_create_install_id(hot_updates_root),
            rule_validators=rule_validators,
            lock_root=hot_updates_root,
        )

    def active_frontend_root() -> Path:
        if hot_store is None:
            return embedded_static_dir
        return hot_store.active_root("webui", embedded_static_dir)

    server = FrontendServer(active_frontend_root, controller)
    server.start()
    controller.start_automation_scheduler()
    controller.start_activity_renewal_scheduler()
    controller.start_analytics_reporter()
    account_action: dict[str, object] = {"value": "", "ready_at": 0.0}
    reload_requested = {"value": False}
    ready_probe = {
        "generation": 0,
        "deadline": 0.0,
        "settled": False,
    }

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
        if hot_runtime is not None:
            hot_runtime.stop()
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

    def mark_webui_unhealthy() -> None:
        if hot_runtime is None or hot_store is None:
            return
        if not hot_store.active_patch_id("webui"):
            return
        if hot_runtime.mark_webui_ready(False):
            view.reload()

    def start_ready_probe(ok: bool) -> None:
        if not ok:
            mark_webui_unhealthy()
            return
        ready_probe["generation"] = int(ready_probe["generation"]) + 1
        generation = int(ready_probe["generation"])
        ready_probe["deadline"] = time.monotonic() + 15.0
        ready_probe["settled"] = False

        def probe() -> None:
            if generation != int(ready_probe["generation"]):
                return
            if time.monotonic() > float(ready_probe["deadline"]):
                if not bool(ready_probe["settled"]):
                    log_event("webui.hot_update.ready_timeout")
                    mark_webui_unhealthy()
                return

            def receive(value: object) -> None:
                if generation != int(ready_probe["generation"]):
                    return
                if bool(value):
                    if not bool(ready_probe["settled"]):
                        ready_probe["settled"] = True
                        if hot_runtime is not None:
                            hot_runtime.mark_webui_ready(True)
                    return
                QTimer.singleShot(120, probe)

            page.runJavaScript(
                "document.documentElement.dataset.langlangReady === '1'",
                receive,
            )

        QTimer.singleShot(0, probe)

    def request_webui_reload() -> None:
        reload_requested["value"] = True

    hot_runtime: HotUpdateRuntime | None = None
    if hot_store is not None and trust.channel_url:
        hot_runtime = HotUpdateRuntime(
            hot_store,
            channel_loader=lambda: fetch_signed_channel(trust.channel_url),
            busy_provider=controller.hot_update_busy,
            reload_handler=request_webui_reload,
            log_callback=lambda event, detail="": log_event(
                event,
                detail=detail,
            ),
        )
    elif trust.enabled:
        log_event("hotupdate.blocked", error=trust.blocker)

    window.destroyed.connect(close_server)
    app.aboutToQuit.connect(close_server)
    def handle_page_loaded(ok: bool) -> None:
        reveal_loaded_page(ok)
        start_ready_probe(ok)

    page.loadFinished.connect(handle_page_loaded)
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
    update_timer = QTimer(window)
    update_timer.setInterval(100)

    def apply_requested_reload() -> None:
        if not bool(reload_requested["value"]):
            return
        reload_requested["value"] = False
        view.reload()

    update_timer.timeout.connect(apply_requested_reload)
    update_timer.start()
    view.load(QUrl(server.entry_url))
    if hot_runtime is not None:
        QTimer.singleShot(5000, hot_runtime.start)
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
