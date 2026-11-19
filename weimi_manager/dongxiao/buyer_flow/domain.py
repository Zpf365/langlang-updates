from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Mapping

from ..domain.rules import (
    ensure_utc,
    sanitize_audit_detail,
    validate_money_cents,
)


class BuyerProvider(StrEnum):
    OFFICIAL_WECHAT = "official_wechat"
    OFFICIAL_PARTNER = "official_partner"
    MANUAL_HANDOFF = "manual_handoff"
    MOCK = "mock"
    UNKNOWN = "unknown"


class BuyerIdentityStatus(StrEnum):
    UNVERIFIED = "unverified"
    USER_ASSERTED = "user_asserted"
    VERIFIED_OFFICIAL = "verified_official"
    VERIFIED_MOCK = "verified_mock"
    EXPIRED = "expired"
    REVOKED = "revoked"


class BuyerAccountStatus(StrEnum):
    PENDING_AUTHORIZATION = "pending_authorization"
    ACTIVE = "active"
    EXPIRED = "expired"
    DISCONNECTED = "disconnected"
    MOCK_READY = "mock_ready"
    OFFICIAL_UNSUPPORTED = "official_unsupported"


class BuyerCapabilityState(StrEnum):
    AVAILABLE = "available"
    AWAITING_AUTHORIZATION = "awaiting_authorization"
    MANUAL_HANDOFF_ONLY = "manual_handoff_only"
    OFFICIAL_UNSUPPORTED = "official_unsupported"
    MOCK_ONLY = "mock_only"


class BuyerFlowMode(StrEnum):
    AUTO_SELECT = "auto_select"
    MANUAL_SELECT = "manual_select"
    MANUAL_HANDOFF = "manual_handoff"
    MOCK = "mock"


class BuyerFlowStatus(StrEnum):
    DRAFT = "draft"
    PREPARED = "prepared"
    BLOCKED_OFFICIAL_UNSUPPORTED = "blocked_official_unsupported"
    WAITING_USER_HANDOFF = "waiting_user_handoff"
    WAITING_ORDER_ID = "waiting_order_id"
    PAUSED = "paused"
    CANCELLED = "cancelled"
    ORDER_VERIFIED = "order_verified"
    FAILED = "failed"
    UNKNOWN = "unknown"


class BuyerItemStatus(StrEnum):
    SELECTED = "selected"
    SKIPPED = "skipped"
    INVALID = "invalid"


class BuyerOrderVerificationStatus(StrEnum):
    UNVERIFIED = "unverified"
    PENDING_PAYMENT = "pending_payment"
    STATUS_MISMATCH = "status_mismatch"
    NOT_FOUND = "not_found"


@dataclass(frozen=True, slots=True)
class BuyerAdapterCapability:
    state: BuyerCapabilityState
    official_login: bool
    official_profile: bool
    official_cart_write: bool
    official_cart_read: bool
    official_order_create: bool
    official_order_read: bool
    manual_handoff: bool
    reason: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "state", BuyerCapabilityState(self.state))
        for name in (
            "official_login",
            "official_profile",
            "official_cart_write",
            "official_cart_read",
            "official_order_create",
            "official_order_read",
            "manual_handoff",
        ):
            object.__setattr__(self, name, bool(getattr(self, name)))
        object.__setattr__(self, "reason", str(self.reason or "")[:2000])

    @property
    def full_automated_flow(self) -> bool:
        return all(
            (
                self.official_login,
                self.official_profile,
                self.official_cart_write,
                self.official_cart_read,
                self.official_order_create,
                self.official_order_read,
            )
        )


@dataclass(frozen=True, slots=True)
class BuyerAccount:
    buyer_account_id: str
    display_name: str
    account_ref_hash: str
    provider: BuyerProvider
    identity_status: BuyerIdentityStatus
    status: BuyerAccountStatus
    authorization_at: datetime | None
    last_verified_at: datetime | None
    created_at: datetime
    updated_at: datetime
    is_current: bool = False
    status_reason: str = ""

    def __post_init__(self) -> None:
        for name in (
            "buyer_account_id",
            "display_name",
            "account_ref_hash",
        ):
            text = str(getattr(self, name) or "").strip()
            if not text:
                raise ValueError(f"{name}不能为空")
            object.__setattr__(self, name, text)
        object.__setattr__(self, "provider", BuyerProvider(self.provider))
        object.__setattr__(
            self,
            "identity_status",
            BuyerIdentityStatus(self.identity_status),
        )
        object.__setattr__(self, "status", BuyerAccountStatus(self.status))
        for name in ("authorization_at", "last_verified_at"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, ensure_utc(value, field=name))
        created = ensure_utc(self.created_at, field="买家账号创建时间")
        updated = ensure_utc(self.updated_at, field="买家账号更新时间")
        if updated < created:
            raise ValueError("买家账号更新时间不能早于创建时间")
        object.__setattr__(self, "created_at", created)
        object.__setattr__(self, "updated_at", updated)
        object.__setattr__(self, "is_current", bool(self.is_current))
        object.__setattr__(
            self,
            "status_reason",
            str(self.status_reason or "")[:2000],
        )

    @property
    def verified_identity(self) -> bool:
        return self.identity_status in {
            BuyerIdentityStatus.VERIFIED_OFFICIAL,
            BuyerIdentityStatus.VERIFIED_MOCK,
        }


