from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

import pytest

from weimi_manager.db import Database
from weimi_manager.dongxiao.application.aftersale_service import (
    DongxiaoAftersaleService,
)
from weimi_manager.dongxiao.application.task_scheduler import (
    DongxiaoTaskScheduler,
)
from weimi_manager.dongxiao.application.task_service import (
    DongxiaoTaskService,
)
from weimi_manager.dongxiao.domain.enums import (
    AftersaleStatus,
    ApprovalStatus,
    OrderStatus,
    OrderVerificationSource,
)
from weimi_manager.dongxiao.domain.models import AftersaleCase, OrderRecord
from weimi_manager.dongxiao.domain.rules import PermissionDenied
from weimi_manager.dongxiao.infrastructure.executors import (
    AftersaleDecisionExecutor,
    CompositeTaskExecutor,
)
from weimi_manager.dongxiao.infrastructure.repository import (
    DongxiaoRepository,
)


class FakeAftersaleAdapter:
    def __init__(self, detail: dict) -> None:
        self.detail = detail
        self.list_calls = 0
        self.detail_calls = 0
        self.accept_calls = 0
        self.reject_calls = 0

    def list_aftersale_ids(
        self,
        _store,
        start_time: int,
        end_time: int,
        time_field: str = "update",
    ):
        del start_time, end_time
        assert time_field == "update"
        self.list_calls += 1
        return [self.detail["after_sale_order_id"]]

    def get_aftersale(self, _store, case_id: str):
        self.detail_calls += 1
        assert case_id == self.detail["after_sale_order_id"]
        return self.detail

    def accept_aftersale(
        self,
        _store,
        case_id: str,
        *,
        address_id: str = "",
        accept_type: int | None = None,
    ):
        del address_id, accept_type
        assert case_id == self.detail["after_sale_order_id"]
        self.accept_calls += 1
        self.detail["status"] = "PLATFORM_REFUNDING"
        return {"errcode": 0}

    def reject_aftersale(
        self,
        _store,
        case_id: str,
        *,
        reject_reason_type: int,
        reject_reason: str = "",
    ):
        del reject_reason_type, reject_reason
        assert case_id == self.detail["after_sale_order_id"]
        self.reject_calls += 1
        self.detail["status"] = "MERCHANT_REJECT_REFUND"
        return {"errcode": 0}


def _store(db: Database):
    store_id = db.add_store("售后店铺", "wx-phase4-aftersale", "x" * 32)
    store = db.get_store(store_id)
    assert store is not None
    return store


def _seed_order(repo: DongxiaoRepository, store) -> OrderRecord:
    now = datetime(2026, 11, 18, 10, 0, tzinfo=UTC)
    order_ref_id = hashlib.sha256(
        f"{store.app_id}\0order-aftersale".encode("utf-8")
    ).hexdigest()
    return repo.upsert_order_record(
        OrderRecord(
            order_ref_id=order_ref_id,
            store_id=store.id,
            store_app_id=store.app_id,
            platform_order_id="order-aftersale",
            status=OrderStatus.COMPLETED,
            create_time=now,
            update_time=now,
            pay_time=now,
            total_amount_cents=10000,
            product_amount_cents=10000,
            freight_amount_cents=0,
            discount_amount_cents=0,
            item_count=1,
            items=(
                {
                    "product_id": "product-1",
                    "sku_id": "sku-1",
                    "title": "售后商品",
                    "quantity": 1,
                    "sale_price_cents": 10000,
                },
            ),
            source=OrderVerificationSource.PLATFORM_API,
            last_synced_at=now,
        )
    )


def _detail() -> dict:
    return {
        "after_sale_order_id": "after-sale-1",
        "order_id": "order-aftersale",
        "status": "MERCHANT_PROCESSING",
        "type": "REFUND",
        "reason_text": "质量问题",
        "refund_info": {"amount": 3000},
        "create_time": 1_765_000_000,
        "update_time": 1_765_003_600,
        "deadline": 1_765_010_000,
    }


