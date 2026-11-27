from __future__ import annotations

from pathlib import Path

import pytest

from weimi_manager.db import Database
from weimi_manager.service import SyncService
from weimi_manager.ui.upload_page import UploadPage


class _CategoryRuleClient:
    def __init__(
        self,
        *,
        fail: bool = False,
        risk_mode: str = "",
    ) -> None:
        self.fail = fail
        self.risk_mode = risk_mode

    def fetch_category_product_rule(
        self,
        category_id: str,
        brand_id: str = "2100000000",
        release_mode: int = 0,
    ) -> dict:
        assert category_id == "6117"
        assert brand_id == "2100000000"
        assert release_mode == 0
        if self.fail:
            raise RuntimeError("类目规则读取失败")
        attributes = [
            {
                "name": "面料材质",
                "type_v2": "select_one",
                "value": "聚酯纤维;尼龙;棉",
                "required_rule": {"rule_type": 1},
            },
            {
                "name": "风格",
                "type_v2": "select_one",
                "value": "休闲;运动",
            },
            {
                "name": "适用场景",
                "type_v2": "multi_select",
                "value": "日常;户外",
            },
            {
                "name": "适用人群",
                "type_v2": "select_one",
                "value": "通用;家庭",
            },
            {
                "name": "厚薄",
                "type_v2": "select_one",
                "value": "常规;加厚",
            },
        ]
        if self.risk_mode:
            attributes.append(
                {
                    "name": "主要下游平台",
                    "type_v2": "select_one",
                    "value": "eBay;亚马逊",
                    "required_rule": {
                        "rule_type": 1
                        if self.risk_mode == "required"
                        else 0
                    },
                }
            )
        return {
            "info": {"name": "防晒手套/防晒袖套/冰袖"},
            "product_attr_list": attributes,
        }


def _service_and_store(tmp_path: Path):
    database = Database(tmp_path / "upload-attribute-safety.db")
    store_id = database.add_store(
        "湛晓冠旺商行",
        "wx-attribute-safety",
        "secret",
    )
    store = database.get_store(store_id)
    assert store
    return SyncService(database), store


def test_category_rule_removes_external_marketing_attributes(
    tmp_path: Path,
) -> None:
    service, store = _service_and_store(tmp_path)
    sku_snapshot = [
        {
            "sale_price": 1990,
            "stock_num": 8,
            "sku_attrs": [
                {"attr_key": "颜色", "attr_value": "黑色"},
                {"attr_key": "尺码", "attr_value": "M"},
            ],
        }
    ]
    payload = {
        "title": "聚酯纤维防晒手套",
        "brand_id": "2100000000",
        "cats_v2": [{"cat_id": "6117"}],
        "attrs": [
            {"attr_key": "是否外贸", "attr_value": "是"},
            {
                "attr_key": "是否跨境出口专供货源",
                "attr_value": "是",
            },
            {
                "attr_key": "主要下游平台",
                "attr_value": "eBay、亚马逊、Wish、速卖通、独立站、LAZADA",
            },
            {
                "attr_key": "主要第三方平台",
                "attr_value": "亚马逊、eBay",
            },
            {
                "attr_key": "主要销售地区",
                "attr_value": "非洲、欧洲、南美、东南亚、北美、东北亚、中东",
            },
            {"attr_key": "主要上下游平台", "attr_value": "1688"},
            {"attr_key": "主要上下游品牌", "attr_value": "品牌甲"},
            {"attr_key": "来源平台", "attr_value": "1688"},
            {"attr_key": "品 牌", "attr_value": "重复品牌字段"},
            {"attr_key": "面料材质", "attr_value": "聚酯纤维"},
            {"attr_key": "风格", "attr_value": "休闲"},
            {"attr_key": "适用场景", "attr_value": "户外"},
            {"attr_key": "厚薄", "attr_value": "常规"},
        ],
        "skus": sku_snapshot,
    }

    service._apply_required_category_attributes(
        store,
        _CategoryRuleClient(),
        payload,
    )

    assert payload["attrs"] == [
        {"attr_key": "面料材质", "attr_value": "聚酯纤维"},
        {"attr_key": "风格", "attr_value": "休闲"},
        {"attr_key": "适用场景", "attr_value": "户外"},
        {"attr_key": "厚薄", "attr_value": "常规"},
    ]
    assert payload["skus"] == sku_snapshot


