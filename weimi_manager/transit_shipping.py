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
MANUAL_SERVICE_URL = (
    f"{STORE_WEB_ORIGIN}/shop/logisticsservice/home"
)
MANUAL_PRODUCT_URL = (
    f"{STORE_WEB_ORIGIN}/shop/logisticsservice/remoteArea/home"
)
MANUAL_TEMPLATE_URL = (
    f"{STORE_WEB_ORIGIN}/shop/address/deliveryMgmt"
)
TRANSIT_BACKUP_PREFIX = "transit_execution_backup"
MAX_OFFICIAL_PAGES = 100

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


class TransitTemplateMismatchError(TransitShippingError):
    def __init__(
        self,
        message: str,
        *,
        differences: list[str],
        official: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.differences = differences
        self.official = official or {}


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


def _canonical_address(value: Any) -> tuple[str, str, str]:
    if not isinstance(value, dict):
        return ("", "", "")
    return (
        str(value.get("provinceName") or "").strip(),
        str(value.get("cityName") or "").strip(),
        str(value.get("countyName") or "").strip(),
    )


def _canonical_calculations(value: Any) -> list[dict[str, Any]]:
    rows = (
        value.get("freightCalcMethodList")
        if isinstance(value, dict)
        else []
    )
    normalized = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        normalized.append(
            {
                "addresses": tuple(
                    sorted(
                        _canonical_address(item)
                        for item in row.get("addressInfo") or []
                    )
                ),
                "isDefault": bool(row.get("isDefault")),
                "firstValAmount": _as_int(row.get("firstValAmount")),
                "firstPrice": _as_int(row.get("firstPrice")),
                "secondValAmount": _as_int(row.get("secondValAmount")),
                "secondPrice": _as_int(row.get("secondPrice")),
            }
        )
    return sorted(
        normalized,
        key=lambda item: (
            not item["isDefault"],
            item["addresses"],
            item["firstValAmount"],
            item["firstPrice"],
            item["secondValAmount"],
            item["secondPrice"],
        ),
    )


def _canonical_address_area(value: Any) -> list[tuple[str, str, str]]:
    if isinstance(value, dict):
        rows = value.get("addressInfo") or []
    elif isinstance(value, list):
        rows = value
    else:
        rows = []
    return sorted(
        _canonical_address(item)
        for item in rows
        if isinstance(item, dict)
    )


def _remove_xinjiang(value: Any) -> Any:
    if isinstance(value, dict):
        result = copy.deepcopy(value)
        if isinstance(result.get("addressInfo"), list):
            result["addressInfo"] = [
                item
                for item in result["addressInfo"]
                if (
                    not isinstance(item, dict)
                    or _province(item) != XINJIANG
                )
            ]
        return result
    if isinstance(value, list):
        return [
            item
            for item in value
            if (
                not isinstance(item, dict)
                or _province(item) != XINJIANG
            )
        ]
    return value


def template_differences(
    actual: dict[str, Any],
    expected: dict[str, Any],
) -> list[str]:
    differences: list[str] = []

    checks = (
        (
            "shippingMethod",
            expected.get("shippingMethod"),
            actual.get("shippingMethod"),
        ),
        (
            "consolidateFlag",
            _as_int(expected.get("consolidateFlag")),
            _as_int(actual.get("consolidateFlag")),
        ),
        (
            "hasRemoteAreaService",
            bool(expected.get("hasRemoteAreaService")),
            bool(actual.get("hasRemoteAreaService")),
        ),
        (
            "直邮运费 firstPrice/secondPrice",
            _canonical_calculations(
                expected.get("allFreightCalcMethod")
            ),
            _canonical_calculations(
                actual.get("allFreightCalcMethod")
            ),
        ),
        (
            "allConditionFreeDetail",
            (
                expected.get("allConditionFreeDetail") or {}
            ).get("conditionFreeDetailList") or [],
            (
                actual.get("allConditionFreeDetail") or {}
            ).get("conditionFreeDetailList") or [],
        ),
        (
            "中转运费 consolidateConfig",
            _canonical_calculations(
                (
                    expected.get("consolidateConfig") or {}
                ).get("allFreightCalcMethod")
            ),
            _canonical_calculations(
                (
                    actual.get("consolidateConfig") or {}
                ).get("allFreightCalcMethod")
            ),
        ),
        (
            "consolidateConfig.allConditionFreeDetail",
            (
                (
                    expected.get("consolidateConfig") or {}
                ).get("allConditionFreeDetail")
                or {}
            ).get("conditionFreeDetailList") or [],
            (
                (
                    actual.get("consolidateConfig") or {}
                ).get("allConditionFreeDetail")
                or {}
            ).get("conditionFreeDetailList") or [],
        ),
        (
            "notSendArea",
            _canonical_address_area(expected.get("notSendArea")),
            _canonical_address_area(actual.get("notSendArea")),
        ),
    )
    for label, expected_value, actual_value in checks:
        if actual_value != expected_value:
            differences.append(
                f"{label}：官方={actual_value!r}，目标={expected_value!r}"
            )

    if "notDeliver" in expected or "notDeliver" in actual:
        expected_not_deliver = _canonical_address_area(
            expected.get("notDeliver")
        )
        actual_not_deliver = _canonical_address_area(
            actual.get("notDeliver")
        )
        if actual_not_deliver != expected_not_deliver:
            differences.append(
                "notDeliver："
                f"官方={actual_not_deliver!r}，"
                f"目标={expected_not_deliver!r}"
            )
    return differences


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
    for field in ("notSendArea", "notDeliver"):
        if field not in payload:
            continue
        source_area = payload.get(field)
        cleaned = _remove_xinjiang(source_area)
        if isinstance(cleaned, dict):
            cleaned.setdefault("addressInfo", [])
        elif not isinstance(cleaned, list):
            cleaned = {"addressInfo": []}
        payload[field] = cleaned
    if "notSendArea" not in payload:
        payload["notSendArea"] = {"addressInfo": []}
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
        result: dict[str, dict[str, Any]] = {}
        offset = 0
        limit = 50
        for _page in range(MAX_OFFICIAL_PAGES):
            data = self._request(
                store,
                "GET",
                (
                    f"{TEMPLATE_LIST_PATH}?token=&lang=zh_CN"
                    f"&offset={offset}&limit={limit}"
                ),
                referer_path="/shop/address/deliveryMgmt",
            )
            rows = [
                dict(item)
                for item in data.get("freightTemplateList") or []
                if isinstance(item, dict)
            ]
            new_rows = 0
            for row in rows:
                template_id = str(row.get("templateId") or "")
                if template_id and template_id not in result:
                    result[template_id] = row
                    new_rows += 1
            total = _as_int(
                data.get("total")
                or data.get("totalCount")
                or data.get("count"),
                -1,
            )
            if total >= 0 and len(result) >= total:
                break
            if total < 0 and len(rows) < limit:
                break
            if not new_rows:
                if total >= 0 and len(result) < total:
                    raise TransitShippingError(
                        "官方运费模板分页没有返回新数据，"
                        "无法确认已读取完整目录"
                    )
                break
            offset += limit
        else:
            raise TransitShippingError(
                "官方运费模板超过分页安全上限，"
                "未执行任何模板修改"
            )
        return list(result.values())

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
        differences = template_differences(refreshed, payload)
        if differences:
            raise TransitTemplateMismatchError(
                "模板保存后官方关键字段回读不一致",
                differences=differences,
                official=refreshed,
            )
        return refreshed

    def products(
        self,
        store: Store,
        *,
        transit_type: int | None = None,
    ) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        page = 1
        while page <= MAX_OFFICIAL_PAGES:
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
        if page > MAX_OFFICIAL_PAGES:
            raise TransitShippingError(
                "官方商品目录超过分页安全上限，"
                "无法确认已读取完整目录"
            )
        return result

    def set_products_transit(
        self,
        store: Store,
        rows: list[dict[str, Any]],
    ) -> dict[str, Any]:
        eligible = [
            item
            for item in rows
            if bool(item.get("canTransit"))
            and _as_int(item.get("transitType")) != 1
        ]
        if not eligible:
            return {
                "requested": 0,
                "already_transit": len(rows),
                "batch_count": 0,
                "items": [],
                "errors": [],
                "item_failures": [],
            }
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
        errors: list[dict[str, Any]] = []
        item_failures: list[dict[str, str]] = []
        batch_count = 0
        for offset in range(0, len(products), 50):
            batch_count += 1
            batch = products[offset : offset + 50]
            try:
                data = self._request(
                    store,
                    "POST",
                    f"{PRODUCT_TRANSIT_PATH}?token=&lang=zh_CN",
                    payload={
                        "products": batch,
                        "uploadSource": 6,
                        "operType": 17,
                    },
                    referer_path="/shop/logisticsservice/remoteArea/home",
                )
            except TransitShippingError as exc:
                errors.append(
                    {
                        "batch": batch_count,
                        "count": len(batch),
                        "reason": str(exc),
                    }
                )
                break
            batch_results = data.get("data")
            if isinstance(batch_results, list):
                results.extend(batch_results)
                for item in batch_results:
                    if not isinstance(item, dict):
                        continue
                    failed = (
                        item.get("success") is False
                        or _as_int(item.get("errcode")) != 0
                        or _as_int(item.get("ret")) not in (0, 1)
                        or _as_int(item.get("result")) not in (0, 1)
                        or str(item.get("status") or "").casefold()
                        in {"fail", "failed", "error"}
                    )
                    if failed:
                        product_id = str(
                            item.get("productId")
                            or item.get("id")
                            or ""
                        )
                        item_failures.append(
                            {
                                "product_id": product_id,
                                "reason": str(
                                    item.get("errmsg")
                                    or item.get("message")
                                    or item.get("msg")
                                    or "官方批次返回单项失败"
                                )[:500],
                            }
                        )
        return {
            "requested": len(products),
            "already_transit": len(rows) - len(eligible),
            "batch_count": batch_count,
            "items": results,
            "errors": errors,
            "item_failures": item_failures,
        }

    def run_continuous_flow(
        self,
        store: Store,
        *,
        template_ids: list[str] | tuple[str, ...] | None = None,
        rules: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        config = normalize_rules(rules)
        requested_ids = [
            str(value).strip()
            for value in template_ids or []
            if str(value).strip()
        ]
        steps = [
            {
                "key": "service",
                "title": "服务开通",
                "status": "pending",
                "detail": "",
            },
            {
                "key": "products",
                "title": "商品切换",
                "status": "pending",
                "detail": "",
            },
            {
                "key": "templates",
                "title": "模板更新",
                "status": "pending",
                "detail": "",
            },
            {
                "key": "verification",
                "title": "最终回读",
                "status": "pending",
                "detail": "",
            },
        ]
        result: dict[str, Any] = {
            "success": False,
            "message": "",
            "failed_stage": "",
            "errors": [],
            "steps": steps,
            "completed_steps": [],
            "product_failures": [],
            "template_failures": [],
            "backup_key": "",
            "product_summary": {},
            "template_summary": {},
            "aggregate": {
                "service_open": False,
                "products_ok": False,
                "templates_ok": False,
            },
            "manual_guidance": [],
        }

        def set_step(
            key: str,
            status: str,
            detail: str,
        ) -> None:
            for step in steps:
                if step["key"] == key:
                    step["status"] = status
                    step["detail"] = detail
                    return

        def completed() -> list[str]:
            return [
                step["title"]
                for step in steps
                if step["status"] == "success"
            ]

        def finish_failed(
            stage: str,
            message: str,
            guidance: list[str],
        ) -> dict[str, Any]:
            result["failed_stage"] = stage
            result["errors"].append(message)
            result["completed_steps"] = completed()
            result["manual_guidance"] = guidance
            for step in steps:
                if step["status"] == "pending":
                    step["status"] = "skipped"
            return result

        set_step("service", "running", "正在读取官方快照")
        try:
            initial_service = self.service_status(store)
            initial_products = self.products(store)
            initial_templates = self.templates(store)
        except Exception as exc:
            set_step("service", "failed", str(exc)[:500])
            return finish_failed(
                "service",
                f"官方预检快照读取失败：{exc}",
                [f"登录店铺后台核对中转服务：{MANUAL_SERVICE_URL}"],
            )

        linked_templates = {
            str(template.get("templateId") or ""): template
            for template in initial_templates
            if _as_int(template.get("relatedProductsNum")) > 0
        }
        selected_ids = (
            requested_ids
            if requested_ids
            else list(linked_templates)
        )
        unlinked_selected = [
            value for value in selected_ids
            if value not in linked_templates
        ]
        selected_templates = [
            linked_templates[value]
            for value in selected_ids
            if value in linked_templates
        ]
        if not selected_templates or unlinked_selected:
            detail = (
                "未选择任何已关联商品的官方模板"
                if not selected_templates
                else "以下模板未关联当前店铺商品，不允许修改："
                + "、".join(unlinked_selected)
            )
            set_step("templates", "failed", detail)
            return finish_failed(
                "templates",
                detail,
                [f"在运费模板页核对模板关联：{MANUAL_TEMPLATE_URL}"],
            )

        run_id = (
            time.strftime("%Y%m%d%H%M%S", time.localtime())
            + f"_{time.time_ns() % 1_000_000:06d}"
        )
        snapshot = {
            "version": 1,
            "run_id": run_id,
            "store_id": int(store.id),
            "store_name": store.name,
            "created_at": time.strftime(
                "%Y-%m-%d %H:%M:%S",
                time.localtime(),
            ),
            "rules": config,
            "selected_template_ids": selected_ids,
            "service": initial_service,
            "templates": initial_templates,
            "products": initial_products,
        }
        snapshot_json = json.dumps(
            snapshot,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        result["backup_key"] = (
            f"{TRANSIT_BACKUP_PREFIX}_{store.id}_{run_id}"
        )
        self.db.set_setting(result["backup_key"], snapshot_json)
        self.db.set_setting(
            f"{TRANSIT_BACKUP_PREFIX}_latest_{store.id}",
            snapshot_json,
        )

        try:
            service = (
                initial_service
                if initial_service["open"]
                else self.open_service(store)
            )
            if not service.get("open"):
                set_step(
                    "service",
                    "failed",
                    "官方回读 open=false",
                )
                return finish_failed(
                    "service",
                    "偏远地区中转服务未开通，流程已停止",
                    [f"到物流服务页人工核对：{MANUAL_SERVICE_URL}"],
                )
            set_step(
                "service",
                "success",
                "官方回读 open=true",
            )
        except Exception as exc:
            set_step("service", "failed", str(exc)[:500])
            return finish_failed(
                "service",
                f"服务开通失败：{exc}",
                [f"到物流服务页人工核对：{MANUAL_SERVICE_URL}"],
            )

        set_step("products", "running", "正在批量切换全部可中转商品")
        eligible_ids = {
            str(row.get("id") or "")
            for row in initial_products
            if bool(row.get("canTransit"))
        }
        product_write_error = ""
        product_write: dict[str, Any] = {}
        try:
            product_write = self.set_products_transit(
                store,
                initial_products,
            )
        except Exception as exc:
            product_write_error = str(exc)
        try:
            product_readback = self.products(store)
        except Exception as exc:
            product_readback = []
            product_write_error = (
                f"{product_write_error}；{exc}"
                if product_write_error
                else str(exc)
            )
        result["product_summary"] = {
            "eligible": len(eligible_ids),
            "requested": _as_int(product_write.get("requested")),
            "already_transit": _as_int(
                product_write.get("already_transit")
            ),
            "batch_count": _as_int(
                product_write.get("batch_count")
            ),
            "batch_errors": product_write.get("errors", []),
        }
        readback_by_id = {
            str(row.get("id") or ""): row
            for row in product_readback
        }
        item_failures = {
            str(item.get("product_id") or ""): str(
                item.get("reason") or ""
            )
            for item in product_write.get("item_failures", [])
        }
        batch_errors = "；".join(
            (
                f"批次 {item.get('batch')}（{item.get('count')} 件）："
                f"{item.get('reason')}"
            )
            for item in product_write.get("errors", [])
            if isinstance(item, dict)
        )
        for product_id in sorted(eligible_ids):
            product = readback_by_id.get(product_id)
            if not product:
                reason = (
                    "官方完整商品目录回读中缺少该商品"
                    + (
                        f"；写入错误：{product_write_error}"
                        if product_write_error
                        else ""
                    )
                )
                if batch_errors:
                    reason += f"；{batch_errors}"
            elif _as_int(product.get("transitType")) != 1:
                reason = (
                    f"官方回读 transitType="
                    f"{_as_int(product.get('transitType'))}，"
                    "未切换为中转"
                )
                if item_failures.get(product_id):
                    reason += f"；{item_failures[product_id]}"
                if batch_errors:
                    reason += f"；{batch_errors}"
                if product_write_error:
                    reason += f"；写入错误：{product_write_error}"
            else:
                continue
            source = readback_by_id.get(product_id) or next(
                (
                    row
                    for row in initial_products
                    if str(row.get("id") or "") == product_id
                ),
                {},
            )
            result["product_failures"].append(
                {
                    "product_id": product_id,
                    "name": str(source.get("name") or ""),
                    "reason": reason,
                    "manual_url": MANUAL_PRODUCT_URL,
                }
            )
        if result["product_failures"]:
            set_step(
                "products",
                "failed",
                f"{len(result['product_failures'])} 件商品未回读为 transitType=1",
            )
            return finish_failed(
                "products",
                "商品切换未全部通过官方逐件回读，模板步骤未执行",
                [
                    f"逐件处理失败商品：{MANUAL_PRODUCT_URL}",
                    "处理完成后重新执行一次连续流程",
                ],
            )
        set_step(
            "products",
            "success",
            (
                f"官方完整目录 {len(product_readback)} 件，"
                f"全部可中转商品 transitType=1"
            ),
        )

        set_step("templates", "running", "正在更新已关联商品的模板")
        template_results: list[dict[str, Any]] = []
        for template in selected_templates:
            template_id = str(template.get("templateId") or "")
            expected = apply_target_rules(template, config)
            existing_differences = template_differences(
                template,
                expected,
            )
            if not existing_differences:
                template_results.append(
                    {
                        "template_id": template_id,
                        "name": str(template.get("name") or ""),
                        "status": "unchanged",
                    }
                )
                continue
            try:
                updated = self.update_template(
                    store,
                    template,
                    config,
                )
                differences = template_differences(updated, expected)
                if differences:
                    raise TransitTemplateMismatchError(
                        "模板保存后官方关键字段回读不一致",
                        differences=differences,
                        official=updated,
                    )
                template_results.append(
                    {
                        "template_id": template_id,
                        "name": str(updated.get("name") or ""),
                        "status": "updated",
                    }
                )
            except Exception as exc:
                differences = list(
                    getattr(exc, "differences", [])
                    or []
                )
                official = getattr(exc, "official", {}) or {}
                failure = {
                    "template_id": template_id,
                    "name": str(template.get("name") or ""),
                    "status": "failed",
                    "error": str(exc)[:500],
                    "differences": differences,
                    "official": official,
                }
                result["template_failures"].append(failure)
                template_results.append(failure)
                break
        result["template_summary"] = {
            "selected": len(selected_ids),
            "updated": sum(
                row["status"] == "updated"
                for row in template_results
            ),
            "unchanged": sum(
                row["status"] == "unchanged"
                for row in template_results
            ),
            "failed": len(result["template_failures"]),
            "results": template_results,
        }
        if result["template_failures"]:
            set_step(
                "templates",
                "failed",
                "模板关键字段未通过官方回读",
            )
            return finish_failed(
                "templates",
                "模板更新失败，最终聚合回读未执行",
                [
                    f"按差异人工核对模板：{MANUAL_TEMPLATE_URL}",
                    "执行前模板快照已保存在本机，可用于核对或回退",
                ],
            )
        set_step(
            "templates",
            "success",
            (
                f"已严格回读 {len(selected_templates)} 个"
                "已关联商品模板"
            ),
        )

        set_step("verification", "running", "正在执行三项官方聚合回读")
        try:
            final_service = self.service_status(store)
            final_products = self.products(store)
            final_templates = self.templates(store)
        except Exception as exc:
            set_step("verification", "failed", str(exc)[:500])
            return finish_failed(
                "verification",
                f"最终聚合回读失败：{exc}",
                ["保持人工核对，不按成功处理"],
            )
        final_product_by_id = {
            str(row.get("id") or ""): row
            for row in final_products
        }
        final_eligible_ids = {
            str(row.get("id") or "")
            for row in final_products
            if bool(row.get("canTransit"))
        }
        final_product_failures = [
            product_id
            for product_id in sorted(eligible_ids | final_eligible_ids)
            if (
                product_id not in final_product_by_id
                or _as_int(
                    final_product_by_id[product_id].get("transitType")
                )
                != 1
            )
        ]
        final_template_by_id = {
            str(row.get("templateId") or ""): row
            for row in final_templates
        }
        final_template_failures: list[dict[str, Any]] = []
        for template in selected_templates:
            template_id = str(template.get("templateId") or "")
            actual = final_template_by_id.get(template_id)
            if not actual:
                final_template_failures.append(
                    {
                        "template_id": template_id,
                        "differences": ["最终官方模板目录中不存在该模板"],
                    }
                )
                continue
            differences = template_differences(
                actual,
                apply_target_rules(template, config),
            )
            if differences:
                final_template_failures.append(
                    {
                        "template_id": template_id,
                        "differences": differences,
                    }
                )
        result["aggregate"] = {
            "service_open": bool(final_service.get("open")),
            "products_ok": not final_product_failures,
            "templates_ok": not final_template_failures,
        }
        if all(result["aggregate"].values()):
            set_step(
                "verification",
                "success",
                "服务、全部可中转商品、所选模板三项一致",
            )
            result["success"] = True
            result["message"] = "全店中转连续流程完成"
            result["completed_steps"] = completed()
            return result
        if final_product_failures:
            result["product_failures"] = [
                {
                    "product_id": product_id,
                    "name": str(
                        (
                            final_product_by_id.get(product_id)
                            or next(
                                (
                                    row
                                    for row in initial_products
                                    if str(row.get("id") or "")
                                    == product_id
                                ),
                                {},
                            )
                        ).get("name")
                        or ""
                    ),
                    "reason": "最终官方聚合回读未确认 transitType=1",
                    "manual_url": MANUAL_PRODUCT_URL,
                }
                for product_id in final_product_failures
            ]
        result["template_failures"] = final_template_failures
        set_step(
            "verification",
            "failed",
            str(result["aggregate"]),
        )
        return finish_failed(
            "verification",
            "最终聚合回读未全部通过，不能标记成功",
            [
                f"商品人工入口：{MANUAL_PRODUCT_URL}",
                f"模板人工入口：{MANUAL_TEMPLATE_URL}",
                "保留本机执行前快照并按回读差异核对",
            ],
        )

    def overview(self, store: Store) -> dict[str, Any]:
        service = self.service_status(store)
        products = self.products(store)
        templates = self.templates(store)
        return {
            "service": service,
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
