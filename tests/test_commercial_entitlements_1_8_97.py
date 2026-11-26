from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from weimi_manager.db import Database
from weimi_manager.models import Order, OrderItem
from weimi_manager.webui.controller import NextUiController


INTERNAL_ACCOUNT_IDS = (
    "220a9616-2346-441b-9e3f-f5f4f5b824fd",
    "8248567f-c855-435a-89ef-4175729155df",
)
HIDDEN_MODULES = {
    "delivery_ledger",
    "team",
    "after_sales",
    "storefront",
    "finance",
    "automation",
    "store_groups",
    "runtime_logs",
}


def _context(
    account_id: str,
    *,
    username: str = "",
    role: str = "owner",
    offline: bool = False,
    permissions: dict | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        account_id=account_id,
        username=username or f"user-{account_id[:6]}",
        email="",
        member_role=role,
        permissions=permissions or {},
        tokens=SimpleNamespace(access_token="capability-jwt"),
        offline=offline,
        license=None,
        organization_name="test",
        entitlements={},
    )


def _controller(tmp_path: Path, context) -> NextUiController:
    return NextUiController(
        Database(tmp_path / f"{context.account_id}.db"),
        account_label=context.username,
        auth_context=context,
    )


@pytest.mark.parametrize("account_id", INTERNAL_ACCOUNT_IDS)
def test_internal_account_ids_see_hidden_modules(
    tmp_path: Path,
    account_id: str,
) -> None:
    payload = _controller(
        tmp_path,
        _context(account_id),
    ).dispatch("bootstrap", {})
    more_keys = {item["key"] for item in payload["more_nav"]}
    primary_keys = {item["key"] for item in payload["primary_nav"]}
    assert HIDDEN_MODULES.issubset(more_keys)
    assert "procurement" not in primary_keys
    assert payload["entitlements"][
        "feature.procurement_association"
    ] is False
    assert all(
        enabled
        for key, enabled in payload["entitlements"].items()
        if key != "feature.procurement_association"
    )


@pytest.mark.parametrize("account_id", INTERNAL_ACCOUNT_IDS)
def test_procurement_association_rejects_non_zpfly_internal_owner(
    tmp_path: Path,
    account_id: str,
) -> None:
    controller = _controller(tmp_path, _context(account_id))
    controller.db.set_setting(
        "local_experiment.procurement_association_enabled",
        "1",
    )

    payload = controller.dispatch("bootstrap", {})
    assert payload["entitlements"][
        "feature.procurement_association"
    ] is False
    with pytest.raises(
        PermissionError,
        match="此功能正在加急开发优化中",
    ):
        controller.dispatch("procurement.list", {})


def test_procurement_association_can_be_disabled_for_zpfly_locally(
    tmp_path: Path,
) -> None:
    context = _context(INTERNAL_ACCOUNT_IDS[0], username="zpfly")
    controller = _controller(tmp_path, context)
    controller.db.set_setting(
        "local_experiment.procurement_association_enabled",
        "0",
    )

    payload = controller.dispatch("bootstrap", {})
    assert payload["entitlements"][
        "feature.procurement_association"
    ] is False
    with pytest.raises(
        PermissionError,
        match="此功能正在加急开发优化中",
    ):
        controller.dispatch("orders.procurement.link-upsert", {})


def test_local_zpfly_whitelist_is_identity_not_account_id(
    tmp_path: Path,
) -> None:
    controller = _controller(
        tmp_path,
        _context("local-zpfly-account", username="zpfly"),
    )

    payload = controller.dispatch("bootstrap", {})
    assert payload["entitlements"][
        "feature.procurement_association"
    ] is True


def test_non_zpfly_gate_does_not_block_after_sales_or_shipping(
    tmp_path: Path,
) -> None:
    controller = _controller(
        tmp_path,
        _context(INTERNAL_ACCOUNT_IDS[0]),
    )
    controller._list_after_sales = lambda payload: {
        "after_sales": [],
        "store_id": payload.get("store_id"),
    }
    controller._refresh_order_shipping = lambda payload: {
        "options": [],
        "order_id": payload.get("order_id"),
    }

    assert controller.dispatch(
        "orders.after-sales.list",
        {"store_id": 19},
    ) == {"after_sales": [], "store_id": 19}
    assert controller.dispatch(
        "orders.workbench.shipping-options",
        {"store_id": 19, "order_id": "order-1"},
    ) == {"options": [], "order_id": "order-1"}


