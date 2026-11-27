from __future__ import annotations

import io
from pathlib import Path

import pytest

from weimi_manager.db import Database
from weimi_manager.models import UploadPreflightError
from weimi_manager.service import SyncService
from weimi_manager.wechat_api import WechatShopClient
from weimi_manager.upload_content_safety import (
    ThirdPartyPlatformRule,
    inspect_image_bytes,
    sanitize_upload_content,
)


def test_sanitizes_text_attributes_and_sku_while_preserving_normal_brands() -> None:
    payload = {
        "title": "淘宝同款 Apple iPhone 15 手机壳",
        "short_title": "PDD货源 手机壳",
        "desc_info": {
            "desc": "1688批发同款，联系淘宝店铺下单。材质安全",
            "imgs": [],
        },
        "attrs": [
            {"attr_key": "面料材质", "attr_value": "聚酯纤维"},
            {"attr_key": "系列", "attr_value": "Apple iPhone 15"},
            {"attr_key": "销售平台", "attr_value": "1688"},
            {
                "attr_key": "商品链接",
                "attr_value": "https://detail.taobao.com/item.htm?id=1",
            },
        ],
        "attributes": {
            "材质": "棉",
            "购买平台": "Amazon",
        },
        "skus": [
            {
                "sale_price": 1990,
                "stock_num": 8,
                "sku_attrs": [
                    {"attr_key": "颜色", "attr_value": "黑色"},
                    {"attr_key": "尺码", "attr_value": "M"},
                    {"attr_key": "购买店铺", "attr_value": "淘宝旗舰店"},
                ],
                "thumb_url": "https://mmecimage.cn/safe-sku.jpg",
            }
        ],
    }

    result = sanitize_upload_content(
        payload,
        required_attribute_keys={"面料材质"},
    )

    assert result.payload["title"] == "Apple iPhone 15 手机壳"
    assert result.payload["short_title"] == "手机壳"
    assert result.payload["desc_info"]["desc"] == "材质安全"
    assert result.payload["attrs"] == [
        {"attr_key": "面料材质", "attr_value": "聚酯纤维"},
        {"attr_key": "系列", "attr_value": "Apple iPhone 15"},
    ]
    assert result.payload["attributes"] == {"材质": "棉"}
    assert result.payload["skus"][0]["sku_attrs"] == [
        {"attr_key": "颜色", "attr_value": "黑色"},
        {"attr_key": "尺码", "attr_value": "M"},
    ]
    assert result.hits
    assert all(
        set(hit) == {"field", "rule", "action"}
        for hit in result.hits
    )
    assert "淘宝同款" not in str(result.hits)
    assert "PDD货源" not in str(result.hits)


def test_empty_title_short_title_and_required_attribute_fail_preflight() -> None:
    with pytest.raises(UploadPreflightError, match="标题"):
        sanitize_upload_content(
            {
                "title": "淘宝同款",
                "short_title": "手机壳",
                "desc_info": {},
                "attrs": [],
                "skus": [],
            }
        )

    with pytest.raises(UploadPreflightError, match="短标题"):
        sanitize_upload_content(
            {
                "title": "手机壳",
                "short_title": "1688货源",
                "desc_info": {},
                "attrs": [],
                "skus": [],
            }
        )

    with pytest.raises(UploadPreflightError, match="面料材质"):
        sanitize_upload_content(
            {
                "title": "手机壳",
                "short_title": "手机壳",
                "desc_info": {},
                "attrs": [
                    {
                        "attr_key": "面料材质",
                        "attr_value": "PDD官方旗舰店",
                    }
                ],
                "skus": [],
            },
            required_attribute_keys={"面料材质"},
        )


@pytest.mark.parametrize(
    "platform_text",
    [
        "淘宝",
        "淘 宝",
        "拼多多",
        "PDD",
        "1688",
        "阿里巴巴",
        "速卖通",
        "AliExpress",
        "亚马逊",
        "Amazon",
        "Ａｍａｚｏｎ",
        "eBay",
        "Wish",
        "Lazada",
        "京东",
        "抖音",
        "快手",
        "小红书",
        "Shein",
    ],
)
def test_all_required_platform_names_are_removed(
    platform_text: str,
) -> None:
    result = sanitize_upload_content(
        {
            "title": f"{platform_text}同款 小米手机壳",
            "short_title": "手机壳",
            "desc_info": {"desc": "材质安全"},
            "attrs": [],
            "skus": [],
        }
    )

    assert result.payload["title"] == "小米手机壳"


