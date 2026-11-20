from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import threading
import tkinter as tk
import uuid
from collections import defaultdict, deque
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import TYPE_CHECKING, Any, Callable

from ..models import Store
from ..shop_private import ShopPrivateClient
from ..source_1688 import (
    Link1688Collector,
    Source1688LoginRequired,
    normalize_1688_capture,
    normalized_1688_row,
    parse_1688_links,
    rank_category_candidates,
)
from ..wechat_api import WechatApiError
from .product_link_dialog import auto_match_skus
from .theme import MUTED, PANEL, TEXT, button

if TYPE_CHECKING:
    from .app import MainWindow


def _json_rows(value: str, default: Any) -> Any:
    try:
        parsed = json.loads(value or "")
    except (TypeError, ValueError):
        return default
    return parsed if isinstance(parsed, type(default)) else default


def _price_cents(value: Any) -> int:
    try:
        number = float(str(value or "0").replace("¥", "").replace(",", ""))
    except (TypeError, ValueError):
        return 0
    return max(0, int(round(number * 100)))


def _optional_non_negative_int(value: Any) -> int | None:
    text = str(value or "").strip().replace(",", "")
    if not text:
        return None
    try:
        number = int(text)
    except (TypeError, ValueError) as exc:
        raise ValueError("库存筛选必须是整数") from exc
    if number < 0:
        raise ValueError("库存筛选不能小于 0")
    return number


_DIRECT_STOCK_KEYS = (
    "stock",
    "库存",
    "totalStockNum",
    "total_stock",
    "totalStock",
    "stockNum",
    "stock_num",
    "quantity",
    "amountOnSale",
    "canBookCount",
    "sellableQuantity",
    "availableQuantity",
    "inventory",
)
_SKU_CONTAINER_KEYS = (
    "skus",
    "skuInfo",
    "skuInfos",
    "skuList",
    "skuBase",
    "skuCore",
    "sku2info",
)


def _stock_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    try:
        number = Decimal(text)
    except (InvalidOperation, ValueError):
        return None
    if not number.is_finite():
        return None
    return max(0, int(number))


def _direct_stock(mapping: dict[str, Any]) -> int | None:
    for key in _DIRECT_STOCK_KEYS:
        if key not in mapping:
            continue
        value = _stock_int(mapping.get(key))
        if value is not None:
            return value
    return None


def _sku_container_total(value: Any) -> int | None:
    values: list[int] = []
    if isinstance(value, dict):
        direct = _direct_stock(value)
        if direct is not None:
            values.append(direct)
        else:
            for item in value.values():
                if not isinstance(item, dict):
                    continue
                stock = _direct_stock(item)
                if stock is not None:
                    values.append(stock)
    elif isinstance(value, list):
        for item in value:
            if not isinstance(item, dict):
                continue
            stock = _direct_stock(item)
            if stock is not None:
                values.append(stock)
    return sum(values) if values else None


def _nested_sku_stock_totals(
    mapping: dict[str, Any],
    *,
    depth: int = 0,
) -> list[int]:
    if depth > 5:
        return []
    totals: list[int] = []
    for key in _SKU_CONTAINER_KEYS:
        if key not in mapping:
            continue
        value = mapping.get(key)
        total = _sku_container_total(value)
        if total is not None:
            totals.append(total)
        if isinstance(value, dict):
            for child_key, child in value.items():
                if child_key in _SKU_CONTAINER_KEYS:
                    child_total = _sku_container_total(child)
                    if child_total is not None:
                        totals.append(child_total)
    for value in mapping.values():
        if isinstance(value, dict):
            totals.extend(
                _nested_sku_stock_totals(value, depth=depth + 1)
            )
    return totals


def _normalized_stock(
    row: dict[str, Any] | None,
    payload: dict[str, Any] | None = None,
) -> tuple[int | None, bool]:
    for mapping in (row, payload):
        if not isinstance(mapping, dict):
            continue
        direct = _direct_stock(mapping)
        if direct is not None:
            return direct, True
    totals: list[int] = []
    for mapping in (row, payload):
        if isinstance(mapping, dict):
            totals.extend(_nested_sku_stock_totals(mapping))
    if totals:
        return max(totals), True
    return None, False


def _row_stock(row: dict[str, Any]) -> int:
    return _stock_int(row.get("stock")) or 0


def _row_stock_known(row: dict[str, Any]) -> bool:
    if "stock_known" in row:
        return bool(row.get("stock_known"))
    return "stock" in row and _stock_int(row.get("stock")) is not None


def _row_price_cents(row: dict[str, Any]) -> int:
    value = row.get("price")
    if isinstance(value, (int, float)):
        return max(0, int(value))
    return _price_cents(value)


def _stores_matching_group_names(
    stores: list[Store],
    group_names: list[str],
) -> list[Store]:
    selected = {
        str(group_name).strip()
        for group_name in group_names
        if str(group_name).strip()
    }
    return [
        store
        for store in stores
        if (store.group_name or "未分组") in selected
    ]


BATCH_FILTER_MODES = (
    "全部商品",
    "标题包含",
    "SKU包含",
    "库存范围",
    "价格范围",
)


def _optional_money_cents(value: Any) -> int | None:
    text = str(value or "").strip().replace("¥", "").replace(",", "")
    if not text:
        return None
    try:
        number = float(text)
    except (TypeError, ValueError) as exc:
        raise ValueError("价格筛选必须是数字") from exc
    if number < 0:
        raise ValueError("价格筛选不能小于 0")
    return int(round(number * 100))


def _sku_search_text(row: dict[str, Any]) -> str:
    values: list[str] = []

    def visit(
        value: Any,
        depth: int = 0,
        include_all: bool = False,
    ) -> None:
        if depth > 5:
            return
        if isinstance(value, dict):
            for key, item in value.items():
                key_text = str(key).casefold()
                if include_all or (
                    "sku" in key_text
                    or "spec" in key_text
                    or "code" in key_text
                ):
                    visit(item, depth + 1, True)
                elif isinstance(item, (dict, list, tuple)):
                    visit(item, depth + 1, False)
        elif isinstance(value, (list, tuple)):
            for item in value[:200]:
                visit(item, depth + 1, include_all)
        elif value is not None:
            values.append(str(value))

    for key in (
        "sku",
        "sku_id",
        "skuId",
        "sku_code",
        "out_sku_id",
        "specs",
        "sku_attrs",
        "skus",
        "skuList",
        "payload",
        "raw",
    ):
        if key in row:
            visit(row[key], include_all=True)
    return " ".join(values).casefold()


def _filter_batch_rows(
    items: list[dict[str, Any]],
    *,
    mode: str,
    keyword: str,
    minimum: str,
    maximum: str,
    platform: str,
) -> list[dict[str, Any]]:
    normalized_mode = mode if mode in BATCH_FILTER_MODES else "全部商品"
    term = keyword.strip().casefold()
    if normalized_mode == "库存范围":
        min_value = _optional_non_negative_int(minimum)
        max_value = _optional_non_negative_int(maximum)
    elif normalized_mode == "价格范围":
        min_value = _optional_money_cents(minimum)
        max_value = _optional_money_cents(maximum)
    else:
        min_value = max_value = None
    if (
        min_value is not None
        and max_value is not None
        and max_value < min_value
    ):
        raise ValueError("筛选上限不能小于下限")

    result: list[dict[str, Any]] = []
    for row in items:
        if not isinstance(row, dict):
            continue
        if platform != "全部平台" and row.get("platform") != platform:
            continue
        if normalized_mode == "标题包含":
            searchable = str(row.get("title") or "").casefold()
            if term and term not in searchable:
                continue
        elif normalized_mode == "SKU包含":
            if term and term not in _sku_search_text(row):
                continue
        elif normalized_mode == "库存范围":
            if not _row_stock_known(row):
                continue
            value = _row_stock(row)
            if min_value is not None and value < min_value:
                continue
            if max_value is not None and value > max_value:
                continue
        elif normalized_mode == "价格范围":
            value = _row_price_cents(row)
            if min_value is not None and value < min_value:
                continue
            if max_value is not None and value > max_value:
                continue
        result.append(row)
    return result


def _definitive_upload_failure_message(exc: Exception) -> str:
    """Return a user-facing message only when the remote definitely rejected."""
    if not isinstance(exc, WechatApiError):
        return ""
    # Transport failures have no structured remote error code. The request may
    # have reached WeChat before the timeout/reset/parse failure, so it must be
    # verified remotely instead of being offered for automatic retry.
    if exc.code is None:
        return ""
    if exc.code in {-1, 45011}:
        return ""
    if exc.code == 6600145:
        return (
            "商品未创建：该店铺没有目标类目权限，"
            "请先到“开通类目”申请并审核通过后重试。"
        )
    if exc.code in {6601120, 6601121}:
        return (
            "商品未创建：类目属性数量格式不合法。"
            "该属性必须使用“正数 + 单位”，请检查模板属性规则。"
        )
    return f"商品信息检查未通过，未创建商品：{exc}"


def _required_category_ids(payload: dict[str, Any]) -> list[str]:
    explicit_apply_ids = payload.get("category_apply_ids")
    if isinstance(explicit_apply_ids, list):
        explicit = [
            str(value).strip()
            for value in explicit_apply_ids
            if str(value).strip()
        ]
        if explicit:
            return explicit
    category_ids: list[str] = []
    seen: set[str] = set()
    for key in ("cats", "cats_v2"):
        values = payload.get(key)
        for value in values if isinstance(values, list) else []:
            if isinstance(value, dict):
                category_id = str(
                    value.get("cat_id")
                    or value.get("catId")
                    or value.get("categoryId")
                    or ""
                ).strip()
            else:
                category_id = str(value or "").strip()
            if category_id and category_id not in seen:
                seen.add(category_id)
                category_ids.append(category_id)
    if not category_ids and payload.get("category_path"):
        category_id = str(payload.get("cat_id") or "").strip()
        if category_id:
            category_ids.append(category_id)
    return category_ids


def _is_category_permission_error(exc: Exception) -> bool:
    if not isinstance(exc, WechatApiError):
        return False
    message = str(exc).casefold()
    return exc.code == 6600145 or (
        "类目" in message
        and any(word in message for word in ("权限", "开通", "未授权"))
    )


def _retry_upload_after_category_open(
    payload: dict[str, Any],
    upload: Callable[[], dict[str, Any]],
    category_client: Any | Callable[[], Any],
    progress: Callable[[str], None] | None = None,
    category_ids: list[str] | None = None,
) -> dict[str, Any]:
    try:
        return upload()
    except Exception as exc:
        if not _is_category_permission_error(exc):
            raise
        category_ids = category_ids or _required_category_ids(payload)
        if not category_ids:
            raise
        if progress is not None:
            progress("正在检查并申请目标类目")
        try:
            client = (
                category_client()
                if callable(category_client)
                else category_client
            )
        except Exception as exc:
            raise CategoryAutoOpenError(
                "无法连接店铺网页后台：" + str(exc)
            ) from exc
        statuses: dict[str, str] = {}
        try:
            statuses = client.category_relation_statuses(
                category_ids
            )
        except Exception:
            statuses = {}
        if not statuses or not all(
            statuses.get(category_id) == "已生效"
            for category_id in category_ids
        ):
            if progress is not None:
                progress("正在提交类目开通申请")
            try:
                client.batch_add_categories(category_ids)
            except Exception as exc:
                raise CategoryAutoOpenError(
                    "类目开通申请失败：" + str(exc)
                ) from exc
            try:
                statuses = client.category_relation_statuses(
                    category_ids
                )
            except Exception:
                statuses = {}
        if statuses and not all(
            statuses.get(category_id) == "已生效"
            for category_id in category_ids
        ):
            raise CategoryApprovalPending(category_ids, statuses)
        if progress is not None:
            progress("类目已生效，正在自动重新上货")
        try:
            return upload()
        except Exception as retry_exc:
            if _is_category_permission_error(retry_exc):
                raise CategoryApprovalPending(
                    category_ids,
                    statuses,
                ) from retry_exc
            raise


