from __future__ import annotations

from datetime import UTC, datetime

import pytest

from weimi_manager.dongxiao.domain.enums import (
    OrderStatus,
    OrderVerificationSource,
    ProductState,
)
from weimi_manager.dongxiao.domain.models import (
    Product,
    RealOrderReference,
    Sku,
)
from weimi_manager.dongxiao.domain.rules import (
    SkuSelectionFailure,
    SkuSelectionMode,
    normalize_product_ids,
    parse_yuan_to_cents,
    select_sku,
    validate_money_cents,
)


def test_product_import_reports_empty_and_duplicate_entries() -> None:
    empty = normalize_product_ids(store_id=11, values=[])
    assert empty.accepted == ()
    assert empty.duplicates == ()
    assert empty.invalid == ()

    parsed = normalize_product_ids(
        store_id=11,
        values=["prod-1", "", "prod-1", "prod-2", "prod-2"],
    )
    assert parsed.accepted == ("prod-1", "prod-2")
    assert parsed.duplicates == ("prod-1", "prod-2")
    assert parsed.invalid == ("",)


def test_money_validation_uses_integer_cents_and_bounds() -> None:
    assert validate_money_cents(0) == 0
    assert validate_money_cents(1) == 1
    assert validate_money_cents(999_999_999_999) == 999_999_999_999

    for invalid in (-1, 1.25, True, "100"):
        with pytest.raises((TypeError, ValueError)):
            validate_money_cents(invalid)
    with pytest.raises(ValueError):
        validate_money_cents(1_000_000_000_000)
    assert parse_yuan_to_cents("12.34") == 1234
    with pytest.raises(ValueError):
        parse_yuan_to_cents("1.234")


def test_product_sku_and_real_order_validate_store_and_trust() -> None:
    observed_at = datetime(2026, 11, 18, 10, 0, tzinfo=UTC)
    product = Product(
        store_id=11,
        store_app_id="wx-store-11",
        product_id="prod-1",
        title="测试商品",
        state=ProductState.ACTIVE,
        min_price_cents=100,
        stock_total=3,
        observed_at=observed_at,
    )
    sku = Sku(
        store_id=11,
        store_app_id="wx-store-11",
        product_id="prod-1",
        sku_id="sku-1",
        specs=(("颜色", "黑色"),),
        price_cents=100,
        stock=1,
        available=True,
        purchase_min=1,
        purchase_max=2,
        observed_at=observed_at,
    )
    assert product.store_id == sku.store_id
    assert sku.specs == (("颜色", "黑色"),)

    with pytest.raises(ValueError):
        Product(
            store_id=0,
            store_app_id="wx-store-11",
            product_id="prod-2",
            title="非法店铺",
            state=ProductState.UNKNOWN,
            min_price_cents=100,
            stock_total=0,
            observed_at=observed_at,
        )
    with pytest.raises(ValueError):
        Product(
            store_id=11,
            store_app_id="wx-store-11",
            product_id="prod-3",
            title="无时区时间",
            state=ProductState.UNKNOWN,
            min_price_cents=100,
            stock_total=0,
            observed_at=datetime(2026, 11, 18, 10, 0),
        )

    simulation = RealOrderReference(
        order_ref_id="order-ref-1",
        store_id=11,
        store_app_id="wx-store-11",
        platform_order_id="order-1",
        status=OrderStatus.PENDING_PAYMENT,
        verified=False,
        source=OrderVerificationSource.MOCK,
        verified_at=None,
        created_at=observed_at,
    )
    assert simulation.verified is False
    with pytest.raises(ValueError):
        RealOrderReference(
            order_ref_id="order-ref-2",
            store_id=11,
            store_app_id="wx-store-11",
            platform_order_id="order-2",
            status=OrderStatus.PAID,
            verified=True,
            source=OrderVerificationSource.CLIENT_REPORT,
            verified_at=observed_at,
            created_at=observed_at,
        )


