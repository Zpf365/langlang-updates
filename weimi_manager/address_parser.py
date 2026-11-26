from __future__ import annotations

import re
from typing import Any


MOBILE_PATTERN = re.compile(r"(?<!\d)(1[3-9]\d{9})(?!\d)")
PHONE_PATTERN = re.compile(
    r"(?<!\d)(1[3-9]\d{9}(?:[-\s转]\d{1,8})?)(?!\d)"
)
PROVINCE_PATTERN = re.compile(
    r"(北京市|上海市|天津市|重庆市|"
    r"[\u4e00-\u9fff]{2,12}(?:省|自治区))"
)
CITY_PATTERN = re.compile(
    r"([\u4e00-\u9fff]{2,16}(?:市|自治州|地区|盟))"
)
COUNTY_PATTERN = re.compile(
    r"([\u4e00-\u9fff]{1,16}(?:区|县|旗|市))"
)


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip(" ,，;；")


def _labelled(text: str, labels: tuple[str, ...]) -> str:
    names = "|".join(re.escape(label) for label in labels)
    match = re.search(
        rf"(?:{names})\s*[:：]\s*([^\n]+)",
        text,
    )
    return _clean(match.group(1)) if match else ""


def parse_address_text(text: str) -> dict[str, Any]:
    raw_text = str(text or "").strip()
    compact = re.sub(r"[\r\n\t]+", " ", raw_text)
    phone_match = PHONE_PATTERN.search(compact)
    phone = phone_match.group(1) if phone_match else ""
    name = _labelled(raw_text, ("收货人", "联系人", "姓名"))
    if name:
        name = re.split(r"\s|,|，|;|；", name, maxsplit=1)[0]
    if not name:
        prefix = compact[: phone_match.start()] if phone_match else compact
        prefix = re.sub(
            r"(?:收货人|联系人|姓名|电话|手机号?)\s*[:：]?",
            " ",
            prefix,
        )
        tokens = [
            token
            for token in re.split(r"\s|,|，|;|；", prefix)
            if token.strip()
        ]
        if tokens:
            name = tokens[-1]

    address_text = _labelled(raw_text, ("收货地址", "详细地址", "地址"))
    if address_text:
        address_text = re.sub(r"^[\s:：]+", "", address_text)
    elif phone_match:
        address_text = compact[phone_match.end() :]
    else:
        address_text = compact
    address_text = re.sub(
        r"(?:收货地址|详细地址|地址|省市区)\s*[:：]?",
        " ",
        address_text,
    )
    address_text = _clean(address_text)

    province = ""
    city = ""
    county = ""
    detail = ""
    province_match = PROVINCE_PATTERN.search(address_text)
    if province_match:
        province = province_match.group(1)
        remaining = address_text[province_match.end() :].strip()
    else:
        remaining = address_text

    municipalities = {"北京市", "上海市", "天津市", "重庆市"}
    if province in municipalities:
        city = province
    else:
        city_match = CITY_PATTERN.search(remaining)
        if city_match:
            city = city_match.group(1)
            remaining = remaining[city_match.end() :].strip()

    county_match = COUNTY_PATTERN.search(remaining)
    if county_match:
        county = county_match.group(1)
        detail = remaining[county_match.end() :].strip()
    else:
        detail = remaining.strip()

    address = {
        "user_name": _clean(name),
        "tel_number": phone,
        "province_name": province,
        "city_name": city,
        "county_name": county,
        "street_name": "",
        "detail_info": _clean(detail),
    }
    required = (
        "user_name",
        "tel_number",
        "province_name",
        "city_name",
        "county_name",
        "detail_info",
    )
    missing = [
        key for key in required if not address[key]
    ]
    if phone and not PHONE_PATTERN.fullmatch(phone):
        missing.append("tel_number")
    labels = {
        "user_name": "收货人",
        "tel_number": "手机号",
        "province_name": "省份",
        "city_name": "城市",
        "county_name": "区县",
        "detail_info": "详细地址",
    }
    reason = (
        ""
        if not missing
        else "地址不完整或无法识别："
        + "、".join(
            labels.get(key, key)
            for key in dict.fromkeys(missing)
        )
    )
    return {
        "address": address,
        "missing": list(dict.fromkeys(missing)),
        "valid": not missing,
        "reason": reason,
        "raw_text": raw_text,
    }


def address_is_complete(address: dict[str, Any] | None) -> bool:
    if not isinstance(address, dict):
        return False
    values = [
        str(address.get(key) or "").strip()
        for key in (
            "user_name",
            "tel_number",
            "province_name",
            "city_name",
            "county_name",
            "detail_info",
        )
    ]
    if any(not value for value in values):
        return False
    if not PHONE_PATTERN.fullmatch(values[1]):
        return False
    if any(
        marker in value
        for value in values
        for marker in ("*", "***", "加密", "ciphertext")
    ):
        return False
    return True
