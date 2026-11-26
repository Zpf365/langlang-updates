from __future__ import annotations

import base64
import hashlib
import json
import queue
import re
import shutil
import threading
import time
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, unquote, urlparse

import requests

from .browser_runtime import browser_launch_options
from .db import Database
from .web_session import normalize_cookies

PLATFORM_LOGIN_URLS = {
    "1688": "https://login.1688.com/member/signin.htm",
    "taobao": "https://login.taobao.com/member/login.jhtml",
    "pinduoduo": "https://mobile.yangkeduo.com/login.html",
    "jd": "https://passport.jd.com/new/login.aspx",
}

PLATFORM_KEYS = {
    "1688": "1688",
    "淘宝": "taobao",
    "taobao": "taobao",
    "tmall": "taobao",
    "天猫": "taobao",
    "拼多多": "pinduoduo",
    "pinduoduo": "pinduoduo",
    "京东": "jd",
    "jd": "jd",
}

PLATFORM_HOME_URLS = {
    "1688": "https://work.1688.com/",
    "taobao": "https://i.taobao.com/my_taobao.htm",
    "pinduoduo": "https://mobile.yangkeduo.com/personal.html",
    "jd": "https://home.jd.com/",
}

PLATFORM_ORDER_URLS = {
    "1688": "https://trade.1688.com/order/buyer_order_list.htm",
    "taobao": "https://buyertrade.taobao.com/trade/itemlist/list_bought_items.htm",
    "pinduoduo": "https://mobile.yangkeduo.com/orders.html",
    "jd": "https://order.jd.com/center/list.action",
}

MTOP_HOSTS = {
    "1688": "https://h5api.m.1688.com",
    "taobao": "https://h5api.m.taobao.com",
}

PLATFORM_COOKIE_DOMAINS = {
    "1688": ".1688.com",
    "taobao": ".taobao.com",
    "pinduoduo": ".yangkeduo.com",
    "jd": ".jd.com",
}


def _default_cookie_domain(platform: str) -> str:
    return PLATFORM_COOKIE_DOMAINS.get(
        canonical_platform(platform),
        "",
    )


def _wait_for_page_close(page: Any) -> None:
    """Wait for the page the user is viewing to close."""

    if page.is_closed():
        return
    try:
        page.wait_for_event("close", timeout=0)
    except Exception as exc:
        if "closed" not in str(exc).casefold():
            raise


def canonical_platform(platform: str) -> str:
    value = str(platform or "").strip()
    key = PLATFORM_KEYS.get(value, PLATFORM_KEYS.get(value.casefold(), ""))
    if not key:
        raise ProcurementError(f"不支持的采购平台：{platform}")
    return key


def procurement_profile_path(
    data_dir: Path,
    account: dict[str, Any],
) -> Path:
    """Resolve one browser profile directory for one procurement account."""

    custom = str(account.get("profile_dir") or "").strip()
    if custom:
        return Path(custom).expanduser().resolve()
    account_id = int(account["id"])
    return (
        Path(data_dir).expanduser().resolve()
        / "procurement_profiles"
        / f"account_{account_id}"
    )


def procurement_login_identity(
    platform: str,
    cookies: list[dict[str, Any]],
) -> str:
    key = canonical_platform(platform)
    names = (
        ("tracknick", "_nk_", "lgc")
        if key == "taobao"
        else (
            "tracknick",
            "_nk_",
            "lgc",
            "__cn_logon_id__",
            "__cn_logon__",
        )
    )
    for name in names:
        value = cookie_value(cookies, name).strip().strip('"')
        if not value:
            continue
        decoded = unquote(value)
        if decoded and decoded.casefold() not in {
            "undefined",
            "null",
            "true",
            "false",
        }:
            return decoded
    return ""


def procurement_page_identity(
    page: Any,
    expected_identity: str,
) -> str:
    """Read a visible official identity without accepting a bare flag."""
    expected = str(expected_identity or "").strip()
    try:
        value = page.evaluate(
            r"""
            (expected) => {
              const wanted = String(expected || '').trim().toLowerCase();
              const selectors = [
                '[data-nick]', '[data-nickname]', '[data-account]',
                '[class*="nickname"]', '[class*="user-name"]',
                '[class*="userName"]', '[class*="account-name"]',
                '[class*="accountName"]', '.site-nav-user',
                '.site-nav-login-info-nick', '#mtb-nickname',
              ];
              const values = [];
              for (const selector of selectors) {
                for (const node of document.querySelectorAll(selector)) {
                  for (const value of [
                    node.textContent,
                    node.getAttribute('data-nick'),
                    node.getAttribute('data-nickname'),
                    node.getAttribute('data-account'),
                    node.getAttribute('title'),
                    node.getAttribute('aria-label'),
                  ]) {
                    const text = String(value || '')
                      .replace(/\s+/g, ' ')
                      .trim();
                    if (text && text.length <= 160) values.push(text);
                  }
                }
              }
              return wanted
                ? values.find(
                    (value) => value.toLowerCase().includes(wanted)
                  ) || ''
                : '';
            }
            """,
            expected,
        )
    except Exception:
        return ""
    return str(value or "").strip()

MTOP_APIS = {
    "1688": (
        "mtop.1688.MtopOrderService.queryOrder",
        "mtop.1688.DeliveryOrderService.queryDeliveryOrderPackList",
        "mtop.cbu.logistics.cbutradedeliveryorderservice.queryreceivepackagelist",
        "mtop.cbu.logistics.cbutradedeliveryorderservice.queryreceivepackagestatistic",
    ),
    "taobao": (
        "mtop.taobao.trade.fullinfo.get",
        "mtop.taobao.logistics.address.search",
    ),
}

PROCUREMENT_AMOUNT_FIELDS: dict[str, tuple[tuple[str, str], ...]] = {
    "1688": (
        ("payAmount", "cent"),
        ("pay_amount", "cent"),
        ("amount", "cent"),
        ("totalAmount", "cent"),
    ),
    "taobao": (
        ("payAmount", "cent"),
        ("pay_amount", "cent"),
        ("actualFee", "yuan"),
        ("payment", "yuan"),
        ("amount", "yuan"),
        ("totalAmount", "yuan"),
    ),
}

PLATFORM_ORDER_ID_FIELDS: dict[str, tuple[str, ...]] = {
    "1688": (
        "tradeNo",
        "trade_no",
        "orderId",
        "order_id",
        "bizOrderId",
        "mainOrderId",
    ),
    "taobao": (
        "tradeNo",
        "trade_no",
        "orderId",
        "order_id",
        "tradeId",
        "trade_id",
        "tid",
        "bizOrderId",
    ),
}


class ProcurementError(RuntimeError):
    pass


def detect_purchase_platform(url: str) -> str:
    host = (urlparse(str(url or "")).hostname or "").casefold().rstrip(".")
    if host == "1688.com" or host.endswith(".1688.com"):
        return "1688"
    if (
        host in {"taobao.com", "tmall.com"}
        or host.endswith(".taobao.com")
        or host.endswith(".tmall.com")
    ):
        return "taobao"
    raise ProcurementError("首版仅支持淘宝和 1688 商品链接")


def normalize_purchase_product_url(url: str) -> str:
    text = str(url or "").strip()
    parsed = urlparse(text)
    if parsed.scheme not in {"http", "https"}:
        raise ProcurementError("请输入完整的淘宝或 1688 商品链接")
    platform = detect_purchase_platform(text)
    query = parse_qs(parsed.query)
    if platform == "1688":
        match = re.search(r"/offer/(\d{6,})\.html", parsed.path, re.I)
        offer_id = (
            match.group(1)
            if match
            else (query.get("offerId") or [""])[0]
        )
        if str(offer_id).isdigit():
            return f"https://detail.1688.com/offer/{offer_id}.html"
    if platform == "taobao":
        item_id = (
            (query.get("id") or [""])[0]
            or (query.get("itemId") or [""])[0]
        )
        host = (
            "detail.tmall.com"
            if "tmall.com" in (parsed.hostname or "")
            else "item.taobao.com"
        )
        if str(item_id).isdigit():
            return f"https://{host}/item.htm?id={item_id}"
    return text


def purchase_source_identity(url: str) -> tuple[str, str, str]:
    normalized = normalize_purchase_product_url(url)
    platform = detect_purchase_platform(normalized)
    parsed = urlparse(normalized)
    if platform == "1688":
        match = re.search(r"/offer/(\d+)\.html", parsed.path, re.I)
        source_id = match.group(1) if match else ""
    else:
        query = parse_qs(parsed.query)
        source_id = (
            (query.get("id") or [""])[0]
            or (query.get("itemId") or [""])[0]
        )
    if not str(source_id).isdigit():
        raise ProcurementError("采购链接缺少有效商品 ID")
    return platform, str(source_id), normalized


def _source_response_matches(
    platform: str,
    response: dict[str, Any],
    source_id: str,
) -> bool:
    url = str(response.get("url") or "")
    if source_id and source_id in url:
        return True
    identity_keys = (
        ("offerId", "offer_id", "productId", "product_id")
        if platform == "1688"
        else ("itemId", "item_id")
    )
    for node in _walk_dicts(response.get("data")):
        if any(
            str(node.get(key) or "").strip() == source_id
            for key in identity_keys
        ):
            return True
    return False


def _target_capture_responses(
    platform: str,
    capture: dict[str, Any],
    source_id: str,
) -> list[dict[str, Any]]:
    responses = [
        item
        for item in capture.get("responses") or []
        if isinstance(item, dict)
    ]
    if not source_id:
        return responses
    return [
        item
        for item in responses
        if _source_response_matches(platform, item, source_id)
    ]


def _optional_amount_cents(value: Any, source_unit: str) -> int | None:
    if value is None or value == "":
        return None
    amount = parse_procurement_amount_cents(value, source_unit)
    return amount if amount > 0 else None


def validated_source_skus(
    skus: Any,
) -> list[dict[str, Any]]:
    if not isinstance(skus, list):
        raise ProcurementError("上家商品缺少真实 SKU 列表")
    output: list[dict[str, Any]] = []
    by_id: dict[str, dict[str, Any]] = {}
    for raw in skus:
        if not isinstance(raw, dict):
            continue
        sku_id = str(
            raw.get("sku_id")
            or raw.get("out_sku_id")
            or ""
        ).strip()
        label = _source_sku_label(raw).strip()
        if not sku_id or not label or label == "默认规格":
            continue
        price = _int(raw.get("price") or raw.get("sale_price"))
        stock_value = raw.get("stock")
        if stock_value in (None, ""):
            stock_value = raw.get("stock_num")
        try:
            stock = int(stock_value)
        except (TypeError, ValueError):
            continue
        if price <= 0 or stock <= 0:
            continue
        attrs = raw.get("attrs") or raw.get("sku_attrs") or []
        item = {
            **raw,
            "sku_id": sku_id,
            "label": label,
            "attrs": attrs,
            "price": price,
            "stock": stock,
        }
        previous = by_id.get(sku_id)
        if previous is not None:
            comparable = {
                "label": previous.get("label"),
                "price": previous.get("price"),
                "stock": previous.get("stock"),
            }
            current = {
                "label": item.get("label"),
                "price": item.get("price"),
                "stock": item.get("stock"),
            }
            if comparable != current:
                raise ProcurementError(
                    f"上家 SKU {sku_id} 出现冲突的重复数据"
                )
            continue
        by_id[sku_id] = item
        output.append(item)
    if not output:
        raise ProcurementError(
            "未能读取上家商品真实 SKU、有效价格和库存"
        )
    return output


def _normalized_sku_text(value: Any) -> str:
    if isinstance(value, list):
        value = " ".join(_normalized_sku_text(item) for item in value)
    elif isinstance(value, dict):
        value = " ".join(
            str(item)
            for item in value.values()
            if not isinstance(item, (dict, list))
        )
    return re.sub(
        r"[\s:：,，;；/|_\-（）()\[\]【】]+",
        "",
        str(value or "").casefold(),
    )


def _source_sku_label(sku: dict[str, Any]) -> str:
    attrs = sku.get("attrs") or sku.get("sku_attrs") or []
    if isinstance(attrs, dict):
        return " / ".join(str(value) for value in attrs.values() if value)
    if isinstance(attrs, list):
        return " / ".join(
            str(
                item.get("attr_value")
                or item.get("value")
                or item
            )
            for item in attrs
            if item
        )
    return str(sku.get("label") or sku.get("sku_id") or "")