def test_real_aftersale_sync_amount_and_approval_workflow(
    tmp_path: Path,
) -> None:
    db = Database(tmp_path / "phase4-aftersales.db")
    store = _store(db)
    repo = DongxiaoRepository(db.path)
    order = _seed_order(repo, store)
    adapter = FakeAftersaleAdapter(_detail())
    service = DongxiaoAftersaleService(repo, adapter)

    result = service.sync_aftersales(
        store,
        start_time=1_764_999_000,
        end_time=1_765_003_000,
    )

    assert result.saved == 1
    cases = service.list_cases(store)
    assert len(cases) == 1
    case = cases[0]
    assert case.status is AftersaleStatus.PENDING_REVIEW
    assert case.order_ref_id == order.order_ref_id
    assert case.requested_amount_cents == 3000
    assert case.paid_amount_cents == 10000
    assert case.remaining_refundable_cents == 10000
    assert case.platform_status == "MERCHANT_PROCESSING"

    preview = service.preview_amount(case, approved_amount_cents=3000)
    assert preview.approved_amount_cents == 3000
    assert preview.remaining_after_approval_cents == 7000
    with pytest.raises(ValueError):
        service.preview_amount(case, approved_amount_cents=10001)

    approval, case = service.create_approval(
        store,
        case.case_id,
        approved_amount_cents=3000,
        requested_by="operator",
        permission_checker=lambda permission: permission
        == "dongxiao_aftersale_request",
        idempotency_key="aftersale-approval-1",
    )
    assert approval.status is ApprovalStatus.PENDING
    assert case.status is AftersaleStatus.PENDING_REVIEW

    with pytest.raises(PermissionDenied):
        service.decide_approval(
            store,
            approval.approval_id,
            approved=True,
            actor_id="operator",
            permission_checker=lambda _permission: False,
        )

    decided, approved_case = service.decide_approval(
        store,
        approval.approval_id,
        approved=True,
        actor_id="manager",
        reason="金额与凭证核验通过",
        permission_checker=lambda permission: permission
        == "dongxiao_aftersale_approve",
    )
    assert decided.status is ApprovalStatus.APPROVED
    assert approved_case.status is AftersaleStatus.APPROVED
    assert approved_case.approved_amount_cents == 3000


def test_aftersale_task_is_linked_to_real_case(
    tmp_path: Path,
) -> None:
    db = Database(tmp_path / "phase4-aftersale-task.db")
    store = _store(db)
    repo = DongxiaoRepository(db.path)
    _seed_order(repo, store)
    service = DongxiaoAftersaleService(
        repo,
        FakeAftersaleAdapter(_detail()),
    )
    service.sync_aftersales(
        store,
        start_time=1_764_999_000,
        end_time=1_765_003_000,
    )
    case = service.list_cases(store)[0]

    task, created = DongxiaoTaskService(
        repo
    ).create_aftersale_verification_task(
        store,
        [case.case_id],
        idempotency_key="verify-aftersale-1",
        actor_id="operator",
    )

    assert created is True
    items = repo.list_task_execution_items(
        task.task_id,
        store_app_id=store.app_id,
    )
    assert items[0].subject_type == "aftersale"
    assert items[0].subject_id == case.platform_case_id


def test_approved_aftersale_executes_once_in_background_and_rechecks_platform(
    tmp_path: Path,
) -> None:
    db = Database(tmp_path / "phase4-aftersale-execute.db")
    store = _store(db)
    repo = DongxiaoRepository(db.path)
    _seed_order(repo, store)
    adapter = FakeAftersaleAdapter(_detail())
    service = DongxiaoAftersaleService(repo, adapter)
    service.sync_aftersales(
        store,
        start_time=1_764_999_000,
        end_time=1_765_003_000,
    )
    case = service.list_cases(store)[0]
    approval, case = service.create_approval(
        store,
        case.case_id,
        approved_amount_cents=3000,
        requested_by="operator",
        permission_checker=lambda _permission: True,
        idempotency_key="execute-approval",
    )
    _decided, case = service.decide_approval(
        store,
        approval.approval_id,
        approved=True,
        actor_id="manager",
        permission_checker=lambda _permission: True,
    )
    assert case.status is AftersaleStatus.APPROVED

    task, created = service.create_decision_task(
        store,
        case.case_id,
        action="accept",
        actor_id="manager",
        permission_checker=lambda permission: permission
        == "dongxiao_aftersale_execute",
        idempotency_key="aftersale-accept-1",
        accept_type=2,
    )
    repeated, created_again = service.create_decision_task(
        store,
        case.case_id,
        action="accept",
        actor_id="manager",
        permission_checker=lambda _permission: True,
        idempotency_key="aftersale-accept-1",
        accept_type=2,
    )
    assert created is True
    assert created_again is False
    assert repeated.task_id == task.task_id
    with pytest.raises(ValueError):
        service.create_decision_task(
            store,
            case.case_id,
            action="accept",
            actor_id="manager",
            permission_checker=lambda _permission: True,
            idempotency_key="aftersale-accept-different-key",
            accept_type=2,
        )

    decision_executor = AftersaleDecisionExecutor(
        repo,
        adapter,
        service,
    )
    composite = CompositeTaskExecutor(
        repo,
        order_service=object(),
        aftersale_service=service,
        aftersale_decision_executor=decision_executor,
    )
    scheduler = DongxiaoTaskScheduler(
        repo,
        composite,
        poll_interval_seconds=0.02,
    )
    assert scheduler.run_until_idle(timeout=5) is True

    saved = repo.get_task(task.task_id, store_app_id=store.app_id)
    assert saved is not None
    assert saved.status.value == "completed"
    assert adapter.accept_calls == 1
    updated_case = repo.get_aftersale_case(store.id, case.case_id)
    assert updated_case is not None
    assert updated_case.status is AftersaleStatus.PROCESSING


