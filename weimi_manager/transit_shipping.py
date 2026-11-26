from __future__ import annotations

import copy
import json
import time
from typing import Any

import requests

from .db import Database
from .models import Store
from .web_session import cookie_header, normalize_cookies

STORE_WEB_ORIGIN = "https://store.weixin.qq.com"
SERVICE_LIST_PATH = "/shop-faas/mmecnodewaybill/logisticsservice/getServiceList"
SERVICE_STATUS_PATH = (
    "/shop-faas/mmecnodefreighttemplate/freight/cgi/"
    "getRemoteTransitServiceOpen"
)
SERVICE_OPEN_PATH = (
    "/shop-faas/mmecnodewaybill/logisticsservice/setServiceOpen"
)
TEMPLATE_LIST_PATH = (
    "/shop-faas/mmecnodefreighttemplate/freight/cgi/getFreightTemplate"
)
TEMPLATE_UPDATE_PATH = (
    "/shop-faas/mmecnodefreighttemplate/freight/cgi/updateFreightTemplate"
)
PRODUCT_SEARCH_PATH = (
    "/shop-faas/mmecnodewaybill/logisticsservice/searchProduct"
)
PRODUCT_TRANSIT_PATH = (
    "/shop-faas/mmecproductnode/batchEdit/cgi/batchEditExpressType"
)

XINJIANG = "新疆维吾尔自治区"
TIBET = "西藏自治区"
QINGHAI = "青海省"

DEFAULT_RULES: dict[str, Any] = {
    "direct_first_quantity": 1,
    "direct_first_price_cents": 0,
    "direct_second_quantity": 1,
    "direct_second_price_cents": 0,
    "special_first_quantity": 1,
    "special_first_price_cents": 1000,
    "special_second_quantity": 1,
    "special_second_price_cents": 800,
    "transit_first_quantity": 1,
    "transit_first_price_cents": 300,
    "transit_second_quantity": 1,
    "transit_second_price_cents": 100,
    "set_all_products_transit": True,
}


class TransitShippingError(RuntimeError):
    pass


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def normalize_rules(value: dict[str, Any] | None) -> dict[str, Any]:
    source = value if isinstance(value, dict) else {}
    result = dict(DEFAULT_RULES)
    for key in DEFAULT_RULES:
        if key == "set_all_products_transit":
            result[key] = bool(source.get(key, DEFAULT_RULES[key]))
        else:
            result[key] = max(0, _as_int(source.get(key), DEFAULT_RULES[key]))
    for key in (
        "direct_first_quantity",
        "direct_second_quantity",
        "special_first_quantity",
        "special_second_quantity",
        "transit_first_quantity",
        "transit_second_quantity",
    ):
        result[key] = max(1, result[key])
    return result


def _address(province: str) -> dict[str, Any]:
    return {
        "userName": "",
        "postalCode": "",
        "provinceName": province,
        "cityName": "",
        "countyName": "",
        "detailInfo": "",
        "nationalCode": "",
        "telNumber": "",
    }


def _province(address: dict[str, Any]) -> str:
    return str(address.get("provinceName") or "").strip()


def _calculation(
    *,
    first_quantity: int,
    first_price: int,
    second_quantity: int,
    second_price: int,
    is_default: bool,
    provinces: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "addressInfo": [_address(item) for item in (provinces or [])],
        "isDefault": bool(is_default),
        "firstValAmount": int(first_quantity),
        "firstPrice": int(first_price),
        "secondValAmount": int(second_quantity),
        "secondPrice": int(second_price),
    }


