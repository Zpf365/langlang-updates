from __future__ import annotations

import io
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Callable, Sequence

from .models import UploadPreflightError


@dataclass(frozen=True)
class ThirdPartyPlatformRule:
    key: str
    names: tuple[str, ...]
    domains: tuple[str, ...]


@dataclass(frozen=True)
class ContentSafetyResult:
    payload: dict[str, Any]
    hits: tuple[dict[str, str], ...] = ()
    removed_images: int = 0


DEFAULT_PLATFORM_RULES: tuple[ThirdPartyPlatformRule, ...] = (
    ThirdPartyPlatformRule(
        "pinduoduo",
        ("拼多多", "PDD", "Pinduoduo"),
        ("pinduoduo.com", "yangkeduo.com", "pdd.com"),
    ),
    ThirdPartyPlatformRule(
        "taobao",
        ("淘宝", "淘寶", "天猫", "天貓", "Taobao", "Tmall"),
        ("taobao.com", "tmall.com", "taobao.hk"),
    ),
    ThirdPartyPlatformRule(
        "1688",
        ("1688", "阿里巴巴", "Alibaba"),
        ("1688.com", "alibaba.com"),
    ),
    ThirdPartyPlatformRule(
        "aliexpress",
        ("速卖通", "AliExpress"),
        ("aliexpress.com",),
    ),
    ThirdPartyPlatformRule(
        "amazon",
        ("亚马逊", "Amazon"),
        ("amazon.com", "amazon.cn", "amazon.co.uk"),
    ),
    ThirdPartyPlatformRule(
        "ebay",
        ("eBay",),
        ("ebay.com", "ebay.cn"),
    ),
    ThirdPartyPlatformRule(
        "wish",
        ("Wish",),
        ("wish.com",),
    ),
    ThirdPartyPlatformRule(
        "lazada",
        ("Lazada",),
        ("lazada.com", "lazada.sg"),
    ),
    ThirdPartyPlatformRule(
        "jd",
        ("京东", "京東"),
        ("jd.com", "jd.hk"),
    ),
    ThirdPartyPlatformRule(
        "douyin",
        ("抖音", "抖店", "Douyin"),
        ("douyin.com", "jinritemai.com"),
    ),
    ThirdPartyPlatformRule(
        "kuaishou",
        ("快手", "快手小店", "Kuaishou"),
        ("kuaishou.com",),
    ),
    ThirdPartyPlatformRule(
        "xiaohongshu",
        ("小红书", "小紅書", "Xiaohongshu"),
        ("xiaohongshu.com", "xhslink.com"),
    ),
    ThirdPartyPlatformRule(
        "shein",
        ("Shein",),
        ("shein.com",),
    ),
)

_LEAD_TERMS = (
    "同款",
    "货源",
    "批发",
    "下单",
    "购买",
    "联系",
    "店铺",
    "官方",
    "旗舰店",
    "链接",
    "扫码",
    "客服",
    "buy",
    "order",
    "source",
    "wholesale",
    "contact",
    "shop",
)
_URL_RE = re.compile(
    r"(?i)\b(?:https?://|www\.)[^\s<>'\"]+"
)
_EDGE_PUNCTUATION_RE = re.compile(
    r"^[\s,，。;；:：、|/\\·\-]+|[\s,，。;；:：、|/\\·\-]+$"
)
_RESIDUAL_LEAD_RE = re.compile(
    r"(?i)(?:同款|货源|(?:点击|立即|联系|扫码|复制)\s*购买|"
    r"(?:联系|扫码|私信|客服)\s*下单|联系客服|客服微信)"
)
_REPEATED_PUNCTUATION_RE = re.compile(
    r"[\s,，;；:：、|/\\·\-]*[，。；、][\s,，。;；:：、|/\\·\-]*"
)
_PRESERVED_TEXT_PUNCTUATION = frozenset("，。；：！？、（）【】《》…—")