def test_third_party_product_urls_are_removed_from_text() -> None:
    result = sanitize_upload_content(
        {
            "title": (
                "淘宝 https://item.taobao.com/item.htm?id=1 "
                "PDD https://mobile.yangkeduo.com/goods.html?goods_id=2 "
                "1688 https://detail.1688.com/offer/3.html "
                "Apple 手机壳"
            ),
            "short_title": "手机壳",
            "desc_info": {
                "desc": (
                    "Amazon https://www.amazon.com/dp/B1 "
                    "AliExpress https://www.aliexpress.com/item/2.html "
                    "材质安全"
                )
            },
            "attrs": [],
            "skus": [],
        }
    )

    assert result.payload["title"] == "Apple 手机壳"
    assert result.payload["desc_info"]["desc"] == "材质安全"


def test_protocol_less_platform_subdomains_are_removed() -> None:
    result = sanitize_upload_content(
        {
            "title": (
                "item.taobao.com m.taobao.com "
                "detail.1688.com Apple 手机壳"
            ),
            "short_title": "手机壳",
            "desc_info": {"desc": "detail.1688.com 材质安全"},
            "attrs": [],
            "skus": [],
        }
    )

    assert result.payload["title"] == "Apple 手机壳"
    assert result.payload["desc_info"]["desc"] == "材质安全"


@pytest.mark.parametrize(
    "host",
    [
        "mobile.yangkeduo.com",
        "item.taobao.com",
        "m.taobao.com",
        "detail.1688.com",
        "www.aliexpress.com",
        "m.amazon.com",
        "m.ebay.com",
        "m.wish.com",
        "www.lazada.com",
        "item.jd.com",
        "www.douyin.com",
        "www.kuaishou.com",
        "www.xiaohongshu.com",
        "www.shein.com",
    ],
)
def test_known_platform_hosts_without_protocol_are_removed(
    host: str,
) -> None:
    result = sanitize_upload_content(
        {
            "title": f"{host} 小米手机壳",
            "short_title": "手机壳",
            "desc_info": {"desc": "材质安全"},
            "attrs": [],
            "skus": [],
        }
    )

    assert result.payload["title"] == "小米手机壳"


def test_normal_brand_and_model_tokens_are_preserved() -> None:
    result = sanitize_upload_content(
        {
            "title": "Apple 小米 JD-200 手机壳",
            "short_title": "Apple 小米手机壳",
            "desc_info": {"desc": "型号 JD-200，材质 TPU"},
            "attrs": [{"attr_key": "型号", "attr_value": "JD-200"}],
            "skus": [],
        }
    )

    assert result.payload["title"] == "Apple 小米 JD-200 手机壳"
    assert result.payload["short_title"] == "Apple 小米手机壳"
    assert result.payload["desc_info"]["desc"] == "型号 JD-200，材质 TPU"
    assert result.payload["attrs"] == [
        {"attr_key": "型号", "attr_value": "JD-200"},
    ]


def test_platform_rules_are_extensible_without_code_changes() -> None:
    result = sanitize_upload_content(
        {
            "title": "Temu同款 手机壳",
            "short_title": "手机壳",
            "desc_info": {"desc": "材质安全"},
            "attrs": [],
            "skus": [],
        },
        platform_rules=(
            ThirdPartyPlatformRule(
                "temu",
                ("Temu",),
                ("temu.com",),
            ),
        ),
    )

    assert result.payload["title"] == "手机壳"


def test_residual_source_and_contact_lead_phrases_are_removed() -> None:
    result = sanitize_upload_content(
        {
            "title": "同款 手机壳",
            "short_title": "同款手机壳",
            "desc_info": {
                "desc": "货源 工艺说明，点击购买，联系客服下单。材质安全"
            },
            "attrs": [{"attr_key": "货源说明", "attr_value": "可批发"}],
            "skus": [],
        }
    )

    assert "同款" not in result.payload["title"]
    assert "同款" not in result.payload["short_title"]
    assert "货源" not in result.payload["desc_info"]["desc"]
    assert "点击购买" not in result.payload["desc_info"]["desc"]
    assert "联系客服下单" not in result.payload["desc_info"]["desc"]
    assert result.payload["attrs"] == []


