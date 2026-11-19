from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime
from threading import Lock
from typing import Callable

from ..domain.models import TaskExecutionItem
from ..domain.operations import TaskExecutionError
from .adapters import (
    DesktopCartAdapter,
    build_cart_adapter,
)
from .domain import (
    CartAdapterMode,
    CartItemRequest,
    CartResult,
    CartResultStatus,
    CartSpeedMode,
)
from .repository import DesktopCartRepository
from .rules import verified_cart_success


class DesktopCartExecutor:
    def __init__(
        self,
        repository: DesktopCartRepository,
        *,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.repository = repository
        self.sleeper = sleeper
        self._speed_lock = Lock()
        self._adaptive_intervals: dict[str, float] = {}
        self._adapters: dict[CartAdapterMode, DesktopCartAdapter] = {
            CartAdapterMode.MOCK: build_cart_adapter(
                CartAdapterMode.MOCK,
                repository,
            ),
            CartAdapterMode.OFFICIAL: build_cart_adapter(
                CartAdapterMode.OFFICIAL,
                repository,
            ),
        }

    def adapter_for(self, mode: CartAdapterMode | str) -> DesktopCartAdapter:
        normalized = CartAdapterMode(mode)
        adapter = self._adapters.get(normalized)
        if adapter is None:
            raise TaskExecutionError(
                "cart_adapter_unsupported",
                f"没有可用的购物车适配器：{normalized.value}",
            )
        return adapter

    def execute(self, item: TaskExecutionItem) -> dict[str, object]:
        if (
            item.subject_type != "product"
            or not item.payload.get("desktop_cart")
        ):
            raise TaskExecutionError(
                "unsupported_subject",
                "购物车执行器不支持当前任务类型",
            )
        started_at = datetime.now(UTC)
        account_id = str(item.payload.get("account_id") or "")
        batch_id = str(item.payload.get("batch_id") or "")
        if not account_id or not batch_id:
            raise TaskExecutionError(
                "cart_payload_invalid",
                "购物车任务缺少批次或执行账号",
            )
        account = self.repository.get_account(
            account_id,
            store_id=item.store_id,
            store_app_id=item.store_app_id,
        )
        if account is None:
            raise TaskExecutionError(
                "cart_account_missing",
                "购物车执行账号不存在",
            )
        request = CartItemRequest(
            product_id=item.product_id,
            sku_id=item.sku_id,
            title=str(item.payload.get("product_title") or item.product_id),
            specs=tuple(
                (str(key), str(value))
                for key, value in dict(
                    item.payload.get("specs") or {}
                ).items()
            ),
            unit_price_cents=int(item.payload.get("unit_price_cents") or 0),
            quantity=int(item.payload.get("quantity") or 1),
        )
        interval_ms = max(
            0,
            int(item.payload.get("interval_ms") or 0),
        )
        self._wait_for_interval(
            account_id=account_id,
            interval_ms=interval_ms,
            speed_mode=str(item.payload.get("speed_mode") or "conservative"),
        )
        adapter = self.adapter_for(account.adapter_mode)
        capability = adapter.capability()
        if not capability.can_add_to_cart or not capability.can_verify_cart:
            reason = capability.reason or "当前购物车适配器不可用"
            self._record_result(
                item=item,
                account_id=account_id,
                request=request,
                started_at=started_at,
                status=CartResultStatus.FAILED,
                verification_source=account.adapter_mode,
                failure_code=capability.state.value,
                failure_reason=reason,
            )
            raise TaskExecutionError(
                "official_cart_api_unsupported",
                reason,
            )
        operation_key = (
            f"{batch_id}:{account_id}:{item.item_id}"
        )
        try:
            request_id = adapter.add_to_cart(
                account,
                request,
                operation_key=operation_key,
            )
            self._record_speed_outcome(account_id, success=True)
        except NotImplementedError as exc:
            self._record_speed_outcome(account_id, success=False)
            self._record_result(
                item=item,
                account_id=account_id,
                request=request,
                started_at=started_at,
                status=CartResultStatus.FAILED,
                verification_source=account.adapter_mode,
                failure_code="official_cart_api_unsupported",
                failure_reason=str(exc),
            )
            raise TaskExecutionError(
                "official_cart_api_unsupported",
                str(exc),
            ) from exc
        except Exception as exc:
            self._record_speed_outcome(account_id, success=False)
            self._record_result(
                item=item,
                account_id=account_id,
                request=request,
                started_at=started_at,
                status=CartResultStatus.UNKNOWN,
                verification_source=account.adapter_mode,
                failure_code="cart_write_unknown",
                failure_reason=f"加购请求结果未知：{exc}",
            )
            raise TaskExecutionError(
                "cart_write_unknown",
                f"加购请求结果未知，必须先核验：{exc}",
                unknown=True,
            ) from exc
        try:
            verification = adapter.verify_cart(
                account,
                request,
                request_id=request_id,
            )
        except Exception as exc:
            self._record_speed_outcome(account_id, success=False)
            self._record_result(
                item=item,
                account_id=account_id,
                request=request,
                started_at=started_at,
                status=CartResultStatus.UNKNOWN,
                verification_source=account.adapter_mode,
                failure_code="cart_verification_failed",
                failure_reason=f"购物车回查失败：{exc}",
                request_id=request_id,
            )
            raise TaskExecutionError(
                "cart_verification_failed",
                f"加购后购物车回查失败，结果未知：{exc}",
                unknown=True,
            ) from exc
        verified = bool(
            verification.verified
            and verified_cart_success(
                verification.snapshot,
                request,
                account_id=account_id,
                store_app_id=item.store_app_id,
            )
        )
        result = self._record_result(
            item=item,
            account_id=account_id,
            request=request,
            started_at=started_at,
            status=(
                CartResultStatus.SUCCEEDED
                if verified
                else CartResultStatus.UNKNOWN
            ),
            verification_source=verification.snapshot.source,
            failure_code="" if verified else "cart_state_mismatch",
            failure_reason="" if verified else (
                verification.reason
                or "购物车服务端状态与商品、SKU、数量或价格不一致"
            ),
            request_id=request_id,
            verification_token=verification.snapshot.server_state_token,
        )
        self.repository.record_verification(
            verification_id=uuid.uuid4().hex,
            result_id=result.result_id,
            snapshot=verification.snapshot,
            verified=verified,
            reason=verification.reason,
        )
        if not verified:
            self._record_speed_outcome(account_id, success=False)
            raise TaskExecutionError(
                "cart_state_mismatch",
                "购物车服务端状态未证明加购成功",
                unknown=True,
            )
        return {
            "verified": True,
            "request_id": request_id,
            "product_id": request.product_id,
            "sku_id": request.sku_id,
            "quantity": request.quantity,
            "source": verification.snapshot.source.value,
            "server_state_token": verification.snapshot.server_state_token,
        }

    def _wait_for_interval(
        self,
        *,
        account_id: str,
        interval_ms: int,
        speed_mode: str,
    ) -> None:
        mode = CartSpeedMode(speed_mode)
        if mode is CartSpeedMode.ADAPTIVE:
            with self._speed_lock:
                delay_ms = self._adaptive_intervals.setdefault(
                    account_id,
                    float(max(250, interval_ms)),
                )
        else:
            delay_ms = float(interval_ms)
        if delay_ms > 0:
            self.sleeper(delay_ms / 1000)

    def _record_speed_outcome(
        self,
        account_id: str,
        *,
        success: bool,
    ) -> None:
        with self._speed_lock:
            current = self._adaptive_intervals.get(account_id)
            if current is None:
                return
            if success:
                self._adaptive_intervals[account_id] = max(
                    250.0,
                    current * 0.9,
                )
            else:
                self._adaptive_intervals[account_id] = min(
                    30_000.0,
                    current * 1.5,
                )

    def verify(self, item: TaskExecutionItem) -> dict[str, object]:
        account_id = str(item.payload.get("account_id") or "")
        account = self.repository.get_account(
            account_id,
            store_id=item.store_id,
            store_app_id=item.store_app_id,
        )
        if account is None:
            raise TaskExecutionError(
                "cart_account_missing",
                "购物车执行账号不存在",
            )
        request = CartItemRequest(
            product_id=item.product_id,
            sku_id=item.sku_id,
            title=str(item.payload.get("product_title") or item.product_id),
            specs=tuple(
                (str(key), str(value))
                for key, value in dict(
                    item.payload.get("specs") or {}
                ).items()
            ),
            unit_price_cents=int(item.payload.get("unit_price_cents") or 0),
            quantity=int(item.payload.get("quantity") or 1),
        )
        adapter = self.adapter_for(account.adapter_mode)
        verification = adapter.verify_cart(
            account,
            request,
            request_id="manual-verify",
        )
        verified = bool(
            verification.verified
            and verified_cart_success(
                verification.snapshot,
                request,
                account_id=account.account_id,
                store_app_id=item.store_app_id,
            )
        )
        if not verified:
            raise TaskExecutionError(
                "cart_state_mismatch",
                "远端购物车状态仍未证明加购成功",
                unknown=True,
            )
        return {
            "verified": True,
            "server_state_token": verification.snapshot.server_state_token,
        }

    def _record_result(
        self,
        *,
        item: TaskExecutionItem,
        account_id: str,
        request: CartItemRequest,
        started_at: datetime,
        status: CartResultStatus,
        verification_source: CartAdapterMode,
        failure_code: str,
        failure_reason: str,
        request_id: str = "",
        verification_token: str = "",
    ) -> CartResult:
        store_name = str(item.payload.get("store_name") or "")
        result = CartResult(
            result_id=uuid.uuid4().hex,
            batch_id=str(item.payload["batch_id"]),
            account_id=account_id,
            task_id=item.task_id,
            item_id=item.item_id,
            store_id=item.store_id,
            store_app_id=item.store_app_id,
            store_name=store_name or item.store_app_id,
            product_id=request.product_id,
            product_title=request.title,
            sku_id=request.sku_id,
            specs=request.specs,
            unit_price_cents=request.unit_price_cents,
            quantity=request.quantity,
            batch_no=int(item.payload.get("batch_no") or 1),
            status=status,
            started_at=started_at,
            finished_at=datetime.now(UTC),
            failure_code=failure_code,
            failure_reason=failure_reason,
            request_id=request_id,
            verification_source=verification_source,
            verification_token=verification_token,
        )
        self.repository.record_result(result)
        return result
