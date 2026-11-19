from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

from ..domain.rules import (
    ensure_utc,
    sanitize_audit_detail,
    validate_money_cents,
)


class CartAdapterMode(StrEnum):
    MOCK = "mock"
    OFFICIAL = "official"
    MANUAL = "manual"
    UNKNOWN = "unknown"


class CartAccountStatus(StrEnum):
    PENDING_AUTHORIZATION = "pending_authorization"
    CONNECTED = "connected"
    EXPIRED = "expired"
    DISCONNECTED = "disconnected"
    MOCK_READY = "mock_ready"


class CartBatchStatus(StrEnum):
    DRAFT = "draft"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    PARTIALLY_FAILED = "partially_failed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


class CartAssignmentStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


class CartResultStatus(StrEnum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"
    UNKNOWN = "unknown"


class CartSkuMode(StrEnum):
    FIRST_AVAILABLE = "first_available"
    LOWEST_PRICE = "lowest_price"
    SPECIFIED = "specified"


class OutOfStockPolicy(StrEnum):
    SKIP = "skip"
    FAIL = "fail"


class CartSpeedMode(StrEnum):
    CONSERVATIVE = "conservative"
    ADAPTIVE = "adaptive"
    FAST = "fast"
    CUSTOM = "custom"


class PendingOrderStatus(StrEnum):
    DRAFT = "draft"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    CANCELLED = "cancelled"
    VERIFIED = "verified"
    OFFICIAL_CREATE_UNSUPPORTED = "official_create_unsupported"


class CartCapabilityState(StrEnum):
    AVAILABLE = "available"
    MOCK_ONLY = "mock_only"
    OFFICIAL_UNSUPPORTED = "official_unsupported"
    AUTHORIZATION_REQUIRED = "authorization_required"


@dataclass(frozen=True, slots=True)
class CartAdapterCapability:
    mode: CartAdapterMode
    state: CartCapabilityState
    can_add_to_cart: bool
    can_verify_cart: bool
    can_create_order: bool
    reason: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "mode", CartAdapterMode(self.mode))
        object.__setattr__(self, "state", CartCapabilityState(self.state))
        object.__setattr__(self, "can_add_to_cart", bool(self.can_add_to_cart))
        object.__setattr__(self, "can_verify_cart", bool(self.can_verify_cart))
        object.__setattr__(self, "can_create_order", bool(self.can_create_order))
        object.__setattr__(self, "reason", str(self.reason or "")[:1000])


@dataclass(frozen=True, slots=True)
class CartAccount:
    account_id: str
    store_id: int | None
    store_app_id: str
    display_name: str
    adapter_mode: CartAdapterMode
    status: CartAccountStatus
    authorization_ref: str
    created_at: datetime
    updated_at: datetime
    last_connected_at: datetime | None = None
    last_checked_at: datetime | None = None
    last_error: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "account_id",
            _required("账号 ID", self.account_id),
        )
        object.__setattr__(self, "store_id", _optional_store(self.store_id))
        object.__setattr__(
            self,
            "store_app_id",
            _required("店铺 AppID", self.store_app_id),
        )
        object.__setattr__(
            self,
            "display_name",
            _required("账号显示名", self.display_name),
        )
        object.__setattr__(
            self,
            "adapter_mode",
            CartAdapterMode(self.adapter_mode),
        )
        object.__setattr__(self, "status", CartAccountStatus(self.status))
        object.__setattr__(
            self,
            "authorization_ref",
            str(self.authorization_ref or "")[:300],
        )
        created = ensure_utc(self.created_at, field="账号创建时间")
        updated = ensure_utc(self.updated_at, field="账号更新时间")
        if updated < created:
            raise ValueError("账号更新时间不能早于创建时间")
        object.__setattr__(self, "created_at", created)
        object.__setattr__(self, "updated_at", updated)
        object.__setattr__(
            self,
            "last_connected_at",
            _optional_time(self.last_connected_at, "账号连接时间"),
        )
        object.__setattr__(
            self,
            "last_checked_at",
            _optional_time(self.last_checked_at, "账号检查时间"),
        )
        object.__setattr__(self, "last_error", str(self.last_error or "")[:1000])