def test_rule_failure_removes_only_known_risky_attributes(
    tmp_path: Path,
) -> None:
    service, store = _service_and_store(tmp_path)
    payload = {
        "title": "防晒手套",
        "brand_id": "2100000000",
        "cats_v2": [{"cat_id": "6117"}],
        "attrs": [
            {"attr_key": "是否 外贸", "attr_value": "是"},
            {"attr_key": "主要 销售 地区", "attr_value": "欧洲"},
            {"attr_key": "主要下游平台", "attr_value": "eBay"},
            {"attr_key": "跨境出口专供货源", "attr_value": "是"},
            {"attr_key": "面料材质", "attr_value": "聚酯纤维"},
            {"attr_key": "颜色", "attr_value": "黑色"},
            {"attr_key": "尺码", "attr_value": "M"},
        ],
        "skus": [
            {
                "sale_price": 1990,
                "stock_num": 8,
                "sku_attrs": [
                    {"attr_key": "颜色", "attr_value": "黑色"},
                    {"attr_key": "尺码", "attr_value": "M"},
                ],
            }
        ],
    }

    service._apply_required_category_attributes(
        store,
        _CategoryRuleClient(fail=True),
        payload,
    )

    assert payload["attrs"] == [
        {"attr_key": "面料材质", "attr_value": "聚酯纤维"},
        {"attr_key": "颜色", "attr_value": "黑色"},
        {"attr_key": "尺码", "attr_value": "M"},
    ]


def test_optional_rule_attribute_cannot_keep_external_platform_marketing(
    tmp_path: Path,
) -> None:
    service, store = _service_and_store(tmp_path)
    payload = {
        "title": "防晒手套",
        "brand_id": "2100000000",
        "cats_v2": [{"cat_id": "6117"}],
        "attrs": [
            {"attr_key": "面料材质", "attr_value": "聚酯纤维"},
            {"attr_key": "主要下游平台", "attr_value": "eBay"},
        ],
    }

    service._apply_required_category_attributes(
        store,
        _CategoryRuleClient(risk_mode="allowed"),
        payload,
    )

    assert payload["attrs"] == [
        {"attr_key": "面料材质", "attr_value": "聚酯纤维"},
    ]


def test_required_rule_attribute_is_kept_even_if_marketing_like(
    tmp_path: Path,
) -> None:
    service, store = _service_and_store(tmp_path)
    payload = {
        "title": "平台专用商品",
        "brand_id": "2100000000",
        "cats_v2": [{"cat_id": "6117"}],
        "attrs": [
            {"attr_key": "面料材质", "attr_value": "聚酯纤维"},
            {"attr_key": "主要下游平台", "attr_value": "微信小店"},
        ],
    }

    service._apply_required_category_attributes(
        store,
        _CategoryRuleClient(risk_mode="required"),
        payload,
    )

    assert payload["attrs"] == [
        {"attr_key": "面料材质", "attr_value": "聚酯纤维"},
        {"attr_key": "主要下游平台", "attr_value": "微信小店"},
    ]


