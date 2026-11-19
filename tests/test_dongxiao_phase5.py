from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from weimi_manager.cloud_commands import AuthorizedServiceAdapter
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
from weimi_manager.dongxiao.domain.operations import PreflightState
from weimi_manager.dongxiao.infrastructure.repository import DongxiaoRepository
from weimi_manager.wechat_api import WechatApiError, WechatShopClient


class _FakeClient:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def get_access_token(self) -> str:
        self.calls.append("get_access_token")
        return "READ-ONLY-TOKEN"

    def fetch_product_id_page(self, **kwargs):
        self.calls.append(f"product:{kwargs['page_size']}")
        return SimpleNamespace(ids=["product-1"], next_key="", has_more=False)

    def fetch_order_ids(
        self,
        start_time: int,
        end_time: int,
        status: int | None = None,
    ) -> list[str]:
        self.calls.append(f"orders:{start_time}:{end_time}:{status}")
        return ["order-1"]

    def fetch_after_sale_ids(
        self,
        start_time: int,
        end_time: int,
        *,
        time_field: str = "create",
    ) -> list[str]:
        self.calls.append(
            f"aftersales:{start_time}:{end_time}:{time_field}"
        )
        return ["aftersale-1"]

    def accept_after_sale(self, payload):
        raise AssertionError("只读检查不得执行售后同意")

    def reject_after_sale(self, payload):
        raise AssertionError("只读检查不得执行售后拒绝")


class _FakeSyncService:
    def __init__(self) -> None:
        self.client = _FakeClient()

    def client_for(self, store):
        del store
        return self.client


class _OfficialTransport:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def post_json(self, url: str, payload: dict) -> dict:
        self.calls.append(url)
        if "/stable_token" in url:
            return {
                "errcode": 0,
                "access_token": "READ-ONLY-TOKEN",
                "expires_in": 7200,
            }
        if "/product/list/get" in url:
            return {
                "errcode": 0,
                "product_ids": [],
                "next_key": "",
            }
        if "/order/list/get" in url:
            return {
                "errcode": 0,
                "order_id_list": [],
                "has_more": False,
            }
        if "/aftersale/getaftersalelist" in url:
            return {
                "errcode": 0,
                "after_sale_order_id_list": [],
                "has_more": False,
            }
        raise AssertionError(f"unexpected official stress URL: {url}")


class _OfficialSyncService:
    def __init__(self) -> None:
        self.transport = _OfficialTransport()
        self.client = WechatShopClient(
            "wx-phase5-test",
            "s" * 32,
            transport=self.transport,
            min_request_interval=0,
        )

    def client_for(self, _store):
        return self.client


def _create_environment(tmp_path: Path) -> tuple[Database, object]:
    db = Database(tmp_path / "langlang-phase5.db")
    store_id = db.add_store(
        "Phase5 测试店铺",
        "wx-phase5-test",
        "s" * 32,
    )
    store = db.get_store(store_id)
    assert store is not None
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    db.backup_to(backup_dir / "phase5-baseline.db")
    return db, store