def _normalize_text(value: Any) -> str:
    return "".join(
        character
        if character in _PRESERVED_TEXT_PUNCTUATION
        else unicodedata.normalize("NFKC", character)
        for character in str(value or "")
    )


def _flexible_literal(value: str) -> str:
    characters = [
        re.escape(character)
        for character in _normalize_text(value)
        if not character.isspace()
    ]
    pattern = r"\s*".join(characters)
    normalized = _normalize_text(value)
    prefix = r"(?<![A-Za-z0-9])" if normalized[:1].isascii() and normalized[:1].isalnum() else ""
    suffix = r"(?![A-Za-z0-9])" if normalized[-1:].isascii() and normalized[-1:].isalnum() else ""
    return prefix + pattern + suffix


def _platform_alias_pattern(
    rules: Sequence[ThirdPartyPlatformRule],
) -> str:
    aliases = sorted(
        {
            name
            for rule in rules
            for name in rule.names
            if str(name).strip()
        },
        key=len,
        reverse=True,
    )
    return "(?:" + "|".join(
        _flexible_literal(alias)
        for alias in aliases
    ) + ")"


def _domain_patterns(
    rules: Sequence[ThirdPartyPlatformRule],
) -> list[tuple[str, re.Pattern[str]]]:
    patterns: list[tuple[str, re.Pattern[str]]] = []
    for rule in rules:
        for domain in rule.domains:
            patterns.append(
                (
                    f"platform_domain.{rule.key}",
                    re.compile(
                        rf"(?i)(?<![A-Za-z0-9-])"
                        rf"(?:[A-Za-z0-9-]+\.)*(?:www\.)?"
                        rf"{re.escape(domain)}(?![A-Za-z0-9.-])"
                        rf"(?:/[^\s<>'\"]*)?"
                    ),
                )
            )
    return patterns


def _clean_text(
    value: Any,
    *,
    field: str,
    alias_pattern: str,
    domains: Sequence[tuple[str, re.Pattern[str]]],
    hits: list[dict[str, str]],
) -> str:
    text = _normalize_text(value)
    if not text:
        return ""
    original = text
    alias_ref = rf"(?:{alias_pattern})"
    lead = "(?:" + "|".join(
        re.escape(_normalize_text(term))
        for term in _LEAD_TERMS
    ) + ")"
    after_reference = re.compile(
        rf"(?i){alias_ref}(?:\s*(?:{lead}))*",
    )
    before_reference = re.compile(
        rf"(?i)(?:{lead}\s*(?:on\s+)?)*(?:{lead}\s*)?{alias_ref}"
        rf"(?:\s*(?:{lead}))*",
    )
    text, url_count = _URL_RE.subn(" ", text)
    if url_count:
        hits.append(
            {
                "field": field,
                "rule": "external_url",
                "action": "removed",
            }
        )
    for rule_name, pattern in domains:
        text, count = pattern.subn(" ", text)
        if count:
            hits.append(
                {
                    "field": field,
                    "rule": rule_name,
                    "action": "removed",
                }
            )
    text, before_count = before_reference.subn(" ", text)
    text, after_count = after_reference.subn(" ", text)
    if after_count + before_count:
        hits.append(
            {
                "field": field,
                "rule": "third_party_platform_phrase",
                "action": "removed",
            }
        )
    text, residual_count = _RESIDUAL_LEAD_RE.subn(" ", text)
    if residual_count:
        hits.append(
            {
                "field": field,
                "rule": "third_party_lead_phrase",
                "action": "removed",
            }
        )
    remaining_pattern = re.compile(rf"(?i){alias_ref}")
    text, remaining_count = remaining_pattern.subn(" ", text)
    if remaining_count:
        hits.append(
            {
                "field": field,
                "rule": "third_party_platform_name",
                "action": "removed",
            }
        )
    text = re.sub(r"[ \t]+", " ", text)
    text = _REPEATED_PUNCTUATION_RE.sub("，", text)
    text = _EDGE_PUNCTUATION_RE.sub("", text).strip()
    if text == original:
        return original
    return text