def match_store_skus_to_source(
    store_skus: list[dict[str, Any]],
    source_skus: list[dict[str, Any]],
    *,
    threshold: float = 0.72,
) -> list[dict[str, Any]]:
    used: set[str] = set()
    output = []
    for store_sku in store_skus:
        store_text = _normalized_sku_text(
            store_sku.get("sku_attributes")
            or store_sku.get("label")
            or store_sku.get("sku_id")
        )
        ranked = []
        for source in source_skus:
            source_id = str(
                source.get("sku_id")
                or source.get("out_sku_id")
                or ""
            )
            if source_id in used:
                continue
            source_text = _normalized_sku_text(
                _source_sku_label(source)
            )
            if store_text == source_text:
                score = 1.0
            elif store_text and source_text and (
                store_text in source_text or source_text in store_text
            ):
                score = min(
                    0.98,
                    0.78
                    + min(len(store_text), len(source_text))
                    / max(len(store_text), len(source_text))
                    * 0.2,
                )
            else:
                score = 0.0
            ranked.append((score, source))
        ranked.sort(key=lambda item: item[0], reverse=True)
        score, source = ranked[0] if ranked else (0.0, {})
        matched = score >= threshold and bool(source)
        source_id = str(
            source.get("sku_id")
            or source.get("out_sku_id")
            or ""
        )
        if matched:
            used.add(source_id)
        output.append(
            {
                "store_sku_id": str(store_sku.get("sku_id") or ""),
                "store_sku_label": _source_sku_label(store_sku),
                "purchase_sku_id": source_id if matched else "",
                "purchase_sku_label": (
                    _source_sku_label(source) if matched else ""
                ),
                "purchase_attrs": (
                    source.get("attrs") or source.get("sku_attrs") or []
                )
                if matched
                else [],
                "purchase_price": int(
                    source.get("price") or source.get("sale_price") or 0
                )
                if matched
                else 0,
                "purchase_stock": int(
                    source.get("stock")
                    if source.get("stock") is not None
                    else source.get("stock_num") or 0
                )
                if matched
                else 0,
                "confidence": round(score, 4),
                "matched": matched,
            }
        )
    return output


def _record_source_product_ids(record: dict[str, Any]) -> set[str]:
    direct = record.get("source_product_ids")
    if isinstance(direct, list):
        values = {str(item) for item in direct if str(item)}
    else:
        values = set()
    raw = record.get("raw")
    if not isinstance(raw, dict):
        raw = record
    for node in _walk_dicts(raw):
        for key in (
            "offerId",
            "offer_id",
            "itemId",
            "item_id",
            "productId",
            "product_id",
        ):
            value = str(node.get(key) or "").strip()
            if value:
                values.add(value)
    return values


def _timestamp_seconds(value: Any) -> int:
    text = str(value or "").strip()
    if not text:
        return 0
    try:
        number = float(text)
        if number > 10_000_000_000:
            number /= 1000
        return int(number)
    except ValueError:
        pass
    try:
        return int(
            datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
        )
    except ValueError:
        return 0


def match_procurement_purchase(
    records: list[dict[str, Any]],
    *,
    source_product_id: str,
    quantity: int,
    amount_cents: int,
    sku_tokens: list[str],
    started_after: int,
    before_order_ids: set[str],
) -> dict[str, Any]:
    target_id = str(source_product_id or "").strip()
    if not target_id:
        return {
            "status": "not_found",
            "reason": "采购链接缺少目标商品 ID",
            "candidates": [],
        }
    before = {str(item) for item in before_order_ids if str(item)}
    candidates = []
    for record in records:
        if not isinstance(record, dict):
            continue
        external_id = str(
            record.get("external_order_id")
            or record.get("trade_no")
            or ""
        ).strip()
        if not external_id or external_id in before:
            continue
        if target_id not in _record_source_product_ids(record):
            continue
        purchase_time = _timestamp_seconds(
            record.get("purchase_time")
            or record.get("fetched_at")
        )
        if started_after and purchase_time:
            if purchase_time < int(started_after) - 300:
                continue
        candidates.append(record)
    if not candidates:
        return {
            "status": "not_found",
            "reason": "未发现目标商品的新采购订单",
            "candidates": [],
        }
    if len(candidates) > 1:
        return {
            "status": "ambiguous",
            "reason": f"发现 {len(candidates)} 个候选采购订单",
            "candidates": candidates,
        }
    record = candidates[0]
    reasons = []
    record_quantity = int(record.get("goods_count") or 0)
    if quantity > 0 and record_quantity != int(quantity):
        reasons.append(
            f"数量冲突：期望 {quantity}，官方 {record_quantity}"
        )
    record_amount = int(record.get("pay_amount") or 0)
    if amount_cents > 0 and record_amount != int(amount_cents):
        reasons.append(
            f"金额冲突：期望 {amount_cents} 分，官方 {record_amount} 分"
        )
    normalized_tokens = [
        _normalized_sku_text(token)
        for token in sku_tokens
        if _normalized_sku_text(token)
    ]
    if normalized_tokens:
        sku_text = _normalized_sku_text(
            record.get("sku_text")
            or record.get("raw")
        )
        if not any(token in sku_text for token in normalized_tokens):
            reasons.append("规格/SKU 冲突")
    if reasons:
        return {
            "status": "conflict",
            "reason": "；".join(reasons),
            "candidates": candidates,
        }
    return {
        "status": "confirmed",
        "reason": "",
        "order": record,
        "candidates": candidates,
    }


def cookie_value(cookies: list[dict[str, Any]], name: str) -> str:
    for cookie in cookies:
        if str(cookie.get("name") or "") == name:
            return str(cookie.get("value") or "")
    return ""


def cookie_header(cookies: list[dict[str, Any]]) -> str:
    return "; ".join(
        f"{cookie.get('name')}={cookie.get('value')}"
        for cookie in cookies
        if cookie.get("name") and cookie.get("value") is not None
    )


def _first(data: dict[str, Any], keys: tuple[str, ...], default: Any = "") -> Any:
    for key in keys:
        value = data.get(key)
        if value is not None and value != "":
            return value
    return default


def _int(value: Any) -> int:
    try:
        return int(round(float(value or 0)))
    except (TypeError, ValueError):
        return 0


