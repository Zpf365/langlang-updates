"""Sales-related task, order verification, and simulated after-sale rules."""

from .aftersale_rules import (
    RefundDecision,
    RefundRequest,
    RefundStatus,
    WhitelistPolicy,
    evaluate_refund_request,
    transition_refund,
)
from .links import ImportResult, normalize_share_link, parse_link_import
from .models import SkuCandidate, SkuSelection
from .repository import SalesRelatedRepository
from .service import SalesRelatedService
from .sku_rules import SkuSelectionMode, select_sku
from .state_machine import (
    SalesEvent,
    SalesTaskStatus,
    transition_task,
)

__all__ = [
    "ImportResult",
    "RefundDecision",
    "RefundRequest",
    "RefundStatus",
    "SalesEvent",
    "SalesRelatedRepository",
    "SalesRelatedService",
    "SalesTaskStatus",
    "SkuCandidate",
    "SkuSelection",
    "SkuSelectionMode",
    "WhitelistPolicy",
    "evaluate_refund_request",
    "normalize_share_link",
    "parse_link_import",
    "select_sku",
    "transition_refund",
    "transition_task",
]
