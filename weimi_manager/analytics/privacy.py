from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urlparse


FORBIDDEN_KEY_PARTS = (
    "secret",
    "password",
    "passwd",
    "cookie",
    "token",
    "authorization",
    "credential",
    "session",
    "payment",
    "pay_password",
    "card_no",
    "cvv",
    "buyer_name",
    "receiver",
    "phone",
    "mobile",
    "tel_number",
    "address",
    "merchant_notes",
    "buyer_message",
    "chat",
    "raw_json",
    "raw_payload",
    "detail_image",
    "sku_image",
    "image_base64",
    "binary",
)
THUMBNAIL_HOSTS = (
    "alicdn.com",
    "1688.com",
    "taobao.com",
    "tmall.com",
    "qpic.cn",
    "weixin.qq.com",
    "wx.qq.com",
)
BASE64_PATTERN = re.compile(
    r"^data:[^;,]+;base64,",
    re.I,
)


def is_forbidden_key(key: str) -> bool:
    normalized = str(key or "").casefold()
    return any(part in normalized for part in FORBIDDEN_KEY_PARTS)


def trusted_thumbnail_url(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    parsed = urlparse(text)
    if parsed.scheme != "https" or not parsed.hostname:
        return ""
    host = parsed.hostname.casefold().rstrip(".")
    if not any(
        host == allowed or host.endswith("." + allowed)
        for allowed in THUMBNAIL_HOSTS
    ):
        return ""
    if parsed.username or parsed.password:
        return ""
    return text[:2048]


def thumbnail_host(value: str) -> str:
    parsed = urlparse(str(value or ""))
    return (parsed.hostname or "").casefold()


def _sanitize_value(
    value: Any,
    *,
    depth: int,
) -> Any:
    if depth > 4:
        return None
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, bytes):
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text or BASE64_PATTERN.match(text):
            return ""
        return text[:1000]
    if isinstance(value, list):
        output = []
        for item in value[:25]:
            clean = _sanitize_value(item, depth=depth + 1)
            if clean is not None:
                output.append(clean)
        return output
    if isinstance(value, dict):
        output: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if is_forbidden_key(key_text):
                continue
            clean = _sanitize_value(item, depth=depth + 1)
            if clean is not None:
                output[key_text[:80]] = clean
        return output
    return None


def sanitize_payload(
    payload: dict[str, Any] | None,
    *,
    allowed_fields: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    source = payload if isinstance(payload, dict) else {}
    if allowed_fields is not None:
        source = {
            key: source.get(key)
            for key in allowed_fields
            if key in source
        }
    clean = _sanitize_value(source, depth=0)
    if not isinstance(clean, dict):
        return {}
    thumbnail = trusted_thumbnail_url(clean.get("thumbnail_url"))
    if thumbnail:
        clean["thumbnail_url"] = thumbnail
        clean["thumbnail_host"] = thumbnail_host(thumbnail)
    else:
        clean.pop("thumbnail_url", None)
        clean.pop("thumbnail_host", None)
    return clean


def payload_size(payload: dict[str, Any]) -> int:
    return len(
        json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    )