@dataclass(frozen=True, slots=True)
class CartSpeedProfile:
    mode: CartSpeedMode
    label: str
    interval_ms: int
    concurrency: int
    cooldown_ms: int
    backoff_base_ms: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "mode", CartSpeedMode(self.mode))
        label = str(self.label or "").strip()
        if not label:
            raise ValueError("速度名称不能为空")
        object.__setattr__(self, "label", label)
        for name, value, minimum, maximum in (
            ("操作间隔", self.interval_ms, 250, 600_000),
            ("并发数", self.concurrency, 1, 2),
            ("限流冷却", self.cooldown_ms, 0, 3_600_000),
            ("退避基数", self.backoff_base_ms, 100, 3_600_000),
        ):
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{name}必须是整数")
            if not minimum <= value <= maximum:
                raise ValueError(f"{name}必须在 {minimum} 到 {maximum} 之间")

    def delay_for_attempt(self, attempt_no: int) -> float:
        exponent = max(0, int(attempt_no) - 1)
        return min(60.0, self.backoff_base_ms * (2**exponent) / 1000)


CONSERVATIVE_PROFILE = CartSpeedProfile(
    CartSpeedMode.CONSERVATIVE,
    "保守",
    2500,
    1,
    30_000,
    2000,
)
ADAPTIVE_PROFILE = CartSpeedProfile(
    CartSpeedMode.ADAPTIVE,
    "自适应",
    1200,
    1,
    15_000,
    1500,
)
FAST_PROFILE = CartSpeedProfile(
    CartSpeedMode.FAST,
    "较快",
    600,
    2,
    5000,
    1000,
)


def speed_profile_for(
    mode: CartSpeedMode | str,
    *,
    interval_ms: int | None = None,
    concurrency: int | None = None,
) -> CartSpeedProfile:
    normalized = CartSpeedMode(mode)
    if normalized is CartSpeedMode.CONSERVATIVE:
        return CONSERVATIVE_PROFILE
    if normalized is CartSpeedMode.ADAPTIVE:
        return ADAPTIVE_PROFILE
    if normalized is CartSpeedMode.FAST:
        return FAST_PROFILE
    return CartSpeedProfile(
        CartSpeedMode.CUSTOM,
        "固定配置",
        interval_ms if interval_ms is not None else 1200,
        concurrency if concurrency is not None else 1,
        15_000,
        1500,
    )