def _delete_empty_attribute(
    item: dict[str, Any],
    *,
    field: str,
    required_keys: set[str],
    hits: list[dict[str, str]],
    alias_pattern: str,
    domains: Sequence[tuple[str, re.Pattern[str]]],
) -> bool:
    hit_count = len(hits)
    original_key = str(item.get("attr_key") or "").strip()
    original_value = item.get("attr_value")
    clean_key = _clean_text(
        original_key,
        field=f"{field}.key",
        alias_pattern=alias_pattern,
        domains=domains,
        hits=hits,
    )
    clean_value = (
        _clean_text(
            original_value,
            field=f"{field}.value",
            alias_pattern=alias_pattern,
            domains=domains,
            hits=hits,
        )
        if isinstance(original_value, str)
        else original_value
    )
    required = _normalized_attribute_key(original_key) in required_keys
    if required and (not clean_key or clean_value in (None, "")):
        raise UploadPreflightError(
            f"平台必填属性清理后为空：{original_key}"
        )
    if not clean_key or clean_value in (None, ""):
        return False
    if not required and len(hits) > hit_count:
        return False
    item["attr_key"] = clean_key
    item["attr_value"] = clean_value
    return True


def _normalized_attribute_key(value: Any) -> str:
    return re.sub(
        r"[\s\-_—:：/\\|（）()\[\]【】.,，。·]+",
        "",
        _normalize_text(value).casefold(),
    )