def test_unknown_aftersale_write_is_not_retried_without_remote_verification(
    tmp_path: Path,
) -> None:
    db = Database(tmp_path / "phase4-aftersale-unknown.db")
    store = _store(db)
    repo = DongxiaoRepository(db.path)
    _seed_order(repo, store)

    class UnknownWriteAdapter(FakeAftersaleAdapter):
        def accept_aftersale(
            self,
            _store,
            case_id: str,
            *,
            address_id: str = "",
            accept_type: int | None = None,
        ):
            del case_id, address_id, accept_type
            self.accept_calls += 1
            raise TimeoutError("connection dropped after send")

    adapter = UnknownWriteAdapter(_detail())
    service = DongxiaoAftersaleService(repo, adapter)
    service.sync_aftersales(
        store,
        start_time=1_764_999_000,
        end_time=1_765_003_000,
    )
    case = service.list_cases(store)[0]
    approval, case = service.create_approval(
        store,
        case.case_id,
        approved_amount_cents=3000,
        requested_by="operator",
        permission_checker=lambda _permission: True,
        idempotency_key="unknown-write-approval",
    )
    _decided, case = service.decide_approval(
        store,
        approval.approval_id,
        approved=True,
        actor_id="manager",
        permission_checker=lambda _permission: True,
    )
    task, _created = service.create_decision_task(
        store,
        case.case_id,
        action="accept",
        actor_id="manager",
        permission_checker=lambda _permission: True,
        idempotency_key="unknown-write-task",
        accept_type=2,
    )
    scheduler = DongxiaoTaskScheduler(
        repo,
        CompositeTaskExecutor(
            repo,
            order_service=object(),
            aftersale_service=service,
            aftersale_decision_executor=AftersaleDecisionExecutor(
                repo,
                adapter,
                service,
            ),
        ),
        poll_interval_seconds=0.02,
    )

    assert scheduler.run_until_idle(timeout=5) is True
    assert scheduler.run_until_idle(timeout=1) is True

    saved = repo.get_task(task.task_id, store_app_id=store.app_id)
    assert saved is not None
    assert saved.status.value == "unknown"
    assert adapter.accept_calls == 1

    adapter.detail["status"] = "PLATFORM_REFUNDING"
    verified_task, verified_count = scheduler.verify_unknown_task(
        task.task_id,
        store_app_id=store.app_id,
        actor_id="manager",
    )
    assert verified_count == 1
    assert verified_task.status.value == "preparing"
    assert scheduler.run_until_idle(timeout=3) is True
    completed = repo.get_task(task.task_id, store_app_id=store.app_id)
    assert completed is not None
    assert completed.status.value == "completed"
    assert adapter.accept_calls == 1


def test_refund_preview_accounts_for_other_cases_on_same_order(
    tmp_path: Path,
) -> None:
    db = Database(tmp_path / "phase4-aftersale-shared-order.db")
    store = _store(db)
    repo = DongxiaoRepository(db.path)
    order = _seed_order(repo, store)
    now = datetime(2026, 11, 18, 10, 0, tzinfo=UTC)
    repo.upsert_aftersale_case(
        AftersaleCase(
            case_id="completed-case",
            store_id=store.id,
            store_app_id=store.app_id,
            platform_case_id="completed-platform-case",
            order_ref_id=order.order_ref_id,
            status=AftersaleStatus.COMPLETED,
            requested_amount_cents=6000,
            approved_amount_cents=6000,
            refunded_amount_cents=6000,
            inflight_amount_cents=0,
            paid_amount_cents=10000,
            idempotency_key="completed-case",
            created_at=now,
            updated_at=now,
            source="platform_api",
            platform_order_id=order.platform_order_id,
        )
    )
    current = repo.upsert_aftersale_case(
        AftersaleCase(
            case_id="pending-case",
            store_id=store.id,
            store_app_id=store.app_id,
            platform_case_id="pending-platform-case",
            order_ref_id=order.order_ref_id,
            status=AftersaleStatus.PENDING_REVIEW,
            requested_amount_cents=5000,
            approved_amount_cents=0,
            paid_amount_cents=10000,
            idempotency_key="pending-case",
            created_at=now,
            updated_at=now,
            source="platform_api",
            platform_order_id=order.platform_order_id,
        )
    )
    service = DongxiaoAftersaleService(repo, FakeAftersaleAdapter(_detail()))

    preview = service.preview_amount(
        current,
        approved_amount_cents=4000,
    )
    assert preview.remaining_before_approval_cents == 4000
    assert preview.remaining_after_approval_cents == 0
    with pytest.raises(ValueError):
        service.preview_amount(current, approved_amount_cents=4001)
