from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from weimi_manager.db import Database
from weimi_manager.dongxiao.domain.enums import (
    AftersaleStatus,
    ApprovalStatus,
    ApprovalSubjectType,
    AuditResult,
    OrderStatus,
    OrderVerificationSource,
    ProductPoolStatus,
    ProductState,
    TaskAttemptStatus,
    TaskItemStatus,
    TaskStatus,
)
from weimi_manager.dongxiao.domain.models import (
    AftersaleCase,
    Approval,
    AuditEvent,
    OrderStatusSnapshot,
    Product,
    ProductPool,
    ProductPoolItem,
    ProductSnapshot,
    RealOrderReference,
    Sku,
    Task,
    TaskAttempt,
    TaskItem,
)
from weimi_manager.dongxiao.domain.rules import (
    PermissionDenied,
    StoreIsolationError,
    require_permission,
)
from weimi_manager.dongxiao.domain.state_machine import (
    UnknownResultRequiresVerification,
)
from weimi_manager.dongxiao.infrastructure.repository import (
    DongxiaoRepository,
    EntityNotFound,
    IdempotencyConflict,
)
from weimi_manager.models import Store


def _store(db: Database, name: str, app_id: str) -> Store:
    store_id = db.add_store(name, app_id, "x" * 32)
    store = db.get_store(store_id)
    assert store is not None
    return store


def _task(
    *,
    task_id: str,
    store: Store,
    idempotency_key: str,
    request_id: str,
    now: datetime,
) -> Task:
    return Task(
        task_id=task_id,
        store_id=store.id,
        store_app_id=store.app_id,
        task_type="product_review",
        idempotency_key=idempotency_key,
        status=TaskStatus.PENDING,
        total_items=1,
        succeeded_items=0,
        failed_items=0,
        created_at=now,
        updated_at=now,
        metadata={"request_id": request_id},
    )


def test_task_creation_is_idempotent_and_rejects_conflicting_request(
    tmp_path: Path,
) -> None:
    db = Database(tmp_path / "repository.db")
    store = _store(db, "店铺一", "wx-repository-one")
    repo = DongxiaoRepository(db.path)
    now = datetime(2026, 11, 18, 10, 0, tzinfo=UTC)

    first, created = repo.create_task(
        _task(
            task_id="task-1",
            store=store,
            idempotency_key="idem-1",
            request_id="request-1",
            now=now,
        )
    )
    repeated, created_again = repo.create_task(
        _task(
            task_id="task-2",
            store=store,
            idempotency_key="idem-1",
            request_id="request-1",
            now=now,
        )
    )

    assert created is True
    assert created_again is False
    assert first.task_id == repeated.task_id == "task-1"

    with pytest.raises(IdempotencyConflict):
        repo.create_task(
            _task(
                task_id="task-3",
                store=store,
                idempotency_key="idem-1",
                request_id="different-request",
                now=now,
            )
        )


def test_unknown_task_requires_remote_verification_before_recovery(
    tmp_path: Path,
) -> None:
    db = Database(tmp_path / "unknown.db")
    store = _store(db, "未知任务店铺", "wx-unknown-task")
    repo = DongxiaoRepository(db.path)
    now = datetime(2026, 11, 18, 10, 0, tzinfo=UTC)
    task, _ = repo.create_task(
        _task(
            task_id="unknown-task",
            store=store,
            idempotency_key="unknown-idem",
            request_id="unknown-request",
            now=now,
        )
    )
    for event in ("start_preparing", "start_running", "mark_unknown"):
        task = repo.transition_task(
            task.task_id,
            store_id=store.id,
            store_app_id=store.app_id,
            event=event,
        )
    assert task.status is TaskStatus.UNKNOWN

    with pytest.raises(UnknownResultRequiresVerification):
        repo.transition_task(
            task.task_id,
            store_id=store.id,
            store_app_id=store.app_id,
            event="recover_unknown",
        )
    unchanged = repo.get_task(task.task_id, store_app_id=store.app_id)
    assert unchanged is not None
    assert unchanged.status is TaskStatus.UNKNOWN

    recovered = repo.transition_task(
        task.task_id,
        store_id=store.id,
        store_app_id=store.app_id,
        event="recover_unknown",
        remote_result_verified=True,
    )
    assert recovered.status is TaskStatus.PREPARING