def test_unsafe_images_are_removed_from_all_image_fields() -> None:
    payload = {
        "title": "Apple 手机壳",
        "short_title": "手机壳",
        "head_imgs": [
            "https://mmecimage.cn/qr-risk.jpg",
            "https://mmecimage.cn/head-safe-1.jpg",
            "https://mmecimage.cn/head-safe-2.jpg",
            "https://mmecimage.cn/head-safe-3.jpg",
        ],
        "desc_info": {
            "desc": "材质安全",
            "imgs": [
                "https://mmecimage.cn/desc-taobao.jpg",
                "https://mmecimage.cn/desc-safe.jpg",
            ],
        },
        "attrs": [],
        "skus": [
            {
                "sale_price": 1990,
                "stock_num": 8,
                "thumb_url": "https://mmecimage.cn/sku-qr.jpg",
                "sku_attrs": [
                    {"attr_key": "颜色", "attr_value": "黑色"},
                    {"attr_key": "尺码", "attr_value": "M"},
                ],
            }
        ],
    }

    result = sanitize_upload_content(
        payload,
        image_inspector=lambda source: (
            ("image.qr_code",)
            if "qr-risk" in source or "sku-qr" in source
            else (
                ("image.third_party_text",)
                if "taobao" in source
                else ()
            )
        ),
    )

    assert result.payload["head_imgs"] == [
        "https://mmecimage.cn/head-safe-1.jpg",
        "https://mmecimage.cn/head-safe-2.jpg",
        "https://mmecimage.cn/head-safe-3.jpg",
    ]
    assert result.payload["desc_info"]["imgs"] == [
        "https://mmecimage.cn/desc-safe.jpg",
    ]
    assert result.payload["skus"][0]["thumb_url"] == ""
    assert result.removed_images == 3
    assert all("mmecimage.cn" not in str(hit) for hit in result.hits)


def test_image_inspection_and_upload_use_the_same_bytes(
    tmp_path,
) -> None:
    database = Database(tmp_path / "image-integrity.db")
    store_id = database.add_store(
        "图片完整性店",
        "wx-image-integrity",
        "secret",
    )
    store = database.get_store(store_id)
    assert store
    uploaded = []
    loaded = []

    class Client:
        @staticmethod
        def upload_product_image(path):
            uploaded.append(Path(path).read_bytes())
            return f"https://uploaded.example/{len(uploaded)}.jpg"

        @staticmethod
        def upload_product_image_url(_url):
            raise AssertionError("不得为同一图片二次下载")

    image_bytes = {
        "https://source.example/head-1.jpg": b"head-one",
        "https://source.example/head-2.jpg": b"head-two",
        "https://source.example/head-3.jpg": b"head-three",
        "https://source.example/detail.jpg": b"detail",
    }

    def load_image(source):
        loaded.append(source)
        return image_bytes[source]

    prepared = SyncService(database)._prepare_product_payload(
        store,
        Client(),
        {
            "title": "Apple 手机壳",
            "short_title": "手机壳",
            "desc_info": {
                "desc": "材质安全",
                "imgs": ["https://source.example/detail.jpg"],
            },
            "head_imgs": [
                "https://source.example/head-1.jpg",
                "https://source.example/head-2.jpg",
                "https://source.example/head-3.jpg",
            ],
            "attrs": [],
            "skus": [
                {
                    "sale_price": 1990,
                    "stock_num": 8,
                    "sku_attrs": [
                        {"attr_key": "颜色", "attr_value": "黑色"},
                    ],
                }
            ],
            "express_info": {
                "template_id": "freight-1",
                "weight": 1000,
                "express_type": 0,
            },
            "after_sale_info": {
                "after_sale_address_id": "address-1",
            },
        },
        content_image_loader=load_image,
        content_image_inspector=lambda _data: (),
    )

    assert loaded == [
        "https://source.example/head-1.jpg",
        "https://source.example/head-2.jpg",
        "https://source.example/head-3.jpg",
        "https://source.example/detail.jpg",
    ]
    assert uploaded == [
        b"head-one",
        b"head-two",
        b"head-three",
        b"detail",
    ]
    assert prepared["head_imgs"] == [
        "https://uploaded.example/1.jpg",
        "https://uploaded.example/2.jpg",
        "https://uploaded.example/3.jpg",
    ]
    assert prepared["desc_info"]["imgs"] == [
        "https://uploaded.example/4.jpg",
    ]