def apply_target_rules(
    template: dict[str, Any],
    rules: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = copy.deepcopy(template)
    config = normalize_rules(rules)
    payload["shippingMethod"] = 2
    payload["valuationType"] = 1
    payload["allConditionFreeDetail"] = {
        "conditionFreeDetailList": []
    }
    payload["allFreightCalcMethod"] = {
        "freightCalcMethodList": [
            _calculation(
                first_quantity=config["direct_first_quantity"],
                first_price=config["direct_first_price_cents"],
                second_quantity=config["direct_second_quantity"],
                second_price=config["direct_second_price_cents"],
                is_default=True,
            ),
            _calculation(
                first_quantity=config["special_first_quantity"],
                first_price=config["special_first_price_cents"],
                second_quantity=config["special_second_quantity"],
                second_price=config["special_second_price_cents"],
                is_default=False,
                provinces=[TIBET, QINGHAI],
            ),
        ]
    }
    not_send = payload.get("notSendArea") or {}
    addresses = not_send.get("addressInfo") or []
    payload["notSendArea"] = {
        "addressInfo": [
            item
            for item in addresses
            if isinstance(item, dict) and _province(item) != XINJIANG
        ]
    }
    payload["consolidateFlag"] = 1
    payload["hasRemoteAreaService"] = True
    payload["consolidateConfig"] = {
        "allFreightCalcMethod": {
            "freightCalcMethodList": [
                _calculation(
                    first_quantity=config["transit_first_quantity"],
                    first_price=config["transit_first_price_cents"],
                    second_quantity=config["transit_second_quantity"],
                    second_price=config["transit_second_price_cents"],
                    is_default=True,
                )
            ]
        },
        "allConditionFreeDetail": {
            "conditionFreeDetailList": []
        },
    }
    payload.pop("relatedProductsNum", None)
    payload.pop("canBeDeleted", None)
    payload.pop("complete", None)
    payload["isDefault"] = _as_int(payload.get("isDefault"))
    return payload


class TransitShippingManager:
    def __init__(self, db: Database):
        self.db = db

    def _headers(
        self,
        store: Store,
        *,
        referer_path: str,
    ) -> dict[str, str]:
        payload = self.db.get_web_cookies(store.id)
        cookies = normalize_cookies((payload or {}).get("cookies") or [])
        if not cookies:
            raise TransitShippingError("店铺网页登录会话不存在，请先登录")
        magic = next(
            (
                str(cookie.get("value") or "")
                for cookie in cookies
                if cookie.get("name") == "biz_magic"
            ),
            "",
        )
        if not magic:
            raise TransitShippingError("店铺网页会话缺少平台校验信息，请重新登录")
        return {
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "Cookie": cookie_header(cookies),
            "Referer": f"{STORE_WEB_ORIGIN}{referer_path}",
            "Origin": STORE_WEB_ORIGIN,
            "User-Agent": "Mozilla/5.0",
            "biz_magic": magic,
            "potter-scene": "weixinShop",
        }

    def _request(
        self,
        store: Store,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        referer_path: str,
    ) -> dict[str, Any]:
        session = requests.Session()
        session.trust_env = False
        try:
            response = session.request(
                method,
                f"{STORE_WEB_ORIGIN}{path}",
                headers=self._headers(
                    store,
                    referer_path=referer_path,
                ),
                json=payload,
                timeout=(8, 35),
            )
        except requests.RequestException as exc:
            raise TransitShippingError(
                f"微信小店后台请求失败：{exc}"
            ) from exc
        if response.status_code != 200:
            raise TransitShippingError(
                f"微信小店后台返回 HTTP {response.status_code}"
            )
        try:
            data = response.json()
        except ValueError as exc:
            raise TransitShippingError(
                "微信小店后台返回内容无法解析"
            ) from exc
        if not isinstance(data, dict):
            raise TransitShippingError("微信小店后台返回格式异常")
        if _as_int(data.get("code"), 0) != 0:
            raise TransitShippingError(
                str(data.get("errmsg") or data.get("msg") or "平台操作失败")
            )
        return data

    def service_status(self, store: Store) -> dict[str, Any]:
        data = self._request(
            store,
            "GET",
            f"{SERVICE_LIST_PATH}?token=&lang=zh_CN",
            referer_path="/shop/logisticsservice/home",
        )
        service = next(
            (
                dict(item)
                for item in data.get("list") or []
                if _as_int(item.get("key")) == 1
            ),
            {},
        )
        return {
            "open": bool(service.get("open")),
            "openable": bool(service.get("openable")),
            "grayed_out": bool(service.get("grayedOut")),
            "reason": str(
                service.get("grayedOutText")
                or service.get("closeReason")
                or ""
            ),
            "service": service,
        }

    def open_service(self, store: Store) -> dict[str, Any]:
        status = self.service_status(store)
        if status["open"]:
            return status
        if not status["openable"] or status["grayed_out"]:
            raise TransitShippingError(
                status["reason"] or "当前店铺暂不满足开通条件"
            )
        self._request(
            store,
            "POST",
            f"{SERVICE_OPEN_PATH}?token=&lang=zh_CN",
            payload={"serviceType": 1, "operatorSource": 1},
            referer_path="/shop/logisticsservice/home?openRemoteArea=1",
        )
        time.sleep(0.8)
        current = self.service_status(store)
        if not current["open"]:
            raise TransitShippingError(
                "平台未回读为已开通，请到物流服务页人工核对"
            )
        return current

    def templates(self, store: Store) -> list[dict[str, Any]]:
        data = self._request(
            store,
            "GET",
            f"{TEMPLATE_LIST_PATH}?token=&lang=zh_CN&offset=0&limit=52",
            referer_path="/shop/address/deliveryMgmt",
        )
        return [
            dict(item)
            for item in data.get("freightTemplateList") or []
            if isinstance(item, dict)
        ]

    def update_template(
        self,
        store: Store,
        template: dict[str, Any],
        rules: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = apply_target_rules(template, rules)
        self._request(
            store,
            "POST",
            f"{TEMPLATE_UPDATE_PATH}?token=&lang=zh_CN",
            payload={
                "expressData": json.dumps(
                    payload,
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            },
            referer_path="/shop/address/deliveryMgmt",
        )
        time.sleep(0.5)
        refreshed = next(
            (
                item
                for item in self.templates(store)
                if str(item.get("templateId"))
                == str(template.get("templateId"))
            ),
            {},
        )
        if not refreshed:
            raise TransitShippingError("模板更新后未在官方列表回读")
        return refreshed

    def products(
        self,
        store: Store,
        *,
        transit_type: int | None = None,
    ) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        page = 1
        while True:
            payload: dict[str, Any] = {"page": page, "pageSize": 50}
            if transit_type is not None:
                payload["transitType"] = transit_type
            data = self._request(
                store,
                "POST",
                f"{PRODUCT_SEARCH_PATH}?token=&lang=zh_CN",
                payload=payload,
                referer_path="/shop/logisticsservice/remoteArea/home",
            )
            rows = [
                dict(item)
                for item in data.get("list") or []
                if isinstance(item, dict)
            ]
            result.extend(rows)
            if len(rows) < 50:
                break
            page += 1
            if page > 100:
                break
        return result

    def set_products_transit(
        self,
        store: Store,
        rows: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        eligible = [
            item
            for item in rows
            if bool(item.get("canTransit"))
            and _as_int(item.get("transitType")) != 1
        ]
        if not eligible:
            return []
        products = [
            {
                "productId": str(item.get("id") or ""),
                "shouldForceUpdateExpressType": False,
                "expressInfo": {
                    "templateId": str(item.get("templateId") or ""),
                    "expressType": 0,
                    "weight": _as_int(item.get("weight")),
                },
            }
            for item in eligible
            if str(item.get("id") or "").strip()
        ]
        results: list[Any] = []
        for offset in range(0, len(products), 50):
            data = self._request(
                store,
                "POST",
                f"{PRODUCT_TRANSIT_PATH}?token=&lang=zh_CN",
                payload={
                    "products": products[offset : offset + 50],
                    "uploadSource": 6,
                    "operType": 17,
                },
                referer_path="/shop/logisticsservice/remoteArea/home",
            )
            batch_results = data.get("data")
            if isinstance(batch_results, list):
                results.extend(batch_results)
        return results

    def overview(self, store: Store) -> dict[str, Any]:
        templates = self.templates(store)
        products = self.products(store)
        return {
            "service": self.service_status(store),
            "templates": templates,
            "products": products,
            "linked_template_count": sum(
                1
                for item in templates
                if _as_int(item.get("relatedProductsNum")) > 0
            ),
            "transit_product_count": sum(
                1
                for item in products
                if _as_int(item.get("transitType")) == 1
            ),
        }


__all__ = [
    "DEFAULT_RULES",
    "TransitShippingError",
    "TransitShippingManager",
    "apply_target_rules",
    "normalize_rules",
]