@dataclass(frozen=True, slots=True)
class CartBatch:
    batch_id: str
    store_id: int | None
    store_app_id: str
    pool_id: str
    name: str
    idempotency_key: str
    status: CartBatchStatus
    total_products: int
    products_per_batch: int
    quantity_per_product: int
    sku_mode: CartSkuMode
    specified_sku_id: str
    out_of_stock_policy: OutOfStockPolicy
    speed_mode: CartSpeedMode
    interval_ms: int
    concurrency: int
    adapter_mode: CartAdapterMode
    created_by: str
    created_at: datetime
    updated_at: datetime
    confirmed_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    last_error: str = ""
    summary: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("batch_id", "store_app_id", "pool_id", "name", "idempotency_key"):
            object.__setattr__(self, name, _required(name, getattr(self, name)))
        object.__setattr__(self, "store_id", _optional_store(self.store_id))
        object.__setattr__(self, "status", CartBatchStatus(self.status))
        for name, value, maximum in (
            ("商品总数", self.total_products, 100_000),
            ("每批数量", self.products_per_batch, 10_000),
            ("每商品数量", self.quantity_per_product, 9999),
            ("操作间隔", self.interval_ms, 600_000),
            ("并发数", self.concurrency, 2),
        ):
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{name}必须是整数")
            if value <= 0 or value > maximum:
                raise ValueError(f"{name}必须在 1 到 {maximum} 之间")
        object.__setattr__(self, "sku_mode", CartSkuMode(self.sku_mode))
        object.__setattr__(
            self,
            "out_of_stock_policy",
            OutOfStockPolicy(self.out_of_stock_policy),
        )
        object.__setattr__(self, "speed_mode", CartSpeedMode(self.speed_mode))
        object.__setattr__(self, "adapter_mode", CartAdapterMode(self.adapter_mode))
        object.__setattr__(self, "specified_sku_id", str(self.specified_sku_id or ""))
        object.__setattr__(self, "created_by", str(self.created_by or "")[:200])
        created = ensure_utc(self.created_at, field="批次创建时间")
        updated = ensure_utc(self.updated_at, field="批次更新时间")
        if updated < created:
            raise ValueError("批次更新时间不能早于创建时间")
        object.__setattr__(self, "created_at", created)
        object.__setattr__(self, "updated_at", updated)
        for name in ("confirmed_at", "started_at", "finished_at"):
            object.__setattr__(
                self,
                name,
                _optional_time(getattr(self, name), f"批次{name}"),
            )
        object.__setattr__(self, "last_error", str(self.last_error or "")[:1000])
        object.__setattr__(self, "summary", sanitize_audit_detail(dict(self.summary)))


@dataclass(frozen=True, slots=True)
class CartItemRequest:
    product_id: str
    sku_id: str
    title: str
    specs: tuple[tuple[str, str], ...]
    unit_price_cents: int
    quantity: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "product_id", _required("商品 ID", self.product_id))
        object.__setattr__(self, "sku_id", _required("SKU ID", self.sku_id))
        object.__setattr__(self, "title", _required("商品标题", self.title))
        object.__setattr__(
            self,
            "specs",
            tuple(sorted((str(key), str(value)) for key, value in self.specs)),
        )
        object.__setattr__(
            self,
            "unit_price_cents",
            validate_money_cents(
                self.unit_price_cents,
                field="购物车商品单价",
                allow_zero=False,
            ),
        )
        if (
            isinstance(self.quantity, bool)
            or not isinstance(self.quantity, int)
            or self.quantity <= 0
        ):
            raise ValueError("购物车商品数量必须为正整数")


@dataclass(frozen=True, slots=True)
class CartServerSnapshot:
    store_app_id: str
    account_id: str
    product_id: str
    sku_id: str
    quantity: int
    unit_price_cents: int
    observed_at: datetime
    source: CartAdapterMode
    server_state_token: str
    raw_fingerprint: str = ""

    def __post_init__(self) -> None:
        for name in (
            "store_app_id",
            "account_id",
            "product_id",
            "sku_id",
            "server_state_token",
        ):
            object.__setattr__(self, name, _required(name, getattr(self, name)))
        if self.quantity < 0:
            raise ValueError("购物车快照数量不能为负数")
        object.__setattr__(
            self,
            "unit_price_cents",
            validate_money_cents(self.unit_price_cents, field="购物车快照单价"),
        )
        object.__setattr__(
            self,
            "observed_at",
            ensure_utc(self.observed_at, field="购物车快照时间"),
        )
        object.__setattr__(self, "source", CartAdapterMode(self.source))

    @property
    def has_server_proof(self) -> bool:
        return bool(self.server_state_token.strip())

    def matches(self, request: CartItemRequest) -> bool:
        return (
            self.product_id == request.product_id
            and self.sku_id == request.sku_id
            and self.quantity >= request.quantity
            and self.unit_price_cents == request.unit_price_cents
            and self.has_server_proof
        )


@dataclass(frozen=True, slots=True)
class CartVerification:
    snapshot: CartServerSnapshot
    verified: bool
    reason: str = ""