def test_image_filename_with_platform_name_is_removed() -> None:
    result = sanitize_upload_content(
        {
            "title": "Apple 手机壳",
            "short_title": "手机壳",
            "head_imgs": [
                "https://cdn.example.test/taobao-main.jpg",
                "https://mmecimage.cn/head-safe-1.jpg",
            ],
            "desc_info": {
                "desc": "材质安全",
                "imgs": ["https://mmecimage.cn/desc-safe.jpg"],
            },
            "attrs": [],
            "skus": [],
        },
        image_inspector=lambda _source: (),
    )

    assert result.payload["head_imgs"] == [
        "https://mmecimage.cn/head-safe-1.jpg",
    ]
    assert result.removed_images == 1
    assert result.hits[0]["rule"] == "image.third_party_source"


def test_unknown_image_safety_fails_instead_of_submitting() -> None:
    def unavailable(_source):
        raise RuntimeError("detector unavailable")

    with pytest.raises(
        UploadPreflightError,
        match="图片无法完成安全检测",
    ):
        sanitize_upload_content(
            {
                "title": "Apple 手机壳",
                "short_title": "手机壳",
                "head_imgs": ["https://mmecimage.cn/head-safe.jpg"],
                "desc_info": {"desc": "材质安全"},
                "attrs": [],
                "skus": [],
            },
            image_inspector=unavailable,
        )


def test_final_product_payload_uses_content_safety_seam(
    tmp_path,
) -> None:
    database = Database(tmp_path / "content-safety.db")
    store_id = database.add_store(
        "安全上货店",
        "wx-content-safety",
        "secret",
    )
    store = database.get_store(store_id)
    assert store

    class Client:
        @staticmethod
        def fetch_category_product_rule(
            category_id,
            brand_id="2100000000",
            release_mode=0,
        ):
            assert category_id == "6117"
            return {
                "info": {"name": "手机壳"},
                "product_attr_list": [
                    {
                        "name": "面料材质",
                        "type_v2": "select_one",
                        "value": "聚酯纤维;硅胶",
                        "required_rule": {"rule_type": 1},
                    },
                    {
                        "name": "系列",
                        "type_v2": "string",
                    },
                ],
            }

    payload = {
        "title": "淘宝同款 Apple iPhone 15 手机壳",
        "short_title": "PDD货源 手机壳",
        "desc_info": {
            "desc": "1688批发同款，材质安全",
            "imgs": [
                "https://mmecimage.cn/desc-taobao.jpg",
                "https://mmecimage.cn/desc-safe.jpg",
            ],
        },
        "brand_id": "2100000000",
        "cats_v2": [{"cat_id": "6117"}],
        "attrs": [
            {"attr_key": "面料材质", "attr_value": "聚酯纤维"},
            {"attr_key": "销售平台", "attr_value": "1688"},
        ],
        "head_imgs": [
            "https://mmecimage.cn/qr-risk.jpg",
            "https://mmecimage.cn/head-safe-1.jpg",
            "https://mmecimage.cn/head-safe-2.jpg",
            "https://mmecimage.cn/head-safe-3.jpg",
        ],
        "skus": [
            {
                "sale_price": 1990,
                "stock_num": 8,
                "sku_attrs": [
                    {"attr_key": "颜色", "attr_value": "黑色"},
                    {"attr_key": "尺码", "attr_value": "M"},
                    {"attr_key": "购买店铺", "attr_value": "淘宝旗舰店"},
                ],
            }
        ],
        "express_info": {
            "template_id": "freight-1",
            "weight": 1000,
            "express_type": 0,
        },
        "after_sale_info": {
            "after_sale_address_id": "address-1",
        },
    }

    prepared = SyncService(database)._prepare_product_payload(
        store,
        Client(),
        payload,
        content_image_loader=lambda source: source.encode("utf-8"),
        content_image_inspector=lambda data: (
            ("image.qr_code",)
            if b"qr-risk" in data or b"taobao" in data
            else ()
        ),
    )

    assert prepared["title"] == "Apple iPhone 15 手机壳"
    assert prepared["short_title"] == "手机壳"
    assert prepared["desc_info"]["desc"] == "材质安全"
    assert prepared["attrs"] == [
        {"attr_key": "面料材质", "attr_value": "聚酯纤维"},
    ]
    assert prepared["skus"][0]["sku_attrs"] == [
        {"attr_key": "颜色", "attr_value": "黑色"},
        {"attr_key": "尺码", "attr_value": "M"},
    ]
    assert prepared["head_imgs"] == [
        "https://mmecimage.cn/head-safe-1.jpg",
        "https://mmecimage.cn/head-safe-2.jpg",
        "https://mmecimage.cn/head-safe-3.jpg",
    ]
    assert prepared["desc_info"]["imgs"] == [
        "https://mmecimage.cn/desc-safe.jpg",
    ]


