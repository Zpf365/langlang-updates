from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtWidgets import QApplication

from weimi_manager.auth_client import LicenseStatus, SessionTokens
from weimi_manager.auth_service import AuthContext
from weimi_manager.config import AppPaths
from weimi_manager.pyside.task_manager import TaskManager
from weimi_manager.pyside.workspace import build_workspace


def _application() -> QApplication:
    return QApplication.instance() or QApplication([])


def _context() -> AuthContext:
    tokens = SessionTokens(
        user_id="account-1",
        account_id="account-1",
        username="pyside-test",
        email="pyside@example.invalid",
        access_token="access",
        refresh_token="refresh",
        expires_at="2099-01-01T00:00:00+00:00",
    )
    return AuthContext(
        tokens=tokens,
        license=LicenseStatus(
            active=True,
            permanent=True,
            expires_at=None,
            status_text="测试授权",
            remaining_days=999999,
            max_devices=1,
            device_bound=True,
            server_time="2026-01-01T00:00:00+00:00",
        ),
        device_hash="device",
        device_name="test",
    )


def test_workspace_reuses_the_legacy_database_and_services(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    paths = AppPaths(
        project_dir=tmp_path,
        resource_dir=tmp_path,
        data_dir=data_dir,
        database=data_dir / "weimi_manager.db",
    )
    application = _application()
    manager = TaskManager(max_workers=2)
    workspace = build_workspace(paths, _context(), manager)
    try:
        assert workspace.paths == paths
        assert workspace.db.path == paths.database
        assert workspace.service.db is workspace.db
        assert workspace.web_session.db is workspace.db
        assert paths.database.is_file()
    finally:
        workspace.close()
        manager.shutdown()
        application.processEvents()


def test_task_manager_runs_work_outside_the_ui_thread() -> None:
    application = _application()
    manager = TaskManager(max_workers=2)
    names: list[str] = []
    completed = threading.Event()

    task_id = manager.submit(lambda: names.append(threading.current_thread().name))
    assert task_id

    def finished(_task_id: str, _result: object) -> None:
        completed.set()

    manager.task_completed.connect(finished)
    deadline = threading.Event()
    for _ in range(200):
        application.processEvents()
        if completed.wait(0.01):
            break
        deadline.wait(0.01)
    assert completed.is_set()
    assert names
    assert names[0] != threading.main_thread().name
    manager.shutdown()