@dataclass(frozen=True, slots=True)
class CartResult:
    result_id: str
    batch_id: str
    account_id: str
    task_id: str
    item_id: str
    store_id: int | None
    store_app_id: str
    store_name: str
    product_id: str
    product_title: str
    sku_id: str
    specs: tuple[tuple[str, str], ...]
    unit_price_cents: int
    quantity: int
    batch_no: int
    status: CartResultStatus
    started_at: datetime
    finished_at: datetime | None
    failure_code: str = ""
    failure_reason: str = ""
    request_id: str = ""
    verification_source: CartAdapterMode = CartAdapterMode.UNKNOWN
    verification_token: str = ""

    def __post_init__(self) -> None:
        for name in (
            "result_id",
            "batch_id",
            "account_id",
            "task_id",
            "item_id",
            "store_app_id",
            "store_name",
            "product_id",
            "product_title",
            "sku_id",
        ):
            object.__setattr__(self, name, _required(name, getattr(self, name)))
        object.__setattr__(self, "store_id", _optional_store(self.store_id))
        object.__setattr__(
            self,
            "specs",
            tuple(sorted((str(key), str(value)) for key, value in self.specs)),
        )
        object.__setattr__(
            self,
            "unit_price_cents",
            validate_money_cents(
                self.unit_price_cents,
                field="购物车结果单价",
                allow_zero=False,
            ),
        )
        if self.quantity <= 0:
            raise ValueError("购物车结果数量必须为正整数")
        if self.batch_no <= 0:
            raise ValueError("购物车结果批次号必须为正整数")
        object.__setattr__(self, "status", CartResultStatus(self.status))
        object.__setattr__(
            self,
            "started_at",
            ensure_utc(self.started_at, field="购物车执行开始时间"),
        )
        object.__setattr__(
            self,
            "finished_at",
            _optional_time(self.finished_at, "购物车执行完成时间"),
        )
        object.__setattr__(
            self,
            "verification_source",
            CartAdapterMode(self.verification_source),
        )
        for name in ("failure_code", "failure_reason", "request_id", "verification_token"):
            object.__setattr__(self, name, str(getattr(self, name) or ""))


@dataclass(frozen=True, slots=True)
class PendingOrderDraft:
    draft_id: str
    batch_id: str
    store_id: int | None
    store_app_id: str
    store_name: str
    account_id: str
    status: PendingOrderStatus
    item_count: int
    total_amount_cents: int
    created_at: datetime
    updated_at: datetime
    approval_id: str = ""
    decided_by: str = ""
    decided_at: datetime | None = None
    official_order_id: str = ""
    reason: str = ""

    def __post_init__(self) -> None:
        for name in (
            "draft_id",
            "batch_id",
            "store_app_id",
            "store_name",
            "account_id",
        ):
            object.__setattr__(self, name, _required(name, getattr(self, name)))
        object.__setattr__(self, "store_id", _optional_store(self.store_id))
        object.__setattr__(self, "status", PendingOrderStatus(self.status))
        if self.item_count < 0:
            raise ValueError("待付款草案商品数不能为负数")
        object.__setattr__(
            self,
            "total_amount_cents",
            validate_money_cents(self.total_amount_cents, field="待付款草案金额"),
        )
        object.__setattr__(
            self,
            "created_at",
            ensure_utc(self.created_at, field="待付款草案创建时间"),
        )
        object.__setattr__(
            self,
            "updated_at",
            ensure_utc(self.updated_at, field="待付款草案更新时间"),
        )
        object.__setattr__(
            self,
            "decided_at",
            _optional_time(self.decided_at, "待付款草案审批时间"),
        )


def _required(label: str, value: object) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{label}不能为空")
    return text


def _optional_store(value: int | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError("店铺 ID 必须为正整数")
    return value


def _optional_time(value: datetime | None, label: str) -> datetime | None:
    return ensure_utc(value, field=label) if value is not None else None


def utc_now() -> datetime:
    return datetime.now(UTC)
