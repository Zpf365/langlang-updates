from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Sequence

import requests

from ...wechat_api import WechatApiError
from ..domain.catalog import (
    CatalogIssue,
    CatalogPageData,
    CatalogProductData,
)
from ..domain.enums import DataSource, ProductState
from ..domain.models import Product, Sku
from .repository import DongxiaoRepository


class CatalogAdapterError(RuntimeError):
    pass


class CatalogAccessError(CatalogAdapterError):
    pass


class CatalogTimeoutError(CatalogAdapterError):
    pass


class CatalogOfflineError(CatalogAdapterError):
    pass


class CatalogDataError(CatalogAdapterError):
    pass


class ProductCatalogAdapter(ABC):
    source: DataSource

    @abstractmethod
    def list_page(
        self,
        store,
        *,
        cursor: str = "",
        page_size: int = 30,
    ) -> CatalogPageData:
        raise NotImplementedError

    @abstractmethod
    def get_product_detail(self, store, product_id: str) -> CatalogProductData:
        raise NotImplementedError

    def get_skus(self, store, product_id: str) -> tuple[Sku, ...]:
        return self.get_product_detail(store, product_id).skus

    def get_price_stock_status(
        self,
        store,
        product_id: str,
    ) -> tuple[int, int, ProductState]:
        detail = self.get_product_detail(store, product_id)
        return (
            detail.product.min_price_cents,
            detail.product.stock_total,
            detail.product.state,
        )


class CatalogAdapterMode(StrEnum):
    OFFICIAL = "official"
    CACHE = "cache"
    MOCK = "mock"


class OfficialProductCatalogAdapter(ProductCatalogAdapter):
    """微信小店官方 OpenAPI 只读适配器。"""

    source = DataSource.OFFICIAL_API

    def __init__(self, sync_service: Any) -> None:
        self.sync_service = sync_service

    def list_page(
        self,
        store,
        *,
        cursor: str = "",
        page_size: int = 30,
    ) -> CatalogPageData:
        del page_size
        try:
            client = self.sync_service.client_for(store)
            api_page = client.fetch_product_id_page(cursor)
        except requests.Timeout as exc:
            raise CatalogTimeoutError("获取商品目录请求超时") from exc
        except requests.ConnectionError as exc:
            raise CatalogOfflineError("网络不可用，无法获取商品目录") from exc
        except (PermissionError, ValueError) as exc:
            raise CatalogAccessError(str(exc) or "店铺没有官方商品接口权限") from exc
        except WechatApiError as exc:
            raise CatalogAccessError(str(exc)) from exc

        items: list[CatalogProductData] = []
        issues: list[CatalogIssue] = []
        for product_id in api_page.ids:
            try:
                items.append(self.get_product_detail(store, str(product_id)))
            except CatalogTimeoutError:
                issues.append(
                    CatalogIssue(
                        str(product_id),
                        "timeout",
                        "商品详情请求超时",
                    )
                )
            except CatalogOfflineError:
                issues.append(
                    CatalogIssue(
                        str(product_id),
                        "offline",
                        "网络不可用",
                    )
                )
            except CatalogDataError as exc:
                issues.append(
                    CatalogIssue(str(product_id), "missing_data", str(exc))
                )
        return CatalogPageData(
            items=tuple(items),
            next_cursor=str(api_page.next_key or ""),
            source=self.source,
            issues=tuple(issues),
        )

    def get_product_detail(self, store, product_id: str) -> CatalogProductData:
        try:
            client = self.sync_service.client_for(store)
            legacy = client.fetch_product(store, str(product_id))
        except requests.Timeout as exc:
            raise CatalogTimeoutError("获取商品详情请求超时") from exc
        except requests.ConnectionError as exc:
            raise CatalogOfflineError("网络不可用，无法获取商品详情") from exc
        except (PermissionError, ValueError) as exc:
            raise CatalogAccessError(str(exc) or "店铺没有官方商品接口权限") from exc
        except WechatApiError as exc:
            raise CatalogAccessError(str(exc)) from exc

        observed_at = datetime.now(UTC)
        state = {
            5: ProductState.ACTIVE,
            6: ProductState.INACTIVE,
            11: ProductState.INACTIVE,
            12: ProductState.INACTIVE,
            13: ProductState.INACTIVE,
            14: ProductState.INACTIVE,
            15: ProductState.INACTIVE,
            20: ProductState.INACTIVE,
        }.get(int(legacy.status), ProductState.UNKNOWN)
        if not str(legacy.product_id or product_id).strip():
            raise CatalogDataError("官方商品详情缺少商品 ID")
        if not str(legacy.title or "").strip():
            raise CatalogDataError("官方商品详情缺少标题")
        product = Product(
            store_id=int(store.id),
            store_app_id=str(store.app_id),
            product_id=str(legacy.product_id or product_id),
            title=str(legacy.title or ""),
            state=state,
            min_price_cents=int(legacy.min_price or 0),
            stock_total=int(legacy.stock_total or 0),
            observed_at=observed_at,
            data_source=self.source,
            last_synced_at=observed_at,
        )
        skus = self._parse_skus(
            store,
            product.product_id,
            legacy.raw,
            observed_at,
        )
        return CatalogProductData(product=product, skus=skus)

    @staticmethod
    def _parse_skus(
        store,
        product_id: str,
        raw: dict[str, Any],
        observed_at: datetime,
    ) -> tuple[Sku, ...]:
        result: list[Sku] = []
        for item in raw.get("skus") or []:
            if not isinstance(item, dict):
                continue
            sku_id = str(item.get("sku_id") or "").strip()
            if not sku_id:
                continue
            price_value = item.get("sale_price")
            stock_value = item.get("stock_num")
            complete = (
                isinstance(price_value, int)
                and not isinstance(price_value, bool)
                and isinstance(stock_value, int)
                and not isinstance(stock_value, bool)
            )
            price = int(price_value) if complete else 0
            stock = int(stock_value) if complete else 0
            specs = tuple(
                (
                    str(attr.get("attr_key") or "").strip(),
                    str(attr.get("attr_value") or "").strip(),
                )
                for attr in item.get("sku_attrs") or []
                if isinstance(attr, dict)
            )
            available = complete and stock > 0
            purchase_min = item.get("min_purchase_num")
            purchase_max = item.get("max_purchase_num")
            result.append(
                Sku(
                    store_id=int(store.id),
                    store_app_id=str(store.app_id),
                    product_id=product_id,
                    sku_id=sku_id,
                    specs=specs,
                    price_cents=max(0, price),
                    stock=max(0, stock),
                    available=available,
                    purchase_min=(
                        int(purchase_min)
                        if isinstance(purchase_min, int)
                        and not isinstance(purchase_min, bool)
                        and purchase_min > 0
                        else 1
                    ),
                    purchase_max=(
                        int(purchase_max)
                        if isinstance(purchase_max, int)
                        and not isinstance(purchase_max, bool)
                        and purchase_max > 0
                        else None
                    ),
                    is_default=bool(
                        item.get("is_default")
                        or item.get("default")
                    ),
                    observed_at=observed_at,
                    data_complete=complete,
                )
            )
        return tuple(result)


