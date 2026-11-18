from __future__ import annotations

from typing import Any, Protocol

from ...models import Order


class DongxiaoPlatformAdapter(Protocol):
    def list_order_ids(
        self,
        store,
        start_time: int,
        end_time: int,
        status: int | None = None,
    ) -> list[str]: ...

    def get_order(self, store, order_id: str) -> Order: ...

    def list_aftersale_ids(
        self,
        store,
        start_time: int,
        end_time: int,
        time_field: str = "update",
    ) -> list[str]: ...

    def get_aftersale(self, store, case_id: str) -> dict[str, Any]: ...

    def accept_aftersale(
        self,
        store,
        case_id: str,
        *,
        address_id: str = "",
        accept_type: int | None = None,
    ) -> dict[str, Any]: ...

    def reject_aftersale(
        self,
        store,
        case_id: str,
        *,
        reject_reason_type: int,
        reject_reason: str = "",
    ) -> dict[str, Any]: ...


class OfficialDongxiaoPlatformAdapter:
    def __init__(self, sync_service) -> None:
        self.sync_service = sync_service

    def list_order_ids(
        self,
        store,
        start_time: int,
        end_time: int,
        status: int | None = None,
    ) -> list[str]:
        return self.sync_service.client_for(store).fetch_order_ids(
            int(start_time),
            int(end_time),
            status,
        )

    def get_order(self, store, order_id: str) -> Order:
        return self.sync_service.client_for(store).fetch_order(
            store,
            str(order_id),
        )

    def list_aftersale_ids(
        self,
        store,
        start_time: int,
        end_time: int,
        time_field: str = "update",
    ) -> list[str]:
        return self.sync_service.client_for(store).fetch_after_sale_ids(
            int(start_time),
            int(end_time),
            time_field=time_field,
        )

    def get_aftersale(self, store, case_id: str) -> dict[str, Any]:
        return self.sync_service.client_for(store).fetch_after_sale(
            str(case_id)
        )

    def accept_aftersale(
        self,
        store,
        case_id: str,
        *,
        address_id: str = "",
        accept_type: int | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "after_sale_order_id": str(case_id),
        }
        if address_id:
            payload["address_id"] = str(address_id)
        if accept_type is not None:
            payload["accept_type"] = int(accept_type)
        return self.sync_service.client_for(store).accept_after_sale(payload)

    def reject_aftersale(
        self,
        store,
        case_id: str,
        *,
        reject_reason_type: int,
        reject_reason: str = "",
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "after_sale_order_id": str(case_id),
            "reject_reason_type": int(reject_reason_type),
        }
        if reject_reason:
            payload["reject_reason"] = str(reject_reason)
        return self.sync_service.client_for(store).reject_after_sale(payload)