def test_template_custom_attributes_cannot_bypass_marketing_filter(
    tmp_path: Path,
) -> None:
    service, store = _service_and_store(tmp_path)
    page = object.__new__(UploadPage)
    item = {
        "title": "聚酯纤维防晒手套",
        "price": 1990,
        "stock": 8,
        "payload": {
            "title": "聚酯纤维防晒手套",
            "cats_v2": [{"cat_id": "6117"}],
            "head_imgs": ["https://example.test/glove.jpg"],
            "attrs": [
                {"attr_key": "是否外贸", "attr_value": "是"},
                {"attr_key": "面料材质", "attr_value": "聚酯纤维"},
            ],
            "skus": [
                {
                    "sale_price": 1990,
                    "stock_num": 8,
                    "sku_attrs": [
                        {"attr_key": "颜色", "attr_value": "黑色"},
                        {"attr_key": "尺码", "attr_value": "M"},
                    ],
                }
            ],
        },
    }
    template = {
        "enable_custom_attributes": True,
        "attribute_rules": (
            "主要下游平台=eBay\n"
            "主要销售地区=欧洲\n"
            "适用人群=家庭\n"
            "厚薄=常规"
        ),
    }

    payload = page._upload_payload(item, template)
    service._apply_required_category_attributes(
        store,
        _CategoryRuleClient(),
        payload,
    )

    keys = {
        item["attr_key"]
        for item in payload["attrs"]
    }
    assert keys == {"面料材质", "适用人群", "厚薄"}
    assert payload["skus"][0]["sku_attrs"] == [
        {"attr_key": "颜色", "attr_value": "黑色"},
        {"attr_key": "尺码", "attr_value": "M"},
    ]


def test_required_category_attribute_is_filled_after_source_filtering(
    tmp_path: Path,
) -> None:
    service, store = _service_and_store(tmp_path)
    payload = {
        "title": "聚酯纤维防晒手套",
        "brand_id": "2100000000",
        "cats_v2": [{"cat_id": "6117"}],
        "attrs": [
            {"attr_key": "主要下游平台", "attr_value": "eBay"},
            {"attr_key": "风格", "attr_value": "休闲"},
        ],
    }

    service._apply_required_category_attributes(
        store,
        _CategoryRuleClient(),
        payload,
    )

    assert payload["attrs"] == [
        {"attr_key": "风格", "attr_value": "休闲"},
        {"attr_key": "面料材质", "attr_value": "聚酯纤维"},
    ]


def test_custom_attribute_switch_off_does_not_add_template_marketing(
    tmp_path: Path,
) -> None:
    service, store = _service_and_store(tmp_path)
    page = object.__new__(UploadPage)
    payload = page._upload_payload(
        {
            "title": "聚酯纤维防晒手套",
            "price": 1990,
            "stock": 8,
            "payload": {
                "title": "聚酯纤维防晒手套",
                "cats_v2": [{"cat_id": "6117"}],
                "head_imgs": ["https://example.test/glove.jpg"],
                "attrs": [
                    {"attr_key": "面料材质", "attr_value": "聚酯纤维"},
                ],
                "skus": [
                    {
                        "sale_price": 1990,
                        "stock_num": 8,
                        "sku_attrs": [
                            {"attr_key": "颜色", "attr_value": "黑色"},
                        ],
                    }
                ],
            },
        },
        {
            "enable_custom_attributes": False,
            "attribute_rules": "主要下游平台=eBay",
        },
    )

    service._apply_required_category_attributes(
        store,
        _CategoryRuleClient(),
        payload,
    )

    assert payload["attrs"] == [
        {"attr_key": "面料材质", "attr_value": "聚酯纤维"},
    ]


def test_similar_official_keys_are_normalized_and_deduplicated(
    tmp_path: Path,
) -> None:
    service, store = _service_and_store(tmp_path)
    payload = {
        "title": "聚酯纤维防晒手套",
        "brand_id": "2100000000",
        "cats_v2": [{"cat_id": "6117"}],
        "attrs": [
            {"attr_key": "面料 材质", "attr_value": "棉"},
            {"attr_key": "面料材质", "attr_value": "聚酯纤维"},
            {"attr_key": "适用 场景", "attr_value": "户外"},
            {"attr_key": "主要 下游 平台", "attr_value": "eBay"},
        ],
    }

    service._apply_required_category_attributes(
        store,
        _CategoryRuleClient(),
        payload,
    )

    assert payload["attrs"] == [
        {"attr_key": "面料材质", "attr_value": "聚酯纤维"},
        {"attr_key": "适用场景", "attr_value": "户外"},
    ]


