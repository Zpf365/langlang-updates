from __future__ import annotations

import hashlib
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
    TaskItemStatus,
    TaskStatus,
)
from weimi_manager.dongxiao.domain.models import (
    AftersaleCase,
    Approval,
    AuditEvent,
    OrderRecord,
    Task,
    TaskExecutionItem,
)
from weimi_manager.dongxiao.domain.operations import TaskExecutionError
from weimi_manager.dongxiao.infrastructure.executors import (
    CompositeTaskExecutor,
)
from weimi_manager.dongxiao.infrastructure.repository import (
    DongxiaoRepository,
)


def _store(db: Database, name: str, app_id: str):
    store_id = db.add_store(name, app_id, "x" * 32)
    store = db.get_store(store_id)
    assert store is not None
    return store


def _seed_historical_records(repo: DongxiaoRepository, store) -> dict[str, str]:
    now = datetime(2026, 11, 18, 10, 0, tzinfo=UTC)
    order_ref_id = hashlib.sha256(
        f"{store.app_id}\0history-order".encode("utf-8")
    ).hexdigest()
    repo.upsert_order_record(
        OrderRecord(
            order_ref_id=order_ref_id,
            store_id=store.id,
            store_app_id=store.app_id,
            platform_order_id="history-order",
            status=OrderStatus.COMPLETED,
            create_time=now,
            update_time=now,
            pay_time=now,
            total_amount_cents=1000,
            product_amount_cents=1000,
            freight_amount_cents=0,
            discount_amount_cents=0,
            item_count=1,
            items=({"product_id": "history-product"},),
            source=OrderVerificationSource.PLATFORM_API,
            last_synced_at=now,
        )
    )
    case = repo.upsert_aftersale_case(
        AftersaleCase(
            case_id="history-case",
            store_id=store.id,
            store_app_id=store.app_id,
            platform_case_id="history-platform-case",
            order_ref_id=order_ref_id,
            status=AftersaleStatus.COMPLETED,
            platform_state=AftersaleStatus.COMPLETED,
            requested_amount_cents=1000,
            approved_amount_cents=1000,
            refunded_amount_cents=1000,
            paid_amount_cents=1000,
            idempotency_key="history-case",
            created_at=now,
            updated_at=now,
            source="platform_api",
            last_synced_at=now,
        )
    )
    task = Task(
        task_id="history-task",
        store_id=store.id,
        store_app_id=store.app_id,
        task_type="history",
        idempotency_key="history-task",
        status=TaskStatus.COMPLETED,
        total_items=1,
        succeeded_items=1,
        failed_items=0,
        created_at=now,
        updated_at=now,
    )
    repo.create_task_with_execution_items(
        task,
        [
            TaskExecutionItem(
                item_id="history-task-item",
                task_id=task.task_id,
                store_id=store.id,
                store_app_id=store.app_id,
                subject_type="product",
                subject_id="history-product",
                status=TaskItemStatus.SUCCEEDED,
                attempt_count=1,
                max_attempts=1,
                created_at=now,
                updated_at=now,
            )
        ],
    )
    repo.create_approval(
        Approval(
            approval_id="history-approval",
            store_id=store.id,
            store_app_id=store.app_id,
            subject_type=ApprovalSubjectType.AFTERSALE,
            subject_id=case.case_id,
            status=ApprovalStatus.APPROVED,
            requested_by="operator",
            requested_at=now,
            decided_by="manager",
            decided_at=now,
        )
    )
    repo.append_audit_event(
        AuditEvent(
            event_id="history-audit",
            store_id=store.id,
            store_app_id=store.app_id,
            actor_id="operator",
            action="history_check",
            entity_type="order",
            entity_id="history-order",
            request_id="history-request",
            result=AuditResult.INFO,
            created_at=now,
        )
    )
    return {
        "order_ref_id": order_ref_id,
        "case_id": case.case_id,
        "task_id": task.task_id,
        "approval_id": "history-approval",
    }


def test_deleted_store_history_remains_readable_by_store_app_id(
    tmp_path: Path,
) -> None:
    db = Database(tmp_path / "phase4-store-history.db")
    store = _store(db, "历史店铺", "wx-phase4-history")
    repo = DongxiaoRepository(db.path)
    ids = _seed_historical_records(repo, store)

    db.delete_store(store.id)

    orders = repo.list_order_records_by_store_app_id(store.app_id)
    cases = repo.list_aftersale_cases_by_store_app_id(store.app_id)
    tasks = repo.list_tasks_by_store_app_id(store.app_id)
    task_items = repo.list_task_execution_items(
        ids["task_id"],
        store_app_id=store.app_id,
    )
    approvals = repo.list_approvals_by_store_app_id(
        store.app_id,
        subject_id=ids["case_id"],
    )
    task = repo.get_task(ids["task_id"], store_app_id=store.app_id)
    audits = repo.list_audit_events(store_app_id=store.app_id)

    assert len(orders) == 1
    assert orders[0].store_id is None
    assert len(cases) == 1
    assert cases[0].store_id is None
    assert len(tasks) == 1
    assert tasks[0].store_id is None
    assert len(task_items) == 1
    assert task_items[0].store_id is None
    assert task is not None
    assert task.store_id is None
    assert len(approvals) == 1
    assert approvals[0].store_id is None
    assert audits[0]["store_id"] is None

    executor = CompositeTaskExecutor(
        repo,
        order_service=object(),
        aftersale_service=object(),
    )
    with pytest.raises(TaskExecutionError) as exc_info:
        executor.execute(
            TaskExecutionItem(
                item_id="deleted-store-item",
                task_id="deleted-store-task",
                store_id=None,
                store_app_id=store.app_id,
                subject_type="order",
                subject_id="history-order",
                status=TaskItemStatus.PENDING,
                attempt_count=0,
                max_attempts=1,
                created_at=datetime(2026, 11, 18, 10, 0, tzinfo=UTC),
                updated_at=datetime(2026, 11, 18, 10, 0, tzinfo=UTC),
            )
        )
    assert exc_info.value.code == "store_deleted"


def test_store_delete_is_blocked_when_active_task_exists(
    tmp_path: Path,
) -> None:
    db = Database(tmp_path / "phase4-store-delete-active.db")
    store = _store(db, "活跃任务店铺", "wx-phase4-active-store")
    repo = DongxiaoRepository(db.path)
    now = datetime(2026, 11, 18, 10, 0, tzinfo=UTC)
    repo.create_task(
        Task(
            task_id="active-task",
            store_id=store.id,
            store_app_id=store.app_id,
            task_type="active",
            idempotency_key="active-task",
            status=TaskStatus.PENDING,
            total_items=0,
            succeeded_items=0,
            failed_items=0,
            created_at=now,
            updated_at=now,
        )
    )

    with pytest.raises(sqlite3.IntegrityError, match="禁止删除"):
        db.delete_store(store.id)

    assert db.get_store(store.id) is not None