def test_repository_enforces_product_task_and_store_isolation(
    tmp_path: Path,
) -> None:
    db = Database(tmp_path / "isolation.db")
    first = _store(db, "店铺一", "wx-isolation-one")
    second = _store(db, "店铺二", "wx-isolation-two")
    repo = DongxiaoRepository(db.path)
    now = datetime(2026, 11, 18, 10, 0, tzinfo=UTC)

    for store, title in ((first, "店铺一商品"), (second, "店铺二商品")):
        repo.upsert_product(
            Product(
                store_id=store.id,
                store_app_id=store.app_id,
                product_id="same-product-id",
                title=title,
                state=ProductState.ACTIVE,
                min_price_cents=500,
                stock_total=2,
                observed_at=now,
            )
        )

    assert [item.title for item in repo.list_products(first.id)] == ["店铺一商品"]
    assert [item.title for item in repo.list_products(second.id)] == ["店铺二商品"]
    repo.upsert_product(
        Product(
            store_id=second.id,
            store_app_id=second.app_id,
            product_id="second-only-product",
            title="仅店铺二商品",
            state=ProductState.ACTIVE,
            min_price_cents=500,
            stock_total=2,
            observed_at=now,
        )
    )

    repo.upsert_sku(
        Sku(
            store_id=first.id,
            store_app_id=first.app_id,
            product_id="same-product-id",
            sku_id="sku-first",
            price_cents=500,
            stock=2,
            observed_at=now,
        )
    )
    with pytest.raises(EntityNotFound):
        repo.upsert_sku(
            Sku(
                store_id=first.id,
                store_app_id=first.app_id,
                product_id="second-only-product",
                sku_id="sku-cross-store",
                price_cents=500,
                stock=2,
                observed_at=now,
            )
        )

    first_task, _ = repo.create_task(
        _task(
            task_id="isolated-task-1",
            store=first,
            idempotency_key="same-key",
            request_id="request-one",
            now=now,
        )
    )
    second_task, _ = repo.create_task(
        _task(
            task_id="isolated-task-2",
            store=second,
            idempotency_key="same-key",
            request_id="request-two",
            now=now,
        )
    )
    assert first_task.store_id != second_task.store_id
    assert (
        repo.get_task(
            first_task.task_id,
            store_app_id=second.app_id,
        )
        is None
    )
    with pytest.raises(StoreIsolationError):
        repo.transition_task(
            first_task.task_id,
            store_id=second.id,
            store_app_id=second.app_id,
            event="start_preparing",
        )


def test_permission_and_trusted_order_rules_are_fail_closed(
    tmp_path: Path,
) -> None:
    db = Database(tmp_path / "trust.db")
    first = _store(db, "店铺一", "wx-trust-one")
    second = _store(db, "店铺二", "wx-trust-two")
    repo = DongxiaoRepository(db.path)
    now = datetime(2026, 11, 18, 10, 0, tzinfo=UTC)

    with pytest.raises(PermissionDenied):
        require_permission(lambda _name: False, "orders_view")

    order = RealOrderReference(
        order_ref_id="order-ref-mock",
        store_id=first.id,
        store_app_id=first.app_id,
        platform_order_id="mock-order-1",
        status=OrderStatus.PENDING_PAYMENT,
        verified=False,
        source=OrderVerificationSource.MOCK,
        verified_at=None,
        created_at=now,
    )
    repo.record_order_reference(order)
    repo.record_order_snapshot(
        OrderStatusSnapshot(
            snapshot_id="order-snapshot-mock",
            order_ref_id=order.order_ref_id,
            store_id=first.id,
            store_app_id=first.app_id,
            platform_order_id=order.platform_order_id,
            status=OrderStatus.PENDING_PAYMENT,
            trusted=False,
            source=OrderVerificationSource.MOCK,
            observed_at=now,
        )
    )
    with pytest.raises(sqlite3.IntegrityError):
        repo.create_aftersale(
            AftersaleCase(
                case_id="cross-store-case",
                store_id=second.id,
                store_app_id=second.app_id,
                platform_case_id="case-1",
                order_ref_id=order.order_ref_id,
                status=AftersaleStatus.REQUESTED,
                requested_amount_cents=100,
                approved_amount_cents=0,
                idempotency_key="case-idem-1",
                created_at=now,
                updated_at=now,
                source="platform_api",
            )
        )


def test_audit_is_redacted_and_append_only(tmp_path: Path) -> None:
    db = Database(tmp_path / "audit.db")
    store = _store(db, "审计店铺", "wx-audit")
    repo = DongxiaoRepository(db.path)
    now = datetime(2026, 11, 18, 10, 0, tzinfo=UTC)
    repo.append_audit_event(
        AuditEvent(
            event_id="audit-1",
            store_id=store.id,
            store_app_id=store.app_id,
            actor_id="tester",
            action="security_check",
            entity_type="store",
            entity_id=store.app_id,
            request_id="req-audit-1",
            result=AuditResult.INFO,
            created_at=now,
            detail={
                "access_token": "secret-token",
                "nested": {"cookie": "session-cookie", "safe": "visible"},
            },
        )
    )

    rows = repo.list_audit_events(store_app_id=store.app_id)
    detail = json.loads(rows[0]["detail_json"])
    assert detail["access_token"] == "[REDACTED]"
    assert detail["nested"]["cookie"] == "[REDACTED]"
    assert detail["nested"]["safe"] == "visible"

    conn = sqlite3.connect(db.path)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "UPDATE dongxiao_audit_events SET action='tampered'"
            )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM dongxiao_audit_events")
    finally:
        conn.close()


