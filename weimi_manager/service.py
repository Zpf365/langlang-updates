from __future__ import annotations

import json
import re
import threading
import time
import uuid
from concurrent.futures import CancelledError, ThreadPoolExecutor, as_completed
from copy import deepcopy
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from .db import PRODUCT_STATUS, Database, order_status_text
from .mock import MockShopClient, ProgressCallback
from .models import (
    Order,
    Product,
    ShippingAccount,
    ShippingTemplate,
    Store,
    SyncResult,
    order_counts_as_transaction,
    product_matches_tab,
)
from .procurement import ProcurementAdapter, ProcurementWebSession
from .shop_private import ShopPrivateClient, ShopPrivateError
from .wechat_api import (
    WechatShopClient,
    parse_shipping_account,
    parse_shipping_template,
)

_NON_BLOCKING_VIOLATION_MARKERS = (
    "价格错误",
    "价格设置错误",
    "价格异常",
    "价保异常",
    "库存错误",
)
_CLOSED_VIOLATION_STATUSES = {
    "已撤销",
    "申诉成功",
    "预警解除",
    "预警结束",
    "审核中",
}


def _discount_price_cents(
    price_cents: int,
    multiplier: float | Decimal,
) -> int:
    """Apply a price multiplier with currency-safe half-up rounding."""
    ratio = (
        multiplier
        if isinstance(multiplier, Decimal)
        else Decimal(str(multiplier))
    )
    value = Decimal(int(price_cents)) * ratio
    return max(
        1,
        int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP)),
    )


def is_explicit_violation_record(record: dict[str, Any]) -> bool:
    """Return true only for actionable platform violation titles."""
    if not isinstance(record, dict):
        return False
    title = str(record.get("title") or "").strip()
    product_id = str(record.get("product_id") or "").strip()
    if not title and not product_id:
        return False
    status = str(record.get("status") or "").strip()
    if status in _CLOSED_VIOLATION_STATUSES:
        return False
    source = str(record.get("source") or "").strip()
    if source in {"预警", "本机黑名单", "导入清单"} and status != "已违规":
        return False
    text = f"{title} {record.get('reason') or ''} {record.get('fix') or ''}"
    return not any(
        marker in text
        for marker in _NON_BLOCKING_VIOLATION_MARKERS
    )


