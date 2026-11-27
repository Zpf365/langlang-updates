from __future__ import annotations

import pytest

from weimi_manager.update_rules import (
    RulePackValidationError,
    rule_validators,
    validate_rule_payload,
)


def test_display_threshold_rules_reject_non_numeric_threshold() -> None:
    with pytest.raises(RulePackValidationError, match="数值"):
        validate_rule_payload(
            "display_thresholds",
            {
                "schema_version": 1,
                "thresholds": {"warning": "100"},
            },
        )


def test_platform_keywords_reject_duplicates() -> None:
    with pytest.raises(RulePackValidationError, match="重复"):
        validate_rule_payload(
            "platform_keywords",
            {
                "schema_version": 1,
                "keywords": ["商品", "商品"],
            },
        )


def test_registered_rule_validators_cover_supported_pack_types() -> None:
    assert set(rule_validators) == {
        "display_thresholds",
        "platform_keywords",
        "upload_attributes",
    }


def test_upload_attribute_rules_accept_only_nonempty_strings() -> None:
    with pytest.raises(RulePackValidationError, match="字符串"):
        validate_rule_payload(
            "upload_attributes",
            {
                "schema_version": 1,
                "values": ["品牌", 3],
            },
        )
