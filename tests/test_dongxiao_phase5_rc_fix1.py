from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from weimi_manager.db import Database
from weimi_manager.dongxiao.application.preflight_service import (
    PLATFORM_READ_AUTHORIZATION,
    DongxiaoPreflightService,
)
from weimi_manager.dongxiao.application.runtime import DongxiaoRuntime
from weimi_manager.dongxiao.application.task_scheduler import (
    DongxiaoTaskScheduler,
)
from weimi_manager.dongxiao.application.task_service import (
    DongxiaoTaskService,
    PlatformWriteDisabled,
)
from weimi_manager.dongxiao.domain.enums import (
    AftersaleStatus,
    ApprovalStatus,
    ApprovalSubjectType,
    TaskItemStatus,
)
from weimi_manager.dongxiao.domain.models import AftersaleCase, Approval
from weimi_manager.dongxiao.domain.operations import (
    PreflightCheck,
    PreflightReport,
    PreflightState,
    TaskExecutionError,
)
from weimi_manager.dongxiao.infrastructure.executors import (
    AftersaleDecisionExecutor,
)
from weimi_manager.dongxiao.infrastructure.repository import DongxiaoRepository
from weimi_manager.service import SyncService
from weimi_manager.wechat_api import (
    WechatApiError,
    WechatNetworkError,
    WechatShopClient,
)


class _Transport:
    def __init__(
        self,
        *,
        token_result: dict | None = None,
        order_result: dict | None = None,
        aftersale_result: dict | None = None,
        product_result: dict | None = None,
        failure: BaseException | None = None,
    ) -> None:
        self.token_result = token_result or {
            "errcode": 0,
            "access_token": "TOKEN",
            "expires_in": 7200,
        }
        self.order_result = order_result or {
            "errcode": 0,
            "order_id_list": [],
            "has_more": False,
        }
        self.aftersale_result = aftersale_result or {
            "errcode": 0,
            "after_sale_order_id_list": [],
            "has_more": False,
        }
        self.product_result = product_result or {
            "errcode": 0,
            "product_ids": [],
            "next_key": "",
        }
        self.failure = failure
        self.calls: list[str] = []

    def post_json(self, url: str, payload: dict) -> dict:
        del payload
        self.calls.append(url)
        if self.failure is not None:
            raise self.failure
        if "/stable_token" in url:
            return dict(self.token_result)
        if "/product/list/get" in url:
            return dict(self.product_result)
        if "/order/list/get" in url:
            return dict(self.order_result)
        if "/aftersale/getaftersalelist" in url:
            return dict(self.aftersale_result)
        raise AssertionError(url)


class _SyncService:
    def __init__(
        self,
        *,
        app_id: str = "wx-phase5-fix1",
        transport: _Transport | None = None,
        client=None,
    ) -> None:
        self.transport = transport or _Transport()
        self.client = client or WechatShopClient(
            app_id,
            "s" * 32,
            transport=self.transport,
            min_request_interval=0,
        )

    def client_for(self, _store):
        return self.client


def _environment(
    tmp_path: Path,
    *,
    app_id: str = "wx-phase5-fix1",
) -> tuple[Database, object, Path]:
    db = Database(tmp_path / "phase5-fix1.db")
    store_id = db.add_store(
        "Phase5 Fix1 店铺",
        app_id,
        "s" * 32,
    )
    store = db.get_store(store_id)
    assert store is not None
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir(exist_ok=True)
    backup = backup_dir / "valid-backup.db"
    db.backup_to(backup)
    return db, store, backup


def _preflight(
    db: Database,
    sync_service,
    *,
    clock=None,
) -> tuple[DongxiaoRuntime, DongxiaoPreflightService]:
    runtime = DongxiaoRuntime(
        db.path,
        sync_service,
        clock=clock,
    )
    return runtime, runtime.preflight_service


