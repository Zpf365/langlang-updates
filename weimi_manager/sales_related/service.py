from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping
from typing import Any

from .aftersale_rules import (
    RefundDecision,
    RefundRequest,
    WhitelistPolicy,
    evaluate_refund_request,
)
from .links import ImportResult, parse_link_import
from .models import SkuCandidate, SkuSelection
from .repository import SalesRelatedRepository
from .sku_rules import select_sku
from .state_machine import SalesEvent, SalesTaskStatus


class SalesRelatedService:
    """Coordinate local sales-related rules without calling platform APIs."""

    def __init__(
        self,
        repository: SalesRelatedRepository,
        *,
        permission_checker: Callable[[str], bool] | None = None,
    ) -> None:
        self.repository = repository
        self.permission_checker = permission_checker or (lambda _permission: True)

    def _require(self, permission: str) -> None:
        if not self.permission_checker(permission):
            raise PermissionError("当前账号没有执行该操作的权限")

    def import_links(
        self,
        text: str,
        *,
        allow_duplicates: bool = False,
    ) -> ImportResult:
        self._require("procurement")
        return parse_link_import(
            text,
            existing_links=self.repository.list_canonical_urls(),
            allow_duplicates=allow_duplicates,
        )

    def create_tasks_from_text(
        self,
        text: str,
        *,
        store_id: int,
        store_app_id: str,
        sku_selection_mode: str,
        purchase_quantity: int = 1,
        device_id: str = "",
        buyer_ref: str = "",
        specified_sku_id: str = "",
        specified_specs: Mapping[str, str] | None = None,
        price_cap_cents: int = 0,
        created_by: str = "",
        allow_duplicates: bool = False,
        simulation_mode: bool = True,
    ) -> tuple[ImportResult, list[dict[str, Any]]]:
        parsed = self.import_links(
            text,
            allow_duplicates=allow_duplicates,
        )
        created = []
        for canonical_url in parsed.accepted:
            created.append(
                self.repository.create_task(
                    source_url=canonical_url,
                    canonical_url=canonical_url,
                    store_id=store_id,
                    store_app_id=store_app_id,
                    device_id=device_id,
                    buyer_ref=buyer_ref,
                    sku_selection_mode=sku_selection_mode,
                    specified_sku_id=specified_sku_id,
                    specified_specs=specified_specs,
                    purchase_quantity=purchase_quantity,
                    price_cap_cents=price_cap_cents,
                    created_by=created_by,
                    simulation_mode=simulation_mode,
                )
            )
        return parsed, created

    def prepare_task_sku(
        self,
        task_id: str,
        candidates: Iterable[SkuCandidate],
        *,
        product_id: str = "",
        actor: str = "",
    ) -> SkuSelection:
        self._require("procurement")
        task = self.repository.get_task(task_id)
        if task is None:
            raise KeyError("任务不存在")
        current = SalesTaskStatus(task["status"])
        if current not in {
            SalesTaskStatus.PENDING,
            SalesTaskStatus.PRODUCT_IDENTIFIED,
        }:
            raise ValueError("任务当前状态不能重新选择 SKU")
        try:
            specs = json.loads(task["specified_specs_json"] or "{}")
        except (TypeError, ValueError):
            specs = {}
        candidate_list = list(candidates)
        selection = select_sku(
            candidate_list,
            task["sku_selection_mode"],
            int(task["purchase_quantity"]),
            specified_sku_id=str(task["specified_sku_id"] or ""),
            specified_specs=specs if isinstance(specs, dict) else {},
            max_price_cents=(
                int(task["price_cap_cents"])
                if int(task["price_cap_cents"] or 0) > 0
                else None
            ),
        )
        for candidate in candidate_list:
            self.repository.add_sku_snapshot(
                task_id,
                candidate,
                product_id=product_id,
            )
        if not selection.ok:
            self.repository.set_task_reason(
                task_id,
                selection.reason,
                actor=actor,
            )
            return selection
        if current == SalesTaskStatus.PENDING:
            self.repository.transition_task(
                task_id,
                SalesEvent.IDENTIFY_PRODUCT,
                actor=actor,
            )
        self.repository.transition_task(
            task_id,
            SalesEvent.CONFIRM_SKU,
            actor=actor,
            reason=(
                f"已选择 {selection.selected.sku_id}"
                if selection.selected
                else ""
            ),
        )
        return selection

    def record_checkout_verified(
        self,
        task_id: str,
        *,
        actor: str = "",
    ) -> dict[str, Any]:
        self._require("procurement")
        return self.repository.transition_task(
            task_id,
            SalesEvent.VERIFY_CHECKOUT,
            actor=actor,
            reason="结算页商品、数量、价格、优惠、运费和总额已核验",
        )

    def request_user_confirmation(self, task_id: str, *, actor: str = "") -> dict[str, Any]:
        self._require("procurement")
        return self.repository.transition_task(
            task_id,
            SalesEvent.REQUEST_USER_CONFIRMATION,
            actor=actor,
            reason="等待用户确认提交",
        )

    def evaluate_simulated_refund(
        self,
        policy: WhitelistPolicy,
        request: RefundRequest,
    ) -> RefundDecision:
        self._require("after_sales")
        return evaluate_refund_request(policy, request)
