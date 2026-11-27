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

    def test_category_deposit_check_uses_requested_business_type(
        self,
    ) -> None:
        client = object.__new__(ShopPrivateClient)
        client._get = lambda _path: {
            "code": 0,
            "productCats": [
                {
                    "value": 401,
                    "label": "测试类目",
                    "leaf": True,
                    "state": 0,
                    "isForbidForSpecialization": False,
                }
            ],
        }
        deposit_payloads = []

        def post(path, body):
            if path.endswith("getCategoryRelationList"):
                return {"code": 0, "categoryRelationList": []}
            if path.endswith("getCategoryDeposit"):
                deposit_payloads.append(body)
                return {
                    "code": 0,
                    "depositBalance": "0",
                    "categoryDepositList": [
                        {
                            "category": {"categoryId": "401"},
                            "requiredDeposit": "100000",
                            "amountToPay": "100000",
                            "isSupportDepositFreeTrial": True,
                        }
                    ],
                }
            if path.endswith("checkNeedQualificationByCategoryIds"):
                return {"code": 0, "data": {}}
            raise AssertionError(path)

        client._post = post
        check = client.category_application_checks(
            ["401"],
            business_type="individual",
        )["401"]

        self.assertEqual(
            deposit_payloads[0]["businessType"],
            "individual",
        )
        self.assertEqual(check["business_type"], "individual")
        self.assertTrue(check["deposit_free_trial"])

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

    def test_category_application_checks_classify_all_recovery_states(
        self,
    ) -> None:
        client = object.__new__(ShopPrivateClient)
        client._get = lambda _path: {
            "code": 0,
            "productCats": [
                {
                    "value": category_id,
                    "label": f"类目{category_id}",
                    "leaf": True,
                    "state": 0,
                    "isForbidForSpecialization": False,
                }
                for category_id in (101, 102, 103, 104, 105, 106)
            ],
        }

        def post(path, body):
            if path.endswith("getCategoryRelationList"):
                if body["status"] == 1:
                    return {
                        "code": 0,
                        "categoryRelationList": [{"id": "101"}],
                    }
                return {
                    "code": 0,
                    "categoryRelationList": [
                        {"id": "102"},
                        {
                            "id": "103",
                            "uneffectiveReason": "类目暂停新增",
                        },
                    ],
                }
            if path.endswith("getCategoryDeposit"):
                return {
                    "code": 0,
                    "categoryDepositList": [
                        {
                            "category": {"categoryId": category_id},
                            "amountToPay": (
                                "100"
                                if category_id == "105"
                                else "0"
                            ),
                            "depositBalance": (
                                "50"
                                if category_id == "105"
                                else "0"
                            ),
                            "requiredDeposit": "0",
                        }
                        for category_id in (
                            "101",
                            "102",
                            "103",
                            "104",
                            "105",
                            "106",
                        )
                    ],
                }
            if path.endswith("checkNeedQualificationByCategoryIds"):
                return {
                    "code": 0,
                    "data": {"needQualificationIdList": ["104"]},
                }
            raise AssertionError(path)

        client._post = post
        checks = client.category_application_checks(
            ["101", "102", "103", "104", "105", "106"]
        )

        self.assertEqual(checks["101"]["state"], "effective")
        self.assertEqual(checks["102"]["state"], "pending")
        self.assertEqual(checks["103"]["state"], "unavailable")
        self.assertEqual(
            checks["104"]["state"],
            "qualification_required",
        )
        self.assertEqual(checks["105"]["state"], "deposit_required")
        self.assertFalse(checks["105"]["eligible"])
        self.assertEqual(checks["105"]["required_deposit"], 0)
        self.assertEqual(checks["105"]["amount_to_pay"], 100)
        self.assertEqual(checks["105"]["deposit_balance"], 50)
        self.assertEqual(checks["105"]["deposit_shortfall"], 50)
        self.assertEqual(checks["106"]["state"], "not_applied")
        self.assertTrue(checks["106"]["eligible"])

    def test_unapplied_category_with_deposit_shortfall_is_blocked(
        self,
    ) -> None:
        client = object.__new__(ShopPrivateClient)
        client._get = lambda _path: {
            "code": 0,
            "productCats": [
                {
                    "value": 301,
                    "label": "源类目",
                    "leaf": True,
                    "state": 0,
                    "isForbidForSpecialization": False,
                }
            ],
        }

        def post(path, body):
            if path.endswith("getCategoryRelationList"):
                return {"code": 0, "categoryRelationList": []}
            if path.endswith("getCategoryDeposit"):
                return {
                    "code": 0,
                    "categoryDepositList": [
                        {
                            "category": {"categoryId": "301"},
                            "requiredDeposit": "100000",
                            "amountToPay": "100000",
                            "depositBalance": "0",
                        }
                    ],
                }
            if path.endswith("checkNeedQualificationByCategoryIds"):
                return {"code": 0, "data": {}}
            raise AssertionError(path)

        client._post = post
        check = client.category_application_checks(["301"])["301"]

        self.assertEqual(check["state"], "deposit_required")
        self.assertFalse(check["eligible"])
        self.assertEqual(check["required_deposit"], 100000)
        self.assertEqual(check["amount_to_pay"], 100000)
        self.assertEqual(check["deposit_balance"], 0)
        self.assertEqual(check["deposit_shortfall"], 100000)

    def test_operation_required_deposit_is_not_replaced_by_category_amount(
        self,
    ) -> None:
        client = object.__new__(ShopPrivateClient)
        client._get = lambda _path: {
            "code": 0,
            "productCats": [
                {
                    "value": 1713,
                    "label": "目标叶子类目",
                    "leaf": True,
                    "state": 0,
                    "isForbidForSpecialization": False,
                }
            ],
        }

        def post(path, body):
            if path.endswith("getCategoryRelationList"):
                return {"code": 0, "categoryRelationList": []}
            if path.endswith("getCategoryDeposit"):
                return {
                    "code": 0,
                    "depositBalance": "0",
                    "realDepositBalance": "0",
                    "operationRequiredDeposit": "0",
                    "categoryDepositList": [
                        {
                            "category": {"categoryId": "1713"},
                            "requiredDeposit": "200000",
                            "amountToPay": "200000",
                            "isSupportDepositFreeTrial": True,
                        }
                    ],
                }
            if path.endswith("checkNeedQualificationByCategoryIds"):
                return {"code": 0, "data": {}}
            raise AssertionError(path)

        client._post = post
        check = client.category_application_checks(["1713"])["1713"]

        self.assertEqual(check["state"], "not_applied")
        self.assertTrue(check["eligible"])
        self.assertEqual(check["required_deposit"], 200000)
        self.assertEqual(check["amount_to_pay"], 200000)
        self.assertEqual(check["operation_required_deposit"], 0)
        self.assertEqual(check["deposit_balance"], 0)
        self.assertEqual(check["real_deposit_balance"], 0)
        self.assertTrue(check["deposit_free_trial"])
        self.assertEqual(check["deposit_shortfall"], 0)

    def test_category_relation_reasons_are_not_collapsed_into_pending(
        self,
    ) -> None:
        client = object.__new__(ShopPrivateClient)
        client._get = lambda _path: {
            "code": 0,
            "productCats": [
                {
                    "value": category_id,
                    "label": f"类目{category_id}",
                    "leaf": True,
                    "state": 0,
                    "isForbidForSpecialization": False,
                }
                for category_id in (201, 202, 203, 204, 205)
            ],
        }

        def post(path, body):
            if path.endswith("getCategoryRelationList"):
                if body["status"] == 1:
                    return {"code": 0, "categoryRelationList": []}
                return {
                    "code": 0,
                    "categoryRelationList": [
                        {"id": "201"},
                        {
                            "id": "202",
                            "uneffectiveReason": "平台已拒绝申请",
                        },
                        {
                            "id": "203",
                            "uneffectiveReason": "缺少品牌授权资质",
                        },
                        {
                            "id": "204",
                            "uneffectiveReason": "保证金不足，请补缴",
                        },
                        {
                            "id": "205",
                            "uneffectiveReason": "类目暂停新增",
                        },
                    ],
                }
            if path.endswith("getCategoryDeposit"):
                return {"code": 0, "categoryDepositList": []}
            if path.endswith("checkNeedQualificationByCategoryIds"):
                return {"code": 0, "data": {}}
            raise AssertionError(path)

        client._post = post
        checks = client.category_application_checks(
            ["201", "202", "203", "204", "205"]
        )

        self.assertEqual(checks["201"]["state"], "pending")
        self.assertEqual(checks["202"]["state"], "rejected")
        self.assertEqual(
            checks["203"]["state"],
            "qualification_required",
        )
        self.assertEqual(checks["204"]["state"], "deposit_required")
        self.assertEqual(checks["205"]["state"], "unavailable")

    def test_category_application_checks_batch_large_category_trees(
        self,
    ) -> None:
        category_ids = [str(1000 + index) for index in range(401)]
        client = object.__new__(ShopPrivateClient)
        client._get = lambda _path: {
            "code": 0,
            "productCats": [
                {
                    "value": category_id,
                    "label": f"类目{category_id}",
                    "leaf": True,
                    "state": 0,
                }
                for category_id in category_ids
            ],
        }
        deposit_batches = []
        qualification_batches = []

        def post(path, body):
            if path.endswith("getCategoryRelationList"):
                return {"code": 0, "categoryRelationList": []}
            if path.endswith("getCategoryDeposit"):
                rows = body["categoryList"]
                deposit_batches.append(len(rows))
                return {
                    "code": 0,
                    "categoryDepositList": [
                        {
                            "category": {
                                "categoryId": row["categoryId"]
                            },
                            "requiredDeposit": "0",
                            "amountToPay": "0",
                            "depositBalance": "0",
                        }
                        for row in rows
                    ],
                }
            if path.endswith("checkNeedQualificationByCategoryIds"):
                qualification_batches.append(len(body["categoryIds"]))
                return {"code": 0, "data": {}}
            raise AssertionError(path)

        client._post = post
        checks = client.category_application_checks(category_ids)

        self.assertEqual(deposit_batches, [200, 200, 1])
        self.assertEqual(qualification_batches, [200, 200, 1])
        self.assertTrue(all(check["eligible"] for check in checks.values()))


if __name__ == "__main__":
    unittest.main()