def test_all_core_models_persist_with_foreign_key_isolation(
    tmp_path: Path,
) -> None:
    db = Database(tmp_path / "models.db")
    store = _store(db, "完整模型店铺", "wx-complete-model")
    repo = DongxiaoRepository(db.path)
    now = datetime(2026, 11, 18, 10, 0, tzinfo=UTC)

    product = repo.upsert_product(
        Product(
            store_id=store.id,
            store_app_id=store.app_id,
            product_id="product-complete",
            title="完整模型商品",
            state=ProductState.ACTIVE,
            min_price_cents=100,
            stock_total=5,
            observed_at=now,
        )
    )
    sku = repo.upsert_sku(
        Sku(
            store_id=store.id,
            store_app_id=store.app_id,
            product_id=product.product_id,
            sku_id="sku-complete",
            price_cents=100,
            stock=5,
            observed_at=now,
        )
    )
    repo.record_product_snapshot(
        ProductSnapshot(
            snapshot_id="snapshot-complete",
            store_id=store.id,
            store_app_id=store.app_id,
            product_id=product.product_id,
            sku_id=sku.sku_id,
            title=product.title,
            price_cents=sku.price_cents,
            stock=sku.stock,
            available=True,
            source="mock",
            observed_at=now,
        )
    )

    pool, created = repo.create_pool(
        ProductPool(
            pool_id="pool-complete",
            store_id=store.id,
            store_app_id=store.app_id,
            name="测试商品池",
            status=ProductPoolStatus.ACTIVE,
            idempotency_key="pool-idem",
            created_at=now,
            updated_at=now,
        )
    )
    assert created is True
    repo.add_pool_item(
        ProductPoolItem(
            item_id="pool-item-complete",
            pool_id=pool.pool_id,
            store_id=store.id,
            store_app_id=store.app_id,
            product_id=product.product_id,
            sku_id=sku.sku_id,
            position=0,
            created_at=now,
        )
    )

    task, _ = repo.create_task(
        _task(
            task_id="task-complete",
            store=store,
            idempotency_key="task-idem",
            request_id="request-complete",
            now=now,
        )
    )
    repo.add_task_item(
        TaskItem(
            item_id="task-item-complete",
            task_id=task.task_id,
            store_id=store.id,
            store_app_id=store.app_id,
            product_id=product.product_id,
            sku_id=sku.sku_id,
            status=TaskItemStatus.PENDING,
            attempt_count=1,
            created_at=now,
            updated_at=now,
        )
    )
    repo.record_task_attempt(
        TaskAttempt(
            attempt_id="attempt-complete",
            task_id=task.task_id,
            item_id="task-item-complete",
            store_id=store.id,
            store_app_id=store.app_id,
            attempt_no=1,
            status=TaskAttemptStatus.SUCCEEDED,
            request_id="attempt-request",
            started_at=now,
            finished_at=now,
            result={"safe": True, "token": "must-not-leak"},
        )
    )

    order = RealOrderReference(
        order_ref_id="order-ref-real",
        store_id=store.id,
        store_app_id=store.app_id,
        platform_order_id="real-order-1",
        status=OrderStatus.PAID,
        verified=True,
        source=OrderVerificationSource.PLATFORM_API,
        verified_at=now,
        created_at=now,
    )
    repo.record_order_reference(order)
    repo.create_aftersale(
        AftersaleCase(
            case_id="case-complete",
            store_id=store.id,
            store_app_id=store.app_id,
            platform_case_id="platform-case-1",
            order_ref_id=order.order_ref_id,
            status=AftersaleStatus.REQUESTED,
            requested_amount_cents=100,
            approved_amount_cents=0,
            idempotency_key="case-idem",
            created_at=now,
            updated_at=now,
            source="platform_api",
        )
    )
    repo.create_approval(
        Approval(
            approval_id="approval-complete",
            store_id=store.id,
            store_app_id=store.app_id,
            subject_type=ApprovalSubjectType.AFTERSALE,
            subject_id="case-complete",
            status=ApprovalStatus.PENDING,
            requested_by="operator",
            requested_at=now,
        )
    )

    conn = sqlite3.connect(db.path)
    try:
        expected = {
            "dongxiao_product_snapshots": 1,
            "dongxiao_product_pools": 1,
            "dongxiao_product_pool_items": 1,
            "dongxiao_task_items": 1,
            "dongxiao_task_attempts": 1,
            "dongxiao_order_refs": 1,
            "dongxiao_aftersale_cases": 1,
            "dongxiao_approvals": 1,
        }
        for table, count in expected.items():
            assert conn.execute(
                f"SELECT COUNT(*) FROM {table}"
            ).fetchone()[0] == count
        attempt_result = json.loads(
            conn.execute(
                "SELECT result_json FROM dongxiao_task_attempts"
            ).fetchone()[0]
        )
        assert attempt_result["token"] == "[REDACTED]"
    finally:
        conn.close()
