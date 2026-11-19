from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from .enums import ProductState
from .models import Product
from .rules import ensure_utc, validate_money_cents


class ProductSortField(StrEnum):
    PRODUCT_ID = "product_id"
    TITLE = "title"
    PRICE = "price"
    SALES = "sales"
    LISTING_TIME = "listing_time"
    UPDATED_AT = "updated_at"


@dataclass(frozen=True, slots=True)
class ProductFilter:
    keyword: str = ""
    title_exact: bool = False
    states: tuple[ProductState, ...] = ()
    product_ids: tuple[str, ...] = ()
    min_price_cents: int | None = None
    max_price_cents: int | None = None
    zero_sales: bool = False
    sales_min: int | None = None
    sales_max: int | None = None
    listed_from: datetime | None = None
    listed_to: datetime | None = None
    page: int = 1
    page_size: int = 50
    sort_by: ProductSortField = ProductSortField.UPDATED_AT
    sort_desc: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "keyword", str(self.keyword or "").strip())
        object.__setattr__(self, "title_exact", bool(self.title_exact))
        object.__setattr__(
            self,
            "states",
            tuple(ProductState(item) for item in self.states),
        )
        object.__setattr__(
            self,
            "product_ids",
            tuple(
                dict.fromkeys(
                    str(item).strip()
                    for item in self.product_ids
                    if str(item).strip()
                )
            ),
        )
        if self.min_price_cents is not None:
            validate_money_cents(
                self.min_price_cents,
                field="最低价格",
            )
        if self.max_price_cents is not None:
            validate_money_cents(
                self.max_price_cents,
                field="最高价格",
            )
        if (
            self.min_price_cents is not None
            and self.max_price_cents is not None
            and self.min_price_cents > self.max_price_cents
        ):
            raise ValueError("最低价格不能高于最高价格")
        for name, value in (
            ("最低销量", self.sales_min),
            ("最高销量", self.sales_max),
        ):
            if value is not None and (
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < 0
            ):
                raise ValueError(f"{name}必须是非负整数")
        if (
            self.sales_min is not None
            and self.sales_max is not None
            and self.sales_min > self.sales_max
        ):
            raise ValueError("最低销量不能高于最高销量")
        if self.zero_sales and (
            self.sales_min is not None or self.sales_max is not None
        ):
            raise ValueError("零销量筛选不能与销量区间同时使用")
        listed_from = (
            ensure_utc(self.listed_from, field="上架起始时间")
            if self.listed_from is not None
            else None
        )
        listed_to = (
            ensure_utc(self.listed_to, field="上架结束时间")
            if self.listed_to is not None
            else None
        )
        if (
            listed_from is not None
            and listed_to is not None
            and listed_from > listed_to
        ):
            raise ValueError("上架起始时间不能晚于结束时间")
        object.__setattr__(self, "listed_from", listed_from)
        object.__setattr__(self, "listed_to", listed_to)
        if (
            isinstance(self.page, bool)
            or not isinstance(self.page, int)
            or self.page < 1
        ):
            raise ValueError("页码必须大于等于 1")
        if (
            isinstance(self.page_size, bool)
            or not isinstance(self.page_size, int)
            or not 1 <= self.page_size <= 500
        ):
            raise ValueError("每页数量必须在 1 到 500 之间")
        object.__setattr__(self, "sort_by", ProductSortField(self.sort_by))
        object.__setattr__(self, "sort_desc", bool(self.sort_desc))


@dataclass(frozen=True, slots=True)
class ProductPage:
    items: tuple[Product, ...]
    total: int
    page: int
    page_size: int
    page_count: int
    unavailable_fields: tuple[str, ...] = ()
    source_summary: tuple[str, ...] = field(default_factory=tuple)
