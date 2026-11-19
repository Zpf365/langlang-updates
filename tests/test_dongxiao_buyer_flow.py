from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from weimi_manager.db import Database
from weimi_manager.dongxiao.buyer_flow.domain import (
    BuyerAccount,
    BuyerAccountStatus,
    BuyerFlowItem,
    BuyerFlowMode,
    BuyerFlowRun,
    BuyerFlowStatus,
    BuyerIdentityStatus,
    BuyerItemStatus,
    BuyerOrderVerificationStatus,
    BuyerProvider,
)
from weimi_manager.dongxiao.buyer_flow.repository import BuyerFlowRepository
from weimi_manager.dongxiao.buyer_flow.service import (
    DongxiaoBuyerFlowService,
)
from weimi_manager.dongxiao.buyer_flow.session_vault import (
    BuyerSessionRecord,
    BuyerSessionVault,
)
from weimi_manager.dongxiao.domain.enums import (
    DataSource,
    OrderStatus,
    OrderVerificationSource,
    ProductState,
)
from weimi_manager.dongxiao.domain.models import (
    OrderRecord,
    Product,
    Sku,
)
from weimi_manager.dongxiao.infrastructure.repository import DongxiaoRepository


def _store(db: Database, name: str = "赤影云册精选"):
    store_id = db.add_store(name, f"wx-buyer-{uuid.uuid4().hex[:8]}", "x" * 32)
    store = db.get_store(store_id)
    assert store is not None
    return store


def _catalog(repository: DongxiaoRepository, store, count: int = 12) -> None:
    now = datetime(2026, 11, 19, 6, 0, tzinfo=UTC)
    for index in range(1, count + 1):
        product_id = f"buyer-product-{index:03d}"
        state = (
            ProductState.INACTIVE
            if index == count
            else ProductState.ACTIVE
        )
        stock = 0 if index == count - 1 else 20
        repository.upsert_product(
            Product(
                store_id=store.id,
                store_app_id=store.app_id,
                product_id=product_id,
                title=f"真实候选商品 {index:03d}",
                state=state,
                min_price_cents=1000 + index,
                stock_total=stock,
                observed_at=now,
                data_source=DataSource.OFFICIAL_API,
                last_synced_at=now,
            )
        )
        repository.upsert_sku(
            Sku(
                store_id=store.id,
                store_app_id=store.app_id,
                product_id=product_id,
                sku_id=f"buyer-sku-{index:03d}",
                specs=(("颜色", f"颜色{index}"),),
                price_cents=1000 + index,
                stock=stock,
                purchase_min=1,
                purchase_max=5,
                observed_at=now,
            )
        )


def _service(tmp_path: Path, *, order_service=None):
    db = Database(tmp_path / "buyer-flow.db")
    store = _store(db)
    catalog = DongxiaoRepository(db.path)
    _catalog(catalog, store)
    repository = BuyerFlowRepository(db.path)
    service = DongxiaoBuyerFlowService(
        repository,
        catalog,
        BuyerSessionVault(tmp_path),
        order_service=order_service,
    )
    return db, store, catalog, repository, service


def test_buyer_session_vault_is_dpapi_protected_and_round_trips(
    tmp_path: Path,
) -> None:
    vault = BuyerSessionVault(tmp_path)
    record = BuyerSessionRecord(
        buyer_account_id="buyer-1",
        provider="official_wechat",
        opaque_session="never-write-this-plaintext",
        issued_at="2026-11-19T06:00:00+00:00",
        expires_at="2026-12-19T06:00:00+00:00",
    )
    vault.save(record)

    raw = vault.path.read_bytes()
    assert b"never-write-this-plaintext" not in raw
    assert vault.get("buyer-1") == record
    metadata = vault.list_metadata()
    assert metadata[0]["buyer_account_id"] == "buyer-1"
    assert "opaque_session" not in metadata[0]
    vault.remove("buyer-1")
    assert vault.get("buyer-1") is None


def test_official_buyer_account_fails_closed_without_official_login(
    tmp_path: Path,
) -> None:
    _db, store, _catalog_repo, _repository, service = _service(tmp_path)

    result = service.create_official_account(
        display_name="真实买家",
        permission_checker=lambda _permission: True,
        actor_id="tester",
    )

    assert result.account.identity_status is BuyerIdentityStatus.USER_ASSERTED
    assert result.account.status is BuyerAccountStatus.DISCONNECTED
    assert result.capability.official_login is False
    assert result.capability.official_cart_write is False
    assert "官方开放接口" in result.capability.reason
    current = service.set_current_account(
        result.account.buyer_account_id,
        permission_checker=lambda _permission: True,
        actor_id="tester",
    )
    assert current.provider is BuyerProvider.MANUAL_HANDOFF
    assert current.identity_status is BuyerIdentityStatus.USER_ASSERTED
    assert current.verified_identity is False
    run, items = service.prepare_run(
        store,
        buyer_account_id=current.buyer_account_id,
        mode=BuyerFlowMode.AUTO_SELECT,
        requested_count=5,
        manual_product_ids=[],
        quantities={},
        idempotency_key="manual-handoff-official",
        permission_checker=lambda _permission: True,
        actor_id="tester",
    )
    assert run.status is BuyerFlowStatus.WAITING_USER_HANDOFF
    assert len(items) == 5
    assert "官方微信客户端" in run.status_reason