class SyncService:
    def __init__(self, db: Database, sleep: Callable[[float], None] | None = None):
        self.db = db
        self.sleep = sleep
        self._product_payload_defaults_cache: dict[int, dict[str, Any]] = {}
        self._category_rule_cache: dict[tuple[int, str, str], dict[str, Any]] = {}

    def client_for(self, store: Store) -> WechatShopClient | MockShopClient:
        if store.app_id == "demo-store":
            return MockShopClient(store)
        _, secret = self.db.get_store_secret(store.id)
        if not secret:
            raise ValueError("该店铺仅使用网页登录，未配置 AppSecret，不能调用公开 API")
        kwargs: dict[str, Any] = (
            {"sleeper": self.sleep} if self.sleep else {}
        )
        observer = getattr(self, "call_observer", None)
        if callable(observer):
            kwargs["call_observer"] = observer
        return WechatShopClient(store.app_id, secret, **kwargs)

    def validate_store(self, store: Store) -> None:
        client = self.client_for(store)
        message = client.test_connection()
        self.db.set_store_status(store.id, message)

    def sync_orders(
        self,
        store: Store,
        start_time: int,
        end_time: int,
        progress: ProgressCallback | None = None,
    ) -> SyncResult:
        if end_time - start_time > 7 * 86400:
            raise ValueError("订单同步时间范围不能超过 7 天")
        run_id = self.db.start_sync_run(store.id, "orders")
        try:
            client = self.client_for(store)
            if isinstance(client, MockShopClient):
                orders = client.fetch_orders(start_time, end_time, progress)
            else:
                order_ids = client.fetch_order_ids(start_time, end_time)
                orders = []
                total = len(order_ids)
                for index, order_id in enumerate(order_ids, 1):
                    if progress:
                        progress(f"正在拉取订单详情 {index}/{total}", index, total)
                    order = client.fetch_order(store, order_id)
                    self.db.upsert_order(order)
                    orders.append(order)
            if isinstance(client, MockShopClient):
                for index, order in enumerate(orders, 1):
                    self.db.upsert_order(order)
                    if progress:
                        progress(f"正在保存订单 {index}/{len(orders)}", index, len(orders))
            self.db.touch_store_sync(store.id)
            message = f"同步完成，共 {len(orders)} 个订单"
            self.db.finish_sync_run(run_id, "success", len(orders), message)
            return SyncResult("orders", len(orders), message)
        except Exception as exc:
            self.db.finish_sync_run(run_id, "failed", 0, str(exc))
            raise

    def _ensure_today_orders_cached(
        self,
        store: Store,
        start_time: int,
        end_time: int,
    ) -> bool:
        """Backfill today's order states once so sales metrics use real status."""
        business_day = datetime.fromtimestamp(start_time).date().isoformat()
        setting_key = f"orders_day_backfill_{store.id}"
        if self.db.get_setting(setting_key, "") == business_day:
            return False
        try:
            client = self.client_for(store)
        except ValueError:
            return False
        if isinstance(client, MockShopClient):
            return False
        self.sync_orders(store, start_time, end_time)
        self.db.set_setting(setting_key, business_day)
        return True

    def update_order_notes(
        self,
        store: Store,
        order_id: str,
        notes: str,
        color: int = 0,
    ) -> None:
        client = self.client_for(store)
        if not isinstance(client, MockShopClient):
            client.update_order_notes(order_id, notes, color)
        self.db.update_order_note(store.id, order_id, notes)

    def update_order_price(
        self,
        store: Store,
        order_id: str,
        change_infos: list[dict[str, Any]],
        change_express: bool,
        express_fee: int,
    ) -> None:
        client = self.client_for(store)
        if not isinstance(client, MockShopClient):
            client.update_order_price(
                {
                    "order_id": order_id,
                    "change_order_infos": change_infos,
                    "change_express": change_express,
                    "express_fee": express_fee if change_express else 0,
                }
            )
            order = client.fetch_order(store, order_id)
            self.db.upsert_order(order)

    def request_order_address_change(
        self,
        store: Store,
        order_id: str,
        address: dict[str, str],
    ) -> None:
        client = self.client_for(store)
        if isinstance(client, MockShopClient):
            return
        client.update_order_address(
            {
                "order_id": order_id,
                "user_address": address,
            }
        )

    def update_order_delivery_info(
        self,
        store: Store,
        order_id: str,
        waybill_id: str,
        delivery_id: str,
        product_infos: list[dict[str, Any]],
    ) -> None:
        client = self.client_for(store)
        if isinstance(client, MockShopClient):
            return
        client.update_order_delivery_info(
            {
                "order_id": order_id,
                "delivery_list": [
                    {
                        "deliver_type": 1,
                        "delivery_id": delivery_id,
                        "waybill_id": waybill_id,
                        "product_infos": product_infos,
                    }
                ],
            }
        )
        self.db.update_order_delivery(
            store.id,
            order_id,
            waybill_id,
            delivery_id,
            21,
            order_status_text(21),
        )

    def update_order_delivery_packages(
        self,
        store: Store,
        order_id: str,
        packages: list[dict[str, str]],
        product_infos: list[dict[str, Any]],
    ) -> None:
        if not packages:
            raise ValueError("至少需要一个店铺包裹")
        client = self.client_for(store)
        if isinstance(client, MockShopClient):
            return
        client.update_order_delivery_info(
            {
                "order_id": order_id,
                "delivery_list": [
                    {
                        "deliver_type": 1,
                        "delivery_id": str(package["delivery_id"]),
                        "waybill_id": str(package["waybill_id"]),
                        "product_infos": product_infos,
                    }
                    for package in packages
                ],
            }
        )
        first = packages[0]
        self.db.update_order_delivery(
            store.id,
            order_id,
            str(first["waybill_id"]),
            str(first["delivery_id"]),
            21,
            order_status_text(21),
        )

    def aftersale_action(
        self,
        store: Store,
        after_sale_id: str,
        action: str,
        payload: dict[str, Any] | None = None,
    ) -> SyncResult:
        client = self.client_for(store)
        payload = dict(payload or {})
        payload["after_sale_order_id"] = after_sale_id
        if isinstance(client, MockShopClient):
            return SyncResult(
                f"aftersale_{action}",
                1,
                "演示店铺操作成功",
                success=1,
            )
        methods = {
            "accept": client.accept_after_sale,
            "reject": client.reject_after_sale,
            "negotiate": client.negotiate_after_sale,
            "reship": client.reship_after_sale,
            "update_reship": client.update_after_sale_reship,
            "accept_exchange": client.accept_after_sale_exchange,
            "reject_exchange": client.reject_after_sale_exchange,
        }
        if action not in methods:
            raise ValueError("不支持的售后操作")
        methods[action](payload)
        try:
            detail = client.fetch_after_sale(after_sale_id)
            self.db.upsert_after_sale(store.id, detail)
        except Exception:
            pass
        return SyncResult(
            f"aftersale_{action}",
            1,
            "售后操作已提交",
            success=1,
        )

    def upload_after_sale_media(
        self,
        store: Store,
        paths: list[str],
    ) -> list[str]:
        client = self.client_for(store)
        if isinstance(client, MockShopClient):
            return [f"demo://after-sale-proof/{Path(path).name}" for path in paths]
        return [client.upload_product_image(path) for path in paths]

    @staticmethod
    def _uses_hosted_product_image(url: str) -> bool:
        host = (urlparse(str(url or "")).hostname or "").lower()
        return (
            host == "mmecimage.cn"
            or host.endswith(".mmecimage.cn")
            or host.endswith(".wxqcloud.qq.com.cn")
            or host.endswith(".wxapp.tc.qq.com")
        )

    @staticmethod
    def _valid_express_info(value: Any) -> bool:
        if not isinstance(value, dict):
            return False
        template_id = str(value.get("template_id") or "").strip()
        return bool(template_id and template_id != "0")

    @staticmethod
    def _valid_after_sale_info(value: Any) -> bool:
        if not isinstance(value, dict):
            return False
        address_id = str(value.get("after_sale_address_id") or "").strip()
        return bool(address_id and address_id != "0")

    def _product_payload_defaults(
        self,
        store: Store,
        client: WechatShopClient | None = None,
    ) -> dict[str, Any]:
        cached = self._product_payload_defaults_cache.get(store.id)
        if cached is not None:
            return cached
        defaults: dict[str, Any] = {}
        with self.db.connect() as connection:
            rows = connection.execute(
                "SELECT raw_json FROM products "
                "WHERE store_id=? AND status=5 "
                "ORDER BY synced_at DESC LIMIT 20",
                (store.id,),
            ).fetchall()
        for row in rows:
            try:
                payload = json.loads(row["raw_json"] or "{}")
            except (TypeError, ValueError):
                continue
            if not isinstance(payload, dict):
                continue
            for key in (
                "express_info",
                "after_sale_info",
                "deliver_acct_type",
                "limited_info",
                "size_chart",
                "hide_in_window",
                "release_mode",
                "head_videos",
            ):
                if defaults.get(key) in (None, {}, []) and payload.get(key):
                    defaults[key] = deepcopy(payload[key])
            if len(defaults.get("head_imgs") or []) < 3:
                defaults["head_imgs"] = deepcopy(payload.get("head_imgs") or [])
            if not (defaults.get("desc_info") or {}).get("imgs"):
                defaults["desc_info"] = deepcopy(payload.get("desc_info") or {})
            if (
                self._valid_express_info(defaults.get("express_info"))
                and self._valid_after_sale_info(defaults.get("after_sale_info"))
                and len(defaults.get("head_imgs") or []) >= 3
            ):
                self._product_payload_defaults_cache[store.id] = defaults
                return defaults
        if client is not None and (
            not self._valid_express_info(defaults.get("express_info"))
            or not self._valid_after_sale_info(defaults.get("after_sale_info"))
            or len(defaults.get("head_imgs") or []) < 3
        ):
            fetch_ids = getattr(client, "fetch_product_ids", None)
            fetch_product = getattr(client, "fetch_product", None)
            if callable(fetch_ids) and callable(fetch_product):
                try:
                    for product_id in list(fetch_ids(5))[:3]:
                        product = fetch_product(store, product_id)
                        remote = getattr(product, "raw", {})
                        if not isinstance(remote, dict):
                            continue
                        for key in (
                            "express_info",
                            "after_sale_info",
                            "deliver_acct_type",
                            "limited_info",
                            "size_chart",
                            "hide_in_window",
                            "release_mode",
                            "head_videos",
                        ):
                            if not defaults.get(key) and remote.get(key):
                                defaults[key] = deepcopy(remote[key])
                        if len(defaults.get("head_imgs") or []) < 3:
                            defaults["head_imgs"] = deepcopy(remote.get("head_imgs") or [])
                        if len(defaults.get("desc_info", {}).get("imgs") or []) == 0:
                            defaults["desc_info"] = deepcopy(
                                remote.get("desc_info") or defaults.get("desc_info")
                            )
                        if (
                            self._valid_express_info(defaults.get("express_info"))
                            and self._valid_after_sale_info(defaults.get("after_sale_info"))
                            and len(defaults.get("head_imgs") or []) >= 3
                        ):
                            break
                except Exception:
                    pass
        if client is not None:
            if not self._valid_express_info(defaults.get("express_info")):
                fetch_freight = getattr(
                    client,
                    "fetch_default_freight_template",
                    None,
                )
                if callable(fetch_freight):
                    try:
                        template = fetch_freight()
                    except Exception:
                        template = {}
                    template_id = str((template or {}).get("template_id") or "").strip()
                    if template_id and template_id != "0":
                        defaults["express_info"] = {
                            "template_id": template_id,
                            "weight": 1000,
                            "express_type": 0,
                        }
            if not self._valid_after_sale_info(defaults.get("after_sale_info")):
                fetch_address = getattr(
                    client,
                    "fetch_default_after_sale_address",
                    None,
                )
                if callable(fetch_address):
                    try:
                        address = fetch_address()
                    except Exception:
                        address = {}
                    address_id = str((address or {}).get("address_id") or "").strip()
                    if address_id and address_id != "0":
                        defaults["after_sale_info"] = {
                            "after_sale_address_id": address_id,
                        }
        if defaults.get("deliver_method") is None:
            defaults["deliver_method"] = 0
        if defaults.get("deliver_acct_type") is None:
            defaults["deliver_acct_type"] = []
        self._product_payload_defaults_cache[store.id] = defaults
        return defaults

    @staticmethod
    def _category_leaf_id(payload: dict[str, Any]) -> str:
        for key in ("cats_v2", "cats"):
            values = payload.get(key)
            if not isinstance(values, list):
                continue
            for value in reversed(values):
                category_id = value.get("cat_id") if isinstance(value, dict) else value
                category_id = str(category_id or "").strip()
                if category_id.isdigit():
                    return category_id
        return ""

    @staticmethod
    def _attribute_options(attribute: dict[str, Any]) -> list[str]:
        return [
            value.strip() for value in str(attribute.get("value") or "").split(";") if value.strip()
        ]

    @staticmethod
    def _infer_material(
        payload: dict[str, Any],
        options: list[str],
        category_name: str = "",
    ) -> str:
        text = " ".join(
            str(value or "")
            for value in (
                payload.get("title"),
                payload.get("short_title"),
                (payload.get("desc_info") or {}).get("desc"),
                category_name,
            )
        )
        synonyms = (
            ("聚酯纤维", ("聚酯纤维",)),
            ("聚酯", ("聚酯纤维",)),
            ("锦纶", ("尼龙", "聚酰胺纤维")),
            ("聚酰胺", ("尼龙", "聚酰胺纤维")),
            ("尼龙", ("尼龙",)),
            ("涤纶", ("涤纶",)),
            ("冰丝", ("冰丝",)),
            ("氨纶", ("氨纶",)),
            ("腈纶", ("腈纶",)),
            ("莱赛尔", ("莱赛尔纤维",)),
            ("粘胶", ("粘胶纤维",)),
            ("真丝", ("真丝", "桑蚕丝")),
            ("羊毛", ("羊毛",)),
            ("羊绒", ("羊绒",)),
            ("棉", ("棉",)),
            ("塑料", ("塑料",)),
        )
        for keyword, preferred in synonyms:
            if keyword not in text:
                continue
            for option in options:
                if any(value in option for value in preferred):
                    return option
            return preferred[0]
        fallbacks = (
            "其他",
            "涤纶",
            "聚酯纤维",
            "尼龙",
            "棉",
        )
        for preferred in fallbacks:
            match = next(
                (option for option in options if preferred in option),
                "",
            )
            if match:
                return match
        return options[0] if options else ""

    @classmethod
    def _required_attribute_value(
        cls,
        attribute: dict[str, Any],
        payload: dict[str, Any],
        category_name: str = "",
    ) -> str:
        name = str(attribute.get("name") or "").strip()
        attribute_type = str(attribute.get("type_v2") or attribute.get("type") or "string")
        options = cls._attribute_options(attribute)
        text = " ".join(
            str(value or "")
            for value in (
                payload.get("title"),
                payload.get("short_title"),
                (payload.get("desc_info") or {}).get("desc"),
                category_name,
            )
        )
        material = cls._infer_material(
            payload,
            options,
            category_name,
        )
        if "unit" in attribute_type:
            units = cls._attribute_options(attribute)
            unit = units[0] if units else ""
            return cls._normalize_attribute_value(
                attribute,
                cls._measure_value(payload, name, unit),
                payload,
                category_name,
            )
        if "成分" in name or "含量" in name:
            if material:
                return f"100%{material}"
            return "100%其他"
        if any(word in name for word in ("材质", "面料")):
            return material or (options[0] if options else "其他")
        exact_option = next(
            (
                option
                for option in sorted(options, key=len, reverse=True)
                if len(option) > 1 and option in text
            ),
            "",
        )
        if exact_option:
            return exact_option
        if "适用对象" in name:
            for preferred in ("成年人", "老少皆宜", "成人"):
                match = next(
                    (option for option in options if preferred in option),
                    "",
                )
                if match:
                    return match
        if "尺码" in name and attribute_type == "string":
            return "均码"
        if "尺码" in name and options:
            match = next(
                (option for option in options if option == "均码"),
                "",
            )
            if match:
                return match
        if "产地" in name:
            match = next(
                (option for option in options if "中国大陆" in option),
                "",
            )
            if match:
                return match
            return "中国大陆"
        if "保质期" in name:
            return "12个月"
        if any(word in name for word in ("颜色", "色系")):
            return "多色"
        if any(word in name for word in ("风格", "款式")):
            return "简约"
        if any(word in name for word in ("型号", "编号", "货号")):
            return str(payload.get("out_product_id") or payload.get("spu_code") or "无")
        if options:
            for preferred in ("其他", "无", "常规", "均码"):
                match = next(
                    (option for option in options if option == preferred),
                    "",
                )
                if match:
                    return match
            return options[0]
        if attribute_type == "boolean":
            return "否"
        if attribute_type == "date":
            return datetime.now().date().isoformat()
        if attribute_type == "datetime":
            return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if attribute_type.startswith("integer"):
            return "1"
        if attribute_type.startswith("decimal"):
            return "1"
        return str(attribute.get("hint") or "无")

    @classmethod
    def _normalize_attribute_value(
        cls,
        attribute: dict[str, Any],
        value: Any,
        payload: dict[str, Any],
        category_name: str = "",
    ) -> str:
        """Normalize numeric category attributes to the official wire format."""
        attribute_type = str(attribute.get("type_v2") or attribute.get("type") or "string")
        raw = str(value or "").strip()
        name = str(attribute.get("name") or "").strip()
        if "unit" in attribute_type:
            units = cls._attribute_options(attribute)
            lowered = raw.casefold()
            unit = next(
                (candidate for candidate in units if candidate and candidate.casefold() in lowered),
                units[0] if units else "",
            )
            if not unit:
                return raw
            if not raw:
                raw = cls._measure_value(payload, name, unit)
            number = re.search(r"-?\d+(?:\.\d+)?", raw)
            if number is None:
                generated = cls._measure_value(payload, name, unit)
                number = re.search(
                    r"-?\d+(?:\.\d+)?",
                    generated,
                )
            numeric = number.group(0) if number else "1"
            try:
                if float(numeric) <= 0:
                    numeric = "1"
                if attribute_type.startswith("integer"):
                    numeric = str(int(float(numeric)))
            except ValueError:
                numeric = "1"
            return f"{numeric} {unit}".strip()
        if attribute_type.startswith("integer") or attribute_type.startswith("decimal"):
            number = re.search(r"-?\d+(?:\.\d+)?", raw)
            numeric = number.group(0) if number else "1"
            try:
                if float(numeric) <= 0:
                    numeric = "1"
                if attribute_type.startswith("integer"):
                    numeric = str(int(float(numeric)))
            except ValueError:
                numeric = "1"
            return numeric
        return raw

    @staticmethod
    def _measure_value(
        payload: dict[str, Any],
        name: str,
        unit: str,
    ) -> str:
        text = " ".join(
            str(value or "")
            for value in (
                payload.get("title"),
                payload.get("short_title"),
                (payload.get("desc_info") or {}).get("desc"),
            )
        )
        aliases: dict[str, tuple[str, ...]] = {
            "%": ("%", "％"),
            "cm": ("cm", "厘米"),
            "mm": ("mm", "毫米"),
            "m": ("m", "米"),
            "kg": ("kg", "千克", "公斤"),
            "g": ("g", "克"),
            "l": ("l", "L", "升"),
            "ml": ("ml", "mL", "毫升"),
            "w": ("w", "W", "瓦"),
            "v": ("v", "V", "伏"),
        }
        lookup = aliases.get(unit.lower(), (unit,))
        for alias in lookup:
            match = re.search(
                rf"(\d+(?:\.\d+)?)\s*{re.escape(alias)}",
                text,
            )
            if match:
                number = match.group(1)
                return f"{number} {unit}".strip()
        defaults = {
            "%": "100",
            "cm": "1",
            "mm": "1",
            "m": "1",
            "kg": "1",
            "g": "1",
            "l": "1",
            "ml": "1",
            "w": "1",
            "v": "1",
        }
        return f"{defaults.get(unit.lower(), '1')} {unit}".strip()

    def _apply_required_category_attributes(
        self,
        store: Store,
        client: WechatShopClient,
        payload: dict[str, Any],
    ) -> None:
        category_id = self._category_leaf_id(payload)
        if not category_id:
            return
        brand_id = str(
            payload.get("brand_id") or "2100000000"
        ).strip()
        payload["brand_id"] = brand_id
        cache_key = (store.id, category_id, brand_id)
        rule = self._category_rule_cache.get(cache_key)
        if rule is None:
            fetch_rule = getattr(
                client,
                "fetch_category_product_rule",
                None,
            )
            if not callable(fetch_rule):
                return
            try:
                rule = fetch_rule(category_id, brand_id=brand_id)
            except Exception:
                return
            self._category_rule_cache[cache_key] = rule
        attrs = payload.get("attrs")
        attrs = list(attrs) if isinstance(attrs, list) else []
        normalized_attrs: list[dict[str, Any]] = []
        existing: dict[str, dict[str, Any]] = {}
        for item in attrs:
            if not isinstance(item, dict):
                continue
            key = str(item.get("attr_key") or "").strip()
            if not key:
                continue
            # brand_id is the official brand field. Keeping a second "品牌"
            # attribute makes WeChat reject product/add with 6600044.
            if key == "品牌" and brand_id:
                continue
            normalized = {
                "attr_key": key,
                "attr_value": item.get("attr_value"),
            }
            current = existing.get(key)
            if current is not None:
                current.update(normalized)
                continue
            normalized_attrs.append(normalized)
            existing[key] = normalized
        attrs = normalized_attrs
        category_name = str((rule.get("info") or {}).get("name") or "")
        for attribute in rule.get("product_attr_list") or []:
            if not isinstance(attribute, dict):
                continue
            name = str(attribute.get("name") or "").strip()
            required_rule = attribute.get("required_rule") or {}
            is_required = bool(
                attribute.get("is_required")
                or (
                    isinstance(required_rule, dict)
                    and int(required_rule.get("rule_type") or 0) == 1
                )
            )
            if not name:
                continue
            if name == "品牌" and brand_id:
                continue
            current = existing.get(name)
            if current is not None:
                current["attr_value"] = self._normalize_attribute_value(
                    attribute,
                    current.get("attr_value"),
                    payload,
                    category_name,
                )
                continue
            if not is_required:
                continue
            value = self._required_attribute_value(
                attribute,
                payload,
                category_name,
            )
            if not value:
                continue
            attrs.append({"attr_key": name, "attr_value": value})
            existing[name] = attrs[-1]
        payload["attrs"] = attrs

    def _prepare_product_payload(
        self,
        store: Store,
        client: WechatShopClient,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        prepared = deepcopy(payload)
        defaults = self._product_payload_defaults(store, client)
        if not self._valid_express_info(prepared.get("express_info")):
            prepared["express_info"] = deepcopy(defaults.get("express_info") or {})
        if not self._valid_after_sale_info(prepared.get("after_sale_info")):
            prepared["after_sale_info"] = deepcopy(defaults.get("after_sale_info") or {})
        for key in (
            "deliver_acct_type",
            "limited_info",
            "after_sale_info",
            "size_chart",
            "hide_in_window",
            "release_mode",
            "head_videos",
        ):
            if key not in prepared and key in defaults:
                prepared[key] = deepcopy(defaults[key])
        if prepared.get("deliver_method") is None:
            prepared["deliver_method"] = defaults.get("deliver_method", 0)
        if prepared.get("deliver_acct_type") is None:
            prepared["deliver_acct_type"] = deepcopy(defaults.get("deliver_acct_type") or [])
        if prepared.get("listing") is None:
            prepared["listing"] = 1
        if not self._valid_express_info(prepared.get("express_info")):
            raise ValueError(f"{store.name}未获取到运费模板ID，请先在微信小店后台配置默认运费模板")
        if not self._valid_after_sale_info(prepared.get("after_sale_info")):
            raise ValueError(f"{store.name}未获取到售后地址ID，请先在微信小店后台配置默认收货地址")
        self._apply_required_category_attributes(
            store,
            client,
            prepared,
        )

        image_cache: dict[str, str] = {}

        def prepare_url(value: Any) -> str:
            url = str(value or "").strip()
            if not url:
                return ""
            if self._uses_hosted_product_image(url):
                return url
            if url not in image_cache:
                image_cache[url] = client.upload_product_image_url(url)
            return image_cache[url]

        head_images = [
            upload
            for upload in (prepare_url(url) for url in prepared.get("head_imgs") or [])
            if upload
        ]
        fallback_heads = [
            str(url)
            for url in defaults.get("head_imgs") or []
            if self._uses_hosted_product_image(str(url))
        ]
        for url in fallback_heads:
            if url not in head_images:
                head_images.append(url)
            if len(head_images) >= 3:
                break
        prepared["head_imgs"] = list(dict.fromkeys(head_images))[:9]
        if len(prepared["head_imgs"]) < 3:
            raise ValueError(f"{store.name}商品主图少于3张：当前仅 {len(prepared['head_imgs'])} 张")

        desc_info = dict(prepared.get("desc_info") or {})
        detail_images = [
            upload for upload in (prepare_url(url) for url in desc_info.get("imgs") or []) if upload
        ]
        if not detail_images:
            fallback_details = [
                str(url)
                for url in (defaults.get("desc_info") or {}).get("imgs") or []
                if self._uses_hosted_product_image(str(url))
            ]
            detail_images = fallback_details[:1]
        if not detail_images and prepared["head_imgs"]:
            detail_images = prepared["head_imgs"][:1]
        desc_info["imgs"] = list(dict.fromkeys(detail_images))[:50]
        prepared["desc_info"] = desc_info
        return prepared

    def product_write(
        self,
        store: Store,
        action: str,
        payload: dict[str, Any] | None = None,
        product_id: str = "",
        client: WechatShopClient | MockShopClient | None = None,
        listing_mode: str = "",
    ) -> dict[str, Any]:
        if (
            client is None
            and action == "delete"
            and store.app_id != "demo-store"
            and not self.db.get_store_secret(store.id)[1]
        ):
            row = self.db.get_product(store.id, product_id)
            way = "real" if row and int(row["status"]) == 6 else "recycle"
            result = self.private_batch_product_action(store, [product_id], way)
            self.db.remove_product_cache(store.id, product_id)
            return result
        client = client or self.client_for(store)
        if isinstance(client, MockShopClient):
            return {"errcode": 0, "demo": True}
        if action == "add":
            prepared = self._prepare_product_payload(
                store,
                client,
                payload or {},
            )
            if listing_mode == "draft":
                prepared["listing"] = 0
            elif listing_mode:
                prepared["listing"] = 1
            result = client.add_product(prepared)
        elif action == "update":
            result = client.update_product(payload or {})
        elif action == "delete":
            result = client.delete_product(product_id)
        elif action == "cancel_audit":
            result = client.cancel_product_audit(product_id)
        else:
            raise ValueError("不支持的商品操作")
        if int(result.get("errcode") or 0) != 0:
            raise RuntimeError(str(result.get("errmsg") or "微信商品接口返回失败"))
        if action == "add" and int(result.get("errcode") or 0) == 0:
            self.db.adjust_upload_metrics(
                store.id,
                product_delta=1,
                quota_delta=(0 if listing_mode == "draft" else -1),
            )
        if action == "delete":
            self.db.remove_product_cache(store.id, product_id)
        elif action in {"add", "update"}:
            result_data = result.get("data")
            result_data = result_data if isinstance(result_data, dict) else {}
            result_product_id = str(
                result.get("product_id")
                or result_data.get("product_id")
                or (payload or {}).get("product_id")
                or ""
            )
            if result_product_id and not result.get("product_id"):
                result["product_id"] = result_product_id
            if result_product_id:
                if action == "add" and listing_mode == "warehouse":
                    try:
                        result["warehouse_result"] = client.set_product_listing(
                            result_product_id,
                            False,
                        )
                        self.db.mark_product_status(
                            store.id,
                            result_product_id,
                            11,
                            PRODUCT_STATUS[11],
                        )
                    except Exception as exc:
                        result["warehouse_error"] = str(exc)
                try:
                    product = client.fetch_product(store, result_product_id)
                    self.db.upsert_product(product)
                    self.db.mark_product_catalog_done(store.id, result_product_id)
                except Exception:
                    pass
        return result

    def upload_product_images(
        self,
        store: Store,
        paths: list[str],
    ) -> list[str]:
        client = self.client_for(store)
        if isinstance(client, MockShopClient):
            return [f"https://example.invalid/demo-{index}.png" for index, _ in enumerate(paths, 1)]
        return [client.upload_product_image(path) for path in paths]

    def sync_recent_order_updates(
        self,
        store: Store,
        start_time: int,
        end_time: int,
    ) -> SyncResult:
        client = self.client_for(store)
        if isinstance(client, MockShopClient):
            return SyncResult("order_updates", 0, "演示店铺无订单更新")
        ids = client.fetch_order_ids_by_update(start_time, end_time)
        failures = []
        for order_id in ids:
            try:
                self.db.upsert_order(client.fetch_order(store, order_id))
            except Exception as exc:
                failures.append(f"{order_id}: {exc}")
        return SyncResult(
            "order_updates",
            len(ids),
            f"订单增量：{len(ids) - len(failures)} 条更新",
            success=len(ids) - len(failures),
            failed=len(failures),
            errors=failures[:20],
        )

    def sync_order(
        self,
        store: Store,
        order_id: str,
    ) -> SyncResult:
        client = self.client_for(store)
        if isinstance(client, MockShopClient):
            return SyncResult(
                "order",
                0,
                "演示店铺不刷新单个订单",
                success=0,
            )
        order = client.fetch_order(store, str(order_id))
        self.db.upsert_order(order)
        self.db.sync_auto_dispatch_tasks()
        return SyncResult(
            "order",
            1,
            f"订单 {order_id} 已刷新",
            success=1,
        )

    def sync_products(
        self,
        store: Store,
        progress: ProgressCallback | None = None,
        max_items: int | None = None,
        workers: int = 4,
        refresh_catalog: bool = False,
        cancel_event: threading.Event | None = None,
    ) -> SyncResult:
        scan_state = self.db.get_product_scan_state(store.id)
        if refresh_catalog or not scan_state or scan_state["status"] != "completed":
            catalog_result = self.scan_product_catalog(
                store,
                progress,
                cancel_event=cancel_event,
                reset=refresh_catalog,
            )
            if catalog_result.cancelled:
                return catalog_result
            if store.app_id == "demo-store":
                return catalog_result
        return self.sync_product_details(
            store,
            progress,
            max_items=max_items,
            workers=workers,
            cancel_event=cancel_event,
        )

    def scan_product_catalog(
        self,
        store: Store,
        progress: ProgressCallback | None = None,
        cancel_event: threading.Event | None = None,
        reset: bool = False,
    ) -> SyncResult:
        run_id = self.db.start_sync_run(store.id, "product_catalog")
        state = self.db.begin_product_scan(
            store.id,
            uuid.uuid4().hex,
            reset=reset,
        )
        scan_id = str(state["scan_id"])
        next_key = str(state["next_key"] or "")
        discovered = int(state["discovered"] or 0)
        try:
            client = self.client_for(store)
            if isinstance(client, MockShopClient):
                products = client.fetch_products(progress)
                product_ids = [product.product_id for product in products]
                discovered = self.db.save_product_scan_page(
                    store.id,
                    scan_id,
                    product_ids,
                    "",
                )
                for product in products:
                    self.db.upsert_product(product)
                    self.db.mark_product_catalog_done(store.id, product.product_id)
            else:
                page_number = 0
                while True:
                    if cancel_event and cancel_event.is_set():
                        raise _TaskCancelled
                    page_number += 1
                    page = client.fetch_product_id_page(next_key)
                    discovered = self.db.save_product_scan_page(
                        store.id,
                        scan_id,
                        page.ids,
                        page.next_key,
                    )
                    if progress:
                        progress(
                            f"正在刷新商品目录：第 {page_number} 页，已发现 {discovered} 个商品",
                            0,
                            0,
                        )
                    next_key = page.next_key
                    if not next_key:
                        break
            self.db.complete_product_scan(store.id, scan_id)
            self.db.touch_store_sync(store.id)
            message = f"商品目录刷新完成，共发现 {discovered} 个商品"
            self.db.finish_sync_run(run_id, "success", discovered, message)
            return SyncResult(
                "product_catalog",
                discovered,
                message,
                success=discovered,
            )
        except _TaskCancelled:
            message = f"商品目录刷新已暂停，已保存 {discovered} 个商品，可继续"
            self.db.finish_sync_run(run_id, "cancelled", discovered, message)
            return SyncResult(
                "product_catalog",
                discovered,
                message,
                success=discovered,
                cancelled=True,
            )
        except Exception as exc:
            self.db.finish_sync_run(run_id, "failed", 0, str(exc))
            raise

    def sync_product_details(
        self,
        store: Store,
        progress: ProgressCallback | None = None,
        max_items: int | None = None,
        workers: int = 4,
        cancel_event: threading.Event | None = None,
        include_failed: bool = True,
    ) -> SyncResult:
        run_id = self.db.start_sync_run(store.id, "product_details")
        product_ids = self.db.list_pending_product_ids(
            store.id,
            limit=max_items,
            include_failed=include_failed,
        )
        total = len(product_ids)
        if not product_ids:
            message = "没有待同步的商品详情"
            self.db.finish_sync_run(run_id, "success", 0, message)
            return SyncResult("product_details", 0, message)

        client = self.client_for(store)
        completed_count = 0
        success_count = 0
        failures: list[str] = []
        cancelled = False

        def fetch_one(product_id: str) -> tuple[str, str | None]:
            if cancel_event and cancel_event.is_set():
                return product_id, "cancelled"
            try:
                product = client.fetch_product(store, product_id)
                self.db.upsert_product(product)
                self.db.mark_product_catalog_done(store.id, product_id)
                return product_id, None
            except Exception as exc:
                self.db.mark_product_catalog_failed(store.id, product_id, str(exc))
                return product_id, str(exc)

        safe_workers = max(1, min(int(workers), 8))
        executor = ThreadPoolExecutor(
            max_workers=safe_workers,
            thread_name_prefix="product-sync",
        )
        futures = {executor.submit(fetch_one, product_id): product_id for product_id in product_ids}
        try:
            for future in as_completed(futures):
                if cancel_event and cancel_event.is_set():
                    cancelled = True
                    for pending in futures:
                        pending.cancel()
                if future.cancelled():
                    continue
                try:
                    product_id, error = future.result()
                except CancelledError:
                    continue
                completed_count += 1
                if error is None:
                    success_count += 1
                elif error != "cancelled":
                    failures.append(f"{product_id}: {error}")
                if progress:
                    progress(
                        f"正在同步商品详情 {completed_count}/{total}",
                        completed_count,
                        total,
                    )
        finally:
            executor.shutdown(wait=True, cancel_futures=True)

        cancelled_count = sum(1 for future in futures if future.cancelled())
        failed_count = max(0, total - success_count - cancelled_count)
        if cancelled:
            message = f"商品详情同步已暂停：成功 {success_count}，失败 {failed_count}，可继续"
            self.db.finish_sync_run(run_id, "cancelled", success_count, message)
            return SyncResult(
                "product_details",
                total,
                message,
                success=success_count,
                failed=failed_count,
                cancelled=True,
                errors=failures[:20],
            )

        message = f"商品详情同步完成：成功 {success_count}，失败 {len(failures)}"
        status = "success" if not failures else "partial"
        self.db.finish_sync_run(run_id, status, success_count, message)
        self.db.touch_store_sync(store.id)
        return SyncResult(
            "product_details",
            total,
            message,
            success=success_count,
            failed=len(failures),
            errors=failures[:20],
        )

    def refresh_product_status_snapshot(
        self,
        store: Store,
        progress: ProgressCallback | None = None,
        cancel_event: threading.Event | None = None,
    ) -> SyncResult:
        try:
            private = ShopPrivateClient(self.db, store)
            if progress:
                progress("正在读取微信商家后台商品状态", 0, 1)
            snapshot = private.status_counts()
        except ShopPrivateError:
            client = self.client_for(store)
            if isinstance(client, MockShopClient):
                products = client.fetch_products(progress)
                snapshot = {
                    "sales": sum(product.status == 5 for product in products),
                    "offline": sum(
                        product.status in {11, 12, 13, 14, 15, 20} for product in products
                    ),
                    "recycle": sum(product.status == 6 for product in products),
                }
            else:
                snapshot = {}
                groups = (
                    ("sales", (5,)),
                    ("offline", (11, 12, 13, 14, 15, 20)),
                    ("recycle", (6,)),
                )
                for group_index, (tab_key, statuses) in enumerate(groups, 1):
                    if cancel_event and cancel_event.is_set():
                        raise _TaskCancelled
                    if progress:
                        progress(
                            f"正在读取官方商品状态：{tab_key}",
                            group_index - 1,
                            len(groups),
                        )
                    snapshot[tab_key] = sum(
                        len(client.fetch_product_ids(status)) for status in statuses
                    )
        self.db.upsert_product_status_snapshot(store.id, snapshot)
        try:
            quota = self.fetch_product_audit_quota(store)
            self.db.set_setting(
                f"product_quota_{store.id}",
                json.dumps(quota, ensure_ascii=False),
            )
        except Exception:
            pass
        return SyncResult(
            "product_status_snapshot",
            sum(snapshot.values()),
            "服务端商品状态数量已刷新",
            success=sum(snapshot.values()),
        )

    def refresh_store_metrics(self, store: Store) -> dict[str, Any]:
        now = datetime.now()
        today = now.date()
        today_start = int(
            now.replace(
                hour=0,
                minute=0,
                second=0,
                microsecond=0,
            ).timestamp()
        )
        month_start = int(
            (now - timedelta(days=29))
            .replace(hour=0, minute=0, second=0, microsecond=0)
            .timestamp()
        )
        quota_error = ""
        errors: list[str] = []
        try:
            self._ensure_today_orders_cached(
                store,
                today_start,
                int(now.timestamp()),
            )
        except Exception as exc:
            errors.append(f"今日订单同步：{exc}")
        try:
            quota = self.fetch_product_audit_quota(store)
            self.db.set_setting(
                f"product_quota_{store.id}",
                json.dumps(quota, ensure_ascii=False),
            )
        except Exception as exc:
            quota_error = str(exc)
            try:
                quota = json.loads(
                    self.db.get_setting(
                        f"product_quota_{store.id}",
                        "{}",
                    )
                )
            except (TypeError, ValueError):
                quota = {}
        orders = self.db.list_orders(
            store_id=store.id,
            start_time=month_start,
            limit=100000,
        )
        valid = [
            row
            for row in orders
            if order_counts_as_transaction(row.get("status"))
        ]

        def buyer_identity(row: dict[str, Any]) -> str:
            try:
                raw = json.loads(row.get("raw_json") or "{}")
            except (TypeError, ValueError):
                raw = {}
            identity = (
                (raw.get("openid") if isinstance(raw, dict) else None)
                or row.get("buyer_name")
                or row.get("order_id")
            )
            return str(identity or "")

        buyer_ids = {identity for row in valid if (identity := buyer_identity(row))}
        today_orders = [
            row
            for row in valid
            if int(row.get("create_time") or 0) >= today_start
        ]
        analytics = self._analytics_days_for_metrics(store)
        analytics_by_date = {
            str(row.get("business_date")): row for row in analytics if row.get("business_date")
        }
        history = [
            row
            for business_date, row in analytics_by_date.items()
            if business_date < today.isoformat()
        ]
        week_start_date = today - timedelta(days=6)
        week_start_ts = int(
            datetime.combine(
                week_start_date,
                datetime.min.time(),
            ).timestamp()
        )
        yesterday_start_ts = int(
            datetime.combine(
                today - timedelta(days=1),
                datetime.min.time(),
            ).timestamp()
        )

        def history_sum(field: str) -> int:
            total = 0
            for row in history:
                total += int(row.get(field) or 0)
            return total

        summary = self.db.store_summary(store.id)
        history_clicks_30d = history_sum("product_click_uv")
        history_refund_30d = history_sum("pay_refund_gmv")
        yesterday_orders = [
            row
            for row in valid
            if yesterday_start_ts
            <= int(row.get("create_time") or 0)
            < today_start
        ]
        week_orders = [
            row
            for row in valid
            if int(row.get("create_time") or 0) >= week_start_ts
        ]
        transaction_gmv_today = sum(
            int(row.get("total_amount") or 0) for row in today_orders
        )
        transaction_gmv_week = sum(
            int(row.get("total_amount") or 0) for row in week_orders
        )
        transaction_gmv_month = sum(
            int(row.get("total_amount") or 0) for row in valid
        )
        metrics: dict[str, Any] = {
            "in_sale": summary["in_sale"],
            "warehouse": summary["warehouse"],
            "product_total": summary["in_sale"] + summary["warehouse"],
            "buyers_today": len(
                {buyer_identity(row) for row in today_orders if buyer_identity(row)}
            ),
            "buyer_30d": len(buyer_ids),
            "today_orders": len(today_orders),
            "orders_yesterday": len(yesterday_orders),
            "product_click_today": sum(
                int(row.get("product_click_uv") or 0)
                for row in analytics_by_date.values()
                if row.get("business_date") == today.isoformat()
            ),
            "visitors_30d": history_clicks_30d,
            "today_refund": sum(
                int(row.get("pay_refund_gmv") or 0)
                for row in analytics_by_date.values()
                if row.get("business_date") == today.isoformat()
            ),
            "refund_30d": history_refund_30d,
            "gmv_today": transaction_gmv_today,
            "gmv_yesterday": sum(
                int(row.get("total_amount") or 0)
                for row in yesterday_orders
            ),
            "gmv_7d": transaction_gmv_week,
            "gmv_30d": transaction_gmv_month,
            "aftersale": sum(
                "完成" not in str(row.get("status_text") or "")
                for row in self.db.list_after_sales(store.id)
            ),
            "warn": 0,
            "violation": 0,
            "cash": 0,
            "frozen": 0,
            "settle": 0,
            "deposit": 0,
            "dsr": None,
            "score_product": None,
            "score_deliver": None,
            "score_service": None,
            "good_eval_ratio": 0.0,
            "quality_refund_ratio": None,
            "wait_send": 0,
            "wait_receive": sum(int(row.get("status") or 0) == 30 for row in valid),
            "recommend_exposure": 0,
            "recommend_exposure_yesterday": 0,
            "search_exposure_yesterday": 0,
            "today_success": 0,
            "stats_at": now.strftime("%Y-%m-%d %H:%M:%S"),
            "updated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        }
        private_account_ok = False
        try:
            private = ShopPrivateClient(self.db, store)
            realtime = private.realtime_overview()
            today_click = int(float(realtime.get("product_click_uv") or 0))
            today_refund = int(float(realtime.get("pay_refund_gmv") or 0))
            metrics.update(
                {
                    "today_refund": today_refund,
                    "product_click_today": today_click,
                    "visitors_30d": int(metrics.get("visitors_30d") or 0) + today_click,
                    "refund_30d": int(metrics.get("refund_30d") or 0) + today_refund,
                }
            )
            metrics.update(private.todo_overview())
            metrics.update(private.score_overview())
            metrics.update(private.evaluation_overview())
            metrics.update(private.account_balance())
            private_account_ok = True
            metrics["deposit"] = private.deposit_balance()
            recommend_today = private.recommend_overview(False)
            recommend_yesterday = private.recommend_overview(True)
            metrics["recommend_exposure"] = recommend_today["exposure"]
            metrics["recommend_exposure_yesterday"] = recommend_yesterday["exposure"]
            search = private.search_overview(30)
            metrics["search_exposure_yesterday"] = search["search_exposure_yesterday"]
            try:
                metrics["today_success"] = private.audit_success_today()
            except Exception as exc:
                errors.append(f"今日审核：{exc}")
        except ShopPrivateError as exc:
            errors.append(f"商家后台：{exc}")
            if any(marker in str(exc) for marker in ("登录已失效", "请重新扫码", "拒绝请求")):
                self.db.update_web_status(store.id, "已掉线", str(exc))
        try:
            client = self.client_for(store)
            if not isinstance(client, MockShopClient):
                with ThreadPoolExecutor(max_workers=3) as executor:
                    count_jobs = {
                        "product_total": executor.submit(client.fetch_product_total, None),
                        "in_sale": executor.submit(client.fetch_product_total, 5),
                        "warehouse": executor.submit(client.fetch_product_total, 11),
                    }
                    for key, future in count_jobs.items():
                        try:
                            metrics[key] = int(future.result())
                        except Exception:
                            pass
                if not private_account_ok:
                    balance = client.fetch_fund_balance()
                    metrics["cash"] = int(float(balance.get("available_amount") or 0))
                    metrics["settle"] = int(float(balance.get("pending_amount") or 0))
        except Exception as exc:
            errors.append(f"资金余额：{exc}")
        if quota_error:
            errors.append(f"提审额度：{quota_error}")
        metrics["quota_remaining"] = int(quota.get("remaining") or 0)
        metrics["quota_total"] = int(quota.get("total") or 0)
        metrics["quota_error"] = quota_error
        metrics["product_total"] = max(
            int(metrics.get("product_total") or 0),
            int(metrics.get("in_sale") or 0) + int(metrics.get("warehouse") or 0),
        )
        metrics["orders_today"] = int(metrics.get("today_orders") or 0)
        metrics["refund_today"] = int(metrics.get("today_refund") or 0)
        metrics["_errors"] = errors
        self.db.set_setting(
            f"store_metrics_{store.id}",
            json.dumps(metrics, ensure_ascii=False),
        )
        return metrics

    def _analytics_days_for_metrics(self, store: Store) -> list[dict[str, Any]]:
        rows = self.db.list_analytics_days(store.id, 30)
        now_ts = int(datetime.now().timestamp())
        try:
            last_attempt = int(
                self.db.get_setting(f"analytics_product_backfill_at_{store.id}", "0")
            )
        except ValueError:
            last_attempt = 0
        if now_ts - last_attempt < 21600:
            return rows
        client = self.client_for(store)
        if isinstance(client, MockShopClient):
            return rows
        today = datetime.now().date()
        day_pairs = [
            (
                (today - timedelta(days=offset)).isoformat(),
                (today - timedelta(days=offset)).strftime("%Y%m%d"),
            )
            for offset in range(1, 30)
        ]

        def fetch_day(pair: tuple[str, str]) -> tuple[str, dict[str, int]]:
            business_date, api_day = pair
            return business_date, client.fetch_product_day_totals(api_day)

        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = {executor.submit(fetch_day, pair): pair[0] for pair in day_pairs}
            for future in as_completed(futures):
                business_date = futures[future]
                try:
                    _business_date, data = future.result()
                    self.db.upsert_analytics_day(
                        store.id,
                        business_date,
                        {
                            **data,
                            "_source": "product_metrics",
                        },
                    )
                except Exception:
                    continue
        self.db.set_setting(f"analytics_product_backfill_at_{store.id}", str(now_ts))
        return self.db.list_analytics_days(store.id, 30)

    def fetch_product_taglink(self, store: Store, product_id: str) -> str:
        client = self.client_for(store)
        if isinstance(client, MockShopClient):
            return f"demo://product-tag/{product_id}"
        result = client.fetch_product_taglink(product_id)
        for key in (
            "product_taglink",
            "product_tag_link",
            "taglink",
            "link",
            "url",
        ):
            value = result.get(key)
            if value:
                return str(value)
        data = result.get("data")
        if isinstance(data, dict):
            for key in (
                "product_taglink",
                "product_tag_link",
                "taglink",
                "link",
                "url",
            ):
                value = data.get(key)
                if value:
                    return str(value)
        return json.dumps(result, ensure_ascii=False)

    def fetch_product_audit_quota(self, store: Store) -> dict[str, int]:
        client = self.client_for(store)
        if isinstance(client, MockShopClient):
            return {"remaining": 500, "total": 500}
        result = client.fetch_product_audit_quota()
        data = result.get("data")
        source = result.get("audit_quota") or (data if isinstance(data, dict) else result)
        if isinstance(source, dict) and isinstance(source.get("audit_quota"), dict):
            source = source["audit_quota"]
        remaining = (
            source.get("audit_total_remaining")
            or source.get("avail_quota")
            or source.get("remaining")
            or source.get("publish_quota_remaining")
            or 0
        )
        total = (
            source.get("audit_total_quota")
            or source.get("total_quota")
            or source.get("total")
            or source.get("publish_quota_total")
            or 0
        )
        return {
            "remaining": int(remaining or 0),
            "total": int(total or 0),
        }

    def refresh_product_tab(
        self,
        store: Store,
        tab_key: str,
        progress: ProgressCallback | None = None,
        cancel_event: threading.Event | None = None,
    ) -> SyncResult:
        if self.db.get_web_cookies(store.id):
            try:
                return self.sync_private_product_tab(
                    store, tab_key, max_pages=None, progress=progress, cancel_event=cancel_event
                )
            except ShopPrivateError:
                if not self.db.get_store_secret(store.id)[1]:
                    raise
        client = self.client_for(store)
        products = []
        if isinstance(client, MockShopClient):
            products = client.fetch_products(progress)
        else:
            product_ids = client.fetch_product_ids()
            for index, product_id in enumerate(product_ids, start=1):
                if cancel_event is not None and cancel_event.is_set():
                    return SyncResult("product_query", 0, "已取消商品查询", cancelled=True)
                products.append(client.fetch_product(store, product_id))
                if progress:
                    progress(
                        f"正在读取商品详情 {index}/{len(product_ids)}", index, len(product_ids)
                    )
        selected = [
            product
            for product in products
            if product_matches_tab(
                {
                    "status": product.status,
                    "edit_status": product.edit_status,
                    "raw_json": json.dumps(product.raw),
                },
                tab_key,
            )
        ]
        if cancel_event is not None and cancel_event.is_set():
            return SyncResult("product_query", 0, "已取消商品查询", cancelled=True)
        self.db.replace_product_tab(store.id, tab_key, selected)
        return SyncResult("product_query", len(selected), "商品已从微信读取", success=len(selected))

    def sync_private_product_tab(
        self,
        store: Store,
        tab_key: str,
        max_pages: int | None = 5,
        progress: ProgressCallback | None = None,
        cancel_event: threading.Event | None = None,
    ) -> SyncResult:
        private = ShopPrivateClient(self.db, store)
        products = private.scan_products(
            tab_key,
            max_pages=max_pages,
            stop=(cancel_event.is_set if cancel_event else None),
            on_progress=(
                (
                    lambda _key, page, pages: progress(
                        f"正在读取商品页第 {page}/{pages} 页",
                        page,
                        pages,
                    )
                )
                if progress
                else None
            ),
        )
        if max_pages is None and cancel_event is not None and cancel_event.is_set():
            return SyncResult("private_product_tab", 0, "已取消商品查询", cancelled=True)
        for product in products:
            product.store_id = store.id
            product.store_name = store.name
        if max_pages is None:
            self.db.replace_product_tab(store.id, tab_key, products)
        elif products:
            self.db.upsert_products(products)
        return SyncResult(
            "private_product_tab",
            len(products),
            f"已更新商品页本地缓存，共 {len(products)} 个",
            success=len(products),
        )

    def sync_private_product_metrics(
        self,
        store: Store,
        product_ids: list[str],
        progress: ProgressCallback | None = None,
        cancel_event: threading.Event | None = None,
    ) -> SyncResult:
        private = ShopPrivateClient(self.db, store)
        metrics = private.product_metrics(
            product_ids,
            stop=(cancel_event.is_set if cancel_event else None),
        )
        for product_id, values in metrics.items():
            self.db.upsert_product_metrics(
                store.id,
                product_id,
                views30=values.get("views30", 0),
                sold30=values.get("sold30", 0),
            )
        return SyncResult(
            "private_product_metrics",
            len(metrics),
            f"已更新 {len(metrics)} 个商品的30天浏览数据",
            success=len(metrics),
        )

    def sync_product_performance(
        self,
        store: Store,
        product_ids: list[str],
        days: int = 30,
        progress: ProgressCallback | None = None,
        cancel_event: threading.Event | None = None,
    ) -> SyncResult:
        ids = [str(value) for value in dict.fromkeys(product_ids) if value]
        if not ids:
            return SyncResult(
                "product_performance",
                0,
                "没有需要更新经营数据的商品",
            )
        updated = 0
        try:
            private = ShopPrivateClient(self.db, store)
            metrics = private.product_metrics(
                ids,
                stop=(cancel_event.is_set if cancel_event else None),
            )
            for product_id, values in metrics.items():
                self.db.upsert_product_metrics(
                    store.id,
                    product_id,
                    views30=values.get("views30", 0),
                    sold30=values.get("sold30", 0),
                )
                updated += 1
        except ShopPrivateError:
            pass
        client = self.client_for(store)
        if isinstance(client, MockShopClient):
            return SyncResult(
                "product_performance",
                updated,
                f"商品经营数据已更新：{updated} 个",
                success=updated,
            )
        span = max(1, min(int(days), 30))
        for day_index in range(span):
            if cancel_event and cancel_event.is_set():
                break
            business_day = (datetime.now() - timedelta(days=day_index + 1)).strftime("%Y%m%d")
            offset = 0
            while True:
                if cancel_event and cancel_event.is_set():
                    break
                result = client.fetch_shop_product_metrics(
                    business_day,
                    offset=offset,
                    limit=200,
                )
                rows = result.get("product_list") or []
                if not isinstance(rows, list):
                    rows = []
                for item in rows:
                    if not isinstance(item, dict):
                        continue
                    product_id = str(item.get("product_id") or "")
                    if product_id not in ids:
                        continue
                    data = item.get("data") or {}
                    if not isinstance(data, dict):
                        continue
                    self.db.upsert_product_metrics(
                        store.id,
                        product_id,
                        click_cnt=int(data.get("product_click_cnt") or 0),
                        click_uv=int(data.get("product_click_uv") or 0),
                        pay_cnt=int(data.get("pay_cnt") or 0),
                        pay_uv=int(data.get("pay_uv") or 0),
                        pay_gmv=int(data.get("pay_gmv") or 0),
                    )
                    updated = max(updated, 1)
                offset += len(rows)
                if len(rows) < 200:
                    break
            if progress:
                progress(
                    f"正在同步商品经营数据 {day_index + 1}/{span} 天",
                    day_index + 1,
                    span,
                )
        message = f"商品经营数据已更新：{len(ids)} 个商品，{span} 天"
        return SyncResult(
            "product_performance",
            len(ids),
            message,
            success=updated,
        )

    def private_batch_product_action(
        self,
        store: Store,
        product_ids: list[str],
        way: str,
    ) -> dict[str, Any]:
        private = ShopPrivateClient(self.db, store)
        return private.batch_product_action(product_ids, way)

    def promote_products_to_league(
        self,
        store: Store,
        product_ids: list[str],
        ratio_percent: float,
    ) -> dict[str, Any]:
        client = self.client_for(store)
        method = getattr(client, "batch_add_league_items", None)
        if not callable(method):
            raise RuntimeError("当前客户端不支持联盟商品推广")
        result = method(
            [
                {
                    "product_id": str(product_id),
                    "ratio": float(ratio_percent),
                }
                for product_id in product_ids
                if str(product_id or "").strip()
            ]
        )
        self.db.log_product_operation(
            store.id,
            "",
            "league_promote",
            "success" if int(result.get("errcode") or 0) == 0 else "failed",
        )
        return result

    def fetch_league_products(self, store: Store) -> dict[str, Any]:
        client = self.client_for(store)
        method = getattr(client, "fetch_league_items", None)
        if not callable(method):
            raise RuntimeError("当前客户端不支持读取联盟商品")
        return method()

    def update_league_ratio(
        self,
        store: Store,
        product_ids: list[str],
        ratio_percent: float,
    ) -> list[str]:
        client = self.client_for(store)
        fetch_detail = getattr(client, "fetch_league_item", None)
        update_item = getattr(client, "update_league_item", None)
        if not callable(fetch_detail) or not callable(update_item):
            raise RuntimeError("当前客户端不支持更新联盟佣金")
        errors: list[str] = []
        for product_id in product_ids:
            try:
                detail = fetch_detail(str(product_id))
                plan_id = str(detail.get("promotion_plan_id") or detail.get("plan_id") or "")
                if not plan_id:
                    errors.append(f"{product_id}: 没有推广计划 ID")
                    continue
                result = update_item(
                    str(product_id),
                    plan_id,
                    float(ratio_percent),
                )
                if int(result.get("errcode") or 0) != 0:
                    errors.append(f"{product_id}: {result.get('errmsg') or '更新失败'}")
            except Exception as exc:
                errors.append(f"{product_id}: {exc}")
        return errors

    def update_product_prices(
        self,
        store: Store,
        product_ids: list[str],
        multiplier: float,
    ) -> list[str]:
        client = self.client_for(store)
        method = getattr(client, "update_product_prices", None)
        if not callable(method):
            raise RuntimeError("当前客户端不支持商品改价")
        errors: list[str] = []
        for product_id in product_ids:
            try:
                result = method(str(product_id), float(multiplier))
                if int(result.get("errcode") or 0) != 0:
                    errors.append(f"{product_id}: {result.get('errmsg') or '改价失败'}")
            except Exception as exc:
                errors.append(f"{product_id}: {exc}")
        return errors

    def create_snapup_activity(
        self,
        store: Store,
        product_ids: list[str],
        discount: float,
        *,
        start_time: int | None = None,
        end_time: int | None = None,
        min_discount_cents: int = 100,
        create_all: bool = False,
    ) -> dict[str, Any]:
        try:
            discount = float(discount)
        except (TypeError, ValueError) as exc:
            raise ValueError("限时抢购折扣必须是 0.1–0.85 之间的数字") from exc
        if not 0.1 <= discount <= 0.85:
            raise ValueError("平台规则要求限时抢购折扣必须在 1–8.5 折之间")
        min_discount_cents = int(min_discount_cents)
        if min_discount_cents < 100:
            raise ValueError("平台规则要求最低优惠不能低于 1 元")
        now = int(time.time())
        start = int(start_time or (now + 300))
        end = int(end_time or (start + 7 * 86400))
        if end <= start:
            raise ValueError("限时抢购结束时间必须晚于开始时间")
        if end - start > 7 * 86400:
            raise ValueError("限时抢购持续时间不能超过 7 天")

        normalized_ids = list(
            dict.fromkeys(str(product_id) for product_id in product_ids if product_id)
        )
        if not normalized_ids:
            raise RuntimeError("没有可参与活动的在售商品")

        private = ShopPrivateClient(self.db, store)
        already_active_ids: set[str] = set()
        list_existing = getattr(private, "snapup_activities", None)
        if create_all and callable(list_existing):
            for activity in list_existing():
                try:
                    activity_status = int(activity.get("status") or 0)
                except (TypeError, ValueError):
                    activity_status = 0
                if activity_status not in {0, 1}:
                    continue
                extra = activity.get("extra") or {}
                products = (
                    extra.get("snapupProducts") or []
                    if isinstance(extra, dict)
                    else []
                )
                for product in products:
                    if not isinstance(product, dict):
                        continue
                    product_id = str(product.get("productId") or "")
                    if product_id:
                        already_active_ids.add(product_id)
        candidate_ids = [
            product_id
            for product_id in normalized_ids
            if product_id not in already_active_ids
        ]
        if not candidate_ids:
            return {
                "activity_id": "",
                "activity_count": 0,
                "activities": [],
                "product_count": 0,
                "sku_count": 0,
                "requested_count": len(normalized_ids),
                "skipped_count": 0,
                "invalid_skipped_count": 0,
                "capacity_skipped_count": 0,
                "already_active_count": len(already_active_ids),
                "failed_count": 0,
                "truncated": False,
                "sku_limit_reached": False,
                "batch_count": 0,
                "snapup_type": 1,
                "start_time": start,
                "end_time": end,
            }
        products = private.fetch_snapup_products(candidate_ids)
        products_by_id = {
            str(
                (product.get("productKey") or {}).get("productId")
                or ""
            ): product
            for product in products
            if isinstance(product, dict)
        }
        eligible_products: list[dict[str, Any]] = []
        invalid_skipped_count = 0
        for order, product_id in enumerate(candidate_ids):
            product = products_by_id.get(product_id)
            if not product:
                invalid_skipped_count += 1
                continue
            try:
                product_status = int(product.get("status") or 0)
            except (TypeError, ValueError):
                product_status = 0
            if product_status != 5:
                invalid_skipped_count += 1
                continue
            snapup_skus: list[dict[str, str]] = []
            for sku in product.get("productSkus") or []:
                if not isinstance(sku, dict):
                    continue
                try:
                    sku_status = int(sku.get("status") or 5)
                except (TypeError, ValueError):
                    sku_status = 5
                sku_info = sku.get("productSkuInfo") or {}
                stock_info = (
                    sku_info.get("stockInfo")
                    if isinstance(sku_info, dict)
                    else {}
                )
                try:
                    sale_price = int(sku_info.get("salePrice") or 0)
                    sale_stock = int((stock_info or {}).get("stockNum") or 0)
                except (TypeError, ValueError):
                    continue
                sku_id = str((sku.get("skuKey") or {}).get("skuId") or "")
                if (
                    sku_status != 5
                    or not sku_id
                    or sale_price <= 0
                    or sale_stock <= 0
                ):
                    continue
                activity_price = _discount_price_cents(sale_price, discount)
                maximum_price = sale_price - min_discount_cents
                minimum_price = _discount_price_cents(sale_price, Decimal("0.1"))
                activity_price = max(
                    minimum_price,
                    min(activity_price, maximum_price),
                )
                if (
                    activity_price >= sale_price
                    or sale_price - activity_price < min_discount_cents
                ):
                    continue
                snapup_skus.append(
                    {
                        "skuId": sku_id,
                        "snapupPrice": str(activity_price),
                        # snapupType=1 is a time-limited price activity.
                        # The merchant backend rejects a custom snap-up stock
                        # for this type; stock remains governed by the SKU.
                        "snapupStock": "0",
                    }
                )
            if snapup_skus:
                eligible_products.append(
                    {
                        "productId": product_id,
                        "snapupSkus": snapup_skus,
                        "_order": order,
                    }
                )
            else:
                invalid_skipped_count += 1
        if not eligible_products and already_active_ids:
            return {
                "activity_id": "",
                "activity_count": 0,
                "activities": [],
                "product_count": 0,
                "sku_count": 0,
                "requested_count": len(normalized_ids),
                "skipped_count": invalid_skipped_count,
                "invalid_skipped_count": invalid_skipped_count,
                "capacity_skipped_count": 0,
                "already_active_count": len(already_active_ids),
                "failed_count": 0,
                "truncated": False,
                "sku_limit_reached": False,
                "batch_count": 0,
                "snapup_type": 1,
                "start_time": start,
                "end_time": end,
            }
        if not eligible_products:
            raise RuntimeError("没有可参与活动的在售且库存大于 0 的商品")

        eligible_products.sort(
            key=lambda item: (len(item["snapupSkus"]), item["_order"])
        )
        batches: list[list[dict[str, Any]]] = []
        remaining = eligible_products
        capacity_skipped_count = 0
        while remaining:
            batch: list[dict[str, Any]] = []
            batch_sku_count = 0
            next_remaining: list[dict[str, Any]] = []
            for item in remaining:
                item_sku_count = len(item["snapupSkus"])
                if item_sku_count > 2000:
                    capacity_skipped_count += 1
                    continue
                if (
                    len(batch) < 200
                    and batch_sku_count + item_sku_count <= 2000
                ):
                    batch.append(item)
                    batch_sku_count += item_sku_count
                else:
                    next_remaining.append(item)
            if not batch:
                capacity_skipped_count += len(next_remaining)
                break
            batches.append(batch)
            remaining = next_remaining

        batches_to_create = batches if create_all else batches[:1]
        activities: list[dict[str, Any]] = []
        start_dt = datetime.fromtimestamp(start)
        for batch_index, batch in enumerate(batches_to_create, start=1):
            snapup_products = [
                {
                    "productId": item["productId"],
                    "snapupSkus": item["snapupSkus"],
                }
                for item in batch
            ]
            title_suffix = (
                f" {batch_index}"
                if len(batches_to_create) > 1
                else ""
            )
            payload = {
                "title": f"澜琅限时7天 {start_dt:%m/%d}{title_suffix}",
                "snapupProducts": snapup_products,
                "startTime": start,
                "endTime": end,
                "snapupType": 1,
                "limitPurchaseNum": 0,
            }
            try:
                response = private.create_snapup_activity(payload)
            except ShopPrivateError as exc:
                activities.append(
                    {
                        "ok": False,
                        "batch_index": batch_index,
                        "product_count": len(snapup_products),
                        "sku_count": sum(
                            len(item["snapupSkus"])
                            for item in batch
                        ),
                        "error": str(exc),
                    }
                )
                if not create_all:
                    raise
                continue
            data = (
                response.get("data")
                if isinstance(response.get("data"), dict)
                else {}
            )
            activity_id = str(
                response.get("activityId")
                or data.get("activityId")
                or ""
            )
            activities.append(
                {
                    "ok": True,
                    "batch_index": batch_index,
                    "activity_id": activity_id,
                    "product_count": len(snapup_products),
                    "sku_count": sum(
                        len(item["snapupSkus"])
                        for item in batch
                    ),
                    "response": response,
                }
            )
        successful = [item for item in activities if item["ok"]]
        failed = [item for item in activities if not item["ok"]]
        skipped_count = (
            invalid_skipped_count
            + capacity_skipped_count
            + sum(
                len(batch)
                for batch in batches[len(batches_to_create) :]
            )
        )
        return {
            "activity_id": (
                successful[0]["activity_id"] if successful else ""
            ),
            "activity_count": len(successful),
            "activities": activities,
            "product_count": sum(
                item["product_count"] for item in successful
            ),
            "sku_count": sum(item["sku_count"] for item in successful),
            "requested_count": len(normalized_ids),
            "skipped_count": skipped_count,
            "invalid_skipped_count": invalid_skipped_count,
            "capacity_skipped_count": capacity_skipped_count,
            "already_active_count": len(already_active_ids),
            "failed_count": len(failed),
            "truncated": len(batches) > len(batches_to_create),
            "sku_limit_reached": len(batches) > len(batches_to_create),
            "batch_count": len(batches),
            "snapup_type": 1,
            "start_time": start,
            "end_time": end,
        }

    def delete_snapup_activity(
        self,
        store: Store,
        activity_id: str,
        *,
        stop_if_active: bool = True,
    ) -> dict[str, Any]:
        private = ShopPrivateClient(self.db, store)
        if stop_if_active:
            task = private.snapup_activity(activity_id)
            try:
                status = int(task.get("status") or 0)
            except (TypeError, ValueError):
                status = 0
            if status == 1:
                private.stop_snapup_activity(activity_id)
        return private.delete_snapup_activity(activity_id)

    def create_limited_discount_tasks(
        self,
        store: Store,
        product_ids: list[str],
        discount: float,
        *,
        start_time: int | None = None,
        end_time: int | None = None,
        min_discount_cents: int = 100,
    ) -> list[str]:
        client = self.client_for(store)
        fetch_product = getattr(client, "fetch_product_payload", None)
        create_task = getattr(client, "create_limited_discount_task", None)
        if not callable(fetch_product) or not callable(create_task):
            raise RuntimeError("当前客户端不支持限时抢购")
        try:
            discount = float(discount)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "限时抢购折扣倍率必须是数字，0.85表示8.5折"
            ) from exc
        if not 0.1 <= discount <= 0.85:
            raise ValueError(
                "平台规则要求限时抢购折扣必须在 1–8.5 折之间，"
                "0.85表示8.5折"
            )
        min_discount_cents = int(min_discount_cents)
        if min_discount_cents < 100:
            raise ValueError("平台规则要求最低优惠不能低于 1 元")
        now = int(time.time())
        start = int(start_time or (now + 300))
        end = int(end_time or (start + 7 * 86400))
        if end <= start:
            raise ValueError("限时抢购结束时间必须晚于开始时间")
        if end - start > 7 * 86400:
            raise ValueError("限时抢购持续时间不能超过 7 天")
        errors: list[str] = []
        for product_id in product_ids:
            try:
                detail = fetch_product(str(product_id), data_type=3)
                product = detail.get("edit_product") or detail.get("product")
                if not isinstance(product, dict):
                    product = {}
                skus = []
                for sku in product.get("skus") or []:
                    if not isinstance(sku, dict):
                        continue
                    try:
                        sale_price = int(sku.get("sale_price") or 0)
                        sale_stock = int(sku.get("stock_num") or 0)
                    except (TypeError, ValueError):
                        continue
                    sku_id = str(sku.get("sku_id") or "")
                    if not sku_id or sale_price <= 0 or sale_stock <= 0:
                        continue
                    activity_price = _discount_price_cents(
                        sale_price,
                        discount,
                    )
                    if min_discount_cents:
                        activity_price = min(
                            activity_price,
                            max(1, sale_price - min_discount_cents),
                        )
                    skus.append(
                        {
                            "sku_id": sku_id,
                            "sale_price": activity_price,
                            "sale_stock": sale_stock,
                        }
                    )
                if not skus:
                    errors.append(f"{product_id}: 没有可参与抢购的 SKU")
                    continue
                result = create_task(
                    {
                        "product_id": str(product_id),
                        "start_time": start,
                        "end_time": end,
                        "limited_discount_skus": skus,
                        "title": "澜琅托管限时抢购",
                    }
                )
                if int(result.get("errcode") or 0) != 0:
                    errors.append(f"{product_id}: {result.get('errmsg') or '创建失败'}")
            except Exception as exc:
                errors.append(f"{product_id}: {exc}")
        return errors

    def stop_active_limited_discounts(
        self,
        store: Store,
    ) -> list[str]:
        client = self.client_for(store)
        fetch_tasks = getattr(client, "fetch_limited_discount_tasks", None)
        stop_task = getattr(client, "stop_limited_discount_task", None)
        if not callable(fetch_tasks) or not callable(stop_task):
            raise RuntimeError("当前客户端不支持停止限时抢购")
        result = fetch_tasks(status=1, page_size=50)
        tasks = result.get("limited_discount_tasks") or []
        errors: list[str] = []
        for task in tasks:
            if not isinstance(task, dict):
                continue
            task_id = str(task.get("task_id") or "")
            if not task_id:
                continue
            try:
                stopped = stop_task(task_id)
                if int(stopped.get("errcode") or 0) != 0:
                    errors.append(f"{task_id}: {stopped.get('errmsg') or '停止失败'}")
            except Exception as exc:
                errors.append(f"{task_id}: {exc}")
        return errors

    def set_products_listing(
        self,
        store: Store,
        product_ids: list[str],
        listed: bool,
        progress: ProgressCallback | None = None,
        workers: int = 4,
        cancel_event: threading.Event | None = None,
    ) -> SyncResult:
        action = "listing" if listed else "delisting"
        private = None
        if (
            not listed
            and store.app_id != "demo-store"
            and not self.db.get_store_secret(store.id)[1]
        ):
            private = ShopPrivateClient(self.db, store)
            client = None
            workers = 1
        else:
            client = self.client_for(store)
        run_id = self.db.start_sync_run(store.id, action)
        total = len(product_ids)
        success_count = 0
        failures: list[str] = []
        cancelled = False
        status_code = 5 if listed else 11
        status_text = PRODUCT_STATUS[status_code]

        def apply_one(product_id: str) -> tuple[str, str | None]:
            if cancel_event and cancel_event.is_set():
                return product_id, "cancelled"
            try:
                if private is not None:
                    result = private.batch_product_action([product_id], "offshelf")
                elif isinstance(client, MockShopClient):
                    result = {"errcode": 0}
                else:
                    result = client.set_product_listing(product_id, listed)
                if int(result.get("errcode") or 0) != 0:
                    raise RuntimeError(str(result.get("errmsg") or "接口返回失败"))
                self.db.mark_product_status(
                    store.id,
                    product_id,
                    status_code,
                    status_text,
                )
                self.db.log_product_operation(
                    store.id,
                    product_id,
                    action,
                    "success",
                )
                return product_id, None
            except Exception as exc:
                message = str(exc)
                self.db.log_product_operation(
                    store.id,
                    product_id,
                    action,
                    "failed",
                    message,
                )
                return product_id, message

        executor = ThreadPoolExecutor(max_workers=max(1, min(workers, 8)))
        futures = {executor.submit(apply_one, product_id): product_id for product_id in product_ids}
        try:
            for index, future in enumerate(as_completed(futures), 1):
                if cancel_event and cancel_event.is_set():
                    cancelled = True
                    for pending in futures:
                        pending.cancel()
                if future.cancelled():
                    continue
                try:
                    product_id, error = future.result()
                except CancelledError:
                    continue
                if error is None:
                    success_count += 1
                elif error != "cancelled":
                    failures.append(f"{product_id}: {error}")
                if progress:
                    progress(
                        f"正在批量{'上架' if listed else '下架'} {index}/{total}",
                        index,
                        total,
                    )
        finally:
            executor.shutdown(wait=True, cancel_futures=True)

        message = f"批量{'上架' if listed else '下架'}：成功 {success_count}，失败 {len(failures)}"
        status = "cancelled" if cancelled else ("success" if not failures else "partial")
        self.db.finish_sync_run(run_id, status, success_count, message)
        return SyncResult(
            action,
            total,
            message,
            success=success_count,
            failed=len(failures),
            cancelled=cancelled,
            errors=failures[:20],
        )

    def update_product_stock(
        self,
        store: Store,
        product_id: str,
        sku_id: str,
        diff_type: int,
        amount: int,
    ) -> Product | None:
        if diff_type not in {1, 2, 3}:
            raise ValueError("库存修改类型必须是 1、2 或 3")
        if amount < 0 or (diff_type != 3 and amount == 0):
            raise ValueError("库存调整数量不正确")
        client = self.client_for(store)
        if isinstance(client, MockShopClient):
            product_row = self.db.get_product(store.id, product_id)
            if product_row:
                import json

                payload = json.loads(product_row["raw_json"] or "{}")
                skus = payload.get("skus") or []
                sku = next(
                    (item for item in skus if str(item.get("sku_id")) == str(sku_id)),
                    None,
                )
                if sku is not None:
                    current = int(sku.get("stock_num") or 0)
                    new_stock = (
                        current + amount
                        if diff_type == 1
                        else current - amount
                        if diff_type == 2
                        else amount
                    )
                    self.db.set_product_stock_cache(
                        store.id,
                        product_id,
                        sku_id,
                        max(0, new_stock),
                    )
            self.db.log_product_operation(store.id, product_id, "stock", "success")
            return None
        try:
            result = client.update_product_stock(
                product_id,
                sku_id,
                diff_type,
                amount,
            )
            if int(result.get("errcode") or 0) != 0:
                raise RuntimeError(str(result.get("errmsg") or "接口返回失败"))
        except Exception as exc:
            self.db.log_product_operation(
                store.id,
                product_id,
                "stock",
                "failed",
                str(exc),
            )
            raise
        self.db.log_product_operation(store.id, product_id, "stock", "success")
        product = client.fetch_product(store, product_id)
        self.db.upsert_product(product)
        return product

    def refresh_shipping_options(
        self,
        store: Store,
        progress: ProgressCallback | None = None,
    ) -> SyncResult:
        run_id = self.db.start_sync_run(store.id, "shipping_options")
        try:
            client = self.client_for(store)
            if isinstance(client, MockShopClient):
                account = ShippingAccount(
                    store_id=store.id,
                    delivery_id="SF",
                    delivery_name="顺丰速运",
                    acct_id="demo-acct",
                    shop_id="demo-shop",
                    site_code="DEMO",
                    site_name="演示网点",
                    status=3,
                    available=100,
                    monthly_card="",
                    sender_name="演示寄件人",
                    sender_mobile="13800000000",
                    sender_address={
                        "province": "湖北省",
                        "city": "武汉市",
                        "county": "青山区",
                        "street": "示范街道",
                        "address": "演示仓",
                    },
                    raw={"demo": True},
                )
                template = ShippingTemplate(
                    store_id=store.id,
                    delivery_id="SF",
                    template_id="demo-template",
                    template_name="演示一联单",
                    template_type="single",
                    is_default=True,
                    raw={"demo": True},
                )
                accounts = [account]
                templates = [template]
            else:
                delivery_rows = client.fetch_ewaybill_delivery_companies(status=3)
                delivery_names = {
                    str(item.get("delivery_id") or ""): str(item.get("delivery_name") or "")
                    for item in delivery_rows
                }
                if progress:
                    progress("正在获取电子面单账号库存…", 0, 0)
                account_rows = client.fetch_ewaybill_accounts(
                    need_balance=True,
                    page_size=10,
                )
                accounts = [
                    parse_shipping_account(
                        store,
                        item,
                        delivery_names.get(
                            str(item.get("delivery_id") or ""),
                            str(item.get("delivery_id") or ""),
                        ),
                    )
                    for item in account_rows
                ]
                if progress:
                    progress("正在获取电子面单模板…", 0, 0)
                template_rows = client.fetch_ewaybill_templates()
                templates = [parse_shipping_template(store, item) for item in template_rows]

            for account in accounts:
                self.db.upsert_shipping_account(account)
            for template in templates:
                self.db.upsert_shipping_template(template)

            config = self.db.get_shipping_config(store.id)
            if config is None and accounts:
                account = next((item for item in accounts if item.status == 3), accounts[0])
                selected_template = next(
                    (
                        item
                        for item in templates
                        if item.delivery_id == account.delivery_id and item.is_default
                    ),
                    next(
                        (item for item in templates if item.delivery_id == account.delivery_id),
                        None,
                    ),
                )
                self.db.save_shipping_config(
                    store.id,
                    {
                        "name": account.sender_name,
                        "mobile": account.sender_mobile,
                        **account.sender_address,
                    },
                    account.acct_id,
                    account.delivery_id,
                    selected_template.template_id if selected_template else "",
                )
            message = f"物流配置已刷新：{len(accounts)} 个面单账号，{len(templates)} 个模板"
            self.db.finish_sync_run(
                run_id,
                "success",
                len(accounts) + len(templates),
                message,
            )
            return SyncResult(
                "shipping_options",
                len(accounts),
                message,
                success=len(accounts),
            )
        except Exception as exc:
            self.db.finish_sync_run(run_id, "failed", 0, str(exc))
            raise

    def create_shipment(self, store: Store, order_id: str) -> dict:
        order = self.db.get_order(store.id, order_id)
        if order is None:
            raise ValueError("订单不存在")
        config = self.db.get_shipping_config(store.id)
        if config is None:
            raise ValueError("请先保存发货人和面单账号设置")
        accounts = self.db.list_shipping_accounts(store.id)
        account = next(
            (item for item in accounts if item["acct_id"] == config["default_acct_id"]),
            None,
        )
        if account is None:
            raise ValueError("默认面单账号不存在，请刷新物流配置")
        self._validate_receiver(order)
        sender = {
            "name": str(config["sender_name"]),
            "mobile": str(config["sender_mobile"]),
            "province": str(config["sender_province"]),
            "city": str(config["sender_city"]),
            "county": str(config["sender_county"]),
            "street": str(config["sender_street"]),
            "address": str(config["sender_address"]),
        }
        missing = [key for key, value in sender.items() if not value]
        if missing:
            raise ValueError("发货地址未填写完整")
        raw = order.raw
        address_info = raw.get("order_detail", {}).get("delivery_info", {}).get("address_info", {})
        receiver = {
            "name": order.buyer_name,
            "mobile": order.phone.split("-", 1)[0],
            "province": str(address_info.get("province_name") or ""),
            "city": str(address_info.get("city_name") or ""),
            "county": str(address_info.get("county_name") or ""),
            "street": str(address_info.get("street_name") or ""),
            "address": str(address_info.get("detail_info") or ""),
        }
        goods_list = [
            {
                "good_name": item.title,
                "good_count": item.sku_count,
                "product_id": int(item.product_id)
                if item.product_id.isdigit()
                else item.product_id,
                "sku_id": int(item.sku_id) if item.sku_id.isdigit() else item.sku_id,
            }
            for item in order.items
        ]
        payload: dict = {
            "delivery_id": account["delivery_id"],
            "ewaybill_acct_id": account["acct_id"],
            "sender": sender,
            "receiver": receiver,
            "ec_order_list": [
                {
                    "ec_order_id": int(order.order_id)
                    if order.order_id.isdigit()
                    else order.order_id,
                    "goods_list": goods_list,
                }
            ],
            "shop_id": account["shop_id"],
        }
        if account["site_code"]:
            payload["site_code"] = account["site_code"]
        template_id = str(config["default_template_id"] or "")
        if template_id:
            payload["template_id"] = template_id
        else:
            payload["template_type"] = "single"

        client = self.client_for(store)
        if isinstance(client, MockShopClient):
            ewaybill_order_id = f"DEMO{order_id}"
            create_result = {
                "ewaybill_order_id": ewaybill_order_id,
                "waybill_id": f"SFDEMO{order_id[-8:]}",
                "print_info": json.dumps(
                    {"demo": True, "order_id": order_id},
                    ensure_ascii=False,
                ),
            }
        else:
            precreate = client.precreate_ewaybill(payload)
            ewaybill_order_id = str(precreate.get("ewaybill_order_id") or "")
            if not ewaybill_order_id:
                raise RuntimeError("预取号接口没有返回 ewaybill_order_id")
            self.db.upsert_shipment(
                store.id,
                order_id,
                ewaybill_order_id,
                account["delivery_id"],
                account["acct_id"],
                template_id,
                "",
                "precreated",
            )
            create_payload = dict(payload)
            create_payload["ewaybill_order_id"] = ewaybill_order_id
            try:
                create_result = client.create_ewaybill(create_payload)
            except Exception as exc:
                self.db.upsert_shipment(
                    store.id,
                    order_id,
                    ewaybill_order_id,
                    account["delivery_id"],
                    account["acct_id"],
                    template_id,
                    "",
                    "create_failed",
                    error=str(exc),
                )
                raise

        order_info = create_result.get("order_info") or {}
        waybill_id = str(create_result.get("waybill_id") or order_info.get("waybill_id") or "")
        if not waybill_id:
            raise RuntimeError("取号接口没有返回运单号")
        print_info = create_result.get("print_info") or order_info.get("print_info") or ""
        if not isinstance(print_info, str):
            print_info = json.dumps(print_info, ensure_ascii=False)
        self.db.upsert_shipment(
            store.id,
            order_id,
            ewaybill_order_id,
            account["delivery_id"],
            account["acct_id"],
            template_id,
            waybill_id,
            "created",
            print_info=print_info,
        )
        self.db.update_order_delivery(
            store.id,
            order_id,
            waybill_id,
            account["delivery_name"],
            order.status,
            order.status_text,
        )
        return self.db.get_shipment(store.id, order_id) or {}

    def complete_shipment(self, store: Store, order_id: str) -> dict:
        shipment = self.db.get_shipment(store.id, order_id)
        if shipment is None or not shipment["waybill_id"]:
            raise ValueError("请先生成电子面单")
        order = self.db.get_order(store.id, order_id)
        if order is None:
            raise ValueError("订单不存在")
        client = self.client_for(store)
        if not isinstance(client, MockShopClient):
            client.confirm_ewaybill_printed(
                shipment["ewaybill_order_id"],
                shipment["delivery_id"],
                shipment["waybill_id"],
            )
            self.db.upsert_shipment(
                store.id,
                order_id,
                shipment["ewaybill_order_id"],
                shipment["delivery_id"],
                shipment["acct_id"],
                shipment["template_id"],
                shipment["waybill_id"],
                "printed",
                shipment["print_info"],
            )
            delivery_list = [
                {
                    "waybill_id": shipment["waybill_id"],
                    "delivery_id": shipment["delivery_id"],
                    "product_infos": [
                        {
                            "product_id": item.product_id,
                            "sku_id": item.sku_id,
                            "product_cnt": item.sku_count,
                        }
                        for item in order.items
                    ],
                    "deliver_type": 1,
                }
            ]
            client.send_order_delivery(order_id, delivery_list)
        self.db.update_order_delivery(
            store.id,
            order_id,
            shipment["waybill_id"],
            shipment["delivery_id"],
            21,
            order_status_text(21),
        )
        self.db.upsert_shipment(
            store.id,
            order_id,
            shipment["ewaybill_order_id"],
            shipment["delivery_id"],
            shipment["acct_id"],
            shipment["template_id"],
            shipment["waybill_id"],
            "shipped",
            shipment["print_info"],
        )
        return self.db.get_shipment(store.id, order_id) or {}

    @staticmethod
    def _validate_receiver(order: Order) -> None:
        values = (order.buyer_name, order.phone, order.address)
        if not all(values) or "*" in "".join(values):
            raise ValueError("订单收货信息仍被隐藏，请先在订单页解密收货信息")

    def decode_order_sensitive(self, store: Store, order_id: str) -> None:
        client = self.client_for(store)
        result = client.decode_sensitive_info(order_id)
        address_info = result.get("address_info") or {}
        virtual_info = result.get("virtual_number_info") or {}
        phone = str(virtual_info.get("virtual_number") or address_info.get("tel_number") or "")
        extension = str(virtual_info.get("extension") or "")
        if phone and extension:
            phone = f"{phone}-{extension}"
        province = str(address_info.get("province_name") or "")
        city = str(address_info.get("city_name") or "")
        county = str(address_info.get("county_name") or "")
        detail = str(address_info.get("detail_info") or "")
        address = "".join(
            dict.fromkeys(value for value in (province, city, county, detail) if value)
        )
        self.db.update_order_sensitive(
            store.id,
            order_id,
            str(address_info.get("user_name") or ""),
            phone,
            address,
            {
                "province": province,
                "city": city,
                "county": county,
                "street": str(address_info.get("street_name") or ""),
                "address": detail,
            },
        )

    def sync_store_profile(self, store: Store) -> SyncResult:
        client = self.client_for(store)
        info = client.fetch_store_info()
        self.db.upsert_store_profile(store.id, info)
        return SyncResult(
            "store_profile",
            1,
            f"店铺资料已更新：{info.get('nickname') or store.name}",
            success=1,
        )

    def sync_after_sales(
        self,
        store: Store,
        start_time: int,
        end_time: int,
        progress: ProgressCallback | None = None,
        time_field: str = "create",
    ) -> SyncResult:
        if end_time - start_time > 86400:
            raise ValueError("售后单单次查询不能超过 24 小时")
        client = self.client_for(store)
        ids = client.fetch_after_sale_ids(
            start_time,
            end_time,
            time_field=time_field,
        )
        failures = []
        for index, after_sale_id in enumerate(ids, 1):
            if progress:
                progress(f"正在拉取售后单 {index}/{len(ids)}", index, len(ids))
            try:
                payload = client.fetch_after_sale(after_sale_id)
                self.db.upsert_after_sale(store.id, payload)
            except Exception as exc:
                failures.append(f"{after_sale_id}: {exc}")
        message = f"售后同步完成：成功 {len(ids) - len(failures)}，失败 {len(failures)}"
        return SyncResult(
            "after_sales",
            len(ids),
            message,
            success=len(ids) - len(failures),
            failed=len(failures),
            errors=failures[:20],
        )

    def sync_analytics_day(
        self,
        store: Store,
        business_date: str,
    ) -> SyncResult:
        client = self.client_for(store)
        data = client.fetch_shop_overview(business_date.replace("-", ""))
        self.db.upsert_analytics_day(store.id, business_date, data)
        return SyncResult(
            "analytics",
            1,
            f"{business_date} 经营数据已更新",
            success=1,
        )

    def sync_analytics_range(
        self,
        store: Store,
        start_date: str,
        end_date: str,
        progress: ProgressCallback | None = None,
    ) -> SyncResult:
        start = datetime.strptime(start_date, "%Y-%m-%d").date()
        end = datetime.strptime(end_date, "%Y-%m-%d").date()
        if end < start:
            raise ValueError("结束日期不能早于开始日期")
        days = (end - start).days + 1
        failures = 0
        for index in range(days):
            if progress:
                progress(
                    f"正在同步经营数据 {index + 1}/{days}",
                    index,
                    days,
                )
            day = (start + timedelta(days=index)).isoformat()
            try:
                self.sync_analytics_day(store, day)
            except Exception:
                failures += 1
        return SyncResult(
            "analytics_range",
            days,
            f"经营数据同步完成：成功 {days - failures}，失败 {failures}",
            success=days - failures,
            failed=failures,
        )

    def sync_coupons(
        self,
        store: Store,
        progress: ProgressCallback | None = None,
    ) -> SyncResult:
        client = self.client_for(store)
        if isinstance(client, MockShopClient):
            return SyncResult("coupons", 0, "演示店铺暂无优惠券")
        ids = []
        for status in (1, 2, 3, 4):
            ids.extend(client.fetch_coupon_ids(status))
        ids = list(dict.fromkeys(ids))
        failures = []
        for index, coupon_id in enumerate(ids, 1):
            if progress:
                progress(f"正在拉取优惠券 {index}/{len(ids)}", index, len(ids))
            try:
                self.db.upsert_coupon(store.id, client.fetch_coupon(coupon_id))
            except Exception as exc:
                failures.append(f"{coupon_id}: {exc}")
        return SyncResult(
            "coupons",
            len(ids),
            f"优惠券同步完成：成功 {len(ids) - len(failures)}，失败 {len(failures)}",
            success=len(ids) - len(failures),
            failed=len(failures),
            errors=failures[:20],
        )

    def coupon_write(
        self,
        store: Store,
        action: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        client = self.client_for(store)
        if isinstance(client, MockShopClient):
            return {"errcode": 0, "demo": True}
        if action == "create":
            result = client.create_coupon(payload)
        elif action == "update":
            result = client.update_coupon(payload)
        elif action == "status":
            result = client.update_coupon_status(payload)
        else:
            raise ValueError("不支持的优惠券操作")
        coupon_id = str(result.get("coupon_id") or payload.get("coupon_id") or "")
        if coupon_id:
            try:
                self.db.upsert_coupon(store.id, client.fetch_coupon(coupon_id))
            except Exception:
                pass
        return result

    def sync_storefront(self, store: Store) -> list[dict[str, Any]]:
        client = self.client_for(store)
        if isinstance(client, MockShopClient):
            return [
                {
                    "product_id": f"P{index:03d}",
                    "title": f"演示商品 {index}",
                    "status_text": "上架",
                }
                for index in range(1, 5)
            ]
        ids = client.fetch_storefront_product_ids()
        result = []
        for product_id in ids:
            try:
                product = client.fetch_product(store, product_id)
                result.append(
                    {
                        "product_id": product_id,
                        "title": product.title,
                        "status_text": product.status_text,
                        "raw": product,
                    }
                )
            except Exception:
                result.append(
                    {
                        "product_id": product_id,
                        "title": "(详情读取失败)",
                        "status_text": "—",
                        "raw": None,
                    }
                )
        return result

    def storefront_action(
        self,
        store: Store,
        action: str,
        product_id: str,
        value: int,
    ) -> None:
        client = self.client_for(store)
        if isinstance(client, MockShopClient):
            return
        if action == "reorder":
            client.reorder_storefront_product(product_id, value)
        elif action == "hide":
            client.hide_storefront_product(product_id, bool(value))
        elif action == "top":
            client.top_storefront_product(product_id, bool(value))
        else:
            raise ValueError("不支持的主页操作")

    def procurement_login(self, account_id: int) -> dict[str, Any]:
        account = self.db.get_procurement_account(account_id)
        if account is None:
            raise ValueError("采购账号不存在")
        return ProcurementWebSession(
            self.db,
            self.db.path.parent,
        ).login(account)

    def procurement_check_status(self, account_id: int) -> str:
        account = self.db.get_procurement_account(account_id)
        if account is None:
            raise ValueError("采购账号不存在")
        return ProcurementWebSession(
            self.db,
            self.db.path.parent,
        ).check_status(account)

    def procurement_bind_alipay(self, account_id: int) -> dict[str, Any]:
        account = self.db.get_procurement_account(account_id)
        if account is None:
            raise ValueError("采购账号不存在")
        return ProcurementWebSession(
            self.db,
            self.db.path.parent,
        ).bind_alipay(account)

    def sync_procurement_account(
        self,
        account_id: int,
        progress: ProgressCallback | None = None,
    ) -> SyncResult:
        account = self.db.get_procurement_account(account_id)
        if account is None:
            raise ValueError("采购账号不存在")
        adapter = ProcurementAdapter(self.db, self.db.path.parent)
        try:
            count = adapter.sync(account, progress)
        except Exception as exc:
            self.db.update_procurement_account(
                account_id,
                status="已掉线" if "登录" in str(exc) else "异常",
                last_error=str(exc)[:1000],
            )
            raise
        return SyncResult(
            f"procurement_{account['platform']}",
            count,
            f"{account['platform']}采购数据已同步：{count} 条",
            success=count,
        )

    def procurement_browse(self, account_id: int) -> SyncResult:
        account = self.db.get_procurement_account(account_id)
        if account is None:
            raise ValueError("采购账号不存在")
        from .procurement import normalize_procurement_records

        session = ProcurementWebSession(self.db, self.db.path.parent)
        captured = session.browse(account)
        records = normalize_procurement_records(
            str(account["platform"]),
            [item.get("data") for item in captured],
        )
        for record in records:
            self.db.upsert_procurement_order(
                int(account["id"]),
                str(account["platform"]),
                record,
            )
        self.db.update_procurement_account(
            int(account["id"]),
            last_sync=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            status="已登录",
            last_error="",
        )
        return SyncResult(
            f"procurement_browse_{account['platform']}",
            len(records),
            f"采购网页已关闭，保存 {len(records)} 条采购记录",
            success=len(records),
        )

    def match_procurement_orders(self, store: Store) -> int:
        return ProcurementAdapter(
            self.db,
            self.db.path.parent,
        ).match_orders(store.id)

    def procurement_local_note(self, store: Store, order_id: str) -> str:
        links = self.db.list_procurement_links(store.id, order_id)
        parts = []
        for link in links:
            parts.append(
                "采购[{platform}] {external_order_id} 物流:{mail_no} "
                "取件码:{pickup_code} {goods_title} ¥{amount:.2f}".format(
                    platform=link.get("platform") or "",
                    external_order_id=link.get("external_order_id") or "",
                    mail_no=link.get("mail_no") or "",
                    pickup_code=link.get("pickup_code") or "",
                    goods_title=link.get("goods_title") or "",
                    amount=int(link.get("pay_amount") or 0) / 100,
                )
            )
            if link.get("local_note"):
                parts.append(str(link["local_note"]))
        return "\n".join(parts)

    def sync_procurement_note_to_wechat(
        self,
        store: Store,
        order_id: str,
    ) -> SyncResult:
        links = self.db.list_procurement_links(store.id, order_id)
        if not links:
            raise ValueError("该订单没有采购关联")
        note = self.procurement_local_note(store, order_id)
        existing = self.db.get_order(store.id, order_id)
        if existing and existing.merchant_notes:
            note = f"{existing.merchant_notes}\n{note}".strip()
        self.update_order_notes(store, order_id, note, 0)
        for link in links:
            self.db.mark_procurement_note_synced(int(link["id"]))
        return SyncResult(
            "procurement_note_sync",
            1,
            "采购备注已同步到微信订单备注",
            success=1,
        )

    def double11_activity_products(
        self,
        store: Store,
        activity_id: int = 1323,
    ) -> dict[str, Any]:
        return ShopPrivateClient(self.db, store).activity_products(activity_id)

    def snapup_activities(self, store: Store) -> list[dict[str, Any]]:
        if store.web_status != "已登录":
            raise ValueError(f"网页{store.web_status}，不能读取限时抢购")
        return ShopPrivateClient(self.db, store).snapup_activities()

    def platform_activity_catalog(self, store: Store) -> dict[str, Any]:
        activities = ShopPrivateClient(self.db, store).platform_activities()
        summaries = []
        for item in activities:
            summaries.append(
                {
                    "activity_id": str(item.get("activity_id") or ""),
                    "name": str(item.get("name") or ""),
                    "activity_state": int(item.get("activityState") or 0),
                    "apply_state": int(item.get("applyState") or 0),
                    "publish_time": int(item.get("publishTime") or 0),
                    "apply_end_time": int(item.get("applyEndTime") or 0),
                    "activity_end_time": int(item.get("activityEndTime") or 0),
                    "description": str(
                        item.get("activityIntroduction") or item.get("incentiveDescription") or ""
                    ),
                }
            )
        payload = {
            "source_store_id": store.id,
            "source_store_name": store.name,
            "fetched_at": int(time.time()),
            "activities": summaries,
        }
        self.db.set_setting(
            "platform_activity_catalog",
            json.dumps(payload, ensure_ascii=False),
        )
        return payload

    def apply_platform_activity(
        self,
        store: Store,
        activity_id: int,
    ) -> dict[str, Any]:
        if store.web_status != "已登录":
            raise ValueError(f"网页{store.web_status}，不能申报平台活动")
        try:
            result = ShopPrivateClient(
                self.db,
                store,
            ).apply_platform_activity(activity_id)
        except Exception as exc:
            self.db.record_activity_operation(
                "活动申报",
                "失败",
                store.id,
                store.name,
                int(activity_id),
                0,
                str(exc),
            )
            raise
        self.db.record_activity_operation(
            "活动申报",
            "成功",
            store.id,
            store.name,
            int(activity_id),
            1,
            "店铺活动申报成功",
            result,
        )
        return {
            "store_id": store.id,
            "store_name": store.name,
            "activity_id": int(activity_id),
            "result": result,
        }

    def activity_preview(
        self,
        store: Store,
        activity_id: int,
        discount_basis_points: int = 9000,
        min_discount_cents: int = 100,
    ) -> dict[str, Any]:
        base = {
            "store_id": store.id,
            "store_name": store.name,
            "status": "跳过",
            "reason": "",
            "rows": [],
            "valid_sku_count": 0,
            "conflict_count": 0,
        }
        if store.web_status != "已登录":
            base["reason"] = f"网页{store.web_status}"
            return base
        try:
            client = ShopPrivateClient(self.db, store)
        except Exception as exc:
            base["reason"] = str(exc)
            return base
        close_status = client.account_close_status()
        if close_status is not None and close_status != 100:
            base["reason"] = f"店铺状态受限（状态码 {close_status}）"
            return base
        try:
            eligible = client.activity_eligible_products(activity_id)
        except Exception as exc:
            base["reason"] = str(exc)
            return base
        if not eligible:
            base["reason"] = "平台没有返回当前可报名商品"
            return base
        try:
            submitted = client.activity_submitted_products(activity_id)
            submitted_keys = {
                (
                    str(row.get("productId") or ""),
                    str(row.get("skuId") or ""),
                )
                for row in submitted
                if row.get("productId") and row.get("skuId")
            }
        except Exception:
            submitted_keys = set()
        if submitted_keys:
            eligible = [
                item
                for item in eligible
                if (
                    str(
                        (item.get("product") or {}).get("productId") or item.get("productId") or ""
                    ),
                    str((item.get("product") or {}).get("skuId") or item.get("skuId") or ""),
                )
                not in submitted_keys
            ]
        if not eligible:
            base["reason"] = (
                "可提报商品均已提交" if submitted_keys else "平台没有返回当前可报名商品"
            )
            return base

        validation_rows: dict[tuple[str, str], dict[str, Any]] = {}
        for start in range(0, len(eligible), 200):
            batch = eligible[start : start + 200]
            validation_items = []
            for item in batch:
                product = item.get("product") or {}
                product_id = str(item.get("productId") or product.get("productId") or "")
                sku_id = str(item.get("skuId") or product.get("skuId") or "")
                if product_id and sku_id:
                    validation_items.append(
                        {
                            "productId": product_id,
                            "skuId": sku_id,
                            "activityStockNum": 0,
                            "activityPrice": 1,
                            "sellingPointWording": "",
                            "stockMode": 1,
                            "applyType": 0,
                        }
                    )
            if not validation_items:
                continue
            try:
                validation = client.activity_validate_products(
                    activity_id,
                    validation_items,
                )
            except Exception:
                validation = {}
            for row in validation.get("resultList") or []:
                if not isinstance(row, dict):
                    continue
                product = row.get("product") or {}
                key = (
                    str(product.get("productId") or ""),
                    str(product.get("skuId") or ""),
                )
                if all(key):
                    validation_rows[key] = row

        base["status"] = "可预览"
        discount_basis_points = max(100, min(10000, int(discount_basis_points)))
        min_discount_cents = max(0, int(min_discount_cents))
        for item in eligible:
            product = item.get("product") or {}
            product_id = str(item.get("productId") or product.get("productId") or "")
            sku_id = str(item.get("skuId") or product.get("skuId") or "")
            try:
                current_price = int(
                    item.get("salePrice")
                    or (item.get("activityPriceCalInfo") or {}).get("salePrice")
                    or 0
                )
            except (TypeError, ValueError):
                current_price = 0
            try:
                stock = int(item.get("commStockNum") or 0)
            except (TypeError, ValueError):
                stock = 0
            title = str(item.get("productName") or item.get("title") or product_id)
            validation = validation_rows.get((product_id, sku_id), {})
            invalid = str(
                item.get("invalidString")
                or item.get("invalidStringPrecheck")
                or validation.get("invalidString")
                or validation.get("invalidStringPrecheck")
                or ""
            ).strip()
            regular_price = _discount_price_cents(
                current_price,
                Decimal(discount_basis_points) / Decimal(10000),
            )
            floor_price = current_price - min_discount_cents
            configured_price = min(regular_price, floor_price)
            try:
                platform_limit = int(validation.get("validPrice") or 0)
            except (TypeError, ValueError):
                platform_limit = 0
            activity_price = (
                min(configured_price, platform_limit) if platform_limit > 0 else configured_price
            )
            discount_cents = current_price - activity_price
            rate = round(activity_price / current_price * 10, 2) if current_price else 0.0
            status_text = "可报名"
            if invalid:
                status_text = invalid
            elif not product_id or not sku_id or current_price <= 0:
                status_text = "价格或SKU异常"
            elif activity_price < 1:
                status_text = "活动价低于0.01元"
            elif current_price <= min_discount_cents:
                status_text = "原价不足最低优惠"
            if status_text == "可报名":
                base["valid_sku_count"] += 1
            else:
                base["conflict_count"] += 1
            base["rows"].append(
                {
                    "store_id": store.id,
                    "store_name": store.name,
                    "product_id": product_id,
                    "title": title,
                    "sku_id": sku_id,
                    "current_price": current_price,
                    "activity_price": activity_price,
                    "discount_cents": discount_cents,
                    "actual_discount": rate,
                    "stock": stock,
                    "status": status_text,
                }
            )
        if base["valid_sku_count"] == 0:
            base["reason"] = "没有符合条件的在售商品" if base["conflict_count"] else "没有在售商品"
        return base

    def activity_submit_items(
        self,
        store: Store,
        activity_id: int,
        items: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if not items:
            raise ValueError("没有可提交的SKU")
        client = ShopPrivateClient(self.db, store)
        normalized = []
        for item in items:
            product_id = str(item.get("productId") or "").strip()
            sku_id = str(item.get("skuId") or "").strip()
            try:
                activity_price = int(item.get("activityPrice") or item.get("newActivityPrice") or 0)
            except (TypeError, ValueError):
                activity_price = 0
            if not product_id or not sku_id or activity_price <= 0:
                continue
            normalized.append(
                {
                    "productId": product_id,
                    "skuId": sku_id,
                    "activityStockNum": 0,
                    "activityPrice": activity_price,
                    "sellingPointWording": "",
                    "stockMode": 1,
                    "applyType": 0,
                }
            )
        if not normalized:
            raise ValueError("没有字段完整的可提交SKU")
        # Platform activity imports silently confirm at most 200 rows.
        batches = [normalized[index : index + 200] for index in range(0, len(normalized), 200)]
        try:
            completed_batches = []
            for batch in batches:
                validation = client.activity_validate_products(
                    activity_id,
                    batch,
                )
                invalid_keys = {
                    (
                        str(row.get("product", {}).get("productId") or ""),
                        str(row.get("product", {}).get("skuId") or ""),
                    )
                    for row in validation.get("resultList") or []
                    if isinstance(row, dict)
                    and (
                        str(row.get("invalidString") or "").strip()
                        or str(row.get("invalidStringPrecheck") or "").strip()
                    )
                }
                valid_items = [
                    item for item in batch if (item["productId"], item["skuId"]) not in invalid_keys
                ]
                if not valid_items:
                    raise ValueError("平台校验未通过，本批次没有可提交SKU")
                added = client.activity_update_products(
                    activity_id,
                    valid_items,
                    action_type=2,
                    add_source=3,
                )
                completed_batches.append(
                    {
                        "count": len(valid_items),
                        "invalid_count": len(invalid_keys),
                        "validation": validation,
                        "added": added,
                    }
                )
        except Exception as exc:
            self.db.record_activity_operation(
                "活动商品提交",
                "失败",
                store.id,
                store.name,
                int(activity_id),
                len(normalized),
                str(exc),
            )
            raise
        result = {
            "store_id": store.id,
            "store_name": store.name,
            "activity_id": int(activity_id),
            "count": len(normalized),
            "batch_count": len(completed_batches),
            "batches": completed_batches,
        }
        self.db.set_setting(
            f"activity_last_result_{store.id}_{activity_id}",
            json.dumps(result, ensure_ascii=False),
        )
        self.db.record_activity_operation(
            "活动商品提交",
            "成功",
            store.id,
            store.name,
            int(activity_id),
            len(normalized),
            "活动商品价格提交成功",
            result,
        )
        return result

    def double11_remove_all(
        self,
        store: Store,
        activity_id: int = 1323,
    ) -> dict[str, Any]:
        client = ShopPrivateClient(self.db, store)
        rows: list[dict[str, Any]] = []
        for page in range(1, 101):
            result = client.activity_products(
                activity_id,
                page=page,
                page_size=100,
            )
            page_rows = [row for row in (result.get("productList") or []) if isinstance(row, dict)]
            rows.extend(page_rows)
            if len(page_rows) < 100:
                break
        items = []
        for row in rows:
            product_id = str(row.get("productId") or "")
            sku_id = str(row.get("skuId") or "")
            if not product_id or not sku_id:
                continue
            items.append(
                {
                    "productId": product_id,
                    "skuId": sku_id,
                    "applyType": 0,
                }
            )
        if not items:
            return {
                "activity_id": int(activity_id),
                "count": 0,
                "message": "当前活动没有报名商品",
            }
        result = client.activity_update_products(
            activity_id,
            items,
            action_type=1,
            del_source=0,
        )
        return {
            "activity_id": int(activity_id),
            "count": len(items),
            "result": result,
        }

    def double11_submit(
        self,
        store: Store,
        drafts: list[dict[str, Any]],
        activity_id: int = 1323,
    ) -> dict[str, Any]:
        if not drafts:
            raise ValueError("双十一草稿为空")
        items = []
        missing = []
        for draft in drafts:
            product_id = str(draft.get("product_id") or "")
            cached = self.db.get_product(store.id, product_id)
            if cached is None:
                missing.append(product_id)
                continue
            try:
                raw = json.loads(cached.get("raw_json") or "{}")
            except json.JSONDecodeError:
                raw = {}
            skus = raw.get("skus") or []
            price = int(draft.get("activity_price") or 0)
            for sku in skus:
                if not isinstance(sku, dict):
                    continue
                sku_id = str(sku.get("sku_id") or "")
                if not sku_id:
                    continue
                items.append(
                    {
                        "productId": product_id,
                        "skuId": sku_id,
                        "newActivityPrice": price,
                        "applyType": 1,
                    }
                )
        if missing:
            raise ValueError("以下商品缺少本地详情，无法提交：" + "、".join(missing[:10]))
        if not items:
            raise ValueError("草稿商品没有可提交的SKU")
        client = ShopPrivateClient(self.db, store)
        added = client.activity_update_products(
            activity_id,
            items,
            action_type=2,
            add_source=1,
        )
        checked = client.activity_check_prices(activity_id, items)
        submitted = client.activity_submit_prices(
            activity_id,
            items,
            apply_type=1,
        )
        result = {
            "activity_id": int(activity_id),
            "count": len(items),
            "added": added,
            "checked": checked,
            "submitted": submitted,
        }
        self.db.set_setting(
            f"double11_last_result_{store.id}",
            json.dumps(result, ensure_ascii=False),
        )
        return result

    def sync_platform_violation_blacklist(
        self,
        store: Store,
    ) -> SyncResult:
        products = ShopPrivateClient(
            self.db,
            store,
        ).platform_violation_products()
        try:
            existing = json.loads(self.db.get_setting("violation_blacklist", "[]"))
        except json.JSONDecodeError:
            existing = []
        rows = [dict(row) for row in existing if isinstance(row, dict)]
        by_id = {str(row.get("product_id")): row for row in rows if row.get("product_id")}
        added = 0
        for product in products:
            product_id = str(product["product_id"])
            row = by_id.get(product_id)
            if row is None:
                row = {
                    "product_id": product_id,
                    "title": str(product.get("title") or ""),
                    "reason": "",
                    "added": datetime.now().strftime("%Y-%m-%d %H:%M"),
                }
                rows.append(row)
                by_id[product_id] = row
                added += 1
            if not row.get("title") and product.get("title"):
                row["title"] = product["title"]
            reason = f"平台同步：{product.get('reason') or '违规/预警'}"
            if reason not in str(row.get("reason") or ""):
                row["reason"] = f"{row.get('reason')}\n{reason}".strip()
        self.db.set_setting(
            "violation_blacklist",
            json.dumps(rows, ensure_ascii=False),
        )
        return SyncResult(
            "platform_violation_blacklist",
            len(products),
            f"平台违规同步完成：发现 {len(products)}，新增 {added}",
            success=len(products),
        )

    def sync_violation_library(self, store: Store) -> SyncResult:
        records = [
            record
            for record in ShopPrivateClient(
                self.db,
                store,
            ).platform_violation_records()
            if is_explicit_violation_record(record)
        ]
        try:
            existing = json.loads(self.db.get_setting("violation_library", "[]"))
        except (TypeError, ValueError):
            existing = []
        rows = [
            dict(row)
            for row in existing
            if isinstance(row, dict)
            and str(row.get("store_app_id") or "") != store.app_id
            and (
                row.get("store_app_id")
                or int(row.get("store_id") or 0) != store.id
            )
        ]
        for record in records:
            rows.append(
                {
                    **record,
                    "store_id": store.id,
                    "store_app_id": store.app_id,
                    "store_name": store.name,
                    "synced_at": datetime.now().isoformat(timespec="seconds"),
                }
            )
        self.db.set_setting(
            "violation_library",
            json.dumps(rows, ensure_ascii=False),
        )
        try:
            blacklist = json.loads(self.db.get_setting("violation_blacklist", "[]"))
        except (TypeError, ValueError):
            blacklist = []
        blocked = [dict(row) for row in blacklist if isinstance(row, dict)]
        by_product = {str(row.get("product_id")): row for row in blocked if row.get("product_id")}
        for record in records:
            product_id = str(record.get("product_id") or "")
            if not product_id:
                continue
            item = by_product.get(product_id)
            if item is None:
                item = {
                    "product_id": product_id,
                    "title": record.get("title", ""),
                    "reason": "",
                    "added": datetime.now().strftime("%Y-%m-%d %H:%M"),
                }
                blocked.append(item)
                by_product[product_id] = item
            reason = f"平台同步：{record.get('reason') or '违规/预警'}"
            if reason not in str(item.get("reason") or ""):
                item["reason"] = (f"{item.get('reason') or ''}\n{reason}").strip()
        self.db.set_setting(
            "violation_blacklist",
            json.dumps(blocked, ensure_ascii=False),
        )
        return SyncResult(
            "violation_library",
            len(records),
            f"明确违规商品已同步：{store.name} {len(records)} 条",
            success=len(records),
        )

    def fetch_funds_summary(self, store: Store, start_time: int, end_time: int) -> dict[str, Any]:
        client = self.client_for(store)
        if isinstance(client, MockShopClient):
            return {
                "available_amount": 10000,
                "pending_amount": 2000,
                "flow_list": [],
            }
        balance = client.fetch_fund_balance()
        flows = client.fetch_funds_flows(start_time, end_time)
        balance["flow_list"] = [
            item for item in flows.get("flow_list", []) if isinstance(item, dict)
        ]
        return balance


def date_range(start_text: str, end_text: str) -> tuple[int, int]:
    start = datetime.strptime(start_text.strip(), "%Y-%m-%d")
    end = datetime.strptime(end_text.strip(), "%Y-%m-%d")
    if end < start:
        raise ValueError("结束日期不能早于开始日期")
    if (end - start).days > 6:
        raise ValueError("单次同步最多选择 7 个自然日")
    start_time = int(start.timestamp())
    end_time = int(end.timestamp()) + 86399
    return start_time, end_time


class _TaskCancelled(Exception):
    pass
