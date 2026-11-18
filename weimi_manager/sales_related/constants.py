from __future__ import annotations

TASK_STATUSES = (
    "pending",
    "product_identified",
    "sku_confirmed",
    "checkout_verified",
    "awaiting_user_confirmation",
    "submission_pending_verification",
    "pending_payment",
    "paid",
    "shipped",
    "completed",
    "closed",
    "failed",
    "unknown",
)

TERMINAL_TASK_STATUSES = frozenset(
    {"completed", "closed", "failed"}
)

SKU_SELECTION_MODES = (
    "any",
    "lowest_price",
    "highest_price",
    "specified",
    "product_default",
)

REFUND_STATUSES = (
    "requested",
    "merchant_approved",
    "payment_accepted",
    "refunded",
    "processing",
    "rejected",
    "failed",
)

VERIFIED_BUYER_IDENTIFIER_TYPES = frozenset({"openid"})

TRUSTED_ORDER_VERIFICATION_SOURCES = frozenset(
    {
        "merchant_order_api",
        "simulated_trusted",
    }
)
