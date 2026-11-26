from __future__ import annotations

import copy
import json
import os
import re
import socket
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Protocol
from urllib.parse import urlencode, urlsplit

import requests

from .db import order_status_text, product_status_text
from .models import Order, OrderItem, Product, ShippingAccount, ShippingTemplate, Store

API_ROOT = "https://api.weixin.qq.com"
MAX_LIST_PAGES = 1000
TOKEN_INVALID_CODES = frozenset({40001, 40014, 42001})
TOKEN_PATH = "/cgi-bin/stable_token"
ORDER_LIST_PATH = "/channels/ec/order/list/get"
ORDER_DETAIL_PATH = "/channels/ec/order/get"
PRODUCT_LIST_PATH = "/channels/ec/product/list/get"
PRODUCT_DETAIL_PATH = "/channels/ec/product/get"
PRODUCT_LISTING_PATH = "/channels/ec/product/listing"
PRODUCT_DELISTING_PATH = "/channels/ec/product/delisting"
PRODUCT_STOCK_PATH = "/channels/ec/product/stock/update"
SENSITIVE_DECODE_PATH = "/channels/ec/order/sensitiveinfo/decode"
DELIVERY_COMPANY_LIST_PATH = "/channels/ec/order/deliverycompanylist/new/get"
EWAYBILL_DELIVERY_PATH = "/channels/ec/logistics/ewaybill/biz/delivery/get"
EWAYBILL_ACCOUNT_PATH = "/channels/ec/logistics/ewaybill/biz/account/get"
EWAYBILL_TEMPLATE_PATH = "/channels/ec/logistics/ewaybill/biz/template/get"
EWAYBILL_PRECREATE_PATH = "/channels/ec/logistics/ewaybill/biz/order/precreate"
EWAYBILL_CREATE_PATH = "/channels/ec/logistics/ewaybill/biz/order/create"
EWAYBILL_GET_PATH = "/channels/ec/logistics/ewaybill/biz/order/get"
EWAYBILL_PRINT_PATH = "/channels/ec/logistics/ewaybill/biz/order/print"
ORDER_DELIVERY_PATH = "/channels/ec/order/delivery/send"
SHOP_INFO_PATH = "/channels/ec/basics/info/get"
AFTER_SALE_LIST_PATH = "/channels/ec/aftersale/getaftersalelist"
AFTER_SALE_DETAIL_PATH = "/channels/ec/aftersale/getaftersaleorder"
SHOP_OVERVIEW_PATH = "/channels/ec/compass/shop/overall/get"
SHOP_PRODUCT_METRICS_PATH = "/channels/ec/compass/shop/product/list/get"
ORDER_NOTES_PATH = "/channels/ec/order/merchantnotes/update"
ORDER_PRICE_PATH = "/channels/ec/order/price/update"
ORDER_ADDRESS_PATH = "/channels/ec/order/address/update"
ORDER_DELIVERY_INFO_PATH = "/channels/ec/order/deliveryinfo/update"
AFTER_SALE_ACCEPT_PATH = "/channels/ec/aftersale/acceptapply"
AFTER_SALE_REJECT_PATH = "/channels/ec/aftersale/rejectapply"
AFTER_SALE_NEGOTIATE_PATH = "/channels/ec/aftersale/merchantupdateaftersale"
AFTER_SALE_RESHIP_PATH = "/channels/ec/aftersale/merchantreship"
AFTER_SALE_UPDATE_RESHIP_PATH = "/channels/ec/aftersale/merchantupdatereshipexpress"
AFTER_SALE_ACCEPT_EXCHANGE_PATH = "/channels/ec/aftersale/acceptexchangereship"
AFTER_SALE_REJECT_EXCHANGE_PATH = "/channels/ec/aftersale/rejectexchangereship"
PRODUCT_ADD_PATH = "/channels/ec/product/add"
PRODUCT_UPDATE_PATH = "/channels/ec/product/update"
PRODUCT_DELETE_PATH = "/channels/ec/product/delete"
PRODUCT_CANCEL_AUDIT_PATH = "/channels/ec/product/audit/cancel"
PRODUCT_TAGLINK_PATH = "/channels/ec/product/taglink/get"
PRODUCT_AUDIT_QUOTA_PATH = "/channels/ec/product/getauditquota"
PRODUCT_IMAGE_UPLOAD_PATH = "/shop/ec/basics/img/upload"
FREIGHT_TEMPLATE_LIST_PATH = "/channels/ec/merchant/getfreighttemplatelist"
FREIGHT_TEMPLATE_DETAIL_PATH = "/channels/ec/merchant/getfreighttemplatedetail"
MERCHANT_ADDRESS_LIST_PATH = "/channels/ec/merchant/address/list"
MERCHANT_ADDRESS_GET_PATH = "/channels/ec/merchant/address/get"
CATEGORY_PRODUCT_RULE_PATH = "/shop/ec/category/getcategoryproductrule"
CATEGORY_ALL_PATH = "/shop/ec/category/all"
CATEGORY_RELATION_LIST_PATH = "/shop/ec/category/get_category_relation_list"
CATEGORY_RULE_PATH = "/shop/ec/category/getcategoryrule"
BRAND_VALID_LIST_PATH = "/channels/ec/brand/valid/list/get"
COUPON_LIST_PATH = "/channels/ec/coupon/get_list"
COUPON_DETAIL_PATH = "/channels/ec/coupon/get"
COUPON_CREATE_PATH = "/channels/ec/coupon/create"
COUPON_UPDATE_PATH = "/channels/ec/coupon/update"
COUPON_STATUS_PATH = "/channels/ec/coupon/update_status"
STOREFRONT_LIST_PATH = "/channels/ec/store/window/product/list/get"
STOREFRONT_REORDER_PATH = "/channels/ec/store/window/product/reorder"
STOREFRONT_HIDE_PATH = "/channels/ec/store/window/product/hide"
STOREFRONT_TOP_PATH = "/channels/ec/store/window/product/settop"
FUNDS_BALANCE_PATH = "/channels/ec/funds/getbalance"
FUNDS_FLOW_LIST_PATH = "/channels/ec/funds/getfundsflowlist"
LEAGUE_ITEM_BATCH_ADD_PATH = "/channels/ec/league/item/batchadd"
LEAGUE_ITEM_LIST_PATH = "/channels/ec/league/item/list/get"
LEAGUE_ITEM_DETAIL_PATH = "/channels/ec/league/item/get"
LEAGUE_ITEM_UPDATE_PATH = "/channels/ec/league/item/upd"
LIMITED_DISCOUNT_ADD_PATH = "/channels/ec/product/limiteddiscounttask/add"
LIMITED_DISCOUNT_LIST_PATH = "/channels/ec/product/limiteddiscounttask/list/get"
LIMITED_DISCOUNT_STOP_PATH = "/channels/ec/product/limiteddiscounttask/stop"


def _redact_sensitive_text(value: object) -> str:
    text = str(value)
    text = re.sub(
        r"(?i)\b(access_token|token|appsecret|secret|password|"
        r"cookie|authorization)\b\s*[:=]\s*[^&;\s,]+",
        r"\1=[REDACTED]",
        text,
    )
    text = re.sub(
        r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+",
        "Bearer [REDACTED]",
        text,
    )
    return re.sub(r"https?://[^\s]+", "[URL]", text)


def _response_id_list(
    result: dict[str, Any],
    field: str,
    label: str,
) -> list[str]:
    if field not in result:
        raise WechatApiError(f"{label}缺少必要字段 {field}")
    values = result.get(field)
    if not isinstance(values, list):
        raise WechatApiError(f"{label}字段 {field} 格式错误")
    ids: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text:
            raise WechatApiError(f"{label}字段 {field} 包含空 ID")
        ids.append(text)
    return ids


