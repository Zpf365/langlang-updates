from __future__ import annotations

import json
from pathlib import Path

import pytest

from weimi_manager.ui.upload_page import (
    UploadPage,
    _normalize_wechat_package_payload,
)
from weimi_manager.db import Database
from weimi_manager.wechat_api import normalize_sku_sale_attrs
from weimi_manager.webui.controller import NextUiController


FIXTURE = (
    Path(__file__).parent
    / "fixtures"
    / "wechat_package_11_skus.json"
)


def test_real_wechat_package_shape_normalizes_to_upload_payload() -> None:
    rows = UploadPage._parse_package(None, FIXTURE)

    assert len(rows) == 1
    row = rows[0]
    payload = row["payload"]
    assert row["platform"] == "微信小店"
    assert row["source_product_id"] == "10000161946828"
    assert row["price"] == 4990
    assert row["stock"] == 21471
    assert row["stock_known"] is True
    assert len(payload["head_imgs"]) == 4
    assert len(payload["desc_info"]["imgs"]) == 16
    assert len(payload["skus"]) == 11
    assert len({sku["out_sku_id"] for sku in payload["skus"]}) == 11
    assert payload["_adapter_errors"] == []
    assert payload["_adapter_conflicts"] == []
    assert payload["cats"] == [
        {"cat_id": "10000132"},
        {"cat_id": "10000137"},
        {"cat_id": "6499"},
    ]

    raw_skus = {
        str(item["skuKey"]["skuId"]): item
        for item in json.loads(FIXTURE.read_text(encoding="utf-8"))[
            "_raw"
        ]["productSkus"]
    }
    for sku in payload["skus"]:
        source = raw_skus[sku["out_sku_id"]]
        info = source["productSkuInfo"]
        assert sku["sale_price"] == int(info["salePrice"])
        assert sku["stock_num"] == int(info["stockInfo"]["stockNum"])
        assert sku["sku_attrs"]
        assert sku["sku_attrs"][0]["attr_key"] == "颜色"

    mapped = UploadPage._upload_payload(None, row, {})
    assert len(mapped["head_imgs"]) == 4
    assert len(mapped["desc_info"]["imgs"]) == 16
    assert len(mapped["skus"]) == 11
    assert [
        sku["stock_num"] for sku in mapped["skus"]
    ] == [
        sku["stock_num"] for sku in payload["skus"]
    ]


def test_wechat_package_image_priority_and_missing_field_diagnostics() -> None:
    source = json.loads(FIXTURE.read_text(encoding="utf-8"))
    source["headImgs"] = ["https://top.example.test/headImgs.jpg"]
    assert _normalize_wechat_package_payload(source)["head_imgs"] == [
        "https://top.example.test/headImgs.jpg"
    ]

    source.pop("headImgs")
    source["headImg"] = ["https://top.example.test/headImg.jpg"]
    assert _normalize_wechat_package_payload(source)["head_imgs"] == [
        "https://top.example.test/headImg.jpg"
    ]

    source.pop("headImg")
    source["_raw"]["headImg"] = [
        "https://raw.example.test/headImg.jpg"
    ]
    assert _normalize_wechat_package_payload(source)["head_imgs"] == [
        "https://raw.example.test/headImg.jpg"
    ]

    source["_raw"]["headImg"] = []
    source["_raw"]["headImgInfos"] = []
    source["_raw"]["productHeadimgInfo"] = []
    source["specImgs"] = []
    source["_raw"]["productSkus"] = []
    source["_raw"]["skuList"] = {"skuInfo": []}
    source["skuSpecs"] = []
    missing = _normalize_wechat_package_payload(source)
    assert missing["head_imgs"] == []
    assert missing["skus"] == []
    assert any("headImg" in error for error in missing["_adapter_errors"])
    assert any("productSkus" in error for error in missing["_adapter_errors"])


def test_wechat_package_price_stock_conflicts_are_explicit() -> None:
    source = json.loads(FIXTURE.read_text(encoding="utf-8"))
    sku_id = source["_raw"]["productSkus"][0]["skuKey"]["skuId"]
    source["_raw"]["productSkus"][0]["productSkuInfo"]["salePrice"] = 12000
    normalized = _normalize_wechat_package_payload(source)

    assert any(
        str(sku_id) in conflict and "价格冲突" in conflict
        for conflict in normalized["_adapter_conflicts"]
    )
    selected = next(
        sku
        for sku in normalized["skus"]
        if sku["out_sku_id"] == str(sku_id)
    )
    assert selected["sale_price"] == 12000


def test_wechat_package_accepts_json_encoded_nested_containers() -> None:
    source = json.loads(FIXTURE.read_text(encoding="utf-8"))
    source["headImgs"] = json.dumps(
        ["https://images.example.test/a.jpg"],
        ensure_ascii=False,
    )
    source["_raw"]["productSkus"] = json.dumps(
        source["_raw"]["productSkus"],
        ensure_ascii=False,
    )
    source["_raw"]["skuList"] = json.dumps(
        source["_raw"]["skuList"],
        ensure_ascii=False,
    )
    source["_raw"]["productSaleAttrs"] = json.dumps(
        source["_raw"]["productSaleAttrs"],
        ensure_ascii=False,
    )
    source["skuSpecs"] = json.dumps(
        source["skuSpecs"],
        ensure_ascii=False,
    )

    normalized = _normalize_wechat_package_payload(source)

    assert normalized["head_imgs"] == [
        "https://images.example.test/a.jpg"
    ]
    assert len(normalized["skus"]) == 11
    assert all(sku["sku_attrs"] for sku in normalized["skus"])


