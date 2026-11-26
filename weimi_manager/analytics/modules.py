from __future__ import annotations

from typing import Any

from .adapter import (
    AnalyticsAdapter,
    EventDefinition,
    registry,
)


FEATURE_EVENT = EventDefinition(
    "feature.usage_daily",
    "heartbeat",
    priority=2,
    retention_days=7,
    allowed_fields=(
        "feature_key",
        "use_count",
        "task_count",
        "error_count",
        "business_date",
    ),
    dedupe_fields=("feature_key", "business_date"),
    aggregation="counter",
)
ERROR_EVENT = EventDefinition(
    "error.summary",
    "error-summary",
    priority=0,
    retention_days=30,
    allowed_fields=(
        "module_key",
        "error_category",
        "count",
        "last_seen_at",
        "business_date",
    ),
    dedupe_fields=(
        "module_key",
        "error_category",
        "business_date",
    ),
    aggregation="counter",
)
HEARTBEAT_EVENT = EventDefinition(
    "device.heartbeat",
    "heartbeat",
    priority=2,
    retention_days=30,
    allowed_fields=(
        "app_version",
        "platform",
        "os_family",
        "business_date",
    ),
    dedupe_fields=("business_date", "app_version"),
    aggregation="latest",
)
VERSION_EVENT = EventDefinition(
    "app.version_seen",
    "heartbeat",
    priority=2,
    retention_days=90,
    allowed_fields=(
        "app_version",
        "first_seen_at",
        "business_date",
    ),
    dedupe_fields=("app_version", "business_date"),
    aggregation="event",
)


class ModuleAnalyticsAdapter(AnalyticsAdapter):
    def __init__(
        self,
        module_key: str,
        definitions: tuple[EventDefinition, ...],
    ) -> None:
        self.module_key = module_key
        self.definitions = definitions + (
            FEATURE_EVENT,
            ERROR_EVENT,
            HEARTBEAT_EVENT,
            VERSION_EVENT,
        )


def _event(
    event_type: str,
    endpoint: str,
    *,
    priority: int = 1,
    retention_days: int = 30,
    fields: tuple[str, ...],
    dedupe: tuple[str, ...],
    aggregation: str = "event",
) -> EventDefinition:
    return EventDefinition(
        event_type,
        endpoint,
        priority=priority,
        retention_days=retention_days,
        allowed_fields=fields,
        dedupe_fields=dedupe,
        aggregation=aggregation,
    )