def _sanitize_attribute_list(
    attrs: Any,
    *,
    field: str,
    required_keys: set[str],
    hits: list[dict[str, str]],
    alias_pattern: str,
    domains: Sequence[tuple[str, re.Pattern[str]]],
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for index, item in enumerate(attrs or []):
        if not isinstance(item, dict):
            continue
        current = dict(item)
        if _delete_empty_attribute(
            current,
            field=f"{field}[{index}]",
            required_keys=required_keys,
            hits=hits,
            alias_pattern=alias_pattern,
            domains=domains,
        ):
            output.append(current)
    return output


def _sanitize_image_list(
    values: Any,
    *,
    field: str,
    image_inspector: Callable[[str], Sequence[str]] | None,
    alias_pattern: str,
    domains: Sequence[tuple[str, re.Pattern[str]]],
    hits: list[dict[str, str]],
) -> tuple[list[str], int]:
    output: list[str] = []
    removed = 0
    for index, value in enumerate(values or []):
        source = str(value or "").strip()
        if not source:
            continue
        source_alias = re.compile(rf"(?i){alias_pattern}")
        source_domain = next(
            (
                rule_name
                for rule_name, pattern in domains
                if pattern.search(source)
            ),
            "",
        )
        if source_alias.search(source) or source_domain:
            removed += 1
            hits.append(
                {
                    "field": f"{field}[{index}]",
                    "rule": (
                        source_domain
                        or "image.third_party_source"
                    ),
                    "action": "image_removed",
                }
            )
            continue
        if image_inspector is not None:
            try:
                risk_rules = tuple(image_inspector(source))
            except UploadPreflightError:
                raise
            except Exception as exc:
                raise UploadPreflightError(
                    f"图片无法完成安全检测：{str(exc)[:200]}"
                ) from exc
            if risk_rules:
                removed += 1
                hits.append(
                    {
                        "field": f"{field}[{index}]",
                        "rule": ",".join(
                            sorted(set(str(rule) for rule in risk_rules))
                        )[:160],
                        "action": "image_removed",
                    }
                )
                continue
        output.append(source)
    return output, removed


def sanitize_upload_content(
    payload: dict[str, Any],
    *,
    required_attribute_keys: Sequence[str] = (),
    image_inspector: Callable[[str], Sequence[str]] | None = None,
    platform_rules: Sequence[ThirdPartyPlatformRule] = DEFAULT_PLATFORM_RULES,
) -> ContentSafetyResult:
    safe = dict(payload)
    safe["desc_info"] = dict(payload.get("desc_info") or {})
    safe["skus"] = [
        dict(item)
        for item in payload.get("skus") or []
        if isinstance(item, dict)
    ]
    hits: list[dict[str, str]] = []
    required_keys = {
        _normalized_attribute_key(value)
        for value in required_attribute_keys or ()
        if str(value).strip()
    }
    alias_pattern = _platform_alias_pattern(platform_rules)
    domains = _domain_patterns(platform_rules)

    for field in ("title", "short_title"):
        original = str(safe.get(field) or "")
        cleaned = _clean_text(
            original,
            field=field,
            alias_pattern=alias_pattern,
            domains=domains,
            hits=hits,
        )
        if original.strip() and not cleaned:
            label = "商品标题" if field == "title" else "商品短标题"
            raise UploadPreflightError(
                f"{label}清理第三方平台信息后为空"
            )
        safe[field] = cleaned

    desc_info = safe["desc_info"]
    original_desc = str(desc_info.get("desc") or "")
    desc_info["desc"] = _clean_text(
        original_desc,
        field="desc_info.desc",
        alias_pattern=alias_pattern,
        domains=domains,
        hits=hits,
    )

    for key in ("attrs", "attributes"):
        if isinstance(safe.get(key), list):
            safe[key] = _sanitize_attribute_list(
                safe[key],
                field=key,
                required_keys=required_keys,
                hits=hits,
                alias_pattern=alias_pattern,
                domains=domains,
            )
        elif isinstance(safe.get(key), dict):
            rows = [
                {"attr_key": attr_key, "attr_value": attr_value}
                for attr_key, attr_value in safe[key].items()
            ]
            safe[key] = {
                item["attr_key"]: item["attr_value"]
                for item in _sanitize_attribute_list(
                    rows,
                    field=key,
                    required_keys=required_keys,
                    hits=hits,
                    alias_pattern=alias_pattern,
                    domains=domains,
                )
            }

    for sku_index, sku in enumerate(safe["skus"]):
        sku_attrs = sku.get("sku_attrs")
        if isinstance(sku_attrs, list):
            had_sku_attrs = bool(sku_attrs)
            sku["sku_attrs"] = _sanitize_attribute_list(
                sku_attrs,
                field=f"skus[{sku_index}].sku_attrs",
                required_keys=set(),
                hits=hits,
                alias_pattern=alias_pattern,
                domains=domains,
            )
            if had_sku_attrs and not sku["sku_attrs"]:
                raise UploadPreflightError(
                    "SKU 规格清理后为空，"
                    f"商品 {sku.get('out_sku_id') or sku_index + 1}"
                )
    removed_images = 0
    head_imgs, removed = _sanitize_image_list(
        safe.get("head_imgs"),
        field="head_imgs",
        image_inspector=image_inspector,
        alias_pattern=alias_pattern,
        domains=domains,
        hits=hits,
    )
    safe["head_imgs"] = head_imgs
    removed_images += removed
    detail_imgs, removed = _sanitize_image_list(
        desc_info.get("imgs"),
        field="desc_info.imgs",
        image_inspector=image_inspector,
        alias_pattern=alias_pattern,
        domains=domains,
        hits=hits,
    )
    desc_info["imgs"] = detail_imgs
    removed_images += removed
    for sku_index, sku in enumerate(safe["skus"]):
        for key in ("thumb_url", "image", "image_url", "sku_image"):
            value = sku.get(key)
            if not isinstance(value, str) or not value:
                continue
            values, removed = _sanitize_image_list(
                [value],
                field=f"skus[{sku_index}].{key}",
                image_inspector=image_inspector,
                alias_pattern=alias_pattern,
                domains=domains,
                hits=hits,
            )
            removed_images += removed
            sku[key] = values[0] if values else ""

    return ContentSafetyResult(
        payload=safe,
        hits=tuple(hits),
        removed_images=removed_images,
    )


def _load_ocr():
    from rapidocr_onnxruntime import RapidOCR

    return RapidOCR()


_OCR_READER: Any | None = None


def inspect_image_bytes(
    data: bytes,
    *,
    qr_detector: Any | None = None,
    ocr_reader: Any | None = None,
) -> tuple[str, ...]:
    try:
        import cv2
        import numpy as np
        from PIL import Image
    except ImportError as exc:
        raise UploadPreflightError(
            "本机图片安全检测依赖不可用"
        ) from exc
    try:
        with Image.open(io.BytesIO(data)) as image:
            rgb = image.convert("RGB")
            array = np.asarray(rgb)
    except Exception as exc:
        raise UploadPreflightError(
            "商品图片格式无法解析"
        ) from exc
    if array.size == 0:
        raise UploadPreflightError("商品图片内容为空")
    risks: list[str] = []
    try:
        detector = qr_detector or cv2.QRCodeDetector()
        qr_input = cv2.cvtColor(array, cv2.COLOR_RGB2GRAY)
        qr_result = detector.detectAndDecodeMulti(qr_input)
        if len(qr_result) >= 4:
            _qr_detected, qr_values = qr_result[:2]
        else:
            qr_values = qr_result[0]
        if any(str(value or "").strip() for value in qr_values or ()):
            risks.append("image.qr_code")
        elif detector.detect(qr_input)[0]:
            risks.append("image.qr_code")
    except Exception as exc:
        raise UploadPreflightError(
            "无法完成二维码安全检测"
        ) from exc
    global _OCR_READER
    reader = ocr_reader
    if reader is None:
        if _OCR_READER is None:
            _OCR_READER = _load_ocr()
        reader = _OCR_READER
    try:
        raw_lines, _elapsed = reader(array)
    except Exception as exc:
        raise UploadPreflightError(
            "无法完成图片文字安全检测"
        ) from exc
    ocr_text = " ".join(
        str(item[1] if isinstance(item, (list, tuple)) and len(item) > 1 else item)
        for item in raw_lines or ()
    )
    if ocr_text:
        image_text_hits: list[dict[str, str]] = []
        _clean_text(
            ocr_text,
            field="image.ocr_text",
            alias_pattern=_platform_alias_pattern(
                DEFAULT_PLATFORM_RULES
            ),
            domains=_domain_patterns(DEFAULT_PLATFORM_RULES),
            hits=image_text_hits,
        )
        if image_text_hits:
            risks.append("image.third_party_text")
    return tuple(dict.fromkeys(risks))


def load_image_source_bytes(
    source: str,
    *,
    timeout: int = 30,
    max_bytes: int = 12 * 1024 * 1024,
) -> bytes:
    text = str(source or "").strip()
    if not text:
        return b""
    if text.startswith(("http://", "https://")):
        import requests

        session = requests.Session()
        session.trust_env = False
        try:
            response = session.get(
                text,
                timeout=timeout,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 Chrome/124 Safari/537.36"
                    ),
                    "Accept": "image/*,*/*;q=0.8",
                },
            )
            response.raise_for_status()
            data = response.content
        except requests.RequestException as exc:
            raise UploadPreflightError(
                f"商品图片无法读取：{str(exc)[:200]}"
            ) from exc
    else:
        try:
            data = open(text, "rb").read(max_bytes + 1)
        except OSError as exc:
            raise UploadPreflightError(
                f"商品图片无法读取：{str(exc)[:200]}"
            ) from exc
    if not data:
        raise UploadPreflightError("商品图片内容为空")
    if len(data) > max_bytes:
        raise UploadPreflightError(
            "商品图片超过安全检测大小上限"
        )
    return data


def inspect_image_source(
    source: str,
    *,
    timeout: int = 30,
    max_bytes: int = 12 * 1024 * 1024,
) -> tuple[str, ...]:
    data = load_image_source_bytes(
        source,
        timeout=timeout,
        max_bytes=max_bytes,
    )
    return inspect_image_bytes(data) if data else ()