def _insert_running_task(
    db: Database,
    store,
    *,
    task_id: str,
    execution_mode: str,
    status: str = "running",
) -> None:
    now = "2026-11-18T10:00:00+00:00"
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO dongxiao_tasks(
                task_id, store_id, store_app_id, task_type,
                idempotency_key, request_fingerprint, status,
                created_at, updated_at, execution_mode,
                total_items, max_attempts
            ) VALUES(?, ?, ?, 'aftersale_decision', ?, 'fingerprint',
                     ?, ?, ?, ?, 1, 1)
            """,
            (
                task_id,
                int(store.id),
                str(store.app_id),
                f"idem-{task_id}",
                status,
                now,
                now,
                execution_mode,
            ),
        )
        conn.execute(
            """
            INSERT INTO dongxiao_task_execution_items(
                item_id, task_id, store_id, store_app_id,
                subject_type, subject_id, status, attempt_count,
                max_attempts, created_at, updated_at
            ) VALUES(?, ?, ?, ?, 'aftersale', 'case-1',
                     'pending', 0, 1, ?, ?)
            """,
            (
                f"item-{task_id}",
                task_id,
                int(store.id),
                str(store.app_id),
                now,
                now,
            ),
        )


def test_api_call_observer_redacts_tokens_urls_and_cookies() -> None:
    observed: list[dict[str, object]] = []

    class FailingTransport:
        @staticmethod
        def post_json(url: str, payload: dict) -> dict:
            del payload
            raise RuntimeError(
                f"请求失败 {url} Cookie: session-value "
                "AppSecret=super-secret-value"
            )

    client = WechatShopClient(
        "wx-observer",
        "super-secret-value",
        transport=FailingTransport(),
        call_observer=observed.append,
        min_request_interval=0,
    )
    client.get_access_token = lambda force_refresh=False: "secret-token-value"

    with pytest.raises(RuntimeError):
        client.fetch_product(SimpleNamespace(app_id="wx-observer"), "product-1")

    assert len(observed) == 1
    payload = observed[0]
    assert payload["path"] == "/channels/ec/product/get"
    assert "?" not in str(payload["path"])
    combined = repr(payload)
    for secret in (
        "secret-token-value",
        "session-value",
        "super-secret-value",
        "https://",
    ):
        assert secret not in combined


def test_product_pagination_rejects_missing_cursor_and_classifies_errors() -> None:
    class MissingCursorTransport:
        @staticmethod
        def post_json(url: str, payload: dict) -> dict:
            del url
            assert payload["page_size"] == 1
            return {
                "errcode": 0,
                "product_ids": ["product-1"],
                "has_more": True,
            }

    client = WechatShopClient(
        "wx-pagination",
        "s" * 32,
        transport=MissingCursorTransport(),
        min_request_interval=0,
    )
    client.get_access_token = lambda force_refresh=False: "token"

    with pytest.raises(WechatApiError, match="缺少 next_key"):
        client.fetch_product_id_page(page_size=1)

    assert WechatApiError("expired", code=42001).category == "authentication"
    assert WechatApiError("limited", code=45011).category == "rate_limit"
    assert WechatApiError("denied", code=48001).category == "permission"


def test_runtime_attaches_api_observer_through_authorized_adapter(
    tmp_path: Path,
) -> None:
    db, _store = _create_environment(tmp_path)
    base_service = _FakeSyncService()
    adapter = AuthorizedServiceAdapter(base_service, SimpleNamespace())

    runtime = DongxiaoRuntime(db.path, adapter)

    assert callable(getattr(base_service, "call_observer", None))
    runtime.stop()


def test_task_lease_claim_updates_heartbeat(tmp_path: Path) -> None:
    db, store = _create_environment(tmp_path)
    _insert_running_task(
        db,
        store,
        task_id="task-heartbeat",
        execution_mode="local_safe",
    )
    repository = DongxiaoRepository(db.path)

    assert repository.claim_task_lease(
        "task-heartbeat",
        store_app_id=store.app_id,
        owner="worker-heartbeat",
    )
    task = repository.get_task(
        "task-heartbeat",
        store_app_id=store.app_id,
    )

    assert task is not None
    assert task.lease_owner == "worker-heartbeat"
    assert task.last_heartbeat_at is not None
    assert task.lease_expires_at is not None


def test_platform_write_switch_defaults_off_and_is_audited(
    tmp_path: Path,
) -> None:
    db, _store = _create_environment(tmp_path)
    repository = DongxiaoRepository(db.path)

    assert repository.platform_writes_enabled() is False
    assert repository.set_platform_writes_enabled(
        True,
        actor_id="owner-test",
    )
    assert repository.platform_writes_enabled() is True
    assert repository.set_platform_writes_enabled(
        False,
        actor_id="owner-test",
    ) is False
    assert repository.platform_writes_enabled() is False
    actions = [
        row["action"] for row in repository.list_audit_events()
    ]
    assert actions.count("platform_write_guard_changed") == 2


def test_task_service_gate_blocks_new_platform_write_task(
    tmp_path: Path,
) -> None:
    db, store = _create_environment(tmp_path)
    service = DongxiaoTaskService(
        DongxiaoRepository(db.path),
        platform_write_gate=lambda _store_app_id: False,
    )

    with pytest.raises(PlatformWriteDisabled):
        service.create_aftersale_decision_task(
            store,
            "case-1",
            action="accept",
            approved_amount_cents=100,
            idempotency_key="phase5-disabled",
            actor_id="tester",
        )


def test_scheduler_does_not_dispatch_platform_write_when_gate_closed(
    tmp_path: Path,
) -> None:
    db, store = _create_environment(tmp_path)
    _insert_running_task(
        db,
        store,
        task_id="task-gated-write",
        execution_mode="platform_write",
        status="pending",
    )
    calls: list[str] = []

    class Executor:
        @staticmethod
        def execute(item):
            calls.append(item.item_id)
            return {}

        @staticmethod
        def verify(item):
            calls.append(item.item_id)
            return {}

    scheduler = DongxiaoTaskScheduler(
        DongxiaoRepository(db.path),
        Executor(),
        platform_write_gate=lambda _store_app_id: False,
        poll_interval_seconds=0.02,
    )
    assert scheduler.run_until_idle(timeout=0.15) is False
    assert calls == []


def test_local_preflight_distinguishes_passed_unverified_and_authorization(
    tmp_path: Path,
) -> None:
    db, _store = _create_environment(tmp_path)
    runtime = DongxiaoRuntime(db.path, _FakeSyncService())
    try:
        report = runtime.preflight_service.run_local(
            actor_id="tester",
            permission_checker=lambda _permission: True,
        )
    finally:
        runtime.stop()

    states = {item.code: item.state for item in report.checks}
    assert states["database_integrity"] is PreflightState.PASSED
    assert states["migration_version"] is PreflightState.PASSED
    assert states["backup_available"] is PreflightState.PASSED
    assert states["restore_rehearsal"] is PreflightState.PASSED
    assert states["scheduler"] is PreflightState.PASSED
    assert states["platform_write_guard"] is PreflightState.PASSED
    assert (
        states["store_authorization"]
        is PreflightState.MANUAL_AUTHORIZATION_REQUIRED
    )
    assert states["product_api"] is PreflightState.NOT_VERIFIED
    assert report.release_ready is False
    assert "需要人工授权" in runtime.preflight_service.format_report(report)


def test_platform_preflight_requires_explicit_authorization_and_is_read_only(
    tmp_path: Path,
) -> None:
    db, store = _create_environment(tmp_path)
    sync_service = _OfficialSyncService()
    runtime = DongxiaoRuntime(db.path, sync_service)
    runtime.start()
    try:
        with pytest.raises(ValueError, match="明确授权"):
            runtime.preflight_service.run_platform_readonly(
                store,
                actor_id="tester",
                permission_checker=lambda _permission: True,
                authorization_text="not-authorized",
            )
        report = runtime.preflight_service.run_platform_readonly(
            store,
            actor_id="tester",
            permission_checker=lambda _permission: True,
            authorization_text=PLATFORM_READ_AUTHORIZATION,
        )
    finally:
        runtime.stop()

    states = {item.code: item.state for item in report.checks}
    assert states["store_authorization"] is PreflightState.PASSED
    assert states["product_api"] is PreflightState.PASSED
    assert states["order_api"] is PreflightState.PASSED
    assert states["aftersale_read_api"] is PreflightState.PASSED
    assert all(
        "/accept" not in call and "/reject" not in call
        for call in sync_service.transport.calls
    )
    assert any("/stable_token" in call for call in sync_service.transport.calls)


def test_preflight_service_enforces_application_permission(
    tmp_path: Path,
) -> None:
    db, store = _create_environment(tmp_path)
    service = DongxiaoPreflightService(
        DongxiaoRepository(db.path),
        runtime=None,
        sync_service=_FakeSyncService(),
        database_path=db.path,
        backup_dir=tmp_path / "backups",
    )

    with pytest.raises(PermissionError):
        service.run_local(
            actor_id="denied",
            permission_checker=lambda _permission: False,
        )
    with pytest.raises(PermissionError):
        service.run_platform_readonly(
            store,
            actor_id="denied",
            permission_checker=lambda _permission: False,
            authorization_text=PLATFORM_READ_AUTHORIZATION,
        )