def test_update_path_sanitizes_before_remote_write(
    tmp_path,
    monkeypatch,
) -> None:
    database = Database(tmp_path / "update-content-safety.db")
    store_id = database.add_store(
        "编辑安全店",
        "wx-update-content-safety",
        "secret",
    )
    store = database.get_store(store_id)
    assert store
    service = SyncService(database)
    monkeypatch.setattr(
        service,
        "_require_platform_write",
        lambda *_args, **_kwargs: None,
    )
    updates = []
    uploads = []
    image_bytes = {
        "https://source.example/head-1.jpg": b"head-one-safe",
        "https://source.example/head-2.jpg": b"head-two-safe",
        "https://source.example/head-3.jpg": b"head-three-safe",
        "https://source.example/detail-1.jpg": b"detail-one-safe",
        "https://source.example/detail-2.jpg": b"detail-two-safe",
        "https://source.example/sku-thumb.jpg": b"sku-thumb-safe",
        "https://source.example/sku-image.jpg": b"sku-image-safe",
        "https://source.example/sku-image-url.jpg": b"sku-url-safe",
        "https://source.example/sku-image-slot.jpg": b"sku-slot-safe",
    }
    mutable_images = dict(image_bytes)

    def load_image(source):
        data = mutable_images[source]
        if source.endswith("head-1.jpg"):
            mutable_images[source] = b"changed-after-inspection"
        return data

    monkeypatch.setattr(
        "weimi_manager.service.load_image_source_bytes",
        load_image,
    )
    monkeypatch.setattr(
        "weimi_manager.service.inspect_image_bytes",
        lambda _data: (),
    )

    class Client(WechatShopClient):
        def __init__(self):
            pass

        def upload_product_image(self, path):
            uploads.append(Path(path).read_bytes())
            return f"https://controlled.example/{len(uploads)}.jpg"

        def update_product(self, payload):
            updates.append(payload)
            return {"errcode": 0}

    result = service.product_write(
        store,
        "update",
        {
            "title": "淘宝同款 Apple 手机壳",
            "short_title": "PDD货源 手机壳",
            "head_imgs": [
                "https://source.example/head-1.jpg",
                "https://source.example/head-2.jpg",
                "https://source.example/head-3.jpg",
            ],
            "desc_info": {
                "desc": "1688批发，材质安全",
                "imgs": [
                    "https://source.example/detail-1.jpg",
                    "https://source.example/detail-2.jpg",
                ],
            },
            "attrs": [],
            "skus": [
                {
                    "sale_price": 1990,
                    "stock_num": 8,
                    "sku_attrs": [
                        {"attr_key": "颜色", "attr_value": "黑色"},
                    ],
                    "thumb_url": "https://source.example/sku-thumb.jpg",
                    "image": "https://source.example/sku-image.jpg",
                    "image_url": (
                        "https://source.example/sku-image-url.jpg"
                    ),
                    "sku_image": (
                        "https://source.example/sku-image-slot.jpg"
                    ),
                }
            ],
        },
        client=Client(),
    )

    assert updates[0]["title"] == "Apple 手机壳"
    assert updates[0]["short_title"] == "手机壳"
    assert updates[0]["desc_info"]["desc"] == "材质安全"
    assert updates[0]["head_imgs"] == [
        "https://controlled.example/1.jpg",
        "https://controlled.example/2.jpg",
        "https://controlled.example/3.jpg",
    ]
    assert updates[0]["desc_info"]["imgs"] == [
        "https://controlled.example/4.jpg",
        "https://controlled.example/5.jpg",
    ]
    assert updates[0]["skus"][0]["thumb_url"] == (
        "https://controlled.example/6.jpg"
    )
    assert updates[0]["skus"][0]["image"] == (
        "https://controlled.example/7.jpg"
    )
    assert updates[0]["skus"][0]["image_url"] == (
        "https://controlled.example/8.jpg"
    )
    assert updates[0]["skus"][0]["sku_image"] == (
        "https://controlled.example/9.jpg"
    )
    assert uploads == list(image_bytes.values())
    assert "source.example" not in str(updates[0])
    assert result["image_audit"]
    assert {
        key
        for entry in result["image_audit"]
        for key in entry
    } == {
        "slot",
        "source_url",
        "bytes_sha256",
        "controlled_url",
    }