STORE_EVENTS = (
    _event(
        "store.daily_metrics",
        "store-daily",
        fields=(
            "store_name",
            "app_id_masked",
            "business_date",
            "gmv",
            "paid_orders",
            "refund_amount",
            "refund_orders",
            "online_products",
            "new_products_today",
            "exposure",
            "score",
            "last_sync_at",
            "data_fresh",
            "source",
        ),
        dedupe=("business_date",),
        aggregation="latest",
    ),
    _event(
        "store.health_changed",
        "store-daily",
        fields=(
            "status",
            "last_sync_at",
            "error_category",
        ),
        dedupe=("status", "last_sync_at"),
    ),
)
ORDER_EVENTS = (
    _event(
        "order.created",
        "order-events",
        fields=(
            "order_ref",
            "items",
            "status",
            "amount",
            "item_count",
            "created_at",
            "paid_at",
            "after_sale",
            "source_tab",
            "currency_code",
        ),
        dedupe=("order_ref",),
    ),
    _event(
        "order.status_changed",
        "order-events",
        fields=(
            "order_ref",
            "items",
            "from_status",
            "to_status",
            "amount",
            "after_sale",
            "updated_at",
        ),
        dedupe=("order_ref", "to_status"),
    ),
    _event(
        "order.after_sale_opened",
        "order-events",
        fields=(
            "order_ref",
            "items",
            "after_sale_ref",
            "after_sale_type",
            "status",
            "refund_amount",
            "updated_at",
        ),
        dedupe=("order_ref", "after_sale_ref", "status"),
    ),
    _event(
        "order.after_sale_changed",
        "order-events",
        fields=(
            "order_ref",
            "items",
            "after_sale_ref",
            "after_sale_type",
            "status",
            "refund_amount",
            "updated_at",
        ),
        dedupe=("order_ref", "after_sale_ref", "status"),
    ),
)
PRODUCT_EVENTS = (
    _event(
        "product.qualified_snapshot",
        "product-daily",
        fields=(
            "business_date",
            "product_id",
            "title",
            "category_id",
            "category_path",
            "status",
            "first_listed_at",
            "last_listed_at",
            "price",
            "stock",
            "sku_count",
            "source_platform",
            "source_product_id",
            "thumbnail_url",
            "thumbnail_updated_at",
            "paid_orders_today",
            "paid_orders_yesterday",
            "paid_units_today",
            "paid_units_yesterday",
            "period_sales",
            "exposure",
        ),
        dedupe=("business_date", "product_id"),
        aggregation="latest",
    ),
    _event(
        "product.daily_metrics",
        "product-daily",
        fields=(
            "business_date",
            "product_id",
            "exposure",
            "exposure_people",
            "click_count",
            "click_people",
            "cart_count",
            "paid_orders",
            "sales_count",
            "sales_amount",
            "refund_amount",
            "purchase_cost",
            "commission",
            "profit",
            "thumbnail_url",
        ),
        dedupe=("business_date", "product_id"),
        aggregation="latest",
    ),
    _event(
        "product.listing_changed",
        "product-daily",
        fields=(
            "product_id",
            "from_status",
            "to_status",
            "updated_at",
        ),
        dedupe=("product_id", "to_status"),
    ),
)
UPLOAD_EVENTS = (
    _event(
        "upload.batch_created",
        "upload-events",
        fields=(
            "batch_ref",
            "total_count",
            "selected_count",
            "store_count",
            "template_ref",
            "created_at",
        ),
        dedupe=("batch_ref",),
    ),
    _event(
        "upload.batch_completed",
        "upload-events",
        fields=(
            "batch_ref",
            "task_count",
            "success_count",
            "failed_count",
            "duration_ms",
            "completed_at",
        ),
        dedupe=("batch_ref", "completed_at"),
    ),
    _event(
        "upload.item_failed",
        "upload-events",
        fields=(
            "batch_ref",
            "task_ref",
            "item_ref",
            "error_category",
            "attempt_count",
            "failed_at",
        ),
        dedupe=("task_ref", "item_ref", "error_category"),
    ),
)
PROCUREMENT_EVENTS = (
    _event(
        "procurement.link_associated",
        "procurement-events",
        fields=(
            "order_ref",
            "product_id",
            "sku_ref",
            "quantity",
            "store_order_item_ref",
            "platform",
            "account_ref",
            "source_product_id",
            "source_sku_id",
            "mapping_count",
            "associated_at",
        ),
        dedupe=("order_ref", "source_product_id", "source_sku_id"),
    ),
    _event(
        "procurement.purchase_submitted",
        "procurement-events",
        fields=(
            "order_ref",
            "product_id",
            "sku_ref",
            "quantity",
            "store_order_item_ref",
            "procurement_ref",
            "platform",
            "account_ref",
            "purchase_amount",
            "submitted_at",
            "verification_required",
        ),
        dedupe=("order_ref", "procurement_ref"),
    ),
    _event(
        "procurement.supplier_status_changed",
        "procurement-events",
        fields=(
            "procurement_ref",
            "product_id",
            "sku_ref",
            "quantity",
            "store_order_item_ref",
            "from_status",
            "to_status",
            "updated_at",
        ),
        dedupe=("procurement_ref", "to_status"),
    ),
    _event(
        "procurement.tracking_received",
        "procurement-events",
        fields=(
            "procurement_ref",
            "product_id",
            "sku_ref",
            "quantity",
            "store_order_item_ref",
            "tracking_company",
            "tracking_no",
            "received_at",
        ),
        dedupe=("procurement_ref", "tracking_no"),
    ),
    _event(
        "procurement.store_shipped",
        "procurement-events",
        fields=(
            "order_ref",
            "product_id",
            "sku_ref",
            "quantity",
            "store_order_item_ref",
            "procurement_ref",
            "package_count",
            "shipped_at",
            "official_readback",
        ),
        dedupe=("order_ref", "shipped_at"),
    ),
    _event(
        "procurement.after_sale_changed",
        "procurement-events",
        fields=(
            "order_ref",
            "product_id",
            "sku_ref",
            "quantity",
            "store_order_item_ref",
            "procurement_ref",
            "stage",
            "status",
            "updated_at",
        ),
        dedupe=("order_ref", "stage", "status"),
    ),
    _event(
        "procurement.exception",
        "procurement-events",
        priority=0,
        fields=(
            "order_ref",
            "product_id",
            "sku_ref",
            "quantity",
            "store_order_item_ref",
            "procurement_ref",
            "error_category",
            "exception_at",
        ),
        dedupe=("order_ref", "error_category"),
    ),
)
LOGISTICS_EVENTS = (
    _event(
        "logistics.shipment_changed",
        "order-events",
        fields=(
            "order_ref",
            "tracking_company",
            "tracking_no",
            "package_count",
            "logistics_status",
            "updated_at",
        ),
        dedupe=("order_ref", "logistics_status", "tracking_no"),
    ),
)
ACTIVITY_EVENTS = (
    _event(
        "activity.changed",
        "store-daily",
        priority=2,
        fields=(
            "activity_ref",
            "activity_type",
            "status",
            "item_count",
            "updated_at",
        ),
        dedupe=("activity_ref", "status"),
    ),
)
FINANCE_EVENTS = (
    _event(
        "finance.daily_ledger",
        "store-daily",
        fields=(
            "business_date",
            "sales_amount",
            "refund_amount",
            "purchase_amount",
            "commission",
            "other_cost",
            "profit",
        ),
        dedupe=("business_date",),
        aggregation="latest",
    ),
)


GENERIC_MODULE_KEYS = (
    "transit",
    "statistics",
    "supply_market",
    "hosted",
    "violations",
    "customer_service",
    "big_screen",
    "operations",
    "runtime_logs",
    "store_groups",
    "automation",
    "storefront",
    "team",
    "delivery_ledger",
)
MODULE_DEFINITIONS: dict[str, tuple[EventDefinition, ...]] = {
    "stores": STORE_EVENTS,
    "orders": ORDER_EVENTS,
    "products": PRODUCT_EVENTS,
    "upload": UPLOAD_EVENTS,
    "procurement": PROCUREMENT_EVENTS,
    "logistics": LOGISTICS_EVENTS,
    "activity": ACTIVITY_EVENTS,
    "finance": FINANCE_EVENTS,
    "after_sales": ORDER_EVENTS[2:],
    **{key: () for key in GENERIC_MODULE_KEYS},
}


def register_builtin_adapters() -> None:
    existing = set(registry.module_keys())
    for module_key, definitions in MODULE_DEFINITIONS.items():
        if module_key in existing:
            continue
        registry.register(ModuleAnalyticsAdapter(module_key, definitions))


def adapter_reference(module_key: str) -> AnalyticsAdapter:
    return registry.get(module_key)


def event_definition_map() -> dict[str, tuple[str, ...]]:
    return {
        module_key: tuple(
            definition.event_type
            for definition in registry.get(module_key).event_types()
        )
        for module_key in registry.module_keys()
    }
