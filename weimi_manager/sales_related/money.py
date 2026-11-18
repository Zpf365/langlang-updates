from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation


def yuan_to_cents(value: str | int | Decimal) -> int:
    """Convert an exact yuan amount to integer cents without float arithmetic."""
    if isinstance(value, float):
        raise TypeError("金额不能使用浮点数，请使用字符串或 Decimal")
    text = str(value).strip().replace(",", "").replace("¥", "").replace("￥", "")
    if not text:
        raise ValueError("金额不能为空")
    try:
        amount = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError("金额格式不正确") from exc
    if not amount.is_finite() or amount < 0:
        raise ValueError("金额必须是大于等于 0 的有效数字")
    return int(
        (amount * Decimal(100)).quantize(
            Decimal("1"),
            rounding=ROUND_HALF_UP,
        )
    )


def cents_to_yuan(cents: int) -> str:
    value = int(cents)
    sign = "-" if value < 0 else ""
    value = abs(value)
    return f"{sign}{value // 100}.{value % 100:02d}"


def multiply_cents(unit_cents: int, quantity: int) -> int:
    unit = int(unit_cents)
    count = int(quantity)
    if unit < 0 or count < 1:
        raise ValueError("金额或数量不正确")
    return unit * count