def test_empty_skus_are_rejected_for_add_and_update(
    tmp_path,
    monkeypatch,
) -> None:
    database = Database(tmp_path / "empty-skus.db")
    store_id = database.add_store(
        "空SKU店",
        "wx-empty-skus",
        "secret",
    )
    store = database.get_store(store_id)
    assert store
    service = SyncService(database)
    monkeypatch.setattr(
        service,
        "_require_platform_write",
        lambda *_args, **_kwargs: None,
    )
    remote_writes = []

    class Client:
        @staticmethod
        def fetch_category_product_rule(
            _category_id,
            brand_id="2100000000",
            release_mode=0,
        ):
            return {"info": {"name": "手机壳"}, "product_attr_list": []}

        @staticmethod
        def add_product(payload):
            remote_writes.append(payload)
            return {"errcode": 0, "product_id": "BAD"}

        @staticmethod
        def update_product(payload):
            remote_writes.append(payload)
            return {"errcode": 0}

    add_payload = {
        "title": "安全商品",
        "short_title": "安全商品",
        "desc_info": {
            "desc": "材质安全",
            "imgs": ["https://mmecimage.cn/detail.jpg"],
        },
        "head_imgs": [
            "https://mmecimage.cn/1.jpg",
            "https://mmecimage.cn/2.jpg",
            "https://mmecimage.cn/3.jpg",
        ],
        "cats_v2": [{"cat_id": "6117"}],
        "attrs": [],
        "skus": [],
        "express_info": {
            "template_id": "freight-1",
            "weight": 1000,
            "express_type": 0,
        },
        "after_sale_info": {
            "after_sale_address_id": "address-1",
        },
    }
    with pytest.raises(UploadPreflightError, match="SKU"):
        service.product_write(
            store,
            "add",
            add_payload,
            client=Client(),
        )
    with pytest.raises(UploadPreflightError, match="SKU"):
        service.product_write(
            store,
            "update",
            {
                "title": "安全商品",
                "short_title": "安全商品",
                "desc_info": {"desc": "材质安全"},
                "attrs": [],
                "skus": [],
            },
            client=Client(),
        )
    assert remote_writes == []


