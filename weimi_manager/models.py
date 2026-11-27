from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


class UploadPreflightError(ValueError):
    """A local preflight failure detected before the product was submitted."""


@dataclass
class ProductQueryResult:
    store_ids: list[int] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    message: str = ""


def product_matches_tab(row: Mapping[str, Any], tab_key: str) -> bool:
    status = int(row.get("status") or 0)
    edit_status = int(row.get("edit_status") or 0)
    try:
        raw = json.loads(row.get("raw_json") or "{}")
    except (TypeError, ValueError):
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    if raw.get("_private_source") == "shop-faas":
        return status in {
            "sales": {5},
            "audit": {2},
            "failed": {3},
            "draft": {0, 1},
            "offline": {11, 12, 13, 14, 15, 20},
            "recycle": {6},
            "strategy": {7},
        }.get(tab_key, {status})
    audit_marker = str(
        raw.get("audit_status") or raw.get("audit_result") or raw.get("audit_state") or ""
    ).lower()
    if tab_key == "audit":
        return status == 0 and (
            edit_status in {1, 2, 3}
            or any(word in audit_marker for word in ("pending", "audit", "审核中"))
        )
    if tab_key == "failed":
        return status == 0 and (
            edit_status in {4, 5}
            or any(word in audit_marker for word in ("fail", "reject", "失败", "驳回"))
        )
    if tab_key == "draft":
        return status == 0 and edit_status == 0 and not audit_marker
    if tab_key == "strategy":
        return bool(raw.get("timing_onsale_info") or raw.get("listing_strategy"))
    return status in {
        "sales": {5},
        "offline": {11, 12, 13, 14, 15, 20},
        "recycle": {6},
    }.get(tab_key, {status})


ORDER_STATUS = {
    10: "待付款",
    12: "礼物待收下",
    13: "待成团",
    17: "待核销",
    20: "待发货",
    21: "部分发货",
    30: "待收货",
    100: "已完成",
    200: "售后退款完成",
    250: "已取消",
}

COUNTABLE_TRANSACTION_STATUSES = frozenset({17, 20, 21, 30, 100})


def order_counts_as_transaction(value: Any) -> bool:
    """Return whether an order represents a claimable or fulfilled sale."""
    try:
        return int(value or 0) in COUNTABLE_TRANSACTION_STATUSES
    except (TypeError, ValueError):
        return False


def is_unclaimed_gift_order(row: Mapping[str, Any]) -> bool:
    """Return whether a WeChat gift order has not yet been claimed.

    WeChat keeps an unclaimed gift at status 12. Older cached rows can lack
    ``raw_json``, so the safe default is to keep those rows out of sale and
    order-reporting views until the platform confirms they were claimed.
    """
    try:
        status = int(row.get("status") or 0)
    except (TypeError, ValueError):
        return False
    if status != 12:
        return False
    try:
        raw = json.loads(row.get("raw_json") or "{}")
    except (TypeError, ValueError):
        raw = {}
    if not isinstance(raw, Mapping):
        return True
    return bool(raw.get("is_present", True))


PRODUCT_STATUS = {
    0: "初始",
    5: "上架",
    6: "回收站",
    11: "下架",
    12: "售罄下架",
    13: "违规下架",
    14: "保证金违规下架",
    15: "品牌到期下架",
    20: "封禁下架",
}

SHIPMENT_STATUS = {
    "precreated": "已预取号",
    "create_failed": "取号失败",
    "created": "已取号",
    "printed": "已打印",
    "shipped": "已发货",
}

