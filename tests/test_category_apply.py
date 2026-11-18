from __future__ import annotations

import unittest

from weimi_manager.shop_private import ShopPrivateClient


class CategoryApplyTests(unittest.TestCase):
    def test_category_candidates_follow_deposit_cap_and_requirements(self) -> None:
        client = object.__new__(ShopPrivateClient)
        client._get = lambda _path, _params=None: {
            "code": 0,
            "productCats": [
                {
                    "value": 1,
                    "label": "一级",
                    "leaf": False,
                    "options": [
                        {
                            "value": 101,
                            "label": "可申请",
                            "leaf": True,
                            "state": 0,
                            "isForbidForSpecialization": False,
                        },
                        {
                            "value": 102,
                            "label": "需资质",
                            "leaf": True,
                            "state": 0,
                            "isForbidForSpecialization": False,
                        },
                        {
                            "value": 103,
                            "label": "超预算",
                            "leaf": True,
                            "state": 0,
                            "isForbidForSpecialization": False,
                        },
                    ],
                }
            ],
        }

        def post(path, body):
            if path.endswith("getCategoryRelationList"):
                return {
                    "code": 0,
                    "categoryRelationList": [{"id": "104"}],
                }
            ids = [
                str(item["categoryId"])
                for item in body.get("categoryList", [])
            ]
            if path.endswith("getCategoryDeposit"):
                return {
                    "code": 0,
                    "depositBalance": "200000",
                    "categoryDepositList": [
                        {
                            "category": {"categoryId": category_id},
                            "requiredDeposit": (
                                "100000"
                                if category_id != "103"
                                else "150000"
                            ),
                            "amountToPay": "0",
                        }
                        for category_id in ids
                    ],
                }
            if path.endswith("checkNeedQualificationByCategoryIds"):
                return {
                    "code": 0,
                    "data": {
                        "canMultiSelect": True,
                        "categoyrRequirementMap": {
                            category_id: {}
                            for category_id in ids
                        },
                        "needQualificationIdList": ["102"],
                    },
                }
            raise AssertionError(path)

        client._post = post
        rows = client.category_candidates(100000)
        self.assertEqual(
            [(row["category_id"], row["status"]) for row in rows],
            [
                ("101", "可申请"),
                ("102", "需资质"),
            ],
        )
        self.assertEqual(rows[0]["path"], "一级 / 可申请")
        self.assertEqual(rows[0]["required_deposit"], 100000)

    def test_batch_add_categories_uses_official_private_endpoint(self) -> None:
        client = object.__new__(ShopPrivateClient)
        calls = []

        def post(path, body):
            calls.append((path, body))
            return {"code": 0, "data": {"success": True}}

        client._post = post
        client.batch_add_categories(["101", "102"])
        self.assertEqual(
            calls,
            [
                (
                    "/shop-faas/mmchannelstradecategory/cgi/"
                    "category/batchAddCategory",
                    {"categoryIdList": [101, 102]},
                )
            ],
        )

    def test_category_relation_statuses_distinguish_pending_and_effective(
        self,
    ) -> None:
        client = object.__new__(ShopPrivateClient)

        def post(_path, body):
            if body["status"] == 1:
                return {
                    "code": 0,
                    "categoryRelationList": [{"id": "101"}],
                }
            return {
                "code": 0,
                "categoryRelationList": [{"id": "102"}],
            }

        client._post = post
        statuses = client.category_relation_statuses(
            ["101", "102", "103"]
        )
        self.assertEqual(
            statuses,
            {
                "101": "已生效",
                "102": "审核中",
                "103": "未查到",
            },
        )


if __name__ == "__main__":
    unittest.main()