def _stable_source_fingerprint(
    platform: str,
    title: str,
    row: dict[str, Any],
    payload: dict[str, Any] | None,
) -> str:
    """Build a deterministic identity when the package has no product ID."""
    identity = {
        "platform": re.sub(r"\s+", " ", platform).strip().casefold(),
        "title": re.sub(r"\s+", " ", title).strip().casefold(),
        "price": row.get("price")
        or row.get("sale_price")
        or row.get("priceLow")
        or "",
        "stock": row.get("stock")
        or row.get("库存")
        or row.get("totalStockNum")
        or "",
        "specs": row.get("specs")
        or row.get("productSaleAttrs")
        or (payload or {}).get("specs")
        or (payload or {}).get("productSaleAttrs")
        or "",
        "skus": row.get("skus")
        or row.get("skuList")
        or (payload or {}).get("skus")
        or (payload or {}).get("skuList")
        or "",
        "images": _image_urls(
            row.get("itemImgs")
            or row.get("headImg")
            or (payload or {}).get("head_imgs")
        ),
        "raw": row,
        "payload": payload or {},
    }
    canonical = json.dumps(
        identity,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"source-fp-{digest[:24]}"


def _recover_upload_tasks(tasks: list[Any]) -> bool:
    """Assign stable identities and protect uploads with unknown results."""
    changed = False
    for task in tasks:
        if not isinstance(task, dict):
            continue
        if not str(task.get("id") or "").strip():
            task["id"] = f"up_{uuid.uuid4().hex[:12]}"
            changed = True
        items = task.get("items")
        if not isinstance(items, list):
            task["items"] = []
            items = task["items"]
            changed = True
        verification_count = 0
        for item in items:
            if not isinstance(item, dict):
                continue
            if not str(item.get("item_id") or "").strip():
                item["item_id"] = uuid.uuid4().hex
                changed = True
            status = str(item.get("status") or "pending")
            if status in {"submitting", "running"}:
                item["status"] = "verification_pending"
                item["message"] = (
                    "上次提交结果未知，请先在远端核实，不能直接重复创建"
                )
                changed = True
            elif status == "queued":
                item["status"] = "paused"
                item["message"] = "上次关闭前尚未提交，可继续上传"
                changed = True
            if item.get("status") == "verification_pending":
                verification_count += 1
        task_status = str(task.get("status") or "pending")
        if verification_count:
            task["status"] = "needs_verification"
            task["message"] = (
                f"待核实 {verification_count} 个商品，请先核对远端结果"
            )
            changed = True
        elif task_status in {"running", "queued"}:
            task["status"] = "paused"
            task["message"] = "上次软件关闭时任务未完成，可继续上传"
            changed = True
    return changed


@dataclass(slots=True)
class CategoryApplyResult:
    message: str
    errors: list[str]
    detail: str
    store_statuses: dict[int, dict[str, str]]


TAOBAO_CATEGORY_PROFILES: dict[str, dict[str, Any]] = {
    "50007068": {
        "cats": [
            {"cat_id": "6033"},
            {"cat_id": "6057"},
            {"cat_id": "547703"},
        ],
        "cats_v2": [
            {"cat_id": "10000111"},
            {"cat_id": "10000113"},
            {"cat_id": "10000672"},
            {"cat_id": "547703"},
        ],
        "attrs": [
            {"attr_key": "厚薄", "attr_value": "厚"},
            {"attr_key": "面料材质", "attr_value": "聚酯纤维"},
            {"attr_key": "面料材质成分含量", "attr_value": "0.1000 %"},
        ],
        "product_qua_infos": [
            {"qua_id": "50000043", "qua_url": []},
            {"qua_id": "50000074", "qua_url": []},
            {"qua_id": "50000044", "qua_url": []},
            {"qua_id": "1111491", "qua_url": []},
            {"qua_id": "1111496", "qua_url": []},
        ],
    },
    "162201": {
        "cats": [
            {"cat_id": "6033"},
            {"cat_id": "6057"},
            {"cat_id": "547675"},
        ],
        "cats_v2": [
            {"cat_id": "10000111"},
            {"cat_id": "10000113"},
            {"cat_id": "10000666"},
            {"cat_id": "547675"},
        ],
        "attrs": [
            {"attr_key": "弹力", "attr_value": "有弹性"},
            {"attr_key": "风格", "attr_value": "运动风;文艺风"},
            {"attr_key": "厚薄", "attr_value": "厚款"},
            {"attr_key": "裤长", "attr_value": "长裤"},
            {"attr_key": "款式", "attr_value": "直筒裤"},
            {"attr_key": "面料材质", "attr_value": "聚酯纤维"},
            {"attr_key": "面料材质成分含量", "attr_value": "97 %"},
            {"attr_key": "上市时间", "attr_value": "2026年秋季"},
            {"attr_key": "适用场景", "attr_value": "休闲外出;运动"},
            {"attr_key": "适用季节", "attr_value": "秋"},
            {"attr_key": "适用人群", "attr_value": "老少皆宜"},
            {"attr_key": "图案", "attr_value": "纯色"},
            {"attr_key": "腰型", "attr_value": "高腰"},
            {"attr_key": "功能", "attr_value": "透气"},
        ],
        "product_qua_infos": [
            {"qua_id": "50000043", "qua_url": []},
            {"qua_id": "50000074", "qua_url": []},
            {"qua_id": "50000044", "qua_url": []},
            {"qua_id": "1111491", "qua_url": []},
            {"qua_id": "1111496", "qua_url": []},
        ],
    },
}

WECHAT_CATEGORY_PROFILES: dict[tuple[str, ...], dict[str, Any]] = {
    ("6033", "6093", "549163"): {
        "cats_v2": ["10000111", "10000114", "10000845", "549163"],
    },
    ("6033", "6093", "549164"): {
        "cats_v2": ["10000111", "10000114", "10000845", "549164"],
    },
    ("6033", "6093", "549165"): {
        "cats_v2": ["10000111", "10000114", "10000845", "549165"],
    },
    ("6033", "6093", "549166"): {
        "cats_v2": ["10000111", "10000114", "10000845", "549166"],
    },
    ("6033", "6093", "6099"): {
        "cats_v2": ["10000111", "10000114", "6099"],
    },
    ("6033", "6093", "6117"): {
        "cats_v2": ["10000111", "10000114", "6117"],
    },
    ("6033", "6093", "6112"): {
        "cats": ["6033", "6093", "6098"],
        "cats_v2": ["10000111", "10000114", "6098"],
    },
    ("6932", "7137", "545263"): {
        "cats_v2": ["10000183", "10000189", "545263"],
    },
    ("6932", "7117", "7125"): {
        "cats": ["6932", "7137", "545263"],
        "cats_v2": ["10000183", "10000189", "545263"],
    },
    ("6831", "6843", "6845"): {
        "cats": ["6831", "6843", "547711"],
        "cats_v2": ["10000173", "10000175", "10000673", "547711"],
    },
}


DEFAULT_PRODUCT_SERVICES = {
    "seven_day_return": 1,
    "pay_after_use": 1,
    "freight_insurance": 1,
    "damage_guarantee": 0,
    "fake_one_pay_three": 0,
    "exchange_support": 1,
}


def _normalize_listing_mode(template: dict[str, Any] | None) -> str:
    template = template or {}
    value = str(template.get("listing_mode") or "").strip().lower()
    if template.get("save_as_draft") or value in {
        "draft",
        "delayed",
    }:
        return "draft"
    if value in {"warehouse", "offline", "down"}:
        return "warehouse"
    # "manual" was the legacy value even though the upload never waited for
    # a manual action. New and legacy defaults both mean immediate listing.
    return "immediate"


LISTING_MODE_LABELS = {
    "immediate": "出售中",
    "warehouse": "仓库",
    "draft": "草稿箱",
}

DUPLICATE_FILTER_LABELS = {
    "store": "本店铺已上货",
    "computer": "本电脑已上货",
    "off": "关闭过滤",
}


def _normalize_duplicate_filter_mode(
    template: dict[str, Any] | None,
) -> str:
    template = template or {}
    value = str(
        template.get("duplicate_filter_mode") or ""
    ).strip().lower()
    if value in {"computer", "local", "all"}:
        return "computer"
    if value == "off" or template.get("skip_duplicate") is False:
        return "off"
    return "store"


def _is_duplicate_upload(
    mode: str,
    source_id: str,
    store_id: int,
    uploaded_by_store: dict[int, set[str]],
    uploaded_by_computer: set[str],
) -> bool:
    if not source_id or mode == "off":
        return False
    if mode == "computer":
        return source_id in uploaded_by_computer
    return source_id in uploaded_by_store[store_id]


def _image_urls(values: Any, limit: int = 9) -> list[str]:
    result: list[str] = []
    if isinstance(values, str):
        candidates = re.findall(
            r"(?:https?:)?//[^\s\"'<>]+",
            values.replace("&amp;", "&"),
        )
        if not candidates and values.strip().startswith(
            ("http://", "https://", "//")
        ):
            candidates = [values]
    elif isinstance(values, dict):
        candidates = [
            values.get("imgUrl")
            or values.get("imageUrl")
            or values.get("picUrl")
            or values.get("url")
            or "",
        ]
    elif isinstance(values, (list, tuple, set)):
        candidates = list(values)
    else:
        candidates = []
    for value in candidates:
        if isinstance(value, dict):
            value = (
                value.get("imgUrl")
                or value.get("imageUrl")
                or value.get("picUrl")
                or value.get("url")
                or ""
            )
        url = str(value or "").strip().replace("&amp;", "&")
        url = url.rstrip("),];")
        if url.startswith("//"):
            url = "https:" + url
        if url.startswith(("http://", "https://")) and url not in result:
            result.append(url)
        if len(result) >= limit:
            break
    return result


def _nested_image_urls(values: Any, limit: int = 9) -> list[str]:
    result: list[str] = []

    def collect(value: Any) -> None:
        if len(result) >= limit:
            return
        if isinstance(value, dict):
            for key, child in value.items():
                key_text = str(key).lower()
                if any(
                    token in key_text
                    for token in ("img", "image", "pic", "thumb", "photo")
                ):
                    for url in _image_urls(child, limit - len(result)):
                        if url not in result:
                            result.append(url)
                        if len(result) >= limit:
                            return
                elif isinstance(child, (dict, list, tuple)):
                    collect(child)
        elif isinstance(value, (list, tuple, set)):
            for child in value:
                collect(child)
                if len(result) >= limit:
                    return

    collect(values)
    return result


def _head_image_urls(
    primary: Any,
    skus: Any,
    detail_images: list[str],
) -> list[str]:
    result = _image_urls(primary)
    if len(result) < 3:
        for url in _nested_image_urls(skus, 9):
            if url not in result:
                result.append(url)
            if len(result) >= 3:
                break
    if len(result) < 3:
        for url in detail_images:
            if url not in result:
                result.append(url)
            if len(result) >= 3:
                break
    return result


def _taobao_sku_attributes(
    specs: dict[str, Any],
) -> list[tuple[str, dict[str, str]]]:
    groups: list[tuple[str, dict[str, str]]] = []
    for source_name, values in (specs or {}).items():
        if not isinstance(values, dict):
            continue
        key = str(source_name)
        if "颜色" in key:
            key = "颜色"
        elif any(word in key for word in ("尺码", "尺寸", "规格")):
            key = "规格"
        lookup: dict[str, str] = {}
        for spec_id, current in values.items():
            if isinstance(current, (list, tuple)) and current:
                lookup[str(spec_id)] = str(current[0])
            elif current:
                lookup[str(spec_id)] = str(current)
        groups.append((key, lookup))
    return groups


def _normalize_source_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if (
        payload.get("productName")
        or payload.get("productId")
        or payload.get("itemImgs")
    ):
        source_url = str(payload.get("productUrl") or "").strip()
        is_1688 = (
            payload.get("platform") == "1688"
            or "1688.com" in source_url.casefold()
        )
        category_id = str(payload.get("categoryId") or "")
        category_name = str(payload.get("categoryName") or "")
        category_path = str(payload.get("categoryPath") or "")
        category_hint = " ".join(
            value for value in (category_name, category_path) if value
        ).strip()
        profile = TAOBAO_CATEGORY_PROFILES.get(category_id)
        if profile is None and "打底裤" in category_name:
            profile = TAOBAO_CATEGORY_PROFILES["50007068"]
        elif profile is None and "裤" in category_name:
            profile = TAOBAO_CATEGORY_PROFILES["162201"]
        profile = deepcopy(profile or {})
        detail_images = _image_urls(payload.get("desc"), 50)
        images = _head_image_urls(
            [
                *(payload.get("itemImgs") or []),
                payload.get("productPicUrl"),
            ],
            payload.get("skus"),
            detail_images,
        )
        groups = _taobao_sku_attributes(payload.get("specs") or {})
        skus: list[dict[str, Any]] = []
        for index, item in enumerate(payload.get("skus") or [], 1):
            if not isinstance(item, dict):
                continue
            try:
                sale_price = int(item.get("price") or 0)
            except (TypeError, ValueError):
                sale_price = 0
            if sale_price <= 0:
                continue
            ids = [
                value.strip()
                for value in str(item.get("id") or "").split(";")
                if value.strip()
            ]
            sku_attrs = []
            for group_index, (key, lookup) in enumerate(groups):
                if group_index >= len(ids):
                    break
                value = lookup.get(ids[group_index])
                if value:
                    sku_attrs.append(
                        {"attr_key": key, "attr_value": value}
                    )
            try:
                stock = max(0, int(item.get("quantity") or 0))
            except (TypeError, ValueError):
                stock = 0
            skus.append(
                {
                    "out_sku_id": str(
                        item.get("skuId")
                        or f"{payload.get('productId', 'TB')}-{index}"
                    ),
                    "sale_price": sale_price,
                    "stock_num": stock,
                    "sku_code": str(item.get("skuId") or ""),
                    "sku_attrs": sku_attrs
                    or [{"attr_key": "规格", "attr_value": "默认规格"}],
                }
            )
        normalized = {
            "title": str(payload.get("productName") or ""),
            "short_title": str(payload.get("productName") or "")[:20],
            "out_product_id": str(payload.get("productId") or ""),
            "head_imgs": images,
            "desc_info": {
                "imgs": detail_images or images[:1],
                "desc": "",
            },
            "skus": skus,
            "deliver_method": 0,
            "brand_id": "2100000000",
            "extra_service": deepcopy(DEFAULT_PRODUCT_SERVICES),
        }
        attributes = payload.get("attribute")
        if isinstance(attributes, list):
            normalized["attrs"] = [
                {
                    "attr_key": str(item.get("key") or "").strip(),
                    "attr_value": str(item.get("value") or "").strip(),
                }
                for item in attributes
                if isinstance(item, dict)
                and str(item.get("key") or "").strip()
                and str(item.get("value") or "").strip()
            ]
        if is_1688:
            normalized.update(
                {
                    "platform": "1688",
                    "offer_id": str(payload.get("productId") or ""),
                    "source_url": source_url,
                    "category_name": category_name,
                    "category_hint": category_hint,
                    "detail_skip_first": 2,
                    "detail_images_before_skip": len(detail_images),
                    "detail_images_after_skip": max(
                        0,
                        len(detail_images) - 2,
                    ),
                }
            )
        normalized.update(profile)
        return normalized

    if payload.get("platform") == "1688":
        if payload.get("head_imgs") and payload.get("skus"):
            normalized = deepcopy(payload)
            source_id = str(
                normalized.get("source_product_id")
                or normalized.get("offer_id")
                or normalized.get("productId")
                or normalized.get("product_id")
                or ""
            ).strip()
            if source_id:
                normalized["source_product_id"] = source_id
                normalized["out_product_id"] = (
                    str(normalized.get("out_product_id") or "").strip()
                    or source_id
                )
                for index, sku in enumerate(
                    normalized.get("skus") or [],
                    1,
                ):
                    if not isinstance(sku, dict):
                        continue
                    sku.setdefault(
                        "out_sku_id",
                        f"1688-{source_id}-{index}",
                    )
            return normalized
        return normalize_1688_capture(
            {
                "offer_id": str(
                    payload.get("offerId")
                    or payload.get("offer_id")
                    or ""
                ),
                "url": str(
                    payload.get("source_url")
                    or payload.get("detailUrl")
                    or ""
                ),
                "dom": {
                    "title": str(
                        payload.get("subject")
                        or payload.get("title")
                        or ""
                    ),
                    "main_images": payload.get("mainImages")
                    or payload.get("imageList")
                    or [],
                    "detail_images": payload.get("detailImages")
                    or payload.get("detailImgs")
                    or [],
                    "category_paths": [
                        payload.get("categoryPath")
                        or payload.get("categoryName")
                        or ""
                    ],
                },
                "responses": [payload],
            }
        )


    if (
        payload.get("headImg")
        or payload.get("skuList")
        or payload.get("category")
    ):
        category = tuple(
            str(item.get("catId"))
            for item in payload.get("category") or []
            if isinstance(item, dict) and item.get("catId")
        )
        profile = deepcopy(WECHAT_CATEGORY_PROFILES.get(category) or {})
        mapped_cats = profile.pop("cats", None)
        cats = mapped_cats or [
            {"cat_id": cat_id}
            for cat_id in (
                (
                    profile.get("cats_v2")
                    if len(profile.get("cats_v2") or []) == len(category)
                    else category
                )
                or category
            )
        ]
        cats_v2_ids = profile.get("cats_v2") or list(category)
        attrs_map: dict[str, list[dict[str, str]]] = {}
        for group in payload.get("productSaleAttrs") or []:
            if not isinstance(group, dict):
                continue
            key = str(group.get("key") or "").strip()
            for item in group.get("items") or []:
                if not isinstance(item, dict):
                    continue
                attr = {
                    "attr_key": key,
                    "attr_value": str(item.get("value") or ""),
                }
                for sku_id in item.get("skuIds") or []:
                    attrs_map.setdefault(str(sku_id), []).append(dict(attr))
        raw_skus = (payload.get("skuList") or {}).get("skuInfo") or []
        skus = []
        for index, item in enumerate(raw_skus, 1):
            if not isinstance(item, dict):
                continue
            try:
                sale_price = int(item.get("price") or 0)
            except (TypeError, ValueError):
                sale_price = 0
            if sale_price <= 0:
                continue
            sku_id = str(item.get("skuId") or "")
            try:
                stock = max(0, int(item.get("stockNum") or 0))
            except (TypeError, ValueError):
                stock = 0
            skus.append(
                {
                    "out_sku_id": sku_id or f"WX-{index}",
                    "sale_price": sale_price,
                    "stock_num": stock,
                    "sku_code": sku_id,
                    "sku_attrs": attrs_map.get(sku_id)
                    or [{"attr_key": "规格", "attr_value": "默认规格"}],
                }
            )
        detail = payload.get("detail") or {}
        detail_images = _image_urls(detail.get("detailImg"), 50)
        head_images = _head_image_urls(
            payload.get("headImg"),
            payload.get("skuList"),
            detail_images,
        )
        return {
            "title": str(payload.get("title") or ""),
            "short_title": str(payload.get("shortTitle") or "")[:20],
            "head_imgs": head_images,
            "cats": cats,
            "cats_v2": [
                {"cat_id": str(cat_id)} for cat_id in cats_v2_ids
            ],
            "skus": skus,
            "desc_info": {
                "imgs": detail_images,
                "desc": str(detail.get("desc") or ""),
            },
            "deliver_method": 0,
            "brand_id": "2100000000",
            "extra_service": deepcopy(DEFAULT_PRODUCT_SERVICES),
        }
    return deepcopy(payload)


def _upload_source_relation(
    item: dict[str, Any],
    product: dict[str, Any] | None,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """Build the source-product link saved with a successful upload."""
    payload = item.get("payload")
    raw = item.get("raw")
    payload = payload if isinstance(payload, dict) else {}
    raw = raw if isinstance(raw, dict) else {}
    normalized = _normalize_source_payload(payload)
    platform = str(
        normalized.get("platform")
        or item.get("platform")
        or raw.get("platform")
        or ""
    ).strip()
    source_url = str(
        normalized.get("source_url")
        or raw.get("source_url")
        or raw.get("productUrl")
        or ""
    ).strip()
    source_product_id = str(
        item.get("source_product_id")
        or normalized.get("source_product_id")
        or normalized.get("offer_id")
        or normalized.get("out_product_id")
        or raw.get("offer_id")
        or ""
    ).strip()
    if not source_product_id and not source_url:
        return None, []

    source_skus = [
        dict(sku)
        for sku in normalized.get("skus") or []
        if isinstance(sku, dict)
    ]
    link = {
        "source_url": source_url,
        "platform": platform,
        "source_product_id": source_product_id,
        "source_title": str(
            normalized.get("title")
            or item.get("title")
            or raw.get("title")
            or ""
        ),
        "source_skus": source_skus,
        "note": "上货成功后自动关联",
        "address_info": {},
    }
    mappings: list[dict[str, Any]] = []
    if product:
        try:
            product_raw = json.loads(product.get("raw_json") or "{}")
        except (TypeError, ValueError):
            product_raw = {}
        store_skus = (
            product_raw.get("skus")
            if isinstance(product_raw, dict)
            else []
        )
        matches = auto_match_skus(
            [
                sku
                for sku in store_skus or []
                if isinstance(sku, dict)
            ],
            source_skus,
        )
        for store_sku_id, source_sku in matches.items():
            image = str(
                source_sku.get("thumb_img")
                or source_sku.get("thumb_url")
                or source_sku.get("sku_image")
                or ""
            ).strip()
            mappings.append(
                {
                    "store_sku_id": store_sku_id,
                    "purchase_sku_id": str(
                        source_sku.get("out_sku_id")
                        or source_sku.get("sku_id")
                        or ""
                    ),
                    "purchase_attrs": (
                        source_sku.get("sku_attrs")
                        if isinstance(source_sku.get("sku_attrs"), list)
                        else []
                    ),
                    "purchase_price": int(
                        source_sku.get("sale_price")
                        or source_sku.get("price")
                        or 0
                    ),
                    "purchase_image": image,
                }
            )
    return link, mappings


class TemplateRuleSkip(ValueError):
    """Raised when a template filter deliberately skips one product."""


class CategoryApprovalPending(RuntimeError):
    """Raised after a category application is submitted but not yet active."""

    def __init__(
        self,
        category_ids: list[str],
        statuses: dict[str, str],
    ) -> None:
        self.category_ids = category_ids
        self.statuses = statuses
        labels = "、".join(
            f"{category_id}（{statuses.get(category_id, '待核验')}）"
            for category_id in category_ids
        )
        super().__init__(f"类目已申请，等待审核生效：{labels}")


class CategoryAutoOpenError(RuntimeError):
    """The automatic category application could not be completed."""


class _ScrollableBody(ttk.Frame):
    def __init__(self, parent: tk.Widget):
        super().__init__(parent, style="TFrame")
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.canvas = tk.Canvas(
            self,
            background=PANEL,
            highlightthickness=0,
            borderwidth=0,
            yscrollincrement=16,
        )
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self._scrollbar_pending: tuple[str, tuple[str, ...]] | None = None
        self._scrollbar_job: str | None = None
        self.scrollbar = ttk.Scrollbar(
            self, orient="vertical", command=self._on_scrollbar
        )
        self.scrollbar.grid(row=0, column=1, sticky="ns")
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self._wheel_delta = 0
        self._wheel_job: str | None = None
        self.body = ttk.Frame(self.canvas, style="Panel.TFrame")
        self.window_id = self.canvas.create_window(
            (0, 0), window=self.body, anchor="nw"
        )
        self.body.bind("<Configure>", self._sync_scroll_region)
        self.canvas.bind("<Configure>", self._sync_width)
        self._wheel_binding = self.canvas.bind_all(
            "<MouseWheel>", self._on_mousewheel, add="+"
        )

    def _sync_scroll_region(self, _event=None) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _sync_width(self, event) -> None:
        self.canvas.itemconfigure(self.window_id, width=event.width)

    def _on_scrollbar(self, *args: str) -> None:
        if not args:
            return
        command = str(args[0])
        if command == "moveto" and len(args) >= 2:
            self._scrollbar_pending = (
                "moveto",
                (str(args[1]),),
            )
        elif command == "scroll" and len(args) >= 3:
            try:
                amount = int(float(args[1]))
            except (TypeError, ValueError):
                return
            if (
                self._scrollbar_pending is not None
                and self._scrollbar_pending[0] == "scroll"
                and self._scrollbar_pending[1][1:] == (str(args[2]),)
            ):
                previous = int(self._scrollbar_pending[1][0])
                amount += previous
            self._scrollbar_pending = (
                "scroll",
                (str(amount), str(args[2])),
            )
        else:
            return
        if self._scrollbar_job is None:
            self._scrollbar_job = self.after(
                16,
                self._flush_scrollbar,
            )

    def _flush_scrollbar(self) -> None:
        self._scrollbar_job = None
        pending = self._scrollbar_pending
        self._scrollbar_pending = None
        if pending is None:
            return
        try:
            self.canvas.yview(*pending[1])
        except tk.TclError:
            return

    def _on_mousewheel(self, event) -> None:
        try:
            target = getattr(event, "widget", None)
            if not self._contains_widget(target):
                target = self.winfo_containing(event.x_root, event.y_root)
            if self.winfo_exists() and self._contains_widget(target):
                delta = int(getattr(event, "delta", 0) or 0)
                if delta:
                    self._wheel_delta += delta
                    if self._wheel_job is None:
                        self._wheel_job = self.after_idle(
                            self._flush_wheel_scroll
                        )
        except (tk.TclError, TypeError):
            return

    def _flush_wheel_scroll(self) -> None:
        self._wheel_job = None
        delta = self._wheel_delta
        self._wheel_delta = 0
        if not delta:
            return
        steps = max(1, int(abs(delta) / 120))
        try:
            self.canvas.yview_scroll(
                steps if delta < 0 else -steps,
                "units",
            )
        except tk.TclError:
            return

    def _contains_widget(self, widget: tk.Misc | None) -> bool:
        while widget is not None:
            if widget is self:
                return True
            widget = getattr(widget, "master", None)
        return False

class UploadPage(ttk.Frame):
    title = "上传商品"
    subtitle = "1688链接/数据包 → 模板 → 店铺任务"

    def __init__(self, parent: tk.Widget, window: "MainWindow"):
        super().__init__(parent, style="TFrame", padding=(24, 16, 24, 14))
        self.window = window
        self.db = window.db
        self._task_lock = threading.RLock()
        self._run_requested = False
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)
        self._templates = _json_rows(
            self.db.get_setting("upload_templates", "[]"), []
        )
        self._batch = _json_rows(
            self.db.get_setting("upload_batch", "[]"), []
        )
        self._tasks = _json_rows(
            self.db.get_setting("upload_tasks", "[]"), []
        )
        recovered = _recover_upload_tasks(self._tasks)
        if recovered:
            self.db.set_setting(
                "upload_tasks",
                json.dumps(self._tasks, ensure_ascii=False),
            )
        self._batch_rows: dict[str, dict[str, Any]] = {}
        self._template_rows: dict[str, dict[str, Any]] = {}
        self._task_rows: dict[str, dict[str, Any]] = {}
        self._selected_batch_ids: set[str] = set()
        self._current_filtered_ids: set[str] = set()
        self._batch_page = 0
        self._batch_page_size = 200
        self._active_view = "templates"
        self._task_has_startable = False
        self._task_has_retry = False
        self._task_has_uploaded = False
        self._paused = False
        self._running = False
        self._category_retry_job: str | None = None
        self.filter_mode_var = tk.StringVar(value="全部商品")
        self.filter_keyword_var = tk.StringVar()
        self.filter_min_var = tk.StringVar()
        self.filter_max_var = tk.StringVar()
        self.platform_var = tk.StringVar(value="全部平台")
        self.batch_page_var = tk.StringVar(value="第 1 / 1 页")
        self.template_var = tk.StringVar(value="选择模板")
        self.assignment_var = tk.StringVar(value="每个店铺都上传")
        self.summary_var = tk.StringVar(value="0 个商品")
        self.batch_summary_var = tk.StringVar(value="本批次：0")
        self.task_summary_var = tk.StringVar(value="运行 0 · 排队 0 · 需处理 0 · 已完成 0")
        self.task_filter_var = tk.StringVar(value="全部状态")
        self.category_mode_var = tk.StringVar(value="单店申请")
        self.category_budget_var = tk.StringVar(value="1000")
        self.category_reference_var = tk.StringVar(value="")
        self.category_status_var = tk.StringVar(
            value="选择店铺并扫描可申请类目"
        )
        self._category_candidates: dict[str, dict[str, Any]] = {}
        self._category_store_options: dict[str, int] = {}
        self._category_catalog_cache: dict[int, list[dict[str, Any]]] = {}

        nav = ttk.Frame(self, style="TFrame")
        nav.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        self._view_buttons: dict[str, ttk.Button] = {}
        for key, label in (
            ("templates", "上货模板"),
            ("products", "上传商品"),
            ("tasks", "上传任务"),
            ("categories", "开通类目"),
        ):
            nav_button = button(
                nav,
                label,
                lambda view=key: self._show_view(view),
            )
            nav_button.pack(side="left", padx=(0, 6))
            self._view_buttons[key] = nav_button
        ttk.Label(
            nav,
            text="数据、模板和任务都保存在本机；提交前可逐项核对",
            style="Subtitle.TLabel",
        ).pack(side="right")

        self.content = ttk.Frame(self, style="TFrame")
        self.content.grid(row=2, column=0, sticky="nsew")
        self.content.columnconfigure(0, weight=1)
        self.content.rowconfigure(0, weight=1)
        self.views: dict[str, tk.Frame | ttk.Frame] = {}
        self._build_templates_view()
        self._build_products_view()
        self._build_tasks_view()
        self._build_categories_view()
        self._show_view("templates")
        self.winfo_toplevel().bind(
            "<Destroy>",
            self._cancel_category_retry,
            add="+",
        )
        self._schedule_category_retry()

    def _show_view(self, key: str) -> None:
        for view in self.views.values():
            view.grid_remove()
        self.views[key].grid(row=0, column=0, sticky="nsew")
        self._active_view = key
        for name, widget in self._view_buttons.items():
            widget.configure(
                style=(
                    "Primary.TButton"
                    if name == key
                    else "Secondary.TButton"
                )
            )
        if key == "templates":
            self._render_templates()
        elif key == "products":
            self._render_batch()
        elif key == "tasks":
            self._render_tasks()
            self._schedule_category_retry()
        elif key == "categories":
            self._render_category_stores()

    def _build_templates_view(self) -> None:
        view = ttk.Frame(self.content, style="TFrame")
        view.columnconfigure(0, weight=1)
        view.rowconfigure(2, weight=1)
        self.views["templates"] = view
        toolbar = ttk.Frame(view, style="TFrame")
        toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ttk.Label(
            toolbar,
            text="把标题、价格、库存和发布规则保存成可复用模板",
            style="Subtitle.TLabel",
        ).pack(side="left")
        button(toolbar, "添加上货模板", self._add_template, "Primary").pack(
            side="right"
        )
        button(
            toolbar,
            "进入上传商品",
            lambda: self._show_view("products"),
        ).pack(side="right", padx=6)
        body = ttk.Frame(view, style="Panel.TFrame")
        body.grid(row=2, column=0, sticky="nsew")
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=1)
        self.template_tree = ttk.Treeview(
            body,
            columns=("name", "title", "price", "stock", "listing", "updated"),
            show="headings",
            selectmode="extended",
        )
        for key, text, width in (
            ("name", "模板名称", 180),
            ("title", "标题规则", 280),
            ("price", "价格规则", 160),
            ("stock", "默认库存", 100),
            ("listing", "发布方式", 160),
            ("updated", "更新时间", 150),
        ):
            self.template_tree.heading(key, text=text)
            self.template_tree.column(key, width=width, anchor="w")
        self.template_tree.grid(row=0, column=0, sticky="nsew")
        ttk.Scrollbar(
            body, orient="vertical", command=self.template_tree.yview
        ).grid(row=0, column=1, sticky="ns")
        self.template_tree.configure(
            yscrollcommand=body.winfo_children()[-1].set
        )
        self.template_tree.bind(
            "<Double-1>", lambda _event: self._edit_template()
        )
        actions = ttk.Frame(view, style="TFrame")
        actions.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        self.template_edit_button = button(actions, "编辑模板", self._edit_template)
        self.template_edit_button.pack(side="left")
        self.template_copy_button = button(
            actions,
            "复制模板",
            self._duplicate_template,
        )
        self.template_copy_button.pack(side="left", padx=6)
        self.template_default_button = button(
            actions,
            "设为默认",
            self._set_default_template,
        )
        self.template_default_button.pack(side="left", padx=(0, 6))
        self.template_delete_button = button(
            actions, "删除模板", self._delete_template, "Danger"
        )
        self.template_delete_button.pack(side="left")
        self.template_tree.bind(
            "<<TreeviewSelect>>",
            self._update_template_actions,
        )

    def _build_products_view(self) -> None:
        view = ttk.Frame(self.content, style="TFrame")
        view.columnconfigure(0, weight=1)
        view.rowconfigure(2, weight=1)
        self.views["products"] = view
        toolbar = ttk.Frame(view, style="TFrame")
        toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        button(toolbar, "管理模板", lambda: self._show_view("templates")).pack(
            side="right"
        )
        button(
            toolbar,
            "导入数据包文件夹",
            self._import_folder,
        ).pack(side="right", padx=6)
        button(
            toolbar,
            "导入1688链接",
            self._import_1688_links,
            "Primary",
        ).pack(side="right", padx=6)
        button(toolbar, "选择文件", self._import_package).pack(side="right")
        button(toolbar, "继续导入", self._continue_import).pack(
            side="right", padx=6
        )
        button(toolbar, "清空批次", self._clear_batch).pack(side="right")
        ttk.Label(
            toolbar,
            textvariable=self.summary_var,
            style="Subtitle.TLabel",
        ).pack(side="right", padx=12)
        self.select_all_button = button(
            toolbar,
            "全选筛选结果",
            self._select_all_batch,
        )
        self.select_all_button.pack(side="left")
        self.batch_clear_selection_button = button(
            toolbar,
            "取消选择",
            self._clear_batch_selection,
        )
        self.batch_clear_selection_button.pack(side="left", padx=6)
        self.batch_invert_button = button(
            toolbar,
            "反选",
            self._invert_batch_selection,
        )
        self.batch_invert_button.pack(side="left")
        self.batch_delete_button = button(
            toolbar,
            "删除选中商品",
            self._delete_selected_batch,
            "Danger",
        )
        self.batch_delete_button.pack(side="left", padx=6)
        filters = ttk.LabelFrame(
            view,
            text="筛选商品",
            padding=(10, 7),
        )
        filters.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        ttk.Label(
            filters,
            text="筛选模式",
            style="Panel.TLabel",
        ).pack(side="left", padx=(0, 5))
        self.filter_mode_combo = ttk.Combobox(
            filters,
            textvariable=self.filter_mode_var,
            state="readonly",
            width=12,
            values=BATCH_FILTER_MODES,
        )
        self.filter_mode_combo.pack(side="left")
        self.filter_control = ttk.Frame(filters, style="TFrame")
        self.filter_control.pack(side="left", padx=(8, 0))
        self.filter_all_label = ttk.Label(
            self.filter_control,
            text="显示全部商品",
            style="Panel.TLabel",
            foreground=MUTED,
        )
        self.filter_keyword_label = ttk.Label(
            self.filter_control,
            text="标题包含",
            style="Panel.TLabel",
        )
        self.filter_keyword_entry = ttk.Entry(
            self.filter_control,
            textvariable=self.filter_keyword_var,
            width=28,
        )
        self.filter_min_label = ttk.Label(
            self.filter_control,
            text="最低库存",
            style="Panel.TLabel",
        )
        self.filter_min_entry = ttk.Entry(
            self.filter_control,
            textvariable=self.filter_min_var,
            width=9,
        )
        self.filter_to_label = ttk.Label(
            self.filter_control,
            text="至",
            style="Panel.TLabel",
        )
        self.filter_max_entry = ttk.Entry(
            self.filter_control,
            textvariable=self.filter_max_var,
            width=9,
        )
        ttk.Label(
            filters,
            text="平台",
            style="Panel.TLabel",
        ).pack(side="left", padx=(12, 5))
        self.filter_platform_combo = ttk.Combobox(
            filters,
            textvariable=self.platform_var,
            state="readonly",
            width=12,
            values=("全部平台", "微信小店", "淘宝", "1688", "其他"),
        )
        self.filter_platform_combo.pack(side="left")
        button(
            filters,
            "应用筛选",
            self._apply_batch_filter,
            "Primary",
        ).pack(side="left", padx=(12, 5))
        button(filters, "重置", self._reset_batch_filter).pack(side="left")
        self.filter_mode_combo.bind(
            "<<ComboboxSelected>>",
            lambda _event: self._sync_filter_controls(reset_page=True),
        )
        for widget in (
            self.filter_keyword_entry,
            self.filter_min_entry,
            self.filter_max_entry,
        ):
            widget.bind(
                "<Return>",
                lambda _event: self._apply_batch_filter(),
            )
        self._sync_filter_controls()
        body = ttk.Frame(view, style="Panel.TFrame")
        body.grid(row=2, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)
        columns = ("selected", "seq", "title", "id", "price", "stock", "platform", "source")
        self.batch_tree = ttk.Treeview(
            body,
            columns=columns,
            show="headings",
            selectmode="extended",
        )
        for key, text, width in (
            ("selected", "选", 38),
            ("seq", "序号", 55),
            ("title", "商品标题", 380),
            ("id", "来源商品ID", 160),
            ("price", "价格", 90),
            ("stock", "库存", 75),
            ("platform", "平台", 90),
            ("source", "数据文件", 190),
        ):
            self.batch_tree.heading(key, text=text)
            self.batch_tree.column(key, width=width, anchor="w")
        self.batch_tree.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(
            body, orient="vertical", command=self.batch_tree.yview
        )
        scroll.grid(row=0, column=1, sticky="ns")
        self.batch_tree.configure(yscrollcommand=scroll.set)
        self.batch_tree.bind("<<TreeviewSelect>>", self._update_batch_summary)
        self.batch_tree.bind(
            "<Delete>",
            lambda _event: self._delete_selected_batch(),
        )
        self.batch_tree.bind("<Button-3>", self._show_batch_context_menu)
        self.batch_tree.bind(
            "<Control-a>",
            lambda _event: self._select_batch_result(),
        )
        page_bar = ttk.Frame(body, style="Panel.TFrame")
        page_bar.grid(
            row=1,
            column=0,
            columnspan=2,
            sticky="ew",
            pady=(6, 0),
        )
        self.batch_prev_button = button(
            page_bar,
            "上一页",
            lambda: self._change_batch_page(-1),
        )
        self.batch_prev_button.pack(side="left")
        ttk.Label(
            page_bar,
            textvariable=self.batch_page_var,
            style="Panel.TLabel",
        ).pack(side="left", padx=10)
        self.batch_next_button = button(
            page_bar,
            "下一页",
            lambda: self._change_batch_page(1),
        )
        self.batch_next_button.pack(side="left")
        ttk.Label(
            page_bar,
            text="右键可全选、取消、反选或删除；Ctrl/Shift 支持多选",
            style="Panel.TLabel",
            foreground=MUTED,
        ).pack(side="right")
        self.batch_context_menu = tk.Menu(self, tearoff=0)
        self.batch_context_menu.add_command(
            label="全选当前筛选结果",
            command=lambda: self._select_batch_result(force=True),
        )
        self.batch_context_menu.add_command(
            label="取消当前选择",
            command=self._clear_batch_selection,
        )
        self.batch_context_menu.add_command(
            label="反选当前筛选结果",
            command=lambda: self._invert_batch_selection(all_items=False),
        )
        self.batch_context_menu.add_command(
            label="反选全部商品",
            command=lambda: self._invert_batch_selection(all_items=True),
        )
        self.batch_context_menu.add_separator()
        self.batch_context_menu.add_command(
            label="删除选中商品",
            command=self._delete_selected_batch,
        )
        self.batch_empty_label = tk.Label(
            body,
            text=(
                "还没有商品数据\n\n"
                "粘贴1688商品链接，或导入 JSON / TXT 数据包，"
                "导入后可按标题、"
                "SKU、库存、价格和平台筛选。"
            ),
            bg=PANEL,
            fg=MUTED,
            justify="center",
            font=("Microsoft YaHei UI", 10),
        )

        side = ttk.Frame(view, style="Panel.TFrame", padding=14)
        side.grid(row=2, column=1, sticky="ns", padx=(8, 0))
        ttk.Label(side, text="本批次", style="Section.TLabel").pack(anchor="w")
        ttk.Label(
            side,
            textvariable=self.batch_summary_var,
            style="Panel.TLabel",
            justify="left",
        ).pack(anchor="w", pady=(6, 12))
        ttk.Label(side, text="上传模板", style="Panel.TLabel").pack(anchor="w")
        self.template_combo = ttk.Combobox(
            side,
            textvariable=self.template_var,
            state="readonly",
            width=26,
        )
        self.template_combo.pack(fill="x", pady=(4, 10))
        ttk.Label(side, text="分配方式", style="Panel.TLabel").pack(anchor="w")
        ttk.Combobox(
            side,
            textvariable=self.assignment_var,
            state="readonly",
            width=26,
            values=("每个店铺都上传", "平均分配给店铺"),
        ).pack(fill="x", pady=(4, 10))
        button(
            side,
            "按分组/店铺选择并创建任务",
            self._create_tasks,
            "Primary",
        ).pack(fill="x", pady=(4, 8))
        ttk.Label(
            side,
            text="支持 JSON 数据包和常见 TXT/TSV 表格。\n"
            "可一次选择多个分组并勾选组内全部店铺。\n"
            "只有包含微信商品 JSON 字段的数据才能直接提交；\n"
            "类目未开通时会自动申请，生效后自动重试。",
            style="Panel.TLabel",
            foreground=MUTED,
            justify="left",
            wraplength=220,
        ).pack(anchor="w")
        view.columnconfigure(0, weight=1)

    def _build_tasks_view(self) -> None:
        view = ttk.Frame(self.content, style="TFrame")
        view.columnconfigure(0, weight=1)
        view.rowconfigure(1, weight=1)
        self.views["tasks"] = view
        toolbar = ttk.Frame(view, style="TFrame")
        toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ttk.Label(
            toolbar,
            textvariable=self.task_summary_var,
            style="Subtitle.TLabel",
        ).pack(side="left")
        ttk.Combobox(
            toolbar,
            textvariable=self.task_filter_var,
            state="readonly",
            width=12,
            values=(
                "全部状态",
                "待执行",
                "执行中",
                "需处理",
                "已完成",
                "已暂停",
            ),
        ).pack(side="left", padx=10)
        self.task_filter_var.trace_add(
            "write", lambda *_args: self._render_tasks()
        )
        self.task_pause_button = button(
            toolbar,
            "暂停全部",
            self._pause_tasks,
        )
        self.task_pause_button.pack(side="right", padx=6)
        self.task_retry_button = button(
            toolbar,
            "继续失败项",
            self._retry_failed,
        )
        self.task_retry_button.pack(side="right")
        self.task_start_button = button(
            toolbar,
            "开始待执行",
            self._start_tasks,
            "Primary",
        )
        self.task_start_button.pack(side="right", padx=6)
        body = ttk.Frame(view, style="Panel.TFrame")
        body.grid(row=1, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)
        self.task_tree = ttk.Treeview(
            body,
            columns=("created", "store", "template", "status", "progress", "message"),
            show="headings",
            selectmode="extended",
        )
        for key, text, width in (
            ("created", "创建时间", 150),
            ("store", "店铺", 170),
            ("template", "模板", 150),
            ("status", "状态", 100),
            ("progress", "进度", 120),
            ("message", "说明", 440),
        ):
            self.task_tree.heading(key, text=text)
            self.task_tree.column(key, width=width, anchor="w")
        self.task_tree.grid(row=0, column=0, sticky="nsew")
        self.task_tree.bind("<<TreeviewSelect>>", self._update_task_actions)
        scroll = ttk.Scrollbar(
            body, orient="vertical", command=self.task_tree.yview
        )
        scroll.grid(row=0, column=1, sticky="ns")
        self.task_tree.configure(yscrollcommand=scroll.set)
        actions = ttk.Frame(view, style="TFrame")
        actions.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        self.task_clear_button = button(
            actions,
            "清空已完成",
            self._clear_completed,
        )
        self.task_clear_button.pack(side="left")
        self.task_delete_button = button(
            actions,
            "删除任务",
            self._delete_tasks,
            "Danger",
        )
        self.task_delete_button.pack(side="left", padx=6)
        self.task_list_button = button(
            actions,
            "上架已上传商品",
            self._list_uploaded_products,
            "Primary",
        )
        self.task_list_button.pack(side="left", padx=6)
        button(actions, "任务日志", self._show_task_log).pack(
            side="right", padx=6
        )
        button(
            actions,
            "补齐上家关联",
            self._backfill_source_links,
        ).pack(side="right", padx=6)
        button(actions, "清空全部", self._clear_all_tasks, "Danger").pack(
            side="right"
        )
        self._update_task_actions()
        self.task_tree.bind(
            "<Double-1>", lambda _event: self._show_task_log()
        )

    def _build_categories_view(self) -> None:
        view = ttk.Frame(self.content, style="TFrame")
        view.columnconfigure(0, weight=1)
        view.columnconfigure(1, weight=2)
        view.rowconfigure(2, weight=1)
        self.views["categories"] = view

        controls = ttk.Frame(view, style="TFrame")
        controls.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        ttk.Label(
            controls,
            text="申请模式",
            style="Panel.TLabel",
        ).pack(side="left")
        mode = ttk.Combobox(
            controls,
            textvariable=self.category_mode_var,
            values=("单店申请", "多店申请"),
            state="readonly",
            width=12,
        )
        mode.pack(side="left", padx=(6, 16))
        mode.bind("<<ComboboxSelected>>", self._category_mode_changed)
        ttk.Label(
            controls,
            text="参考店铺",
            style="Panel.TLabel",
        ).pack(side="left", padx=(12, 0))
        self.category_reference_combo = ttk.Combobox(
            controls,
            textvariable=self.category_reference_var,
            state="readonly",
            width=24,
        )
        self.category_reference_combo.pack(side="left", padx=(6, 16))
        ttk.Label(
            controls,
            text="保证金上限（元）",
            style="Panel.TLabel",
        ).pack(side="left")
        ttk.Entry(
            controls,
            textvariable=self.category_budget_var,
            width=12,
        ).pack(side="left", padx=(6, 10))
        button(
            controls,
            "扫描可申请类目",
            self._scan_category_candidates,
            "Primary",
        ).pack(side="left")
        button(
            controls,
            "刷新店铺",
            self._render_category_stores,
        ).pack(side="left", padx=6)
        ttk.Label(
            controls,
            textvariable=self.category_status_var,
            style="Subtitle.TLabel",
        ).pack(side="right")

        ttk.Label(
            view,
            text="选择店铺（单店模式只能选择一个；多店模式可多选）",
            style="Panel.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(0, 4))
        ttk.Label(
            view,
            text="不超过保证金上限且无额外资质要求的类目",
            style="Panel.TLabel",
        ).grid(row=1, column=1, sticky="w", padx=(8, 0), pady=(0, 4))

        stores_frame = ttk.Frame(view, style="Panel.TFrame")
        stores_frame.grid(row=2, column=0, sticky="nsew")
        stores_frame.columnconfigure(0, weight=1)
        stores_frame.rowconfigure(0, weight=1)
        self.category_store_tree = ttk.Treeview(
            stores_frame,
            columns=("name", "login", "app_id"),
            show="headings",
            selectmode="extended",
        )
        for key, label, width in (
            ("name", "店铺", 155),
            ("login", "网页登录", 82),
            ("app_id", "AppID", 170),
        ):
            self.category_store_tree.heading(key, text=label)
            self.category_store_tree.column(key, width=width, anchor="w")
        self.category_store_tree.grid(row=0, column=0, sticky="nsew")
        store_scroll = ttk.Scrollbar(
            stores_frame,
            orient="vertical",
            command=self.category_store_tree.yview,
        )
        store_scroll.grid(row=0, column=1, sticky="ns")
        self.category_store_tree.configure(yscrollcommand=store_scroll.set)
        self.category_store_tree.bind(
            "<<TreeviewSelect>>",
            self._category_selection_changed,
        )

        result_frame = ttk.Frame(view, style="Panel.TFrame")
        result_frame.grid(
            row=2,
            column=1,
            sticky="nsew",
            padx=(8, 0),
        )
        result_frame.columnconfigure(0, weight=1)
        result_frame.rowconfigure(0, weight=1)
        self.category_result_tree = ttk.Treeview(
            result_frame,
            columns=(
                "store",
                "name",
                "path",
                "deposit",
                "amount",
                "status",
            ),
            show="headings",
            selectmode="extended",
        )
        for key, label, width in (
            ("store", "店铺", 130),
            ("name", "类目", 120),
            ("path", "类目路径", 300),
            ("deposit", "保证金", 90),
            ("amount", "需补缴", 90),
            ("status", "状态", 100),
        ):
            self.category_result_tree.heading(key, text=label)
            self.category_result_tree.column(key, width=width, anchor="w")
        self.category_result_tree.grid(row=0, column=0, sticky="nsew")
        result_scroll = ttk.Scrollbar(
            result_frame,
            orient="vertical",
            command=self.category_result_tree.yview,
        )
        result_scroll.grid(row=0, column=1, sticky="ns")
        self.category_result_tree.configure(
            yscrollcommand=result_scroll.set
        )

        actions = ttk.Frame(view, style="TFrame")
        actions.grid(
            row=3,
            column=0,
            columnspan=2,
            sticky="ew",
            pady=(8, 0),
        )
        button(
            actions,
            "全选可申请",
            self._select_applyable_categories,
        ).pack(side="left")
        button(
            actions,
            "取消选择",
            self._clear_category_selection,
        ).pack(side="left", padx=6)
        self.category_submit_button = button(
            actions,
            "提交审核",
            self._submit_category_candidates,
            "Primary",
        )
        self.category_submit_button.pack(side="right")
        ttk.Label(
            actions,
            text=(
                "仅提交“可申请”类目；需资质、超过保证金上限、"
                "已有或审核中的类目不会重复提交。"
            ),
            style="Subtitle.TLabel",
        ).pack(side="right", padx=12)

    def _category_mode_changed(self, _event=None) -> None:
        self.category_status_var.set(
            "单店申请：请选择一个店铺"
            if self.category_mode_var.get() == "单店申请"
            else "多店申请：请选择目标店铺并指定参考店铺"
        )
        self._refresh_category_reference_options()

    def _category_selection_changed(self, _event=None) -> None:
        self._refresh_category_reference_options()

    def _refresh_category_reference_options(self) -> None:
        combo = getattr(self, "category_reference_combo", None)
        if combo is None:
            return
        selected = self._selected_category_stores()
        source = selected or [
            store
            for store in self.db.list_stores()
            if store.web_status == "已登录"
        ]
        options = {
            f"{store.name} · {store.app_id[-4:]}": store.id
            for store in source
            if store.web_status == "已登录"
        }
        self._category_store_options = options
        values = list(options)
        combo["values"] = values
        if self.category_reference_var.get() not in values:
            self.category_reference_var.set(values[0] if values else "")
        combo.configure(
            state=(
                "disabled"
                if self.category_mode_var.get() == "单店申请"
                else "readonly"
            )
        )

    def _category_reference_store(
        self,
        selected: list[Store],
    ) -> Store | None:
        if self.category_mode_var.get() == "单店申请":
            return selected[0] if selected else None
        reference_id = self._category_store_options.get(
            self.category_reference_var.get()
        )
        if reference_id is not None:
            reference = self.db.get_store(reference_id)
            if reference is not None:
                return reference
        return next(
            (
                store
                for store in selected
                if store.web_status == "已登录"
            ),
            None,
        )

    def _render_category_stores(self) -> None:
        tree = getattr(self, "category_store_tree", None)
        if tree is None:
            return
        selected = set(tree.selection())
        tree.delete(*tree.get_children())
        for store in self.db.list_stores():
            tree.insert(
                "",
                "end",
                iid=str(store.id),
                values=(
                    store.name,
                    store.web_status,
                    store.app_id,
                ),
            )
        tree.selection_set(
            tuple(value for value in selected if tree.exists(value))
        )
        self._refresh_category_reference_options()

    def _selected_category_stores(self) -> list[Store]:
        tree = getattr(self, "category_store_tree", None)
        if tree is None:
            return []
        return [
            store
            for iid in tree.selection()
            if (store := self.db.get_store(int(iid))) is not None
        ]

    @staticmethod
    def _category_deposit_text(cents: int) -> str:
        return f"¥{int(cents or 0) / 100:,.2f}"

    def _render_category_candidates(self) -> None:
        tree = self.category_result_tree
        tree.delete(*tree.get_children())
        rows = list(self._category_candidates.values())
        self._category_candidates.clear()
        for index, row in enumerate(rows):
            iid = str(index)
            self._category_candidates[iid] = row
            tree.insert(
                "",
                "end",
                iid=iid,
                values=(
                    row["store_name"],
                    row["name"],
                    row["path"],
                    self._category_deposit_text(
                        row["required_deposit"]
                    ),
                    self._category_deposit_text(row["amount_to_pay"]),
                    row["status"],
                ),
            )
        self.category_status_var.set(
            f"共扫描到 {len(self._category_candidates)} 个符合保证金上限的类目"
        )

    def _scan_category_candidates(self) -> None:
        stores = self._selected_category_stores()
        if not stores:
            messagebox.showinfo(
                "请选择店铺",
                "请先选择一个或多个店铺。",
                parent=self,
            )
            return
        if (
            self.category_mode_var.get() == "单店申请"
            and len(stores) != 1
        ):
            messagebox.showinfo(
                "单店申请",
                "单店申请模式只能选择一个店铺。",
                parent=self,
            )
            return
        unlogged = [
            store.name
            for store in stores
            if store.web_status != "已登录"
        ]
        if unlogged:
            messagebox.showwarning(
                "店铺未登录",
                "所选店铺必须全部已登录网页后台：\n"
                + "、".join(unlogged[:10]),
                parent=self,
            )
            return
        reference = self._category_reference_store(stores)
        if reference is None:
            messagebox.showinfo(
                "参考店铺",
                "请选择一家已登录的参考店铺。",
                parent=self,
            )
            return
        try:
            budget_cents = _price_cents(
                self.category_budget_var.get().strip()
            )
        except ValueError:
            budget_cents = 0
        if budget_cents <= 0:
            messagebox.showwarning(
                "保证金上限",
                "请输入大于 0 的保证金上限。",
                parent=self,
            )
            return
        self.category_status_var.set("正在扫描可申请类目…")
        target_ids = [store.id for store in stores]
        target_names = [store.name for store in stores]

        def work(progress) -> tuple[list[dict[str, Any]], list[str]]:
            rows: list[dict[str, Any]] = []
            errors: list[str] = []
            if progress:
                progress(
                    f"正在扫描参考店铺 {reference.name} 的可申请类目",
                    0,
                    1,
                )
            try:
                client = ShopPrivateClient(self.db, reference)
                for item in client.category_candidates(budget_cents):
                    rows.append(
                        {
                            **item,
                            "store_name": (
                                reference.name
                                if len(stores) == 1
                                else f"{len(stores)} 家店铺"
                            ),
                            "reference_store_id": reference.id,
                            "reference_store_name": reference.name,
                            "target_store_ids": target_ids,
                            "target_store_names": target_names,
                        }
                    )
            except Exception as exc:
                errors.append(f"{reference.name}: {exc}")
            return rows, errors

        def done(result: tuple[list[dict[str, Any]], list[str]]) -> None:
            rows, errors = result
            self._category_candidates.clear()
            for index, row in enumerate(rows):
                self._category_candidates[str(index)] = row
            self._render_category_candidates()
            self._select_applyable_categories()
            self.category_status_var.set(
                f"参考店铺 {reference.name}；"
                f"可申请 {sum(row['status'] == '可申请' for row in rows)}，"
                f"需资质 {sum(row['status'] == '需资质' for row in rows)}"
                + (f"，扫描失败 {len(errors)}" if errors else "")
            )
            if errors:
                messagebox.showwarning(
                    "参考店铺扫描失败",
                    "\n".join(errors[:8]),
                    parent=self,
                )

        self.window.run_task(
            work,
            done,
            lambda error: messagebox.showerror(
                "类目扫描失败",
                error,
                parent=self,
            ),
            progress=self._progress,
            log_title="扫描可申请类目",
        )

    def _select_applyable_categories(self) -> None:
        eligible = tuple(
            iid
            for iid, row in self._category_candidates.items()
            if row.get("status") == "可申请"
        )
        self.category_result_tree.selection_set(eligible)

    def _clear_category_selection(self) -> None:
        self.category_result_tree.selection_set(())

    def _submit_category_candidates(self) -> None:
        selected = [
            self._category_candidates[iid]
            for iid in self.category_result_tree.selection()
            if iid in self._category_candidates
            and self._category_candidates[iid].get("status")
            == "可申请"
        ]
        if not selected:
            messagebox.showinfo(
                "没有可提交类目",
                "请先选择状态为“可申请”的类目。",
                parent=self,
            )
            return
        target_ids = {
            int(store_id)
            for row in selected
            for store_id in row.get("target_store_ids") or []
        }
        stores = {
            store.id: store
            for store in self.db.list_stores()
        }
        target_stores = [
            stores[store_id]
            for store_id in target_ids
            if store_id in stores
        ]
        if not target_stores:
            messagebox.showwarning(
                "没有目标店铺",
                "请重新选择目标店铺并扫描类目。",
                parent=self,
            )
            return
        category_ids = [str(row["category_id"]) for row in selected]
        summary = (
            f"参考店铺：{selected[0].get('reference_store_name', '—')}\n"
            f"目标店铺：{len(target_stores)} 家\n"
            f"统一申请类目：{len(category_ids)} 个"
        )
        if not messagebox.askyesno(
            "提交类目申请",
            "即将对每个目标店铺发送同一批类目审核申请。\n"
            f"{summary}\n\n是否继续？",
            parent=self,
        ):
            return

        def work(progress) -> CategoryApplyResult:
            errors: list[str] = []
            detail_lines: list[str] = []
            store_statuses: dict[int, dict[str, str]] = {}
            totals = {"已生效": 0, "审核中": 0, "未查到": 0}
            category_names = {
                str(row["category_id"]): str(row.get("name") or "")
                for row in selected
            }
            total = len(target_stores)
            for index, store in enumerate(target_stores, 1):
                if progress:
                    progress(
                        f"正在提交 {store.name} 类目 {index}/{total}",
                        index,
                        total,
                    )
                submitted = False
                try:
                    client = ShopPrivateClient(self.db, store)
                    for start in range(0, len(category_ids), 200):
                        client.batch_add_categories(
                            category_ids[start : start + 200]
                        )
                    submitted = True
                except Exception as exc:
                    errors.append(f"{store.name}: 提交失败：{exc}")
                    detail_lines.append(
                        f"{store.name}：提交失败 · {exc}"
                    )
                    continue
                if not submitted:
                    continue
                try:
                    statuses = client.category_relation_statuses(
                        category_ids
                    )
                except Exception as exc:
                    statuses = {
                        category_id: "未查到"
                        for category_id in category_ids
                    }
                    errors.append(
                        f"{store.name}: 已提交，但状态核验失败：{exc}"
                    )
                    detail_lines.append(
                        f"{store.name}：已提交，状态核验失败 · {exc}"
                    )
                store_statuses[store.id] = statuses
                counts = {
                    label: sum(
                        status == label
                        for status in statuses.values()
                    )
                    for label in totals
                }
                for label, count in counts.items():
                    totals[label] += count
                detail_lines.append(
                    f"{store.name}：已生效 {counts['已生效']}，"
                    f"审核中 {counts['审核中']}，"
                    f"未查到 {counts['未查到']}"
                )
                grouped: dict[str, list[str]] = {
                    label: []
                    for label in totals
                }
                for category_id, status in statuses.items():
                    name = category_names.get(category_id, "")
                    grouped.setdefault(status, []).append(
                        (
                            f"{name}({category_id})"
                            if name
                            else category_id
                        )
                    )
                for label, names in grouped.items():
                    if names:
                        shown = "、".join(names[:20])
                        if len(names) > 20:
                            shown += f" 等 {len(names)} 个"
                        detail_lines.append(
                            f"{store.name} / {label}：{shown}"
                        )
                if counts["未查到"]:
                    errors.append(
                        f"{store.name}: 已提交，但暂时未查到 "
                        f"{counts['未查到']} 个类目的审核状态"
                    )
            message = (
                f"类目提交结果：已生效 {totals['已生效']}，"
                f"审核中 {totals['审核中']}，"
                f"未查到 {totals['未查到']}"
            )
            return CategoryApplyResult(
                message=message,
                errors=errors,
                detail="\n".join(detail_lines),
                store_statuses=store_statuses,
            )

        def done(result: CategoryApplyResult) -> None:
            for iid in self.category_result_tree.selection():
                row = self._category_candidates.get(iid)
                if row and row.get("status") == "可申请":
                    target_ids = [
                        int(store_id)
                        for store_id in row.get("target_store_ids") or []
                    ]
                    statuses = {
                        result.store_statuses.get(store_id, {}).get(
                            str(row["category_id"])
                        )
                        for store_id in target_ids
                    }
                    if statuses and all(
                        status == "已生效"
                        for status in statuses
                    ):
                        label = "已生效"
                    elif "审核中" in statuses:
                        label = "审核中"
                    elif len(target_ids) == 1 and statuses == {"未查到"}:
                        label = "已提交待核验"
                    else:
                        label = "已提交（见日志）"
                    row["status"] = label
                    self.category_result_tree.set(iid, "status", label)
            self.category_status_var.set(
                result.message
                + (
                    f"；需处理 {len(result.errors)} 项"
                    if result.errors
                    else ""
                )
            )
            if result.errors:
                messagebox.showwarning(
                    "类目申请结果",
                    "\n".join(result.errors[:8])
                    + "\n\n完整状态已写入任务日志。",
                    parent=self,
                )

        self.window.run_task(
            work,
            done,
            lambda error: messagebox.showerror(
                "类目申请失败",
                error,
                parent=self,
            ),
            progress=self._progress,
            log_title="提交类目审核",
        )

    def refresh(self) -> None:
        self._templates = _json_rows(
            self.db.get_setting("upload_templates", "[]"), []
        )
        self._batch = _json_rows(
            self.db.get_setting("upload_batch", "[]"), []
        )
        valid_ids = {
            str(row.get("batch_id"))
            for row in self._batch
            if isinstance(row, dict) and row.get("batch_id")
        }
        selected_ids = getattr(self, "_selected_batch_ids", None)
        if selected_ids is not None:
            selected_ids.intersection_update(valid_ids)
        if not (self._running or self._run_requested):
            with self._task_lock:
                self._tasks = _json_rows(
                    self.db.get_setting("upload_tasks", "[]"), []
                )
        active_view = getattr(self, "_active_view", "templates")
        if active_view == "templates":
            self._render_templates()
        elif active_view == "products":
            self._render_batch()
        elif active_view == "tasks":
            self._render_tasks()
        elif active_view == "categories":
            self._render_category_stores()

    def _save_templates(self) -> None:
        self.db.set_setting(
            "upload_templates",
            json.dumps(self._templates, ensure_ascii=False),
        )

    def _save_batch(self) -> None:
        self.db.set_setting(
            "upload_batch",
            json.dumps(self._batch, ensure_ascii=False),
        )

    def _normalize_batch_rows(self) -> int:
        rows: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        repaired = 0
        for index, raw in enumerate(self._batch):
            if isinstance(raw, dict):
                row = dict(raw)
            else:
                row = {
                    "title": str(raw or f"商品 {index + 1}"),
                    "source_product_id": "",
                    "price": 0,
                    "stock": 0,
                    "platform": "",
                }
                repaired += 1
            batch_id = str(row.get("batch_id") or "").strip()
            if not batch_id or batch_id in seen_ids:
                batch_id = f"recovered-{uuid.uuid4().hex}"
                row["batch_id"] = batch_id
                repaired += 1
            seen_ids.add(batch_id)
            row["title"] = str(row.get("title") or "")
            row["source_product_id"] = str(
                row.get("source_product_id") or ""
            )
            row["platform"] = str(row.get("platform") or "")
            row["source_file"] = str(row.get("source_file") or "")
            if "stock_known" not in row:
                raw = row.get("raw")
                payload = row.get("payload")
                stock, stock_known = _normalized_stock(
                    raw if isinstance(raw, dict) else None,
                    payload if isinstance(payload, dict) else None,
                )
                if stock_known:
                    row["stock"] = stock
                    row["stock_known"] = True
                elif isinstance(raw, dict) or isinstance(payload, dict):
                    row["stock"] = None
                    row["stock_known"] = False
                else:
                    row["stock"] = _stock_int(row.get("stock"))
                    row["stock_known"] = row["stock"] is not None
            rows.append(row)
        self._batch = rows
        return repaired

    def _save_tasks(self) -> None:
        self.db.set_setting("upload_tasks", self._tasks_snapshot_json())

    def _tasks_snapshot_json(self) -> str:
        with self._task_lock:
            return json.dumps(self._tasks, ensure_ascii=False)

    def _task_list_snapshot(self) -> list[dict[str, Any]]:
        with self._task_lock:
            return list(self._tasks)

    def prepare_for_close(self) -> None:
        """Persist safe recovery states without touching Tk from workers."""
        self._paused = True
        with self._task_lock:
            changed = _recover_upload_tasks(self._tasks)
        if changed:
            self._save_tasks()

    def _commit_upload_success(
        self,
        task: dict[str, Any],
        item: dict[str, Any],
        product_id: str,
    ) -> None:
        store_id = int(task.get("store_id") or 0)
        get_product = getattr(self.db, "get_product", None)
        product = (
            get_product(store_id, product_id)
            if callable(get_product)
            else None
        )
        source_link, mappings = _upload_source_relation(item, product)
        item["source_link_status"] = (
            "linked" if source_link is not None else "not_applicable"
        )
        item["source_link_mapping_count"] = len(mappings)
        if source_link is not None:
            item["message"] = (
                f"{item.get('message') or ''}；已自动关联上家 "
                f"{source_link.get('platform') or ''} "
                f"{source_link.get('source_product_id') or ''}"
                f"（{len(mappings)} 个规格）"
            ).strip("；")
        snapshot = self._tasks_snapshot_json()
        commit = getattr(self.db, "commit_upload_task_result", None)
        if callable(commit):
            commit(
                snapshot,
                store_id,
                str(item.get("source_product_id") or ""),
                product_id,
                str(item.get("title") or ""),
                source_link=source_link,
                sku_mappings=mappings,
            )
            return
        self._save_tasks()
        self.db.record_upload_history(
            store_id,
            str(item.get("source_product_id") or ""),
            product_id,
            str(item.get("title") or ""),
        )

    def _render_templates(self) -> None:
        self.template_tree.delete(*self.template_tree.get_children())
        self._template_rows.clear()
        for index, template in enumerate(self._templates):
            if not isinstance(template, dict):
                continue
            iid = str(index)
            self._template_rows[iid] = template
            prefix = str(template.get("title_prefix") or "")
            suffix = str(template.get("title_suffix") or "")
            title_rule = f"前缀：{prefix}" if prefix else ""
            if suffix:
                title_rule += ("；" if title_rule else "") + f"后缀：{suffix}"
            multiplier = float(template.get("price_multiplier") or 1)
            offset = int(template.get("price_offset") or 0)
            price_rule = f"×{multiplier:g}"
            if offset:
                price_rule += f" {offset / 100:+.2f} 元"
            self.template_tree.insert(
                "",
                "end",
                iid=iid,
                values=(
                    (
                        f"默认 · {template.get('name', '')}"
                        if template.get("is_default")
                        else template.get("name", "")
                    ),
                    title_rule or "保持原标题",
                    price_rule,
                    template.get("stock", 0),
                    LISTING_MODE_LABELS[
                        _normalize_listing_mode(template)
                    ],
                    str(template.get("updated_at") or "")[:16],
                ),
            )
        names = ["选择模板", *[
            str(row.get("name"))
            for row in self._templates
            if isinstance(row, dict) and row.get("name")
        ]]
        if hasattr(self, "template_combo"):
            self.template_combo["values"] = names
            if self.template_var.get() not in names:
                default_name = next(
                    (
                        str(row.get("name"))
                        for row in self._templates
                        if isinstance(row, dict)
                        and row.get("is_default")
                        and row.get("name")
                    ),
                    "",
                )
                self.template_var.set(
                    default_name if default_name in names else names[0]
                )
        self._update_template_actions()

    def _update_template_actions(self, _event=None) -> None:
        state = "normal" if self.template_tree.selection() else "disabled"
        for action_button in (
            self.template_edit_button,
            self.template_copy_button,
            self.template_default_button,
            self.template_delete_button,
        ):
            action_button.configure(state=state)

    def _choose_template_folder(self, variable: tk.StringVar) -> None:
        path = filedialog.askdirectory(
            parent=self,
            title="选择商品视频文件夹",
            initialdir=variable.get() or None,
        )
        if path:
            variable.set(path)

    def _edit_ladder_rules(
        self,
        values: dict[str, tk.StringVar],
        parent: tk.Misc,
    ) -> None:
        dialog = tk.Toplevel(parent)
        dialog.title("阶梯倍率")
        dialog.transient(parent)
        dialog.geometry("620x360")
        panel = ttk.Frame(dialog, style="Panel.TFrame", padding=14)
        panel.pack(fill="both", expand=True)
        ttk.Label(
            panel,
            text="每行一个区间：最低原价(元)=最高原价(元)=倍率=加价(元)",
            style="Panel.TLabel",
            foreground=MUTED,
        ).pack(anchor="w")
        editor = tk.Text(
            panel,
            height=12,
            wrap="none",
            relief="solid",
            borderwidth=1,
            bg="white",
            fg=TEXT,
        )
        editor.pack(fill="both", expand=True, pady=(7, 9))
        editor.insert("1.0", values["ladder_rules"].get())
        actions = ttk.Frame(panel, style="Panel.TFrame")
        actions.pack(anchor="e")

        def save() -> None:
            lines = [
                line.strip()
                for line in editor.get("1.0", "end-1c").splitlines()
                if line.strip()
            ]
            for line in lines:
                parts = [part.strip() for part in line.split("=")]
                if len(parts) != 4:
                    messagebox.showwarning(
                        "阶梯倍率",
                        "每行必须填写 4 项：最低价=最高价=倍率=加价。",
                        parent=dialog,
                    )
                    return
                try:
                    low, high, multiplier, offset = map(float, parts)
                except ValueError:
                    messagebox.showwarning(
                        "阶梯倍率",
                        "阶梯倍率参数必须是数字。",
                        parent=dialog,
                    )
                    return
                if low < 0 or high < low or multiplier <= 0:
                    messagebox.showwarning(
                        "阶梯倍率",
                        "价格区间或倍率无效。",
                        parent=dialog,
                    )
                    return
            values["ladder_rules"].set("\n".join(lines))
            dialog.destroy()

        button(actions, "取消", dialog.destroy).pack(side="left", padx=6)
        button(actions, "保存", save, "Primary").pack(side="left")
        dialog.bind("<Escape>", lambda _event: dialog.destroy())
        dialog.grab_set()

    def _edit_cloud_violation_types(
        self,
        values: dict[str, tk.StringVar],
        parent: tk.Misc,
    ) -> None:
        types: set[str] = set()
        for row in getattr(self, "_all_violation_rows", []):
            reason = str(row.get("reason") or "").strip()
            if reason:
                types.add(reason)
        # The local violation library is the single-machine equivalent of the
        # cloud snapshot. It never leaves this computer.
        try:
            rows = json.loads(
                self.db.get_setting("violation_library", "[]")
            )
        except (TypeError, ValueError):
            rows = []
        for row in rows if isinstance(rows, list) else []:
            if isinstance(row, dict):
                reason = str(row.get("reason") or "").strip()
                if reason:
                    types.add(reason)
        dialog = tk.Toplevel(parent)
        dialog.title("云违规设置")
        dialog.transient(parent)
        dialog.geometry("560x420")
        panel = ttk.Frame(dialog, style="Panel.TFrame", padding=14)
        panel.pack(fill="both", expand=True)
        ttk.Label(
            panel,
            text="选择参与模板过滤的违规类型；每行一个类型。",
            style="Panel.TLabel",
            foreground=MUTED,
        ).pack(anchor="w")
        editor = tk.Text(
            panel,
            height=13,
            wrap="word",
            relief="solid",
            borderwidth=1,
            bg="white",
            fg=TEXT,
        )
        editor.pack(fill="both", expand=True, pady=(7, 9))
        current = values["cloud_violation_types"].get().strip()
        editor.insert(
            "1.0",
            current
            or "\n".join(sorted(types))
            or "未设置类型时按本机违规商品 ID 过滤",
        )
        actions = ttk.Frame(panel, style="Panel.TFrame")
        actions.pack(anchor="e")

        def save() -> None:
            values["cloud_violation_types"].set(
                editor.get("1.0", "end-1c").strip()
            )
            dialog.destroy()

        button(actions, "取消", dialog.destroy).pack(side="left", padx=6)
        button(actions, "保存", save, "Primary").pack(side="left")
        dialog.bind("<Escape>", lambda _event: dialog.destroy())
        dialog.grab_set()

    def _template_dialog(
        self, template: dict[str, Any] | None = None
    ) -> None:
        template = template or {}
        dialog = tk.Toplevel(self)
        dialog.title("编辑上货模板" if template else "添加上货模板")
        dialog.transient(self.winfo_toplevel())
        dialog.resizable(True, True)
        width = min(1080, max(820, dialog.winfo_screenwidth() - 120))
        height = min(850, max(680, dialog.winfo_screenheight() - 100))
        dialog.geometry(f"{width}x{height}")
        dialog.minsize(760, 600)
        dialog.columnconfigure(0, weight=1)
        dialog.rowconfigure(1, weight=1)

        title_bar = ttk.Frame(dialog, style="Panel.TFrame", padding=(18, 14))
        title_bar.grid(row=0, column=0, sticky="ew")
        title_bar.columnconfigure(0, weight=1)
        ttk.Label(
            title_bar,
            text="添加上货模板" if not template else "编辑上货模板",
            style="Section.TLabel",
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(
            title_bar,
            text="保存后只影响新任务，历史任务继续使用原模板快照。",
            style="Subtitle.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(3, 0))
        button(title_bar, "✕  关闭", dialog.destroy).grid(
            row=0, column=1, rowspan=2, sticky="e"
        )

        notebook = ttk.Notebook(dialog)
        notebook.grid(row=1, column=0, sticky="nsew", padx=12)
        string_fields = {
            "name": template.get("name") or "新上传模板",
            "title_prefix": template.get("title_prefix", ""),
            "title_suffix": template.get("title_suffix", ""),
            "price_multiplier": template.get("price_multiplier", 1),
            "price_offset": template.get("price_offset", 0),
            "fixed_price": template.get("fixed_price", 0),
            "final_price_decimals": template.get("final_price_decimals", 2),
            "video_folder": template.get("video_folder", ""),
            "distributor_name": template.get("distributor_name", ""),
            "distributor_contact": template.get("distributor_contact", ""),
            "delivery_type": template.get("delivery_type", "现货"),
            "delivery_time": template.get("delivery_time", "48小时内发货"),
            "stock": template.get("stock", 1000),
            "title_replace_rules": template.get("title_replace_rules", ""),
            "fallback_category": template.get("fallback_category", ""),
            "deposit_limit": template.get("deposit_limit", 0),
            "category_brand_ids": template.get("category_brand_ids", ""),
            "category_license_groups": template.get(
                "category_license_groups", ""
            ),
            "low_price_multiplier": template.get("low_price_multiplier", 2),
            "low_price_action": template.get(
                "low_price_action", "跳过商品"
            ),
            "trim_head": template.get("trim_head", 0),
            "trim_detail": template.get("trim_detail", 0),
            "title_rules": template.get("title_rules", ""),
            "sku_rules": template.get("sku_rules", ""),
            "image_mode": template.get("image_mode", "默认方式"),
            "head_action": template.get("head_action", "删除违规图片"),
            "detail_action": template.get(
                "detail_action", "删除违规图片"
            ),
            "image_rules": template.get("image_rules", ""),
            "attribute_rules": template.get("attribute_rules", ""),
            "ladder_rules": template.get("ladder_rules", ""),
            "cloud_violation_types": template.get(
                "cloud_violation_types", ""
            ),
        }
        values = {
            key: tk.StringVar(value=str(value))
            for key, value in string_fields.items()
        }
        # Stored offsets are cents; the editor uses yuan for the same UX.
        try:
            values["price_offset"].set(
                f"{int(template.get('price_offset') or 0) / 100:g}"
            )
            values["fixed_price"].set(
                f"{int(template.get('fixed_price') or 0) / 100:g}"
            )
        except (TypeError, ValueError):
            values["price_offset"].set("0")
            values["fixed_price"].set("0")
        current_listing_mode = _normalize_listing_mode(template)
        listing_mode_var = tk.StringVar(
            value=LISTING_MODE_LABELS[current_listing_mode]
        )
        duplicate_filter_var = tk.StringVar(
            value=DUPLICATE_FILTER_LABELS[
                _normalize_duplicate_filter_mode(template)
            ]
        )
        flags = {
            "fixed_price_enabled": bool(template.get("fixed_price_enabled")),
            "ladder_enabled": bool(template.get("ladder_enabled")),
            "main_image_video": bool(template.get("main_image_video")),
            "use_package_tags": bool(template.get("use_package_tags", True)),
            "read_packaging_labels": bool(
                template.get("read_packaging_labels")
            ),
            "clothing_mode": bool(template.get("clothing_mode")),
            "seven_day_return": bool(
                template.get("seven_day_return", True)
            ),
            "shipping_insurance": bool(
                template.get("shipping_insurance")
            ),
            "damage_return": bool(template.get("damage_return")),
            "fake_one_three": bool(template.get("fake_one_three")),
            "support_exchange": bool(
                template.get("support_exchange")
            ),
            "auto_open_category": bool(
                template.get("auto_open_category", True)
            ),
            "force_fallback_category": bool(
                template.get("force_fallback_category")
            ),
            "cloud_violation": bool(template.get("cloud_violation")),
            "strip_title_brackets": bool(
                template.get("strip_title_brackets")
            ),
            "enable_custom_attributes": bool(
                template.get("enable_custom_attributes")
            ),
            "low_price_enabled": bool(
                template.get("low_price_enabled")
            ),
        }
        boolean_vars = {
            key: tk.BooleanVar(value=value) for key, value in flags.items()
        }

        def section(parent: tk.Widget, title: str) -> ttk.LabelFrame:
            frame = ttk.LabelFrame(
                parent,
                text=title,
                padding=(12, 9),
                style="Panel.TFrame",
            )
            frame.pack(fill="x", pady=(0, 9))
            return frame

        def entry(
            parent: tk.Widget,
            row: int,
            column: int,
            label: str,
            key: str,
            width: int = 28,
        ) -> ttk.Entry:
            ttk.Label(
                parent, text=label, style="Panel.TLabel"
            ).grid(
                row=row,
                column=column,
                sticky="w",
                padx=(0 if column == 0 else 16, 7),
                pady=4,
            )
            widget = ttk.Entry(
                parent, textvariable=values[key], width=width
            )
            widget.grid(row=row, column=column + 1, sticky="ew", pady=4)
            return widget

        def check_grid(
            parent: tk.Widget,
            keys: tuple[tuple[str, str], ...],
            columns: int = 3,
        ) -> None:
            for index, (key, label) in enumerate(keys):
                tk.Checkbutton(
                    parent,
                    text=label,
                    variable=boolean_vars[key],
                    bg=PANEL,
                    fg=TEXT,
                    activebackground=PANEL,
                    activeforeground=TEXT,
                    selectcolor="white",
                    bd=0,
                    anchor="w",
                ).grid(
                    row=index // columns,
                    column=index % columns,
                    sticky="w",
                    padx=(0, 16),
                    pady=4,
                )

        def text_box(
            parent: tk.Widget,
            title: str,
            hint: str,
            key: str,
            height: int = 5,
        ) -> tk.Text:
            wrapper = section(parent, title)
            ttk.Label(
                wrapper,
                text=hint,
                style="Panel.TLabel",
                foreground=MUTED,
            ).pack(anchor="w", pady=(0, 4))
            widget = tk.Text(
                wrapper,
                height=height,
                wrap="word",
                relief="solid",
                borderwidth=1,
                highlightthickness=0,
                bg="white",
                fg=TEXT,
                font=("Microsoft YaHei UI", 9),
            )
            widget.pack(fill="x")
            widget.insert("1.0", values[key].get())
            return widget

        basic_page = _ScrollableBody(notebook)
        notebook.add(basic_page, text="基础设置")
        basic = basic_page.body
        basic.columnconfigure(0, weight=1)
        basic.columnconfigure(1, weight=1)
        basic.configure(padding=(8, 8, 30, 8))

        name_box = section(basic, "模板")
        name_box.columnconfigure(1, weight=1)
        entry(name_box, 0, 0, "模板名称", "name", 54)

        price_box = section(basic, "发布与价格")
        for column in (1, 3, 5, 7):
            price_box.columnconfigure(column, weight=1)
        ttk.Label(
            price_box, text="默认发布算法", style="Panel.TLabel"
        ).grid(row=0, column=0, sticky="w", padx=(0, 7))
        ttk.Label(price_box, text="×", style="Panel.TLabel").grid(
            row=0, column=1, sticky="w"
        )
        ttk.Entry(
            price_box, textvariable=values["price_multiplier"], width=10
        ).grid(row=0, column=2, sticky="w", padx=(0, 6))
        ttk.Label(price_box, text="＋", style="Panel.TLabel").grid(
            row=0, column=3, sticky="w"
        )
        ttk.Entry(
            price_box, textvariable=values["price_offset"], width=10
        ).grid(row=0, column=4, sticky="w", padx=(0, 6))
        tk.Checkbutton(
            price_box,
            text="固定价格（元）",
            variable=boolean_vars["fixed_price_enabled"],
            bg=PANEL,
            fg=TEXT,
            activebackground=PANEL,
            selectcolor="white",
            bd=0,
        ).grid(row=0, column=5, sticky="w", padx=(10, 4))
        ttk.Entry(
            price_box, textvariable=values["fixed_price"], width=9
        ).grid(row=0, column=6, sticky="w")
        ttk.Label(
            price_box, text="最终价格小数", style="Panel.TLabel"
        ).grid(row=0, column=7, sticky="e", padx=(14, 4))
        ttk.Spinbox(
            price_box,
            textvariable=values["final_price_decimals"],
            from_=0,
            to=4,
            width=6,
        ).grid(row=0, column=8, sticky="w")
        ladder_row = ttk.Frame(price_box, style="Panel.TFrame")
        ladder_row.grid(row=1, column=0, columnspan=9, sticky="ew", pady=(8, 0))
        tk.Checkbutton(
            ladder_row,
            text="启用阶梯倍率",
            variable=boolean_vars["ladder_enabled"],
            bg=PANEL,
            fg=TEXT,
            activebackground=PANEL,
            selectcolor="white",
            bd=0,
        ).pack(side="left")
        button(
            ladder_row,
            "添加阶梯倍率",
            lambda: self._edit_ladder_rules(values, dialog),
        ).pack(side="left", padx=8)
        ttk.Label(
            ladder_row,
            text="按原价区间命中，未命中的规格使用上方默认算法。",
            style="Panel.TLabel",
            foreground=MUTED,
        ).pack(side="left")

        title_box = section(basic, "标题处理")
        title_box.columnconfigure(1, weight=1)
        title_box.columnconfigure(3, weight=1)
        entry(title_box, 0, 0, "标题前缀", "title_prefix", 30)
        entry(title_box, 0, 2, "标题后缀", "title_suffix", 30)

        video_box = section(basic, "视频来源")
        video_box.columnconfigure(1, weight=1)
        entry(video_box, 0, 0, "视频文件夹", "video_folder", 52)
        button(
            video_box,
            "选择文件夹",
            lambda: self._choose_template_folder(values["video_folder"]),
        ).grid(row=0, column=3, sticky="w", padx=(8, 0))
        ttk.Label(
            video_box,
            text="文件夹内放商品ID.mp4，或按商品ID建子文件夹",
            style="Panel.TLabel",
            foreground=MUTED,
        ).grid(row=1, column=1, columnspan=3, sticky="w")

        service_box = section(basic, "服务与发布方式")
        check_grid(
            service_box,
            (
                ("main_image_video", "主图生视频"),
                ("use_package_tags", "使用数据包标签"),
                ("read_packaging_labels", "读取包装标签图（较慢）"),
                ("clothing_mode", "服装模式（同色尺码统一最高价）"),
                ("seven_day_return", "支持七天无理由"),
                ("shipping_insurance", "支持运费险"),
                ("damage_return", "支持损坏包退"),
                ("fake_one_three", "支持假一赔三"),
                ("support_exchange", "支持换货"),
                ("auto_open_category", "缺类目时自动申请并重试"),
            ),
        )
        ttk.Label(
            service_box,
            text="勾选内容会写入模板快照；发布任务的规则以创建时的快照为准。",
            style="Panel.TLabel",
            foreground=MUTED,
        ).grid(row=4, column=0, columnspan=3, sticky="w", pady=(5, 0))
        ttk.Label(
            service_box,
            text="上传后状态",
            style="Panel.TLabel",
        ).grid(row=5, column=0, sticky="w", pady=(8, 0))
        ttk.Combobox(
            service_box,
            textvariable=listing_mode_var,
            values=tuple(LISTING_MODE_LABELS.values()),
            state="readonly",
            width=14,
        ).grid(row=5, column=1, sticky="w", pady=(8, 0))
        ttk.Label(
            service_box,
            text="默认：出售中；仓库表示创建后下架；草稿箱表示仅保存草稿",
            style="Panel.TLabel",
            foreground=MUTED,
        ).grid(
            row=6,
            column=0,
            columnspan=3,
            sticky="w",
            pady=(4, 0),
        )
        ttk.Label(
            service_box,
            text="重复商品过滤",
            style="Panel.TLabel",
        ).grid(row=7, column=0, sticky="w", pady=(10, 0))
        ttk.Combobox(
            service_box,
            textvariable=duplicate_filter_var,
            values=tuple(DUPLICATE_FILTER_LABELS.values()),
            state="readonly",
            width=16,
        ).grid(row=7, column=1, sticky="w", pady=(10, 0))
        ttk.Label(
            service_box,
            text="默认只阻止本店铺重复；关闭后即使上过也继续上传",
            style="Panel.TLabel",
            foreground=MUTED,
        ).grid(
            row=8,
            column=0,
            columnspan=3,
            sticky="w",
            pady=(4, 0),
        )

        tag_box = section(basic, "标签图资料")
        tag_box.columnconfigure(1, weight=1)
        tag_box.columnconfigure(3, weight=1)
        entry(tag_box, 0, 0, "经销者名称", "distributor_name", 28)
        entry(tag_box, 0, 2, "联系方式", "distributor_contact", 28)

        delivery_box = section(basic, "发货设置")
        delivery_box.columnconfigure(1, weight=1)
        delivery_box.columnconfigure(3, weight=1)
        ttk.Label(
            delivery_box, text="发货类型", style="Panel.TLabel"
        ).grid(row=0, column=0, sticky="w", padx=(0, 7))
        ttk.Combobox(
            delivery_box,
            textvariable=values["delivery_type"],
            values=("现货", "预售"),
            state="readonly",
            width=18,
        ).grid(row=0, column=1, sticky="w")
        ttk.Label(
            delivery_box, text="发货时效", style="Panel.TLabel"
        ).grid(row=0, column=2, sticky="w", padx=(16, 7))
        ttk.Combobox(
            delivery_box,
            textvariable=values["delivery_time"],
            values=("48小时内发货", "7天内发货", "15天内发货"),
            state="readonly",
            width=20,
        ).grid(row=0, column=3, sticky="w")
        ttk.Label(
            delivery_box, text="统一库存", style="Panel.TLabel"
        ).grid(row=1, column=0, sticky="w", padx=(0, 7), pady=(6, 0))
        ttk.Spinbox(
            delivery_box,
            textvariable=values["stock"],
            from_=0,
            to=10_000_000,
            width=14,
        ).grid(row=1, column=1, sticky="w", pady=(6, 0))

        replacement_text = text_box(
            basic,
            "标题替换",
            "每行一条：旧标题片段=新标题片段；新标题片段留空表示删除。",
            "title_replace_rules",
            4,
        )
        fallback_box = section(basic, "兜底类目")
        fallback_box.columnconfigure(1, weight=1)
        tk.Checkbutton(
            fallback_box,
            text="强制兜底",
            variable=boolean_vars["force_fallback_category"],
            bg=PANEL,
            fg=TEXT,
            activebackground=PANEL,
            selectcolor="white",
            bd=0,
        ).grid(row=0, column=0, sticky="w")
        ttk.Entry(
            fallback_box, textvariable=values["fallback_category"]
        ).grid(row=0, column=1, columnspan=3, sticky="ew", padx=(8, 0))
        entry(fallback_box, 1, 0, "最高保证金（元）", "deposit_limit", 14)
        entry(fallback_box, 1, 2, "申请品牌 ID", "category_brand_ids", 22)
        ttk.Label(
            fallback_box,
            text="申请证照组 JSON",
            style="Panel.TLabel",
        ).grid(row=2, column=0, sticky="w", pady=4)
        ttk.Entry(
            fallback_box,
            textvariable=values["category_license_groups"],
        ).grid(row=2, column=1, columnspan=3, sticky="ew", padx=(7, 0))

        low_box = section(basic, "低价引流")
        tk.Checkbutton(
            low_box,
            text="启用低价引流保护",
            variable=boolean_vars["low_price_enabled"],
            bg=PANEL,
            fg=TEXT,
            activebackground=PANEL,
            selectcolor="white",
            bd=0,
        ).grid(row=0, column=0, sticky="w")
        entry(low_box, 0, 1, "倍数", "low_price_multiplier", 10)
        ttk.Label(
            low_box, text="低价规格处理", style="Panel.TLabel"
        ).grid(row=0, column=3, sticky="w", padx=(14, 7))
        ttk.Combobox(
            low_box,
            textvariable=values["low_price_action"],
            values=("跳过商品", "删除最低价"),
            state="readonly",
            width=15,
        ).grid(row=0, column=4, sticky="w")
        entry(low_box, 1, 0, "删除前主图", "trim_head", 10)
        entry(low_box, 1, 2, "删除前详情图", "trim_detail", 10)

        filter_page = _ScrollableBody(notebook)
        notebook.add(filter_page, text="商品过滤")
        filter_body = filter_page.body
        filter_body.configure(padding=(8, 8, 30, 8))
        cloud_box = section(filter_body, "违规商品过滤")
        tk.Checkbutton(
            cloud_box,
            text="启用云违规商品过滤",
            variable=boolean_vars["cloud_violation"],
            bg=PANEL,
            fg=TEXT,
            activebackground=PANEL,
            selectcolor="white",
            bd=0,
        ).pack(side="left")
        button(
            cloud_box,
            "云违规设置",
            lambda: self._edit_cloud_violation_types(values, dialog),
        ).pack(side="left", padx=8)
        tk.Checkbutton(
            filter_body,
            text="删除商品标题开头【】中的内容",
            variable=boolean_vars["strip_title_brackets"],
            bg=PANEL,
            fg=TEXT,
            activebackground=PANEL,
            selectcolor="white",
            bd=0,
        ).pack(anchor="w", pady=(0, 7))
        title_filter_text = text_box(
            filter_body,
            "标题过滤",
            "每行一个违规词；标题命中后跳过该商品。",
            "title_rules",
            6,
        )
        sku_filter_text = text_box(
            filter_body,
            "规格过滤",
            "每行一个违规词；任意规格命中后跳过该商品。",
            "sku_rules",
            6,
        )
        image_box = section(filter_body, "图片过滤")
        image_box.columnconfigure(1, weight=1)
        ttk.Label(image_box, text="模式", style="Panel.TLabel").grid(
            row=0, column=0, sticky="w", padx=(0, 7)
        )
        ttk.Combobox(
            image_box,
            textvariable=values["image_mode"],
            values=("默认方式", "OCR 识图"),
            state="readonly",
            width=16,
        ).grid(row=0, column=1, sticky="w")
        ttk.Label(image_box, text="主图命中", style="Panel.TLabel").grid(
            row=0, column=2, sticky="w", padx=(16, 7)
        )
        ttk.Combobox(
            image_box,
            textvariable=values["head_action"],
            values=("删除违规图片", "跳过商品"),
            state="readonly",
            width=15,
        ).grid(row=0, column=3, sticky="w")
        ttk.Label(
            image_box, text="详情图命中", style="Panel.TLabel"
        ).grid(row=1, column=0, sticky="w", padx=(0, 7), pady=(7, 0))
        ttk.Combobox(
            image_box,
            textvariable=values["detail_action"],
            values=("删除违规图片", "跳过商品"),
            state="readonly",
            width=15,
        ).grid(row=1, column=1, sticky="w", pady=(7, 0))
        ttk.Label(
            image_box, text="图片违规词", style="Panel.TLabel"
        ).grid(row=1, column=2, sticky="w", padx=(16, 7), pady=(7, 0))
        ttk.Entry(
            image_box, textvariable=values["image_rules"], width=34
        ).grid(row=1, column=3, sticky="w", pady=(7, 0))

        attr_page = _ScrollableBody(notebook)
        notebook.add(attr_page, text="属性设置")
        attr_body = attr_page.body
        attr_body.configure(padding=(8, 8, 30, 8))
        attr_box = section(attr_body, "模板自定义属性")
        tk.Checkbutton(
            attr_box,
            text="启用模板自定义属性",
            variable=boolean_vars["enable_custom_attributes"],
            bg=PANEL,
            fg=TEXT,
            activebackground=PANEL,
            selectcolor="white",
            bd=0,
        ).pack(anchor="w")
        ttk.Label(
            attr_box,
            text="数据包里的类目属性始终会提交；这里只追加模板属性。",
            style="Panel.TLabel",
            foreground=MUTED,
        ).pack(anchor="w", pady=(3, 5))
        attribute_text = tk.Text(
            attr_box,
            height=18,
            wrap="word",
            relief="solid",
            borderwidth=1,
            highlightthickness=0,
            bg="white",
            fg=TEXT,
            font=("Microsoft YaHei UI", 9),
        )
        attribute_text.pack(fill="both", expand=True)
        attribute_text.insert("1.0", values["attribute_rules"].get())

        footer = ttk.Frame(dialog, style="Panel.TFrame", padding=(14, 10))
        footer.grid(row=2, column=0, sticky="ew")
        footer.columnconfigure(0, weight=1)

        def save() -> None:
            name = values["name"].get().strip()
            if not name:
                messagebox.showwarning(
                    "模板名称", "模板名称不能为空。", parent=dialog
                )
                return
            try:
                license_groups = values[
                    "category_license_groups"
                ].get().strip()
                if license_groups:
                    parsed_license_groups = json.loads(license_groups)
                    if not isinstance(parsed_license_groups, list):
                        raise ValueError("证照组必须是 JSON 数组")
                item = {
                    **template,
                    "id": template.get("id") or f"tpl_{uuid.uuid4().hex[:10]}",
                    "name": name,
                    "title_prefix": values["title_prefix"].get().strip(),
                    "title_suffix": values["title_suffix"].get().strip(),
                    "price_multiplier": float(
                        values["price_multiplier"].get() or 1
                    ),
                    "price_offset": int(
                        round(
                            float(values["price_offset"].get() or 0)
                            * 100
                        )
                    ),
                    "fixed_price_enabled": boolean_vars[
                        "fixed_price_enabled"
                    ].get(),
                    "fixed_price": int(
                        round(
                            float(values["fixed_price"].get() or 0)
                            * 100
                        )
                    ),
                    "final_price_decimals": max(
                        0,
                        min(
                            4,
                            int(
                                values[
                                    "final_price_decimals"
                                ].get()
                                or 2
                            ),
                        ),
                    ),
                    "ladder_enabled": boolean_vars[
                        "ladder_enabled"
                    ].get(),
                    "ladder_rules": values["ladder_rules"].get().strip(),
                    "stock": max(0, int(values["stock"].get() or 0)),
                    "listing_mode": next(
                        key
                        for key, label in LISTING_MODE_LABELS.items()
                        if label == listing_mode_var.get()
                    ),
                    "duplicate_filter_mode": next(
                        key
                        for key, label in DUPLICATE_FILTER_LABELS.items()
                        if label == duplicate_filter_var.get()
                    ),
                    "skip_duplicate": (
                        next(
                            key
                            for key, label in DUPLICATE_FILTER_LABELS.items()
                            if label == duplicate_filter_var.get()
                        )
                        != "off"
                    ),
                    "video_folder": values["video_folder"].get().strip(),
                    "distributor_name": values[
                        "distributor_name"
                    ].get().strip(),
                    "distributor_contact": values[
                        "distributor_contact"
                    ].get().strip(),
                    "delivery_type": values["delivery_type"].get(),
                    "delivery_time": values["delivery_time"].get(),
                    "title_replace_rules": replacement_text.get(
                        "1.0", "end-1c"
                    ).strip(),
                    "fallback_category": values[
                        "fallback_category"
                    ].get().strip(),
                    "deposit_limit": max(
                        0, int(values["deposit_limit"].get() or 0)
                    ),
                    "category_brand_ids": values[
                        "category_brand_ids"
                    ].get().strip(),
                    "category_license_groups": license_groups,
                    "low_price_multiplier": max(
                        1.0,
                        float(
                            values["low_price_multiplier"].get() or 2
                        ),
                    ),
                    "low_price_action": values[
                        "low_price_action"
                    ].get(),
                    "trim_head": max(
                        0, int(values["trim_head"].get() or 0)
                    ),
                    "trim_detail": max(
                        0, int(values["trim_detail"].get() or 0)
                    ),
                    "title_rules": title_filter_text.get(
                        "1.0", "end-1c"
                    ).strip(),
                    "sku_rules": sku_filter_text.get(
                        "1.0", "end-1c"
                    ).strip(),
                    "image_mode": values["image_mode"].get(),
                    "head_action": values["head_action"].get(),
                    "detail_action": values["detail_action"].get(),
                    "image_rules": values["image_rules"].get().strip(),
                    "attribute_rules": attribute_text.get(
                        "1.0", "end-1c"
                    ).strip(),
                    "cloud_violation_types": values[
                        "cloud_violation_types"
                    ].get().strip(),
                    "updated_at": datetime.now().isoformat(
                        timespec="seconds"
                    ),
                }
                item.update(
                    {
                        key: variable.get()
                        for key, variable in boolean_vars.items()
                    }
                )
            except ValueError:
                messagebox.showwarning(
                    "模板参数",
                    "价格、库存、保证金和小数位必须是有效数字；"
                    "证照组必须是 JSON 数组。",
                    parent=dialog,
                )
                return
            if template:
                self._templates = [
                    item if row.get("id") == template.get("id") else row
                    for row in self._templates
                    if isinstance(row, dict)
                ]
            else:
                item["created_at"] = item["updated_at"]
                self._templates.append(item)
            self._save_templates()
            dialog.destroy()
            self._render_templates()

        ttk.Label(
            footer,
            text="所有规则随模板快照保存，历史上传任务不会被后续修改覆盖。",
            style="Subtitle.TLabel",
        ).grid(row=0, column=0, sticky="w")
        button(footer, "取消", dialog.destroy).grid(
            row=0, column=1, padx=(8, 6)
        )
        button(footer, "保存模板", save, "Primary").grid(row=0, column=2)
        dialog.bind("<Escape>", lambda _event: dialog.destroy())
        dialog.grab_set()

    def _add_template(self) -> None:
        self._template_dialog()

    def _selected_template(self) -> dict[str, Any] | None:
        selection = self.template_tree.selection()
        return self._template_rows.get(selection[0]) if selection else None

    def _edit_template(self) -> None:
        template = self._selected_template()
        if template is None:
            messagebox.showinfo(
                "请选择模板", "请先选择一条模板。", parent=self
            )
            return
        self._template_dialog(template)

    def _duplicate_template(self) -> None:
        template = self._selected_template()
        if template is None:
            return
        item = {
            **template,
            "id": f"tpl_{uuid.uuid4().hex[:10]}",
            "name": f"{template.get('name', '上传模板')} 副本",
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }
        self._templates.append(item)
        self._save_templates()
        self._render_templates()

    def _delete_template(self) -> None:
        template = self._selected_template()
        if template is None:
            return
        if not messagebox.askyesno(
            "删除模板",
            f"确定删除模板“{template.get('name', '')}”吗？",
            parent=self,
        ):
            return
        self._templates = [
            row
            for row in self._templates
            if row.get("id") != template.get("id")
        ]
        self._save_templates()
        self._render_templates()

    def _set_default_template(self) -> None:
        template = self._selected_template()
        if template is None:
            return
        for row in self._templates:
            if isinstance(row, dict):
                row["is_default"] = row.get("id") == template.get("id")
        self._save_templates()
        self._render_templates()
        self.window.set_status(f"已设为默认模板：{template.get('name', '')}")

    def _parse_package(self, path: Path) -> list[dict[str, Any]]:
        text = path.read_text(encoding="utf-8-sig")
        data: Any = None
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            lines = [line for line in text.splitlines() if line.strip()]
            if lines and all(line.lstrip().startswith(("{", "[")) for line in lines):
                data = []
                for line in lines:
                    try:
                        data.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
            else:
                dialect = csv.excel_tab
                try:
                    dialect = csv.Sniffer().sniff("\n".join(lines[:20]))
                except csv.Error:
                    pass
                data = list(csv.DictReader(io.StringIO(text), dialect=dialect))
        if isinstance(data, dict):
            for key in ("products", "goods", "items", "list", "data"):
                if isinstance(data.get(key), list):
                    data = data[key]
                    break
            else:
                data = [data]
        if not isinstance(data, list):
            raise ValueError("数据包顶层必须是对象或数组")
        output = []
        fingerprint_counts: dict[str, int] = defaultdict(int)
        for row in data:
            if not isinstance(row, dict):
                continue
            payload = row.get("payload")
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except json.JSONDecodeError:
                    payload = None
            if not isinstance(payload, dict):
                payload = next(
                    (
                        row.get(key)
                        for key in (
                            "productAddBody",
                            "product",
                            "goods",
                            "source",
                        )
                        if isinstance(row.get(key), dict)
                    ),
                    None,
                )
            title = str(
                row.get("title")
                or row.get("subject")
                or row.get("offerTitle")
                or row.get("name")
                or row.get("productName")
                or (payload or {}).get("title")
                or (payload or {}).get("name")
                or ""
            ).strip()
            if not title:
                continue
            platform = str(
                row.get("platform")
                or row.get("平台")
                or (
                    "1688"
                    if "1688.com"
                    in str(row.get("productUrl") or "").casefold()
                    else ""
                )
                or (
                    "1688"
                    if row.get("offerId")
                    or row.get("offer_id")
                    or row.get("skuInfos")
                    or row.get("detailImages")
                    else
                    "淘宝"
                    if row.get("productName")
                    or row.get("productId")
                    else "微信小店"
                )
            )
            normalized_price: int | None = None
            explicit_source_id = (
                row.get("source_product_id")
                or row.get("offerId")
                or row.get("offer_id")
                or row.get("product_id")
                or row.get("productId")
                or row.get("uniqueId")
                or row.get("id")
                or (payload or {}).get("source_product_id")
                or (payload or {}).get("productId")
                or (payload or {}).get("uniqueId")
            )
            if explicit_source_id not in (None, ""):
                source_id = str(explicit_source_id)
                source_identity = "explicit"
            else:
                fingerprint = _stable_source_fingerprint(
                    platform,
                    title,
                    row,
                    payload,
                )
                fingerprint_counts[fingerprint] += 1
                occurrence = fingerprint_counts[fingerprint]
                source_id = (
                    fingerprint
                    if occurrence == 1
                    else f"{fingerprint}-{occurrence}"
                )
                source_identity = "fingerprint"
            if platform == "1688":
                source_payload = payload if isinstance(payload, dict) else row
                normalized_payload = _normalize_source_payload(
                    {**source_payload, "platform": "1688"}
                )
                payload = normalized_payload
                title = str(normalized_payload.get("title") or title)
            price_value = (
                row.get("price")
                or row.get("sale_price")
                or row.get("priceLow")
                or row.get("价格")
                or 0
            )
            if platform == "1688" and isinstance(payload, dict):
                sku_prices = [
                    int(item.get("sale_price") or 0)
                    for item in payload.get("skus") or []
                    if isinstance(item, dict)
                    and int(item.get("sale_price") or 0) > 0
                ]
                if sku_prices:
                    normalized_price = min(sku_prices)
            if not price_value and isinstance(payload, dict):
                payload_skus = payload.get("skus")
                if isinstance(payload_skus, list) and payload_skus:
                    try:
                        price_value = (
                            float(payload_skus[0].get("sale_price") or 0)
                            / 100
                        )
                    except (AttributeError, TypeError, ValueError):
                        price_value = 0
            if not price_value:
                sku_info = (row.get("skuList") or {}).get("skuInfo")
                if isinstance(sku_info, list):
                    prices = []
                    for sku in sku_info:
                        if not isinstance(sku, dict):
                            continue
                        try:
                            price = float(sku.get("price") or 0) / 100
                        except (TypeError, ValueError):
                            price = 0
                        if price > 0:
                            prices.append(price)
                    if prices:
                        price_value = min(prices)
            price = (
                normalized_price
                if normalized_price is not None
                else _price_cents(price_value)
            )
            stock, stock_known = _normalized_stock(row, payload)
            if platform == "1688" and isinstance(payload, dict):
                sku_stocks = []
                for item in payload.get("skus") or []:
                    if not isinstance(item, dict):
                        continue
                    value = _stock_int(item.get("stock_num"))
                    if value is not None:
                        sku_stocks.append(value)
                if sku_stocks:
                    stock = max(sku_stocks)
                    stock_known = True
            output.append(
                {
                    "batch_id": uuid.uuid4().hex,
                    "source_product_id": source_id,
                    "title": title,
                    "price": price,
                    "stock": stock,
                    "stock_known": stock_known,
                    "platform": platform,
                    "payload": payload if isinstance(payload, dict) else row,
                    "raw": row,
                    "source_file": path.name,
                    "source_identity": source_identity,
                }
            )
        return output

    def _import_file(self, path: Path, append: bool = False) -> None:
        self._import_files([path], append=append)

    def _import_files(
        self,
        paths: list[Path],
        *,
        append: bool = False,
    ) -> None:
        if not paths:
            return
        if not append:
            self._batch = []
            self._selected_batch_ids.clear()
        existing = {
            (
                str(item.get("source_product_id")),
                str(item.get("title")),
            )
            for item in self._batch
        }
        imported = 0
        imported_keys: set[tuple[str, str]] = set()
        failures: list[str] = []
        for path in paths:
            try:
                rows = self._parse_package(path)
            except Exception as exc:
                failures.append(f"{path.name}: {exc}")
                continue
            for row in rows:
                key = (row["source_product_id"], row["title"])
                if key not in existing:
                    self._batch.append(row)
                    existing.add(key)
                    imported_keys.add(key)
                    imported += 1
        self._normalize_batch_rows()
        valid_ids = {
            str(row.get("batch_id"))
            for row in self._batch
            if isinstance(row, dict) and row.get("batch_id")
        }
        self._selected_batch_ids.intersection_update(valid_ids)
        self._selected_batch_ids.update(
            str(row.get("batch_id"))
            for row in self._batch
            if (
                str(row.get("source_product_id")),
                str(row.get("title")),
            )
            in imported_keys
        )
        self._batch_page = 0
        self.db.set_setting("upload_last_source", str(paths[-1]))
        self._save_batch()
        self._show_view("products")
        self._render_batch()
        self.window.set_status(
            f"导入 {len(paths)} 个文件、{imported} 个商品，"
            f"本批次共 {len(self._batch)} 个商品"
            + (f"，失败 {len(failures)} 个" if failures else "")
        )
        if failures:
            messagebox.showwarning(
                "部分数据包未导入",
                "\n".join(failures[:10]),
                parent=self,
            )

    def _import_package(self) -> None:
        paths = filedialog.askopenfilenames(
            parent=self,
            title="导入商品数据包",
            filetypes=[
                ("商品数据包", "*.json *.txt *.tsv *.csv"),
                ("所有文件", "*.*"),
            ],
        )
        if paths:
            self._import_files([Path(path) for path in paths])

    def _import_1688_links(self) -> None:
        accounts = self.db.list_procurement_accounts("1688")
        if not accounts:
            messagebox.showinfo(
                "请先登录1688",
                "请先在采购中心添加并登录一个1688账号，"
                "再回来粘贴1688商品链接。",
                parent=self,
            )
            self.window.show_page("procurement")
            return

        dialog = tk.Toplevel(self)
        dialog.title("导入1688商品链接")
        dialog.geometry("760x520")
        dialog.transient(self.winfo_toplevel())
        dialog.columnconfigure(0, weight=1)
        dialog.rowconfigure(2, weight=1)
        ttk.Label(
            dialog,
            text="每行粘贴一个1688商品链接，也可以一次粘贴多个链接。",
            style="TLabel",
        ).grid(row=0, column=0, sticky="w", padx=16, pady=(16, 6))
        account_row = ttk.Frame(dialog, style="TFrame")
        account_row.grid(row=1, column=0, sticky="ew", padx=16, pady=6)
        ttk.Label(
            account_row,
            text="采集账号",
            style="TLabel",
        ).pack(side="left")
        account_labels = {
            f"{row.get('label') or row.get('login_id') or ('1688账号' + str(row['id']))} "
            f"(#{row['id']})": row
            for row in accounts
        }
        account_var = tk.StringVar(value=next(iter(account_labels)))
        account_combo = ttk.Combobox(
            account_row,
            textvariable=account_var,
            values=tuple(account_labels),
            state="readonly",
            width=34,
        )
        account_combo.pack(side="left", padx=(8, 18))
        ttk.Label(
            account_row,
            text="详情图跳过前",
            style="TLabel",
        ).pack(side="left")
        skip_var = tk.StringVar(value="2")
        ttk.Spinbox(
            account_row,
            from_=0,
            to=20,
            textvariable=skip_var,
            width=6,
        ).pack(side="left", padx=5)
        ttk.Label(account_row, text="张", style="TLabel").pack(side="left")

        editor = tk.Text(
            dialog,
            wrap="word",
            undo=True,
            relief="solid",
            borderwidth=1,
            height=14,
            font=("Microsoft YaHei UI", 10),
        )
        editor.grid(
            row=2,
            column=0,
            sticky="nsew",
            padx=16,
            pady=(8, 8),
        )
        editor.insert(
            "1.0",
            "https://detail.1688.com/offer/1098691125768.html",
        )
        note = ttk.Label(
            dialog,
            text=(
                "只接受 detail.1688.com 等 1688 商品链接。"
                "采集不会购买商品；遇到登录失效或安全验证时会停止并提示。"
            ),
            style="Subtitle.TLabel",
            justify="left",
        )
        note.grid(row=3, column=0, sticky="w", padx=16, pady=(0, 8))
        actions = ttk.Frame(dialog, style="TFrame")
        actions.grid(row=4, column=0, sticky="e", padx=16, pady=(0, 16))

        def start() -> None:
            text = editor.get("1.0", "end")
            links = parse_1688_links(text)
            if not links:
                messagebox.showwarning(
                    "没有有效链接",
                    "当前只支持1688商品链接，并且需要包含商品 offerId。",
                    parent=dialog,
                )
                return
            account = account_labels.get(account_var.get())
            if account is None:
                messagebox.showwarning(
                    "请选择账号",
                    "请选择用于采集的1688账号。",
                    parent=dialog,
                )
                return
            try:
                skip_count = max(0, min(20, int(skip_var.get() or 0)))
            except ValueError:
                messagebox.showwarning(
                    "详情图数量无效",
                    "详情图跳过数量必须是0到20之间的整数。",
                    parent=dialog,
                )
                return
            dialog.destroy()
            self._collect_1688_links(account, links, skip_count)

        button(actions, "取消", dialog.destroy).pack(side="left", padx=6)
        button(actions, "开始采集", start, "Primary").pack(side="left")
        dialog.bind("<Escape>", lambda _event: dialog.destroy())
        dialog.grab_set()

    def _collect_1688_links(
        self,
        account: dict[str, Any],
        links: list[Any],
        skip_detail_images: int,
    ) -> None:
        def work(progress) -> list[dict[str, Any]]:
            collector = Link1688Collector(
                self.db,
                self.window.paths.data_dir,
            )
            return collector.collect(
                account,
                links,
                skip_detail_images=skip_detail_images,
                progress=progress,
            )

        def done(results: list[dict[str, Any]]) -> None:
            successes = [
                normalized_1688_row(result["data"])
                for result in results
                if result.get("ok") and isinstance(result.get("data"), dict)
            ]
            failures = [
                result
                for result in results
                if not result.get("ok")
            ]
            existing = {
                str(item.get("source_product_id") or "")
                for item in self._batch
                if isinstance(item, dict)
            }
            imported = 0
            imported_ids: set[str] = set()
            for row in successes:
                source_id = str(row.get("source_product_id") or "")
                if source_id and source_id in existing:
                    continue
                self._batch.append(row)
                existing.add(source_id)
                imported_ids.add(source_id)
                imported += 1
            self._normalize_batch_rows()
            valid_ids = {
                str(row.get("batch_id"))
                for row in self._batch
                if isinstance(row, dict) and row.get("batch_id")
            }
            self._selected_batch_ids.intersection_update(valid_ids)
            self._selected_batch_ids.update(
                str(row.get("batch_id"))
                for row in self._batch
                if str(row.get("source_product_id")) in imported_ids
            )
            self._batch_page = 0
            self._save_batch()
            self._render_batch()
            self._show_view("products")
            self.window.set_status(
                f"1688链接采集完成：新增 {imported} 个商品"
                + (f"，失败 {len(failures)} 个" if failures else ""),
                busy=False,
            )
            if failures:
                messagebox.showwarning(
                    "部分1688链接未采集",
                    "\n".join(
                        f"{item.get('offer_id', '')}: {item.get('error', '')}"
                        for item in failures[:10]
                    ),
                    parent=self,
                )

        def error(exc: Exception) -> None:
            self.window.set_status("1688链接采集未完成", busy=False)
            if isinstance(exc, Source1688LoginRequired):
                messagebox.showwarning(
                    "1688登录已失效",
                    str(exc),
                    parent=self,
                )
                self.window.show_page("procurement")
                return
            messagebox.showerror(
                "1688链接采集失败",
                str(exc),
                parent=self,
            )

        self.window.run_task(
            work,
            done,
            error,
            progress=self._progress,
            log_title="采集1688商品链接",
        )

    @staticmethod
    def _package_files(folder: Path) -> list[Path]:
        return sorted(
            path
            for path in folder.rglob("*")
            if path.is_file()
            and path.suffix.lower() in {".json", ".txt", ".tsv", ".csv"}
        )

    def _import_folder(self) -> None:
        folder = filedialog.askdirectory(
            parent=self,
            title="选择商品数据包文件夹",
        )
        if not folder:
            return
        files = self._package_files(Path(folder))
        if not files:
            messagebox.showinfo(
                "文件夹中没有数据包",
                "支持的格式：JSON、TXT、TSV、CSV。",
                parent=self,
            )
            return
        self._import_files(files)

    def _continue_import(self) -> None:
        value = self.db.get_setting("upload_last_source", "")
        path = Path(value) if value else None
        if path is None or not path.is_file():
            self._import_package()
            return
        self._import_file(path, append=True)

    def _clear_batch(self) -> None:
        if not self._batch:
            return
        if not messagebox.askyesno(
            "清空商品",
            "清空当前导入批次？已经创建的上传任务不会受影响。",
            parent=self,
        ):
            return
        self._batch = []
        self._selected_batch_ids.clear()
        self._batch_page = 0
        self._save_batch()
        self._render_batch()

    def _sync_filter_controls(self, reset_page: bool = False) -> None:
        if not hasattr(self, "filter_control"):
            return
        for widget in (
            self.filter_all_label,
            self.filter_keyword_label,
            self.filter_keyword_entry,
            self.filter_min_label,
            self.filter_min_entry,
            self.filter_to_label,
            self.filter_max_entry,
        ):
            widget.pack_forget()
        mode = self.filter_mode_var.get()
        if mode == "标题包含":
            self.filter_keyword_label.configure(text="标题包含")
            self.filter_keyword_label.pack(side="left", padx=(0, 5))
            self.filter_keyword_entry.pack(side="left")
        elif mode == "SKU包含":
            self.filter_keyword_label.configure(text="SKU包含")
            self.filter_keyword_label.pack(side="left", padx=(0, 5))
            self.filter_keyword_entry.pack(side="left")
        elif mode in {"库存范围", "价格范围"}:
            unit = "库存" if mode == "库存范围" else "价格（元）"
            self.filter_min_label.configure(text=f"最低{unit}")
            self.filter_max_entry.configure(width=9 if mode == "库存范围" else 11)
            self.filter_min_label.pack(side="left", padx=(0, 5))
            self.filter_min_entry.pack(side="left")
            self.filter_to_label.pack(side="left", padx=5)
            self.filter_max_entry.pack(side="left")
        else:
            self.filter_all_label.pack(side="left")
        if reset_page:
            self._batch_page = 0

    def _apply_batch_filter(self) -> None:
        try:
            self._visible_batch()
        except ValueError as exc:
            messagebox.showwarning(
                "筛选条件有误",
                str(exc),
                parent=self,
            )
            return
        self._batch_page = 0
        self._render_batch()

    def _reset_batch_filter(self) -> None:
        self.filter_mode_var.set("全部商品")
        self.filter_keyword_var.set("")
        self.filter_min_var.set("")
        self.filter_max_var.set("")
        self.platform_var.set("全部平台")
        self._batch_page = 0
        self._sync_filter_controls()
        self._render_batch()

    def _delete_selected_batch(self) -> None:
        self._sync_batch_selection()
        if not self._selected_batch_ids:
            messagebox.showinfo(
                "请选择商品",
                "请先选择一条或多条商品再删除。",
                parent=self,
            )
            return
        if not messagebox.askyesno(
            "删除选中商品",
            f"确定从当前导入批次删除 "
            f"{len(self._selected_batch_ids)} 条商品吗？\n"
            "已经创建的上传任务不会受影响。",
            parent=self,
        ):
            return
        selected_ids = set(self._selected_batch_ids)
        self._batch = [
            row
            for row in self._batch
            if isinstance(row, dict)
            and str(row.get("batch_id")) not in selected_ids
        ]
        self._selected_batch_ids.difference_update(selected_ids)
        self._save_batch()
        self._render_batch()
        self.window.set_status(f"已删除 {len(selected_ids)} 条商品")

    def _visible_batch(self) -> list[dict[str, Any]]:
        return _filter_batch_rows(
            self._batch,
            mode=self.filter_mode_var.get(),
            keyword=self.filter_keyword_var.get(),
            minimum=self.filter_min_var.get(),
            maximum=self.filter_max_var.get(),
            platform=self.platform_var.get(),
        )

    def _render_batch(self) -> None:
        if not hasattr(self, "batch_tree"):
            return
        self._normalize_batch_rows()
        try:
            visible = self._visible_batch()
        except ValueError:
            visible = []
        self._current_filtered_ids = {
            str(row.get("batch_id"))
            for row in visible
            if isinstance(row, dict) and row.get("batch_id")
        }
        # A filter change defines the current working set. Hidden selections
        # must not remain in the count or be deleted by a visible-only action.
        self._selected_batch_ids.intersection_update(
            self._current_filtered_ids
        )
        page_count = max(
            1,
            (
                len(visible)
                + self._batch_page_size
                - 1
            )
            // self._batch_page_size,
        )
        self._batch_page = min(max(0, self._batch_page), page_count - 1)
        start = self._batch_page * self._batch_page_size
        end = min(start + self._batch_page_size, len(visible))
        page_rows = visible[start:end]
        self.batch_tree.delete(*self.batch_tree.get_children())
        self._batch_rows.clear()
        for index, row in enumerate(page_rows, start + 1):
            iid = str(row.get("batch_id"))
            self._batch_rows[iid] = row
            self.batch_tree.insert(
                "",
                "end",
                iid=iid,
                values=(
                    "",
                    index,
                    str(row.get("title") or ""),
                    str(row.get("source_product_id") or ""),
                    f"¥{_row_price_cents(row) / 100:,.2f}",
                    (
                        _row_stock(row)
                        if _row_stock_known(row)
                        else "未知"
                    ),
                    str(row.get("platform") or ""),
                    str(row.get("source_file") or ""),
                ),
            )
        selected_iids = tuple(
            iid
            for iid in self._batch_rows
            if iid in self._selected_batch_ids
        )
        self.batch_tree.selection_set(selected_iids)
        self.batch_page_var.set(
            f"第 {self._batch_page + 1} / {page_count} 页"
            + (
                f" · 本页 {start + 1}-{end}"
                if page_rows
                else " · 当前页无商品"
            )
        )
        self.batch_prev_button.configure(
            state="normal" if self._batch_page > 0 else "disabled"
        )
        self.batch_next_button.configure(
            state=(
                "normal"
                if self._batch_page + 1 < page_count
                else "disabled"
            )
        )
        if hasattr(self, "batch_empty_label"):
            if visible:
                self.batch_empty_label.place_forget()
            else:
                self.batch_empty_label.place(relx=0.5, rely=0.48, anchor="center")
        self._update_batch_summary()

    def _change_batch_page(self, delta: int) -> None:
        self._batch_page = max(0, self._batch_page + delta)
        self._render_batch()

    def _select_all_batch(self) -> None:
        self._select_batch_result()

    def _select_batch_result(self, *, force: bool = False) -> None:
        filtered = set(self._current_filtered_ids)
        if not filtered:
            return
        if not force and filtered.issubset(self._selected_batch_ids):
            self._selected_batch_ids.difference_update(filtered)
        else:
            self._selected_batch_ids.update(filtered)
        self._apply_selection_to_tree()
        self._update_batch_summary()

    def _clear_batch_selection(self) -> None:
        self._selected_batch_ids.clear()
        self.batch_tree.selection_set(())
        self._update_batch_summary()

    def _invert_batch_selection(self, *, all_items: bool = False) -> None:
        if all_items:
            target_ids = {
                str(row.get("batch_id"))
                for row in self._batch
                if isinstance(row, dict) and row.get("batch_id")
            }
        else:
            target_ids = set(self._current_filtered_ids)
        self._selected_batch_ids.symmetric_difference_update(target_ids)
        self._apply_selection_to_tree()
        self._update_batch_summary()

    def _apply_selection_to_tree(self) -> None:
        selected_iids = tuple(
            iid
            for iid, row in self._batch_rows.items()
            if str(row.get("batch_id")) in self._selected_batch_ids
        )
        self.batch_tree.selection_set(selected_iids)

    def _sync_batch_selection(self) -> None:
        selected_items = set(self.batch_tree.selection())
        for iid, row in self._batch_rows.items():
            batch_id = str(row.get("batch_id"))
            if iid in selected_items:
                self._selected_batch_ids.add(batch_id)
            else:
                self._selected_batch_ids.discard(batch_id)

    def _show_batch_context_menu(self, event) -> None:
        iid = self.batch_tree.identify_row(event.y)
        if iid and iid not in self.batch_tree.selection():
            self.batch_tree.selection_set((iid,))
            self._update_batch_summary()
        try:
            self.batch_context_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.batch_context_menu.grab_release()

    def _update_batch_summary(self, _event=None) -> None:
        if not hasattr(self, "batch_tree"):
            return
        self._sync_batch_selection()
        selected_items = tuple(self.batch_tree.selection())
        visible_items = tuple(self.batch_tree.get_children())
        selected = len(self._selected_batch_ids)
        visible = len(visible_items)
        filtered = len(self._current_filtered_ids)
        self.batch_summary_var.set(
            f"总商品 {len(self._batch)}\n"
            f"筛选结果 {filtered}\n"
            f"已选商品 {selected}\n"
            f"已选店铺 —\n"
            f"预计每店 —"
        )
        self.summary_var.set(
            f"本页 {visible} · 筛选 {filtered} / {len(self._batch)}"
            f" · 已选 {selected}"
        )
        if hasattr(self, "select_all_button"):
            self.select_all_button.configure(
                text=(
                    "取消筛选结果"
                    if self._current_filtered_ids
                    and self._current_filtered_ids.issubset(
                        self._selected_batch_ids
                    )
                    else "全选筛选结果"
                )
            )
        selected_set = set(selected_items)
        for iid in visible_items:
            values = list(self.batch_tree.item(iid, "values"))
            if not values:
                continue
            values[0] = "✓" if iid in selected_set else ""
            self.batch_tree.item(iid, values=values)

    def _choose_stores(self) -> list[Store]:
        stores = self.db.list_stores()
        if not stores:
            return []
        groups = sorted(
            set(self.db.list_store_groups())
            | {store.group_name for store in stores if store.group_name}
        )
        if any(not store.group_name for store in stores):
            groups.append("未分组")
        dialog = tk.Toplevel(self)
        dialog.title("选择上传店铺")
        dialog.transient(self.winfo_toplevel())
        dialog.geometry("900x560")
        dialog.minsize(760, 480)
        dialog.columnconfigure(0, weight=1)
        dialog.rowconfigure(0, weight=1)
        body = ttk.Frame(dialog, padding=(12, 12, 12, 0))
        body.grid(row=0, column=0, sticky="nsew")
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)

        group_panel = ttk.Frame(body, style="Panel.TFrame", padding=10)
        group_panel.grid(row=0, column=0, sticky="ns", padx=(0, 10))
        ttk.Label(
            group_panel,
            text="按分组选择",
            style="Section.TLabel",
        ).pack(anchor="w")
        ttk.Label(
            group_panel,
            text="Ctrl / Shift 可多选分组\n选择后会勾选分组内全部店铺",
            style="Panel.TLabel",
            foreground=MUTED,
        ).pack(anchor="w", pady=(4, 8))
        group_list = tk.Listbox(
            group_panel,
            selectmode="extended",
            exportselection=False,
            width=22,
            height=16,
            activestyle="none",
        )
        for group_name in groups:
            group_list.insert("end", group_name)
        group_list.pack(fill="both", expand=True)
        group_actions = ttk.Frame(group_panel, style="Panel.TFrame")
        group_actions.pack(fill="x", pady=(8, 0))
        button(
            group_actions,
            "全选分组",
            lambda: (
                group_list.selection_set(0, "end"),
                choose_groups(),
            ),
        ).pack(side="left")
        button(
            group_actions,
            "清空",
            lambda: (
                group_list.selection_clear(0, "end"),
                tree.selection_set(()),
                update_summary(),
            ),
        ).pack(side="left", padx=(6, 0))

        store_panel = ttk.Frame(body)
        store_panel.grid(row=0, column=1, sticky="nsew")
        store_panel.columnconfigure(0, weight=1)
        store_panel.rowconfigure(0, weight=1)
        tree = ttk.Treeview(
            store_panel,
            columns=("name", "group", "login"),
            show="headings",
            selectmode="extended",
        )
        for key, text, width in (
            ("name", "店铺", 260),
            ("group", "分组", 180),
            ("login", "网页登录", 120),
        ):
            tree.heading(key, text=text)
            tree.column(key, width=width, anchor="w")
        for store in stores:
            tree.insert(
                "",
                "end",
                iid=str(store.id),
                values=(store.name, store.group_name or "未分组", store.web_status),
            )
        tree.grid(row=0, column=0, sticky="nsew", padx=12, pady=(12, 0))
        ttk.Scrollbar(
            store_panel,
            orient="vertical",
            command=tree.yview,
        ).grid(row=0, column=1, sticky="ns", padx=(0, 12), pady=(12, 0))
        tree.selection_set(tuple(str(store.id) for store in stores))
        result: list[Store] = []

        summary_var = tk.StringVar(
            value=f"已选择 {len(stores)} 家店铺"
        )
        summary = ttk.Label(
            store_panel,
            textvariable=summary_var,
            style="Panel.TLabel",
        )
        summary.grid(row=1, column=0, columnspan=2, sticky="w", padx=12, pady=8)

        def update_summary(_event=None) -> None:
            summary_var.set(
                f"已选择 {len(tree.selection())} 家店铺"
            )

        def choose_groups(_event=None) -> None:
            names = [
                str(group_list.get(index))
                for index in group_list.curselection()
            ]
            selected = _stores_matching_group_names(stores, names)
            tree.selection_set(tuple(str(store.id) for store in selected))
            update_summary()

        tree.bind("<<TreeviewSelect>>", update_summary)
        group_list.bind("<<ListboxSelect>>", choose_groups)

        def accept() -> None:
            chosen = {int(iid) for iid in tree.selection()}
            result.extend(store for store in stores if store.id in chosen)
            dialog.destroy()

        actions = ttk.Frame(dialog)
        actions.grid(row=1, column=0, sticky="e", padx=12, pady=12)
        button(actions, "取消", dialog.destroy).pack(side="left", padx=6)
        button(actions, "确定", accept, "Primary").pack(side="left")
        dialog.grab_set()
        self.wait_window(dialog)
        return result

    def _chosen_template(self) -> dict[str, Any] | None:
        name = self.template_var.get()
        return next(
            (
                row
                for row in self._templates
                if isinstance(row, dict) and row.get("name") == name
            ),
            None,
        )

    def _create_tasks(self) -> None:
        self._sync_batch_selection()
        selected = [
            row
            for row in self._batch
            if isinstance(row, dict)
            and str(row.get("batch_id")) in self._selected_batch_ids
        ]
        if not selected:
            messagebox.showinfo(
                "请选择商品", "请先选择至少一个商品。", parent=self
            )
            return
        zero_stock = [
            row
            for row in selected
            if _row_stock_known(row) and _row_stock(row) <= 0
        ]
        if zero_stock:
            selected = [
                row for row in selected if _row_stock(row) > 0
            ]
            messagebox.showwarning(
                "已跳过零库存商品",
                f"本批次选中商品中有 {len(zero_stock)} 条库存为零，"
                "不会创建上货任务。",
                parent=self,
            )
            if not selected:
                return
        template = self._chosen_template()
        if template is None:
            messagebox.showinfo(
                "请选择模板",
                "请先选择一个上传模板；模板规则会在创建任务时锁定。",
                parent=self,
            )
            return
        stores = self._choose_stores()
        if not stores:
            messagebox.showinfo(
                "请选择店铺", "请至少选择一家店铺。", parent=self
            )
            return
        self._show_view("tasks")
        base_payload = [
            {
                "title": row.get("title", ""),
                "price": row.get("price", 0),
                "stock": row.get("stock", 0),
                "stock_known": _row_stock_known(row),
                "payload": row.get("payload"),
                "raw": row.get("raw"),
                "source_product_id": row.get("source_product_id", ""),
            }
            for row in selected
        ]
        average = self.assignment_var.get().startswith("平均")
        for index, store in enumerate(stores):
            if average and len(stores) > 1:
                start = index * len(base_payload) // len(stores)
                end = (index + 1) * len(base_payload) // len(stores)
                items = base_payload[start:end]
            else:
                items = base_payload
            task = {
                "id": f"up_{uuid.uuid4().hex[:12]}",
                "store_id": store.id,
                "store_name": store.name,
                "template": template,
                "status": "pending",
                "created_at": datetime.now().isoformat(timespec="seconds"),
                "message": "等待开始",
                "items": [
                    {
                        **item,
                        "item_id": uuid.uuid4().hex,
                        "status": "pending",
                        "message": "等待上传",
                        "product_id": "",
                    }
                    for item in items
                ],
            }
            self._tasks.insert(0, task)
        self._save_tasks()
        self._render_tasks()
        self.window.set_status(f"已创建 {len(stores)} 个上传任务")

    def _task_counts(self, task: dict[str, Any]) -> tuple[int, int, int]:
        items = task.get("items") or []
        success = sum(item.get("status") == "success" for item in items)
        failed = sum(item.get("status") == "failed" for item in items)
        return success, failed, len(items)

    def _render_tasks(self) -> None:
        if not hasattr(self, "task_tree"):
            return
        tasks_view = self._task_list_snapshot()
        self.task_tree.delete(*self.task_tree.get_children())
        self._task_rows.clear()
        running = queued = blocked = completed = 0
        status_labels = {
            "pending": "待执行",
            "queued": "排队中",
            "running": "执行中",
            "paused": "已暂停",
            "partial": "需处理",
            "needs_verification": "待核实",
            "needs_category": "待类目生效",
            "failed": "失败",
            "completed": "已完成",
        }
        status_filter = self.task_filter_var.get()
        for index, task in enumerate(tasks_view):
            iid = str(index)
            self._task_rows[iid] = task
            success, failed, total = self._task_counts(task)
            status = str(task.get("status") or "pending")
            if status == "running":
                running += 1
            elif status in {"pending", "queued"}:
                queued += 1
            elif status in {
                "failed",
                "partial",
                "needs_verification",
                "needs_category",
            } or failed:
                blocked += 1
            elif status == "completed":
                completed += 1
            status_label = status_labels.get(status, status)
            if status_filter == "需处理" and status not in {
                "partial",
                "failed",
                "needs_verification",
                "needs_category",
            }:
                continue
            if (
                status_filter not in {"全部状态", "需处理"}
                and status_label != status_filter
            ):
                continue
            template = task.get("template") or {}
            self.task_tree.insert(
                "",
                "end",
                iid=iid,
                values=(
                    str(task.get("created_at") or "")[:16],
                    task.get("store_name", ""),
                    template.get("name", "不使用模板"),
                    status_label,
                    f"{success + failed}/{total}",
                    task.get("message") or "",
                ),
            )
        self.task_summary_var.set(
            f"运行 {running} · 排队 {queued} · 需处理 {blocked} · 已完成 {completed}"
        )
        self._task_has_startable = any(
            item.get("status")
            in {
                "pending",
                "queued",
                "running",
                "submitting",
                "paused",
                "failed",
                "waiting_category",
            }
            for task in tasks_view
            for item in task.get("items") or []
        )
        self._task_has_retry = any(
            item.get("status") == "failed"
            for task in tasks_view
            for item in task.get("items") or []
        )
        self._task_has_completed = bool(completed)
        self._task_has_uploaded = any(
            item.get("status") == "success"
            and item.get("product_id")
            for task in tasks_view
            for item in task.get("items") or []
        )
        self._update_task_actions()

    def _update_task_actions(self, _event=None) -> None:
        has_selection = bool(self.task_tree.selection())
        task_mutation_allowed = not (
            self._running or self._run_requested
        )
        self.task_start_button.configure(
            state=(
                "normal"
                if (
                    task_mutation_allowed
                    and getattr(self, "_task_has_startable", False)
                )
                else "disabled"
            )
        )
        self.task_retry_button.configure(
            state=(
                "normal"
                if (
                    task_mutation_allowed
                    and getattr(self, "_task_has_retry", False)
                )
                else "disabled"
            )
        )
        self.task_pause_button.configure(
            state="normal" if self._tasks else "disabled"
        )
        self.task_clear_button.configure(
            state=(
                "normal"
                if (
                    task_mutation_allowed
                    and getattr(self, "_task_has_completed", False)
                )
                else "disabled"
            )
        )
        self.task_delete_button.configure(
            state=(
                "normal"
                if task_mutation_allowed and has_selection
                else "disabled"
            )
        )
        self.task_list_button.configure(
            state=(
                "normal"
                if getattr(self, "_task_has_uploaded", False)
                else "disabled"
            )
        )

    def _pause_tasks(self) -> None:
        self._paused = True
        self.window.set_status("已请求暂停，当前商品处理结束后停止")

    def _retry_failed(self) -> None:
        if self._running or self._run_requested:
            messagebox.showwarning(
                "上传任务正在运行",
                "请先暂停并等待当前商品结束，再重试失败任务。",
                parent=self,
            )
            return
        selected = [
            self._task_rows[iid]
            for iid in self.task_tree.selection()
            if iid in self._task_rows
        ]
        if not selected:
            selected = self._tasks
        changed = 0
        for task in selected:
            for item in task.get("items") or []:
                if item.get("status") == "failed":
                    item["status"] = "pending"
                    item["message"] = "等待重试"
                    changed += 1
            if task.get("status") in {"failed", "partial"}:
                task["status"] = "paused"
                task["message"] = "失败商品已放回队列"
        self._save_tasks()
        self._render_tasks()
        self.window.set_status(f"已放回 {changed} 个失败商品")

    def _list_uploaded_products(self) -> None:
        selected = [
            self._task_rows[iid]
            for iid in self.task_tree.selection()
            if iid in self._task_rows
        ]
        tasks = selected or self._tasks
        grouped: dict[int, list[tuple[dict, dict]]] = defaultdict(list)
        for task in tasks:
            store_id = int(task.get("store_id") or 0)
            if not store_id:
                continue
            for item in task.get("items") or []:
                product_id = str(item.get("product_id") or "")
                if (
                    item.get("status") == "success"
                    and product_id
                    and not item.get("listed")
                ):
                    grouped[store_id].append((task, item))
        if not grouped:
            messagebox.showinfo(
                "没有待上架商品",
                "所选任务中没有已上传但未标记上架的商品。",
                parent=self,
            )
            return
        product_count = sum(len(rows) for rows in grouped.values())
        if not messagebox.askyesno(
            "确认上架",
            f"即将把 {len(grouped)} 家店铺的 {product_count} 个已上传商品"
            "提交上架。\\n\\n是否继续？",
            parent=self,
        ):
            return

        def work(progress) -> list[str]:
            errors: list[str] = []
            for index, (store_id, rows) in enumerate(grouped.items(), 1):
                store = self.db.get_store(store_id)
                if store is None:
                    errors.append(f"店铺 #{store_id}: 店铺不存在")
                    continue
                product_ids = [
                    str(item["product_id"])
                    for _task, item in rows
                ]
                if progress:
                    progress(
                        f"正在上架 {store.name} {index}/{len(grouped)}",
                        index,
                        len(grouped),
                    )
                try:
                    result = self.window.service.set_products_listing(
                        store,
                        product_ids,
                        True,
                    )
                except Exception as exc:
                    errors.append(f"{store.name}: {exc}")
                    continue
                errors.extend(
                    f"{store.name}: {message}"
                    for message in result.errors
                )
                failed_ids = {
                    str(message).split(":", 1)[0]
                    for message in result.errors
                }
                for _task, item in rows:
                    if str(item.get("product_id")) not in failed_ids:
                        item["listed"] = True
                        item["listed_at"] = datetime.now().isoformat(
                            timespec="seconds"
                        )
                        item["message"] = (
                            str(item.get("message") or "")
                            + "；已提交上架"
                        ).strip("；")
            self._save_tasks()
            return errors

        def done(errors: list[str]) -> None:
            self._render_tasks()
            self.window.set_status(
                f"上架提交完成，问题 {len(errors)} 条"
                if errors
                else "已上传商品已提交上架"
            )
            if errors:
                messagebox.showwarning(
                    "部分商品上架失败",
                    "\\n".join(errors[:10]),
                    parent=self,
                )

        self.window.run_task(
            work,
            done,
            lambda error: messagebox.showerror(
                "上架已上传商品失败",
                error,
                parent=self,
            ),
            progress=self._progress,
            log_title="上架已上传商品",
        )

    def _has_waiting_category_items(self) -> bool:
        return any(
            item.get("status") == "waiting_category"
            for task in self._tasks
            for item in task.get("items") or []
        )

    def _cancel_category_retry(self, _event=None) -> None:
        job = getattr(self, "_category_retry_job", None)
        self._category_retry_job = None
        if job is None:
            return
        try:
            self.winfo_toplevel().after_cancel(job)
        except (AttributeError, tk.TclError):
            pass

    def _schedule_category_retry(self) -> None:
        if (
            getattr(self, "_category_retry_job", None) is not None
            or not self._has_waiting_category_items()
        ):
            return
        try:
            scheduler = self.winfo_toplevel().after
        except (AttributeError, tk.TclError):
            return
        try:
            minutes = int(
                os.environ.get(
                    "WEIMI_CATEGORY_RETRY_MINUTES",
                    "10",
                )
            )
        except ValueError:
            minutes = 10
        delay = max(1, min(120, minutes)) * 60_000
        try:
            self._category_retry_job = scheduler(
                delay,
                self._auto_retry_category_tasks,
            )
        except (AttributeError, tk.TclError):
            self._category_retry_job = None

    def _auto_retry_category_tasks(self) -> None:
        self._category_retry_job = None
        if not self._has_waiting_category_items():
            return
        if self._running or self._run_requested:
            self._schedule_category_retry()
            return
        self.window.set_status(
            "正在检查已申请类目并自动恢复上货…",
            busy=True,
        )
        self._start_tasks(automatic=True)
        self._schedule_category_retry()

    def _start_tasks(self, *, automatic: bool = False) -> None:
        if self._running:
            return
        with self._task_lock:
            identities_changed = _recover_upload_tasks(self._tasks)
        if identities_changed:
            self._save_tasks()
        selected = []
        if automatic:
            selected = [
                task
                for task in self._tasks
                if any(
                    item.get("status") == "waiting_category"
                    for item in task.get("items") or []
                )
            ]
        else:
            selected = [
                self._task_rows[iid]
                for iid in self.task_tree.selection()
                if iid in self._task_rows
            ]
        if not selected and not automatic:
            selected = [
                task
                for task in self._tasks
                if task.get("status")
                in {
                    "pending",
                    "queued",
                    "paused",
                    "partial",
                    "failed",
                    "needs_category",
                }
            ]
        if not selected:
            messagebox.showinfo(
                "没有待执行任务", "当前没有待执行的上传任务。", parent=self
            )
            return
        if not automatic and not messagebox.askyesno(
            "确认上传商品",
            f"即将向微信小店创建商品，共 {len(selected)} 个任务。\n"
            "每个商品都可能消耗提审额度，是否继续？",
            parent=self,
        ):
            return
        self._paused = False
        self._running = False
        self._run_requested = True

        def work(progress) -> list[str]:
            self._running = True
            self._run_requested = False
            errors: list[str] = []
            completed_sources: dict[int, set[str]] = defaultdict(set)
            completed_sources_computer: set[str] = set()
            history_loaded: set[int] = set()
            for previous_task in self._tasks:
                store_id = int(previous_task.get("store_id") or 0)
                for previous_item in previous_task.get("items") or []:
                    if previous_item.get("status") == "success":
                        source_id = str(
                            previous_item.get("source_product_id") or ""
                        )
                        if source_id:
                            completed_sources[store_id].add(source_id)
                            completed_sources_computer.add(source_id)
                            self.db.record_upload_history(
                                store_id,
                                source_id,
                                str(previous_item.get("product_id") or ""),
                                str(previous_item.get("title") or ""),
                            )

            stores: dict[int, Store] = {}
            lanes: dict[int, deque[tuple[dict, dict, int]]] = defaultdict(
                deque
            )
            for task_index, task in enumerate(selected, 1):
                store_id = int(task.get("store_id") or 0)
                store = stores.get(store_id)
                if store is None:
                    store = self.db.get_store(store_id)
                    if store is not None:
                        stores[store_id] = store
                if store is None:
                    task["status"] = "failed"
                    task["message"] = "店铺不存在"
                    errors.append(
                        f"{task.get('store_name')}: 店铺不存在"
                    )
                    continue
                if store_id not in history_loaded:
                    persisted = self.db.uploaded_source_ids(store_id)
                    completed_sources[store_id].update(persisted)
                    completed_sources_computer.update(persisted)
                    history_loaded.add(store_id)
                task["status"] = "running"
                template = task.get("template") or {}
                duplicate_filter_mode = (
                    _normalize_duplicate_filter_mode(template)
                )
                for item in task.get("items") or []:
                    if item.get("status") == "success":
                        continue
                    if item.get("status") in {
                        "submitting",
                        "verification_pending",
                    }:
                        continue
                    source_id = str(
                        item.get("source_product_id") or ""
                    )
                    duplicate = _is_duplicate_upload(
                        duplicate_filter_mode,
                        source_id,
                        store_id,
                        completed_sources,
                        completed_sources_computer,
                    )
                    if duplicate_filter_mode != "off" and duplicate:
                        item["status"] = "skipped"
                        item["message"] = (
                            "本电脑已上传过相同来源商品"
                            if duplicate_filter_mode == "computer"
                            else "本店铺已上传过相同来源商品"
                        )
                        continue
                    if source_id:
                        completed_sources[store_id].add(source_id)
                        completed_sources_computer.add(source_id)
                    item["status"] = "queued"
                    item["message"] = "等待并发上传"
                    lanes[store_id].append(
                        (task, item, task_index)
                    )

            self._save_tasks()
            ordered_jobs: list[tuple[dict, dict, int]] = []
            active_lanes = {
                store_id: queue
                for store_id, queue in lanes.items()
                if queue
            }
            while active_lanes:
                for store_id in list(active_lanes):
                    queue = active_lanes[store_id]
                    if queue:
                        ordered_jobs.append(queue.popleft())
                    if not queue:
                        del active_lanes[store_id]

            total_jobs = len(ordered_jobs)
            clients = {
                store_id: self.window.service.client_for(store)
                for store_id, store in stores.items()
                if any(
                    int(task.get("store_id") or 0) == store_id
                    for task in selected
                )
            }
            try:
                configured_workers = int(
                    os.environ.get("WEIMI_UPLOAD_WORKERS", "12")
                )
            except ValueError:
                configured_workers = 12
            worker_count = min(
                32,
                max(
                    1,
                    min(
                        configured_workers,
                        max(1, total_jobs),
                    ),
                ),
            )
            state_lock = threading.Lock()
            completed_count = {"value": 0}

            def process(
                job: tuple[dict, dict, int],
            ) -> tuple[str, dict, dict, int, str, str]:
                task, item, task_index = job
                store_id = int(task.get("store_id") or 0)
                store = stores[store_id]
                if self._paused:
                    return (
                        "paused",
                        task,
                        item,
                        task_index,
                        "",
                        "等待继续",
                    )
                try:
                    template = task.get("template") or {}
                    listing_mode = _normalize_listing_mode(template)
                    payload = self._upload_payload(item, template)
                    if payload.get("_auto_category_required"):
                        payload.pop("_auto_category_required", None)
                        payload = self._auto_match_category(
                            store,
                            payload,
                            template,
                        )
                    category_apply_ids = payload.pop(
                        "category_apply_ids",
                        [],
                    )
                    payload.pop("category_name", None)
                    payload.pop("category_hint", None)
                    payload.pop("auto_category", None)
                except TemplateRuleSkip as exc:
                    return (
                        "skipped",
                        task,
                        item,
                        task_index,
                        "",
                        str(exc),
                    )
                except Exception as exc:
                    return (
                        "failed",
                        task,
                        item,
                        task_index,
                        "",
                        str(exc)[:500],
                    )
                with self._task_lock:
                    item["status"] = "submitting"
                    item["submission_attempt_id"] = uuid.uuid4().hex
                    item["submission_started_at"] = datetime.now().isoformat(
                        timespec="seconds"
                    )
                    item["message"] = "正在提交，结果尚未确认"
                try:
                    self._save_tasks()
                except Exception as exc:
                    with self._task_lock:
                        item["status"] = "failed"
                        item["message"] = (
                            "本地无法保存提交状态，已取消远端提交："
                            + str(exc)[:300]
                        )
                    return (
                        "failed",
                        task,
                        item,
                        task_index,
                        "",
                        item["message"],
                    )
                try:
                    category_recovery_message = ""

                    def submit_product() -> dict[str, Any]:
                        return self.window.service.product_write(
                            store,
                            "add",
                            payload,
                            client=clients[store_id],
                            listing_mode=listing_mode,
                        )

                    if template.get("auto_open_category", True):
                        def update_category_progress(
                            message: str,
                        ) -> None:
                            with self._task_lock:
                                item["message"] = message

                        result = _retry_upload_after_category_open(
                            payload,
                            submit_product,
                            lambda: ShopPrivateClient(self.db, store),
                            progress=update_category_progress,
                            category_ids=(
                                category_apply_ids
                                if isinstance(category_apply_ids, list)
                                else []
                            ),
                        )
                        latest_message = str(item.get("message") or "")
                        if "类目" in latest_message:
                            category_recovery_message = latest_message
                    else:
                        result = submit_product()
                    product_id = str(
                        result.get("product_id")
                        or payload.get("product_id")
                        or ""
                    )
                    if not product_id:
                        return (
                            "verification_pending",
                            task,
                            item,
                            task_index,
                            "",
                            "远端响应缺少商品 ID，请先核实是否已创建",
                        )
                    warehouse_error = str(
                        result.get("warehouse_error") or ""
                    )
                    if warehouse_error:
                        message = (
                            "商品已创建，但转入仓库失败："
                            + warehouse_error[:300]
                        )
                    elif listing_mode == "warehouse":
                        message = "上传成功，已转入仓库"
                    elif listing_mode == "draft":
                        message = "上传成功，已保存草稿箱"
                    else:
                        message = "上传成功，已提交出售中"
                    if category_recovery_message:
                        message += "；" + category_recovery_message
                    return (
                        "success",
                        task,
                        item,
                        task_index,
                        product_id,
                        message,
                    )
                except CategoryApprovalPending as exc:
                    return (
                        "waiting_category",
                        task,
                        item,
                        task_index,
                        "",
                        str(exc),
                    )
                except CategoryAutoOpenError as exc:
                    return (
                        "failed",
                        task,
                        item,
                        task_index,
                        "",
                        "商品未创建：目标类目未开通，"
                        f"自动申请失败：{str(exc)[:400]}",
                    )
                except Exception as exc:
                    rejection = _definitive_upload_failure_message(exc)
                    if rejection:
                        return (
                            "failed",
                            task,
                            item,
                            task_index,
                            "",
                            rejection,
                        )
                    return (
                        "verification_pending",
                        task,
                        item,
                        task_index,
                        "",
                        "远端提交结果未知，请先核实：" + str(exc)[:400],
                    )

            pending: dict[Any, tuple[dict, dict, int]] = {}
            job_index = 0

            def resolve_current_item(
                task: dict[str, Any],
                item: dict[str, Any],
            ) -> tuple[dict[str, Any], dict[str, Any]]:
                task_id = str(task.get("id") or "")
                item_id = str(item.get("item_id") or "")
                with self._task_lock:
                    for current_task in self._tasks:
                        if str(current_task.get("id") or "") != task_id:
                            continue
                        for current_item in current_task.get("items") or []:
                            if (
                                str(current_item.get("item_id") or "")
                                == item_id
                            ):
                                return current_task, current_item
                raise KeyError(
                    f"上传结果找不到任务或商品：{task_id}/{item_id}"
                )

            def submit_available(executor: ThreadPoolExecutor) -> None:
                nonlocal job_index
                while (
                    not self._paused
                    and job_index < total_jobs
                    and len(pending) < worker_count
                ):
                    job = ordered_jobs[job_index]
                    job_index += 1
                    pending[executor.submit(process, job)] = job

            with ThreadPoolExecutor(
                max_workers=worker_count,
                thread_name_prefix="langlang-upload",
            ) as executor:
                submit_available(executor)
                while pending:
                    finished, _ = wait(
                        pending,
                        return_when=FIRST_COMPLETED,
                    )
                    for future in finished:
                        job = pending.get(future)
                        pending.pop(future, None)
                        try:
                            (
                                outcome,
                                task,
                                item,
                                _task_index,
                                product_id,
                                message,
                            ) = future.result()
                        except Exception as exc:
                            if job is not None:
                                failed_task, failed_item, _ = job
                                with self._task_lock:
                                    failed_item["status"] = "verification_pending"
                                    failed_item["message"] = (
                                        "上传线程异常，结果未知，请先核实："
                                        + str(exc)[:300]
                                    )
                                try:
                                    self._save_tasks()
                                except Exception as save_exc:
                                    errors.append(
                                        "保存待核实状态失败："
                                        + str(save_exc)
                                    )
                            errors.append(f"并发任务异常：{exc}")
                            continue
                        if outcome == "paused":
                            with self._task_lock:
                                item["status"] = "paused"
                                item["message"] = "等待继续"
                            continue
                        try:
                            task, item = resolve_current_item(task, item)
                        except KeyError as exc:
                            errors.append(str(exc))
                            continue
                        if outcome == "success":
                            with self._task_lock:
                                item["status"] = "success"
                                item["product_id"] = product_id
                                item["message"] = message
                            try:
                                self._commit_upload_success(
                                    task,
                                    item,
                                    product_id,
                                )
                            except Exception as exc:
                                with self._task_lock:
                                    item["status"] = "verification_pending"
                                    item["message"] = (
                                        "远端已创建成功，但本地结果保存失败，"
                                        "请先核实后再处理："
                                        + str(exc)[:300]
                                    )
                                errors.append(
                                    f"{task.get('store_name')} / "
                                    f"{item.get('title', '')}: "
                                    "本地落盘失败，商品已标记待核实"
                                )
                                diagnostics = getattr(
                                    self.window,
                                    "diagnostics",
                                    None,
                                )
                                if diagnostics is not None:
                                    diagnostics.record(
                                        "upload_local_commit",
                                        "远端成功但本地结果保存失败",
                                        detail={
                                            "task_id": task.get("id"),
                                            "item_id": item.get("item_id"),
                                            "product_id": product_id,
                                            "error": str(exc),
                                        },
                                    )
                            if "失败" in message:
                                errors.append(
                                    f"{task.get('store_name')} / "
                                    f"{item.get('title', '')}: {message}"
                                )
                        elif outcome == "skipped":
                            with self._task_lock:
                                item["status"] = "skipped"
                                item["message"] = message
                            self._save_tasks()
                        elif outcome == "waiting_category":
                            with self._task_lock:
                                item["status"] = "waiting_category"
                                item["message"] = message
                            errors.append(
                                f"{task.get('store_name')} / "
                                f"{item.get('title', '')}: {message}"
                            )
                            self._save_tasks()
                        elif outcome == "verification_pending":
                            with self._task_lock:
                                item["status"] = "verification_pending"
                                item["message"] = message
                            errors.append(
                                f"{task.get('store_name')} / "
                                f"{item.get('title', '')}: {message}"
                            )
                            diagnostics = getattr(
                                self.window,
                                "diagnostics",
                                None,
                            )
                            if diagnostics is not None:
                                diagnostics.record(
                                    "upload_unknown",
                                    message,
                                    detail={
                                        "task_id": task.get("id"),
                                        "item_id": item.get("item_id"),
                                    },
                                )
                            try:
                                self._save_tasks()
                            except Exception as exc:
                                errors.append(
                                    "保存待核实状态失败："
                                    + str(exc)
                                )
                        else:
                            with self._task_lock:
                                item["status"] = "failed"
                                item["message"] = message
                            self._save_tasks()
                            errors.append(
                                f"{task.get('store_name')} / "
                                f"{item.get('title', '')}: {message}"
                            )
                        with state_lock:
                            completed_count["value"] += 1
                            completed = completed_count["value"]
                        if progress:
                            progress(
                                f"并发上传完成 {completed}/{total_jobs}",
                                completed,
                                total_jobs,
                            )
                    submit_available(executor)

            for task in selected:
                if task.get("status") == "failed" and not any(
                    item.get("status") == "success"
                    for item in task.get("items") or []
                ):
                    continue
                success, failed, total = self._task_counts(task)
                skipped = sum(
                    item.get("status") == "skipped"
                    for item in task.get("items") or []
                )
                pending_count = sum(
                    item.get("status")
                    in {
                        "pending",
                        "queued",
                        "running",
                        "submitting",
                        "paused",
                    }
                    for item in task.get("items") or []
                )
                verification_count = sum(
                    item.get("status") == "verification_pending"
                    for item in task.get("items") or []
                )
                category_pending_count = sum(
                    item.get("status") == "waiting_category"
                    for item in task.get("items") or []
                )
                has_warning = any(
                    item.get("status") == "success"
                    and "失败" in str(item.get("message") or "")
                    for item in task.get("items") or []
                )
                if verification_count:
                    task["status"] = "needs_verification"
                elif category_pending_count:
                    task["status"] = "needs_category"
                elif pending_count and self._paused:
                    task["status"] = "paused"
                elif failed or has_warning:
                    task["status"] = "partial"
                else:
                    task["status"] = "completed"
                task["message"] = (
                    f"成功 {success}，失败 {failed}，跳过 {skipped}"
                    + (
                        f"，待核实 {verification_count}"
                        if verification_count
                        else ""
                    )
                    + (
                        f"，待类目生效 {category_pending_count}"
                        if category_pending_count
                        else ""
                    )
                    + (
                        f"，待处理 {pending_count}"
                        if pending_count
                        else ""
                    )
                )
            self._save_tasks()
            return errors

        def done(errors: list[str]) -> None:
            self._running = False
            self._run_requested = False
            self._save_tasks()
            self._render_tasks()
            affected_stores = {
                int(task.get("store_id") or 0)
                for task in selected
                if int(task.get("store_id") or 0)
            }
            self.window.store_data_changed()
            if affected_stores:
                self.window.store_page.refresh_after_upload(
                    affected_stores
                )
            self.window.set_status(
                "上传任务已结束"
                + (f"，问题 {len(errors)} 条" if errors else "")
            )
            self._schedule_category_retry()
            if errors:
                messagebox.showwarning(
                    "部分商品上传失败",
                    "\n".join(errors[:10]),
                    parent=self,
                )

        accepted = self.window.run_task(
            work,
            done,
            lambda error: (
                setattr(self, "_running", False),
                setattr(self, "_run_requested", False),
                messagebox.showerror("上传任务失败", error, parent=self),
            ),
            progress=self._progress,
            log_title="执行商品上传任务",
        )
        if not accepted:
            self._running = False
            self._run_requested = False
            self._paused = False
            self.window.set_status(
                "上传任务未启动：同类上传任务正在执行，其他页面仍可继续操作",
                busy=False,
            )
            self._render_tasks()
            return
        if self._run_requested or self._running:
            self.window.set_status("正在上传商品…", busy=True)

    def _upload_payload(
        self,
        item: dict[str, Any],
        template: dict[str, Any] | None,
    ) -> dict[str, Any]:
        payload = item.get("payload")
        if not isinstance(payload, dict):
            raw = item.get("raw")
            payload = dict(raw) if isinstance(raw, dict) else {}
        payload = json.loads(json.dumps(payload, ensure_ascii=False))
        payload = _normalize_source_payload(payload)
        if not payload.get("title"):
            payload["title"] = str(item.get("title") or "")
        template = template or {}
        payload["listing"] = (
            0
            if _normalize_listing_mode(template) == "draft"
            else 1
        )
        title = str(payload.get("title") or "")
        if template.get("strip_title_brackets"):
            title = re.sub(r"^【[^】]*】\s*", "", title)

        def rule_words(value: Any) -> list[str]:
            return [
                word.strip()
                for word in re.split(r"[\n,，]+", str(value or ""))
                if word.strip()
            ]

        title_words = rule_words(template.get("title_rules"))
        hit_title_word = next(
            (word for word in title_words if word in title),
            "",
        )
        if hit_title_word:
            raise TemplateRuleSkip(
                f"模板标题过滤命中：{hit_title_word}"
            )
        sku_words = rule_words(template.get("sku_rules"))
        raw_skus = payload.get("skus")
        sku_text = json.dumps(raw_skus or [], ensure_ascii=False)
        hit_sku_word = next(
            (word for word in sku_words if word in sku_text),
            "",
        )
        if hit_sku_word:
            raise TemplateRuleSkip(f"模板规格过滤命中：{hit_sku_word}")

        if template.get("cloud_violation"):
            database = getattr(self, "db", None)
            product_id = str(item.get("source_product_id") or "")
            blocked_ids: set[str] = set()
            selected_types = set(
                rule_words(template.get("cloud_violation_types"))
            ) - {"未设置类型时按本机违规商品 ID 过滤"}
            if database is not None:
                try:
                    blocked_ids = database.violation_blacklist_ids()
                except Exception:
                    blocked_ids = set()
            if product_id and product_id in blocked_ids:
                raise TemplateRuleSkip("模板过滤命中本机违规商品")
            if database is not None and selected_types:
                try:
                    records = json.loads(
                        database.get_setting("violation_library", "[]")
                    )
                except (TypeError, ValueError):
                    records = []
                for record in records if isinstance(records, list) else []:
                    if not isinstance(record, dict):
                        continue
                    if str(record.get("product_id") or "") != product_id:
                        continue
                    if str(record.get("reason") or "") in selected_types:
                        raise TemplateRuleSkip(
                            f"模板过滤命中违规类型：{record.get('reason')}"
                        )

        image_words = rule_words(template.get("image_rules"))
        if image_words:
            for key, action_key in (
                ("head_imgs", "head_action"),
                ("detail_imgs", "detail_action"),
            ):
                images = payload.get(key)
                if not isinstance(images, list):
                    continue
                matched = [
                    image
                    for image in images
                    if any(word in str(image) for word in image_words)
                ]
                if not matched:
                    continue
                if template.get(action_key) == "删除违规图片":
                    payload[key] = [
                        image for image in images if image not in matched
                    ]
                else:
                    raise TemplateRuleSkip(
                        f"模板图片过滤命中 {len(matched)} 张图片"
                    )

        replacement_rules = []
        for line in str(
            template.get("title_replace_rules") or ""
        ).splitlines():
            if "=" not in line:
                continue
            old, new = line.split("=", 1)
            old = old.strip()
            new = new.strip()
            if old:
                replacement_rules.append((old, new))
        for old, new in replacement_rules:
            title = title.replace(old, new)

        prefix = str(template.get("title_prefix") or "")
        suffix = str(template.get("title_suffix") or "")
        payload["title"] = f"{prefix}{title}{suffix}".strip()
        template_stock = _stock_int(template.get("stock"))
        item_stock = _stock_int(item.get("stock"))
        stock = (
            template_stock
            if template_stock is not None
            else item_stock
            if item_stock is not None
            else 0
        )
        multiplier = float(template.get("price_multiplier") or 1)
        offset = int(template.get("price_offset") or 0)
        fixed_price = int(template.get("fixed_price") or 0)
        fixed_price_enabled = bool(template.get("fixed_price_enabled"))
        decimal_places = max(
            0, min(4, int(template.get("final_price_decimals", 2) or 0))
        )
        rounding_unit = 10 ** max(0, 2 - decimal_places)
        ladder: list[tuple[int, int, float, int]] = []
        if template.get("ladder_enabled"):
            for line in str(template.get("ladder_rules") or "").splitlines():
                parts = [part.strip() for part in line.split("=")]
                if len(parts) != 4:
                    continue
                try:
                    low, high, ladder_multiplier, ladder_offset = parts
                    ladder.append(
                        (
                            int(round(float(low) * 100)),
                            int(round(float(high) * 100)),
                            float(ladder_multiplier),
                            int(round(float(ladder_offset) * 100)),
                        )
                    )
                except ValueError:
                    continue

        def price_for(base: int) -> int:
            if fixed_price_enabled and fixed_price > 0:
                value = fixed_price
            else:
                value_multiplier = multiplier
                value_offset = offset
                for low, high, tier_multiplier, tier_offset in ladder:
                    if low <= base <= high:
                        value_multiplier = tier_multiplier
                        value_offset = tier_offset
                        break
                value = int(round(base * value_multiplier)) + value_offset
            value = max(0, value)
            return int(round(value / rounding_unit) * rounding_unit)

        skus = payload.get("skus")
        if isinstance(skus, list) and skus:
            for sku in skus:
                if not isinstance(sku, dict):
                    continue
                try:
                    base = int(sku.get("sale_price") or item.get("price") or 0)
                except (TypeError, ValueError):
                    base = int(item.get("price") or 0)
                sku["sale_price"] = price_for(base)
                sku["stock_num"] = stock
            if template.get("low_price_enabled"):
                sale_prices = sorted(
                    int(sku.get("sale_price") or 0)
                    for sku in skus
                    if isinstance(sku, dict)
                )
                low_multiplier = max(
                    1.0,
                    float(template.get("low_price_multiplier") or 2),
                )
                if (
                    len(sale_prices) > 1
                    and sale_prices[0] > 0
                    and sale_prices[1]
                    >= sale_prices[0] * low_multiplier
                ):
                    if template.get("low_price_action") == "删除最低价":
                        lowest = sale_prices[0]
                        payload["skus"] = [
                            sku
                            for sku in skus
                            if int(sku.get("sale_price") or 0) != lowest
                        ]
                    else:
                        raise TemplateRuleSkip("命中低价引流保护")
        elif item.get("price"):
            payload["skus"] = [
                {
                    "sale_price": price_for(int(item.get("price") or 0)),
                    "stock_num": stock,
                }
            ]

        def category_objects(values: Any) -> list[dict[str, str]]:
            output: list[dict[str, str]] = []
            for value in values if isinstance(values, list) else []:
                if isinstance(value, dict):
                    category_id = str(
                        value.get("cat_id")
                        or value.get("catId")
                        or ""
                    ).strip()
                else:
                    category_id = str(value or "").strip()
                if category_id:
                    output.append({"cat_id": category_id})
            return output

        if payload.get("cats"):
            payload["cats"] = category_objects(payload.get("cats"))
        if payload.get("cats_v2"):
            payload["cats_v2"] = category_objects(
                payload.get("cats_v2")
            )

        if (
            not payload.get("cats")
            and not payload.get("cats_v2")
            and not payload.get("category_path")
            and str(template.get("fallback_category") or "").strip()
        ):
            category = str(template["fallback_category"]).strip()
            try:
                parsed_category = json.loads(category)
            except json.JSONDecodeError:
                parsed_category = {"cat_id": category.split("/")[-1].strip()}
            payload["cats"] = (
                parsed_category
                if isinstance(parsed_category, list)
                else [parsed_category]
            )
            payload["cats"] = category_objects(payload["cats"])
        if template.get("enable_custom_attributes"):
            attrs = payload.get("attrs")
            if not isinstance(attrs, list):
                attrs = []
            existing_keys = {
                str(attr.get("attr_key") or "")
                for attr in attrs
                if isinstance(attr, dict)
            }
            for line in str(
                template.get("attribute_rules") or ""
            ).splitlines():
                if "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                if key and key not in existing_keys:
                    attrs.append(
                        {"attr_key": key, "attr_value": value.strip()}
                    )
                    existing_keys.add(key)
            payload["attrs"] = attrs
        payload.setdefault("delivery_type", template.get("delivery_type") or "现货")
        payload.setdefault(
            "delivery_time",
            template.get("delivery_time") or "48小时内发货",
        )
        if payload.get("platform") == "1688":
            desc_info = payload.get("desc_info")
            if isinstance(desc_info, dict):
                images = desc_info.get("imgs")
                if isinstance(images, list):
                    try:
                        template_skip = int(template.get("trim_detail") or 0)
                    except (TypeError, ValueError):
                        template_skip = 0
                    try:
                        source_skip = int(
                            payload.get("detail_skip_first") or 0
                        )
                    except (TypeError, ValueError):
                        source_skip = 0
                    skip_count = max(template_skip, source_skip)
                    if skip_count:
                        desc_info["imgs"] = images[skip_count:]
            payload.pop("detail_skip_first", None)
            payload.pop("detail_images_before_skip", None)
            payload.pop("detail_images_after_skip", None)
        required = ("head_imgs", "skus")
        missing = [key for key in required if not payload.get(key)]
        if not any(
            payload.get(key)
            for key in ("cats", "cats_v2", "category_path")
        ):
            payload["_auto_category_required"] = True
        if missing:
            raise ValueError(
                "数据缺少微信商品必填字段：" + "、".join(missing)
            )
        if payload.get("product_id"):
            payload.pop("product_id", None)
        return payload

    def _auto_match_category(
        self,
        store: Store,
        payload: dict[str, Any],
        template: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            catalog = self._category_catalog_cache.get(store.id)
            if not catalog:
                catalog = ShopPrivateClient(
                    self.db,
                    store,
                ).category_catalog()
                self._category_catalog_cache[store.id] = catalog
        except Exception as exc:
            raise CategoryAutoOpenError(
                f"{store.name} 无法读取微信类目树：{exc}"
            ) from exc
        source_text = " ".join(
            str(payload.get(key) or "")
            for key in ("category_hint", "category_name", "title")
        )
        ranked = rank_category_candidates(
            source_text,
            catalog,
            limit=25,
        )
        if not ranked:
            fallback = str(template.get("fallback_category") or "").strip()
            if fallback:
                try:
                    parsed = json.loads(fallback)
                except json.JSONDecodeError:
                    parsed = {"cat_id": fallback.split("/")[-1].strip()}
                payload["cats"] = (
                    parsed if isinstance(parsed, list) else [parsed]
                )
                return payload
            raise CategoryAutoOpenError(
                f"{store.name} 没有找到匹配类目，请先配置模板兜底类目"
            )
        best = ranked[0]
        if float(best.get("match_score") or 0) < 2:
            raise CategoryAutoOpenError(
                f"{store.name} 的类目匹配可信度不足：{best.get('path', '')}"
            )
        path_ids = [
            str(value)
            for value in best.get("id_path") or []
            if str(value)
        ]
        if not path_ids:
            path_ids = [str(best.get("category_id") or "")]
        # WeChat uses cats_v2 as the authoritative new multi-level tree
        # whenever it is present. Sending legacy three-level cats together
        # causes length conflicts for categories with more than three levels.
        payload["cats_v2"] = [
            {"cat_id": category_id}
            for category_id in path_ids
            if category_id
        ]
        payload.pop("cats", None)
        payload["category_path"] = str(best.get("path") or "")
        payload["category_apply_ids"] = [
            str(best.get("category_id") or "")
        ]
        payload["auto_category"] = {
            "category_id": str(best.get("category_id") or ""),
            "path": str(best.get("path") or ""),
            "score": float(best.get("match_score") or 0),
        }
        return payload

    def _progress(self, message: str, current: int, total: int) -> None:
        self.window.set_status(message, busy=True)

    def _show_task_log(self) -> None:
        selected = [
            self._task_rows[iid]
            for iid in self.task_tree.selection()
            if iid in self._task_rows
        ]
        tasks = selected or self._tasks
        if not tasks:
            messagebox.showinfo(
                "任务日志",
                "当前没有上传任务。",
                parent=self,
            )
            return
        dialog = tk.Toplevel(self)
        dialog.title("上传任务日志")
        dialog.geometry("900x620")
        dialog.transient(self.winfo_toplevel())
        dialog.columnconfigure(0, weight=1)
        dialog.rowconfigure(0, weight=1)
        editor = tk.Text(
            dialog,
            wrap="word",
            bg="#0f1c26",
            fg="#dcebf2",
            insertbackground="white",
            relief="flat",
            padx=14,
            pady=12,
        )
        editor.grid(row=0, column=0, sticky="nsew")
        ttk.Scrollbar(
            dialog, orient="vertical", command=editor.yview
        ).grid(row=0, column=1, sticky="ns")
        editor.configure(yscrollcommand=dialog.winfo_children()[-1].set)
        lines = []
        for task in tasks:
            item_success, item_failed, item_total = self._task_counts(task)
            lines.append(
                f"任务 {task.get('id', '')} · {task.get('store_name', '')} · "
                f"{task.get('template', {}).get('name', '不使用模板')}"
            )
            lines.append(
                f"状态：{task.get('status', '')} · "
                f"成功 {item_success} / 失败 {item_failed} / 共 {item_total}"
            )
            for index, item in enumerate(task.get("items") or [], 1):
                lines.append(
                    f"  {index:03d}. {item.get('title', '')} · "
                    f"{item.get('status', '')} · {item.get('message', '')}"
                )
            lines.append("")
        editor.insert("1.0", "\n".join(lines))
        editor.configure(state="disabled")
        button(
            dialog,
            "关闭",
            dialog.destroy,
            "Primary",
        ).grid(row=1, column=0, columnspan=2, sticky="e", padx=12, pady=10)
        dialog.bind("<Escape>", lambda _event: dialog.destroy())
        dialog.grab_set()

    def _backfill_source_links(self) -> None:
        jobs = [
            (task, item)
            for task in self._tasks
            for item in task.get("items") or []
            if item.get("status") == "success"
            and str(item.get("product_id") or "").strip()
            and item.get("source_link_status") != "linked"
        ]
        if not jobs:
            messagebox.showinfo(
                "上家关联",
                "没有需要补齐关联的已上传商品。",
                parent=self,
            )
            return
        self.window.set_status(
            f"正在补齐 {len(jobs)} 个已上传商品的上家关联…",
            busy=True,
        )

        def work(progress) -> int:
            linked = 0
            for index, (task, item) in enumerate(jobs, 1):
                progress(
                    f"正在补齐上家关联 {index}/{len(jobs)}",
                    index,
                    len(jobs),
                )
                self._commit_upload_success(
                    task,
                    item,
                    str(item.get("product_id") or ""),
                )
                if item.get("source_link_status") == "linked":
                    linked += 1
            self.db.sync_auto_dispatch_tasks()
            return linked

        def done(linked: int) -> None:
            self._render_tasks()
            self.window.set_status(
                f"已补齐 {linked} 个商品的上家关联",
                busy=False,
            )

        self.window.run_task(
            work,
            done,
            lambda error: messagebox.showerror(
                "补齐上家关联失败",
                error,
                parent=self,
            ),
            self._progress,
            log_title="补齐已上传商品的上家关联",
        )

    def _clear_all_tasks(self) -> None:
        if not self._tasks:
            return
        if self._running or self._run_requested:
            messagebox.showwarning(
                "上传任务正在运行",
                "请先点击暂停，并等待当前在途商品结束后再清空任务。",
                parent=self,
            )
            return
        if not messagebox.askyesno(
            "清空全部任务",
            f"确定清空全部 {len(self._tasks)} 个上传任务？"
            "正在执行的任务会停止。",
            parent=self,
        ):
            return
        self._paused = True
        self._tasks = []
        self._save_tasks()
        self._render_tasks()
        self.window.set_status("上传任务已清空")

    def _clear_completed(self) -> None:
        if self._running or self._run_requested:
            messagebox.showwarning(
                "上传任务正在运行",
                "请先暂停并等待当前商品结束，再清理已完成任务。",
                parent=self,
            )
            return
        self._tasks = [
            task
            for task in self._tasks
            if task.get("status") != "completed"
        ]
        self._save_tasks()
        self._render_tasks()

    def _delete_tasks(self) -> None:
        if self._running or self._run_requested:
            messagebox.showwarning(
                "上传任务正在运行",
                "请先暂停并等待当前商品结束，再删除任务。",
                parent=self,
            )
            return
        selected = [
            self._task_rows[iid]
            for iid in self.task_tree.selection()
            if iid in self._task_rows
        ]
        if not selected:
            return
        if not messagebox.askyesno(
            "删除上传任务",
            f"确定删除选中的 {len(selected)} 个上传任务及其本地快照吗？",
            parent=self,
        ):
            return
        selected_ids = {task.get("id") for task in selected}
        self._tasks = [
            task
            for task in self._tasks
            if task.get("id") not in selected_ids
        ]
        self._save_tasks()
        self._render_tasks()