class MockProductCatalogAdapter(ProductCatalogAdapter):
    source = DataSource.MOCK

    def __init__(
        self,
        items: Sequence[CatalogProductData],
        *,
        fail_product_ids: frozenset[str] = frozenset(),
    ) -> None:
        self.items = tuple(items)
        self.fail_product_ids = frozenset(fail_product_ids)

    def list_page(
        self,
        store,
        *,
        cursor: str = "",
        page_size: int = 30,
    ) -> CatalogPageData:
        start = int(cursor or "0")
        end = min(len(self.items), start + max(1, int(page_size)))
        issues: list[CatalogIssue] = []
        selected: list[CatalogProductData] = []
        for item in self.items[start:end]:
            if item.product.product_id in self.fail_product_ids:
                issues.append(
                    CatalogIssue(
                        item.product.product_id,
                        "mock_failure",
                        "模拟商品详情同步失败",
                    )
                )
            else:
                selected.append(item)
        cursor_value = str(end) if end < len(self.items) else ""
        return CatalogPageData(
            items=tuple(selected),
            next_cursor=cursor_value,
            total=len(self.items),
            source=self.source,
            issues=tuple(issues),
        )

    def get_product_detail(self, store, product_id: str) -> CatalogProductData:
        del store
        for item in self.items:
            if item.product.product_id == str(product_id):
                return item
        raise CatalogDataError(f"Mock 商品不存在：{product_id}")


class CachedProductCatalogAdapter(ProductCatalogAdapter):
    source = DataSource.LOCAL_CACHE

    def __init__(self, repository: DongxiaoRepository) -> None:
        self.repository = repository

    def list_page(
        self,
        store,
        *,
        cursor: str = "",
        page_size: int = 30,
    ) -> CatalogPageData:
        from ..domain.queries import ProductFilter

        page = int(cursor or "1")
        product_page = self.repository.query_products(
            int(store.id),
            ProductFilter(page=page, page_size=max(1, int(page_size))),
        )
        items = []
        for product in product_page.items:
            items.append(
                CatalogProductData(
                    product=replace(product, from_cache=True),
                    skus=tuple(
                        self.repository.list_skus(
                            product.store_id,
                            product.product_id,
                        )
                    ),
                )
            )
        next_cursor = (
            str(page + 1)
            if page < product_page.page_count
            else ""
        )
        return CatalogPageData(
            items=tuple(items),
            next_cursor=next_cursor,
            total=product_page.total,
            source=self.source,
        )

    def get_product_detail(self, store, product_id: str) -> CatalogProductData:
        product = self.repository.get_product(int(store.id), str(product_id))
        if product is None:
            raise CatalogDataError("本地缓存中没有该商品")
        return CatalogProductData(
            product=replace(product, from_cache=True),
            skus=tuple(
                self.repository.list_skus(
                    product.store_id,
                    product.product_id,
                )
            ),
        )


def build_catalog_adapter(
    mode: CatalogAdapterMode | str,
    *,
    sync_service: Any | None = None,
    repository: DongxiaoRepository | None = None,
    mock_items: Sequence[CatalogProductData] = (),
) -> ProductCatalogAdapter:
    selected = CatalogAdapterMode(mode)
    if selected is CatalogAdapterMode.OFFICIAL:
        if sync_service is None:
            raise CatalogAccessError("官方接口适配器缺少现有店铺服务")
        return OfficialProductCatalogAdapter(sync_service)
    if selected is CatalogAdapterMode.CACHE:
        if repository is None:
            raise CatalogDataError("缓存适配器缺少 DongxiaoRepository")
        return CachedProductCatalogAdapter(repository)
    return MockProductCatalogAdapter(mock_items)