def test_unsafe_images_use_safe_defaults_or_fail_when_insufficient(
    tmp_path,
) -> None:
    database = Database(tmp_path / "content-image-fallback.db")
    store_id = database.add_store(
        "安全图片店",
        "wx-content-image-fallback",
        "secret",
    )
    store = database.get_store(store_id)
    assert store

    class Client:
        @staticmethod
        def fetch_category_product_rule(
            category_id,
            brand_id="2100000000",
            release_mode=0,
        ):
            return {
                "info": {"name": "手机壳"},
                "product_attr_list": [
                    {
                        "name": "面料材质",
                        "type_v2": "string",
                    }
                ],
            }

    base_payload = {
        "title": "Apple 手机壳",
        "short_title": "手机壳",
        "desc_info": {
            "desc": "材质安全",
            "imgs": ["https://mmecimage.cn/desc-safe.jpg"],
        },
        "brand_id": "2100000000",
        "cats_v2": [{"cat_id": "6117"}],
        "attrs": [],
        "head_imgs": [
            "https://mmecimage.cn/head-safe-1.jpg",
            "https://mmecimage.cn/head-safe-2.jpg",
            "https://mmecimage.cn/head-risky.jpg",
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
        "express_info": {
            "template_id": "freight-1",
            "weight": 1000,
            "express_type": 0,
        },
        "after_sale_info": {
            "after_sale_address_id": "address-1",
        },
    }
    loader = lambda source: source.encode("utf-8")
    inspector = lambda data: (
        ("image.qr_code",)
        if b"risky" in data or b"bad-default" in data
        else ()
    )
    service = SyncService(database)
    service._product_payload_defaults_cache[store.id] = {
        "head_imgs": ["https://mmecimage.cn/safe-default.jpg"],
        "desc_info": {
            "imgs": ["https://mmecimage.cn/safe-default-detail.jpg"],
        },
    }

    prepared = service._prepare_product_payload(
        store,
        Client(),
        base_payload,
        content_image_loader=loader,
        content_image_inspector=inspector,
    )
    assert prepared["head_imgs"] == [
        "https://mmecimage.cn/head-safe-1.jpg",
        "https://mmecimage.cn/head-safe-2.jpg",
        "https://mmecimage.cn/safe-default.jpg",
    ]

    service._product_payload_defaults_cache[store.id] = {
        "head_imgs": ["https://mmecimage.cn/bad-default.jpg"],
        "desc_info": {
            "imgs": ["https://mmecimage.cn/safe-default-detail.jpg"],
        },
    }
    with pytest.raises(
        UploadPreflightError,
        match="安全图片仅 2 张",
    ):
        service._prepare_product_payload(
            store,
            Client(),
            base_payload,
            content_image_loader=loader,
            content_image_inspector=inspector,
        )


def test_local_image_inspection_detects_qr_and_platform_text() -> None:
    from PIL import Image

    stream = io.BytesIO()
    Image.new("RGB", (64, 64), "white").save(stream, format="PNG")
    image_bytes = stream.getvalue()

    class QrDetector:
        def detectAndDecodeMulti(self, _array):
            return (["https://detail.taobao.com/item.htm?id=1"], [], None)

        def detect(self, _array):
            return ("", None, None)

    class NoTextOcr:
        def __call__(self, _array):
            return ([], 0.0)

    assert inspect_image_bytes(
        image_bytes,
        qr_detector=QrDetector(),
        ocr_reader=NoTextOcr(),
    ) == ("image.qr_code",)

    class NoQrDetector:
        def detectAndDecodeMulti(self, _array):
            return ([], [], None)

        def detect(self, _array):
            return ("", None, None)

    class PlatformTextOcr:
        def __call__(self, _array):
            return ([([], "淘宝同款", 0.99)], 0.1)

    assert inspect_image_bytes(
        image_bytes,
        qr_detector=NoQrDetector(),
        ocr_reader=PlatformTextOcr(),
    ) == ("image.third_party_text",)


def test_local_image_inspection_detects_real_qr_payload_when_encoder_available() -> None:
    import cv2
    from PIL import Image

    if not hasattr(cv2, "QRCodeEncoder"):
        pytest.skip("当前 OpenCV 构建不提供测试用 QR 编码器")
    qr = cv2.QRCodeEncoder.create().encode(
        "https://detail.taobao.com/item.htm?id=1"
    )
    qr = cv2.resize(qr, (132, 132), interpolation=cv2.INTER_NEAREST)
    stream = io.BytesIO()
    Image.fromarray(qr).save(stream, format="PNG")

    class NoTextOcr:
        def __call__(self, _array):
            return ([], 0.0)

    assert inspect_image_bytes(
        stream.getvalue(),
        qr_detector=cv2.QRCodeDetector(),
        ocr_reader=NoTextOcr(),
    ) == ("image.qr_code",)


def test_real_local_ocr_detects_visible_platform_text() -> None:
    from pathlib import Path

    from PIL import Image, ImageDraw, ImageFont

    font_path = Path(r"C:\Windows\Fonts\arial.ttf")
    if not font_path.exists():
        pytest.skip("测试环境缺少可用的 ASCII 字体")
    image = Image.new("RGB", (700, 140), "white")
    ImageDraw.Draw(image).text(
        (30, 30),
        "TAOBAO HOT SALE",
        font=ImageFont.truetype(str(font_path), 56),
        fill="black",
    )
    stream = io.BytesIO()
    image.save(stream, format="PNG")

    assert inspect_image_bytes(stream.getvalue()) == (
        "image.third_party_text",
    )