def test_imported_attribute_list_is_sanitized_in_upload_payload(
    tmp_path: Path,
) -> None:
    service, store = _service_and_store(tmp_path)
    page = object.__new__(UploadPage)
    payload = page._upload_payload(
        {
            "title": "聚酯纤维防晒手套",
            "price": 1990,
            "stock": 8,
            "payload": {
                "platform": "1688",
                "productId": "6117000001",
                "productName": "聚酯纤维防晒手套",
                "productUrl": "https://detail.1688.com/offer/6117000001.html",
                "itemImgs": ["https://example.test/glove.jpg"],
                "desc": ["https://example.test/detail.jpg"],
                "skus": [
                    {
                        "skuId": "SKU-1",
                        "price": 1990,
                        "quantity": 8,
                    }
                ],
                "attribute": [
                    {"key": "是否外贸", "value": "是"},
                    {"key": "主要下游平台", "value": "eBay"},
                    {"key": "面料材质", "value": "聚酯纤维"},
                    {"key": "风格", "value": "休闲"},
                ],
            },
        },
        {},
    )
    payload["cats_v2"] = [{"cat_id": "6117"}]
    assert {
        item["attr_key"]
        for item in payload["attrs"]
    } == {"是否外贸", "主要下游平台", "面料材质", "风格"}

    service._apply_required_category_attributes(
        store,
        _CategoryRuleClient(),
        payload,
    )

    assert payload["attrs"] == [
        {"attr_key": "面料材质", "attr_value": "聚酯纤维"},
        {"attr_key": "风格", "attr_value": "休闲"},
    ]
    assert payload["skus"][0]["sku_attrs"] == [
        {"attr_key": "规格", "attr_value": "默认规格"},
    ]


def test_rule_failure_with_brand_id_removes_normalized_brand_attributes(
    tmp_path: Path,
) -> None:
    service, store = _service_and_store(tmp_path)
    payload = {
        "title": "防晒手套",
        "brand_id": "2100000000",
        "cats_v2": [{"cat_id": "6117"}],
        "attrs": [
            {"attr_key": "品牌", "attr_value": "旧品牌"},
            {"attr_key": "品 牌", "attr_value": "另一个品牌"},
            {"attr_key": "面料材质", "attr_value": "聚酯纤维"},
        ],
    }

    service._apply_required_category_attributes(
        store,
        _CategoryRuleClient(fail=True),
        payload,
    )

    assert payload["attrs"] == [
        {"attr_key": "面料材质", "attr_value": "聚酯纤维"},
    ]


def test_required_attribute_with_empty_existing_value_is_refilled(
    tmp_path: Path,
) -> None:
    service, store = _service_and_store(tmp_path)
    payload = {
        "title": "聚酯纤维防晒手套",
        "brand_id": "2100000000",
        "cats_v2": [{"cat_id": "6117"}],
        "attrs": [
            {"attr_key": "面料材质", "attr_value": ""},
        ],
    }

    service._apply_required_category_attributes(
        store,
        _CategoryRuleClient(),
        payload,
    )

    assert payload["attrs"] == [
        {"attr_key": "面料材质", "attr_value": "聚酯纤维"},
    ]


def test_required_attribute_that_cannot_be_refilled_fails_preflight(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service, store = _service_and_store(tmp_path)
    monkeypatch.setattr(
        service,
        "_required_attribute_value",
        lambda *_args, **_kwargs: "",
    )
    payload = {
        "title": "防晒手套",
        "brand_id": "2100000000",
        "cats_v2": [{"cat_id": "6117"}],
        "attrs": [
            {"attr_key": "面料材质", "attr_value": ""},
        ],
    }

    with pytest.raises(ValueError, match="面料材质"):
        service._apply_required_category_attributes(
            store,
            _CategoryRuleClient(),
            payload,
        )
