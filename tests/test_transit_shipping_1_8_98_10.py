from __future__ import annotations

from weimi_manager.transit_shipping import (
    QINGHAI,
    TIBET,
    XINJIANG,
    apply_target_rules,
    normalize_rules,
)


def _template() -> dict:
    return {
        "templateId": "template-1",
        "name": "全国包邮(偏远地区除外)",
        "valuationType": 1,
        "sendTime": 7,
        "shippingMethod": 2,
        "isDefault": 1,
        "deliveryType": 1,
        "allFreightCalcMethod": {
            "freightCalcMethodList": [
                {
                    "isDefault": True,
                    "firstValAmount": 1,
                    "firstPrice": 0,
                    "secondValAmount": 1,
                    "secondPrice": 0,
                },
                {
                    "isDefault": False,
                    "addressInfo": [
                        {"provinceName": XINJIANG},
                        {"provinceName": TIBET},
                        {"provinceName": QINGHAI},
                    ],
                    "firstValAmount": 1,
                    "firstPrice": 1000,
                    "secondValAmount": 1,
                    "secondPrice": 800,
                },
                {
                    "isDefault": False,
                    "addressInfo": [{"provinceName": "海南省"}],
                    "firstValAmount": 1,
                    "firstPrice": 500,
                    "secondValAmount": 1,
                    "secondPrice": 0,
                },
            ]
        },
        "notSendArea": {
            "addressInfo": [
                {"provinceName": XINJIANG},
                {"provinceName": "台湾省"},
            ]
        },
        "consolidateFlag": 0,
        "relatedProductsNum": 10,
    }


def test_target_rules_remove_xinjiang_and_build_transit_schema() -> None:
    rules = normalize_rules(
        {
            "transit_first_quantity": 1,
            "transit_first_price_cents": 300,
            "transit_second_quantity": 1,
            "transit_second_price_cents": 100,
        }
    )
    result = apply_target_rules(_template(), rules)

    assert result["shippingMethod"] == 2
    direct = result["allFreightCalcMethod"]["freightCalcMethodList"]
    assert len(direct) == 2
    assert direct[0] == {
        "addressInfo": [],
        "isDefault": True,
        "firstValAmount": 1,
        "firstPrice": 0,
        "secondValAmount": 1,
        "secondPrice": 0,
    }
    regions = {
        item["provinceName"]
        for item in direct[1]["addressInfo"]
    }
    assert regions == {TIBET, QINGHAI}
    assert direct[1]["firstPrice"] == 1000
    assert direct[1]["secondPrice"] == 800
    assert {
        item["provinceName"]
        for item in result["notSendArea"]["addressInfo"]
    } == {"台湾省"}
    assert result["consolidateFlag"] == 1
    transit = result["consolidateConfig"]["allFreightCalcMethod"][
        "freightCalcMethodList"
    ]
    assert len(transit) == 1
    assert transit[0]["isDefault"] is True
    assert transit[0]["firstPrice"] == 300
    assert transit[0]["secondPrice"] == 100


def test_rules_allow_zero_fee_and_reject_negative_values() -> None:
    rules = normalize_rules(
        {
            "transit_first_price_cents": -1,
            "transit_second_price_cents": 0,
            "transit_first_quantity": 0,
        }
    )
    assert rules["transit_first_price_cents"] == 0
    assert rules["transit_second_price_cents"] == 0
    assert rules["transit_first_quantity"] == 1
