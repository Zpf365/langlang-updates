from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any, Mapping


class RulePackValidationError(ValueError):
    """Raised when a signed JSON rule pack fails its business schema."""


def _common(payload: Any) -> Mapping[str, Any]:
    if not isinstance(payload, Mapping):
        raise RulePackValidationError("规则包必须是 JSON 对象")
    if int(payload.get("schema_version") or 0) != 1:
        raise RulePackValidationError("规则包 schema_version 不受支持")
    return payload


def _string_list(payload: Any, key: str) -> list[str]:
    data = _common(payload)
    values = data.get(key)
    if not isinstance(values, list) or not values:
        raise RulePackValidationError(f"{key} 必须是非空列表")
    result: list[str] = []
    seen: set[str] = set()
    for item in values:
        if not isinstance(item, str) or not item.strip():
            raise RulePackValidationError(f"{key} 只能包含非空字符串")
        value = item.strip()
        folded = value.casefold()
        if folded in seen:
            raise RulePackValidationError(f"{key} 包含重复值")
        seen.add(folded)
        result.append(value)
    return result


def _validate_platform_keywords(payload: Any) -> None:
    _string_list(payload, "keywords")


def _validate_upload_attributes(payload: Any) -> None:
    _string_list(payload, "values")


def _validate_display_thresholds(payload: Any) -> None:
    data = _common(payload)
    thresholds = data.get("thresholds")
    if not isinstance(thresholds, Mapping) or not thresholds:
        raise RulePackValidationError("thresholds 必须是非空对象")
    for key, value in thresholds.items():
        if not isinstance(key, str) or not key.strip():
            raise RulePackValidationError("阈值名称必须是非空字符串")
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            or value < 0
        ):
            raise RulePackValidationError("阈值必须是有限非负数值")


rule_validators: dict[str, Callable[[Any], None]] = {
    "display_thresholds": _validate_display_thresholds,
    "platform_keywords": _validate_platform_keywords,
    "upload_attributes": _validate_upload_attributes,
}


def validate_rule_payload(name: str, payload: Any) -> None:
    validator = rule_validators.get(str(name))
    if validator is None:
        raise RulePackValidationError(f"未注册规则类型：{name}")
    validator(payload)