def test_ordinary_account_keeps_core_nav_and_hides_internal_modules(
    tmp_path: Path,
) -> None:
    controller = _controller(
        tmp_path,
        _context("ordinary-account"),
    )
    payload = controller.dispatch("bootstrap", {})
    more_keys = {item["key"] for item in payload["more_nav"]}
    primary_keys = {item["key"] for item in payload["primary_nav"]}
    assert not HIDDEN_MODULES.intersection(more_keys)
    assert {
        "stores",
        "orders",
        "logistics",
        "statistics",
        "products",
        "upload",
        "violations",
    }.issubset(primary_keys)
    assert "procurement" not in primary_keys
    assert payload["entitlements"] == {
        key: False for key in payload["entitlements"]
    }
    for action, payload_data in (
        ("team.operations.members", {}),
        ("logs.status", {}),
        ("finance.summary", {}),
        ("automation.tasks.list", {}),
        ("storefront.load", {}),
        ("orders.after-sales.list", {}),
        ("stores.groups.create", {"group_name": "x"}),
        ("update.check", {"manual": True}),
        ("account.action", {"action": "switch"}),
    ):
        with pytest.raises(PermissionError, match="无权"):
            controller.dispatch(action, payload_data)
    for action in (
        "procurement.list",
        "orders.procurement.link-upsert",
        "orders.workbench.manual-purchase",
    ):
        with pytest.raises(
            PermissionError,
            match="此功能正在加急开发优化中",
        ):
            controller.dispatch(action, {})


def test_explicit_backend_entitlement_grants_only_requested_module(
    tmp_path: Path,
) -> None:
    controller = _controller(
        tmp_path,
        _context(
            "granted-account",
            permissions={"module.finance": True},
        ),
    )
    payload = controller.dispatch("bootstrap", {})
    more_keys = {item["key"] for item in payload["more_nav"]}
    assert "finance" in more_keys
    assert "team" not in more_keys
    assert payload["entitlements"]["module.finance"] is True
    assert payload["entitlements"]["module.team"] is False


def test_server_capabilities_are_pulled_and_revoked_fail_closed(
    tmp_path: Path,
) -> None:
    grants = {"module.finance": True}

    class CapabilityClient:
        def my_ops_capabilities(self, access_token):
            assert access_token == "capability-jwt"
            return dict(grants)

    context = _context("ordinary-capability-owner")
    context.entitlements = {"module.finance": True}
    controller = NextUiController(
        Database(tmp_path / "capability.db"),
        account_label=context.username,
        auth_context=context,
        auth_client=CapabilityClient(),
    )
    payload = controller.dispatch("bootstrap", {})
    assert payload["entitlements"]["module.finance"] is True
    assert payload["entitlements"]["module.team"] is False
    refreshed = controller.dispatch("capabilities.refresh", {})
    assert refreshed["entitlements"]["module.finance"] is True

    controller._require_entitlement("module.finance")
    grants.clear()
    with pytest.raises(PermissionError, match="无权"):
        controller._require_entitlement("module.finance")
    revoked_payload = controller.dispatch("bootstrap", {})
    assert revoked_payload["entitlements"]["module.finance"] is False

    grants["module.finance"] = True
    controller._require_entitlement("module.finance")
    restored_payload = controller.dispatch("capabilities.refresh", {})
    assert restored_payload["entitlements"]["module.finance"] is True


def test_capability_pull_failure_does_not_trust_stale_context(
    tmp_path: Path,
) -> None:
    class FailingCapabilityClient:
        def my_ops_capabilities(self, _access_token):
            raise RuntimeError("network unavailable")

    context = _context("stale-capability-owner")
    context.entitlements = {"module.finance": True}
    controller = NextUiController(
        Database(tmp_path / "stale-capability.db"),
        account_label=context.username,
        auth_context=context,
        auth_client=FailingCapabilityClient(),
    )

    payload = controller.dispatch("bootstrap", {})

    assert payload["entitlements"]["module.finance"] is False
    with pytest.raises(PermissionError, match="无权"):
        controller._require_entitlement("module.finance")


def test_offline_or_empty_role_denies_internal_entitlements(
    tmp_path: Path,
) -> None:
    for context in (
        _context(INTERNAL_ACCOUNT_IDS[0], offline=True),
        _context(INTERNAL_ACCOUNT_IDS[1], role=""),
    ):
        payload = _controller(tmp_path, context).dispatch("bootstrap", {})
        more_keys = {item["key"] for item in payload["more_nav"]}
        assert not HIDDEN_MODULES.intersection(more_keys)
        assert not any(payload["entitlements"].values())