def normalize_sku_sale_attrs(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize SKU attribute aliases and reject conflicting duplicates."""
    normalized_payload = copy.deepcopy(payload)
    skus = normalized_payload.get("skus")
    if not isinstance(skus, list):
        return normalized_payload
    for sku_index, sku in enumerate(skus, start=1):
        if not isinstance(sku, dict):
            continue
        raw_attrs = (
            sku.get("sku_attrs")
            or sku.get("sale_attrs")
            or sku.get("attributes")
            or []
        )
        if not isinstance(raw_attrs, list):
            continue
        by_key: dict[str, str] = {}
        clean_attrs: list[dict[str, str]] = []
        for item in raw_attrs:
            if not isinstance(item, dict):
                continue
            key = str(
                item.get("attr_key")
                or item.get("item_key")
                or item.get("key")
                or item.get("name")
                or ""
            ).strip()
            value = str(
                item.get("attr_value")
                or item.get("item_value")
                or item.get("value")
                or ""
            ).strip()
            if not key or not value:
                continue
            current = by_key.get(key)
            if current is not None:
                if current == value:
                    continue
                sku_id = str(
                    sku.get("out_sku_id")
                    or sku.get("sku_code")
                    or sku.get("sku_id")
                    or sku_index
                )
                raise ValueError(
                    f"SKU {sku_id} 的销售属性“{key}”包含重复冲突值："
                    f"{current} / {value}"
                )
            by_key[key] = value
            clean_attrs.append(
                {"attr_key": key, "attr_value": value}
            )
        sku["sku_attrs"] = clean_attrs
        for alias in ("sale_attrs", "attributes"):
            sku.pop(alias, None)
    return normalized_payload


class WechatApiError(RuntimeError):
    def __init__(
        self,
        message: str,
        code: int | None = None,
        rid: str = "",
        request_id: str = "",
    ):
        super().__init__(message)
        self.code = code
        self.rid = rid
        self.request_id = request_id

    @property
    def category(self) -> str:
        if self.code in {40001, 40013, 40014, 40125, 41004, 42001}:
            return "authentication"
        if self.code in {-1, 45011}:
            return "rate_limit"
        if self.code in {-2, 50002}:
            return "platform_temporary"
        if self.code in {48001, 50001}:
            return "permission"
        if self.code is None:
            return "transport"
        return "api"


class WechatTransportError(WechatApiError):
    pass


class WechatNetworkError(WechatTransportError):
    pass


class WechatTimeoutError(WechatTransportError):
    pass


class Transport(Protocol):
    def post_json(self, url: str, payload: dict[str, Any], timeout: int = 25) -> dict[str, Any]: ...

    def get_json(self, url: str, timeout: int = 25) -> dict[str, Any]: ...


class HttpTransport:
    def __init__(self, use_system_proxy: bool | None = None):
        if use_system_proxy is None:
            use_system_proxy = os.environ.get("WEIMI_NETWORK_MODE", "direct") == "system"
        handlers = [] if use_system_proxy else [urllib.request.ProxyHandler({})]
        self.opener = urllib.request.build_opener(*handlers)

    def post_json(self, url: str, payload: dict[str, Any], timeout: int = 25) -> dict[str, Any]:
        return self._request_json(url, payload, "POST", timeout)

    def get_json(self, url: str, timeout: int = 25) -> dict[str, Any]:
        return self._request_json(url, None, "GET", timeout)

    def _request_json(
        self,
        url: str,
        payload: dict[str, Any] | None,
        method: str,
        timeout: int,
    ) -> dict[str, Any]:
        body = (
            json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        )
        request = urllib.request.Request(
            url,
            data=body,
            method=method,
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "User-Agent": "WeimiManager/0.1",
            },
        )
        try:
            with self.opener.open(request, timeout=timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read(300).decode("utf-8", "replace")
            raise WechatApiError(
                f"HTTP {exc.code}: {_redact_sensitive_text(detail)}"
            ) from exc
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, (TimeoutError, socket.timeout)):
                raise WechatTimeoutError(
                    f"网络请求超时: {_redact_sensitive_text(exc)}"
                ) from exc
            raise WechatNetworkError(
                f"网络请求失败: {_redact_sensitive_text(exc)}"
            ) from exc
        except (TimeoutError, socket.timeout) as exc:
            raise WechatTimeoutError(
                f"网络请求超时: {_redact_sensitive_text(exc)}"
            ) from exc
        try:
            result = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise WechatApiError("接口返回了无法解析的数据") from exc
        if not isinstance(result, dict):
            raise WechatApiError("接口返回格式不正确")
        return result


@dataclass(slots=True)
class ApiListPage:
    ids: list[str]
    next_key: str
    has_more: bool


class WechatShopClient:
    def __init__(
        self,
        app_id: str,
        secret: str,
        transport: Transport | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        min_request_interval: float | None = None,
        call_observer: Callable[[dict[str, Any]], None] | None = None,
        write_authorizer: Callable[[str, str], None] | None = None,
    ):
        self.app_id = app_id.strip()
        self._secret = secret.strip()
        self.transport = transport or HttpTransport()
        self.sleeper = sleeper
        self._token = ""
        self._token_expires_at = 0.0
        self._token_lock = threading.Lock()
        self._last_call_lock = threading.Lock()
        self._last_call_metadata: dict[str, Any] = {}
        self._rate_lock = threading.Lock()
        self._next_request_at = 0.0
        self._min_request_interval = (
            float(os.environ.get("WEIMI_MIN_REQUEST_INTERVAL", "0.12"))
            if min_request_interval is None
            else max(0.0, min_request_interval)
        )
        self.call_observer = call_observer
        self.write_authorizer = write_authorizer

    def set_call_observer(
        self,
        observer: Callable[[dict[str, Any]], None] | None,
    ) -> None:
        self.call_observer = observer

    def _post_write_api(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        action: str,
    ) -> dict[str, Any]:
        if not callable(self.write_authorizer):
            raise PermissionError(
                "官方客户端未连接当前会话写操作安全门，"
                f"已拒绝执行：{str(action)}"
            )
        self.write_authorizer(self.app_id, str(action))
        token = self.get_access_token()
        request_id = uuid.uuid4().hex
        started_at = datetime.now(UTC)
        started_perf = time.perf_counter()
        self._wait_for_rate_limit()
        try:
            result = self.transport.post_json(
                f"{API_ROOT}{path}?access_token={token}",
                payload,
            )
        except Exception as exc:
            self._notify_api_call(
                path=path,
                method="POST",
                attempt_no=1,
                request_id=request_id,
                started_at=started_at,
                started_perf=started_perf,
                error=exc,
            )
            raise
        self._notify_api_call(
            path=path,
            method="POST",
            attempt_no=1,
            request_id=request_id,
            started_at=started_at,
            started_perf=started_perf,
            result=result,
        )
        try:
            self._raise_for_error(result)
            return result
        except WechatApiError as exc:
            exc.request_id = request_id
            if exc.code not in TOKEN_INVALID_CODES:
                raise
        # An invalid-token response is a definitive business-request
        # rejection. It is safe to refresh once and resend.
        self._invalidate_access_token()
        token = self.get_access_token()
        retry_request_id = uuid.uuid4().hex
        retry_started_at = datetime.now(UTC)
        retry_started_perf = time.perf_counter()
        self._wait_for_rate_limit()
        try:
            retry_result = self.transport.post_json(
                f"{API_ROOT}{path}?access_token={token}",
                payload,
            )
        except Exception as exc:
            self._notify_api_call(
                path=path,
                method="POST",
                attempt_no=2,
                request_id=retry_request_id,
                started_at=retry_started_at,
                started_perf=retry_started_perf,
                error=exc,
            )
            raise
        self._notify_api_call(
            path=path,
            method="POST",
            attempt_no=2,
            request_id=retry_request_id,
            started_at=retry_started_at,
            started_perf=retry_started_perf,
            result=retry_result,
        )
        try:
            self._raise_for_error(retry_result)
        except WechatApiError as exc:
            exc.request_id = retry_request_id
            raise
        return retry_result

    def _notify_api_call(
        self,
        *,
        path: str,
        method: str,
        attempt_no: int,
        request_id: str,
        started_at: datetime,
        started_perf: float,
        result: dict[str, Any] | None = None,
        error: BaseException | None = None,
    ) -> None:
        if self.call_observer is None:
            return
        finished_at = datetime.now(UTC)
        errcode = None
        rid = ""
        if result is not None:
            raw_code = result.get("errcode")
            try:
                errcode = int(raw_code or 0)
            except (TypeError, ValueError):
                errcode = None
            rid = str(result.get("rid") or "")[:200]
        outcome = (
            "success"
            if error is None and errcode in {None, 0}
            else (
                "api_error"
                if error is None
                else "transport_error"
            )
        )
        safe_path = urlsplit(str(path)).path or "/"
        payload = {
            "call_id": uuid.uuid4().hex,
            "store_app_id": self.app_id,
            "request_id": request_id,
            "method": method,
            "path": safe_path,
            "attempt_no": attempt_no,
            "outcome": outcome,
            "errcode": errcode,
            "rid": rid,
            "error_type": type(error).__name__ if error is not None else "",
            "error_message": self._safe_error_text(error),
            "started_at": started_at,
            "finished_at": finished_at,
            "duration_ms": max(
                0,
                int((time.perf_counter() - started_perf) * 1000),
            ),
        }
        try:
            with self._last_call_lock:
                self._last_call_metadata = dict(payload)
            self.call_observer(payload)
        except Exception:
            pass

    def last_call_metadata(self) -> dict[str, Any]:
        with self._last_call_lock:
            return dict(self._last_call_metadata)

    def _safe_error_text(self, error: BaseException | None) -> str:
        if error is None:
            return ""
        text = str(error)
        if self._secret:
            text = text.replace(self._secret, "[REDACTED]")
        return _redact_sensitive_text(text)[:1000]

    def get_access_token(self, force_refresh: bool = False) -> str:
        with self._token_lock:
            if not force_refresh and self._token and time.time() < self._token_expires_at - 60:
                return self._token
            payload = {
                "grant_type": "client_credential",
                "appid": self.app_id,
                "secret": self._secret,
            }
            if force_refresh:
                payload["force_refresh"] = True
            request_id = uuid.uuid4().hex
            started_at = datetime.now(UTC)
            started_perf = time.perf_counter()
            try:
                result = self.transport.post_json(
                    API_ROOT + TOKEN_PATH,
                    payload,
                )
            except Exception as exc:
                self._notify_api_call(
                    path=TOKEN_PATH,
                    method="POST",
                    attempt_no=1,
                    request_id=request_id,
                    started_at=started_at,
                    started_perf=started_perf,
                    error=exc,
                )
                raise
            self._notify_api_call(
                path=TOKEN_PATH,
                method="POST",
                attempt_no=1,
                request_id=request_id,
                started_at=started_at,
                started_perf=started_perf,
                result=result,
            )
            try:
                self._raise_for_error(result)
            except WechatApiError as exc:
                exc.request_id = request_id
                raise
            token = str(result.get("access_token") or "")
            if not token:
                raise WechatApiError(
                    "接口没有返回 access_token",
                    request_id=request_id,
                )
            self._token = token
            self._token_expires_at = time.time() + int(result.get("expires_in") or 7200)
            return token

    def test_connection(self) -> str:
        self.get_access_token()
        return "连接成功"

    def _invalidate_access_token(self) -> None:
        with self._token_lock:
            self._token = ""
            self._token_expires_at = 0.0

    def fetch_order_ids(
        self,
        start_time: int,
        end_time: int,
        status: int | None = None,
    ) -> list[str]:
        if end_time - start_time > 7 * 86400:
            raise ValueError("单次订单查询时间范围不能超过 7 天")
        ids: list[str] = []
        next_key = ""
        seen_keys: set[str] = set()
        for page_number in range(1, MAX_LIST_PAGES + 1):
            payload: dict[str, Any] = {
                "create_time_range": {
                    "start_time": int(start_time),
                    "end_time": int(end_time),
                },
                "page_size": 100,
            }
            if status is not None:
                payload["status"] = status
            if next_key:
                payload["next_key"] = next_key
            result = self._post_api(ORDER_LIST_PATH, payload)
            page_ids = _response_id_list(
                result,
                "order_id_list",
                "订单列表",
            )
            ids.extend(page_ids)
            has_more = result.get("has_more")
            next_key = str(result.get("next_key") or "")
            if has_more is False:
                break
            if not next_key:
                if has_more is True:
                    raise WechatApiError(
                        "订单分页返回 has_more=true 但缺少 next_key"
                    )
                break
            if next_key in seen_keys:
                raise WechatApiError("订单分页游标重复，已停止同步")
            seen_keys.add(next_key)
        else:
            raise WechatApiError(
                f"订单分页超过最大页数 {MAX_LIST_PAGES}"
            )
        return _unique(ids)

    def fetch_order_ids_by_update(
        self,
        start_time: int,
        end_time: int,
    ) -> list[str]:
        if end_time - start_time > 7 * 86400:
            raise ValueError("单次订单更新时间查询不能超过 7 天")
        ids: list[str] = []
        next_key = ""
        seen_keys: set[str] = set()
        for _page_number in range(1, MAX_LIST_PAGES + 1):
            payload: dict[str, Any] = {
                "update_time_range": {
                    "start_time": int(start_time),
                    "end_time": int(end_time),
                },
                "page_size": 100,
            }
            if next_key:
                payload["next_key"] = next_key
            result = self._post_api(ORDER_LIST_PATH, payload)
            ids.extend(
                _response_id_list(
                    result,
                    "order_id_list",
                    "订单列表",
                )
            )
            has_more = result.get("has_more")
            next_key = str(result.get("next_key") or "")
            if has_more is False:
                break
            if not next_key:
                if has_more is True:
                    raise WechatApiError(
                        "订单分页返回 has_more=true 但缺少 next_key"
                    )
                break
            if next_key in seen_keys:
                raise WechatApiError("订单分页游标重复，已停止同步")
            seen_keys.add(next_key)
        else:
            raise WechatApiError(
                f"订单分页超过最大页数 {MAX_LIST_PAGES}"
            )
        return _unique(ids)

    def fetch_order(self, store: Store, order_id: str) -> Order:
        result = self._post_api(ORDER_DETAIL_PATH, {"order_id": str(order_id)})
        payload = result.get("order") or result.get("data", {}).get("order")
        if not isinstance(payload, dict):
            raise WechatApiError(f"订单 {order_id} 返回内容为空")
        return parse_order(store, payload)

    def fetch_product_ids(self, status: int | None = None) -> list[str]:
        ids: list[str] = []
        next_key = ""
        seen_keys = set()
        for _page_number in range(1, MAX_LIST_PAGES + 1):
            page = self.fetch_product_id_page(next_key, status)
            ids.extend(page.ids)
            next_key = page.next_key
            if not next_key:
                break
            if next_key in seen_keys:
                raise WechatApiError("微信商品分页游标重复，未能读取完整商品目录")
            seen_keys.add(next_key)
        else:
            raise WechatApiError(
                f"商品分页超过最大页数 {MAX_LIST_PAGES}"
            )
        return _unique(ids)

    def fetch_product_id_page(
        self,
        next_key: str = "",
        status: int | None = None,
        page_size: int = 30,
    ) -> ApiListPage:
        if isinstance(page_size, bool) or not isinstance(page_size, int):
            raise ValueError("商品分页大小必须是整数")
        if not 1 <= page_size <= 30:
            raise ValueError("商品分页大小必须在 1 到 30 之间")
        payload: dict[str, Any] = {"page_size": page_size}
        if status is not None:
            payload["status"] = status
        if next_key:
            payload["next_key"] = next_key
        result = self._post_api(PRODUCT_LIST_PATH, payload)
        product_ids = _response_id_list(
            result,
            "product_ids",
            "商品目录",
        )
        next_key = str(result.get("next_key") or "")
        has_more = bool(result.get("has_more", bool(next_key)))
        if has_more and not next_key:
            raise WechatApiError(
                "商品分页返回 has_more=true 但缺少 next_key"
            )
        return ApiListPage(
            ids=product_ids,
            next_key=next_key,
            has_more=has_more,
        )

    def fetch_product_total(self, status: int | None = None) -> int:
        payload: dict[str, Any] = {"page_size": 1}
        if status is not None:
            payload["status"] = int(status)
        result = self._post_api(PRODUCT_LIST_PATH, payload)
        data = result.get("data")
        raw_total = result.get("total_num")
        if raw_total in (None, "") and isinstance(data, dict):
            raw_total = data.get("total_num")
        if raw_total in (None, ""):
            raise WechatApiError("商品列表响应缺少 total_num，无法确认商品数量")
        try:
            return int(raw_total)
        except (TypeError, ValueError) as exc:
            raise WechatApiError("商品列表 total_num 不是有效整数") from exc

    def fetch_product(self, store: Store, product_id: str) -> Product:
        result = self._post_api(
            PRODUCT_DETAIL_PATH,
            {"product_id": str(product_id), "data_type": 1},
        )
        payload = result.get("product") or result.get("data", {}).get("product")
        if not isinstance(payload, dict):
            raise WechatApiError(f"商品 {product_id} 返回内容为空")
        return parse_product(store, payload)

    def decode_sensitive_info(self, order_id: str) -> dict[str, Any]:
        return self._post_api(SENSITIVE_DECODE_PATH, {"order_id": str(order_id)})

    def fetch_store_info(self) -> dict[str, Any]:
        result = self._get_api(SHOP_INFO_PATH)
        return result.get("info") or {}

    def fetch_after_sale_ids(
        self,
        start_time: int,
        end_time: int,
        time_field: str = "create",
    ) -> list[str]:
        if end_time - start_time > 86400:
            raise ValueError("售后单查询时间范围不能超过 24 小时")
        ids: list[str] = []
        next_key = ""
        seen_keys: set[str] = set()
        for _page_number in range(1, MAX_LIST_PAGES + 1):
            prefix = "update" if time_field == "update" else "create"
            payload: dict[str, Any] = {
                f"begin_{prefix}_time": int(start_time),
                f"end_{prefix}_time": int(end_time),
            }
            if next_key:
                payload["next_key"] = next_key
            result = self._post_api(AFTER_SALE_LIST_PATH, payload)
            ids.extend(
                _response_id_list(
                    result,
                    "after_sale_order_id_list",
                    "售后列表",
                )
            )
            has_more = result.get("has_more")
            next_key = str(result.get("next_key") or "")
            if has_more is False:
                break
            if not next_key:
                if has_more is True:
                    raise WechatApiError(
                        "售后分页返回 has_more=true 但缺少 next_key"
                    )
                break
            if next_key in seen_keys:
                raise WechatApiError("售后分页游标重复，已停止同步")
            seen_keys.add(next_key)
        else:
            raise WechatApiError(
                f"售后分页超过最大页数 {MAX_LIST_PAGES}"
            )
        return _unique(ids)

    def fetch_after_sale(self, after_sale_id: str) -> dict[str, Any]:
        result = self._post_api(
            AFTER_SALE_DETAIL_PATH,
            {"after_sale_order_id": str(after_sale_id)},
        )
        return result.get("after_sale_order") or {}

    def fetch_shop_overview(self, day: str) -> dict[str, str]:
        result = self._post_api(SHOP_OVERVIEW_PATH, {"ds": day})
        return result.get("data") or {}

    def fetch_shop_product_metrics(
        self,
        day: str,
        offset: int = 0,
        limit: int = 200,
    ) -> dict[str, Any]:
        return self._post_api(
            SHOP_PRODUCT_METRICS_PATH,
            {
                "ds": str(day),
                "offset": int(offset),
                "limit": int(limit),
            },
        )

    def fetch_product_day_totals(self, day: str) -> dict[str, int]:
        totals = {
            "pay_gmv": 0,
            "pay_uv": 0,
            "pay_order_cnt": 0,
            "pay_refund_gmv": 0,
            "product_click_uv": 0,
        }
        offset = 0
        while True:
            result = self.fetch_shop_product_metrics(
                day,
                offset=offset,
                limit=200,
            )
            rows = [row for row in (result.get("product_list") or []) if isinstance(row, dict)]
            for row in rows:
                data = row.get("data")
                if not isinstance(data, dict):
                    continue
                for key in totals:
                    source_key = "pay_cnt" if key == "pay_order_cnt" else key
                    try:
                        totals[key] += int(float(data.get(source_key) or 0))
                    except (TypeError, ValueError):
                        continue
            offset += len(rows)
            total = int(result.get("total_count") or 0)
            if not rows or not total or offset >= total:
                break
        return totals

    def update_order_notes(
        self,
        order_id: str,
        notes: str,
        color: int = 0,
    ) -> dict[str, Any]:
        return self._post_write_api(
            ORDER_NOTES_PATH,
            {
                "order_id": str(order_id),
                "merchant_notes": notes,
                "merchant_notes_tag_color": int(color),
            },
            action="order_notes_update",
        )

    def update_order_price(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._post_write_api(
            ORDER_PRICE_PATH,
            payload,
            action="order_price_update",
        )

    def update_order_address(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._post_write_api(
            ORDER_ADDRESS_PATH,
            payload,
            action="order_address_update",
        )

    def update_order_delivery_info(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._post_write_api(
            ORDER_DELIVERY_INFO_PATH,
            payload,
            action="order_delivery_update",
        )

    def accept_after_sale(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._post_write_api(
            AFTER_SALE_ACCEPT_PATH,
            payload,
            action="aftersale_accept",
        )

    def reject_after_sale(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._post_write_api(
            AFTER_SALE_REJECT_PATH,
            payload,
            action="aftersale_reject",
        )

    def negotiate_after_sale(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._post_write_api(
            AFTER_SALE_NEGOTIATE_PATH,
            payload,
            action="aftersale_negotiate",
        )

    def reship_after_sale(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._post_write_api(
            AFTER_SALE_RESHIP_PATH,
            payload,
            action="aftersale_reship",
        )

    def update_after_sale_reship(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._post_write_api(
            AFTER_SALE_UPDATE_RESHIP_PATH,
            payload,
            action="aftersale_reship_update",
        )

    def accept_after_sale_exchange(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._post_write_api(
            AFTER_SALE_ACCEPT_EXCHANGE_PATH,
            payload,
            action="aftersale_exchange_accept",
        )

    def reject_after_sale_exchange(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._post_write_api(
            AFTER_SALE_REJECT_EXCHANGE_PATH,
            payload,
            action="aftersale_exchange_reject",
        )

    def add_product(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._post_write_api(
            PRODUCT_ADD_PATH,
            normalize_sku_sale_attrs(payload),
            action="product_add",
        )

    def update_product(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._post_write_api(
            PRODUCT_UPDATE_PATH,
            payload,
            action="product_update",
        )

    def fetch_product_payload(
        self,
        product_id: str,
        data_type: int = 3,
    ) -> dict[str, Any]:
        return self._post_api(
            PRODUCT_DETAIL_PATH,
            {
                "product_id": str(product_id),
                "data_type": int(data_type),
            },
        )

    def update_product_prices(
        self,
        product_id: str,
        multiplier: float,
    ) -> dict[str, Any]:
        detail = self.fetch_product_payload(product_id, data_type=3)
        source = detail.get("edit_product") or detail.get("product")
        if not isinstance(source, dict):
            raise WechatApiError("商品详情没有返回可编辑数据")
        allowed = {
            "title",
            "head_imgs",
            "deliver_method",
            "deliver_acct_type",
            "desc_info",
            "cats",
            "cats_v2",
            "attrs",
            "spu_code",
            "brand_id",
            "product_qua_infos",
            "express_info",
            "aftersale_desc",
            "limited_info",
            "extra_service",
            "skus",
            "after_sale_info",
            "size_chart",
            "hide_in_window",
            "timing_onsale_info",
            "release_mode",
            "head_videos",
            "spu_deliver_info",
            "supply_source",
            "channel_info",
            "face_activation",
        }
        payload = {key: copy.deepcopy(value) for key, value in source.items() if key in allowed}
        skus = payload.get("skus")
        if not isinstance(skus, list) or not skus:
            raise WechatApiError("商品没有可修改价格的 SKU")
        changed = 0
        for sku in skus:
            if not isinstance(sku, dict):
                continue
            try:
                current = int(sku.get("sale_price") or 0)
            except (TypeError, ValueError):
                current = 0
            if current <= 0:
                continue
            sku["sale_price"] = max(1, int(round(current * multiplier)))
            changed += 1
        if not changed:
            raise WechatApiError("商品 SKU 没有有效售价")
        payload["product_id"] = str(product_id)
        payload["listing"] = 1 if int(source.get("status") or 0) == 5 else 0
        return self.update_product(payload)

    def batch_add_league_items(
        self,
        items: list[dict[str, Any]],
        *,
        plan_type: int = 100,
        begin_time: int = 0,
        end_time: int = 0,
        forever: bool = True,
    ) -> dict[str, Any]:
        clean = []
        seen: set[str] = set()
        for item in items:
            product_id = str(item.get("product_id") or "").strip()
            ratio = float(item.get("ratio") or 0)
            if (
                not product_id
                or product_id in seen
                or not 0 <= ratio <= 90
            ):
                continue
            try:
                numeric_product_id = int(product_id)
            except ValueError:
                continue
            seen.add(product_id)
            clean.append(
                {
                    "product_id": numeric_product_id,
                    "ratio": ratio,
                }
            )
        if not clean:
            raise WechatApiError("没有可新增的联盟商品")
        batch_results = []
        for start in range(0, len(clean), 20):
            batch_results.append(
                self._post_write_api(
                    LEAGUE_ITEM_BATCH_ADD_PATH,
                    {
                        "type": int(plan_type),
                        "list": clean[start : start + 20],
                        "begin_time": int(begin_time),
                        "end_time": int(end_time),
                        "is_forerver": bool(forever),
                    },
                    action="league_items_add",
                )
            )
        result = dict(batch_results[0])
        result_info_list: list[dict[str, Any]] = []
        error_messages: list[str] = []
        error_codes: list[int] = []
        for batch_result in batch_results:
            rows = batch_result.get("result_info_list")
            if isinstance(rows, list):
                result_info_list.extend(
                    row for row in rows if isinstance(row, dict)
                )
            code = int(batch_result.get("errcode") or 0)
            if code:
                error_codes.append(code)
                error_messages.append(
                    str(batch_result.get("errmsg") or f"errno {code}")
                )
        result.update(
            {
                "errcode": error_codes[0] if error_codes else 0,
                "errmsg": "；".join(dict.fromkeys(error_messages)),
                "result_info_list": result_info_list,
                "requested_count": len(clean),
                "batch_count": len(batch_results),
            }
        )
        return result

    def fetch_league_items(
        self,
        *,
        page_size: int = 100,
        page_index: int = 1,
    ) -> dict[str, Any]:
        return self._post_api(
            LEAGUE_ITEM_LIST_PATH,
            {
                "type": 100,
                "page_size": max(1, min(100, int(page_size))),
                "page_index": max(1, int(page_index)),
                "need_total_num": True,
            },
        )

    def fetch_league_item(self, product_id: str) -> dict[str, Any]:
        result = self._post_api(
            LEAGUE_ITEM_DETAIL_PATH,
            {"product_id": int(product_id)},
        )
        item = result.get("item") or result.get("info")
        return dict(item) if isinstance(item, dict) else {}

    def update_league_item(
        self,
        product_id: str,
        promotion_plan_id: str,
        ratio: float,
        *,
        plan_type: int = 100,
    ) -> dict[str, Any]:
        return self._post_write_api(
            LEAGUE_ITEM_UPDATE_PATH,
            {
                "type": int(plan_type),
                "product_id": int(product_id),
                "promotion_plan_id": int(promotion_plan_id),
                "operate_type": 1,
                "ratio": max(0, min(90, float(ratio))),
            },
            action="league_item_update",
        )

    def create_limited_discount_task(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        return self._post_write_api(
            LIMITED_DISCOUNT_ADD_PATH,
            payload,
            action="limited_discount_create",
        )

    def fetch_limited_discount_tasks(
        self,
        *,
        status: int | None = None,
        page_size: int = 50,
        next_key: str = "",
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "page_size": max(1, min(50, int(page_size))),
        }
        if status is not None:
            body["status"] = int(status)
        if next_key:
            body["next_key"] = next_key
        return self._post_api(LIMITED_DISCOUNT_LIST_PATH, body)

    def stop_limited_discount_task(self, task_id: str) -> dict[str, Any]:
        return self._post_write_api(
            LIMITED_DISCOUNT_STOP_PATH,
            {"task_id": str(task_id)},
            action="limited_discount_stop",
        )

    def delete_product(self, product_id: str) -> dict[str, Any]:
        return self._post_write_api(
            PRODUCT_DELETE_PATH,
            {"product_id": str(product_id)},
            action="product_delete",
        )

    def cancel_product_audit(self, product_id: str) -> dict[str, Any]:
        return self._post_write_api(
            PRODUCT_CANCEL_AUDIT_PATH,
            {"product_id": str(product_id)},
            action="product_audit_cancel",
        )

    def fetch_product_taglink(self, product_id: str) -> dict[str, Any]:
        return self._post_api(
            PRODUCT_TAGLINK_PATH,
            {"product_id": str(product_id)},
        )

    def fetch_product_audit_quota(self) -> dict[str, Any]:
        return self._post_api(PRODUCT_AUDIT_QUOTA_PATH, {})

    def fetch_freight_template_ids(
        self,
        page_size: int = 20,
    ) -> list[str]:
        limit = max(1, min(50, int(page_size)))
        offset = 0
        template_ids: list[str] = []
        while True:
            result = self._post_api(
                FREIGHT_TEMPLATE_LIST_PATH,
                {"offset": offset, "limit": limit},
            )
            page = [
                str(value)
                for value in result.get("template_id_list") or []
                if str(value or "").strip()
            ]
            template_ids.extend(page)
            if len(page) < limit:
                break
            offset += len(page)
        return _ordered_unique(template_ids)

    def fetch_freight_template_detail(
        self,
        template_id: str,
    ) -> dict[str, Any]:
        result = self._post_api(
            FREIGHT_TEMPLATE_DETAIL_PATH,
            {"template_id": str(template_id)},
        )
        detail = result.get("freight_template")
        if not isinstance(detail, dict):
            return {}
        item = dict(detail)
        item["template_id"] = str(item.get("template_id") or template_id)
        return item

    def fetch_default_freight_template(self) -> dict[str, Any]:
        fallback: dict[str, Any] = {}
        for template_id in self.fetch_freight_template_ids():
            detail = self.fetch_freight_template_detail(template_id)
            if not detail:
                continue
            if not fallback:
                fallback = detail
            if _to_int(detail.get("is_default")) == 1:
                return detail
        return fallback

    def fetch_merchant_address_ids(
        self,
        page_size: int = 20,
    ) -> list[str]:
        limit = max(1, min(50, int(page_size)))
        offset = 0
        address_ids: list[str] = []
        while True:
            result = self._post_api(
                MERCHANT_ADDRESS_LIST_PATH,
                {"offset": offset, "limit": limit},
            )
            page = [
                str(value)
                for value in result.get("address_id_list") or []
                if str(value or "").strip()
            ]
            address_ids.extend(page)
            if len(page) < limit:
                break
            offset += len(page)
        return _ordered_unique(address_ids)

    def fetch_merchant_address_detail(
        self,
        address_id: str,
    ) -> dict[str, Any]:
        result = self._post_api(
            MERCHANT_ADDRESS_GET_PATH,
            {"address_id": int(address_id)},
        )
        detail = result.get("address_detail")
        if not isinstance(detail, dict):
            return {}
        item = dict(detail)
        item["address_id"] = str(item.get("address_id") or address_id)
        return item

    def fetch_default_after_sale_address(self) -> dict[str, Any]:
        fallback: dict[str, Any] = {}
        receive_fallback: dict[str, Any] = {}
        for address_id in self.fetch_merchant_address_ids():
            detail = self.fetch_merchant_address_detail(address_id)
            if not detail:
                continue
            if not fallback:
                fallback = detail
            if _to_int(detail.get("recv_addr")) == 1:
                if not receive_fallback:
                    receive_fallback = detail
                if _to_int(detail.get("default_recv")) == 1:
                    return detail
        return receive_fallback or fallback

    def fetch_category_product_rule(
        self,
        category_id: str,
        brand_id: str | int = 2100000000,
        release_mode: int = 0,
    ) -> dict[str, Any]:
        result = self._post_api(
            CATEGORY_PRODUCT_RULE_PATH,
            {
                "cat_id": int(category_id),
                "brand_id": int(brand_id),
                "release_mode": int(release_mode),
            },
        )
        return result if isinstance(result, dict) else {}

    def fetch_all_categories(self) -> dict[str, Any]:
        result = self._post_api(CATEGORY_ALL_PATH, {})
        categories = result.get("cats_v2")
        if not isinstance(categories, list) or not categories:
            raise WechatApiError("官方类目树没有返回 cats_v2")
        return result

    def fetch_effective_category_relations(self) -> list[str]:
        result = self._post_api(
            CATEGORY_RELATION_LIST_PATH,
            {
                "is_filter_status": True,
                "status": 1,
            },
        )
        rows = result.get("list")
        if not isinstance(rows, list):
            raise WechatApiError(
                "店铺类目权限列表缺少必要字段 list"
            )
        category_ids: list[str] = []
        for item in rows:
            if not isinstance(item, dict):
                continue
            category_id = str(item.get("id") or "").strip()
            if not category_id:
                continue
            try:
                status = int(item.get("status") or 0)
            except (TypeError, ValueError):
                status = 0
            if status == 1:
                category_ids.append(category_id)
        return list(dict.fromkeys(category_ids))

    def fetch_valid_brands(self) -> list[dict[str, Any]]:
        brands: list[dict[str, Any]] = []
        seen_brand_ids: set[str] = set()
        next_key = ""
        seen_keys: set[str] = set()
        for _page_number in range(1, MAX_LIST_PAGES + 1):
            payload: dict[str, Any] = {"page_size": 50}
            if next_key:
                payload["next_key"] = next_key
            result = self._post_api(BRAND_VALID_LIST_PATH, payload)
            rows = result.get("brands")
            if not isinstance(rows, list):
                raise WechatApiError(
                    "店铺生效品牌列表缺少必要字段 brands"
                )
            for item in rows:
                if not isinstance(item, dict):
                    continue
                brand_id = str(item.get("brand_id") or "").strip()
                if not brand_id or brand_id in seen_brand_ids:
                    continue
                seen_brand_ids.add(brand_id)
                try:
                    status = int(item.get("status") or 0)
                except (TypeError, ValueError):
                    status = 0
                brands.append(
                    {
                        **item,
                        "brand_id": brand_id,
                        "status": status,
                        "expiring": status == 5,
                    }
                )
            next_key = str(result.get("next_key") or "")
            if not next_key:
                break
            if next_key in seen_keys:
                raise WechatApiError(
                    "店铺生效品牌分页游标重复，已停止读取"
                )
            seen_keys.add(next_key)
        else:
            raise WechatApiError(
                f"店铺生效品牌分页超过最大页数 {MAX_LIST_PAGES}"
            )
        return brands

    def fetch_category_rule(
        self,
        category_id: str,
        *,
        rule_id: int,
    ) -> dict[str, Any]:
        result = self._post_api(
            CATEGORY_RULE_PATH,
            {"category_id": int(category_id)},
            query={"rule_id": int(rule_id)},
        )
        return result if isinstance(result, dict) else {}

    def upload_product_image(self, path: str | Path) -> str:
        if callable(self.write_authorizer):
            self.write_authorizer(self.app_id, "product_image_upload")
        from PIL import Image

        image_path = Path(path)
        data = image_path.read_bytes()
        with Image.open(image_path) as image:
            width, height = image.size
            image_format = (image.format or "").upper()
        mime = {
            "JPG": "image/jpeg",
            "JPEG": "image/jpeg",
            "PNG": "image/png",
            "BMP": "image/bmp",
            "WEBP": "image/webp",
        }.get(image_format)
        if mime is None:
            raise ValueError("商品图片只支持 JPG、PNG、BMP、WEBP")
        token = self.get_access_token()
        session = requests.Session()
        session.trust_env = False
        try:
            response = session.post(
                API_ROOT + PRODUCT_IMAGE_UPLOAD_PATH,
                params={
                    "access_token": token,
                    "upload_type": 0,
                    "resp_type": 1,
                    "height": height,
                    "width": width,
                },
                files={"media": (image_path.name, data, mime)},
                timeout=60,
            )
            result = response.json()
        except requests.RequestException as exc:
            raise WechatApiError(f"图片上传失败: {exc}") from exc
        self._raise_for_error(result)
        image_url = str((result.get("pic_file") or {}).get("img_url") or "")
        if not image_url:
            raise WechatApiError("图片上传接口没有返回图片 URL")
        return image_url

    def upload_product_image_url(self, url: str) -> str:
        image_url = str(url or "").strip()
        if not image_url.startswith(("http://", "https://")):
            raise ValueError("商品图片地址必须以 http:// 或 https:// 开头")
        image_host = str(urlsplit(image_url).hostname or "").casefold()
        referer = (
            "https://detail.1688.com/"
            if image_host.endswith("alicdn.com")
            or image_host.endswith("1688.com")
            else ""
        )
        session = requests.Session()
        session.trust_env = False
        try:
            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 Chrome/124 Safari/537.36"
                ),
                "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
            }
            if referer:
                headers["Referer"] = referer
            response = session.get(
                image_url,
                timeout=60,
                headers=headers,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            raise WechatApiError(f"商品图片下载失败: {exc}") from exc
        content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        suffix = {
            "image/jpeg": ".jpg",
            "image/png": ".png",
            "image/bmp": ".bmp",
            "image/webp": ".webp",
        }.get(content_type, ".jpg")
        temporary = tempfile.NamedTemporaryFile(
            suffix=suffix,
            delete=False,
        )
        try:
            temporary.write(response.content)
            temporary.close()
            return self.upload_product_image(temporary.name)
        finally:
            try:
                Path(temporary.name).unlink()
            except FileNotFoundError:
                pass

    def fetch_coupon_ids(self, status: int) -> list[str]:
        ids: list[str] = []
        page = 1
        page_ctx = ""
        while True:
            result = self._post_api(
                COUPON_LIST_PATH,
                {
                    "status": status,
                    "page": page,
                    "page_size": 100,
                    "page_ctx": page_ctx,
                },
            )
            page_ids = [
                str(item.get("coupon_id"))
                for item in result.get("coupons", [])
                if isinstance(item, dict) and item.get("coupon_id") is not None
            ]
            ids.extend(page_ids)
            next_ctx = str(result.get("page_ctx") or "")
            total = int(result.get("total_num") or 0)
            if not page_ids or len(ids) >= total or not next_ctx:
                break
            page_ctx = next_ctx
            page += 1
        return _unique(ids)

    def fetch_coupon(self, coupon_id: str) -> dict[str, Any]:
        result = self._post_api(COUPON_DETAIL_PATH, {"coupon_id": str(coupon_id)})
        return result.get("coupon") or {}

    def create_coupon(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._post_write_api(
            COUPON_CREATE_PATH,
            payload,
            action="coupon_create",
        )

    def update_coupon(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._post_write_api(
            COUPON_UPDATE_PATH,
            payload,
            action="coupon_update",
        )

    def update_coupon_status(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._post_write_api(
            COUPON_STATUS_PATH,
            payload,
            action="coupon_status_update",
        )

    def fetch_storefront_product_ids(self) -> list[str]:
        ids: list[str] = []
        next_key = ""
        while True:
            payload: dict[str, Any] = {"page_size": 30}
            if next_key:
                payload["next_key"] = next_key
            result = self._post_api(STOREFRONT_LIST_PATH, payload)
            ids.extend(str(value) for value in result.get("product_id_list", []))
            next_key = str(result.get("next_key") or "")
            if not next_key:
                break
        return list(dict.fromkeys(ids))

    def reorder_storefront_product(self, product_id: str, index_num: int) -> None:
        self._post_write_api(
            STOREFRONT_REORDER_PATH,
            {"product_id": str(product_id), "index_num": int(index_num)},
            action="storefront_reorder",
        )

    def hide_storefront_product(self, product_id: str, hidden: bool) -> None:
        self._post_write_api(
            STOREFRONT_HIDE_PATH,
            {"product_id": str(product_id), "is_set_hide": 1 if hidden else 0},
            action="storefront_hide",
        )

    def top_storefront_product(self, product_id: str, pinned: bool) -> None:
        self._post_write_api(
            STOREFRONT_TOP_PATH,
            {"product_id": str(product_id), "is_set_top": 1 if pinned else 0},
            action="storefront_pin",
        )

    def fetch_fund_balance(self) -> dict[str, Any]:
        return self._post_api(FUNDS_BALANCE_PATH, {})

    def fetch_funds_flows(
        self, start_time: int, end_time: int, page: int = 1, page_size: int = 50
    ) -> dict[str, Any]:
        return self._post_api(
            FUNDS_FLOW_LIST_PATH,
            {
                "page": page,
                "page_size": page_size,
                "start_time": int(start_time),
                "end_time": int(end_time),
            },
        )

    def set_product_listing(self, product_id: str, listed: bool) -> dict[str, Any]:
        path = PRODUCT_LISTING_PATH if listed else PRODUCT_DELISTING_PATH
        return self._post_write_api(
            path,
            {"product_id": str(product_id)},
            action="product_listing" if listed else "product_delisting",
        )

    def update_product_stock(
        self,
        product_id: str,
        sku_id: str,
        diff_type: int,
        amount: int,
    ) -> dict[str, Any]:
        if diff_type not in {1, 2, 3}:
            raise ValueError("库存修改类型必须是 1、2 或 3")
        return self._post_write_api(
            PRODUCT_STOCK_PATH,
            {
                "product_id": str(product_id),
                "sku_id": str(sku_id),
                "diff_type": diff_type,
                "num": int(amount),
            },
            action="product_stock_update",
        )

    def fetch_delivery_companies(self, ewaybill_only: bool = False) -> list[dict[str, Any]]:
        result = self._post_api(
            DELIVERY_COMPANY_LIST_PATH,
            {"ewaybill_only": ewaybill_only},
        )
        return [item for item in result.get("company_list", []) if isinstance(item, dict)]

    def fetch_ewaybill_delivery_companies(self, status: int | None = 3) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {}
        if status is not None:
            payload["status"] = status
        result = self._post_api(EWAYBILL_DELIVERY_PATH, payload)
        return [item for item in result.get("list", []) if isinstance(item, dict)]

    def fetch_ewaybill_accounts(
        self,
        delivery_id: str | None = None,
        need_balance: bool = True,
        page_size: int = 10,
    ) -> list[dict[str, Any]]:
        accounts: list[dict[str, Any]] = []
        offset = 0
        while True:
            payload: dict[str, Any] = {
                "need_balance": need_balance,
                "limit": max(1, min(page_size, 20)),
                "offset": offset,
            }
            if delivery_id:
                payload["delivery_id"] = delivery_id
            result = self._post_api(EWAYBILL_ACCOUNT_PATH, payload)
            page = [item for item in result.get("account_list", []) if isinstance(item, dict)]
            accounts.extend(page)
            total = int(result.get("total_num") or 0)
            offset += len(page)
            if not page or not total or offset >= total:
                break
        return accounts

    def fetch_ewaybill_templates(
        self,
        delivery_id: str | None = None,
    ) -> list[dict[str, Any]]:
        payload = {"delivery_id": delivery_id} if delivery_id else {}
        result = self._post_api(EWAYBILL_TEMPLATE_PATH, payload)
        templates: list[dict[str, Any]] = []
        for group in result.get("total_template", []):
            if not isinstance(group, dict):
                continue
            group_delivery_id = str(group.get("delivery_id") or delivery_id or "")
            default_id = str(group.get("default_template_id") or "")
            for template in group.get("template_list", []):
                if isinstance(template, dict):
                    item = dict(template)
                    item["delivery_id"] = group_delivery_id
                    item["is_default"] = bool(
                        template.get("is_default") or str(template.get("template_id")) == default_id
                    )
                    templates.append(item)
        return templates

    def precreate_ewaybill(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._post_write_api(
            EWAYBILL_PRECREATE_PATH,
            payload,
            action="ewaybill_precreate",
        )

    def create_ewaybill(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._post_write_api(
            EWAYBILL_CREATE_PATH,
            payload,
            action="ewaybill_create",
        )

    def fetch_ewaybill(self, ewaybill_order_id: str) -> dict[str, Any]:
        result = self._post_api(
            EWAYBILL_GET_PATH,
            {"ewaybill_order_id": str(ewaybill_order_id)},
        )
        return result.get("order_info") or {}

    def confirm_ewaybill_printed(
        self,
        ewaybill_order_id: str,
        delivery_id: str,
        waybill_id: str,
        reprint: bool = False,
    ) -> dict[str, Any]:
        return self._post_write_api(
            EWAYBILL_PRINT_PATH,
            {
                "ewaybill_order_id": str(ewaybill_order_id),
                "delivery_id": str(delivery_id),
                "waybill_id": str(waybill_id),
                "re_print": 1 if reprint else 0,
            },
            action="ewaybill_print_confirm",
        )

    def send_order_delivery(
        self,
        order_id: str,
        delivery_list: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return self._post_write_api(
            ORDER_DELIVERY_PATH,
            {
                "order_id": str(order_id),
                "delivery_list": delivery_list,
            },
            action="order_delivery_send",
        )

    def _post_api(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        query: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        token = self.get_access_token()
        params = {"access_token": token, **(query or {})}
        url = f"{API_ROOT}{path}?{urlencode(params)}"
        last_error: WechatApiError | None = None
        token_refreshed = False
        for attempt in range(3):
            self._wait_for_rate_limit()
            request_id = uuid.uuid4().hex
            started_at = datetime.now(UTC)
            started_perf = time.perf_counter()
            try:
                result = self.transport.post_json(url, payload)
            except Exception as exc:
                self._notify_api_call(
                    path=path,
                    method="POST",
                    attempt_no=attempt + 1,
                    request_id=request_id,
                    started_at=started_at,
                    started_perf=started_perf,
                    error=exc,
                )
                raise
            self._notify_api_call(
                path=path,
                method="POST",
                attempt_no=attempt + 1,
                request_id=request_id,
                started_at=started_at,
                started_perf=started_perf,
                result=result,
            )
            try:
                self._raise_for_error(result)
                return result
            except WechatApiError as exc:
                exc.request_id = request_id
                last_error = exc
                if exc.code in TOKEN_INVALID_CODES and not token_refreshed:
                    self._invalidate_access_token()
                    token = self.get_access_token()
                    url = f"{API_ROOT}{path}?access_token={token}"
                    token_refreshed = True
                    continue
                if exc.code not in {-1, 45011} or attempt == 2:
                    raise
                self.sleeper(1.5 * (attempt + 1))
        raise last_error or WechatApiError("接口调用失败")

    def _get_api(self, path: str) -> dict[str, Any]:
        token = self.get_access_token()
        url = f"{API_ROOT}{path}?access_token={token}"
        last_error: WechatApiError | None = None
        token_refreshed = False
        for attempt in range(3):
            self._wait_for_rate_limit()
            request_id = uuid.uuid4().hex
            started_at = datetime.now(UTC)
            started_perf = time.perf_counter()
            try:
                result = self.transport.get_json(url)
            except Exception as exc:
                self._notify_api_call(
                    path=path,
                    method="GET",
                    attempt_no=attempt + 1,
                    request_id=request_id,
                    started_at=started_at,
                    started_perf=started_perf,
                    error=exc,
                )
                raise
            self._notify_api_call(
                path=path,
                method="GET",
                attempt_no=attempt + 1,
                request_id=request_id,
                started_at=started_at,
                started_perf=started_perf,
                result=result,
            )
            try:
                self._raise_for_error(result)
                return result
            except WechatApiError as exc:
                exc.request_id = request_id
                last_error = exc
                if exc.code in TOKEN_INVALID_CODES and not token_refreshed:
                    self._invalidate_access_token()
                    token = self.get_access_token()
                    url = f"{API_ROOT}{path}?access_token={token}"
                    token_refreshed = True
                    continue
                if exc.code not in {-1, 45011} or attempt == 2:
                    raise
                self.sleeper(1.5 * (attempt + 1))
        raise last_error or WechatApiError("接口调用失败")

    def _wait_for_rate_limit(self) -> None:
        if self._min_request_interval <= 0:
            return
        with self._rate_lock:
            delay = self._next_request_at - time.time()
            if delay > 0:
                self.sleeper(delay)
            self._next_request_at = time.time() + self._min_request_interval

    @staticmethod
    def _raise_for_error(result: dict[str, Any]) -> None:
        code = int(result.get("errcode") or 0)
        if code == 0:
            return
        message = str(result.get("errmsg") or "微信接口返回错误")
        rid = str(result.get("rid") or "")
        if rid:
            message = f"{message}（rid: {rid}）"
        raise WechatApiError(message, code=code, rid=rid)


def parse_order(store: Store, payload: dict[str, Any]) -> Order:
    detail = payload.get("order_detail") or {}
    pay_info = detail.get("pay_info") or {}
    price_info = detail.get("price_info") or {}
    delivery_info = detail.get("delivery_info") or {}
    address_info = delivery_info.get("address_info") or {}
    ext_info = detail.get("ext_info") or {}
    deliveries = delivery_info.get("delivery_product_info") or []
    delivery = deliveries[0] if deliveries and isinstance(deliveries[0], dict) else {}

    items: list[OrderItem] = []
    for item in detail.get("product_infos") or []:
        if not isinstance(item, dict):
            continue
        attributes = item.get("sku_attrs") or []
        attr_text = "；".join(
            f"{attr.get('attr_key', '')}:{attr.get('attr_value', '')}"
            for attr in attributes
            if isinstance(attr, dict)
        )
        items.append(
            OrderItem(
                product_id=str(item.get("product_id") or ""),
                sku_id=str(item.get("sku_id") or ""),
                title=str(item.get("title") or ""),
                sku_count=_to_int(item.get("sku_cnt")),
                sale_price=_to_int(item.get("sale_price")),
                thumb_url=str(item.get("thumb_img") or ""),
                sku_attributes=attr_text,
            )
        )

    province = str(address_info.get("province_name") or "")
    city = str(address_info.get("city_name") or "")
    county = str(address_info.get("county_name") or "")
    detail_address = str(address_info.get("detail_info") or "")
    address = "".join(_ordered_unique([province, city, county, detail_address]))
    status = _to_int(payload.get("status"))
    total_amount = _to_int(price_info.get("order_price"))
    product_amount = _to_int(price_info.get("product_price"))
    freight = _to_int(price_info.get("freight"))
    discount = max(0, product_amount + freight - total_amount)
    return Order(
        store_id=store.id,
        store_name=store.name,
        order_id=str(payload.get("order_id") or ""),
        status=status,
        status_text=order_status_text(status),
        create_time=_to_int(payload.get("create_time")),
        update_time=_to_int(payload.get("update_time")),
        pay_time=_to_int(pay_info.get("pay_time")),
        total_amount=total_amount,
        product_amount=product_amount,
        freight_amount=freight,
        discount_amount=discount,
        buyer_name=str(address_info.get("user_name") or ""),
        phone=str(address_info.get("tel_number") or ""),
        address=address,
        waybill_id=str(delivery.get("waybill_id") or ""),
        carrier=str(delivery.get("delivery_id") or ""),
        merchant_notes=str(ext_info.get("merchant_notes") or ""),
        items=items,
        raw=payload,
    )


def parse_product(store: Store, payload: dict[str, Any]) -> Product:
    skus = [item for item in payload.get("skus") or [] if isinstance(item, dict)]
    status = _to_int(payload.get("status"))
    head_images = payload.get("head_imgs") or []
    return Product(
        store_id=store.id,
        store_name=store.name,
        product_id=str(payload.get("product_id") or ""),
        title=str(payload.get("title") or ""),
        status=status,
        status_text=product_status_text(status),
        edit_status=_to_int(payload.get("edit_status")),
        min_price=_to_int(payload.get("min_price")),
        sku_count=len(skus),
        stock_total=sum(_to_int(item.get("stock_num")) for item in skus),
        spu_code=str(payload.get("spu_code") or ""),
        out_product_id=str(payload.get("out_product_id") or ""),
        head_img=str(head_images[0]) if head_images else "",
        raw=payload,
    )


def parse_shipping_account(
    store: Store,
    payload: dict[str, Any],
    delivery_name: str,
) -> ShippingAccount:
    site_info = payload.get("site_info") or {}
    contact = site_info.get("contact") or {}
    sender_address = payload.get("sender_address") or {}
    return ShippingAccount(
        store_id=store.id,
        delivery_id=str(payload.get("delivery_id") or ""),
        delivery_name=delivery_name,
        acct_id=str(payload.get("acct_id") or ""),
        shop_id=str(payload.get("shop_id") or ""),
        site_code=str(site_info.get("site_code") or ""),
        site_name=str(site_info.get("site_name") or ""),
        status=_to_int(payload.get("status")),
        available=_to_int(payload.get("available")),
        monthly_card=str(payload.get("monthly_card") or ""),
        sender_name=str(contact.get("name") or ""),
        sender_mobile=str(contact.get("mobile") or contact.get("phone") or ""),
        sender_address={
            "province": str(sender_address.get("province") or ""),
            "city": str(sender_address.get("city") or ""),
            "county": str(sender_address.get("county") or ""),
            "street": str(sender_address.get("street") or ""),
            "address": str(sender_address.get("address") or ""),
        },
        raw=payload,
    )


def parse_shipping_template(
    store: Store,
    payload: dict[str, Any],
) -> ShippingTemplate:
    return ShippingTemplate(
        store_id=store.id,
        delivery_id=str(payload.get("delivery_id") or ""),
        template_id=str(payload.get("template_id") or ""),
        template_name=str(payload.get("template_name") or ""),
        template_type=str(payload.get("template_type") or "single"),
        is_default=bool(payload.get("is_default")),
        raw=payload,
    )


def _to_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _ordered_unique(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result


def timestamp(value: str, end_of_day: bool = False) -> int:
    parsed = datetime.strptime(value.strip(), "%Y-%m-%d")
    return int(parsed.timestamp()) + (86399 if end_of_day else 0)