AFTER_SALE_STATUS = {
    "USER_CANCELD": "用户已取消",
    "MERCHANT_PROCESSING": "商家受理中",
    "MERCHANT_REJECT_REFUND": "商家拒绝退款",
    "MERCHANT_REJECT_RETURN": "商家拒绝退货",
    "USER_WAIT_RETURN": "待买家退货",
    "RETURN_CLOSED": "退货已关闭",
    "MERCHANT_WAIT_RECEIPT": "待商家收货",
    "MERCHANT_OVERDUE_REFUND": "商家逾期未退款",
    "MERCHANT_REFUND_SUCCESS": "退款完成",
    "MERCHANT_RETURN_SUCCESS": "退货退款完成",
    "PLATFORM_REFUNDING": "平台退款中",
    "PLATFORM_REFUND_FAIL": "平台退款失败",
    "USER_WAIT_CONFIRM": "待用户确认",
    "MERCHANT_REFUND_RETRY_FAIL": "打款失败",
    "MERCHANT_FAIL": "售后关闭",
    "USER_WAIT_CONFIRM_UPDATE": "待用户确认协商",
    "USER_WAIT_HANDLE_MERCHANT_AFTER_SALE": "待用户处理售后",
    "WAIT_PACKAGE_INTERCEPT": "物流拦截中",
    "MERCHANT_REJECT_EXCHANGE": "商家拒绝换货",
    "MERCHANT_REJECT_RESHIP": "商家拒绝补寄",
    "USER_WAIT_RECEIPT": "待用户收货",
    "MERCHANT_EXCHANGE_SUCCESS": "换货完成",
    "MERCHANT_WAIT_RESHIP": "待商家补寄",
    "MERCHANT_RESHIP_OVERDUE": "补寄超时",
    "RESHIP_SUCCESS": "补寄完成",
}


def is_pending_purchase_order(
    order: Mapping[str, Any],
    workbench: Mapping[str, Any],
    has_after_sale: bool,
) -> bool:
    """Return whether a paid order still needs purchasing."""
    try:
        status = int(order.get("status") or 0)
        pay_time = int(order.get("pay_time") or 0)
    except (TypeError, ValueError):
        return False
    if status != 20 or pay_time <= 0 or has_after_sale:
        return False
    return not bool(workbench.get("purchase_order_no"))


@dataclass(slots=True)
class Store:
    id: int
    name: str
    app_id: str
    group_name: str
    status: str
    last_sync_at: str | None
    created_at: str
    web_status: str = "未登录"
    web_status_detail: str = ""
    web_checked_at: str | None = None
    web_logged_in_at: str | None = None


@dataclass(slots=True)
class OrderItem:
    product_id: str
    sku_id: str
    title: str
    sku_count: int
    sale_price: int
    thumb_url: str = ""
    sku_attributes: str = ""


@dataclass(slots=True)
class Order:
    store_id: int
    store_name: str
    order_id: str
    status: int
    status_text: str
    create_time: int
    update_time: int
    pay_time: int
    total_amount: int
    product_amount: int
    freight_amount: int
    discount_amount: int
    buyer_name: str
    phone: str
    address: str
    waybill_id: str
    carrier: str
    merchant_notes: str
    items: list[OrderItem] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass(slots=True)
class Product:
    store_id: int
    store_name: str
    product_id: str
    title: str
    status: int
    status_text: str
    edit_status: int
    min_price: int
    sku_count: int
    stock_total: int
    spu_code: str
    out_product_id: str
    head_img: str
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass(slots=True)
class SyncResult:
    resource: str
    count: int
    message: str = ""
    success: int = 0
    failed: int = 0
    cancelled: bool = False
    errors: list[str] = field(default_factory=list)
    pending: int = 0

    @property
    def verification_pending(self) -> bool:
        return self.pending > 0


@dataclass(slots=True)
class ShippingAccount:
    store_id: int
    delivery_id: str
    delivery_name: str
    acct_id: str
    shop_id: str
    site_code: str
    site_name: str
    status: int
    available: int
    monthly_card: str
    sender_name: str
    sender_mobile: str
    sender_address: dict[str, str]
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass(slots=True)
class ShippingTemplate:
    store_id: int
    delivery_id: str
    template_id: str
    template_name: str
    template_type: str
    is_default: bool
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