def test_frontend_router_and_pages_use_entitlement_guards() -> None:
    root = Path(__file__).resolve().parent.parent
    router = (root / "webui" / "src" / "router.js").read_text(
        encoding="utf-8"
    )
    more_page = (root / "webui" / "src" / "pages" / "MorePage.vue").read_text(
        encoding="utf-8"
    )
    stores_page = (root / "webui" / "src" / "pages" / "StoresPage.vue").read_text(
        encoding="utf-8"
    )
    app = (root / "webui" / "src" / "App.vue").read_text(
        encoding="utf-8"
    )
    for entitlement in (
        "module.automation",
        "module.finance",
        "module.storefront",
        "module.team",
        "module.delivery_ledger",
        "module.runtime_logs",
        "module.after_sales",
        "feature.procurement_association",
    ):
        assert entitlement in router
    assert "hasEntitlement(permission)" in more_page
    assert "canViewRuntimeLogs" in more_page
    assert "canUseStoreGroups" in stores_page
    assert 'hasEntitlement("feature.software_update")' in app
    assert 'hasEntitlement("account.view_others")' in app


def test_procurement_association_data_and_actions_are_owner_only(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "procurement-owner-only.db")
    store_id = database.add_store("门禁店铺", "wx-owner-only", "secret")
    database.upsert_order(
        Order(
            store_id=store_id,
            store_name="门禁店铺",
            order_id="owner-only-order",
            status=20,
            status_text="待发货",
            create_time=1_700_000_000,
            update_time=1_700_000_010,
            pay_time=1_700_000_005,
            total_amount=3990,
            product_amount=3990,
            freight_amount=0,
            discount_amount=0,
            buyer_name="测试买家",
            phone="13800000000",
            address="测试地址",
            waybill_id="",
            carrier="",
            merchant_notes="",
            items=[
                OrderItem(
                    product_id="product-1",
                    sku_id="sku-1",
                    title="测试商品",
                    sku_count=1,
                    sale_price=3990,
                )
            ],
            raw={},
        )
    )
    owner = NextUiController(
        database,
        auth_context=_context(
            INTERNAL_ACCOUNT_IDS[0],
            username="zpfly",
        ),
    )
    owner.dispatch(
        "orders.workbench.manual-purchase",
        {
            "store_id": store_id,
            "order_id": "owner-only-order",
            "platform": "1688",
            "purchase_order_no": "PURCHASE-SECRET",
            "purchase_amount_cents": 1234,
        },
    )

    ordinary = NextUiController(
        database,
        auth_context=_context("ordinary-account"),
    )
    listed = ordinary.dispatch(
        "orders.list",
        {"store_id": store_id},
    )["orders"][0]
    assert listed["purchase_order_no"] == ""
    assert listed["purchase_orders"] == []
    assert listed["procurement_tasks"] == []
    assert listed["procurement_state"] == "not_required"
    assert listed["cost"] == 0
    assert listed["profit"] == 0
    assert listed["fulfillment_stage"] == ""

    detail = ordinary.dispatch(
        "orders.workbench.detail",
        {"store_id": store_id, "order_id": "owner-only-order"},
    )
    assert detail["workbench"]["purchase_order_no"] == ""
    assert detail["procurement_links"] == []
    assert detail["product_purchase_links"] == []
    assert detail["procurement_accounts"] == []
    assert detail["dispatch_tasks"] == []
    assert detail["workbench"]["cost"] == 0
    assert detail["workbench"]["profit"] == 0
    assert detail["workbench"]["fulfillment_stage"] == ""

    updated = ordinary.dispatch(
        "orders.workbench.local-update",
        {
            "store_id": store_id,
            "order_id": "owner-only-order",
            "note": "普通订单备注仍可用",
        },
    )
    assert updated["workbench"]["note"] == "普通订单备注仍可用"
    for action, action_payload in (
        (
            "orders.workbench.local-update",
            {
                "store_id": store_id,
                "order_id": "owner-only-order",
                "purchase_order_no": "BYPASS",
            },
        ),
        (
            "orders.workbench.import",
            {
                "store_id": store_id,
                "records": [{
                    "order_id": "owner-only-order",
                    "purchase_order_no": "BYPASS",
                }],
            },
        ),
    ):
        with pytest.raises(
            PermissionError,
            match="此功能正在加急开发优化中",
        ):
            ordinary.dispatch(action, action_payload)

    for action in (
        "procurement.list",
        (
            "orders.procurement.link-upsert"
        ),
        (
            "orders.workbench.manual-purchase"
        ),
    ):
        with pytest.raises(
            PermissionError,
            match="此功能正在加急开发优化中",
        ):
            ordinary.dispatch(action, {})
