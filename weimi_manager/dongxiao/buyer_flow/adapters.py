from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from .domain import (
    BuyerAdapterCapability,
    BuyerCapabilityState,
    BuyerIdentityStatus,
)


@dataclass(frozen=True, slots=True)
class BuyerIdentityProof:
    provider: str
    official_account_id: str
    display_name: str
    verified_at: datetime
    proof_token: str

    def __post_init__(self) -> None:
        if not self.official_account_id.strip():
            raise ValueError("官方买家身份标识不能为空")
        if not self.display_name.strip():
            raise ValueError("官方买家昵称不能为空")
        if not self.proof_token.strip():
            raise ValueError("官方买家身份证明不能为空")


@dataclass(frozen=True, slots=True)
class BuyerAuthorizationSession:
    authorization_id: str
    official_url: str
    expires_at: datetime
    instructions: str


@dataclass(frozen=True, slots=True)
class BuyerCartProof:
    product_id: str
    sku_id: str
    quantity: int
    unit_price_cents: int
    server_state_token: str


@dataclass(frozen=True, slots=True)
class BuyerOrderProof:
    order_id: str
    pending_payment: bool
    total_amount_cents: int
    item_count: int
    status_token: str


class BuyerFlowAdapter(Protocol):
    def capability(self) -> BuyerAdapterCapability: ...

    def begin_authorization(self) -> BuyerAuthorizationSession | None: ...

    def verify_identity(
        self,
        *,
        authorization_id: str,
        user_confirmed_official_client: bool,
    ) -> BuyerIdentityProof: ...

    def add_to_cart(
        self,
        *,
        buyer_account_id: str,
        product_id: str,
        sku_id: str,
        quantity: int,
        unit_price_cents: int,
        idempotency_key: str,
    ) -> BuyerCartProof: ...

    def read_cart(
        self,
        *,
        buyer_account_id: str,
        product_id: str,
        sku_id: str,
    ) -> BuyerCartProof | None: ...

    def create_order(
        self,
        *,
        buyer_account_id: str,
        idempotency_key: str,
    ) -> BuyerOrderProof: ...

    def read_order(
        self,
        *,
        buyer_account_id: str,
        order_id: str,
    ) -> BuyerOrderProof | None: ...


class OfficialBuyerFlowAdapter:
    """Expose only capabilities that can be proven from official interfaces."""

    def capability(self) -> BuyerAdapterCapability:
        return BuyerAdapterCapability(
            state=BuyerCapabilityState.OFFICIAL_UNSUPPORTED,
            official_login=False,
            official_profile=False,
            official_cart_write=False,
            official_cart_read=False,
            official_order_create=False,
            official_order_read=False,
            manual_handoff=True,
            reason=(
                "当前可确认的微信小店官方开放接口不提供买家桌面登录、"
                "购物车读写或买家待付款订单创建。未接入任何私有网页接口。"
            ),
        )

    def begin_authorization(self) -> BuyerAuthorizationSession | None:
        return None

    def verify_identity(
        self,
        *,
        authorization_id: str,
        user_confirmed_official_client: bool,
    ) -> BuyerIdentityProof:
        del authorization_id, user_confirmed_official_client
        raise NotImplementedError(
            "没有可核验的官方买家授权会话，不能生成身份证明"
        )

    def add_to_cart(
        self,
        *,
        buyer_account_id: str,
        product_id: str,
        sku_id: str,
        quantity: int,
        unit_price_cents: int,
        idempotency_key: str,
    ) -> BuyerCartProof:
        del (
            buyer_account_id,
            product_id,
            sku_id,
            quantity,
            unit_price_cents,
            idempotency_key,
        )
        raise NotImplementedError("官方接口未提供买家加购能力")

    def read_cart(
        self,
        *,
        buyer_account_id: str,
        product_id: str,
        sku_id: str,
    ) -> BuyerCartProof | None:
        del buyer_account_id, product_id, sku_id
        raise NotImplementedError("官方接口未提供买家购物车读取能力")

    def create_order(
        self,
        *,
        buyer_account_id: str,
        idempotency_key: str,
    ) -> BuyerOrderProof:
        del buyer_account_id, idempotency_key
        raise NotImplementedError("官方接口未提供买家订单创建能力")

    def read_order(
        self,
        *,
        buyer_account_id: str,
        order_id: str,
    ) -> BuyerOrderProof | None:
        del buyer_account_id, order_id
        raise NotImplementedError("官方接口未提供买家订单读取能力")