@dataclass(frozen=True, slots=True)
class BuyerFlowItem:
    item_id: str
    run_id: str
    product_id: str
    title: str
    sku_id: str
    specs: tuple[tuple[str, str], ...]
    unit_price_cents: int
    quantity: int
    stock: int
    status: BuyerItemStatus
    reason: str = ""

    def __post_init__(self) -> None:
        for name in (
            "item_id",
            "run_id",
            "product_id",
            "title",
            "sku_id",
        ):
            text = str(getattr(self, name) or "").strip()
            if not text:
                raise ValueError(f"{name}不能为空")
            object.__setattr__(self, name, text)
        object.__setattr__(
            self,
            "specs",
            tuple(sorted((str(k), str(v)) for k, v in self.specs)),
        )
        object.__setattr__(
            self,
            "unit_price_cents",
            validate_money_cents(
                self.unit_price_cents,
                field="买家流程商品单价",
                allow_zero=False,
            ),
        )
        if self.quantity <= 0:
            raise ValueError("买家流程商品数量必须为正整数")
        if self.stock < 0:
            raise ValueError("买家流程商品库存不能为负数")
        object.__setattr__(self, "status", BuyerItemStatus(self.status))
        object.__setattr__(self, "reason", str(self.reason or "")[:1000])

    @property
    def subtotal_cents(self) -> int:
        return self.unit_price_cents * self.quantity


@dataclass(frozen=True, slots=True)
class BuyerFlowRun:
    run_id: str
    store_id: int | None
    store_app_id: str
    store_name: str
    buyer_account_id: str
    mode: BuyerFlowMode
    status: BuyerFlowStatus
    idempotency_key: str
    requested_count: int
    item_count: int
    merchandise_total_cents: int
    created_at: datetime
    updated_at: datetime
    expires_at: datetime | None = None
    handoff_token: str = ""
    order_id: str = ""
    verification_status: BuyerOrderVerificationStatus = (
        BuyerOrderVerificationStatus.UNVERIFIED
    )
    status_reason: str = ""
    capability: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in (
            "run_id",
            "store_app_id",
            "store_name",
            "buyer_account_id",
            "idempotency_key",
        ):
            text = str(getattr(self, name) or "").strip()
            if not text:
                raise ValueError(f"{name}不能为空")
            object.__setattr__(self, name, text)
        object.__setattr__(self, "store_id", _optional_store(self.store_id))
        object.__setattr__(self, "mode", BuyerFlowMode(self.mode))
        object.__setattr__(self, "status", BuyerFlowStatus(self.status))
        for name in ("requested_count", "item_count"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{name}必须是整数")
            if value < 0:
                raise ValueError(f"{name}不能为负数")
        if not 1 <= self.requested_count <= 10:
            raise ValueError("买家流程商品数量必须在 1 到 10 之间")
        object.__setattr__(
            self,
            "merchandise_total_cents",
            validate_money_cents(
                self.merchandise_total_cents,
                field="买家流程商品总额",
            ),
        )
        created = ensure_utc(self.created_at, field="买家流程创建时间")
        updated = ensure_utc(self.updated_at, field="买家流程更新时间")
        if updated < created:
            raise ValueError("买家流程更新时间不能早于创建时间")
        object.__setattr__(self, "created_at", created)
        object.__setattr__(self, "updated_at", updated)
        object.__setattr__(
            self,
            "expires_at",
            ensure_utc(self.expires_at, field="买家流程过期时间")
            if self.expires_at is not None
            else None,
        )
        object.__setattr__(self, "handoff_token", str(self.handoff_token or ""))
        object.__setattr__(self, "order_id", str(self.order_id or "").strip())
        object.__setattr__(
            self,
            "verification_status",
            BuyerOrderVerificationStatus(self.verification_status),
        )
        object.__setattr__(
            self,
            "status_reason",
            str(self.status_reason or "")[:2000],
        )
        object.__setattr__(
            self,
            "capability",
            sanitize_audit_detail(dict(self.capability)),
        )


@dataclass(frozen=True, slots=True)
class BuyerOrderLink:
    link_id: str
    run_id: str
    store_id: int | None
    store_app_id: str
    buyer_account_id: str
    platform_order_id: str
    status: BuyerOrderVerificationStatus
    total_amount_cents: int
    item_count: int
    created_at: datetime
    verified_at: datetime
    detail: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in (
            "link_id",
            "run_id",
            "store_app_id",
            "buyer_account_id",
            "platform_order_id",
        ):
            text = str(getattr(self, name) or "").strip()
            if not text:
                raise ValueError(f"{name}不能为空")
            object.__setattr__(self, name, text)
        object.__setattr__(self, "store_id", _optional_store(self.store_id))
        object.__setattr__(self, "status", BuyerOrderVerificationStatus(self.status))
        object.__setattr__(
            self,
            "total_amount_cents",
            validate_money_cents(
                self.total_amount_cents,
                field="买家订单总额",
            ),
        )
        if self.item_count < 0:
            raise ValueError("买家订单商品数不能为负数")
        object.__setattr__(
            self,
            "created_at",
            ensure_utc(self.created_at, field="买家订单关联创建时间"),
        )
        object.__setattr__(
            self,
            "verified_at",
            ensure_utc(self.verified_at, field="买家订单核验时间"),
        )
        object.__setattr__(
            self,
            "detail",
            sanitize_audit_detail(dict(self.detail)),
        )


@dataclass(frozen=True, slots=True)
class BuyerSelectionItem:
    product_id: str
    title: str
    sku_id: str
    specs: tuple[tuple[str, str], ...]
    unit_price_cents: int
    quantity: int
    stock: int


@dataclass(frozen=True, slots=True)
class BuyerOrderPreview:
    run_id: str
    store_name: str
    buyer_display_name: str
    items: tuple[BuyerFlowItem, ...]
    merchandise_total_cents: int
    freight_cents: int | None
    final_payable_cents: int | None
    address_text: str
    notes: tuple[str, ...] = ()


def _optional_store(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError("店铺 ID 必须为正整数")
    return value


def utc_now() -> datetime:
    return datetime.now(UTC)
