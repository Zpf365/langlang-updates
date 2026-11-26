from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from weimi_manager.shop_private import ShopPrivateClient
from weimi_manager.service import SyncService
from weimi_manager.source_1688 import (
    is_1688_official_url,
    normalize_1688_capture,
    normalized_1688_row,
    parse_1688_links,
    rank_category_candidates,
)
from weimi_manager.ui.upload_page import UploadPage, _required_category_ids

SAMPLE_RESPONSE = {
    "data": {
        "offerId": "1098691125768",
        "subject": "2026夏季女装短袖T恤",
        "categoryPath": "女装 / 上衣 / T恤",
        "imageList": [
            "https://cbu01.alicdn.com/main-1.jpg",
            "https://cbu01.alicdn.com/main-2.jpg",
        ],
        "skuInfos": [
            {
                "skuId": "SKU-RED-M",
                "price": 2599,
                "stock": 15,
                "specAttrs": "颜色:红色;尺码:M",
            },
            {
                "skuId": "SKU-BLUE-M",
                "price": 2699,
                "stock": 8,
                "specAttrs": "颜色:蓝色;尺码:M",
            },
        ],
        "detailImages": [
            "https://cbu01.alicdn.com/detail-1.jpg",
            "https://cbu01.alicdn.com/detail-2.jpg",
            "https://cbu01.alicdn.com/detail-3.jpg",
        ],
    }
}

REAL_PACKAGE_SHAPE = {
    "productId": "740782436970",
    "productName": "加绒保暖女鞋",
    "categoryId": "10170",
    "categoryName": "女式棉鞋",
    "categoryPath": "",
    "productUrl": "https://detail.1688.com/offer/740782436970.html",
    "itemImgs": [
        "https://cbu01.alicdn.com/img/ibank/main-1.jpg",
        "https://cbu01.alicdn.com/img/ibank/main-2.jpg",
        "https://cbu01.alicdn.com/img/ibank/main-3.jpg",
    ],
    "desc": (
        '<img src="https://cbu01.alicdn.com/img/ibank/detail-1.jpg">'
        '<img src="https://cbu01.alicdn.com/img/ibank/detail-2.jpg">'
        '<img src="https://cbu01.alicdn.com/img/ibank/detail-3.jpg">'
    ),
    "attribute": [
        {"key": "鞋面材质", "value": "PU"},
        {"key": "鞋底材质", "value": "橡胶"},
    ],
    "specs": {
        "颜色": {
            "3216:6013107": [
                "米色",
                "https://cbu01.alicdn.com/img/ibank/sku-color.jpg",
            ]
        },
        "尺码": {
            "450:6605532": ["36", ""],
        },
    },
    "skus": [
        {
            "quantity": 21,
            "price": "2030",
            "skuId": 5283937279196,
            "id": "3216:6013107;450:6605532",
        }
    ],
}


