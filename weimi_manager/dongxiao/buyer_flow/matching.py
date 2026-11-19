from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from ..domain.rules import ensure_utc, validate_money_cents


class OrderMatchState(StrEnum):
    EXACT = "exact"
    SPLIT = "split"
    AMBIGUOUS = "ambiguous"
    WAITING = "waiting"
    NOT_FOUND = "not_found"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ExpectedOrderItem:
    product_id: str
    sku_id: str
    quantity: int
    unit_price_cents: int

    def __post_init__(self) -> None:
        if not self.product_id or not self.sku_id:
            raise ValueError("订单匹配商品和 SKU 不能为空")
        if self.quantity <= 0:
            raise ValueError("订单匹配数量必须为正整数")
        validate_money_cents(
            self.unit_price_cents,
            field="订单匹配单价",
            allow_zero=False,
        )


@dataclass(frozen=True, slots=True)
class CandidateOrderItem:
    product_id: str
    sku_id: str
    quantity: int
    unit_price_cents: int

    def __post_init__(self) -> None:
        if not self.product_id or not self.sku_id:
            raise ValueError("候选订单商品和 SKU 不能为空")
        if self.quantity <= 0:
            raise ValueError("候选订单数量必须为正整数")
        validate_money_cents(
            self.unit_price_cents,
            field="候选订单单价",
            allow_zero=False,
        )


@dataclass(frozen=True, slots=True)
class CandidateOrder:
    order_id: str
    status: str
    total_amount_cents: int
    created_at: datetime
    items: tuple[CandidateOrderItem, ...]

    def __post_init__(self) -> None:
        if not self.order_id.strip():
            raise ValueError("候选订单号不能为空")
        validate_money_cents(self.total_amount_cents, field="候选订单金额")
        object.__setattr__(
            self,
            "created_at",
            ensure_utc(self.created_at, field="候选订单创建时间"),
        )


@dataclass(frozen=True, slots=True)
class OrderMatchResult:
    state: OrderMatchState
    candidates: tuple[tuple[str, int], ...] = ()
    reason: str = ""


def match_candidate_orders(
    expected: tuple[ExpectedOrderItem, ...],
    candidates: tuple[CandidateOrder, ...],
    *,
    not_before: datetime,
    now: datetime,
    late_after: timedelta = timedelta(minutes=10),
) -> OrderMatchResult:
    expected_at = ensure_utc(not_before, field="订单匹配起始时间")
    current = ensure_utc(now, field="订单匹配当前时间")
    expected_counts: dict[tuple[str, str], int] = {}
    for item in expected:
        key = (item.product_id, item.sku_id)
        expected_counts[key] = expected_counts.get(key, 0) + item.quantity
    scored: list[tuple[str, int]] = []
    covered: set[tuple[str, str]] = set()
    for candidate in candidates:
        if candidate.created_at < expected_at:
            continue
        candidate_counts: dict[tuple[str, str], int] = {}
        for item in candidate.items:
            key = (item.product_id, item.sku_id)
            candidate_counts[key] = candidate_counts.get(key, 0) + item.quantity
        matched = sum(
            min(quantity, candidate_counts.get(key, 0))
            for key, quantity in expected_counts.items()
        )
        if matched <= 0:
            continue
        total = sum(expected_counts.values())
        score = int(round(matched * 100 / max(1, total)))
        scored.append((candidate.order_id, score))
        covered.update(
            key for key in expected_counts if candidate_counts.get(key, 0) > 0
        )
    if not scored:
        if current - expected_at < late_after:
            return OrderMatchResult(
                OrderMatchState.WAITING,
                reason="官方订单可能延迟出现，等待后重新查询",
            )
        return OrderMatchResult(
            OrderMatchState.NOT_FOUND,
            reason="超过延迟窗口仍未找到候选订单",
        )
    scored.sort(key=lambda item: (-item[1], item[0]))
    if scored[0][1] == 100 and (
        len(scored) == 1 or scored[1][1] < 100
    ):
        return OrderMatchResult(OrderMatchState.EXACT, (scored[0],))
    if covered == set(expected_counts):
        return OrderMatchResult(
            OrderMatchState.SPLIT,
            tuple(scored),
            "匹配商品分布在多个子订单，需要整体确认",
        )
    if len(scored) > 1 and scored[0][1] == scored[1][1]:
        return OrderMatchResult(
            OrderMatchState.AMBIGUOUS,
            tuple(scored),
            "存在多个相同匹配分数候选订单，必须人工确认",
        )
    return OrderMatchResult(
        OrderMatchState.UNKNOWN,
        tuple(scored),
        "候选订单只匹配了部分商品或数量",
    )
