from __future__ import annotations

from weimi_manager.ui.upload_page import UploadPage
from weimi_manager.upload_violations import apply_template_title_rules


def _payload(title: str, template: dict) -> dict:
    return UploadPage._upload_payload(
        None,
        {
            "source_product_id": "TITLE-REPLACE-1",
            "title": title,
            "price": 1000,
            "payload": {
                "title": title,
                "platform": "淘宝",
                "cats": [{"cat_id": "10000111"}],
                "head_imgs": ["https://example.test/main.jpg"],
                "skus": [{"sale_price": 1000, "stock_num": 8}],
            },
        },
        template,
    )


def test_title_replacements_match_preflight_and_upload_payload() -> None:
    title = "【促销】2004款 2004系列 2000纪念版"
    template = {
        "strip_title_brackets": True,
        "title_replace_rules": "2004=2025\n2000=2025",
        "title_prefix": "新品·",
        "title_suffix": " 现货",
    }

    expected = "新品·2025款 2025系列 2025纪念版 现货"

    assert apply_template_title_rules(title, template) == expected
    assert _payload(title, template)["title"] == expected


def test_title_replacements_run_in_order_and_support_empty_deletion() -> None:
    template = {
        "title_replace_rules": "2000=2004\n2004=2025\n旧款=",
    }

    expected = "2025款 2025系列 棉柔巾"
    original = "2000款 2004系列 旧款棉柔巾"

    assert apply_template_title_rules(original, template) == expected
    assert _payload(original, template)["title"] == expected


def test_title_replacement_keeps_equals_signs_in_new_text() -> None:
    template = {"title_replace_rules": "A01=AB=CD"}

    expected = "款号AB=CD"
    original = "款号A01"

    assert apply_template_title_rules(original, template) == expected
    assert _payload(original, template)["title"] == expected


def test_title_replacement_keeps_dollar_patterns_as_literal_text() -> None:
    for replacement in ("AB$&CD", "AB$$CD", "AB$`CD", "AB$'CD"):
        with_substitutions = {"title_replace_rules": f"A01={replacement}"}
        expected = f"前{replacement}后"

        assert apply_template_title_rules("前A01后", with_substitutions) == expected
        assert _payload("前A01后", with_substitutions)["title"] == expected