class Source1688Tests(unittest.TestCase):
    def test_official_1688_outbound_whitelist_blocks_third_party_hosts(
        self,
    ) -> None:
        for url in (
            "https://detail.1688.com/offer/1098691125768.html",
            "https://h5api.m.1688.com/h5/mtop.1688.offer.get/1.0/",
            "https://cbu01.alicdn.com/img/main.jpg",
            "data:image/png;base64,AAAA",
            "blob:https://detail.1688.com/example",
        ):
            self.assertTrue(is_1688_official_url(url), url)
        for url in (
            "https://example.com/api/parse",
            "https://api.third-party.test/1688",
            "https://1688.com.evil.example/offer/123",
            "ftp://detail.1688.com/offer/123.html",
        ):
            self.assertFalse(is_1688_official_url(url), url)

        source = (
            Path(__file__).resolve().parents[1]
            / "weimi_manager"
            / "source_1688.py"
        ).read_text(encoding="utf-8")
        self.assertIn('page.route("**/*", route_official)', source)
        self.assertIn(
            "is_1688_official_url(route.request.url)",
            source,
        )
        self.assertNotIn("requests.", source)
        self.assertNotIn("httpx.", source)

    def test_link_parser_accepts_only_1688_and_deduplicates(self) -> None:
        links = parse_1688_links(
            "\n".join(
                [
                    "https://detail.1688.com/offer/1098691125768.html",
                    "https://detail.1688.com/offer/1098691125768.html?sk=consign",
                    "https://example.com/offer/123456789.html",
                    "https://m.1688.com/offer/222222222.html",
                ]
            )
        )
        self.assertEqual(
            [link.offer_id for link in links],
            ["1098691125768", "222222222"],
        )

    def test_normalizer_extracts_sku_images_and_raw_detail_order(self) -> None:
        payload = normalize_1688_capture(
            {
                "offer_id": "1098691125768",
                "url": "https://detail.1688.com/offer/1098691125768.html",
                "responses": [SAMPLE_RESPONSE],
                "dom": {},
            },
            skip_detail_images=2,
        )
        self.assertEqual(payload["title"], "2026夏季女装短袖T恤")
        self.assertEqual(payload["category_name"], "女装 / 上衣 / T恤")
        self.assertEqual(len(payload["skus"]), 2)
        self.assertEqual(payload["skus"][0]["sale_price"], 2599)
        self.assertEqual(payload["skus"][0]["stock_num"], 15)
        self.assertEqual(payload["detail_skip_first"], 2)
        self.assertEqual(
            payload["desc_info"]["imgs"],
            [
                "https://cbu01.alicdn.com/detail-1.jpg",
                "https://cbu01.alicdn.com/detail-2.jpg",
                "https://cbu01.alicdn.com/detail-3.jpg",
            ],
        )

    def test_upload_payload_skips_first_two_1688_detail_images_once(self) -> None:
        normalized = normalize_1688_capture(
            {
                "offer_id": "1098691125768",
                "url": "https://detail.1688.com/offer/1098691125768.html",
                "responses": [SAMPLE_RESPONSE],
                "dom": {},
            },
            skip_detail_images=2,
        )
        page = object.__new__(UploadPage)
        result = page._upload_payload(
            {
                "title": normalized["title"],
                "price": 2599,
                "stock": 15,
                "payload": normalized,
                "source_product_id": "1098691125768",
            },
            {
                "price_multiplier": 1,
                "price_offset": 0,
                "stock": 100,
                "trim_detail": 0,
            },
        )
        self.assertEqual(
            result["desc_info"]["imgs"],
            ["https://cbu01.alicdn.com/detail-3.jpg"],
        )
        self.assertTrue(result["_auto_category_required"])
        self.assertNotIn("detail_skip_first", result)

    def test_category_ranker_prefers_matching_clothing_path(self) -> None:
        ranked = rank_category_candidates(
            "女装 上衣 短袖 T恤",
            [
                {
                    "category_id": "101",
                    "name": "T恤",
                    "path": "女装 / 上衣 / T恤",
                    "id_path": ["10", "20", "101"],
                },
                {
                    "category_id": "202",
                    "name": "鞋",
                    "path": "鞋靴 / 女鞋 / 休闲鞋",
                    "id_path": ["30", "40", "202"],
                },
            ],
        )
        self.assertEqual(ranked[0]["category_id"], "101")
        self.assertGreater(ranked[0]["match_score"], 2)

    def test_auto_category_uses_matched_tree_path(self) -> None:
        page = object.__new__(UploadPage)
        page.db = object()
        page._category_catalog_cache = {}
        catalog = [
            {
                "category_id": "101",
                "name": "T恤",
                "path": "女装 / 上衣 / T恤",
                "id_path": ["10", "20", "101"],
            }
        ]
        store = SimpleNamespace(id=7, name="测试店铺")
        with mock.patch(
            "weimi_manager.ui.upload_page.ShopPrivateClient",
            return_value=SimpleNamespace(
                category_catalog=lambda: catalog
            ),
        ):
            result = page._auto_match_category(
                store,
                {
                    "platform": "1688",
                    "title": "女装短袖T恤",
                    "category_hint": "女装 / 上衣 / T恤",
                    "head_imgs": ["https://cbu01.alicdn.com/main.jpg"],
                    "skus": [{"sale_price": 2599, "stock_num": 10}],
                },
                {},
            )
        self.assertNotIn("cats", result)
        self.assertEqual(
            result["cats_v2"],
            [
                {"cat_id": "10"},
                {"cat_id": "20"},
                {"cat_id": "101"},
            ],
        )
        self.assertEqual(result["category_apply_ids"], ["101"])

    def test_auto_category_uses_full_cats_v2_for_four_level_path(
        self,
    ) -> None:
        page = object.__new__(UploadPage)
        page.db = object()
        page._category_catalog_cache = {}
        catalog = [
            {
                "category_id": "547711",
                "name": "保暖棉鞋",
                "path": "鞋靴 / 女鞋 / 棉鞋 / 保暖棉鞋",
                "id_path": [
                    "10000173",
                    "10000175",
                    "10000673",
                    "547711",
                ],
            }
        ]
        store = SimpleNamespace(id=7, name="三级类目测试店")
        with mock.patch(
            "weimi_manager.ui.upload_page.ShopPrivateClient",
            return_value=SimpleNamespace(
                category_catalog=lambda: catalog
            ),
        ):
            result = page._auto_match_category(
                store,
                {
                    "platform": "1688",
                    "title": "女士保暖棉鞋",
                    "category_hint": "鞋靴 / 女鞋 / 棉鞋 / 保暖棉鞋",
                    "head_imgs": ["https://cbu01.alicdn.com/main.jpg"],
                    "skus": [{"sale_price": 2599, "stock_num": 10}],
                },
                {},
            )
        self.assertNotIn(
            "cats",
            result,
            "新类目树只提交cats_v2，避免旧三级字段冲突",
        )
        self.assertEqual(
            result["cats_v2"],
            [
                {"cat_id": "10000173"},
                {"cat_id": "10000175"},
                {"cat_id": "10000673"},
                {"cat_id": "547711"},
            ],
        )
        self.assertEqual(result["category_apply_ids"], ["547711"])

    def test_sync_service_adds_official_category_path_before_upload(
        self,
    ) -> None:
        service = SyncService(object())
        store = SimpleNamespace(id=7, name="上货类目店铺")
        catalog = [
            {
                "category_id": "547711",
                "name": "保暖棉鞋",
                "path": "鞋靴 / 女鞋 / 棉鞋 / 保暖棉鞋",
                "id_path": [
                    "10000173",
                    "10000175",
                    "10000673",
                    "547711",
                ],
            }
        ]
        with mock.patch(
            "weimi_manager.service.ShopPrivateClient",
            return_value=SimpleNamespace(
                category_catalog=lambda: catalog,
            ),
        ):
            result = service._ensure_product_category(
                store,
                {
                    "title": "女士冬季保暖加厚棉鞋",
                    "category_hint": "保暖棉鞋",
                },
            )
        self.assertEqual(
            result["cats_v2"],
            [
                {"cat_id": "10000173"},
                {"cat_id": "10000175"},
                {"cat_id": "10000673"},
                {"cat_id": "547711"},
            ],
        )
        self.assertEqual(result["category_path"], "鞋靴 / 女鞋 / 棉鞋 / 保暖棉鞋")

    def test_category_open_uses_leaf_id_instead_of_all_path_nodes(self) -> None:
        payload = {
            "cats": [
                {"cat_id": "10"},
                {"cat_id": "20"},
                {"cat_id": "101"},
            ],
            "category_apply_ids": ["101"],
        }
        self.assertEqual(_required_category_ids(payload), ["101"])

    def test_category_candidate_uses_compliant_semantic_match_before_price(
        self,
    ) -> None:
        service = SyncService(object())
        store = SimpleNamespace(id=7, name="候选类目店铺")
        catalog = [
            {
                "category_id": "EXACT",
                "name": "保暖棉鞋",
                "path": "鞋靴 / 女鞋 / 棉鞋 / 保暖棉鞋",
                "id_path": ["100", "200", "EXACT"],
            },
            {
                "category_id": "CHEAPER",
                "name": "加厚棉鞋",
                "path": "鞋靴 / 女鞋 / 棉鞋 / 加厚棉鞋",
                "id_path": ["100", "200", "CHEAPER"],
            },
            {
                "category_id": "UNRELATED",
                "name": "手机壳",
                "path": "数码 / 手机配件 / 手机壳",
                "id_path": ["900", "UNRELATED"],
            },
        ]

        class PrivateClient:
            def category_catalog(self):
                return catalog

            def category_application_checks(self, category_ids):
                return {
                    category_id: {
                        "state": (
                            "deposit_required"
                            if category_id == "EXACT"
                            else "not_applied"
                        ),
                        "label": (
                            "保证金不足"
                            if category_id == "EXACT"
                            else "未申请"
                        ),
                        "reason": (
                            "需补缴 200000 分"
                            if category_id == "EXACT"
                            else ""
                        ),
                        "eligible": category_id != "EXACT",
                        "deposit_amount": (
                            200000
                            if category_id == "EXACT"
                            else 10000
                        ),
                        "deposit_shortfall": (
                            200000
                            if category_id == "EXACT"
                            else 0
                        ),
                    }
                    for category_id in category_ids
                }

        with mock.patch(
            "weimi_manager.service.ShopPrivateClient",
            return_value=PrivateClient(),
        ):
            result = service._ensure_product_category(
                store,
                {
                    "title": "女士冬季保暖加厚棉鞋",
                    "category_hint": "保暖棉鞋",
                },
            )

        assert result["category_apply_ids"] == ["CHEAPER"]
        assert result["_category_selected"]["application_state"] == "not_applied"
        assert result["_category_selected"]["deposit_amount"] == 10000
        assert {
            item["category_id"]
            for item in result["_category_candidates"]
        } >= {"EXACT", "CHEAPER"}
        assert "UNRELATED" not in result["category_apply_ids"]

    def test_1688_data_package_uses_same_normalizer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "1688-package.json"
            path.write_text(
                json.dumps(SAMPLE_RESPONSE["data"], ensure_ascii=False),
                encoding="utf-8",
            )
            rows = UploadPage._parse_package(None, path)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["platform"], "1688")
        self.assertEqual(rows[0]["source_product_id"], "1098691125768")
        self.assertEqual(rows[0]["price"], 2599)
        self.assertEqual(rows[0]["stock"], 15)
        self.assertEqual(rows[0]["title"], "2026夏季女装短袖T恤")
        self.assertEqual(
            normalized_1688_row(rows[0]["payload"])["platform"],
            "1688",
        )

    def test_real_1688_package_shape_parses_and_skips_two_details(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "740782436970.txt"
            path.write_text(
                json.dumps(REAL_PACKAGE_SHAPE, ensure_ascii=False),
                encoding="utf-8",
            )
            rows = UploadPage._parse_package(None, path)
        self.assertEqual(rows[0]["platform"], "1688")
        self.assertEqual(rows[0]["source_product_id"], "740782436970")
        self.assertEqual(len(rows[0]["payload"]["head_imgs"]), 3)
        self.assertEqual(len(rows[0]["payload"]["attrs"]), 2)
        self.assertEqual(rows[0]["payload"]["detail_skip_first"], 2)
        prepared = object.__new__(UploadPage)._upload_payload(
            rows[0],
            {
                "price_multiplier": 1.2,
                "price_offset": 0,
                "stock": 100,
                "trim_detail": 0,
            },
        )
        self.assertEqual(
            prepared["desc_info"]["imgs"],
            ["https://cbu01.alicdn.com/img/ibank/detail-3.jpg"],
        )
        self.assertEqual(prepared["skus"][0]["sale_price"], 2436)

    def test_shop_category_catalog_keeps_complete_id_path(self) -> None:
        client = object.__new__(ShopPrivateClient)
        client._get = lambda _path: {
            "productCats": [
                {
                    "value": "10",
                    "label": "女装",
                    "leaf": False,
                    "state": 0,
                    "options": [
                        {
                            "value": "20",
                            "label": "上衣",
                            "leaf": False,
                            "state": 0,
                            "options": [
                                {
                                    "value": "101",
                                    "label": "T恤",
                                    "leaf": True,
                                    "state": 0,
                                }
                            ],
                        }
                    ],
                }
            ]
        }
        catalog = client.category_catalog()
        self.assertEqual(catalog[0]["category_id"], "101")
        self.assertEqual(catalog[0]["id_path"], ["10", "20", "101"])
        self.assertEqual(catalog[0]["path"], "女装 / 上衣 / T恤")


if __name__ == "__main__":
    unittest.main()
