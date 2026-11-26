from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from weimi_manager.db import Database
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
    role: str = "owner",
    offline: bool = False,
    permissions: dict | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        account_id=account_id,
        username=f"user-{account_id[:6]}",
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
    assert HIDDEN_MODULES.issubset(more_keys)
    assert all(payload["entitlements"].values())


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
        "procurement",
        "violations",
    }.issubset(primary_keys)
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
    ):
        assert entitlement in router
    assert "hasEntitlement(permission)" in more_page
    assert "canViewRuntimeLogs" in more_page
    assert "canUseStoreGroups" in stores_page
    assert 'hasEntitlement("feature.software_update")' in app
    assert 'hasEntitlement("account.view_others")' in app
