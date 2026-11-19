from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol

from .domain import (
    CartAccount,
    CartAccountStatus,
    CartAdapterCapability,
    CartAdapterMode,
    CartCapabilityState,
    CartItemRequest,
    CartServerSnapshot,
    CartVerification,
)
from .repository import DesktopCartRepository


class DesktopCartAdapter(Protocol):
    mode: CartAdapterMode

    def capability(self) -> CartAdapterCapability: ...

    def check_account(self, account: CartAccount) -> CartAccountStatus: ...

    def add_to_cart(
        self,
        account: CartAccount,
        request: CartItemRequest,
        *,
        operation_key: str,
    ) -> str: ...

    def verify_cart(
        self,
        account: CartAccount,
        request: CartItemRequest,
        *,
        request_id: str,
    ) -> CartVerification: ...


class MockCartAdapter:
    """Isolated adapter used only for workflow validation and demonstrations."""

    mode = CartAdapterMode.MOCK

    def __init__(self, repository: DesktopCartRepository) -> None:
        self.repository = repository

    def capability(self) -> CartAdapterCapability:
        return CartAdapterCapability(
            mode=self.mode,
            state=CartCapabilityState.MOCK_ONLY,
            can_add_to_cart=True,
            can_verify_cart=True,
            can_create_order=False,
            reason=(
                "Mock 适配器只在隔离数据库内维护模拟购物车状态，"
                "不代表微信小店真实购物车。"
            ),
        )

    def check_account(self, account: CartAccount) -> CartAccountStatus:
        if account.adapter_mode is not CartAdapterMode.MOCK:
            return CartAccountStatus.DISCONNECTED
        return CartAccountStatus.MOCK_READY

    def add_to_cart(
        self,
        account: CartAccount,
        request: CartItemRequest,
        *,
        operation_key: str,
    ) -> str:
        if account.status is not CartAccountStatus.MOCK_READY:
            raise PermissionError("模拟执行账号尚未连接")
        _snapshot, request_id, _duplicate = (
            self.repository.apply_mock_cart_operation(
                operation_key=str(operation_key),
                store_app_id=account.store_app_id,
                account_id=account.account_id,
                product_id=request.product_id,
                sku_id=request.sku_id,
                quantity=request.quantity,
                unit_price_cents=request.unit_price_cents,
                now=datetime.now(UTC),
            )
        )
        return request_id

    def verify_cart(
        self,
        account: CartAccount,
        request: CartItemRequest,
        *,
        request_id: str,
    ) -> CartVerification:
        del request_id
        snapshot = self.repository.get_mock_cart_state(
            store_app_id=account.store_app_id,
            account_id=account.account_id,
            product_id=request.product_id,
            sku_id=request.sku_id,
        )
        if snapshot is None:
            return CartVerification(
                snapshot=CartServerSnapshot(
                    store_app_id=account.store_app_id,
                    account_id=account.account_id,
                    product_id=request.product_id,
                    sku_id=request.sku_id,
                    quantity=0,
                    unit_price_cents=request.unit_price_cents,
                    observed_at=datetime.now(UTC),
                    source=CartAdapterMode.MOCK,
                    server_state_token="",
                ),
                verified=False,
                reason="模拟购物车中没有目标 SKU",
            )
        verified = snapshot.matches(request)
        return CartVerification(
            snapshot=snapshot,
            verified=verified,
            reason="" if verified else "模拟购物车状态与请求不一致",
        )


class UnsupportedOfficialCartAdapter:
    """Fail-closed placeholder for missing official buyer-cart capability."""

    mode = CartAdapterMode.OFFICIAL

    def capability(self) -> CartAdapterCapability:
        return CartAdapterCapability(
            mode=self.mode,
            state=CartCapabilityState.OFFICIAL_UNSUPPORTED,
            can_add_to_cart=False,
            can_verify_cart=False,
            can_create_order=False,
            reason=(
                "当前项目可确认的微信小店商家开放接口不提供买家加购、"
                "买家购物车读取或买家待付款订单创建能力。"
                "未接入未经确认的网页私有接口。"
            ),
        )

    def check_account(self, account: CartAccount) -> CartAccountStatus:
        del account
        return CartAccountStatus.PENDING_AUTHORIZATION

    def add_to_cart(
        self,
        account: CartAccount,
        request: CartItemRequest,
        *,
        operation_key: str,
    ) -> str:
        del account, request, operation_key
        raise NotImplementedError(
            "微信小店官方接口未提供买家加购能力"
        )

    def verify_cart(
        self,
        account: CartAccount,
        request: CartItemRequest,
        *,
        request_id: str,
    ) -> CartVerification:
        del account, request, request_id
        raise NotImplementedError(
            "微信小店官方接口未提供买家购物车读取能力"
        )


def build_cart_adapter(
    mode: CartAdapterMode | str,
    repository: DesktopCartRepository,
) -> DesktopCartAdapter:
    normalized = CartAdapterMode(mode)
    if normalized is CartAdapterMode.MOCK:
        return MockCartAdapter(repository)
    if normalized is CartAdapterMode.OFFICIAL:
        return UnsupportedOfficialCartAdapter()
    raise ValueError(
        "当前没有可确认授权的桌面购物车适配器："
        f"{normalized.value}"
    )
