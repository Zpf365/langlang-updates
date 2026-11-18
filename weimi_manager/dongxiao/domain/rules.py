from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Iterable, Mapping, Protocol

if TYPE_CHECKING:
    from .models import Sku


MAX_MONEY_CENTS = 999_999_999_999
SENSITIVE_KEY_PARTS = (
    "authorization",
    "cookie",
    "password",
    "secret",
    "token",
    "access_key",
    "private_key",
)


class PermissionDenied(PermissionError):
    pass


class StoreIsolationError(ValueError):
    pass


class OrderTrustError(ValueError):
    pass


class SkuSelectionFailure(StrEnum):
    NO_SKU = "no_sku"
    UNAVAILABLE = "unavailable"
    OUT_OF_STOCK = "out_of_stock"
    INSUFFICIENT_STOCK = "insufficient_stock"
    PRICE_CHANGED = "price_changed"
    OVER_PRICE_CAP = "over_price_cap"
    BELOW_LIMIT = "below_limit"
    ABOVE_LIMIT = "above_limit"


class SkuLike(Protocol):
    store_id: int
    product_id: str
    sku_id: str
    price_cents: int
    stock: int
    available: bool
    purchase_min: int
    purchase_max: int | None


@dataclass(frozen=True, slots=True)
class SkuSelection:
    selected: "Sku | None"
    quantity: int
    failure: SkuSelectionFailure | None = None
    reason: str = ""


@dataclass(frozen=True, slots=True)
class ProductImportResult:
    accepted: tuple[str, ...] = ()
    duplicates: tuple[str, ...] = ()
    invalid: tuple[str, ...] = ()


def normalize_product_ids(
    *,
    store_id: int,
    values: Iterable[str],
) -> ProductImportResult:
    if isinstance(store_id, bool) or not isinstance(store_id, int) or store_id <= 0:
        raise ValueError("店铺 ID 必须为正整数")

    accepted: list[str] = []
    duplicates: list[str] = []
    invalid: list[str] = []
    seen: set[str] = set()
    for value in values:
        product_id = str(value or "").strip()
        if not product_id:
            invalid.append("")
            continue
        if product_id in seen:
            duplicates.append(product_id)
            continue
        seen.add(product_id)
        accepted.append(product_id)
    return ProductImportResult(
        accepted=tuple(accepted),
        duplicates=tuple(duplicates),
        invalid=tuple(invalid),
    )


def validate_money_cents(
    value: object,
    *,
    field: str = "金额",
    allow_zero: bool = True,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field}必须使用整数分")
    minimum = 0 if allow_zero else 1
    if value < minimum:
        raise ValueError(f"{field}必须大于等于 {minimum} 分")
    if value > MAX_MONEY_CENTS:
        raise ValueError(f"{field}超过允许的最大值")
    return value


def utc_now() -> datetime:
    return datetime.now(UTC)


def ensure_utc(value: datetime, *, field: str = "时间") -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{field}必须是 datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field}必须包含时区")
    return value.astimezone(UTC)


def to_utc_iso(value: datetime, *, field: str = "时间") -> str:
    return ensure_utc(value, field=field).isoformat(timespec="seconds")


def parse_utc_iso(value: str, *, field: str = "时间") -> datetime:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field}不能为空")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field}不是有效的 ISO 8601 时间") from exc
    return ensure_utc(parsed, field=field)


def ensure_same_store(
    expected_store_id: int,
    actual_store_id: int,
    *,
    label: str = "业务对象",
) -> None:
    if expected_store_id != actual_store_id:
        raise StoreIsolationError(
            f"{label}不属于当前店铺，禁止跨店铺操作"
        )


def require_permission(
    permission_checker,
    permission: str,
) -> None:
    if not callable(permission_checker):
        raise TypeError("权限检查器必须可调用")
    if not permission_checker(str(permission)):
        raise PermissionDenied("当前账号没有执行该操作的权限")


