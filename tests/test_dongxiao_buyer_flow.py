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
    BuyerOrderStatus,
    BuyerOrderVerificationStatus,
    BuyerProvider,
    BuyerRunRevalidation,
)
from weimi_manager.dongxiao.buyer_flow.repository import BuyerFlowRepository
from weimi_manager.dongxiao.buyer_flow.service import (
    DongxiaoBuyerFlowService,
)
from weimi_manager.dongxiao.buyer_flow.session_vault import (
    BuyerSessionRecord,
    BuyerSessionVault,
)
from weimi_manager.dongxiao.buyer_flow.wechat_desktop import (
    WechatDesktopProbe,
    WechatDesktopState,
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


def _service(tmp_path: Path, *, order_service=None, wechat_client=None):
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
        wechat_client=wechat_client,
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


def test_buyer_account_profile_and_switch_history_are_auditable(
    tmp_path: Path,
) -> None:
    _db, _store, _catalog_repo, repository, service = _service(tmp_path)
    first = service.create_official_account(
        display_name="买家甲",
        wechat_nickname="微信甲",
        identity_label="本人确认-A",
        remark="常用测试号",
        permission_checker=lambda _permission: True,
        actor_id="tester",
    ).account
    second = service.create_official_account(
        display_name="买家乙",
        wechat_nickname="微信乙",
        identity_label="本人确认-B",
        remark="备用号",
        permission_checker=lambda _permission: True,
        actor_id="tester",
    ).account
    service.set_current_account(
        first.buyer_account_id,
        permission_checker=lambda _permission: True,
        actor_id="tester",
    )
    service.update_account_profile(
        first.buyer_account_id,
        display_name="买家甲（已编辑）",
        wechat_nickname="微信甲昵称",
        identity_label="本人确认-A2",
        remark="主用测试账号",
        permission_checker=lambda _permission: True,
        actor_id="tester",
    )
    current = service.set_current_account(
        second.buyer_account_id,
        permission_checker=lambda _permission: True,
        actor_id="tester",
    )

    accounts = repository.list_accounts()
    by_id = {item.buyer_account_id: item for item in accounts}
    assert by_id[first.buyer_account_id].display_name == "买家甲（已编辑）"
    assert by_id[first.buyer_account_id].wechat_nickname == "微信甲昵称"
    assert by_id[first.buyer_account_id].identity_label == "本人确认-A2"
    assert by_id[first.buyer_account_id].remark == "主用测试账号"
    assert current.is_current is True
    assert current.last_used_at is not None
    events = repository.list_account_switch_events(limit=10)
    assert len(events) == 2
    assert events[0].to_buyer_account_id == second.buyer_account_id
    assert events[0].from_buyer_account_id == first.buyer_account_id
    assert events[0].actor_id == "tester"

    with pytest.raises(ValueError, match="敏感"):
        service.update_account_profile(
            first.buyer_account_id,
            remark="cookie=abc123",
            permission_checker=lambda _permission: True,
            actor_id="tester",
        )


def test_wechat_probe_and_manual_identity_confirmation_are_recorded(
    tmp_path: Path,
) -> None:
    class FakeWechatClient:
        @staticmethod
        def probe() -> WechatDesktopProbe:
            return WechatDesktopProbe(
                state=WechatDesktopState.IDENTITY_UNCERTAIN,
                installed=True,
                running=True,
                checked_at=datetime.now(UTC),
                executable_path="C:/Official/WeChat.exe",
                hwnd=100,
                process_id=200,
                window_title="微信",
                executable_name="WeChat.exe",
                identity_confirmed=False,
                instructions="请人工确认当前微信身份。",
            )

        @staticmethod
        def switch_account_instructions() -> str:
            return "请用户在官方微信退出并重新登录。"

        @staticmethod
        def open_official_url(_url: str) -> bool:
            return True

    _db, _store, _catalog_repo, repository, service = _service(
        tmp_path,
        wechat_client=FakeWechatClient(),
    )
    account = service.create_official_account(
        display_name="人工确认买家",
        permission_checker=lambda _permission: True,
        actor_id="tester",
    ).account

    probe = service.inspect_wechat_client(
        buyer_account_id=account.buyer_account_id,
        permission_checker=lambda _permission: True,
        actor_id="tester",
    )
    assert probe.state is WechatDesktopState.IDENTITY_UNCERTAIN
    confirmed = service.confirm_wechat_identity(
        account.buyer_account_id,
        wechat_nickname="买家微信昵称",
        identity_label="手机微信本人确认",
        permission_checker=lambda _permission: True,
        actor_id="tester",
    )
    assert confirmed.wechat_nickname == "买家微信昵称"
    assert confirmed.identity_label == "手机微信本人确认"
    assert confirmed.identity_status is BuyerIdentityStatus.USER_ASSERTED
    events = repository.list_client_events(
        buyer_account_id=account.buyer_account_id
    )
    assert len(events) == 2
    assert events[0]["client_state"] == "identity_confirmed"
    assert events[1]["client_state"] == "identity_uncertain"


def test_manual_handoff_blocks_when_windows_wechat_is_not_running(
    tmp_path: Path,
) -> None:
    class MissingClient:
        @staticmethod
        def probe() -> WechatDesktopProbe:
            return WechatDesktopProbe(
                state=WechatDesktopState.NOT_RUNNING,
                installed=True,
                running=False,
                checked_at=datetime.now(UTC),
                instructions="请先启动官方微信。",
            )

        @staticmethod
        def switch_account_instructions() -> str:
            return "官方微信内切换账号"

        @staticmethod
        def open_official_url(_url: str) -> bool:
            return True

    _db, store, _catalog_repo, _repository, service = _service(
        tmp_path,
        wechat_client=MissingClient(),
    )
    account = service.create_official_account(
        display_name="未启动客户端买家",
        permission_checker=lambda _permission: True,
        actor_id="tester",
    ).account
    service.set_current_account(
        account.buyer_account_id,
        permission_checker=lambda _permission: True,
        actor_id="tester",
    )
    run, _items = service.prepare_run(
        store,
        buyer_account_id=account.buyer_account_id,
        mode=BuyerFlowMode.AUTO_SELECT,
        requested_count=5,
        manual_product_ids=[],
        quantities={},
        idempotency_key="missing-client-handoff",
        permission_checker=lambda _permission: True,
        actor_id="tester",
    )

    with pytest.raises(Exception, match="未运行"):
        service.start_manual_handoff(
            run.run_id,
            permission_checker=lambda _permission: True,
            actor_id="tester",
        )


def test_buyer_account_switch_is_blocked_by_active_task(
    tmp_path: Path,
) -> None:
    _db, store, _catalog_repo, _repository, service = _service(tmp_path)
    first = service.create_official_account(
        display_name="任务中买家",
        permission_checker=lambda _permission: True,
        actor_id="tester",
    ).account
    second = service.create_official_account(
        display_name="目标买家",
        permission_checker=lambda _permission: True,
        actor_id="tester",
    ).account
    service.set_current_account(
        first.buyer_account_id,
        permission_checker=lambda _permission: True,
        actor_id="tester",
    )
    service.prepare_run(
        store,
        buyer_account_id=first.buyer_account_id,
        mode=BuyerFlowMode.AUTO_SELECT,
        requested_count=5,
        manual_product_ids=[],
        quantities={},
        idempotency_key="active-switch-conflict",
        permission_checker=lambda _permission: True,
        actor_id="tester",
    )

    with pytest.raises(Exception, match="未完成"):
        service.set_current_account(
            second.buyer_account_id,
            permission_checker=lambda _permission: True,
            actor_id="tester",
        )


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


def test_buyer_task_pause_resume_and_cancel_are_recorded(
    tmp_path: Path,
) -> None:
    _db, store, _catalog_repo, repository, service = _service(tmp_path)
    account = service.create_mock_account(
        display_name="暂停恢复账号",
        permission_checker=lambda _permission: True,
        actor_id="tester",
    )
    run, _items = service.prepare_run(
        store,
        buyer_account_id=account.buyer_account_id,
        mode=BuyerFlowMode.AUTO_SELECT,
        requested_count=5,
        manual_product_ids=[],
        quantities={},
        idempotency_key="pause-resume-cancel",
        permission_checker=lambda _permission: True,
        actor_id="tester",
    )

    paused = service.set_run_status(
        run.run_id,
        "pause",
        permission_checker=lambda _permission: True,
        actor_id="tester",
    )
    resumed = service.set_run_status(
        run.run_id,
        "resume",
        permission_checker=lambda _permission: True,
        actor_id="tester",
    )
    cancelled = service.set_run_status(
        run.run_id,
        "cancel",
        permission_checker=lambda _permission: True,
        actor_id="tester",
    )

    assert paused.status is BuyerFlowStatus.PAUSED
    assert resumed.status is BuyerFlowStatus.WAITING_USER_HANDOFF
    assert cancelled.status is BuyerFlowStatus.CANCELLED
    events = repository.list_task_events(run.run_id)
    assert [event["event_type"] for event in events] == [
        "pause",
        "resume",
        "cancel",
    ]


def test_buyer_task_revalidation_stops_on_price_or_stock_change(
    tmp_path: Path,
) -> None:
    _db, store, catalog, repository, service = _service(tmp_path)
    account = service.create_mock_account(
        display_name="重新核验账号",
        permission_checker=lambda _permission: True,
        actor_id="tester",
    )
    run, items = service.prepare_run(
        store,
        buyer_account_id=account.buyer_account_id,
        mode=BuyerFlowMode.MANUAL_SELECT,
        requested_count=1,
        manual_product_ids=["buyer-product-001"],
        quantities={"buyer-product-001": 2},
        idempotency_key="revalidate-price-stock",
        permission_checker=lambda _permission: True,
        actor_id="tester",
    )
    item = items[0]
    catalog.upsert_sku(
        Sku(
            store_id=store.id,
            store_app_id=store.app_id,
            product_id=item.product_id,
            sku_id=item.sku_id,
            specs=item.specs,
            price_cents=item.unit_price_cents + 500,
            stock=1,
            purchase_min=1,
            purchase_max=5,
            observed_at=datetime.now(UTC),
        )
    )

    result = service.revalidate_run(
        run.run_id,
        store=store,
        permission_checker=lambda _permission: True,
        actor_id="tester",
    )

    assert isinstance(result, BuyerRunRevalidation)
    assert result.changed is True
    assert any("价格" in reason for reason in result.reasons)
    assert any("库存" in reason for reason in result.reasons)
    saved = repository.get_run(run.run_id)
    assert saved is not None
    assert saved.status is BuyerFlowStatus.PAUSED


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


@pytest.mark.parametrize(
    ("platform_status", "expected_status"),
    [
        (OrderStatus.PAID, BuyerOrderStatus.PAID),
        (OrderStatus.SHIPPED, BuyerOrderStatus.SHIPPED),
        (OrderStatus.COMPLETED, BuyerOrderStatus.COMPLETED),
        (OrderStatus.CANCELLED, BuyerOrderStatus.CANCELLED),
        (OrderStatus.REFUNDING, BuyerOrderStatus.REFUNDING),
        (OrderStatus.REFUNDED, BuyerOrderStatus.REFUNDED),
        (OrderStatus.UNKNOWN, BuyerOrderStatus.UNKNOWN),
    ],
)
def test_official_order_statuses_are_preserved_after_verification(
    tmp_path: Path,
    platform_status: OrderStatus,
    expected_status: BuyerOrderStatus,
) -> None:
    class OrderService:
        @staticmethod
        def sync_order(store, order_id: str) -> OrderRecord:
            now = datetime.now(UTC)
            return OrderRecord(
                order_ref_id=f"order-ref-{platform_status.value}",
                store_id=store.id,
                store_app_id=store.app_id,
                platform_order_id=order_id,
                status=platform_status,
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
        buyer_account_id="status-buyer",
        display_name="状态买家",
        account_ref_hash="b" * 64,
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
    repository.create_run(
        BuyerFlowRun(
            run_id=run_id,
            store_id=store.id,
            store_app_id=store.app_id,
            store_name=store.name,
            buyer_account_id=account.buyer_account_id,
            mode=BuyerFlowMode.MANUAL_HANDOFF,
            status=BuyerFlowStatus.WAITING_ORDER_ID,
            idempotency_key=f"order-status-{platform_status.value}",
            requested_count=1,
            item_count=1,
            merchandise_total_cents=12000,
            created_at=now,
            updated_at=now,
        ),
        [
            BuyerFlowItem(
                item_id=f"item-{platform_status.value}",
                run_id=run_id,
                product_id="product-status",
                title="状态测试商品",
                sku_id="sku-status",
                specs=(("颜色", "测试"),),
                unit_price_cents=12000,
                quantity=1,
                stock=10,
                status=BuyerItemStatus.SELECTED,
            )
        ],
    )

    link = service.verify_merchant_order(
        run_id,
        order_id=f"ORDER-{platform_status.value}",
        store=store,
        permission_checker=lambda _permission: True,
        actor_id="tester",
    )

    assert link.order_status is expected_status
    saved = repository.get_run(run_id)
    assert saved is not None
    assert saved.order_status is expected_status
    assert saved.status is (
        BuyerFlowStatus.FAILED
        if expected_status is BuyerOrderStatus.UNKNOWN
        else BuyerFlowStatus.ORDER_VERIFIED
    )


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
