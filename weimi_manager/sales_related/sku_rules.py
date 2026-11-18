from __future__ import annotations

from collections import Counter
from enum import StrEnum
from typing import Iterable, Mapping

from .models import SkuCandidate, SkuSelection


class SkuSelectionMode(StrEnum):
    ANY = "any"
    LOWEST_PRICE = "lowest_price"
    HIGHEST_PRICE = "highest_price"
    SPECIFIED = "specified"
    PRODUCT_DEFAULT = "product_default"


def _spec_key(specs: Mapping[str, str]) -> tuple[tuple[str, str], ...]:
    return tuple(
        sorted(
            (str(key).strip(), str(value).strip())
            for key, value in specs.items()
            if str(key).strip() or str(value).strip()
        )
    )


def _failure(
    quantity: int,
    code: str,
    message: str,
) -> SkuSelection:
    return SkuSelection(
        selected=None,
        quantity=quantity,
        reason_code=code,
        reason=message,
    )


def select_sku(
    candidates: Iterable[SkuCandidate],
    mode: SkuSelectionMode | str,
    quantity: int,
    *,
    specified_sku_id: str = "",
    specified_specs: Mapping[str, str] | None = None,
    max_price_cents: int | None = None,
) -> SkuSelection:
    """Select one purchasable SKU under all quantity and price constraints."""
    try:
        selected_mode = SkuSelectionMode(mode)
    except ValueError as exc:
        raise ValueError("未知 SKU 选择方式") from exc
    try:
        count = int(quantity)
    except (TypeError, ValueError) as exc:
        raise ValueError("购买数量必须是整数") from exc
    if count < 1:
        raise ValueError("购买数量必须大于等于 1")

    reasons: Counter[str] = Counter()
    valid: list[SkuCandidate] = []
    for candidate in candidates:
        if not str(candidate.sku_id or "").strip():
            reasons["missing_sku"] += 1
            continue
        if not candidate.complete:
            reasons["incomplete"] += 1
            continue
        if not candidate.available:
            reasons["unavailable"] += 1
            continue
        price = int(candidate.price_cents)
        stock = int(candidate.stock)
        if price <= 0:
            reasons["invalid_price"] += 1
            continue
        if max_price_cents is not None and price > int(max_price_cents):
            reasons["over_price_cap"] += 1
            continue
        if stock <= 0:
            reasons["out_of_stock"] += 1
            continue
        limit_min = max(1, int(candidate.limit_min or 1))
        limit_max = (
            int(candidate.limit_max)
            if candidate.limit_max is not None
            else None
        )
        if count < limit_min:
            reasons["below_limit"] += 1
            continue
        if limit_max is not None and count > limit_max:
            reasons["above_limit"] += 1
            continue
        if count > stock:
            reasons["insufficient_stock"] += 1
            continue
        valid.append(candidate)

    if not valid:
        if reasons.get("over_price_cap"):
            return _failure(
                count,
                "over_price_cap",
                "可售 SKU 的单价超过任务价格上限",
            )
        if reasons.get("insufficient_stock"):
            return _failure(
                count,
                "insufficient_stock",
                "没有单个 SKU 能一次满足购买数量，任务不拆单",
            )
        if reasons.get("above_limit"):
            return _failure(
                count,
                "above_limit",
                "购买数量超过平台限购数量",
            )
        if reasons.get("below_limit"):
            return _failure(
                count,
                "below_limit",
                "购买数量低于平台起购数量",
            )
        if reasons.get("incomplete"):
            return _failure(
                count,
                "incomplete",
                "SKU、价格、库存或规格信息不完整",
            )
        if reasons.get("unavailable"):
            return _failure(
                count,
                "unavailable",
                "当前没有可购买的 SKU",
            )
        if reasons.get("out_of_stock"):
            return _failure(
                count,
                "out_of_stock",
                "当前 SKU 均无库存",
            )
        return _failure(
            count,
            "no_sku",
            "商品没有可用 SKU",
        )

    selected: SkuCandidate
    if selected_mode == SkuSelectionMode.ANY:
        selected = valid[0]
    elif selected_mode == SkuSelectionMode.LOWEST_PRICE:
        selected = min(
            valid,
            key=lambda item: (int(item.price_cents), str(item.sku_id)),
        )
    elif selected_mode == SkuSelectionMode.HIGHEST_PRICE:
        selected = max(
            valid,
            key=lambda item: (int(item.price_cents), str(item.sku_id)),
        )
    elif selected_mode == SkuSelectionMode.PRODUCT_DEFAULT:
        defaults = [item for item in valid if item.default]
        if not defaults:
            return _failure(
                count,
                "default_missing",
                "商品页没有可验证的默认 SKU",
            )
        if len(defaults) > 1:
            return _failure(
                count,
                "default_ambiguous",
                "商品页返回了多个默认 SKU，请人工确认",
            )
        selected = defaults[0]
    else:
        sku_id = str(specified_sku_id or "").strip()
        specs = dict(specified_specs or {})
        if not sku_id and not specs:
            return _failure(
                count,
                "specified_missing",
                "指定 SKU 模式需要填写 SKU ID 或规格组合",
            )
        matches = [
            item
            for item in valid
            if (
                sku_id
                and str(item.sku_id) == sku_id
                or not sku_id
                and _spec_key(item.specs) == _spec_key(specs)
            )
        ]
        if not matches:
            return _failure(
                count,
                "specified_not_found",
                "指定 SKU 或规格组合当前不可购买",
            )
        if len(matches) > 1:
            return _failure(
                count,
                "specified_ambiguous",
                "指定规格匹配到多个 SKU，请使用 SKU ID",
            )
        selected = matches[0]
    return SkuSelection(
        selected=selected,
        quantity=count,
    )