def parse_procurement_amount_cents(value: Any, source_unit: str) -> int:
    """Parse a platform amount without binary floating-point rounding."""
    try:
        text = str(value).strip().replace(",", "").replace("¥", "").replace("￥", "")
        amount = Decimal(text)
        if source_unit == "yuan":
            amount *= Decimal("100")
        return int(amount.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    except (InvalidOperation, TypeError, ValueError):
        return 0


def _amount_cents(platform: str, data: dict[str, Any]) -> tuple[int, str, str]:
    fields = PROCUREMENT_AMOUNT_FIELDS.get(platform, ())
    for field, source_unit in fields:
        value = data.get(field)
        if value is None or value == "":
            continue
        return parse_procurement_amount_cents(value, source_unit), field, "cent"
    return 0, "", "cent"


def _walk_dicts(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_dicts(child)


def normalize_procurement_records(
    platform: str,
    payloads: list[Any],
) -> list[dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    for payload in payloads:
        for item in _walk_dicts(payload):
            trade_no = str(
                _first(
                    item,
                    PLATFORM_ORDER_ID_FIELDS.get(platform, ()),
                )
            )
            mail_no = str(
                _first(
                    item,
                    (
                        "mailNo",
                        "mail_no",
                        "waybillNo",
                        "waybill_no",
                        "billNo",
                        "trackingNumber",
                    ),
                )
            )
            pickup_code = str(
                _first(
                    item,
                    (
                        "sendingCode",
                        "sending_code",
                        "pickupCode",
                        "pickup_code",
                        "takeCode",
                        "code",
                    ),
                )
            )
            goods_title = str(
                _first(
                    item,
                    (
                        "itemName",
                        "item_name",
                        "goodsTitle",
                        "goods_title",
                        "productName",
                        "title",
                        "subject",
                    ),
                )
            )
            if not (trade_no or mail_no):
                continue
            external_id = trade_no or mail_no
            pay, pay_source, pay_unit = _amount_cents(platform, item)
            status = str(
                _first(item, ("status", "tradeStatus", "orderStatus", "logisticsStatus"))
            )
            records[external_id] = {
                "external_order_id": external_id,
                "trade_no": trade_no or external_id,
                "mail_no": mail_no,
                "pickup_code": pickup_code,
                "station_name": str(
                    _first(
                        item,
                        ("stationName", "station_name", "pickupStation", "siteName"),
                    )
                ),
                "station_phone": str(
                    _first(item, ("stationPhone", "station_phone", "phone", "mobile"))
                ),
                "status": status,
                "status_text": str(
                    _first(item, ("statusText", "status_text", "statusName"), default=status)
                ),
                "goods_title": goods_title,
                "goods_image": str(
                    _first(item, ("itemImg", "item_img", "goodsImage", "picUrl"))
                ),
                "goods_count": _int(
                    _first(item, ("itemNum", "item_num", "quantity", "buyAmount"))
                ),
                "pay_amount": pay,
                "pay_amount_source": pay_source,
                "pay_amount_unit": pay_unit,
                "unit_price": parse_procurement_amount_cents(
                    _first(
                        item,
                        (
                            "unitPrice",
                            "unit_price",
                            "itemPrice",
                            "item_price",
                            "price",
                        ),
                    ),
                    pay_unit,
                ),
                "freight_amount": parse_procurement_amount_cents(
                    _first(
                        item,
                        (
                            "postFee",
                            "post_fee",
                            "shippingFee",
                            "shipping_fee",
                            "freight",
                            "carriage",
                        ),
                    ),
                    pay_unit,
                ),
                "purchase_time": str(
                    _first(
                        item,
                        (
                            "createTime",
                            "create_time",
                            "payTime",
                            "pay_time",
                            "orderTime",
                            "order_time",
                            "gmtCreate",
                        ),
                    )
                ),
                "sku_text": str(
                    _first(
                        item,
                        (
                            "skuName",
                            "sku_name",
                            "specAttrs",
                            "spec_attrs",
                            "skuPropertiesName",
                            "sku_properties_name",
                        ),
                    )
                ),
                "source_product_ids": [
                    str(item.get(key))
                    for key in (
                        "offerId",
                        "offer_id",
                        "itemId",
                        "item_id",
                        "productId",
                        "product_id",
                    )
                    if item.get(key) not in (None, "")
                ],
                "carrier": str(
                    _first(
                        item,
                        (
                            "deliveryId",
                            "delivery_id",
                            "carrierCode",
                            "carrier_code",
                            "companyCode",
                            "company_code",
                            "expressCompanyCode",
                            "express_company_code",
                        ),
                    )
                ),
                "tracking_time": str(
                    _first(
                        item,
                        (
                            "deliveryTime",
                            "delivery_time",
                            "shipTime",
                            "ship_time",
                            "consignTime",
                        ),
                    )
                ),
                "seller": str(
                    _first(item, ("seller", "sellerName", "companyName", "supplier"))
                ),
                "buyer": str(
                    _first(item, ("buyer", "buyerName", "receiverName", "rcvName"))
                ),
                "fetched_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "raw": item,
                "platform": platform,
            }
    return list(records.values())


def normalize_supplier_capture(
    platform: str,
    capture: dict[str, Any],
) -> dict[str, Any]:
    key = canonical_platform(platform)
    target_source_id = str(
        capture.get("target_source_id")
        or capture.get("offer_id")
        or ""
    ).strip()
    if not target_source_id:
        capture_url = str(capture.get("url") or "").strip()
        if capture_url:
            try:
                target_source_id = purchase_source_identity(capture_url)[1]
            except ProcurementError:
                target_source_id = ""
    if key == "1688":
        from .source_1688 import Source1688Error, normalize_1688_capture

        responses = _target_capture_responses(
            "1688",
            capture,
            target_source_id,
        )
        if target_source_id and not responses:
            raise ProcurementError(
                f"未能读取目标 1688 商品 {target_source_id} 的官方主数据"
            )
        try:
            data = normalize_1688_capture(
                {
                    **capture,
                    "offer_id": (
                        target_source_id
                        or capture.get("offer_id")
                        or ""
                    ),
                    "target_source_id": target_source_id,
                    "responses": responses,
                }
            )
        except Source1688Error as exc:
            raise ProcurementError(str(exc)) from exc
        if (
            target_source_id
            and str(data.get("offer_id") or "") != target_source_id
        ):
            raise ProcurementError("读取结果与目标 1688 商品 ID 不一致")
        skus = [
            item
            for item in data.get("skus") or []
            if isinstance(item, dict)
        ]
        if not skus:
            raise ProcurementError("未能读取 1688 商品真实 SKU")
        return {
            "platform": "1688",
            "source_product_id": str(data.get("offer_id") or ""),
            "source_url": str(data.get("source_url") or capture.get("url") or ""),
            "title": str(data.get("title") or ""),
            "main_image": str((data.get("head_imgs") or [""])[0] or ""),
            "skus": [
                {
                    "sku_id": str(
                        item.get("out_sku_id")
                        or item.get("sku_code")
                        or ""
                    ),
                    "label": " / ".join(
                        str(attr.get("attr_value") or "")
                        for attr in item.get("sku_attrs") or []
                        if isinstance(attr, dict)
                    ),
                    "attrs": item.get("sku_attrs") or [],
                    "price": int(item.get("sale_price") or 0),
                    "stock": int(item.get("stock_num") or 0),
                }
                for item in skus
                if isinstance(item, dict)
            ],
            "read_at": datetime.now().isoformat(timespec="seconds"),
        }
    if key != "taobao":
        raise ProcurementError("首版仅支持淘宝和 1688 商品链接")
    responses = _target_capture_responses(
        "taobao",
        capture,
        target_source_id,
    )
    if target_source_id and not responses:
        raise ProcurementError(
            f"未能读取目标淘宝商品 {target_source_id} 的官方主数据"
        )
    nodes = [
        node
        for response in responses
        for node in _walk_dicts((response or {}).get("data"))
    ]
    dom = capture.get("dom") if isinstance(capture.get("dom"), dict) else {}
    title = str(
        dom.get("product_title")
        or ""
    ).strip()
    if not title:
        for node in nodes:
            value = node.get("title") or node.get("itemTitle")
            if value:
                title = re.sub(r"\s+", " ", str(value)).strip()
                if title:
                    break
    if not title:
        raise ProcurementError("未能读取淘宝商品标题")
    skus = []
    seen = set()
    for node in nodes:
        sku_id = str(
            node.get("skuId")
            or node.get("sku_id")
            or node.get("specId")
            or ""
        )
        attrs: list[dict[str, str]] = []
        for attr_key in (
            "skuProps",
            "skuProperties",
            "props",
            "specAttrs",
            "skuAttrs",
            "attributes",
        ):
            value = node.get(attr_key)
            if isinstance(value, dict):
                attrs = [
                    {
                        "attr_key": str(name),
                        "attr_value": str(attr),
                    }
                    for name, attr in value.items()
                    if not isinstance(attr, (dict, list))
                ]
            elif isinstance(value, list):
                attrs = []
                for item in value:
                    if isinstance(item, dict):
                        current = (
                            item.get("value")
                            or item.get("propValue")
                            or item.get("attr_value")
                        )
                        name = (
                            item.get("name")
                            or item.get("propName")
                            or "规格"
                        )
                        if current not in (None, ""):
                            attrs.append(
                                {
                                    "attr_key": str(name),
                                    "attr_value": str(current),
                                }
                            )
            if attrs:
                break
        if not sku_id or not attrs:
            continue
        price = 0
        for price_key in (
            "salePrice",
            "priceText",
            "price",
            "promotionPrice",
        ):
            if node.get(price_key) not in (None, ""):
                price = parse_procurement_amount_cents(
                    node.get(price_key),
                    "yuan",
                )
                if price:
                    break
        if not price or sku_id in seen:
            continue
        stock_value = (
            node.get("stock")
            if node.get("stock") not in (None, "")
            else node.get("quantity")
            if node.get("quantity") not in (None, "")
            else node.get("stockNum")
        )
        if stock_value in (None, ""):
            continue
        stock_num = _int(stock_value)
        seen.add(sku_id)
        skus.append(
            {
                "sku_id": sku_id,
                "label": " / ".join(
                    item["attr_value"] for item in attrs
                ),
                "attrs": attrs,
                "price": price,
                "stock": stock_num,
            }
        )
    if not skus:
        raise ProcurementError("未能读取淘宝商品真实 SKU")
    source_url = normalize_purchase_product_url(
        str(capture.get("url") or dom.get("url") or "")
    )
    query = parse_qs(urlparse(source_url).query)
    source_id = (
        (query.get("id") or [""])[0]
        or (query.get("itemId") or [""])[0]
    )
    if target_source_id and str(source_id) != target_source_id:
        raise ProcurementError("读取结果与目标淘宝商品 ID 不一致")
    main_images = [
        str(item.get("src") or "")
        for item in dom.get("main_images") or []
        if isinstance(item, dict) and item.get("src")
    ]
    return {
        "platform": "淘宝",
        "source_product_id": str(source_id),
        "source_url": source_url,
        "title": title,
        "main_image": main_images[0] if main_images else "",
        "skus": skus,
        "read_at": datetime.now().isoformat(timespec="seconds"),
    }


SUPPLIER_CAPTURE_SCRIPT = r"""
() => {
  const clean = (value) => String(value || '').replace(/\s+/g, ' ').trim();
  const productTitle = (() => {
    for (const node of document.querySelectorAll('script[type="application/ld+json"]')) {
      try {
        const data = JSON.parse(node.textContent || '{}');
        const values = Array.isArray(data) ? data : [data];
        const product = values.find((item) => item && item['@type'] === 'Product');
        if (clean(product?.name)) return clean(product.name);
      } catch (_error) {
        // Ignore malformed structured data and use a target-title selector.
      }
    }
    const selectors = [
      '[data-spm*="title"]',
      '[data-testid*="title"]',
      '[class*="offer-title"]',
      '[class*="product-title"]',
      '[class*="ItemHeader--mainTitle"]',
      '[class*="ItemTitle"]',
      '.tb-main-title',
    ];
    for (const selector of selectors) {
      const node = document.querySelector(selector);
      const text = clean(node?.innerText || node?.textContent);
      if (text) return text;
    }
    return '';
  })();
  return {
    title: productTitle,
    product_title: productTitle,
    url: location.href,
    text: clean(document.body?.innerText || '').slice(0, 30000),
    main_images: [...document.images].slice(0, 30).map((img) => ({
      src: img.currentSrc || img.src ||
        img.getAttribute('data-src') ||
        img.getAttribute('data-lazy-src') || ''
    })).filter((item) => item.src)
  };
}
"""


class ProcurementWebSession:
    def __init__(self, db: Database, data_dir: Path):
        self.db = db
        self.data_dir = data_dir
        self.use_system_proxy = False

    def login(
        self,
        account: dict[str, Any],
        *,
        auto_fill: bool = True,
    ) -> dict[str, Any]:
        try:
            from playwright.sync_api import Error as PlaywrightError
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise ProcurementError("缺少 Playwright 本地运行环境") from exc
        platform_name = str(account["platform"])
        platform = canonical_platform(platform_name)
        login_url = PLATFORM_LOGIN_URLS[platform]
        profile = procurement_profile_path(
            self.data_dir,
            account,
        )
        profile.mkdir(parents=True, exist_ok=True)
        playwright = sync_playwright().start()
        context = None
        cookies: list[dict[str, Any]] = []
        current_url = login_url
        try:
            try:
                launch_options = browser_launch_options(
                    headless=False,
                    no_viewport=True,
                    args=[
                        "--start-maximized",
                        "--no-first-run",
                        "--disable-popup-blocking",
                    ],
                )
                context = playwright.chromium.launch_persistent_context(
                    str(profile),
                    **launch_options,
                )
            except Exception as exc:
                raise ProcurementError(
                    f"采购账号登录窗口创建失败：{exc}"
                ) from exc
            page = context.pages[0] if context.pages else context.new_page()
            page.bring_to_front()
            page.goto(login_url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(900)
            login_name = (
                str(
                    account.get("login_id")
                    or account.get("label")
                    or ""
                ).strip()
                if auto_fill
                else ""
            )
            password = ""
            if auto_fill:
                try:
                    password = self.db.get_procurement_account_password(
                        int(account["id"])
                    )
                except Exception:
                    password = ""
            username_selectors = (
                "#fm-login-id",
                "input[name='account']",
                "input[name='loginId']",
                "input[name='username']",
                "#username",
                "#loginname",
            )
            password_selectors = (
                "#fm-login-password",
                "input[name='password']",
                "input[name='loginPassword']",
                "#password",
                "#nloginpwd",
            )
            for frame in context.pages[0].frames:
                if login_name:
                    for selector in username_selectors:
                        locator = frame.locator(selector).first
                        try:
                            if locator.count() and locator.is_visible():
                                locator.fill(login_name)
                                break
                        except Exception:
                            continue
                if password:
                    for selector in password_selectors:
                        locator = frame.locator(selector).first
                        try:
                            if locator.count() and locator.is_visible():
                                locator.fill(password)
                                break
                        except Exception:
                            continue
            markers = {
                "1688": {
                    "_m_h5_tk",
                    "cookie2",
                    "__cn_logon__",
                    "lgc",
                    "unb",
                },
                "taobao": {"_m_h5_tk", "cookie2", "tracknick", "unb"},
                "pinduoduo": {
                    "PDDAccessToken",
                    "pdd_user_id",
                    "api_uid",
                },
                "jd": {"pt_key", "pt_pin", "thor"},
            }.get(platform, set())
            expected = str(
                account.get("login_id") or account.get("label") or ""
            ).strip()
            identity = ""
            logged_in_detected_at = 0.0
            while not page.is_closed():
                open_pages = [
                    candidate
                    for candidate in context.pages
                    if not candidate.is_closed()
                ]
                if not open_pages:
                    break
                time.sleep(1)
                try:
                    cookies = normalize_cookies(context.cookies())
                    page_urls = [
                        candidate.url
                        for candidate in open_pages
                        if candidate.url.startswith("http")
                    ]
                    if page_urls:
                        current_url = page_urls[0]
                    names = {
                        str(cookie.get("name") or "")
                        for cookie in cookies
                    }
                    authenticated_page = any(
                        not any(
                            marker in url.casefold()
                            for marker in ("login.", "passport.")
                        )
                        for url in page_urls
                    )
                    identity = procurement_login_identity(
                        platform_name,
                        cookies,
                    )
                    if not identity and authenticated_page:
                        for candidate in open_pages:
                            if any(
                                marker
                                in str(candidate.url or "").casefold()
                                for marker in ("login.", "passport.")
                            ):
                                continue
                            identity = procurement_page_identity(
                                candidate,
                                expected,
                            )
                            if identity:
                                break
                    if (
                        names.intersection(markers)
                        and authenticated_page
                    ):
                        if not logged_in_detected_at:
                            logged_in_detected_at = time.monotonic()
                        if (
                            identity
                            or time.monotonic()
                            - logged_in_detected_at >= 15
                        ):
                            break
                except PlaywrightError:
                    break
        finally:
            if context is not None:
                try:
                    latest = normalize_cookies(context.cookies())
                    if latest:
                        cookies = latest
                except Exception:
                    pass
                try:
                    context.close()
                except Exception:
                    pass
            playwright.stop()
        if not cookies:
            self.db.update_procurement_account(
                int(account["id"]),
                status="未登录",
                last_error="没有捕获到登录 Cookie",
            )
            raise ProcurementError("没有捕获到登录 Cookie")
        names = {str(cookie.get("name") or "") for cookie in cookies}
        identity = (
            procurement_login_identity(platform_name, cookies)
            or identity
        )
        if expected and identity and identity.casefold() != expected.casefold():
            message = (
                f"登录到了“{identity}”，不是目标账号“{expected}”"
            )
            self.db.update_procurement_account(
                int(account["id"]),
                status="账号不匹配",
                last_error=message,
                last_checked_at=datetime.now().isoformat(
                    timespec="seconds"
                ),
            )
            self.cleanup_profile(account)
            raise ProcurementError(message)
        status = (
            "已登录"
            if expected
            and identity
            and names.intersection(markers)
            else "待验证"
        )
        payload = {
            "cookies": cookies,
            "source_url": current_url,
            "saved_at": int(time.time()),
            "platform": platform_name,
            "identity": identity,
            "expected_identity": expected,
            "login_window_closed_automatically": True,
        }
        self.db.set_procurement_session(
            int(account["id"]),
            payload,
            status,
            "",
        )
        updates: dict[str, Any] = {
            "status": status,
            "last_checked_at": datetime.now().isoformat(
                timespec="seconds"
            ),
            "last_error": "",
        }
        if identity:
            updates["nickname"] = identity
        self.db.update_procurement_account(
            int(account["id"]),
            **updates,
        )
        return payload

    def open_backend(
        self,
        account: dict[str, Any],
        *,
        url: str | None = None,
    ) -> dict[str, Any]:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise ProcurementError("缺少 Playwright 本地运行环境") from exc
        payload = self.db.get_procurement_session(int(account["id"]))
        if not payload:
            raise ProcurementError("采购账号尚未登录，请先扫码登录")
        platform_name = str(account["platform"])
        platform = canonical_platform(platform_name)
        expected = str(
            account.get("login_id")
            or account.get("label")
            or ""
        ).strip()
        profile = procurement_profile_path(
            self.data_dir,
            account,
        )
        profile.mkdir(parents=True, exist_ok=True)
        cookies = normalize_cookies(payload.get("cookies") or [])
        if not cookies:
            raise ProcurementError("采购账号会话不完整，请重新扫码登录")
        if self.check_status(account) != "已登录":
            raise ProcurementError("采购账号登录已失效，请重新扫码登录")
        target_url = url or PLATFORM_HOME_URLS[platform]
        playwright = sync_playwright().start()
        context = None
        identity = str(payload.get("identity") or expected)
        backend_ready = False
        try:
            context = playwright.chromium.launch_persistent_context(
                str(profile),
                **browser_launch_options(
                    headless=False,
                    no_viewport=True,
                    args=["--start-maximized", "--no-first-run"],
                ),
            )
            context.add_cookies(
                [
                    {
                        "name": cookie["name"],
                        "value": cookie["value"],
                        "domain": cookie.get("domain")
                        or _default_cookie_domain(platform),
                        "path": cookie.get("path") or "/",
                    }
                    for cookie in cookies
                ]
            )
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(
                target_url,
                wait_until="domcontentloaded",
                timeout=60000,
            )
            page.wait_for_timeout(1500)
            page.bring_to_front()
            current_url = str(page.url or "")
            if any(
                marker in current_url.casefold()
                for marker in ("login.", "passport.")
            ):
                self.db.update_procurement_account(
                    int(account["id"]),
                    status="登录已失效",
                    last_error="官方后台跳转到登录页面，请重新扫码",
                    last_checked_at=datetime.now().isoformat(
                        timespec="seconds"
                    ),
                )
                raise ProcurementError(
                    "官方后台跳转到登录页面，请重新扫码"
                )
            latest_cookies = normalize_cookies(context.cookies())
            identity = (
                procurement_login_identity(
                    platform_name,
                    latest_cookies,
                )
                or procurement_page_identity(page, expected)
            )
            if not identity:
                raise ProcurementError(
                    "官方后台未回读到目标采购账号身份"
                )
            if (
                expected
                and identity.casefold() != expected.casefold()
            ):
                raise ProcurementError(
                    f"官方后台属于“{identity}”，不是“{expected}”"
                )
            backend_ready = True
            _wait_for_page_close(page)
            return {
                "platform": platform_name,
                "identity": identity,
                "opened_url": target_url,
                "final_url": current_url,
                "login_window_closed_automatically": False,
            }
        finally:
            if context is not None:
                try:
                    latest_cookies = normalize_cookies(
                        context.cookies()
                    )
                    if latest_cookies:
                        cookies = latest_cookies
                except Exception:
                    pass
                try:
                    context.close()
                except Exception:
                    pass
            if cookies and backend_ready:
                payload["cookies"] = cookies
                payload["identity"] = identity
                self.db.set_procurement_session(
                    int(account["id"]),
                    payload,
                    "已登录",
                    "",
                )
            playwright.stop()

    def check_status(self, account: dict[str, Any]) -> str:
        payload = self.db.get_procurement_session(int(account["id"]))
        if not payload:
            status = "未登录"
            self.db.update_procurement_account(
                int(account["id"]),
                status=status,
                last_checked_at=datetime.now().isoformat(timespec="seconds"),
                last_error="本机没有采购账号登录会话",
            )
            return status
        platform_name = str(account["platform"])
        platform = canonical_platform(platform_name)
        profile = procurement_profile_path(
            self.data_dir,
            account,
        )
        profile.mkdir(parents=True, exist_ok=True)
        cookies = normalize_cookies(payload.get("cookies") or [])
        refreshed_cookies: list[dict[str, Any]] = []
        prior_status = str(account.get("status") or "")
        stored_identity = str(
            payload.get("identity")
            or ""
        ).strip()
        status = (
            "已登录"
            if prior_status == "已登录" and cookies
            else "未登录"
        )
        detail = ""
        try:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as playwright:
                context = playwright.chromium.launch_persistent_context(
                    str(profile),
                    **browser_launch_options(
                        headless=True,
                        viewport={"width": 1360, "height": 900},
                        args=["--no-first-run"],
                    ),
                )
                try:
                    context.add_cookies(
                        [
                            {
                                "name": cookie["name"],
                                "value": cookie["value"],
                                "domain": cookie.get("domain")
                                or _default_cookie_domain(platform),
                                "path": cookie.get("path") or "/",
                            }
                            for cookie in cookies
                        ]
                    )
                    page = (
                        context.pages[0]
                        if context.pages
                        else context.new_page()
                    )
                    page.goto(
                        PLATFORM_HOME_URLS.get(
                            platform,
                            PLATFORM_ORDER_URLS[platform],
                        ),
                        wait_until="domcontentloaded",
                        timeout=45000,
                    )
                    page.wait_for_timeout(1200)
                    final_url = page.url.lower()
                    refreshed_cookies = normalize_cookies(
                        context.cookies()
                    )
                    names = {
                        str(cookie.get("name") or "")
                        for cookie in refreshed_cookies
                    }
                    login_markers = {
                        "1688": {
                            "_m_h5_tk",
                            "cookie2",
                            "__cn_logon__",
                            "lgc",
                            "unb",
                        },
                        "taobao": {
                            "_m_h5_tk",
                            "cookie2",
                            "tracknick",
                            "unb",
                        },
                        "pinduoduo": {
                            "PDDAccessToken",
                            "pdd_user_id",
                            "api_uid",
                        },
                        "jd": {"pt_key", "pt_pin", "thor"},
                    }.get(platform, set())
                    logged_out = (
                        "login." in final_url
                        or "passport." in final_url
                    )
                    identity = procurement_login_identity(
                        platform_name,
                        [
                            {
                                "name": cookie.get("name"),
                                "value": cookie.get("value"),
                            }
                            for cookie in context.cookies()
                        ],
                    )
                    expected = str(
                        account.get("login_id")
                        or account.get("label")
                        or ""
                    ).strip()
                    if not identity:
                        identity = procurement_page_identity(
                            page,
                            expected,
                        )
                    if not identity:
                        identity = stored_identity
                    if not identity and expected:
                        identity = procurement_login_identity(
                            platform_name,
                            [
                                {
                                    "name": cookie.get("name"),
                                    "value": cookie.get("value"),
                                }
                                for cookie in cookies
                            ],
                        )
                    if (
                        expected
                        and identity
                        and identity.casefold() != expected.casefold()
                    ):
                        status = "账号不匹配"
                        detail = (
                            f"官方会话属于“{identity}”，"
                            f"不是“{expected}”"
                        )
                    elif (
                        names.intersection(login_markers)
                        and not logged_out
                    ):
                        if identity:
                            status = "已登录"
                            self.db.update_procurement_account(
                                int(account["id"]),
                                nickname=identity,
                            )
                            refreshed_session = dict(payload)
                            refreshed_session["identity"] = identity
                            refreshed_session["cookies"] = (
                                refreshed_cookies or cookies
                            )
                            refreshed_session["last_validated_at"] = (
                                datetime.now().isoformat(
                                    timespec="seconds"
                                )
                            )
                            self.db.set_procurement_session(
                                int(account["id"]),
                                refreshed_session,
                                "已登录",
                                "",
                            )
                        else:
                            status = "待验证"
                            detail = (
                                "官方登录凭据仍存在，但尚未回读到账号身份"
                            )
                    elif logged_out:
                        status = "登录已失效"
                    else:
                        # 1688 frequently serves a temporary challenge or a
                        # partially rendered home page.  That is not proof
                        # that the encrypted login session is invalid.
                        if prior_status == "已登录" and cookies:
                            status = "已登录"
                            detail = (
                                "官方页面暂未返回可判定状态，"
                                "本地加密会话已保留"
                            )
                            refreshed_session = dict(payload)
                            if refreshed_cookies:
                                refreshed_session["cookies"] = (
                                    refreshed_cookies
                                )
                            refreshed_session[
                                "last_status_check_at"
                            ] = datetime.now().isoformat(
                                timespec="seconds"
                            )
                            self.db.set_procurement_session(
                                int(account["id"]),
                                refreshed_session,
                                status,
                                detail,
                            )
                        else:
                            status = "待验证"
                finally:
                    context.close()
        except Exception as exc:
            if prior_status == "已登录" and cookies:
                status = "已登录"
                detail = (
                    "官方状态复验暂时失败，本地采购会话保留："
                    + str(exc)[:300]
                )
            else:
                status = "异常"
                detail = str(exc)[:500]
        self.db.update_procurement_account(
            int(account["id"]),
            status=status,
            last_checked_at=datetime.now().isoformat(timespec="seconds"),
            last_error=detail,
        )
        return status

    def cleanup_profile(self, account: dict[str, Any]) -> list[str]:
        """Remove only this account's default browser profile."""
        account_id = int(account["id"])
        default_root = (
            Path(self.data_dir).expanduser().resolve()
            / "procurement_profiles"
        )
        profile = procurement_profile_path(self.data_dir, account)
        try:
            profile.relative_to(default_root)
        except ValueError:
            return [
                f"自定义账号目录未自动删除：{profile}"
            ]
        if not profile.exists():
            return []
        try:
            shutil.rmtree(profile)
        except OSError as exc:
            return [f"账号浏览器目录清理失败：{exc}"]
        return []

    def inspect_product(
        self,
        account: dict[str, Any],
        url: str,
    ) -> dict[str, Any]:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise ProcurementError("缺少 Playwright 本地运行环境") from exc
        platform = canonical_platform(str(account.get("platform") or ""))
        target_url = normalize_purchase_product_url(url)
        if detect_purchase_platform(target_url) != platform:
            raise ProcurementError("商品链接平台与采购账号不一致")
        payload = self.db.get_procurement_session(int(account["id"]))
        if not payload:
            raise ProcurementError("采购账号尚未登录")
        profile = procurement_profile_path(
            self.data_dir,
            account,
        )
        profile.mkdir(parents=True, exist_ok=True)
        captured: list[dict[str, Any]] = []
        playwright = sync_playwright().start()
        context = None
        try:
            context = playwright.chromium.launch_persistent_context(
                str(profile),
                **browser_launch_options(
                    headless=True,
                    viewport={"width": 1440, "height": 1000},
                    args=[
                        "--no-first-run",
                        "--disable-blink-features=AutomationControlled",
                    ],
                ),
            )
            context.add_cookies(
                [
                    {
                        "name": cookie["name"],
                        "value": cookie["value"],
                        "domain": cookie.get("domain")
                        or _default_cookie_domain(platform),
                        "path": cookie.get("path") or "/",
                    }
                    for cookie in normalize_cookies(
                        payload.get("cookies") or []
                    )
                ]
            )
            page = context.pages[0] if context.pages else context.new_page()

            def on_response(response) -> None:
                content_type = str(
                    response.headers.get("content-type") or ""
                ).casefold()
                if "json" not in content_type:
                    return
                try:
                    data = response.json()
                except Exception:
                    return
                captured.append({"url": response.url, "data": data})

            page.on("response", on_response)
            page.goto(
                target_url,
                wait_until="domcontentloaded",
                timeout=60000,
            )
            page.wait_for_timeout(2500)
            for _index in range(4):
                page.mouse.wheel(0, 800)
                page.wait_for_timeout(400)
            dom = page.evaluate(SUPPLIER_CAPTURE_SCRIPT)
            final_url = str(page.url or target_url)
            text = str((dom or {}).get("text") or "")
            if (
                "login." in final_url.casefold()
                or "captcha" in final_url.casefold()
                or any(
                    marker in text
                    for marker in ("请登录", "安全验证", "验证码")
                )
            ):
                raise ProcurementError("官方页面要求重新登录或安全验证")
            return normalize_supplier_capture(
                platform,
                {
                    "url": final_url,
                    "dom": dom,
                    "responses": captured,
                    "target_source_id": purchase_source_identity(
                        target_url
                    )[1],
                },
            )
        finally:
            if context is not None:
                try:
                    latest = normalize_cookies(context.cookies())
                    if latest:
                        payload["cookies"] = latest
                except Exception:
                    pass
                try:
                    context.close()
                except Exception:
                    pass
            if payload.get("cookies"):
                self.db.set_procurement_session(
                    int(account["id"]),
                    payload,
                    "已登录",
                    "",
                )
            playwright.stop()

    def _read_order_list_in_context(
        self,
        account: dict[str, Any],
        context: Any,
    ) -> list[dict[str, Any]]:
        platform = canonical_platform(str(account.get("platform") or ""))
        order_url = (
            "https://trade.1688.com/order/buyer_order_list.htm"
            if platform == "1688"
            else (
                "https://buyertrade.taobao.com/trade/itemlist/"
                "list_bought_items.htm"
            )
        )
        order_page = context.new_page()
        captured: list[dict[str, Any]] = []

        def on_response(response) -> None:
            content_type = str(
                response.headers.get("content-type") or ""
            ).casefold()
            if "json" not in content_type:
                return
            try:
                data = response.json()
            except Exception:
                return
            captured.append({"url": response.url, "data": data})

        order_page.on("response", on_response)
        try:
            order_page.goto(
                order_url,
                wait_until="domcontentloaded",
                timeout=60000,
            )
            order_page.wait_for_timeout(2500)
            for _ in range(2):
                order_page.mouse.wheel(0, 900)
                order_page.wait_for_timeout(500)
        finally:
            try:
                order_page.remove_listener("response", on_response)
            except Exception:
                pass
            try:
                order_page.close()
            except Exception:
                pass
        payloads = [item.get("data") for item in captured]
        cookies = normalize_cookies(context.cookies())
        if cookies:
            try:
                client = MtopClient(platform, cookies)
                for api in MTOP_APIS[platform]:
                    data = (
                        {"page": 1, "pageSize": 30}
                        if "Order" in api or "trade" in api
                        else {"pageNo": 1, "pageSize": 30}
                    )
                    try:
                        payloads.append(client.call(api, data))
                    except ProcurementError:
                        continue
            except ProcurementError:
                pass
        return normalize_procurement_records(platform, payloads)

    def open_product(
        self,
        account: dict[str, Any],
        url: str,
        attrs: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        try:
            from playwright.sync_api import Error as PlaywrightError
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise ProcurementError("缺少 Playwright 本地运行环境") from exc
        platform = canonical_platform(str(account.get("platform") or ""))
        target_url = normalize_purchase_product_url(url)
        if detect_purchase_platform(target_url) != platform:
            raise ProcurementError("商品链接平台与采购账号不一致")
        payload = self.db.get_procurement_session(int(account["id"]))
        if not payload:
            raise ProcurementError("采购账号尚未登录")
        profile = procurement_profile_path(
            self.data_dir,
            account,
        )
        profile.mkdir(parents=True, exist_ok=True)
        playwright = sync_playwright().start()
        context = None
        selected = []
        try:
            context = playwright.chromium.launch_persistent_context(
                str(profile),
                **browser_launch_options(
                    headless=False,
                    no_viewport=True,
                    args=[
                        "--start-maximized",
                        "--no-first-run",
                        "--disable-blink-features=AutomationControlled",
                    ],
                ),
            )
            context.add_cookies(
                [
                    {
                        "name": cookie["name"],
                        "value": cookie["value"],
                        "domain": cookie.get("domain")
                        or _default_cookie_domain(platform),
                        "path": cookie.get("path") or "/",
                    }
                    for cookie in normalize_cookies(
                        payload.get("cookies") or []
                    )
                ]
            )
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(
                target_url,
                wait_until="domcontentloaded",
                timeout=60000,
            )
            page.wait_for_timeout(2200)
            for attr in attrs or []:
                label = str(
                    attr.get("attr_value")
                    or attr.get("value")
                    or ""
                ).strip()
                if not label:
                    continue
                try:
                    locator = page.get_by_text(label, exact=False).first
                    if locator.count() and locator.is_visible():
                        locator.click(timeout=3000)
                        selected.append(label)
                        page.wait_for_timeout(300)
                except Exception:
                    continue
            page.bring_to_front()
            while context.pages:
                time.sleep(1)
                try:
                    latest = normalize_cookies(context.cookies())
                    if latest:
                        payload["cookies"] = latest
                except PlaywrightError:
                    break
        finally:
            if context is not None:
                try:
                    latest = normalize_cookies(context.cookies())
                    if latest:
                        payload["cookies"] = latest
                except Exception:
                    pass
                try:
                    context.close()
                except Exception:
                    pass
            if payload.get("cookies"):
                self.db.set_procurement_session(
                    int(account["id"]),
                    payload,
                    "已登录",
                    "",
                )
            playwright.stop()
        return {
            "product_url": target_url,
            "selected_attrs": selected,
            "closed_at": datetime.now().isoformat(timespec="seconds"),
            "result": "verification_required",
        }

    def bind_alipay(self, account: dict[str, Any]) -> dict[str, Any]:
        profile_root = procurement_profile_path(
            self.data_dir,
            account,
        )
        profile = profile_root / "alipay_profile"
        profile.mkdir(parents=True, exist_ok=True)
        cookies: list[dict[str, Any]] = []
        try:
            from playwright.sync_api import Error as PlaywrightError
            from playwright.sync_api import sync_playwright

            with sync_playwright() as playwright:
                context = playwright.chromium.launch_persistent_context(
                    str(profile),
                    **browser_launch_options(
                        headless=False,
                        no_viewport=True,
                        args=["--start-maximized", "--no-first-run"],
                    ),
                )
                try:
                    page = (
                        context.pages[0]
                        if context.pages
                        else context.new_page()
                    )
                    page.goto(
                        "https://auth.alipay.com/login/index.htm",
                        wait_until="domcontentloaded",
                        timeout=60000,
                    )
                    while context.pages:
                        time.sleep(1)
                        try:
                            cookies = normalize_cookies(context.cookies())
                        except PlaywrightError:
                            break
                finally:
                    try:
                        latest = normalize_cookies(context.cookies())
                        if latest:
                            cookies = latest
                    except Exception:
                        pass
                    context.close()
        except Exception as exc:
            raise ProcurementError(f"支付宝授权窗口启动失败：{exc}") from exc
        if not cookies:
            raise ProcurementError("没有捕获到支付宝登录 Cookie")
        uid = ""
        for cookie in cookies:
            name = str(cookie.get("name") or "").lower()
            if "uid" in name or "user_id" in name:
                uid = str(cookie.get("value") or "")[:128]
                break
        self.db.set_procurement_alipay_session(
            int(account["id"]),
            {
                "cookies": cookies,
                "saved_at": int(time.time()),
                "platform": account.get("platform") or "",
            },
            uid,
        )
        return {
            "uid": uid,
            "cookie_count": len(cookies),
            "bound": True,
        }

    @staticmethod
    def payload_cookie_header(payload: dict[str, Any] | None) -> str:
        if not payload:
            return ""
        return cookie_header(normalize_cookies(payload.get("cookies") or []))

    def capture_page_responses(
        self,
        account: dict[str, Any],
        url: str | None = None,
        scroll_count: int = 8,
    ) -> list[dict[str, Any]]:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise ProcurementError("缺少 Playwright 本地运行环境") from exc
        platform_name = str(account["platform"])
        platform = canonical_platform(platform_name)
        payload = self.db.get_procurement_session(int(account["id"]))
        if not payload:
            raise ProcurementError("采购账号尚未登录")
        profile = procurement_profile_path(
            self.data_dir,
            account,
        )
        profile.mkdir(parents=True, exist_ok=True)
        captured: list[dict[str, Any]] = []
        playwright = sync_playwright().start()
        context = None
        try:
            context = playwright.chromium.launch_persistent_context(
                str(profile),
                **browser_launch_options(
                    headless=True,
                    viewport={"width": 1360, "height": 900},
                    args=["--no-first-run"],
                ),
            )
            context.add_cookies(
                [
                    {
                        "name": cookie["name"],
                        "value": cookie["value"],
                            "domain": cookie.get("domain")
                            or _default_cookie_domain(platform),
                        "path": cookie.get("path") or "/",
                    }
                    for cookie in normalize_cookies(payload.get("cookies") or [])
                ]
            )
            page = context.pages[0] if context.pages else context.new_page()

            def on_response(response) -> None:
                response_url = response.url.lower()
                if not any(
                    token in response_url
                    for token in ("order", "trade", "logistics", "parcel", "delivery")
                ):
                    return
                content_type = str(response.headers.get("content-type") or "")
                if "json" not in content_type:
                    return
                try:
                    data = response.json()
                except Exception:
                    return
                captured.append({"url": response.url, "data": data})

            page.on("response", on_response)
            page.goto(
                url or PLATFORM_ORDER_URLS[platform],
                wait_until="domcontentloaded",
                timeout=60000,
            )
            page.wait_for_timeout(2500)
            for _index in range(max(0, scroll_count)):
                page.mouse.wheel(0, 1400)
                page.wait_for_timeout(700)
        finally:
            if context is not None:
                try:
                    context.close()
                except Exception:
                    pass
            playwright.stop()
        return captured

    def browse(
        self,
        account: dict[str, Any],
        url: str | None = None,
    ) -> list[dict[str, Any]]:
        try:
            from playwright.sync_api import Error as PlaywrightError
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise ProcurementError("缺少 Playwright 本地运行环境") from exc
        platform_name = str(account["platform"])
        platform = canonical_platform(platform_name)
        payload = self.db.get_procurement_session(int(account["id"]))
        if not payload:
            raise ProcurementError("采购账号尚未登录")
        profile = procurement_profile_path(
            self.data_dir,
            account,
        )
        profile.mkdir(parents=True, exist_ok=True)
        captured: list[dict[str, Any]] = []
        cookies = normalize_cookies(payload.get("cookies") or [])
        playwright = sync_playwright().start()
        context = None
        try:
            context = playwright.chromium.launch_persistent_context(
                str(profile),
                **browser_launch_options(
                    headless=False,
                    no_viewport=True,
                    args=["--start-maximized", "--no-first-run"],
                ),
            )
            context.add_cookies(
                [
                    {
                        "name": cookie["name"],
                        "value": cookie["value"],
                            "domain": cookie.get("domain")
                            or _default_cookie_domain(platform),
                        "path": cookie.get("path") or "/",
                    }
                    for cookie in cookies
                ]
            )
            page = context.pages[0] if context.pages else context.new_page()

            def on_response(response) -> None:
                response_url = response.url.lower()
                if not any(
                    token in response_url
                    for token in ("order", "trade", "logistics", "parcel", "delivery")
                ):
                    return
                if "json" not in str(response.headers.get("content-type") or ""):
                    return
                try:
                    captured.append(
                        {"url": response.url, "data": response.json()}
                    )
                except Exception:
                    return

            page.on("response", on_response)
            page.goto(
                url or PLATFORM_ORDER_URLS[platform],
                wait_until="domcontentloaded",
                timeout=60000,
            )
            while context.pages:
                time.sleep(1)
                try:
                    latest = normalize_cookies(context.cookies())
                    if latest:
                        payload["cookies"] = latest
                except PlaywrightError:
                    break
        finally:
            if context is not None:
                try:
                    latest = normalize_cookies(context.cookies())
                    if latest:
                        payload["cookies"] = latest
                except Exception:
                    pass
                try:
                    context.close()
                except Exception:
                    pass
            if payload.get("cookies"):
                self.db.set_procurement_session(
                    int(account["id"]),
                    payload,
                    "已登录",
                    "",
                )
            playwright.stop()
        return captured


def purchase_overlay_script(details: dict[str, Any]) -> str:
    """Return an idempotent sidebar script for official purchase pages."""
    payload = json.dumps(
        details,
        ensure_ascii=False,
        separators=(",", ":"),
    ).replace("<", "\\u003c")
    return f"""
(() => {{
  if (window.top !== window) return;
  const hostName = String(location.hostname || '').toLowerCase();
  if (!/(^|\\.)(1688\\.com|taobao\\.com|tmall\\.com)$/.test(hostName)) return;
  const DATA = {payload};
  const install = () => {{
    const existing = document.getElementById('langlang-procurement-sidebar');
    if (existing) existing.remove();
    const host = document.createElement('div');
    host.id = 'langlang-procurement-sidebar';
    host.style.cssText = 'all:initial;position:fixed;left:0;top:0;width:340px;height:100vh;z-index:2147483647;';
    const shadow = host.attachShadow({{ mode: 'closed' }});
    const style = document.createElement('style');
    style.textContent = `
      *{{box-sizing:border-box}}
      .panel{{width:340px;height:100vh;overflow:auto;background:#fff;color:#20364a;border-right:1px solid #d7e1e9;box-shadow:4px 0 18px rgba(20,44,64,.18);font:12px/1.45 "Microsoft YaHei",sans-serif}}
      .top{{position:sticky;top:0;z-index:2;display:flex;align-items:center;justify-content:space-between;padding:12px 14px;background:#f4f8fb;border-bottom:1px solid #dce6ed}}
      .top b{{font-size:15px;color:#163b57}} .top button{{border:1px solid #cbd8e2;background:#fff;border-radius:6px;padding:4px 8px;cursor:pointer}}
      .section{{margin:10px;padding:11px;border:1px solid #dce5ec;border-radius:9px;background:#fff}}
      .section h3{{margin:0 0 8px;font-size:12px;color:#49677f}} .muted{{color:#7b8b99}} .row{{display:grid;grid-template-columns:62px minmax(0,1fr);gap:7px;margin:5px 0}}
      .row span:last-child{{overflow-wrap:anywhere}} .item{{display:grid;grid-template-columns:48px minmax(0,1fr);gap:9px;padding:9px 0;border-top:1px dashed #e4ebf0}}
      .item:first-of-type{{border-top:0}} .thumb{{width:48px;height:48px;border-radius:6px;object-fit:cover;background:#edf2f5}}
      .item b,.item small{{display:block}} .item b{{color:#223c50;line-height:1.35;max-height:36px;overflow:hidden}}
      .thumb-empty{{width:48px;height:48px;display:grid;place-items:center;border-radius:6px;background:#eef3f6;color:#8a99a5;font-size:10px}}
      canvas.thumb{{width:48px;height:48px;border-radius:6px;background:#eef3f6}}
      .image-preview{{position:fixed;inset:0;z-index:2147483647;display:grid;place-items:center;padding:24px;background:rgba(10,20,30,.72);cursor:zoom-out}}
      .image-preview canvas,.image-preview img{{max-width:90vw;max-height:90vh;width:auto;height:auto;border-radius:8px;background:#fff;box-shadow:0 18px 60px rgba(0,0,0,.35);cursor:zoom-out}}
      .sku{{margin-top:4px;color:#775515}} .money{{display:flex;justify-content:space-between;padding:4px 0}}
      .status{{padding:8px;border-radius:7px;background:#f7fafc;color:#607888;border:1px dashed #cbd9e2}}
      .actions{{display:grid;grid-template-columns:1fr 1fr;gap:7px;margin-top:9px}}
      .actions button{{border:0;border-radius:6px;padding:8px;background:#2f78b7;color:#fff;cursor:pointer}}
      .actions button.secondary{{background:#e8f0f6;color:#335b78}}
      .panel.collapsed{{width:44px}} .panel.collapsed .content{{display:none}} .panel.collapsed .top{{padding:8px 5px}} .panel.collapsed .top b{{display:none}}
    `;
    const panel = document.createElement('aside');
    panel.className = 'panel';
    const top = document.createElement('div'); top.className = 'top';
    const title = document.createElement('b'); title.textContent = '采购订单';
    const toggle = document.createElement('button'); toggle.type = 'button'; toggle.textContent = '收起';
    toggle.onclick = () => {{ panel.classList.toggle('collapsed'); toggle.textContent = panel.classList.contains('collapsed') ? '展开' : '收起'; }};
    top.append(title, toggle);
    const content = document.createElement('div'); content.className = 'content';
    const add = (parent, tag, cls, text) => {{ const n=document.createElement(tag); if(cls)n.className=cls; if(text!==undefined)n.textContent=text; parent.append(n); return n; }};
    const section = (heading) => {{ const s=add(content,'section','section'); add(s,'h3','',heading); return s; }};
    const identity = section('当前采购');
    [['账号', DATA.account?.label || '—'], ['店铺', DATA.store?.name || '—'], ['订单号', DATA.order?.id || '—']].forEach(([k,v]) => {{ const r=add(identity,'div','row'); add(r,'span','muted',k); add(r,'span','',v); }});
    const product = section('客户购买商品');
    (DATA.items || []).forEach((item) => {{
      const row=add(product,'div','item');
      let img;
      if (item.thumb_base64) {{
        img=document.createElement('canvas'); img.className='thumb'; img.width=96; img.height=96;
        try {{
          const binary=atob(item.thumb_base64);
          const bytes=new Uint8Array(binary.length);
          for(let i=0;i<binary.length;i++) bytes[i]=binary.charCodeAt(i);
          createImageBitmap(new Blob([bytes],{{type:item.thumb_mime||'image/jpeg'}})).then((bitmap) => {{
            const ctx=img.getContext('2d');
            const scale=Math.max(96/bitmap.width,96/bitmap.height);
            const width=bitmap.width*scale, height=bitmap.height*scale;
            ctx.drawImage(bitmap,(96-width)/2,(96-height)/2,width,height);
            img.style.cursor='zoom-in';
            img.onclick=() => {{
              const preview=document.createElement('div'); preview.className='image-preview';
              const large=document.createElement('canvas'); large.width=bitmap.width; large.height=bitmap.height;
              large.getContext('2d').drawImage(bitmap,0,0);
              preview.append(large); preview.onclick=() => preview.remove(); shadow.append(preview);
            }};
          }}).catch(() => {{ img.replaceWith(Object.assign(document.createElement('div'),{{className:'thumb-empty',textContent:'无图'}})); }});
        }} catch (_e) {{}}
      }} else if (item.thumb_url) {{
        img=document.createElement('img'); img.className='thumb'; img.referrerPolicy='no-referrer';
        img.src=item.thumb_url;
        img.style.cursor='zoom-in';
        img.onclick=() => {{
          const preview=document.createElement('div'); preview.className='image-preview';
          const large=document.createElement('img'); large.src=item.thumb_url; large.referrerPolicy='no-referrer';
          preview.append(large); preview.onclick=() => preview.remove(); shadow.append(preview);
        }};
      }} else {{
        img=document.createElement('div'); img.className='thumb-empty'; img.textContent='无图';
      }}
      row.append(img);
      const info=add(row,'div','');
      add(info,'b','',item.title || '订单商品');
      add(info,'small','muted',`商品 ID：${{item.product_id || '—'}}`);
      add(info,'small','sku',`本地 SKU：${{item.sku_attributes || item.sku_id || '—'}}`);
      add(info,'small','',`数量 ×${{item.quantity || 1}} · 售价 ${{item.sale_price_text || '—'}}`);
    }});
    const recipient = section('收件信息');
    [['收件人', DATA.recipient?.display_name || DATA.recipient?.user_name || '—'], ['电话', DATA.recipient?.display_phone || DATA.recipient?.tel_number || '—'], ['地址', DATA.recipient?.display_address || DATA.recipient?.full_address || '—']].forEach(([k,v]) => {{ const r=add(recipient,'div','row'); add(r,'span','muted',k); add(r,'span','',v); }});
    const customer = section('客户留言');
    add(customer,'div','',DATA.order?.customer_message || '无客户留言');
    const finance = section('金额');
    [['客户实付',DATA.order?.total_amount_text || '—'],['预计采购价',DATA.order?.cost_text || '待填写'],['预计利润',DATA.order?.profit_text || '—']].forEach(([k,v]) => {{ const r=add(finance,'div','money'); add(r,'span','muted',k); add(r,'b','',v); }});
    const statusSection = section('地址同步');
    const status = add(statusSection,'div','status','进入官方结算页后自动核对采购地址');
    status.id = 'address-status';
    const actions = add(statusSection,'div','actions');
    const copy = add(actions,'button','','复制收件信息');
    copy.onclick = async () => {{
      const text = [DATA.recipient?.display_name,DATA.recipient?.display_phone,DATA.recipient?.display_address].filter(Boolean).join('，');
      try {{ await navigator.clipboard.writeText(text); status.textContent='收件信息已复制'; }} catch (_e) {{
        const area=document.createElement('textarea'); area.value=text; document.body.append(area); area.select(); document.execCommand('copy'); area.remove(); status.textContent='收件信息已复制';
      }}
    }};
    const hide = add(actions,'button','secondary','关闭侧栏');
    hide.onclick = () => host.remove();
    window.addEventListener('langlang-address-status', (event) => {{
      if (event?.detail?.message) status.textContent = String(event.detail.message);
    }});
    content.prepend(identity);
    shadow.append(style, panel); // moved into panel below
    panel.append(top, content);
    document.documentElement.append(host);
    window.__langlangProcurementContext = DATA;
  }};
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', install, {{ once: true }});
  else install();
}})();
"""


def _set_overlay_status(page: Any, message: str) -> None:
    try:
        page.evaluate(
            """(message) => window.dispatchEvent(
              new CustomEvent('langlang-address-status', {detail: {message}})
            )""",
            message,
        )
    except Exception:
        pass


def _inline_overlay_thumbnails(
    context: Any,
    details: dict[str, Any],
) -> dict[str, Any]:
    prepared = {
        **details,
        "items": [
            dict(item)
            for item in details.get("items", [])
            if isinstance(item, dict)
        ],
    }
    for item in prepared["items"]:
        url = str(item.get("thumb_url") or "").strip()
        if not url.startswith(("http://", "https://")):
            continue
        try:
            response = context.request.get(
                url,
                timeout=8000,
                fail_on_status_code=False,
            )
            body = response.body()
            content_type = str(
                response.headers.get("content-type") or ""
            ).casefold()
            if (
                response.ok
                and body
                and len(body) <= 2 * 1024 * 1024
                and "image/" in content_type
            ):
                item["thumb_base64"] = base64.b64encode(body).decode(
                    "ascii"
                )
                item["thumb_mime"] = (
                    content_type.split(";", 1)[0] or "image/jpeg"
                )
        except Exception:
            continue
    return prepared


def _attempt_official_checkout_address(
    page: Any,
    address: dict[str, Any],
) -> str:
    if not address:
        return "采购地址为空，无法自动填写"
    if "1688.com" in str(page.url or "").casefold():
        return _attempt_1688_temporary_address(page, address)
    result = page.evaluate(
        r"""async (address) => {
          const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
          const visible = (el) => {
            if (!el) return false;
            const style = getComputedStyle(el);
            const rect = el.getBoundingClientRect();
            return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
          };
          const text = document.body?.innerText || '';
          const phoneBase = String(address.tel_number || '').split(/[-\s转]/)[0];
          const extension = String(address.tel_number || '').match(/[-\s转](\d{1,8})$/)?.[1] || '';
          const displayName = extension ? `${{address.user_name}}[${{extension}}]` : String(address.user_name || '');
          const displayDetail = extension ? `${{address.detail_info}}[${{extension}}]` : String(address.detail_info || '');
          if (String(address.detail_info || '') && text.includes(String(address.detail_info)) && text.includes(phoneBase) && (!extension || text.includes(`[${{extension}}]`))) {
            return {status: 'matched', message: '结算页已使用当前订单的收件地址'};
          }
          const setValue = (input, value) => {
            const proto = input.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
            const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
            if (setter) setter.call(input, value); else input.value = value;
            input.dispatchEvent(new Event('input', {bubbles: true}));
            input.dispatchEvent(new Event('change', {bubbles: true}));
            input.dispatchEvent(new Event('blur', {bubbles: true}));
          };
          const buttons = [...document.querySelectorAll('button,a,[role="button"]')].filter(visible);
          let addButton = buttons.find((el) => /新增收货地址|新增地址|添加地址|使用新地址/.test((el.innerText || el.textContent || '').replace(/\s+/g, '')));
          if (!addButton) {
            const modify = buttons.find((el) => /修改地址|修改收货地址|更换地址|选择地址/.test((el.innerText || el.textContent || '').replace(/\s+/g, '')));
            if (modify) {
              modify.click();
              await sleep(700);
              addButton = [...document.querySelectorAll('button,a,[role="button"]')].filter(visible).find((el) => /新增收货地址|新增地址|添加地址|使用新地址/.test((el.innerText || el.textContent || '').replace(/\s+/g, '')));
            }
          }
          if (!addButton) return {status: 'not_found', message: '未找到1688地址簿新增入口，请保留当前结算页并反馈界面'};
          addButton.click();
          await sleep(900);
          const fields = [...document.querySelectorAll('input,textarea')].filter(visible);
          const filled = {};
          const labelOf = (el) => {
            const labels = [...(el.labels || [])].map((x) => x.innerText || x.textContent || '').join(' ');
            let parent = el.parentElement; let parentText = '';
            for (let i=0; parent && i<4; i++,parent=parent.parentElement) parentText += ` ${{parent.innerText || ''}}`;
            return [el.placeholder,el.getAttribute('aria-label'),el.name,el.id,labels,parentText].filter(Boolean).join(' ').replace(/\s+/g,' ');
          };
          const rules = [
            ['user_name', /收货人|联系人|姓名/],
            ['tel_number', /手机|电话|联系方式/],
            ['province_name', /省份|所在省/],
            ['city_name', /城市|所在市/],
            ['county_name', /区县|区\/县|所在区/],
            ['detail_info', /详细地址|街道|门牌|地址详情/],
          ];
          for (const el of fields) {
            const label = labelOf(el);
            const rule = rules.find(([key, pattern]) => !filled[key] && pattern.test(label));
            if (!rule) continue;
            let value = String(address[rule[0]] || '');
            if (rule[0] === 'tel_number') value = phoneBase;
            if (rule[0] === 'user_name') value = displayName;
            if (rule[0] === 'detail_info') value = displayDetail;
            if (!value) continue;
            setValue(el, value); filled[rule[0]] = true;
          }
          const required = ['user_name','tel_number','province_name','city_name','county_name','detail_info'];
          if (required.some((key) => !filled[key])) {
            return {status: 'partial', message: '地址表单已打开，但省市区需要人工确认；未提交官方地址'};
          }
          const save = buttons.find((el) => /保存|确认|确定/.test((el.innerText || el.textContent || '').trim())) ||
            [...document.querySelectorAll('button,[role="button"]')].filter(visible).find((el) => /保存|确认|确定/.test((el.innerText || el.textContent || '').trim()));
          if (!save) return {status: 'partial', message: '地址字段已填写，未找到官方保存按钮，请人工保存'};
          save.click();
          await sleep(1200);
          const after = document.body?.innerText || '';
          if (after.includes(String(address.detail_info || '')) && after.includes(phoneBase)) {
            return {status: 'saved', message: '当前订单地址已写入1688地址簿和结算页，请核对后自行付款'};
          }
          return {status: 'verification_pending', message: '地址保存结果未回读，请在官方页面人工核对'};
        }""",
        address,
    )
    if not isinstance(result, dict):
        return "地址同步状态未知，请在官方页面人工核对"
    return str(result.get("message") or result.get("status") or "请在官方页面人工核对")


def _visible_locator(page: Any, selectors: tuple[str, ...]) -> Any:
    for selector in selectors:
        try:
            locator = page.locator(selector)
            count = locator.count()
        except Exception:
            continue
        for index in range(count):
            candidate = locator.nth(index)
            try:
                if candidate.is_visible():
                    return candidate
            except Exception:
                continue
    return None


def _visible_labeled_field(
    page: Any,
    marker: str,
    label_pattern: str,
) -> Any:
    """Mark a visible input by the nearest short field label.

    The Alibaba checkout modal does not keep stable placeholder/name attributes
    between releases. Its labels remain visible next to the editable controls,
    so select the input whose nearest labelled wrapper is shortest.
    """
    try:
        found = page.evaluate(
            r"""({marker, labelPattern}) => {
              const pattern = new RegExp(labelPattern);
              const visible = (el) => {
                if (!el) return false;
                const style = getComputedStyle(el);
                const rect = el.getBoundingClientRect();
                return style.display !== 'none'
                  && style.visibility !== 'hidden'
                  && rect.width > 0
                  && rect.height > 0;
              };
              const descriptiveText = (el) => [
                el.getAttribute('placeholder'),
                el.getAttribute('aria-label'),
                el.getAttribute('name'),
                el.id,
              ].filter(Boolean).join(' ');
              const candidates = [];
              for (const input of document.querySelectorAll(
                "input:not([type='hidden']):not([type='checkbox']):not([type='radio'])"
              )) {
                if (!visible(input) || input.readOnly || input.disabled) continue;
                let bestScore = Number.MAX_SAFE_INTEGER;
                const direct = descriptiveText(input);
                if (pattern.test(direct)) bestScore = direct.length;
                for (const label of (input.labels || [])) {
                  const text = (label.innerText || label.textContent || '').trim();
                  if (pattern.test(text)) bestScore = Math.min(bestScore, text.length + 20);
                }
                let parent = input.parentElement;
                for (let depth = 1; parent && depth <= 3; depth++, parent = parent.parentElement) {
                  const text = (parent.innerText || parent.textContent || '').replace(/\s+/g, ' ').trim();
                  if (text && text.length <= 180 && pattern.test(text)) {
                    bestScore = Math.min(bestScore, text.length + depth * 100);
                    break;
                  }
                }
                if (bestScore < Number.MAX_SAFE_INTEGER) {
                  candidates.push({input, score: bestScore});
                }
              }
              candidates.sort((a, b) => a.score - b.score);
              const chosen = candidates[0]?.input;
              if (!chosen) return false;
              document.querySelectorAll('[data-langlang-address-field]').forEach(
                (el) => el.removeAttribute('data-langlang-address-field')
              );
              chosen.setAttribute('data-langlang-address-field', marker);
              return true;
            }""",
            {
                "marker": marker,
                "labelPattern": label_pattern,
            },
        )
    except Exception:
        return None
    if not found:
        return None
    try:
        locator = page.locator(
            f"[data-langlang-address-field='{marker}']"
        )
        if locator.count() and locator.first.is_visible():
            return locator.first
    except Exception:
        return None
    return None


def _strip_temporary_extension(
    value: str,
) -> str:
    return re.sub(r"\s*\[\d{1,8}\]\s*$", "", str(value or "")).strip()


def _temporary_address_payload(
    address: dict[str, Any],
) -> dict[str, str]:
    phone = str(address.get("tel_number") or "").strip()
    extension_match = re.search(r"[-\s转](\d{1,8})$", phone)
    extension = extension_match.group(1) if extension_match else ""
    phone_base = re.split(r"[-\s转]", phone, maxsplit=1)[0]
    name = _strip_temporary_extension(
        str(address.get("user_name") or "")
    )
    detail = _strip_temporary_extension(
        str(address.get("detail_info") or "")
    )
    address_parts: list[str] = []
    for raw in (
        address.get("province_name"),
        address.get("city_name"),
        address.get("county_name"),
        address.get("street_name"),
        detail,
    ):
        part = _strip_temporary_extension(str(raw or ""))
        if not part:
            continue
        if address_parts and part in address_parts[-1]:
            continue
        if address_parts and address_parts[-1] in part:
            address_parts[-1] = part
            continue
        address_parts.append(part)
    full_address = "".join(address_parts)
    suffix = f"[{extension}]" if extension else ""
    return {
        "phone": phone,
        "phone_base": phone_base,
        "name": name or phone_base,
        "detail": detail,
        "full_address": full_address,
        "display_name": f"{name}[{extension}]" if name and extension else name,
        "display_address": (
            f"{full_address}[{extension}]"
            if full_address and extension
            else full_address
        ),
        "extension_suffix": suffix,
    }


def _temporary_address_fingerprint(payload: dict[str, str]) -> str:
    raw = "\0".join(
        (
            payload["display_name"],
            payload["phone_base"],
            payload["display_address"],
        )
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def _read_official_checkout_address(
    page: Any,
    payload: dict[str, str],
) -> str:
    """Read a short official address block, excluding the injected sidebar."""

    try:
        value = page.evaluate(
            r"""(wanted) => {
              const compact = (value) =>
                String(value || '').replace(/\s+/g, '');
              const needles = wanted.map(compact).filter(Boolean);
              if (!needles.length) return '';
              const visible = (el) => {
                const style = getComputedStyle(el);
                const rect = el.getBoundingClientRect();
                return style.display !== 'none'
                  && style.visibility !== 'hidden'
                  && rect.width > 0
                  && rect.height > 0;
              };
              const candidates = [];
              for (const el of document.querySelectorAll(
                'main *, [class*="address"] *, [class*="Address"] *, '
                + '[id*="address"] *, [id*="Address"] *, body > *'
              )) {
                if (!visible(el)) continue;
                if (el.closest(
                  '#langlang-procurement-sidebar, script, style, '
                  + 'input, textarea, button, select, [role="dialog"]'
                )) continue;
                const text = String(el.innerText || el.textContent || '')
                  .replace(/\s+/g, ' ')
                  .trim();
                if (!text || text.length > 600) continue;
                const value = compact(text);
                if (needles.every((needle) => value.includes(needle))) {
                  candidates.push(text);
                }
              }
              candidates.sort((a, b) => a.length - b.length);
              return candidates[0] || '';
            }""",
            [
                payload["display_name"],
                payload["phone_base"],
                payload["display_address"],
            ],
        )
    except Exception:
        return ""
    return str(value or "").strip()


def _set_temporary_address_phase(
    page: Any,
    fingerprint: str,
    phase: str,
) -> None:
    try:
        page.evaluate(
            """({fingerprint, phase}) => {
              window.__langlang1688TemporaryAddress = {fingerprint, phase};
            }""",
            {
                "fingerprint": fingerprint,
                "phase": phase,
            },
        )
    except Exception:
        pass


def _temporary_address_phase(
    page: Any,
    fingerprint: str,
) -> str:
    try:
        state = page.evaluate(
            """() => window.__langlang1688TemporaryAddress || null"""
        )
    except Exception:
        return ""
    if not isinstance(state, dict):
        return ""
    if str(state.get("fingerprint") or "") != fingerprint:
        return ""
    return str(state.get("phase") or "")


def _attempt_1688_temporary_address(
    page: Any,
    address: dict[str, Any],
) -> str:
    payload = _temporary_address_payload(address)
    phone_base = payload["phone_base"]
    display_name = payload["display_name"]
    display_address = payload["display_address"]
    if (
        not payload["name"]
        or not phone_base
        or not display_address
    ):
        return "采购地址不完整，无法填写1688临时地址"
    fingerprint = _temporary_address_fingerprint(payload)
    phase = _temporary_address_phase(page, fingerprint)
    if _read_official_checkout_address(page, payload):
        _set_temporary_address_phase(page, fingerprint, "confirmed")
        return "结算页已使用当前订单的1688临时地址"
    if phase in {"submitting", "submitted"}:
        return (
            "1688临时地址已提交但顶部地址未回读，"
            "请勿重复提交，请在结算页人工核对"
        )
    edit = _visible_locator(
        page,
        (
            "text=编辑临时地址",
            "a:has-text('编辑临时地址')",
            "button:has-text('编辑临时地址')",
        ),
    )
    if edit is None:
        modify = _visible_locator(
            page,
            (
                "button:has-text('修改地址')",
                "[role='button']:has-text('修改地址')",
                "text=修改地址",
            ),
        )
        if modify is not None:
            try:
                modify.click(timeout=5000)
                page.wait_for_timeout(500)
            except Exception:
                pass
            edit = _visible_locator(
                page,
                (
                    "text=编辑临时地址",
                    "a:has-text('编辑临时地址')",
                    "button:has-text('编辑临时地址')",
                ),
            )
    if edit is None:
        return "未找到“编辑临时地址”入口，请在结算页人工处理"
    try:
        edit.click(timeout=6000)
        page.wait_for_timeout(900)
        textarea = _visible_locator(
            page,
            (
                "textarea",
                "[contenteditable='true']",
            ),
        )
        if textarea is None:
            return "已打开临时地址，但未找到批量地址输入框"
        textarea.fill(
            f"{payload['name']}\n{phone_base}\n{display_address}"
        )
        auto_fill = _visible_locator(
            page,
            (
                "button:has-text('自动填写')",
                "[role='button']:has-text('自动填写')",
                "text=自动填写",
            ),
        )
        if auto_fill is not None:
            auto_fill.click(timeout=6000)
            page.wait_for_timeout(900)

        # The official parser can omit the recipient name or retain the previous
        # temporary recipient. Fill the two stable text controls afterwards;
        # region selection and postal code remain owned by the official parser.
        name_input = _visible_labeled_field(
            page,
            "recipient-name",
            "姓名|收货人|联系人",
        )
        if name_input is None:
            name_input = _visible_locator(
                page,
                (
                    "input[placeholder*='姓名']",
                    "input[placeholder*='收货人']",
                    "input[aria-label*='姓名']",
                    "input[aria-label*='收货人']",
                    "input[name*='name' i]",
                ),
            )
        if name_input is not None:
            name_input.fill(display_name)
        address_input = _visible_labeled_field(
            page,
            "recipient-address",
            "详细地址|街道|门牌|地址详情|^地址$",
        )
        if address_input is None:
            address_input = _visible_locator(
                page,
                (
                    "input[placeholder*='详细地址']",
                    "input[placeholder*='街道']",
                    "input[aria-label*='详细地址']",
                    "input[name*='address' i]",
                ),
            )
        if address_input is not None:
            address_input.fill(display_address)

        save = _visible_locator(
            page,
            (
                "button:has-text('保存')",
                "[role='button']:has-text('保存')",
            ),
        )
        if save is None:
            return "临时地址已填写，但未找到保存按钮，请人工确认"
        _set_temporary_address_phase(page, fingerprint, "submitting")
        save.click(timeout=6000)
        _set_temporary_address_phase(page, fingerprint, "submitted")
        page.wait_for_timeout(1200)
        if _read_official_checkout_address(page, payload):
            _set_temporary_address_phase(page, fingerprint, "confirmed")
            return "1688临时地址已保存并回读一致，请核对后付款"
        return "1688临时地址保存结果未回读，请在结算页人工确认"
    except Exception as exc:
        if _read_official_checkout_address(page, payload):
            _set_temporary_address_phase(page, fingerprint, "confirmed")
            return "1688临时地址已保存并回读一致，请核对后付款"
        if _temporary_address_phase(page, fingerprint) == "submitting":
            return (
                "1688临时地址提交状态未知，请勿重复提交，"
                "请在结算页人工核对：" + str(exc)[:180]
            )
        return "1688临时地址自动填写失败，请人工确认：" + str(exc)[:180]


class ControlledPurchaseWindow:
    def __init__(
        self,
        db: Database,
        data_dir: Path,
        account: dict[str, Any],
        url: str,
        address: dict[str, Any] | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.db = db
        self.data_dir = Path(data_dir)
        self.account = dict(account)
        self.url = str(url)
        self.address = dict(address or {})
        self.details = dict(details or {})
        self.commands: queue.Queue[
            tuple[str, dict[str, Any], queue.Queue[dict[str, Any]]]
        ] = queue.Queue()
        self.ready = threading.Event()
        self.closed = threading.Event()
        self.error = ""
        self.thread = threading.Thread(
            target=self._run,
            name=f"ProcurementWindow-{account.get('id')}",
            daemon=True,
        )

    def start(self) -> None:
        self.thread.start()
        if not self.ready.wait(60):
            raise ProcurementError("采购窗口启动超时")
        if self.error:
            raise ProcurementError(self.error)

    def _run(self) -> None:
        context = None
        playwright = None
        try:
            from playwright.sync_api import sync_playwright

            platform = canonical_platform(
                str(self.account.get("platform") or "")
            )
            payload = self.db.get_procurement_session(
                int(self.account["id"])
            )
            if not payload:
                raise ProcurementError("采购账号尚未登录")
            profile = procurement_profile_path(
                self.data_dir,
                self.account,
            )
            profile.mkdir(parents=True, exist_ok=True)
            playwright = sync_playwright().start()
            context = playwright.chromium.launch_persistent_context(
                str(profile),
                **browser_launch_options(
                    headless=False,
                    no_viewport=True,
                    args=[
                        "--start-maximized",
                        "--no-first-run",
                        "--disable-blink-features=AutomationControlled",
                    ],
                ),
            )
            if self.details:
                details = _inline_overlay_thumbnails(
                    context,
                    self.details,
                )
                context.add_init_script(
                    script=purchase_overlay_script(details)
                )
            cookies = normalize_cookies(payload.get("cookies") or [])
            if cookies:
                context.add_cookies(
                    [
                        {
                            "name": cookie["name"],
                            "value": cookie["value"],
                            "domain": cookie.get("domain")
                            or _default_cookie_domain(platform),
                            "path": cookie.get("path") or "/",
                        }
                        for cookie in cookies
                    ]
                )
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(
                self.url,
                wait_until="domcontentloaded",
                timeout=60000,
            )
            page.bring_to_front()
            self.ready.set()
            session = ProcurementWebSession(self.db, self.data_dir)
            attempted_checkout_urls: dict[str, int] = {}
            checkout_messages: dict[str, str] = {}
            while True:
                try:
                    command, args, response = self.commands.get(timeout=1)
                except queue.Empty:
                    active_pages = [
                        candidate
                        for candidate in context.pages
                        if not candidate.is_closed()
                    ]
                    if not active_pages:
                        break
                    for candidate in active_pages:
                        current_url = str(candidate.url or "")
                        checkout = any(
                            marker in current_url.casefold()
                            for marker in (
                                "confirm_order",
                                "confirmorder",
                                "/order/confirm",
                                "buy.1688.com",
                                "order.1688.com",
                                "buyer_make_order",
                                "smart_make_order",
                            )
                        )
                        if not checkout:
                            continue
                        previous_message = checkout_messages.get(
                            current_url,
                            "",
                        )
                        attempts = attempted_checkout_urls.get(
                            current_url,
                            0,
                        )
                        should_retry = (
                            attempts == 0
                            or (
                                "未找到" in previous_message
                                and attempts < 4
                            )
                        )
                        if not should_retry:
                            continue
                        attempted_checkout_urls[current_url] = attempts + 1
                        candidate.wait_for_timeout(
                            1200 if attempts == 0 else 1800
                        )
                        _set_overlay_status(
                            candidate,
                            "正在核对官方结算页收件地址…",
                        )
                        try:
                            message = _attempt_official_checkout_address(
                                candidate,
                                self.address,
                            )
                        except Exception as exc:
                            message = (
                                "地址自动核对未完成，请人工确认："
                                + str(exc)[:160]
                            )
                        checkout_messages[current_url] = message
                        _set_overlay_status(candidate, message)
                    continue
                if command == "close":
                    response.put({"status": "closed"})
                    break
                if command == "verify":
                    try:
                        records = session._read_order_list_in_context(
                            self.account,
                            context,
                        )
                        response.put({
                            "status": "ok",
                            "records": records,
                        })
                    except Exception as exc:
                        response.put({
                            "status": "error",
                            "reason": str(exc)[:500],
                        })
        except Exception as exc:
            self.error = str(exc)[:1000]
            self.ready.set()
        finally:
            self.closed.set()
            if context is not None:
                context.close()
            if playwright is not None:
                playwright.stop()

    def verify_once(
        self,
        expected: dict[str, Any],
        timeout: int = 90,
    ) -> dict[str, Any]:
        if self.closed.is_set():
            return {
                "status": "error",
                "reason": "采购页面已关闭",
            }
        response: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)
        self.commands.put(("verify", expected, response))
        try:
            payload = response.get(timeout=timeout)
        except queue.Empty:
            return {
                "status": "error",
                "reason": "官方订单读取超时",
            }
        if payload.get("status") != "ok":
            return payload
        return match_procurement_purchase(
            payload.get("records") or [],
            **expected,
        )

    def close(self, timeout: int = 30) -> None:
        if self.closed.is_set():
            return
        response: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)
        self.commands.put(("close", {}, response))
        try:
            response.get(timeout=timeout)
        except queue.Empty:
            pass
        self.thread.join(timeout=timeout)


class MtopClient:
    def __init__(self, platform: str, cookies: list[dict[str, Any]]):
        self.platform = platform
        self.host = MTOP_HOSTS[platform]
        self.cookie_header = cookie_header(cookies)
        self.token = cookie_value(cookies, "_m_h5_tk").split("_", 1)[0]
        if not self.token:
            raise ProcurementError("登录 Cookie 中没有 MTOP 令牌")

    def call(self, api: str, data: dict[str, Any]) -> dict[str, Any]:
        timestamp = str(int(time.time() * 1000))
        data_text = json.dumps(
            data,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        app_key = "12574478"
        sign_text = f"{self.token}&{timestamp}&{app_key}&{data_text}"
        sign = hashlib.md5(sign_text.encode("utf-8")).hexdigest()
        params = {
            "jsv": "2.7.2",
            "appKey": app_key,
            "t": timestamp,
            "sign": sign,
            "api": api,
            "v": "1.0",
            "type": "jsonp",
            "dataType": "jsonp",
            "data": data_text,
        }
        try:
            response = requests.get(
                f"{self.host}/h5/{api}/1.0/",
                params=params,
                headers={
                    "Cookie": self.cookie_header,
                    "Referer": (
                        "https://trade.1688.com/"
                        if self.platform == "1688"
                        else "https://buyertrade.taobao.com/"
                    ),
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 Chrome/132.0.0.0 Safari/537.36"
                    ),
                },
                timeout=(5, 25),
                proxies={"http": None, "https": None},
            )
        except requests.RequestException as exc:
            raise ProcurementError(f"MTOP网络异常：{exc}") from exc
        text = response.text.strip()
        match = re.search(r"\((\{.*\})\)\s*;?\s*$", text, re.S)
        if match:
            text = match.group(1)
        try:
            result = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ProcurementError("MTOP返回内容无法解析") from exc
        if "TOKEN_EMPTY" in text or "TOKEN_EXPIRED" in text:
            raise ProcurementError("MTOP令牌已过期，请重新登录采购账号")
        return result


class ProcurementAdapter:
    def __init__(self, db: Database, data_dir: Path):
        self.db = db
        self.data_dir = data_dir
        self.web = ProcurementWebSession(db, data_dir)

    def sync(
        self,
        account: dict[str, Any],
        progress: Callable[[str, int, int], None] | None = None,
    ) -> int:
        platform_name = str(account["platform"])
        platform = canonical_platform(platform_name)
        payloads: list[Any] = []
        session = self.db.get_procurement_session(int(account["id"]))
        if not session:
            raise ProcurementError("采购账号尚未登录")
        cookies = normalize_cookies(session.get("cookies") or [])
        if progress:
            progress("正在读取采购订单", 0, 2)
        try:
            client = MtopClient(platform, cookies)
            for api in MTOP_APIS[platform]:
                data = (
                    {"page": 1, "pageSize": 30}
                    if "Order" in api or "trade" in api
                    else {"pageNo": 1, "pageSize": 30}
                )
                try:
                    payloads.append(client.call(api, data))
                except ProcurementError:
                    continue
        except ProcurementError:
            pass
        if progress:
            progress("正在读取采购页面", 1, 2)
        try:
            captured = self.web.capture_page_responses(account)
            payloads.extend(item.get("data") for item in captured)
        except ProcurementError:
            if not payloads:
                raise
        records = normalize_procurement_records(platform, payloads)
        for record in records:
            self.db.upsert_procurement_order(
                int(account["id"]),
                platform_name,
                record,
            )
        self.db.update_procurement_account(
            int(account["id"]),
            status="已登录",
            last_sync=time.strftime("%Y-%m-%d %H:%M:%S"),
            last_error="",
        )
        if progress:
            progress("采购订单同步完成", 2, 2)
        return len(records)

    def match_orders(self, store_id: int) -> int:
        unmatched = self.db.list_procurement_orders()
        matched = 0
        for procurement in unmatched:
            mail_no = str(procurement.get("mail_no") or "")
            trade_no = str(procurement.get("trade_no") or "")
            candidates = []
            if mail_no:
                candidates = self.db.list_orders(store_id=store_id, search=mail_no)
            if not candidates and trade_no:
                candidates = self.db.list_orders(store_id=store_id, search=trade_no)
            exact = [
                order
                for order in candidates
                if mail_no and mail_no == str(order.get("waybill_id") or "")
            ]
            if len(exact) == 1:
                self.db.upsert_procurement_link(
                    store_id,
                    str(exact[0]["order_id"]),
                    int(procurement["id"]),
                    "mail_no",
                    100,
                )
                matched += 1
        return matched
