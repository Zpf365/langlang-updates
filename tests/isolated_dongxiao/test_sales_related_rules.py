from __future__ import annotations

from decimal import Decimal

import pytest

from weimi_manager.sales_related.links import (
    normalize_share_link,
    parse_link_import,
)
from weimi_manager.sales_related.models import SkuCandidate
from weimi_manager.sales_related.money import (
    cents_to_yuan,
    multiply_cents,
    yuan_to_cents,
)
from weimi_manager.sales_related.sku_rules import (
    SkuSelectionMode,
    select_sku,
)


def candidate(
    sku_id: str,
    price: int,
    stock: int,
    **overrides,
) -> SkuCandidate:
    values = {
        "sku_id": sku_id,
        "price_cents": price,
        "stock": stock,
        "specs": {"颜色": "黑色", "尺码": "M"},
        "available": True,
        "complete": True,
        "limit_min": 1,
        "limit_max": None,
        "default": False,
    }
    values.update(overrides)
    return SkuCandidate(**values)


def test_money_is_integer_cents_and_rejects_float() -> None:
    assert yuan_to_cents("20.05") == 2005
    assert yuan_to_cents(Decimal("0.015")) == 2
    assert cents_to_yuan(2005) == "20.05"
    assert multiply_cents(2005, 3) == 6015
    with pytest.raises(TypeError):
        yuan_to_cents(20.05)


def test_link_import_has_no_hardcoded_batch_limit_and_reports_duplicates() -> None:
    lines = [
        f"https://example.test/product/{index}?b=2&a=1#fragment"
        for index in range(60)
    ]
    lines.append(lines[0])
    lines.append("这不是链接")
    existing = {
        normalize_share_link("https://example.test/product/7?a=1&b=2")
    }

    result = parse_link_import(
        "\n".join(lines),
        existing_links=existing,
    )

    assert len(result.accepted) == 59
    assert result.duplicates
    assert result.invalid
    assert all("#" not in link for link in result.accepted)


def test_duplicate_can_be_explicitly_imported_again() -> None:
    link = "https://example.test/item/1"
    result = parse_link_import(
        f"{link}\n{link}",
        allow_duplicates=True,
    )
    assert result.accepted == (link, link)
    assert result.duplicate_entries == (link,)


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        (SkuSelectionMode.ANY, "SKU-A"),
        (SkuSelectionMode.LOWEST_PRICE, "SKU-A"),
        (SkuSelectionMode.HIGHEST_PRICE, "SKU-C"),
        (SkuSelectionMode.PRODUCT_DEFAULT, "SKU-B"),
    ],
)
def test_sku_selection_modes(mode, expected) -> None:
    result = select_sku(
        [
            candidate("SKU-A", 1000, 10),
            candidate("SKU-B", 1500, 10, default=True),
            candidate("SKU-C", 2000, 10),
        ],
        mode,
        1,
    )
    assert result.ok
    assert result.selected
    assert result.selected.sku_id == expected


def test_specified_sku_and_spec_selection() -> None:
    candidates = [
        candidate("SKU-A", 1000, 10, specs={"颜色": "黑色", "尺码": "M"}),
        candidate("SKU-B", 1200, 10, specs={"颜色": "白色", "尺码": "L"}),
    ]
    by_id = select_sku(
        candidates,
        SkuSelectionMode.SPECIFIED,
        1,
        specified_sku_id="SKU-B",
    )
    assert by_id.selected and by_id.selected.sku_id == "SKU-B"
    by_spec = select_sku(
        candidates,
        SkuSelectionMode.SPECIFIED,
        1,
        specified_specs={"尺码": "L", "颜色": "白色"},
    )
    assert by_spec.selected and by_spec.selected.sku_id == "SKU-B"


def test_quantity_must_be_met_by_one_sku_without_split() -> None:
    result = select_sku(
        [
            candidate("SKU-A", 1000, 1),
            candidate("SKU-B", 1000, 2),
        ],
        SkuSelectionMode.LOWEST_PRICE,
        3,
    )
    assert not result.ok
    assert result.reason_code == "insufficient_stock"
    assert "不拆单" in result.reason


@pytest.mark.parametrize("quantity", [1, 3, 5, 10])
def test_custom_quantities_are_supported(quantity: int) -> None:
    result = select_sku(
        [candidate("SKU-10", 1000, 10)],
        SkuSelectionMode.ANY,
        quantity,
    )
    assert result.ok
    assert result.quantity == quantity


def test_limit_and_price_cap_are_checked_before_selection() -> None:
    result = select_sku(
        [
            candidate("SKU-A", 1000, 10, limit_max=2),
            candidate("SKU-B", 2000, 10),
        ],
        SkuSelectionMode.LOWEST_PRICE,
        3,
        max_price_cents=1500,
    )
    assert not result.ok
    assert result.reason_code == "over_price_cap"


def test_incomplete_sku_pauses_instead_of_guessing() -> None:
    result = select_sku(
        [
            candidate("SKU-A", 1000, 10, complete=False),
            candidate("SKU-B", 900, 10, available=False),
        ],
        SkuSelectionMode.ANY,
        1,
    )
    assert not result.ok
    assert result.reason_code == "incomplete"
