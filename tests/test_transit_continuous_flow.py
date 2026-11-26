from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest

import weimi_manager.webui.controller as controller_module
from weimi_manager.db import Database
from weimi_manager.models import Store
from weimi_manager.transit_shipping import (
    QINGHAI,
    TIBET,
    XINJIANG,
    TransitShippingManager,
    apply_target_rules,
)
from weimi_manager.webui.controller import NextUiController


def _template(
    template_id: str,
    *,
    linked: int,
    target: bool = False,
) -> dict[str, Any]:
    template: dict[str, Any] = {
        "templateId": template_id,
        "name": f"模板 {template_id}",
        "valuationType": 1,
        "sendTime": 7,
        "shippingMethod": 2,
        "isDefault": 1,
        "deliveryType": 1,
        "allFreightCalcMethod": {
            "freightCalcMethodList": [
                {
                    "isDefault": True,
                    "firstValAmount": 1,
                    "firstPrice": 700,
                    "secondValAmount": 1,
                    "secondPrice": 300,
                },
                {
                    "isDefault": False,
                    "addressInfo": [
                        {"provinceName": XINJIANG},
                        {"provinceName": TIBET},
                        {"provinceName": QINGHAI},
                    ],
                    "firstValAmount": 1,
                    "firstPrice": 1200,
                    "secondValAmount": 1,
                    "secondPrice": 900,
                },
            ]
        },
        "allConditionFreeDetail": {
            "conditionFreeDetailList": [{"amount": 9900}]
        },
        "notSendArea": {
            "addressInfo": [
                {"provinceName": XINJIANG},
                {"provinceName": "台湾省"},
            ]
        },
        "consolidateFlag": 0,
        "relatedProductsNum": linked,
    }
    if target:
        return apply_target_rules(template)
    return template


def _products(count: int = 10) -> list[dict[str, Any]]:
    return [
        {
            "id": str(1000 + index),
            "name": f"测试商品 {index}",
            "canTransit": True,
            "transitType": 0,
            "templateId": "T-LINKED",
        }
        for index in range(count)
    ]


def _store() -> Store:
    return Store(
        19,
        "再百宽栾商行",
        "wx-test",
        "测试分组",
        "启用",
        None,
        "2026-11-26 12:00:00",
        web_status="已登录",
    )


