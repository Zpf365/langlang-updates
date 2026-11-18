from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Callable

from ..domain.enums import (
    OrderStatus,
    OrderVerificationSource,
)
from ..domain.models import OrderRecord
from ..domain.operations import OperationSyncResult
from ..infrastructure.platform import DongxiaoPlatformAdapter
from ..infrastructure.repository import DongxiaoRepository

ProgressCallback = Callable[[str, int, int], None]


class DongxiaoOrderService:
    def __init__(
        self,
        repository: DongxiaoRepository,
        adapter: DongxiaoPlatformAdapter,
    ) -> None:
        self.repository = repository
        self.adapter = adapter

    def sync_orders(
        self,
        store,
        *,
        start_time: int,
        end_time: int,
        status: int | None = None,
        progress: ProgressCallback | None = None,
    ) -> OperationSyncResult:
        ids = self.adapter.list_order_ids(
            store,
            int(start_time),
            int(end_time),
            status,
        )
        saved = 0
        errors: list[str] = []
        pending_records: list[OrderRecord] = []

        def flush() -> None:
            nonlocal saved
            if not pending_records:
                return
            try:
                saved += self.repository.upsert_order_records_batch(
                    pending_records
                )
            except Exception as exc:
                for record in pending_records:
                    try:
                        self.repository.upsert_order_record(
                            record,
                            request_id=f"order:{record.platform_order_id}",
                        )
                        saved += 1
                    except Exception as item_exc:
                        errors.append(
                            f"{record.platform_order_id}: "
                            f"批量失败后单项保存失败: {item_exc}"
                        )
                errors.append(f"订单批量保存失败，已回退逐项保存: {exc}")
            finally:
                pending_records.clear()

        for index, order_id in enumerate(ids, 1):
            if progress:
                progress(
                    f"正在同步真实订单 {index}/{len(ids)}",
                    index,
                    len(ids),
                )
            try:
                order = self.adapter.get_order(store, str(order_id))
                if str(order.order_id) != str(order_id):
                    raise ValueError("订单详情返回的订单号与请求不一致")
                pending_records.append(self._map_order(store, order))
                if len(pending_records) >= 100:
                    flush()
            except Exception as exc:
                errors.append(f"{order_id}: {exc}")
        flush()
        return OperationSyncResult(
            discovered=len(ids),
            saved=saved,
            failed=len(errors),
            errors=tuple(errors[:100]),
        )

    def sync_order(self, store, order_id: str) -> OrderRecord:
        order = self.adapter.get_order(store, str(order_id))
        if str(order.order_id) != str(order_id):
            raise ValueError("订单详情返回的订单号与请求不一致")
        record = self._map_order(store, order)
        return self.repository.upsert_order_record(
            record,
            request_id=f"order:{order_id}",
        )

    def list_orders(
        self,
        store,
        *,
        limit: int = 500,
    ) -> list[OrderRecord]:
        return self.repository.list_order_records(
            int(store.id),
            limit=limit,
        )

    def get_order(self, store, order_id: str) -> OrderRecord | None:
        return self.repository.get_order_record(
            int(store.id),
            str(order_id),
        )

    def list_history(self, store_app_id: str) -> list[OrderRecord]:
        return self.repository.list_order_records_by_store_app_id(
            str(store_app_id)
        )

    def _map_order(self, store, order) -> OrderRecord:
        reference_id = hashlib.sha256(
            f"{store.app_id}\0{order.order_id}".encode("utf-8")
        ).hexdigest()
        items = tuple(
            {
                "product_id": str(item.product_id),
                "sku_id": str(item.sku_id),
                "title": str(item.title),
                "quantity": int(item.sku_count),
                "sale_price_cents": int(item.sale_price),
                "sku_attributes": str(item.sku_attributes),
            }
            for item in order.items
        )
        mapped_status = _map_order_status(int(order.status))
        aftersale_detail = order.raw.get("aftersale_detail")
        if (
            isinstance(aftersale_detail, dict)
            and int(aftersale_detail.get("on_aftersale_order_cnt") or 0) > 0
            and mapped_status
            not in {
                OrderStatus.COMPLETED,
                OrderStatus.CANCELLED,
                OrderStatus.REFUNDED,
            }
        ):
            mapped_status = OrderStatus.REFUNDING
        return OrderRecord(
            order_ref_id=reference_id,
            store_id=int(store.id),
            store_app_id=str(store.app_id),
            platform_order_id=str(order.order_id),
            status=mapped_status,
            create_time=_timestamp(order.create_time),
            update_time=_timestamp(order.update_time),
            pay_time=_timestamp(order.pay_time),
            total_amount_cents=max(0, int(order.total_amount)),
            product_amount_cents=max(0, int(order.product_amount)),
            freight_amount_cents=max(0, int(order.freight_amount)),
            discount_amount_cents=max(0, int(order.discount_amount)),
            item_count=len(items),
            items=items,
            source=OrderVerificationSource.PLATFORM_API,
            last_synced_at=datetime.now(UTC),
        )


def _timestamp(value: int) -> datetime | None:
    seconds = int(value or 0)
    if seconds <= 0:
        return None
    return datetime.fromtimestamp(seconds, tz=UTC)


def _map_order_status(value: int) -> OrderStatus:
    if value in {10, 12, 13, 17}:
        return OrderStatus.PENDING_PAYMENT
    if value == 20:
        return OrderStatus.PAID
    if value in {21, 30}:
        return OrderStatus.SHIPPED
    if value == 100:
        return OrderStatus.COMPLETED
    if value == 200:
        return OrderStatus.REFUNDED
    if value == 250:
        return OrderStatus.CANCELLED
    return OrderStatus.UNKNOWN