class MockBuyerFlowAdapter:
    """Deterministic local-only adapter for regression tests and UI drills."""

    def __init__(self) -> None:
        self._cart: dict[tuple[str, str, str], BuyerCartProof] = {}
        self._orders: dict[tuple[str, str], BuyerOrderProof] = {}

    def capability(self) -> BuyerAdapterCapability:
        return BuyerAdapterCapability(
            state=BuyerCapabilityState.MOCK_ONLY,
            official_login=False,
            official_profile=False,
            official_cart_write=False,
            official_cart_read=False,
            official_order_create=False,
            official_order_read=False,
            manual_handoff=False,
            reason="Mock 仅用于本地流程测试，不代表任何真实微信状态。",
        )

    def begin_authorization(self) -> BuyerAuthorizationSession | None:
        return None

    def verify_identity(
        self,
        *,
        authorization_id: str,
        user_confirmed_official_client: bool,
    ) -> BuyerIdentityProof:
        del authorization_id, user_confirmed_official_client
        now = datetime.now(UTC)
        return BuyerIdentityProof(
            provider="mock",
            official_account_id="mock-buyer",
            display_name="Mock 买家",
            verified_at=now,
            proof_token=f"mock-identity:{now.isoformat()}",
        )

    def add_to_cart(
        self,
        *,
        buyer_account_id: str,
        product_id: str,
        sku_id: str,
        quantity: int,
        unit_price_cents: int,
        idempotency_key: str,
    ) -> BuyerCartProof:
        key = (buyer_account_id, product_id, sku_id)
        existing = self._cart.get(key)
        token = hashlib.sha256(
            f"{buyer_account_id}\0{product_id}\0{sku_id}\0"
            f"{quantity}\0{unit_price_cents}\0{idempotency_key}".encode("utf-8")
        ).hexdigest()
        proof = BuyerCartProof(
            product_id=product_id,
            sku_id=sku_id,
            quantity=max(quantity, existing.quantity if existing else 0),
            unit_price_cents=unit_price_cents,
            server_state_token=token,
        )
        self._cart[key] = proof
        return proof

    def read_cart(
        self,
        *,
        buyer_account_id: str,
        product_id: str,
        sku_id: str,
    ) -> BuyerCartProof | None:
        return self._cart.get((buyer_account_id, product_id, sku_id))

    def create_order(
        self,
        *,
        buyer_account_id: str,
        idempotency_key: str,
    ) -> BuyerOrderProof:
        order_id = "MOCK-" + hashlib.sha256(
            f"{buyer_account_id}\0{idempotency_key}".encode("utf-8")
        ).hexdigest()[:16].upper()
        proof = BuyerOrderProof(
            order_id=order_id,
            pending_payment=True,
            total_amount_cents=sum(
                item.unit_price_cents * item.quantity
                for (account_id, _product_id, _sku_id), item in self._cart.items()
                if account_id == buyer_account_id
            ),
            item_count=sum(
                1
                for account_id, _product_id, _sku_id in self._cart
                if account_id == buyer_account_id
            ),
            status_token=f"mock-order:{order_id}",
        )
        self._orders[(buyer_account_id, order_id)] = proof
        return proof

    def read_order(
        self,
        *,
        buyer_account_id: str,
        order_id: str,
    ) -> BuyerOrderProof | None:
        return self._orders.get((buyer_account_id, order_id))


def identity_status_for_provider(provider: str) -> BuyerIdentityStatus:
    return (
        BuyerIdentityStatus.VERIFIED_MOCK
        if provider == "mock"
        else BuyerIdentityStatus.VERIFIED_OFFICIAL
    )