def test_restore_rehearsal_uses_real_backup_and_cleans_temporary_copy(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db, _store, backup = _environment(tmp_path)
    runtime, service = _preflight(db, _SyncService())
    temp_root = tmp_path / "restore-temporary"

    def create_temp(**_kwargs) -> str:
        temp_root.mkdir()
        return str(temp_root)

    monkeypatch.setattr(
        "weimi_manager.dongxiao.application.preflight_service.tempfile.mkdtemp",
        create_temp,
    )
    before = hashlib.sha256(backup.read_bytes()).hexdigest()
    report = service.run_local(
        actor_id="tester",
        permission_checker=lambda _permission: True,
    )
    after = hashlib.sha256(backup.read_bytes()).hexdigest()
    states = {item.code: item.state for item in report.checks}

    assert states["backup_available"] is PreflightState.PASSED
    assert states["restore_rehearsal"] is PreflightState.PASSED
    assert before == after
    assert not temp_root.exists()
    runtime.stop()


def test_missing_empty_corrupt_and_incompatible_backups_fail_safely(
    tmp_path: Path,
) -> None:
    db, _store, backup = _environment(tmp_path)
    service = DongxiaoPreflightService(
        DongxiaoRepository(db.path),
        runtime=None,
        sync_service=_SyncService(),
        database_path=db.path,
        backup_dir=tmp_path / "backups",
    )

    empty = tmp_path / "backups" / "empty.db"
    empty.touch()
    assert not service._inspect_backup_database(empty)["valid"]

    corrupt = tmp_path / "backups" / "corrupt.db"
    corrupt.write_bytes(b"not sqlite")
    assert not service._inspect_backup_database(corrupt)["valid"]

    incompatible = tmp_path / "backups" / "incompatible.db"
    shutil.copy2(backup, incompatible)
    conn = sqlite3.connect(incompatible)
    try:
        conn.execute(
                "DELETE FROM dongxiao_schema_migrations WHERE version=10"
        )
        conn.commit()
    finally:
        conn.close()
    inspection = service._inspect_backup_database(incompatible)
    assert inspection["valid"] is False
    assert "版本" in str(inspection["reason"])

    backup.unlink()
    empty.unlink()
    corrupt.unlink()
    incompatible.unlink()
    found, reason = service._find_latest_valid_backup()
    assert found is None
    assert "没有有效备份" in reason


def test_restore_copy_interruption_fails_and_cleans_temporary_directory(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db, _store, _backup = _environment(tmp_path)
    service = DongxiaoPreflightService(
        DongxiaoRepository(db.path),
        runtime=None,
        sync_service=_SyncService(),
        database_path=db.path,
        backup_dir=tmp_path / "backups",
    )
    temp_root = tmp_path / "interrupted-restore"

    def create_temp(**_kwargs) -> str:
        temp_root.mkdir()
        return str(temp_root)

    def interrupt_copy(_source, _destination) -> None:
        raise OSError("simulated restore interruption")

    monkeypatch.setattr(
        "weimi_manager.dongxiao.application.preflight_service.tempfile.mkdtemp",
        create_temp,
    )
    monkeypatch.setattr(
        "weimi_manager.dongxiao.application.preflight_service.shutil.copy2",
        interrupt_copy,
    )
    backup, _error = service._find_latest_valid_backup()
    check = service._restore_rehearsal_check(backup)

    assert check.state is PreflightState.FAILED
    assert not temp_root.exists()


def test_newer_corrupt_backup_is_skipped_for_older_valid_backup(
    tmp_path: Path,
) -> None:
    db, _store, backup = _environment(tmp_path)
    corrupt = tmp_path / "backups" / "newer-corrupt.db"
    corrupt.write_bytes(b"broken")
    os.utime(backup, (1, 1))
    service = DongxiaoPreflightService(
        DongxiaoRepository(db.path),
        runtime=None,
        sync_service=_SyncService(),
        database_path=db.path,
        backup_dir=tmp_path / "backups",
    )

    found, reason = service._find_latest_valid_backup()

    assert found == backup.resolve()
    assert reason == ""


def test_demo_store_and_fake_clients_never_pass_real_preflight(
    tmp_path: Path,
) -> None:
    db = Database(tmp_path / "demo.db")
    store_id = db.add_store("演示店铺", "demo-store", "local")
    store = db.get_store(store_id)
    assert store is not None
    db.backup_to(tmp_path / "backups-demo.db")
    mock_service = SimpleNamespace(
        client_for=lambda _store: SimpleNamespace(
            fetch_order_ids=lambda *args, **kwargs: ["DEMO"]
        )
    )
    runtime = DongxiaoRuntime(db.path, mock_service)
    preflight = DongxiaoPreflightService(
        runtime.repository,
        runtime=runtime,
        sync_service=mock_service,
        database_path=db.path,
        backup_dir=tmp_path / "backups",
    )
    report = preflight.run_platform_readonly(
        store,
        actor_id="tester",
        permission_checker=lambda _permission: True,
        authorization_text=PLATFORM_READ_AUTHORIZATION,
    )
    states = {item.code: item.state for item in report.checks}

    assert states["store_authorization"] is PreflightState.NOT_VERIFIED
    assert states["product_api"] is PreflightState.NOT_VERIFIED
    assert states["order_api"] is PreflightState.NOT_VERIFIED
    assert states["aftersale_read_api"] is PreflightState.NOT_VERIFIED
    assert report.release_ready is False
    runtime.stop()


def test_fake_client_for_normal_app_id_is_not_treated_as_official(
    tmp_path: Path,
) -> None:
    db, store, _backup = _environment(tmp_path)
    fake = SimpleNamespace(
        get_access_token=lambda: "FAKE",
        fetch_product_id_page=lambda **_kwargs: SimpleNamespace(
            ids=["fake"],
            next_key="",
            has_more=False,
        ),
        fetch_order_ids=lambda *_args, **_kwargs: [],
        fetch_after_sale_ids=lambda *_args, **_kwargs: [],
    )
    sync_service = SimpleNamespace(client_for=lambda _store: fake)
    runtime, preflight = _preflight(db, sync_service)

    report = preflight.run_platform_readonly(
        store,
        actor_id="tester",
        permission_checker=lambda _permission: True,
        authorization_text=PLATFORM_READ_AUTHORIZATION,
    )
    states = {item.code: item.state for item in report.checks}

    assert states["store_authorization"] is PreflightState.NOT_VERIFIED
    assert states["product_api"] is PreflightState.NOT_VERIFIED
    assert states["order_api"] is PreflightState.NOT_VERIFIED
    assert states["aftersale_read_api"] is PreflightState.NOT_VERIFIED
    runtime.stop()


def test_token_failure_stops_before_product_order_and_aftersale_calls(
    tmp_path: Path,
) -> None:
    db, store, _backup = _environment(tmp_path)
    transport = _Transport(
        token_result={
            "errcode": 40013,
            "errmsg": "invalid appid",
        }
    )
    runtime, preflight = _preflight(
        db,
        _SyncService(transport=transport),
    )
    report = preflight.run_platform_readonly(
        store,
        actor_id="tester",
        permission_checker=lambda _permission: True,
        authorization_text=PLATFORM_READ_AUTHORIZATION,
    )
    states = {item.code: item.state for item in report.checks}

    assert states["store_authorization"] is PreflightState.MANUAL_AUTHORIZATION_REQUIRED
    assert states["product_api"] is PreflightState.NOT_VERIFIED
    assert states["order_api"] is PreflightState.NOT_VERIFIED
    assert states["aftersale_read_api"] is PreflightState.NOT_VERIFIED
    assert len(transport.calls) == 1
    assert "/stable_token" in transport.calls[0]
    runtime.stop()


@pytest.mark.parametrize(
    ("kind", "message"),
    (
        ("order", "order_id_list"),
        ("aftersale", "after_sale_order_id_list"),
    ),
)
def test_missing_required_list_fields_fail_instead_of_returning_empty(
    kind: str,
    message: str,
) -> None:
    transport = _Transport(
        order_result={"errcode": 0, "has_more": False},
        aftersale_result={"errcode": 0, "has_more": False},
    )
    client = WechatShopClient(
        "wx-fields",
        "s" * 32,
        transport=transport,
        min_request_interval=0,
    )

    with pytest.raises(WechatApiError, match=message):
        if kind == "order":
            client.fetch_order_ids(1, 2)
        else:
            client.fetch_after_sale_ids(1, 2)


def test_normal_empty_lists_and_paginated_lists_are_valid() -> None:
    empty = WechatShopClient(
        "wx-empty",
        "s" * 32,
        transport=_Transport(),
        min_request_interval=0,
    )
    assert empty.fetch_order_ids(1, 2) == []
    assert empty.fetch_after_sale_ids(1, 2) == []

    class PagedTransport(_Transport):
        def __init__(self) -> None:
            super().__init__()
            self.pages = 0

        def post_json(self, url: str, payload: dict) -> dict:
            if "/stable_token" in url:
                return super().post_json(url, payload)
            self.pages += 1
            if self.pages == 1:
                return {
                    "errcode": 0,
                    "order_id_list": ["o1", "o2"],
                    "has_more": True,
                    "next_key": "page-2",
                }
            assert payload["next_key"] == "page-2"
            return {
                "errcode": 0,
                "order_id_list": ["o3"],
                "has_more": False,
            }

    paged = WechatShopClient(
        "wx-paged",
        "s" * 32,
        transport=PagedTransport(),
        min_request_interval=0,
    )
    assert paged.fetch_order_ids(1, 2) == ["o1", "o2", "o3"]

    class MissingProductsTransport(_Transport):
        def post_json(self, url: str, payload: dict) -> dict:
            if "/product/list/get" in url:
                return {"errcode": 0, "next_key": ""}
            return super().post_json(url, payload)

    products = WechatShopClient(
        "wx-products",
        "s" * 32,
        transport=MissingProductsTransport(),
        min_request_interval=0,
    )
    with pytest.raises(WechatApiError, match="product_ids"):
        products.fetch_product_id_page(page_size=1)


def test_preflight_detects_network_and_official_error_codes(
    tmp_path: Path,
) -> None:
    db, store, _backup = _environment(tmp_path)
    runtime, preflight = _preflight(
        db,
        _SyncService(
            transport=_Transport(failure=WechatNetworkError("offline"))
        ),
    )
    report = preflight.run_platform_readonly(
        store,
        actor_id="tester",
        permission_checker=lambda _permission: True,
        authorization_text=PLATFORM_READ_AUTHORIZATION,
    )
    auth = next(
        item for item in report.checks
        if item.code == "store_authorization"
    )
    assert auth.state in {
        PreflightState.FAILED,
        PreflightState.MANUAL_AUTHORIZATION_REQUIRED,
    }
    runtime.stop()


def test_new_process_forces_platform_writes_off_and_audits(
    tmp_path: Path,
) -> None:
    db, _store, _backup = _environment(tmp_path)
    repository = DongxiaoRepository(db.path)
    repository.set_platform_writes_enabled(True, actor_id="previous-session")
    code = (
        "from weimi_manager.dongxiao.application.runtime import DongxiaoRuntime;"
        f"DongxiaoRuntime(r'{db.path}', object())"
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path.cwd())
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path.cwd(),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert repository.platform_writes_enabled() is False
    actions = [
        row["action"] for row in repository.list_audit_events()
    ]
    assert "platform_write_startup_reset" in actions


def test_repeated_runtime_in_same_process_does_not_reset_active_session(
    tmp_path: Path,
) -> None:
    db, _store, _backup = _environment(tmp_path)
    first = DongxiaoRuntime(db.path, _SyncService())
    first.repository.set_platform_writes_enabled(
        True,
        actor_id="first-runtime",
    )
    second = DongxiaoRuntime(db.path, _SyncService())

    assert second.platform_writes_enabled() is True
    first.stop()
    second.stop()


def test_enable_requires_current_store_verification_and_expires_after_ten_minutes(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 11, 19, 1, 0, tzinfo=UTC)
    db, store, _backup = _environment(tmp_path)
    runtime, preflight = _preflight(
        db,
        _SyncService(),
        clock=lambda: now,
    )
    report = preflight.run_platform_readonly(
        store,
        actor_id="tester",
        permission_checker=lambda _permission: True,
        authorization_text=PLATFORM_READ_AUTHORIZATION,
    )
    assert report.release_ready is True

    with pytest.raises(PermissionError):
        runtime.set_platform_writes_enabled(
            True,
            store=store,
            actor_id="denied",
            permission_checker=lambda _permission: False,
        )
    assert runtime.set_platform_writes_enabled(
        True,
        store=store,
        actor_id="owner",
        permission_checker=lambda _permission: True,
    )
    assert runtime.platform_write_gate(store.app_id) is True
    assert (
        preflight._write_guard_check(store.app_id).state
        is PreflightState.PASSED
    )
    other_store_id = db.add_store(
        "Phase5 Fix1 其他店铺",
        "wx-phase5-fix1-other",
        "o" * 32,
    )
    other_store = db.get_store(other_store_id)
    assert other_store is not None
    assert runtime.platform_write_gate(other_store.app_id) is False
    runtime.invalidate_platform_readonly_verification()
    assert runtime.platform_write_gate(store.app_id) is False

    runtime.set_platform_writes_enabled(
        False,
        actor_id="owner",
        permission_checker=lambda _permission: False,
        emergency=True,
    )
    report = preflight.run_platform_readonly(
        store,
        actor_id="tester",
        permission_checker=lambda _permission: True,
        authorization_text=PLATFORM_READ_AUTHORIZATION,
    )
    assert report.release_ready is True
    runtime.set_platform_writes_enabled(
        True,
        store=store,
        actor_id="owner",
        permission_checker=lambda _permission: True,
    )
    assert runtime.platform_write_gate(store.app_id) is True
    now = now + timedelta(seconds=601)
    runtime.clock = lambda: now
    assert runtime.platform_write_gate(store.app_id) is False
    runtime.stop()


def _passing_report(store, now: datetime) -> PreflightReport:
    codes = (
        ("store_authorization", "店铺授权"),
        ("product_api", "商品接口"),
        ("order_api", "订单接口"),
        ("aftersale_read_api", "售后读取"),
        ("database_integrity", "数据库"),
    )
    return PreflightReport(
        generated_at=now,
        store_app_id=store.app_id,
        checks=tuple(
            PreflightCheck(
                code=code,
                label=label,
                state=PreflightState.PASSED,
                message="通过",
            )
            for code, label in codes
        ),
    )


def test_store_write_switches_are_independent_and_emergency_close_clears_all(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 11, 19, 2, 0, tzinfo=UTC)
    db, first_store, _backup = _environment(tmp_path)
    second_id = db.add_store(
        "第二真实写店铺",
        "wx-phase5-fix1-second",
        "z" * 32,
    )
    second_store = db.get_store(second_id)
    assert second_store is not None
    runtime, _preflight_service = _preflight(
        db,
        _SyncService(),
        clock=lambda: now,
    )
    runtime.remember_platform_readonly_report(
        _passing_report(first_store, now)
    )
    runtime.set_platform_writes_enabled(
        True,
        store=first_store,
        actor_id="owner",
        permission_checker=lambda _permission: True,
    )
    runtime.remember_platform_readonly_report(
        _passing_report(second_store, now)
    )
    runtime.set_platform_writes_enabled(
        True,
        store=second_store,
        actor_id="owner",
        permission_checker=lambda _permission: True,
    )

    assert runtime.store_platform_writes_enabled(first_store.app_id) is True
    assert runtime.store_platform_writes_enabled(second_store.app_id) is True
    runtime.set_platform_writes_enabled(
        False,
        store=first_store,
        actor_id="owner",
        permission_checker=lambda _permission: True,
    )
    assert runtime.store_platform_writes_enabled(first_store.app_id) is False
    assert runtime.store_platform_writes_enabled(second_store.app_id) is True
    runtime.set_platform_writes_enabled(
        False,
        actor_id="owner",
        permission_checker=lambda _permission: True,
    )
    assert runtime.store_platform_writes_enabled(first_store.app_id) is False
    assert runtime.store_platform_writes_enabled(second_store.app_id) is False
    runtime.stop()


def test_new_process_resets_all_per_store_write_switches(
    tmp_path: Path,
) -> None:
    db, store, _backup = _environment(tmp_path)
    repository = DongxiaoRepository(db.path)
    repository.set_store_platform_writes_enabled(
        store.app_id,
        True,
        session_id="previous",
        actor_id="previous",
    )
    repository.set_platform_writes_enabled(True, actor_id="previous")
    code = (
        "from weimi_manager.dongxiao.application.runtime import "
        "DongxiaoRuntime;"
        f"DongxiaoRuntime(r'{db.path}', object())"
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path.cwd())
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path.cwd(),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert repository.store_platform_writes_enabled(store.app_id) is False
    assert repository.platform_writes_enabled() is False


def test_official_client_and_sync_service_block_writes_before_network(
    tmp_path: Path,
) -> None:
    db, store, _backup = _environment(tmp_path)
    calls: list[tuple[str, str]] = []

    def deny(app_id: str, action: str) -> None:
        calls.append((app_id, action))
        raise PermissionError("真实平台写操作已关闭")

    transport = _Transport()
    client = WechatShopClient(
        store.app_id,
        "s" * 32,
        transport=transport,
        min_request_interval=0,
        write_authorizer=deny,
    )
    with pytest.raises(PermissionError):
        client.set_product_listing("product-1", True)
    assert transport.calls == []
    assert calls[-1] == (store.app_id, "product_listing")

    service = SyncService(db)
    service.platform_write_authorizer = deny
    with pytest.raises(PermissionError):
        service.update_order_notes(store, "order-1", "test", 0)
    assert calls[-1] == (store.app_id, "order_notes_update")


def test_sync_service_fails_closed_without_runtime_authorizer(
    tmp_path: Path,
) -> None:
    db, store, _backup = _environment(tmp_path)
    service = SyncService(db)

    with pytest.raises(PermissionError, match="缺少当前会话安全门"):
        service.update_order_notes(store, "order-1", "test", 0)
    store.web_status = "已登录"
    with pytest.raises(PermissionError, match="缺少当前会话安全门"):
        service.apply_platform_activity(store, 1)


def test_official_client_fails_closed_without_write_authorizer() -> None:
    transport = _Transport()
    client = WechatShopClient(
        "wx-no-authorizer",
        "s" * 32,
        transport=transport,
        min_request_interval=0,
    )

    with pytest.raises(PermissionError, match="未连接当前会话写操作安全门"):
        client.set_product_listing("product-1", True)
    assert transport.calls == []


def test_write_api_never_retries_ambiguous_rate_limit_or_network_failure(
    tmp_path: Path,
) -> None:
    del tmp_path

    class AmbiguousTransport:
        def __init__(self) -> None:
            self.write_calls = 0

        def post_json(self, url: str, payload: dict) -> dict:
            del payload
            if "/stable_token" in url:
                return {
                    "errcode": 0,
                    "access_token": "TOKEN",
                    "expires_in": 7200,
                }
            self.write_calls += 1
            return {"errcode": 45011, "errmsg": "freq limit"}

    transport = AmbiguousTransport()
    client = WechatShopClient(
        "wx-write-safety",
        "s" * 32,
        transport=transport,
        min_request_interval=0,
        write_authorizer=lambda _app_id, _action: None,
    )
    with pytest.raises(WechatApiError) as exc_info:
        client.set_product_listing("product-1", True)
    assert exc_info.value.code == 45011
    assert transport.write_calls == 1

    class NetworkTransport(AmbiguousTransport):
        def post_json(self, url: str, payload: dict) -> dict:
            if "/stable_token" in url:
                return super().post_json(url, payload)
            self.write_calls += 1
            raise WechatNetworkError("connection dropped")

    network_transport = NetworkTransport()
    network_client = WechatShopClient(
        "wx-write-network",
        "s" * 32,
        transport=network_transport,
        min_request_interval=0,
        write_authorizer=lambda _app_id, _action: None,
    )
    with pytest.raises(WechatNetworkError):
        network_client.set_product_listing("product-1", True)
    assert network_transport.write_calls == 1


def test_idle_scheduler_passes_but_pending_work_without_thread_fails(
    tmp_path: Path,
) -> None:
    db, store, _backup = _environment(tmp_path)
    runtime, _preflight_service = _preflight(db, _SyncService())
    idle = runtime.scheduler_health()
    assert idle["healthy"] is True
    assert idle["idle"] is True

    now = datetime.now(UTC).isoformat()
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO dongxiao_tasks(
                task_id, store_id, store_app_id, task_type,
                idempotency_key, request_fingerprint, status,
                created_at, updated_at, execution_mode,
                total_items, max_attempts
            ) VALUES(
                'pending-scheduler-probe', ?, ?, 'order_verification',
                'pending-scheduler-probe', 'fingerprint', 'pending',
                ?, ?, 'official_read', 0, 3
            )
            """,
            (store.id, store.app_id, now, now),
        )
    pending = runtime.scheduler_health()
    assert pending["healthy"] is False
    assert pending["runnable_tasks"] is True
    runtime.stop()


def test_task_service_and_scheduler_use_store_specific_session_gate(
    tmp_path: Path,
) -> None:
    db, store, _backup = _environment(tmp_path)
    calls: list[str] = []
    service = DongxiaoTaskService(
        DongxiaoRepository(db.path),
        platform_write_gate=lambda store_app_id: (
            calls.append(store_app_id) or False
        ),
    )

    with pytest.raises(PlatformWriteDisabled):
        service.create_aftersale_decision_task(
            store,
            "case",
            action="accept",
            approved_amount_cents=100,
            idempotency_key="store-gate",
            actor_id="tester",
        )
    assert calls == [store.app_id]

    scheduler_calls: list[str] = []
    scheduler = DongxiaoTaskScheduler(
        DongxiaoRepository(db.path),
        object(),
        platform_write_gate=lambda store_app_id: (
            scheduler_calls.append(store_app_id) or False
        ),
    )
    assert scheduler._platform_write_allowed(store.app_id) is False
    assert scheduler_calls == [store.app_id]


def test_aftersale_executor_rechecks_session_verification(
    tmp_path: Path,
) -> None:
    db, store, _backup = _environment(tmp_path)
    repository = DongxiaoRepository(db.path)
    now = datetime.now(UTC)
    case = repository.upsert_aftersale_case(
        AftersaleCase(
            case_id="case-gate",
            store_id=store.id,
            store_app_id=store.app_id,
            platform_case_id="platform-case-gate",
            status=AftersaleStatus.APPROVED,
            requested_amount_cents=100,
            approved_amount_cents=100,
            idempotency_key="case-gate",
            created_at=now,
            updated_at=now,
            source="platform_api",
            platform_state=AftersaleStatus.PENDING_REVIEW,
            platform_status="MERCHANT_PROCESSING",
            after_sale_type="REFUND",
            paid_amount_cents=1000,
        )
    )
    repository.create_approval(
        Approval(
            approval_id="approval-gate",
            store_id=store.id,
            store_app_id=store.app_id,
            subject_type=ApprovalSubjectType.AFTERSALE,
            subject_id=case.case_id,
            status=ApprovalStatus.APPROVED,
            requested_by="requester",
            requested_at=now,
            decided_by="manager",
            decision_reason="approved",
            decided_at=now,
        )
    )
    adapter = SimpleNamespace(
        accept_aftersale=lambda *_args, **_kwargs: pytest.fail(
            "gate must stop before platform write"
        ),
        reject_aftersale=lambda *_args, **_kwargs: pytest.fail(
            "gate must stop before platform write"
        ),
    )
    aftersale_service = SimpleNamespace(
        preview_amount=lambda *_args, **_kwargs: None,
    )
    executor = AftersaleDecisionExecutor(
        repository,
        adapter,
        aftersale_service,
        platform_write_gate=lambda _store_app_id: False,
    )

    with pytest.raises(TaskExecutionError) as exc_info:
        executor.execute(
            SimpleNamespace(
                subject_type="aftersale",
                store_id=store.id,
                store_app_id=store.app_id,
                subject_id=case.case_id,
                payload={
                    "approved_amount_cents": 100,
                    "action": "accept",
                    "accept_type": 2,
                    "initial_platform_status": "MERCHANT_PROCESSING",
                },
                status=TaskItemStatus.PENDING,
            )
        )
    assert exc_info.value.code == "platform_write_not_allowed"


def test_preflight_report_includes_request_ids_for_official_checks(
    tmp_path: Path,
) -> None:
    db, store, _backup = _environment(tmp_path)
    runtime, preflight = _preflight(db, _SyncService())
    report = preflight.run_platform_readonly(
        store,
        actor_id="tester",
        permission_checker=lambda _permission: True,
        authorization_text=PLATFORM_READ_AUTHORIZATION,
    )
    official = [
        item
        for item in report.checks
        if item.code
        in {"store_authorization", "product_api", "order_api", "aftersale_read_api"}
    ]
    assert all("request_id=" in item.detail for item in official)
    runtime.stop()