def test_sku_selection_checks_stock_price_and_limits() -> None:
    observed_at = datetime(2026, 11, 18, 10, 0, tzinfo=UTC)
    available = Sku(
        store_id=11,
        store_app_id="wx-store-11",
        product_id="prod-1",
        sku_id="sku-1",
        price_cents=500,
        stock=1,
        available=True,
        purchase_min=1,
        purchase_max=2,
        observed_at=observed_at,
    )
    unavailable = Sku(
        store_id=11,
        store_app_id="wx-store-11",
        product_id="prod-1",
        sku_id="sku-2",
        price_cents=400,
        stock=10,
        available=False,
        observed_at=observed_at,
    )
    empty_stock = Sku(
        store_id=11,
        store_app_id="wx-store-11",
        product_id="prod-1",
        sku_id="sku-3",
        price_cents=450,
        stock=0,
        available=True,
        observed_at=observed_at,
    )
    higher_stock = Sku(
        store_id=11,
        store_app_id="wx-store-11",
        product_id="prod-1",
        sku_id="sku-4",
        price_cents=600,
        stock=10,
        available=True,
        purchase_min=1,
        purchase_max=2,
        observed_at=observed_at,
    )
    default_sku = Sku(
        store_id=11,
        store_app_id="wx-store-11",
        product_id="prod-1",
        sku_id="sku-default",
        price_cents=550,
        stock=10,
        available=True,
        is_default=True,
        observed_at=observed_at,
    )
    incomplete = Sku(
        store_id=11,
        store_app_id="wx-store-11",
        product_id="prod-1",
        sku_id="sku-incomplete",
        price_cents=0,
        stock=0,
        available=True,
        observed_at=observed_at,
        data_complete=False,
    )

    assert select_sku(
        store_id=11,
        product_id="prod-1",
        candidates=(),
        quantity=1,
    ).failure is SkuSelectionFailure.NO_SKU
    assert select_sku(
        store_id=11,
        product_id="prod-1",
        candidates=(unavailable,),
        quantity=1,
    ).failure is SkuSelectionFailure.UNAVAILABLE
    assert select_sku(
        store_id=11,
        product_id="prod-1",
        candidates=(empty_stock,),
        quantity=1,
    ).failure is SkuSelectionFailure.OUT_OF_STOCK
    assert select_sku(
        store_id=11,
        product_id="prod-1",
        candidates=(available,),
        quantity=2,
    ).failure is SkuSelectionFailure.INSUFFICIENT_STOCK
    assert select_sku(
        store_id=11,
        product_id="prod-1",
        candidates=(unavailable,),
        quantity=1,
        expected_price_cents=450,
    ).failure is SkuSelectionFailure.PRICE_CHANGED

    selection = select_sku(
        store_id=11,
        product_id="prod-1",
        candidates=(available,),
        quantity=1,
        expected_price_cents=500,
        max_price_cents=500,
    )
    assert selection.selected == available

    assert select_sku(
        store_id=11,
        product_id="prod-1",
        candidates=(available,),
        quantity=2,
        expected_price_cents=500,
    ).failure is SkuSelectionFailure.INSUFFICIENT_STOCK
    assert select_sku(
        store_id=11,
        product_id="prod-1",
        candidates=(higher_stock,),
        quantity=3,
    ).failure is SkuSelectionFailure.ABOVE_LIMIT
    assert select_sku(
        store_id=11,
        product_id="prod-1",
        candidates=(higher_stock,),
        quantity=1,
        max_price_cents=500,
    ).failure is SkuSelectionFailure.OVER_PRICE_CAP
    with pytest.raises(ValueError):
        select_sku(
            store_id=11,
            product_id="prod-1",
            candidates=(available,),
            quantity=0,
        )

    assert select_sku(
        store_id=11,
        product_id="prod-1",
        candidates=(higher_stock, available),
        quantity=1,
        expected_price_cents=None,
        mode=SkuSelectionMode.LOWEST_PRICE,
    ).selected == available
    assert select_sku(
        store_id=11,
        product_id="prod-1",
        candidates=(higher_stock, available),
        quantity=1,
        mode=SkuSelectionMode.HIGHEST_PRICE,
    ).selected == higher_stock
    assert select_sku(
        store_id=11,
        product_id="prod-1",
        candidates=(higher_stock, default_sku),
        quantity=1,
        mode=SkuSelectionMode.PRODUCT_DEFAULT,
    ).selected == default_sku
    assert select_sku(
        store_id=11,
        product_id="prod-1",
        candidates=(higher_stock,),
        quantity=1,
        mode=SkuSelectionMode.SPECIFIED,
        specified_sku_id="sku-missing",
    ).failure is SkuSelectionFailure.SPECIFIED_NOT_FOUND
    assert select_sku(
        store_id=11,
        product_id="prod-1",
        candidates=(incomplete,),
        quantity=1,
    ).failure is SkuSelectionFailure.INCOMPLETE_DATA
    assert select_sku(
        store_id=11,
        product_id="prod-1",
        candidates=(available,),
        quantity=1,
        product_state=ProductState.INACTIVE,
    ).failure is SkuSelectionFailure.PRODUCT_INACTIVE


def test_sku_selection_is_deterministic_for_equal_prices() -> None:
    observed_at = datetime(2026, 11, 18, 10, 0, tzinfo=UTC)
    candidates = (
        Sku(
            store_id=11,
            store_app_id="wx-store-11",
            product_id="prod-1",
            sku_id="sku-z",
            price_cents=500,
            stock=10,
            observed_at=observed_at,
        ),
        Sku(
            store_id=11,
            store_app_id="wx-store-11",
            product_id="prod-1",
            sku_id="sku-a",
            price_cents=500,
            stock=10,
            observed_at=observed_at,
        ),
    )

    for mode in (
        SkuSelectionMode.ANY,
        SkuSelectionMode.LOWEST_PRICE,
        SkuSelectionMode.HIGHEST_PRICE,
    ):
        selected = {
            select_sku(
                store_id=11,
                product_id="prod-1",
                candidates=candidates,
                quantity=1,
                mode=mode,
            ).selected.sku_id
            for _index in range(5)
        }
        assert selected == {"sku-a"}


def test_sku_selection_defaults_unverified_purchase_minimum_to_one() -> None:
    observed_at = datetime(2026, 11, 18, 10, 0, tzinfo=UTC)
    sku = Sku(
        store_id=11,
        store_app_id="wx-store-11",
        product_id="prod-1",
        sku_id="sku-1",
        price_cents=500,
        stock=10,
        observed_at=observed_at,
        purchase_min_verified=False,
    )

    selection = select_sku(
        store_id=11,
        product_id="prod-1",
        candidates=(sku,),
        quantity=1,
    )

    assert selection.selected is sku
    assert selection.selected.purchase_min == 1