class FlowFixtureManager(TransitShippingManager):
    def __init__(
        self,
        db: Database,
        *,
        service_open: bool = False,
        product_rows: list[dict[str, Any]] | None = None,
        template_rows: list[dict[str, Any]] | None = None,
        product_failure_ids: set[str] | None = None,
        template_mismatch: bool = False,
    ) -> None:
        super().__init__(db)
        self._service_open = service_open
        self._product_rows = copy.deepcopy(product_rows or _products())
        self._template_rows = copy.deepcopy(
            template_rows
            or [
                _template("T-LINKED", linked=10),
                _template("T-UNLINKED", linked=0),
            ]
        )
        self._product_failure_ids = product_failure_ids or set()
        self._template_mismatch = template_mismatch
        self.calls: list[tuple[str, Any]] = []

    def service_status(self, store: Store) -> dict[str, Any]:
        self.calls.append(("service_status", self._service_open))
        return {
            "open": self._service_open,
            "openable": True,
            "grayed_out": False,
            "reason": "",
            "service": {},
        }

    def open_service(self, store: Store) -> dict[str, Any]:
        self.calls.append(("open_service", None))
        self._service_open = True
        return self.service_status(store)

    def products(
        self,
        store: Store,
        *,
        transit_type: int | None = None,
    ) -> list[dict[str, Any]]:
        self.calls.append(("products", transit_type))
        return copy.deepcopy(self._product_rows)

    def templates(self, store: Store) -> list[dict[str, Any]]:
        self.calls.append(("templates", None))
        return copy.deepcopy(self._template_rows)

    def set_products_transit(
        self,
        store: Store,
        rows: list[dict[str, Any]],
    ) -> dict[str, Any]:
        pending = [
            row for row in rows
            if bool(row.get("canTransit"))
            and int(row.get("transitType") or 0) != 1
        ]
        if not pending:
            return {
                "requested": 0,
                "already_transit": len(rows),
                "batch_count": 0,
                "items": [],
                "errors": [],
                "item_failures": [],
            }
        self.calls.append(("set_products_transit", len(pending)))
        for row in self._product_rows:
            if (
                str(row.get("id")) not in self._product_failure_ids
                and bool(row.get("canTransit"))
            ):
                row["transitType"] = 1
        return {
            "requested": len(pending),
            "already_transit": len(rows) - len(pending),
            "batch_count": (len(pending) + 49) // 50,
            "items": [],
            "errors": [],
            "item_failures": [],
        }

    def update_template(
        self,
        store: Store,
        template: dict[str, Any],
        rules: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        template_id = str(template.get("templateId") or "")
        self.calls.append(("update_template", template_id))
        if self._template_mismatch:
            return copy.deepcopy(template)
        updated = apply_target_rules(template, rules)
        updated["relatedProductsNum"] = int(
            template.get("relatedProductsNum") or 0
        )
        self._template_rows = [
            copy.deepcopy(updated)
            if str(row.get("templateId")) == template_id
            else row
            for row in self._template_rows
        ]
        return copy.deepcopy(updated)


def test_continuous_flow_writes_service_products_then_templates(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "flow.db")
    manager = FlowFixtureManager(database, service_open=False)
    store = _store()

    result = manager.run_continuous_flow(
        store,
        template_ids=["T-LINKED"],
    )

    assert result["success"] is True
    assert result["message"] == "全店中转连续流程完成"
    assert [name for name, _value in manager.calls[:3]] == [
        "service_status",
        "products",
        "templates",
    ]
    write_calls = [
        name
        for name, _value in manager.calls
        if name in {"open_service", "set_products_transit", "update_template"}
    ]
    assert write_calls == [
        "open_service",
        "set_products_transit",
        "update_template",
    ]
    assert result["aggregate"] == {
        "service_open": True,
        "products_ok": True,
        "templates_ok": True,
    }
    backup = database.get_setting(result["backup_key"])
    assert backup
    assert '"templateId":"T-LINKED"' in backup


def test_product_failure_lists_details_and_never_updates_templates(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "flow.db")
    rows = _products(3)
    rows[1]["id"] = "FAIL-2"
    manager = FlowFixtureManager(
        database,
        service_open=True,
        product_rows=rows,
        product_failure_ids={"FAIL-2"},
    )
    store = _store()

    result = manager.run_continuous_flow(
        store,
        template_ids=["T-LINKED"],
    )

    assert result["success"] is False
    assert result["failed_stage"] == "products"
    assert not any(
        name == "update_template"
        for name, _value in manager.calls
    )
    assert result["product_failures"] == [
        {
            "product_id": "FAIL-2",
            "name": "测试商品 1",
            "reason": "官方回读 transitType=0，未切换为中转",
            "manual_url": (
                "https://store.weixin.qq.com/"
                "shop/logisticsservice/remoteArea/home"
            ),
        }
    ]
    assert "服务开通" in result["completed_steps"]


def test_service_failure_stops_before_products_and_templates(
    tmp_path: Path,
) -> None:
    class ServiceClosedManager(FlowFixtureManager):
        def open_service(self, store: Store) -> dict[str, Any]:
            self.calls.append(("open_service", None))
            return {
                "open": False,
                "openable": False,
                "grayed_out": True,
                "reason": "店铺不满足开通条件",
                "service": {},
            }

    database = Database(tmp_path / "flow.db")
    manager = ServiceClosedManager(database, service_open=False)

    result = manager.run_continuous_flow(
        _store(),
        template_ids=["T-LINKED"],
    )

    assert result["success"] is False
    assert result["failed_stage"] == "service"
    assert not any(
        name in {"set_products_transit", "update_template"}
        for name, _value in manager.calls
    )
    assert result["steps"][1]["status"] == "skipped"
    assert result["steps"][2]["status"] == "skipped"


def test_template_field_mismatch_is_failure_not_success(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "flow.db")
    manager = FlowFixtureManager(
        database,
        service_open=True,
        template_mismatch=True,
    )
    store = _store()

    result = manager.run_continuous_flow(
        store,
        template_ids=["T-LINKED"],
    )

    assert result["success"] is False
    assert result["failed_stage"] == "templates"
    assert result["template_failures"][0]["template_id"] == "T-LINKED"
    assert result["template_failures"][0]["differences"]
    assert result["steps"][-1]["status"] == "skipped"


def test_product_batching_sends_50_50_and_remainder(
    tmp_path: Path,
) -> None:
    class BatchManager(TransitShippingManager):
        def __init__(self, db: Database) -> None:
            super().__init__(db)
            self.batch_sizes: list[int] = []

        def _request(
            self,
            store: Store,
            method: str,
            path: str,
            *,
            payload: dict[str, Any] | None = None,
            referer_path: str,
        ) -> dict[str, Any]:
            assert method == "POST"
            self.batch_sizes.append(len(payload["products"]))
            return {"code": 0, "data": []}

    manager = BatchManager(Database(tmp_path / "batches.db"))
    store = _store()

    result = manager.set_products_transit(store, _products(120))

    assert manager.batch_sizes == [50, 50, 20]
    assert result["batch_count"] == 3


def test_repeated_continuous_flow_is_idempotent(tmp_path: Path) -> None:
    database = Database(tmp_path / "flow.db")
    manager = FlowFixtureManager(database, service_open=True)
    store = _store()

    first = manager.run_continuous_flow(
        store,
        template_ids=["T-LINKED"],
    )
    manager.calls.clear()
    second = manager.run_continuous_flow(
        store,
        template_ids=["T-LINKED"],
    )

    assert first["success"] is True
    assert second["success"] is True
    assert [
        name
        for name, _value in manager.calls
        if name in {"open_service", "set_products_transit", "update_template"}
    ] == []
    assert second["product_summary"]["already_transit"] == 10
    assert second["template_summary"]["unchanged"] == 1


def test_apply_rpc_requires_one_explicit_write_confirmation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = Database(tmp_path / "rpc.db")
    store_id = database.add_store(
        "再百宽栾商行",
        "wx-test",
        "secret",
    )
    database.update_web_status(store_id, "已登录")
    manager = FlowFixtureManager(database, service_open=True)
    monkeypatch.setattr(
        controller_module,
        "TransitShippingManager",
        lambda _db: manager,
    )
    controller = NextUiController(database, project_dir=tmp_path)

    with pytest.raises(ValueError, match="确认"):
        controller.dispatch(
            "transit.shipping.apply",
            {
                "store_id": store_id,
                "template_ids": ["T-LINKED"],
            },
        )

    result = controller.dispatch(
        "transit.shipping.apply",
        {
            "store_id": store_id,
            "template_ids": ["T-LINKED"],
            "confirmed": True,
        },
    )
    assert result["success"] is True
    assert manager.calls.count(("open_service", None)) == 0