def test_mock_buyer_selects_five_unique_items_and_verifies_cart_state(
    tmp_path: Path,
) -> None:
    _db, store, _catalog_repo, repository, service = _service(tmp_path)
    account = service.create_mock_account(
        display_name="Mock 流程账号",
        permission_checker=lambda _permission: True,
        actor_id="tester",
    )
    run, items = service.prepare_run(
        store,
        buyer_account_id=account.buyer_account_id,
        mode=BuyerFlowMode.AUTO_SELECT,
        requested_count=5,
        manual_product_ids=[],
        quantities={},
        idempotency_key="mock-buyer-flow-1",
        permission_checker=lambda _permission: True,
        actor_id="tester",
    )

    assert run.item_count == 5
    assert len({item.product_id for item in items}) == 5
    assert all(item.status is BuyerItemStatus.SELECTED for item in items)
    assert all(item.quantity == 1 for item in items)
    assert all(item.product_id not in {
        "buyer-product-011",
        "buyer-product-012",
    } for item in items)
    completed = service.execute_mock_flow(
        run.run_id,
        permission_checker=lambda _permission: True,
        actor_id="tester",
    )
    assert completed.status is BuyerFlowStatus.ORDER_VERIFIED
    links = repository.list_order_links(
        store_id=store.id,
        buyer_account_id=account.buyer_account_id,
    )
    assert len(links) == 1
    assert links[0].status is BuyerOrderVerificationStatus.PENDING_PAYMENT
    assert links[0].detail["source"] == "mock"


def test_verified_merchant_order_updates_pending_payment_link(
    tmp_path: Path,
) -> None:
    class OrderService:
        @staticmethod
        def sync_order(store, order_id: str) -> OrderRecord:
            now = datetime.now(UTC)
            return OrderRecord(
                order_ref_id="order-ref-buyer",
                store_id=store.id,
                store_app_id=store.app_id,
                platform_order_id=order_id,
                status=OrderStatus.PENDING_PAYMENT,
                total_amount_cents=12345,
                product_amount_cents=12000,
                freight_amount_cents=345,
                discount_amount_cents=0,
                item_count=5,
                source=OrderVerificationSource.PLATFORM_API,
                last_synced_at=now,
                create_time=now,
                update_time=now,
            )

    _db, store, _catalog_repo, repository, service = _service(
        tmp_path,
        order_service=OrderService(),
    )
    now = datetime.now(UTC)
    account = BuyerAccount(
        buyer_account_id="verified-official-buyer",
        display_name="官方已验证买家",
        account_ref_hash="a" * 64,
        provider=BuyerProvider.OFFICIAL_WECHAT,
        identity_status=BuyerIdentityStatus.VERIFIED_OFFICIAL,
        status=BuyerAccountStatus.ACTIVE,
        authorization_at=now,
        last_verified_at=now,
        created_at=now,
        updated_at=now,
    )
    repository.create_account(account)
    run_id = uuid.uuid4().hex
    run = BuyerFlowRun(
        run_id=run_id,
        store_id=store.id,
        store_app_id=store.app_id,
        store_name=store.name,
        buyer_account_id=account.buyer_account_id,
        mode=BuyerFlowMode.MANUAL_HANDOFF,
        status=BuyerFlowStatus.WAITING_ORDER_ID,
        idempotency_key="verified-official-flow",
        requested_count=5,
        item_count=5,
        merchandise_total_cents=12000,
        created_at=now,
        updated_at=now,
    )
    repository.create_run(
        run,
        [
            BuyerFlowItem(
                item_id=f"item-{index}",
                run_id=run_id,
                product_id=f"product-{index}",
                title=f"商品 {index}",
                sku_id=f"sku-{index}",
                specs=(("颜色", "测试"),),
                unit_price_cents=2400,
                quantity=1,
                stock=10,
                status=BuyerItemStatus.SELECTED,
            )
            for index in range(1, 6)
        ],
    )

    link = service.verify_merchant_order(
        run_id,
        order_id="ORDER-PENDING-001",
        store=store,
        permission_checker=lambda _permission: True,
        actor_id="tester",
    )

    assert link.status is BuyerOrderVerificationStatus.PENDING_PAYMENT
    assert link.total_amount_cents == 12345
    saved = repository.get_run(run_id)
    assert saved is not None
    assert saved.status is BuyerFlowStatus.ORDER_VERIFIED
    assert saved.order_id == "ORDER-PENDING-001"


def test_target_store_and_manual_quantity_validation(
    tmp_path: Path,
) -> None:
    _db, store, _catalog_repo, _repository, service = _service(tmp_path)
    account = service.create_mock_account(
        display_name="手动选择账号",
        permission_checker=lambda _permission: True,
        actor_id="tester",
    )
    stores = [
        SimpleNamespace(id=1, name="其他店铺", app_id="other"),
        store,
    ]
    assert service.find_target_store(stores).name == "赤影云册精选"
    run, items = service.prepare_run(
        store,
        buyer_account_id=account.buyer_account_id,
        mode=BuyerFlowMode.MANUAL_SELECT,
        requested_count=5,
        manual_product_ids=[
            "buyer-product-001",
            "buyer-product-001",
            "buyer-product-002",
        ],
        quantities={"buyer-product-001": 2},
        idempotency_key="manual-buyer-flow",
        permission_checker=lambda _permission: True,
        actor_id="tester",
    )
    assert run.item_count == 2
    by_product = {item.product_id: item for item in items}
    assert by_product["buyer-product-001"].quantity == 2
    with pytest.raises(Exception, match="数量"):
        service.prepare_run(
            store,
            buyer_account_id=account.buyer_account_id,
            mode=BuyerFlowMode.MANUAL_SELECT,
            requested_count=5,
            manual_product_ids=["buyer-product-001"],
            quantities={"buyer-product-001": 0},
            idempotency_key="manual-buyer-flow-invalid",
            permission_checker=lambda _permission: True,
            actor_id="tester",
        )