def test_sku_sale_attribute_aliases_are_canonicalized_without_duplicate_keys() -> None:
    payload = {
        "skus": [
            {
                "out_sku_id": "sku-1",
                "sku_attrs": [
                    {"item_key": "颜色", "item_value": "红色"},
                    {"attr_key": "颜色", "attr_value": "红色"},
                    {"key": "尺码", "value": "M"},
                ],
            }
        ]
    }

    normalized = normalize_sku_sale_attrs(payload)

    assert normalized["skus"][0]["sku_attrs"] == [
        {"attr_key": "颜色", "attr_value": "红色"},
        {"attr_key": "尺码", "attr_value": "M"},
    ]


def test_sku_sale_attribute_conflict_is_reported_instead_of_silently_merged() -> None:
    payload = {
        "skus": [
            {
                "out_sku_id": "sku-conflict",
                "sku_attrs": [
                    {"attr_key": "颜色", "attr_value": "红色"},
                    {"item_key": "颜色", "item_value": "蓝色"},
                ],
            }
        ]
    }

    with pytest.raises(ValueError, match="颜色.*红色 / 蓝色"):
        normalize_sku_sale_attrs(payload)


def test_txt_and_json_are_detected_by_content_without_changing_txt(
    tmp_path: Path,
) -> None:
    json_path = tmp_path / "package.txt"
    json_path.write_text(
        json.dumps(
            {
                "products": [
                    {
                        "title": "TXT中的完整JSON",
                        "price": "9.90",
                        "stock": 8,
                        "payload": {
                            "platform": "微信小店",
                            "head_imgs": [
                                "https://example.test/a.jpg"
                            ],
                            "skus": [
                                {
                                    "sale_price": 990,
                                    "stock_num": 8,
                                }
                            ],
                        },
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    rows = UploadPage._parse_package(None, json_path)
    assert len(rows) == 1
    assert rows[0]["title"] == "TXT中的完整JSON"

    csv_path = tmp_path / "legacy.txt"
    csv_path.write_text(
        "title,platform,price,stock,payload\n"
        '旧版TXT含{}字符,淘宝,19.90,6,"{}"\n',
        encoding="utf-8",
    )
    legacy_rows = UploadPage._parse_package(None, csv_path)
    assert len(legacy_rows) == 1
    assert legacy_rows[0]["title"] == "旧版TXT含{}字符"
    assert legacy_rows[0]["platform"] == "淘宝"
    assert legacy_rows[0]["price"] == 1990
    assert legacy_rows[0]["stock"] == 6


def test_wechat_package_import_preflight_and_dedupe_use_same_entry(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "wechat-package.db")
    controller = NextUiController(database)
    content = FIXTURE.read_text(encoding="utf-8")
    files = [{"name": "10000161946828.json", "content": content}]

    imported = controller.dispatch(
        "upload.batch.import",
        {"files": files, "append": True},
    )
    repeated = controller.dispatch(
        "upload.batch.import",
        {"files": files, "append": True},
    )
    preflight = controller.dispatch(
        "upload.batch.preflight",
        {},
    )

    assert imported["imported"] == 1
    assert imported["parsed"] == 1
    assert imported["formats"] == ["微信 JSON 数据包"]
    assert repeated["imported"] == 0
    assert repeated["skipped"] == 1
    assert preflight["checked"] == 1
    assert preflight["ready"] == 1
    mapping = preflight["rows"][0]["mapping"]
    assert mapping["sku_count"] == 11
    assert mapping["head_image_count"] == 4
    assert mapping["detail_image_count"] == 16
    assert mapping["stock_total"] == 21471


def test_existing_raw_wechat_batch_can_be_reparsed_without_platform_write(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "wechat-reparse.db")
    controller = NextUiController(database)
    source = json.loads(FIXTURE.read_text(encoding="utf-8"))
    batch_id = "legacy-wechat-row"
    database.set_setting(
        "upload_batch",
        json.dumps(
            [
                {
                    "batch_id": batch_id,
                    "source_product_id": "10000161946828",
                    "title": source["title"],
                    "price": 0,
                    "stock": 21471,
                    "stock_known": True,
                    "platform": "微信小店",
                    "payload": source,
                    "raw": source,
                }
            ],
            ensure_ascii=False,
        ),
    )

    preview = controller.dispatch(
        "upload.batch.reparse",
        {"batch_ids": [batch_id]},
    )
    saved = controller._upload_batch_rows()[0]

    assert preview["batch_recoverable"] == 1
    assert preview["batch_blocked"] == 0
    assert preview["batch_preview"][0]["sku_count"] == 11
    assert preview["batch_preview"][0]["head_image_count"] == 4
    assert len(saved["payload"]["head_imgs"]) == 4
    assert len(saved["payload"]["skus"]) == 11
    assert saved["price"] == 4990
    assert not database.get_setting("upload_tasks", "")