def sanitize_audit_detail(
    value: object,
    *,
    depth: int = 0,
) -> object:
    if depth > 8:
        return "[truncated]"
    if isinstance(value, Mapping):
        output: dict[str, object] = {}
        for raw_key, child in value.items():
            key = str(raw_key)
            normalized = key.casefold().replace("-", "_")
            if any(part in normalized for part in SENSITIVE_KEY_PARTS):
                output[key] = "[REDACTED]"
            else:
                output[key] = sanitize_audit_detail(child, depth=depth + 1)
        return output
    if isinstance(value, (list, tuple)):
        return [
            sanitize_audit_detail(item, depth=depth + 1)
            for item in value
        ]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def task_request_fingerprint(
    *,
    task_type: str,
    store_app_id: str,
    metadata: Mapping[str, object],
) -> str:
    payload = {
        "task_type": str(task_type).strip(),
        "store_app_id": str(store_app_id).strip(),
        "metadata": sanitize_audit_detail(dict(metadata)),
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def select_sku(
    *,
    store_id: int,
    product_id: str,
    candidates: Iterable[SkuLike],
    quantity: int,
    expected_price_cents: int | None = None,
    max_price_cents: int | None = None,
) -> SkuSelection:
    store_id = _validate_store_id(store_id)
    product_id = _required_product_id(product_id)
    quantity = _validate_positive_int(quantity, field="购买数量")
    expected = (
        validate_money_cents(expected_price_cents, field="预期 SKU 价格")
        if expected_price_cents is not None
        else None
    )
    maximum = (
        validate_money_cents(max_price_cents, field="SKU 价格上限")
        if max_price_cents is not None
        else None
    )

    sku_list = list(candidates)
    if not sku_list:
        return _sku_failure(SkuSelectionFailure.NO_SKU, quantity)

    valid: list[SkuLike] = []
    price_changed = False
    unavailable = False
    out_of_stock = False
    insufficient_stock = False
    over_price_cap = False
    below_limit = False
    above_limit = False
    for candidate in sku_list:
        ensure_same_store(store_id, candidate.store_id, label="SKU")
        if str(candidate.product_id) != product_id:
            raise StoreIsolationError("SKU 不属于当前商品，禁止跨商品组合")
        if expected is not None and int(candidate.price_cents) != expected:
            price_changed = True
            continue
        if not bool(candidate.available):
            unavailable = True
            continue
        stock = int(candidate.stock)
        if stock <= 0:
            out_of_stock = True
            continue
        if quantity > stock:
            insufficient_stock = True
            continue
        price = int(candidate.price_cents)
        if maximum is not None and price > maximum:
            over_price_cap = True
            continue
        minimum = max(1, int(candidate.purchase_min))
        if quantity < minimum:
            below_limit = True
            continue
        if (
            candidate.purchase_max is not None
            and quantity > int(candidate.purchase_max)
        ):
            above_limit = True
            continue
        valid.append(candidate)

    if not valid:
        if price_changed:
            return _sku_failure(
                SkuSelectionFailure.PRICE_CHANGED,
                quantity,
                "SKU 价格与任务快照不一致",
            )
        if over_price_cap:
            return _sku_failure(
                SkuSelectionFailure.OVER_PRICE_CAP,
                quantity,
                "SKU 价格超过任务价格上限",
            )
        if insufficient_stock:
            return _sku_failure(
                SkuSelectionFailure.INSUFFICIENT_STOCK,
                quantity,
                "SKU 库存不足，禁止自动拆单",
            )
        if out_of_stock:
            return _sku_failure(
                SkuSelectionFailure.OUT_OF_STOCK,
                quantity,
                "SKU 当前无库存",
            )
        if unavailable:
            return _sku_failure(
                SkuSelectionFailure.UNAVAILABLE,
                quantity,
                "SKU 当前不可售",
            )
        if above_limit:
            return _sku_failure(
                SkuSelectionFailure.ABOVE_LIMIT,
                quantity,
                "购买数量超过 SKU 限购",
            )
        if below_limit:
            return _sku_failure(
                SkuSelectionFailure.BELOW_LIMIT,
                quantity,
                "购买数量低于 SKU 起购数量",
            )
        return _sku_failure(SkuSelectionFailure.NO_SKU, quantity)

    selected = min(
        valid,
        key=lambda item: (int(item.price_cents), str(item.sku_id)),
    )
    return SkuSelection(selected=selected, quantity=quantity)


def _sku_failure(
    failure: SkuSelectionFailure,
    quantity: int,
    reason: str = "",
) -> SkuSelection:
    return SkuSelection(
        selected=None,
        quantity=quantity,
        failure=failure,
        reason=reason,
    )


def _validate_store_id(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError("店铺 ID 必须为正整数")
    return value


def _required_product_id(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError("商品 ID 不能为空")
    return text


def _validate_positive_int(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field}必须是整数")
    if value <= 0:
        raise ValueError(f"{field}必须大于 0")
    return value
