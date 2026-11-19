from __future__ import annotations

from dataclasses import dataclass

from ..domain.models import Product, Sku
from ..domain.rules import SkuSelectionFailure, SkuSelectionMode, select_sku
from .domain import (
    CartItemRequest,
    CartServerSnapshot,
    CartSkuMode,
    OutOfStockPolicy,
)


@dataclass(frozen=True, slots=True)
class CartPreviewItem:
    product_id: str
    title: str
    sku_id: str
    specs: tuple[tuple[str, str], ...]
    unit_price_cents: int
    quantity: int
    status: str
    reason: str = ""


def resolve_cart_item(
    *,
    product: Product | None,
    candidates: tuple[Sku, ...] | list[Sku],
    quantity: int,
    sku_mode: CartSkuMode | str,
    specified_sku_id: str,
    out_of_stock_policy: OutOfStockPolicy | str,
    expected_price_cents: int | None = None,
) -> tuple[CartItemRequest | None, str, str]:
    if product is None:
        return None, "missing", "商品不在当前店铺目录中"
    mode = CartSkuMode(sku_mode)
    policy = OutOfStockPolicy(out_of_stock_policy)
    selection_mode = {
        CartSkuMode.FIRST_AVAILABLE: SkuSelectionMode.ANY,
        CartSkuMode.LOWEST_PRICE: SkuSelectionMode.LOWEST_PRICE,
        CartSkuMode.SPECIFIED: SkuSelectionMode.SPECIFIED,
    }[mode]
    selection = select_sku(
        store_id=product.store_id,
        product_id=product.product_id,
        candidates=tuple(candidates),
        quantity=quantity,
        mode=selection_mode,
        specified_sku_id=specified_sku_id,
        expected_price_cents=expected_price_cents,
        product_state=product.state,
    )
    if selection.selected is None:
        failure = selection.failure or SkuSelectionFailure.INCOMPLETE_DATA
        if failure in {
            SkuSelectionFailure.OUT_OF_STOCK,
            SkuSelectionFailure.INSUFFICIENT_STOCK,
            SkuSelectionFailure.UNAVAILABLE,
        } and policy is OutOfStockPolicy.SKIP:
            return None, "skipped", selection.reason or "商品缺货，已按规则跳过"
        return None, "failed", selection.reason or "SKU 规则校验失败"
    selected = selection.selected
    return (
        CartItemRequest(
            product_id=product.product_id,
            sku_id=selected.sku_id,
            title=product.title,
            specs=selected.specs,
            unit_price_cents=selected.price_cents,
            quantity=selection.quantity,
        ),
        "ready",
        "",
    )


def verified_cart_success(
    snapshot: CartServerSnapshot | None,
    request: CartItemRequest,
    *,
    account_id: str,
    store_app_id: str,
) -> bool:
    if snapshot is None:
        return False
    return bool(
        snapshot.account_id == str(account_id)
        and snapshot.store_app_id == str(store_app_id)
        and snapshot.matches(request)
    )


def batch_number_for(index: int, products_per_batch: int) -> int:
    if index < 0:
        raise ValueError("商品序号不能为负数")
    if products_per_batch <= 0:
        raise ValueError("每批商品数量必须大于 0")
    return index // products_per_batch + 1


def aggregate_cart_results(statuses: list[str]) -> str:
    normalized = {
        (
            "succeeded"
            if str(item) == "completed"
            else "failed"
            if str(item) in {"failed", "cancelled"}
            else str(item)
        )
        for item in statuses
    }
    if not normalized:
        return "pending"
    if normalized == {"succeeded"}:
        return "completed"
    if normalized <= {"failed", "skipped"}:
        return "failed"
    if "unknown" in normalized or "running" in normalized:
        return "unknown"
    if "succeeded" in normalized:
        return "partially_failed"
    return "pending"
