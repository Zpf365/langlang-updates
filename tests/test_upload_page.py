from __future__ import annotations

from datetime import datetime, timedelta
import json
import os
import tempfile
import threading
import time
import tkinter as tk
import unittest
import uuid
from pathlib import Path
from tkinter import ttk
from types import SimpleNamespace
from unittest.mock import Mock, patch

from weimi_manager.db import Database
from weimi_manager.models import Product, Store
from weimi_manager.ui.theme import apply_theme
from weimi_manager.ui.upload_page import (
    CategoryApprovalPending,
    CategoryAutoOpenError,
    TemplateRuleSkip,
    UploadPage,
    _definitive_upload_failure_message,
    _filter_batch_rows,
    _is_duplicate_upload,
    _normalize_duplicate_filter_mode,
    _normalize_source_payload,
    _recover_upload_tasks,
    _required_category_ids,
    _retry_upload_after_category_open,
    _ScrollableBody,
    _stores_matching_group_names,
    _upload_source_relation,
)
from weimi_manager.wechat_api import WechatApiError


class UploadPageContractTests(unittest.TestCase):
    def test_required_category_ids_only_submits_leaf_categories(self) -> None:
        self.assertEqual(
            _required_category_ids(
                {
                    "category_apply_ids": ["1000", "2000", "3001"],
                    "cats_v2": [
                        {"cat_id": "1000"},
                        {"cat_id": "2000"},
                        {"cat_id": "3001"},
                    ],
                }
            ),
            ["3001"],
        )
        self.assertEqual(
            _required_category_ids(
                {
                    "cats": [
                        {"cat_id": "100"},
                        {"cat_id": "200"},
                        {"cat_id": "301"},
                    ]
                }
            ),
            ["301"],
        )
        self.assertEqual(
            _required_category_ids(
                {"category_apply_ids": ["401", "402"]}
            ),
            ["401", "402"],
        )
        for platform in ("1688", "淘宝", "微信小店"):
            with self.subTest(platform=platform):
                self.assertEqual(
                    _required_category_ids(
                        {
                            "platform": platform,
                            "category_apply_ids": ["501", "502", "603"],
                            "cats_v2": [
                                {"cat_id": "501"},
                                {"cat_id": "502"},
                                {"cat_id": "603"},
                            ],
                        }
                    ),
                    ["603"],
                )

    def test_legacy_category_failures_enter_automatic_recovery_only_when_safe(
        self,
    ) -> None:
        complete_payload = {
            "title": "历史类目商品",
            "head_imgs": [
                "https://example.invalid/1.jpg",
                "https://example.invalid/2.jpg",
                "https://example.invalid/3.jpg",
            ],
            "skus": [{"sale_price": 100, "stock_num": 2}],
            "cats_v2": [
                {"cat_id": "1000"},
                {"cat_id": "2001"},
            ],
        }
        tasks = [
            {
                "id": "legacy-task",
                "items": [
                    {
                        "item_id": "legacy-failed",
                        "status": "failed",
                        "message": (
                            "商品未创建, 错误码:6600145 原因:无该类目权限"
                        ),
                        "payload": complete_payload,
                    },
                    {
                        "item_id": "legacy-waiting",
                        "status": "waiting_category",
                        "payload": {
                            "cats_v2": [
                                {"cat_id": "1000"},
                                {"cat_id": "2001"},
                            ]
                        },
                    },
                    {
                        "item_id": "legacy-error-code-only",
                        "status": "failed",
                        "message": "商品未创建",
                        "error_code": 6600145,
                    },
                    {
                        "item_id": "ordinary-failed",
                        "status": "failed",
                        "message": "商品信息格式错误",
                        "payload": complete_payload,
                    },
                    {
                        "item_id": "verification-pending",
                        "status": "verification_pending",
                        "message": "远端结果未知",
                        "payload": complete_payload,
                    },
                    {
                        "item_id": "already-created",
                        "status": "failed",
                        "message": "6600145",
                        "product_id": "WX-EXISTS",
                        "payload": complete_payload,
                    },
                ],
            }
        ]

        changed = _recover_upload_tasks(tasks)

        self.assertTrue(changed)
        (
            failed,
            waiting,
            error_code_only,
            ordinary_failed,
            verification_pending,
            already_created,
        ) = tasks[0]["items"]
        self.assertEqual(failed["status"], "waiting_category")
        self.assertEqual(failed["category_state"], "waiting")
        self.assertTrue(failed["category_auto_recovery"])
        self.assertFalse(failed["category_apply_submitted"])
        self.assertEqual(failed["category_ids"], ["2001"])
        self.assertEqual(waiting["category_state"], "waiting")
        self.assertEqual(waiting["category_ids"], ["2001"])
        self.assertTrue(waiting["category_apply_submitted"])
        self.assertEqual(error_code_only["category_state"], "failed")
        self.assertFalse(error_code_only["category_reprocess_available"])
        self.assertEqual(ordinary_failed["status"], "failed")
        self.assertNotIn("category_state", ordinary_failed)
        self.assertEqual(
            verification_pending["status"],
            "verification_pending",
        )
        self.assertNotIn("category_state", verification_pending)
        self.assertEqual(already_created["status"], "failed")
        self.assertEqual(already_created["category_state"], "failed")
        self.assertFalse(already_created["category_reprocess_available"])
        self.assertEqual(tasks[0]["status"], "needs_verification")

    def test_1688_upload_keeps_source_code_and_creates_sku_mapping(
        self,
    ) -> None:
        payload = _normalize_source_payload(
            {
                "platform": "1688",
                "offer_id": "1098691125768",
                "source_url": (
                    "https://detail.1688.com/offer/1098691125768.html"
                ),
                "title": "上家测试商品",
                "head_imgs": ["https://example.test/main.jpg"],
                "skus": [
                    {
                        "out_sku_id": "1688-SKU-RED",
                        "sale_price": 1000,
                        "stock_num": 10,
                        "sku_attrs": [
                            {"attr_key": "颜色", "attr_value": "红色"}
                        ],
                    }
                ],
            }
        )
        self.assertEqual(payload["out_product_id"], "1098691125768")
        item = {
            "source_product_id": "1098691125768",
            "title": payload["title"],
            "payload": payload,
            "raw": {"offer_id": "1098691125768"},
        }
        product = {
            "raw_json": json.dumps(
                {
                    "skus": [
                        {
                            "sku_id": "WX-SKU-RED",
                            "sale_price": 1000,
                            "sku_attrs": [
                                {
                                    "attr_key": "颜色",
                                    "attr_value": "红色",
                                }
                            ],
                        }
                    ]
                },
                ensure_ascii=False,
            )
        }

        source_link, mappings = _upload_source_relation(item, product)

        self.assertIsNotNone(source_link)
        assert source_link is not None
        self.assertEqual(
            source_link["source_product_id"],
            "1098691125768",
        )
        self.assertEqual(mappings[0]["store_sku_id"], "WX-SKU-RED")
        self.assertEqual(
            mappings[0]["purchase_sku_id"],
            "1688-SKU-RED",
        )

        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "upload-source.db")
            store_id = db.add_store("上货店铺", "wx-upload-source", "secret")
            db.upsert_product(
                Product(
                    store_id=store_id,
                    store_name="上货店铺",
                    product_id="WX-PRODUCT-1",
                    title="上家测试商品",
                    status=5,
                    status_text="销售中",
                    edit_status=0,
                    min_price=1500,
                    sku_count=1,
                    stock_total=10,
                    spu_code="",
                    out_product_id="1098691125768",
                    head_img="https://example.test/main.jpg",
                    raw=json.loads(product["raw_json"]),
                )
            )
            db.commit_upload_task_result(
                "[]",
                store_id,
                "1098691125768",
                "WX-PRODUCT-1",
                "上家测试商品",
                source_link=source_link,
                sku_mappings=mappings,
            )

            links = db.list_product_purchase_links(
                store_id,
                "WX-PRODUCT-1",
            )
            saved_mappings = db.list_product_sku_mappings(
                store_id,
                "WX-PRODUCT-1",
            )

        self.assertEqual(len(links), 1)
        self.assertEqual(links[0]["source_product_id"], "1098691125768")
        self.assertEqual(len(saved_mappings), 1)
        self.assertEqual(
            saved_mappings[0]["purchase_sku_id"],
            "1688-SKU-RED",
        )

    def test_missing_category_permission_applies_category_and_retries_upload(
        self,
    ) -> None:
        upload_calls = []

        def upload():
            upload_calls.append("upload")
            if len(upload_calls) == 1:
                raise WechatApiError("商品无该类目权限", code=6600145)
            return {"product_id": "product-1"}

        class CategoryClient:
            def __init__(self) -> None:
                self.applied: list[list[str]] = []

            def category_relation_statuses(self, category_ids):
                if not self.applied:
                    return {}
                return {
                    str(category_id): "已生效"
                    for category_id in category_ids
                }

            def batch_add_categories(self, category_ids):
                self.applied.append(list(category_ids))
                return {"success": True}

        category_client = CategoryClient()
        result = _retry_upload_after_category_open(
            {
                "cats": [{"cat_id": "101"}],
                "cats_v2": [{"cat_id": "101"}],
            },
            upload,
            category_client,
        )

        self.assertEqual(result["product_id"], "product-1")
        self.assertEqual(category_client.applied, [["101"]])
        self.assertEqual(upload_calls, ["upload", "upload"])

    def test_category_under_review_stops_retry_and_keeps_categories_for_resume(
        self,
    ) -> None:
        class CategoryClient:
            def category_relation_statuses(self, category_ids):
                return {
                    str(category_id): "审核中"
                    for category_id in category_ids
                }

            def batch_add_categories(self, _category_ids):
                return {"success": True}

        with self.assertRaises(CategoryApprovalPending) as raised:
            _retry_upload_after_category_open(
                {"cats": [{"cat_id": "101"}]},
                lambda: (_ for _ in ()).throw(
                    WechatApiError("商品无该类目权限", code=6600145)
                ),
                CategoryClient(),
            )

        self.assertEqual(raised.exception.category_ids, ["101"])
        self.assertEqual(raised.exception.statuses, {"101": "审核中"})

    def test_category_rejection_stops_without_retrying_upload(self) -> None:
        upload_calls = []

        class CategoryClient:
            def category_application_checks(self, category_ids):
                return {
                    str(category_id): {
                        "state": "unavailable",
                        "label": "不可申请",
                        "reason": "平台禁止该叶子类目申请",
                        "eligible": False,
                    }
                    for category_id in category_ids
                }

            def batch_add_categories(self, _category_ids):
                raise AssertionError("unavailable category must not apply")

        with self.assertRaises(CategoryAutoOpenError) as raised:
            _retry_upload_after_category_open(
                {"cats_v2": [{"cat_id": "100"}, {"cat_id": "201"}]},
                lambda: upload_calls.append("upload") or (
                    (_ for _ in ()).throw(
                        WechatApiError("无该类目权限", code=6600145)
                    )
                ),
                CategoryClient(),
            )

        self.assertIn("平台禁止", str(raised.exception))
        self.assertEqual(upload_calls, ["upload"])

    def test_missing_leaf_category_stops_with_actionable_reason(self) -> None:
        with self.assertRaises(CategoryAutoOpenError) as raised:
            _retry_upload_after_category_open(
                {"title": "No category"},
                lambda: (_ for _ in ()).throw(
                    WechatApiError("无该类目权限", code=6600145)
                ),
                object(),
            )

        self.assertIn("未识别到", str(raised.exception))
        self.assertIn("叶子类目", str(raised.exception))

    def test_unknown_category_apply_result_waits_without_reapplying(self) -> None:
        upload_calls = []

        class CategoryClient:
            checks = 0

            def category_application_checks(self, category_ids):
                self.checks += 1
                state = "not_applied" if self.checks == 1 else "unknown"
                return {
                    str(category_id): {
                        "state": state,
                        "label": (
                            "未申请" if state == "not_applied" else "状态未知"
                        ),
                        "reason": "" if state == "not_applied" else "网络超时",
                        "eligible": state == "not_applied",
                    }
                    for category_id in category_ids
                }

            def batch_add_categories(self, _category_ids):
                raise RuntimeError("商家后台网络异常：timeout")

        with self.assertRaises(CategoryApprovalPending) as raised:
            _retry_upload_after_category_open(
                {"cats_v2": [{"cat_id": "3001"}]},
                lambda: upload_calls.append("upload") or (
                    (_ for _ in ()).throw(
                        WechatApiError("无该类目权限", code=6600145)
                    )
                ),
                CategoryClient(),
            )

        self.assertTrue(raised.exception.submitted)
        self.assertIn("网络异常", raised.exception.error)
        self.assertEqual(upload_calls, ["upload"])

    def test_explicit_category_apply_rejection_stops_immediately(self) -> None:
        class CategoryClient:
            def category_application_checks(self, category_ids):
                return {
                    str(category_id): {
                        "state": "not_applied",
                        "label": "未申请",
                        "reason": "",
                        "eligible": True,
                    }
                    for category_id in category_ids
                }

            def batch_add_categories(self, _category_ids):
                raise RuntimeError("平台禁止该叶子类目申请")

        with self.assertRaises(CategoryAutoOpenError) as raised:
            _retry_upload_after_category_open(
                {"cats_v2": [{"cat_id": "3001"}]},
                lambda: (_ for _ in ()).throw(
                    WechatApiError("无该类目权限", code=6600145)
                ),
                CategoryClient(),
            )

        self.assertIn("禁止", str(raised.exception))

    def test_waiting_category_unknown_stops_after_limited_checks(self) -> None:
        upload_calls = []

        class CategoryClient:
            def category_application_checks(self, category_ids):
                return {
                    str(category_id): {
                        "state": "unknown",
                        "label": "状态未知",
                        "reason": "网络超时",
                        "eligible": False,
                    }
                    for category_id in category_ids
                }

        with self.assertRaises(CategoryAutoOpenError) as raised:
            _retry_upload_after_category_open(
                {"cats_v2": [{"cat_id": "100"}, {"cat_id": "201"}]},
                lambda: upload_calls.append("upload") or {
                    "product_id": "should-not-run"
                },
                CategoryClient(),
                application={
                    "category_state": "waiting",
                    "category_ids": ["201"],
                    "category_apply_submitted": True,
                    "category_check_attempts": 12,
                },
            )

        self.assertIn("12 次", str(raised.exception))
        self.assertEqual(upload_calls, [])

    def test_pending_category_review_is_not_limited_to_twelve_checks(
        self,
    ) -> None:
        upload_calls = []
        category_calls = []

        class CategoryClient:
            effective = False

            def category_application_checks(self, category_ids):
                category_calls.append(tuple(category_ids))
                state = "effective" if self.effective else "pending"
                return {
                    str(category_id): {
                        "state": state,
                        "label": "已生效" if self.effective else "审核中",
                        "reason": "",
                        "eligible": False,
                    }
                    for category_id in category_ids
                }

            def batch_add_categories(self, _category_ids):
                raise AssertionError("pending category must not reapply")

        client = CategoryClient()
        application = {
            "category_state": "waiting",
            "category_ids": ["3001"],
            "category_apply_submitted": True,
            "category_application_at": datetime.now().isoformat(
                timespec="seconds"
            ),
        }
        for _ in range(15):
            with self.assertRaises(CategoryApprovalPending) as raised:
                _retry_upload_after_category_open(
                    {"cats_v2": [{"cat_id": "3001"}]},
                    lambda: upload_calls.append("upload") or {
                        "product_id": "must-not-exist"
                    },
                    client,
                    application=application,
                )
            application["category_check_attempts"] = (
                raised.exception.attempts
            )

        self.assertEqual(upload_calls, [])
        self.assertEqual(len(category_calls), 15)
        client.effective = True
        result = _retry_upload_after_category_open(
            {"cats_v2": [{"cat_id": "3001"}]},
            lambda: upload_calls.append("upload") or {
                "product_id": "WX-PENDING-APPROVED"
            },
            client,
            application=application,
        )

        self.assertEqual(result["product_id"], "WX-PENDING-APPROVED")
        self.assertEqual(upload_calls, ["upload"])

    def test_deposit_required_state_waits_without_reapplying_unchanged_request(
        self,
    ) -> None:
        upload_calls = []
        category_calls = []
        apply_calls = []

        class CategoryClient:
            state = "deposit_required"

            def category_application_checks(self, category_ids):
                category_calls.append(self.state)
                return {
                    str(category_id): {
                        "state": self.state,
                        "label": (
                            "保证金不足"
                            if self.state == "deposit_required"
                            else (
                                "已生效"
                                if self.state == "effective"
                                else "未申请"
                            )
                        ),
                        "reason": (
                            "需补缴 100000 分"
                            if self.state == "deposit_required"
                            else ""
                        ),
                        "eligible": self.state == "not_applied",
                        "required_deposit": 100000,
                        "amount_to_pay": 100000,
                        "operation_required_deposit": 0,
                        "deposit_balance": 0,
                        "real_deposit_balance": 0,
                        "deposit_shortfall": 0,
                    }
                    for category_id in category_ids
                }

            def batch_add_categories(self, category_ids):
                apply_calls.append(list(category_ids))
                self.state = "pending"

        client = CategoryClient()
        application = {
            "status": "waiting_category",
            "category_state": "deposit_required",
            "category_ids": ["3001"],
            "category_apply_submitted": False,
            "category_deposit_snapshot": {
                "required_deposit": 100000,
                "amount_to_pay": 100000,
                "operation_required_deposit": 0,
                "deposit_balance": 0,
                "real_deposit_balance": 0,
                "deposit_shortfall": 0,
                "online_count": None,
            },
        }
        with self.assertRaises(CategoryApprovalPending) as raised:
            _retry_upload_after_category_open(
                {"cats_v2": [{"cat_id": "3001"}]},
                lambda: upload_calls.append("upload") or {
                    "product_id": "must-not-exist"
                },
                client,
                application=application,
            )
        self.assertEqual(raised.exception.state, "deposit_required")
        self.assertFalse(raised.exception.submitted)
        self.assertEqual(apply_calls, [])
        self.assertEqual(upload_calls, [])
        self.assertEqual(category_calls, ["deposit_required"])

    def test_deposit_error_from_apply_is_waiting_not_failed(self) -> None:
        class CategoryClient:
            def category_application_checks(self, category_ids):
                return {
                    str(category_id): {
                        "state": "not_applied",
                        "label": "未申请",
                        "reason": "",
                        "eligible": True,
                    }
                    for category_id in category_ids
                }

            def batch_add_categories(self, _category_ids):
                raise RuntimeError("保证金不足，需补缴 200000 分")

        with self.assertRaises(CategoryApprovalPending) as raised:
            _retry_upload_after_category_open(
                {"cats_v2": [{"cat_id": "3001"}]},
                lambda: (_ for _ in ()).throw(
                    WechatApiError("无该类目权限", code=6600145)
                ),
                CategoryClient(),
            )

        self.assertEqual(raised.exception.state, "deposit_required")
        self.assertIn("保证金", str(raised.exception))

    def test_6600133_preserves_official_deposit_error_and_snapshot(
        self,
    ) -> None:
        upload_calls = []
        apply_calls = []

        class CategoryClient:
            def online_product_count(self):
                return {"count": 101}

            def category_application_checks(self, category_ids):
                return {
                    category_id: {
                        "state": "effective",
                        "label": "已生效",
                        "eligible": False,
                        "required_deposit": 200000,
                        "amount_to_pay": 200000,
                        "operation_required_deposit": 0,
                        "deposit_balance": 0,
                        "real_deposit_balance": 0,
                        "deposit_shortfall": 0,
                        "deposit_free_trial": True,
                    }
                    for category_id in category_ids
                }

            def batch_add_categories(self, category_ids):
                apply_calls.append(list(category_ids))

        def upload():
            upload_calls.append("upload")
            raise WechatApiError(
                "商品信息检查不通过, 错误码:6600133 "
                "原因:当前你的店铺已上架100个商品，"
                "如需上架请先缴纳足额保证金",
                code=6600133,
            )

        application = {}
        with self.assertRaises(CategoryApprovalPending) as raised:
            _retry_upload_after_category_open(
                {"cats_v2": [{"cat_id": "1713"}]},
                upload,
                CategoryClient(),
                application=application,
            )

        self.assertEqual(raised.exception.state, "deposit_required")
        self.assertFalse(raised.exception.submitted)
        self.assertIn("6600133", str(raised.exception))
        self.assertEqual(upload_calls, ["upload"])
        self.assertEqual(apply_calls, [])
        self.assertEqual(
            raised.exception.deposit_snapshot["online_count"],
            101,
        )

    def test_historical_6600133_recovers_to_deposit_wait_without_payload_loss(
        self,
    ) -> None:
        tasks = [
            {
                "id": "task-deposit-history",
                "items": [
                    {
                        "item_id": "item-deposit-history",
                        "status": "failed",
                        "message": (
                            "商品信息检查不通过, 错误码:6600133 "
                            "原因:当前你的店铺已上架100个商品，"
                            "如需上架请先缴纳足额保证金"
                        ),
                        "payload": {
                            "head_imgs": [
                                "https://example.invalid/1.jpg"
                            ],
                            "skus": [
                                {"sale_price": 100, "stock_num": 1}
                            ],
                            "cats_v2": [{"cat_id": "1713"}],
                        },
                    }
                ],
            }
        ]

        self.assertTrue(_recover_upload_tasks(tasks))
        item = tasks[0]["items"][0]
        self.assertEqual(item["status"], "waiting_category")
        self.assertEqual(item["category_state"], "deposit_required")
        self.assertEqual(item["category_ids"], ["1713"])
        self.assertIn("category_deposit_snapshot", item)
        self.assertEqual(
            item["category_deposit_snapshot"]["online_count"],
            100,
        )
        self.assertIn("6600133", item["category_previous_failure_message"])

    def test_saved_deposit_wait_does_not_reapply_unchanged_category(
        self,
    ) -> None:
        upload_calls = []
        apply_calls = []

        class CategoryClient:
            def online_product_count(self):
                return {"count": 101}

            def category_application_checks(self, category_ids):
                return {
                    str(category_id): {
                        "state": "not_applied",
                        "label": "未申请",
                        "reason": "",
                        "eligible": True,
                        "required_deposit": 200000,
                        "amount_to_pay": 200000,
                        "operation_required_deposit": 0,
                        "deposit_balance": 0,
                        "real_deposit_balance": 0,
                        "deposit_shortfall": 0,
                        "deposit_free_trial": True,
                        "business_type": "individual",
                    }
                    for category_id in category_ids
                }

            def batch_add_categories(self, category_ids):
                apply_calls.append(list(category_ids))

        application = {
            "status": "waiting_category",
            "category_state": "deposit_required",
            "category_ids": ["1713"],
            "category_apply_submitted": False,
            "category_error": "保证金不足，需补缴 200000 分",
            "category_deposit_snapshot": {
                "deposit_balance": 0,
                "real_deposit_balance": 0,
                "operation_required_deposit": 0,
                "online_count": 101,
            },
        }
        with self.assertRaises(CategoryApprovalPending) as raised:
            _retry_upload_after_category_open(
                {"cats_v2": [{"cat_id": "1713"}]},
                lambda: upload_calls.append("upload") or {
                    "product_id": "must-not-exist"
                },
                CategoryClient(),
                application=application,
            )

        self.assertEqual(raised.exception.state, "deposit_required")
        self.assertFalse(raised.exception.submitted)
        self.assertEqual(apply_calls, [])
        self.assertEqual(upload_calls, [])

    def test_saved_deposit_wait_reapplies_after_wallet_balance_change(
        self,
    ) -> None:
        apply_calls = []
        upload_calls = []

        class CategoryClient:
            state = "not_applied"

            def online_product_count(self):
                return {"count": 101}

            def category_application_checks(self, category_ids):
                return {
                    str(category_id): {
                        "state": self.state,
                        "label": (
                            "未申请"
                            if self.state == "not_applied"
                            else "已生效"
                        ),
                        "reason": "",
                        "eligible": self.state == "not_applied",
                        "required_deposit": 200000,
                        "amount_to_pay": 200000,
                        "operation_required_deposit": 0,
                        "deposit_balance": 200000,
                        "real_deposit_balance": 200000,
                        "deposit_shortfall": 0,
                        "deposit_free_trial": True,
                        "business_type": "individual",
                    }
                    for category_id in category_ids
                }

            def batch_add_categories(self, category_ids):
                apply_calls.append(list(category_ids))
                self.state = "effective"

        application = {
            "status": "waiting_category",
            "category_state": "deposit_required",
            "category_ids": ["1713"],
            "category_apply_submitted": False,
            "category_error": "保证金不足，需补缴 200000 分",
            "category_deposit_snapshot": {
                "deposit_balance": 0,
                "real_deposit_balance": 0,
                "operation_required_deposit": 0,
                "deposit_shortfall": 0,
                "online_count": 101,
            },
        }

        result = _retry_upload_after_category_open(
            {"cats_v2": [{"cat_id": "1713"}]},
            lambda: upload_calls.append("upload") or {
                "product_id": "WX-DEPOSIT-RECOVERED"
            },
            CategoryClient(),
            application=application,
        )

        self.assertEqual(result["product_id"], "WX-DEPOSIT-RECOVERED")
        self.assertEqual(apply_calls, [["1713"]])
        self.assertEqual(upload_calls, ["upload"])

    def test_saved_deposit_wait_stops_after_long_unchanged_window(
        self,
    ) -> None:
        class CategoryClient:
            def category_application_checks(self, category_ids):
                return {
                    str(category_id): {
                        "state": "not_applied",
                        "label": "未申请",
                        "reason": "",
                        "eligible": True,
                        "required_deposit": 200000,
                        "amount_to_pay": 200000,
                        "operation_required_deposit": 0,
                        "deposit_balance": 0,
                        "real_deposit_balance": 0,
                        "deposit_shortfall": 0,
                        "deposit_free_trial": True,
                    }
                    for category_id in category_ids
                }

        application = {
            "status": "waiting_category",
            "category_state": "deposit_required",
            "category_ids": ["1713"],
            "category_apply_submitted": False,
            "category_application_at": "2026-11-01T09:00:00",
            "category_deposit_snapshot": {
                "deposit_balance": 0,
                "real_deposit_balance": 0,
                "operation_required_deposit": 0,
                "deposit_shortfall": 0,
                "online_count": 101,
            },
        }

        with self.assertRaises(CategoryAutoOpenError) as raised:
            _retry_upload_after_category_open(
                {"cats_v2": [{"cat_id": "1713"}]},
                lambda: {"product_id": "must-not-exist"},
                CategoryClient(),
                application=application,
            )

        self.assertEqual(raised.exception.state, "deposit_required")
        self.assertIn("保证金", str(raised.exception))

    def test_under_100_live_products_applies_category_without_deposit_block(
        self,
    ) -> None:
        upload_calls = []
        apply_calls = []
        online_calls = []

        class CategoryClient:
            state = "not_applied"

            def online_product_count(self):
                online_calls.append(True)
                return {
                    "count": 99,
                    "source": "官方实时出售中商品数",
                }

            def category_application_checks(self, category_ids):
                return {
                    category_id: {
                        "state": self.state,
                        "label": (
                            "未申请"
                            if self.state == "not_applied"
                            else "已生效"
                        ),
                        "reason": "",
                        "eligible": self.state == "not_applied",
                        "required_deposit": 100000,
                        "amount_to_pay": 100000,
                        "deposit_balance": 0,
                        "deposit_shortfall": 100000,
                        "deposit_free_trial": True,
                    }
                    for category_id in category_ids
                }

            def batch_add_categories(self, category_ids):
                apply_calls.append(list(category_ids))
                self.state = "effective"

        client = CategoryClient()
        application = {}

        def upload():
            upload_calls.append("upload")
            if len(upload_calls) == 1:
                raise WechatApiError(
                    "无该类目权限",
                    code=6600145,
                )
            return {"product_id": "WX-UNDER-100"}

        result = _retry_upload_after_category_open(
            {"cats_v2": [{"cat_id": "547307"}]},
            upload,
            client,
            application=application,
        )

        self.assertEqual(result["product_id"], "WX-UNDER-100")
        self.assertEqual(upload_calls, ["upload", "upload"])
        self.assertEqual(apply_calls, [["547307"]])
        self.assertEqual(online_calls, [True])
        self.assertEqual(
            application["online_product_snapshot"]["count"],
            99,
        )

    def test_deposit_fields_are_informational_above_100_live_products(
        self,
    ) -> None:
        apply_calls = []
        upload_calls = []

        class CategoryClient:
            state = "not_applied"

            def online_product_count(self):
                return {"count": 100}

            def category_application_checks(self, category_ids):
                return {
                    category_id: {
                        "state": self.state,
                        "label": "未申请",
                        "reason": "",
                        "eligible": True,
                        "required_deposit": 100000,
                        "amount_to_pay": 100000,
                        "deposit_balance": 0,
                        "deposit_shortfall": 100000,
                        "deposit_free_trial": False,
                    }
                    for category_id in category_ids
                }

            def batch_add_categories(self, category_ids):
                apply_calls.append(list(category_ids))
                self.state = "effective"

        def upload():
            upload_calls.append("upload")
            if len(upload_calls) == 1:
                raise WechatApiError(
                    "无该类目权限",
                    code=6600145,
                )
            return {"product_id": "WX-AT-100"}

        result = _retry_upload_after_category_open(
            {"cats_v2": [{"cat_id": "547307"}]},
            upload,
            CategoryClient(),
        )

        self.assertEqual(result["product_id"], "WX-AT-100")
        self.assertEqual(apply_calls, [["547307"]])
        self.assertEqual(upload_calls, ["upload", "upload"])

    def test_pending_category_review_has_long_manual_fallback(
        self,
    ) -> None:
        class CategoryClient:
            def category_application_checks(self, category_ids):
                return {
                    str(category_id): {
                        "state": "pending",
                        "label": "审核中",
                        "reason": "",
                        "eligible": False,
                    }
                    for category_id in category_ids
                }

        with patch.dict(
            os.environ,
            {"WEIMI_CATEGORY_REVIEW_MAX_HOURS": "72"},
        ):
            with self.assertRaises(CategoryAutoOpenError) as raised:
                _retry_upload_after_category_open(
                    {"cats_v2": [{"cat_id": "3001"}]},
                    lambda: {"product_id": "must-not-exist"},
                    CategoryClient(),
                    application={
                        "category_state": "waiting",
                        "category_ids": ["3001"],
                        "category_apply_submitted": True,
                        "category_application_at": (
                            datetime.now() - timedelta(days=4)
                        ).isoformat(timespec="seconds"),
                    },
                )

        self.assertIn("72 小时", str(raised.exception))

    def test_empty_or_partial_category_checks_are_never_treated_as_effective(
        self,
    ) -> None:
        upload_calls = []

        class EmptyCategoryClient:
            def category_application_checks(self, _category_ids):
                return {}

        with self.assertRaises(CategoryApprovalPending):
            _retry_upload_after_category_open(
                {"cats_v2": [{"cat_id": "100"}, {"cat_id": "201"}]},
                lambda: upload_calls.append("upload") or {
                    "product_id": "must-not-exist"
                },
                EmptyCategoryClient(),
                application={
                    "category_state": "waiting",
                    "category_ids": ["201"],
                    "category_apply_submitted": True,
                },
            )

        class PartialCategoryClient:
            def category_application_checks(self, _category_ids):
                return {
                    "201": {
                        "state": "effective",
                        "label": "已生效",
                        "reason": "",
                        "eligible": False,
                    }
                }

        with self.assertRaises(CategoryApprovalPending):
            _retry_upload_after_category_open(
                {
                    "cats_v2": [
                        {"cat_id": "100"},
                        {"cat_id": "201"},
                        {"cat_id": "202"},
                    ]
                },
                lambda: upload_calls.append("upload") or {
                    "product_id": "must-not-exist"
                },
                PartialCategoryClient(),
                category_ids=["201", "202"],
                application={
                    "category_state": "waiting",
                    "category_ids": ["201", "202"],
                    "category_apply_submitted": True,
                },
            )

        self.assertEqual(upload_calls, [])

    def test_automatic_category_retry_resumes_waiting_items(self) -> None:
        class FakeWindow:
            def __init__(self) -> None:
                self.statuses = []

            def set_status(self, message, **_kwargs) -> None:
                self.statuses.append(message)

        page = object.__new__(UploadPage)
        page.window = FakeWindow()
        page._tasks = [
            {
                "status": "needs_category",
                "items": [
                    {
                        "status": "waiting_category",
                        "message": "等待类目生效",
                    }
                ],
            }
        ]
        page._running = False
        page._run_requested = False
        page._category_retry_job = None
        page._start_tasks = Mock()

        page._auto_retry_category_tasks()

        page._start_tasks.assert_called_once_with(automatic=True)
        self.assertEqual(
            page.window.statuses,
            ["正在检查已申请类目并自动恢复上货…"],
        )

    def test_automatic_category_retry_waits_for_next_check_time(self) -> None:
        class FakeWindow:
            def set_status(self, *_args, **_kwargs) -> None:
                raise AssertionError("future category check must not start")

        page = object.__new__(UploadPage)
        page.window = FakeWindow()
        page._tasks = [
            {
                "id": "future-task",
                "status": "needs_category",
                "items": [
                    {
                        "item_id": "future-item",
                        "status": "waiting_category",
                        "category_next_check_at": (
                            datetime.now() + timedelta(minutes=10)
                        ).isoformat(timespec="seconds"),
                    }
                ],
            }
        ]
        page._running = False
        page._run_requested = False
        page._category_retry_job = None
        page._start_tasks = Mock()

        page._auto_retry_category_tasks()

        page._start_tasks.assert_not_called()

    def test_automatic_category_retry_defers_while_upload_is_running(
        self,
    ) -> None:
        class FakeTopLevel:
            def __init__(self) -> None:
                self.delay = None

            def after(self, delay, _callback):
                self.delay = delay
                return "retry-job"

        class FakeWindow:
            def set_status(self, *_args, **_kwargs) -> None:
                raise AssertionError("running upload must be allowed to finish")

        top_level = FakeTopLevel()
        page = object.__new__(UploadPage)
        page.window = FakeWindow()
        page._tasks = [
            {
                "id": "running-task",
                "items": [
                    {
                        "item_id": "running-item",
                        "status": "waiting_category",
                        "category_next_check_at": (
                            datetime.now() - timedelta(minutes=1)
                        ).isoformat(timespec="seconds"),
                    }
                ],
            }
        ]
        page._running = True
        page._run_requested = False
        page._category_retry_job = None
        page._start_tasks = Mock()
        page.winfo_toplevel = lambda: top_level

        page._auto_retry_category_tasks()

        page._start_tasks.assert_not_called()
        self.assertGreaterEqual(top_level.delay, 60_000)

    def test_group_selection_includes_all_stores_in_selected_groups(
        self,
    ) -> None:
        stores = [
            SimpleNamespace(id=1, group_name="予安"),
            SimpleNamespace(id=2, group_name="予安"),
            SimpleNamespace(id=3, group_name="盛达"),
            SimpleNamespace(id=4, group_name=""),
        ]

        selected = _stores_matching_group_names(
            stores,
            ["予安", "未分组"],
        )

        self.assertEqual([store.id for store in selected], [1, 2, 4])

    def test_upload_store_dialog_selects_all_stores_in_multiple_groups(
        self,
    ) -> None:
        class FakeWindow:
            def __init__(self, db) -> None:
                self.db = db

            def set_status(self, *_args, **_kwargs) -> None:
                pass

        def descendants(widget):
            for child in widget.winfo_children():
                yield child
                yield from descendants(child)

        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "store-select.db")
            first = db.add_store("予安店铺一", "wx-group-a1", "secret", "予安")
            second = db.add_store("予安店铺二", "wx-group-a2", "secret", "予安")
            third = db.add_store("盛达店铺", "wx-group-b1", "secret", "盛达")
            db.create_store_group("空分组")
            try:
                root = tk.Tk()
            except tk.TclError as exc:
                self.skipTest(str(exc))
            root.withdraw()
            apply_theme(root)
            page = UploadPage(root, FakeWindow(db))

            def drive_dialog() -> None:
                dialogs = [
                    widget
                    for widget in descendants(root)
                    if isinstance(widget, tk.Toplevel)
                    and widget.title() == "选择上传店铺"
                ]
                if not dialogs:
                    root.after(20, drive_dialog)
                    return
                dialog = dialogs[0]
                group_list = next(
                    widget
                    for widget in descendants(dialog)
                    if isinstance(widget, tk.Listbox)
                )
                selected_indices = [
                    index
                    for index in range(group_list.size())
                    if group_list.get(index) in {"予安", "盛达"}
                ]
                group_list.selection_set(selected_indices[0])
                group_list.selection_set(selected_indices[1])
                group_list.event_generate("<<ListboxSelect>>")
                dialog.update_idletasks()
                confirm = next(
                    widget
                    for widget in descendants(dialog)
                    if isinstance(widget, ttk.Button)
                    and widget.cget("text") == "确定"
                )
                confirm.invoke()

            root.after(20, drive_dialog)
            try:
                selected = page._choose_stores()
                self.assertEqual(
                    {store.id for store in selected},
                    {first, second, third},
                )
            finally:
                root.destroy()

    def test_known_remote_rejections_are_not_reported_as_unknown(
        self,
    ) -> None:
        category_error = WechatApiError(
            "商品无该类目权限",
            code=6600145,
        )
        attribute_error = WechatApiError(
            "商品参数类目数量需要选择单位",
            code=6601121,
        )
        self.assertIn(
            "目标类目权限",
            _definitive_upload_failure_message(category_error),
        )
        self.assertIn(
            "类目属性数量格式",
            _definitive_upload_failure_message(attribute_error),
        )
        self.assertEqual(
            _definitive_upload_failure_message(
                WechatApiError("系统繁忙", code=-1)
            ),
            "",
        )
        self.assertEqual(
            _definitive_upload_failure_message(
                ConnectionError("网络中断")
            ),
            "",
        )
        for error in (
            WechatApiError("HTTP 500: upstream reset"),
            WechatApiError("网络请求失败: timed out"),
            WechatApiError("接口返回了无法解析的数据"),
        ):
            with self.subTest(error=str(error)):
                self.assertEqual(
                    _definitive_upload_failure_message(error),
                    "",
                    "无结构化错误码的传输失败必须进入待核实，不能自动重试",
                )

    def test_refresh_only_renders_active_upload_view(self) -> None:
        class FakeWindow:
            def __init__(self, db) -> None:
                self.db = db

            def set_status(self, *_args, **_kwargs) -> None:
                pass

        with tempfile.TemporaryDirectory() as tmp:
            try:
                root = tk.Tk()
            except tk.TclError as exc:
                self.skipTest(str(exc))
            root.withdraw()
            apply_theme(root)
            try:
                page = UploadPage(
                    root,
                    FakeWindow(Database(Path(tmp) / "refresh.db")),
                )
                page._show_view("products")
                with (
                    patch.object(page, "_render_templates") as templates,
                    patch.object(page, "_render_batch") as batch,
                    patch.object(page, "_render_tasks") as tasks,
                    patch.object(
                        page,
                        "_render_category_stores",
                    ) as categories,
                ):
                    page.refresh()
                templates.assert_not_called()
                batch.assert_called_once()
                tasks.assert_not_called()
                categories.assert_not_called()
            finally:
                root.destroy()

    def test_batch_filter_modes_cover_title_sku_stock_and_price(self) -> None:
        items = [
            {
                "batch_id": "a",
                "title": "夏季连衣裙",
                "price": 1999,
                "stock": 5,
                "platform": "微信小店",
                "payload": {"skus": [{"id": "SKU-RED-01"}]},
            },
            {
                "batch_id": "b",
                "title": "冬季棉服",
                "price": 8999,
                "stock": 80,
                "platform": "淘宝",
                "payload": {"skus": [{"id": "SKU-WINTER-88"}]},
            },
        ]

        def ids(**criteria):
            arguments = {
                "keyword": "",
                "minimum": "",
                "maximum": "",
                "platform": "全部平台",
            }
            arguments.update(criteria)
            return [
                row["batch_id"]
                for row in _filter_batch_rows(
                    items,
                    **arguments,
                )
            ]

        self.assertEqual(ids(mode="标题包含", keyword="连衣裙"), ["a"])
        self.assertEqual(ids(mode="SKU包含", keyword="winter-88"), ["b"])
        self.assertEqual(
            ids(mode="库存范围", minimum="1", maximum="10"),
            ["a"],
        )
        self.assertEqual(
            ids(mode="价格范围", minimum="50", maximum="100"),
            ["b"],
        )
        with self.assertRaises(ValueError):
            ids(mode="库存范围", minimum="20", maximum="10")

    def test_selection_survives_pages_and_inverts_all_items(self) -> None:
        class FakeWindow:
            def __init__(self, db) -> None:
                self.db = db

            def set_status(self, *_args, **_kwargs) -> None:
                pass

        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "selection.db")
            try:
                root = tk.Tk()
            except tk.TclError as exc:
                self.skipTest(str(exc))
            root.withdraw()
            apply_theme(root)
            try:
                page = UploadPage(root, FakeWindow(db))
                page._batch = [
                    {
                        "batch_id": str(index),
                        "title": f"商品 {index}",
                        "source_product_id": f"ID-{index}",
                        "price": 1000,
                        "stock": 10,
                        "platform": "微信小店",
                    }
                    for index in range(450)
                ]
                page._render_batch()
                self.assertEqual(len(page.batch_tree.get_children()), 200)

                page.batch_tree.selection_set(("0",))
                page._update_batch_summary()
                self.assertEqual(page._selected_batch_ids, {"0"})
                page._change_batch_page(1)
                self.assertEqual(len(page.batch_tree.get_children()), 200)
                self.assertEqual(page._selected_batch_ids, {"0"})

                page._invert_batch_selection(all_items=True)
                self.assertEqual(len(page._selected_batch_ids), 449)
                self.assertNotIn("0", page._selected_batch_ids)

                page._select_batch_result(force=True)
                self.assertEqual(len(page._selected_batch_ids), 450)
                page._clear_batch_selection()
                self.assertEqual(page._selected_batch_ids, set())
            finally:
                root.destroy()

    def test_batch_render_repairs_missing_batch_ids(self) -> None:
        class FakeWindow:
            def __init__(self, db) -> None:
                self.db = db

            def set_status(self, *_args, **_kwargs) -> None:
                pass

        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "missing-ids.db")
            try:
                root = tk.Tk()
            except tk.TclError as exc:
                self.skipTest(str(exc))
            root.withdraw()
            apply_theme(root)
            try:
                page = UploadPage(root, FakeWindow(db))
                page._batch = [
                    {
                        "title": f"商品 {index}",
                        "source_product_id": f"ID-{index}",
                        "price": 1000,
                        "stock": 10,
                        "platform": "1688",
                    }
                    for index in range(175)
                ]
                page._render_batch()

                children = page.batch_tree.get_children()
                self.assertEqual(len(children), 175)
                self.assertEqual(len(set(children)), 175)
                self.assertTrue(
                    all(
                        str(row.get("batch_id") or "").strip()
                        for row in page._batch
                    )
                )
            finally:
                root.destroy()

    def test_import_automatically_selects_new_products(self) -> None:
        class FakeWindow:
            def __init__(self, db) -> None:
                self.db = db

            def set_status(self, *_args, **_kwargs) -> None:
                pass

        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "auto-select.db")
            try:
                root = tk.Tk()
            except tk.TclError as exc:
                self.skipTest(str(exc))
            root.withdraw()
            apply_theme(root)
            try:
                page = UploadPage(root, FakeWindow(db))
                parsed = [
                    {
                        "batch_id": f"row-{index}",
                        "title": f"商品 {index}",
                        "source_product_id": f"ID-{index}",
                        "price": 1000,
                        "stock": 10,
                        "platform": "1688",
                    }
                    for index in range(3)
                ]
                with patch.object(
                    page,
                    "_parse_package",
                    return_value=parsed,
                ):
                    page._import_files([Path("fixture.json")])

                self.assertEqual(
                    page._selected_batch_ids,
                    {"row-0", "row-1", "row-2"},
                )
                self.assertEqual(
                    len(page.batch_tree.selection()),
                    3,
                )
            finally:
                root.destroy()

    def test_batch_render_tolerates_mixed_price_and_stock_types(self) -> None:
        class FakeWindow:
            def __init__(self, db) -> None:
                self.db = db

            def set_status(self, *_args, **_kwargs) -> None:
                pass

        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "mixed-types.db")
            try:
                root = tk.Tk()
            except tk.TclError as exc:
                self.skipTest(str(exc))
            root.withdraw()
            apply_theme(root)
            try:
                page = UploadPage(root, FakeWindow(db))
                page._batch = [
                    {
                        "batch_id": "price-text",
                        "title": "价格文本",
                        "price": "¥12.50",
                        "stock": "12",
                        "platform": "1688",
                    },
                    {
                        "batch_id": "bad-stock",
                        "title": "异常库存",
                        "price": 990,
                        "stock": {"unknown": True},
                        "platform": "1688",
                    },
                    {
                        "batch_id": "missing-price",
                        "title": "缺少价格",
                        "stock": None,
                        "platform": "其他",
                    },
                ]
                page._render_batch()

                self.assertEqual(
                    len(page.batch_tree.get_children()),
                    3,
                )
                self.assertEqual(
                    page.batch_tree.set("price-text", "price"),
                    "¥12.50",
                )
                self.assertEqual(
                    page.batch_tree.set("bad-stock", "stock"),
                    "未知",
                )
            finally:
                root.destroy()

    def test_filter_change_prunes_hidden_selection_before_delete(self) -> None:
        class FakeWindow:
            def __init__(self, db) -> None:
                self.db = db

            def set_status(self, *_args, **_kwargs) -> None:
                pass

        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "filter-selection.db")
            try:
                root = tk.Tk()
            except tk.TclError as exc:
                self.skipTest(str(exc))
            root.withdraw()
            apply_theme(root)
            try:
                page = UploadPage(root, FakeWindow(db))
                page._batch = [
                    {
                        "batch_id": str(index),
                        "title": title,
                        "source_product_id": f"ID-{index}",
                        "price": 1000,
                        "stock": 10,
                        "platform": "微信小店",
                    }
                    for index, title in enumerate(
                        ("连衣裙一", "连衣裙二", "连衣裙三", "外套"),
                        start=1,
                    )
                ]
                page._render_batch()
                page._select_batch_result(force=True)
                self.assertEqual(len(page._selected_batch_ids), 4)

                page.filter_mode_var.set("标题包含")
                page.filter_keyword_var.set("连衣裙")
                page._apply_batch_filter()
                self.assertEqual(
                    page._selected_batch_ids,
                    {"1", "2", "3"},
                )

                with patch(
                    "weimi_manager.ui.upload_page.messagebox.askyesno",
                    return_value=True,
                ) as confirm:
                    page._delete_selected_batch()
                self.assertIn("3 条商品", str(confirm.call_args))
                self.assertEqual(
                    [row["title"] for row in page._batch],
                    ["外套"],
                )
            finally:
                root.destroy()

    def test_import_filter_and_create_tasks_end_to_end(self) -> None:
        class FakeWindow:
            def __init__(self, db) -> None:
                self.db = db

            def set_status(self, *_args, **_kwargs) -> None:
                pass

        payload = {
            "products": [
                {
                    "id": "SOURCE-ZERO",
                    "title": "零库存商品",
                    "price": "10.00",
                    "stock": 0,
                    "payload": {"title": "零库存商品"},
                },
                {
                    "id": "SOURCE-FIVE",
                    "title": "五件库存商品",
                    "price": "20.00",
                    "stock": 5,
                    "payload": {"title": "五件库存商品"},
                },
                {
                    "id": "SOURCE-TEN",
                    "title": "十件库存商品",
                    "price": "30.00",
                    "stock": 10,
                    "payload": {"title": "十件库存商品"},
                },
            ]
        }
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "flow.db")
            package = Path(tmp) / "products.json"
            package.write_text(
                json.dumps(payload, ensure_ascii=False),
                encoding="utf-8",
            )
            try:
                root = tk.Tk()
            except tk.TclError as exc:
                self.skipTest(str(exc))
            root.withdraw()
            apply_theme(root)
            try:
                page = UploadPage(root, FakeWindow(db))
                page._import_files([package])
                self.assertEqual(len(page._batch), 3)
                page.filter_mode_var.set("库存范围")
                page.filter_min_var.set("1")
                page._render_batch()
                page._select_batch_result(force=True)
                page._templates = [
                    {
                        "name": "库存测试模板",
                        "price_multiplier": 1,
                        "price_offset": 0,
                        "stock": 100,
                    }
                ]
                page.template_var.set("库存测试模板")
                store = Store(
                    id=1,
                    name="测试店铺",
                    app_id="wx-test",
                    group_name="",
                    status="正常",
                    last_sync_at=None,
                    created_at="",
                )
                with patch.object(
                    page,
                    "_choose_stores",
                    return_value=[store],
                ):
                    page._create_tasks()
                self.assertEqual(len(page._tasks), 1)
                titles = {
                    item["title"]
                    for item in page._tasks[0]["items"]
                }
                self.assertEqual(
                    titles,
                    {"五件库存商品", "十件库存商品"},
                )
                saved_tasks = json.loads(
                    db.get_setting("upload_tasks", "[]")
                )
                self.assertEqual(
                    len(saved_tasks[0]["items"]),
                    2,
                )
            finally:
                root.destroy()

    def test_template_scroll_wheel_works_over_child_controls(self) -> None:
        try:
            root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(str(exc))
        root.geometry("500x260+0+0")
        apply_theme(root)
        try:
            scroller = _ScrollableBody(root)
            scroller.pack(fill="both", expand=True)
            first_child = None
            for index in range(20):
                child = ttk.Label(
                    scroller.body,
                    text=f"模板设置 {index}",
                )
                child.pack(anchor="w", pady=8)
                if first_child is None:
                    first_child = child
            root.update()
            scroller.canvas.configure(
                scrollregion=scroller.canvas.bbox("all")
            )
            root.update()
            before = scroller.canvas.yview()
            event = SimpleNamespace(
                widget=first_child,
                x_root=first_child.winfo_rootx() + 3,
                y_root=first_child.winfo_rooty() + 3,
                delta=-120,
            )
            scroller._on_mousewheel(event)
            root.update_idletasks()
            after = scroller.canvas.yview()
            self.assertGreater(after[0], before[0])
        finally:
            root.destroy()

    def test_rapid_template_wheel_burst_is_coalesced(self) -> None:
        try:
            try:
                root = tk.Tk()
            except tk.TclError as exc:
                self.skipTest(str(exc))
        except tk.TclError as exc:
            self.skipTest(str(exc))
        root.geometry("500x260+0+0")
        apply_theme(root)
        try:
            scroller = _ScrollableBody(root)
            scroller.pack(fill="both", expand=True)
            first_child = None
            for index in range(40):
                child = ttk.Label(
                    scroller.body,
                    text=f"快速滚动设置 {index}",
                )
                child.pack(anchor="w", pady=5)
                if first_child is None:
                    first_child = child
            root.update()
            event = SimpleNamespace(
                widget=first_child,
                x_root=first_child.winfo_rootx() + 2,
                y_root=first_child.winfo_rooty() + 2,
                delta=-120,
            )
            with patch.object(
                scroller.canvas,
                "yview_scroll",
            ) as scroll:
                for _index in range(120):
                    scroller._on_mousewheel(event)
                root.update_idletasks()
            scroll.assert_called_once_with(120, "units")
        finally:
            root.destroy()

    def test_template_scrollbar_drag_is_coalesced(self) -> None:
        try:
            try:
                root = tk.Tk()
            except tk.TclError as exc:
                self.skipTest(str(exc))
        except tk.TclError as exc:
            self.skipTest(str(exc))
        apply_theme(root)
        try:
            scroller = _ScrollableBody(root)
            scroller.pack(fill="both", expand=True)
            with patch.object(scroller.canvas, "yview") as yview:
                for index in range(80):
                    scroller._on_scrollbar(
                        "moveto",
                        str(index / 79),
                    )
                time.sleep(0.03)
                root.update()
            self.assertEqual(yview.call_count, 1)
            yview.assert_called_with("1.0")
        finally:
            root.destroy()

    def test_batch_stock_filter_delete_selection_and_zero_stock_guard(
        self,
    ) -> None:
        class FakeWindow:
            def __init__(self, db) -> None:
                self.db = db
                self.messages: list[str] = []

            def set_status(self, message, **_kwargs) -> None:
                self.messages.append(message)

        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "upload.db")
            try:
                root = tk.Tk()
            except tk.TclError as exc:
                self.skipTest(str(exc))
            root.withdraw()
            apply_theme(root)
            try:
                page = UploadPage(root, FakeWindow(db))
                page._batch = [
                    {
                        "batch_id": "zero",
                        "title": "零库存商品",
                        "source_product_id": "SOURCE-ZERO",
                        "price": 100,
                        "stock": 0,
                        "platform": "微信小店",
                    },
                    {
                        "batch_id": "five",
                        "title": "五件库存商品",
                        "source_product_id": "SOURCE-FIVE",
                        "price": 200,
                        "stock": 5,
                        "platform": "微信小店",
                    },
                    {
                        "batch_id": "ten",
                        "title": "十件库存商品",
                        "source_product_id": "SOURCE-TEN",
                        "price": 300,
                        "stock": 10,
                        "platform": "微信小店",
                    },
                ]
                page.filter_mode_var.set("库存范围")
                page.filter_min_var.set("1")
                page.filter_max_var.set("")
                page._render_batch()
                self.assertEqual(
                    set(page.batch_tree.get_children()),
                    {"five", "ten"},
                )

                page.filter_min_var.set("0")
                page.filter_max_var.set("0")
                page._render_batch()
                self.assertEqual(
                    set(page.batch_tree.get_children()),
                    {"zero"},
                )

                page.filter_mode_var.set("全部商品")
                page.filter_min_var.set("")
                page.filter_max_var.set("")
                page._render_batch()
                page.batch_tree.selection_set(("zero",))
                with patch(
                    "weimi_manager.ui.upload_page.messagebox.showwarning"
                ) as warning:
                    page._create_tasks()
                warning.assert_called_once()
                self.assertEqual(page._tasks, [])

                page.batch_tree.selection_set(("zero", "ten"))
                page._update_batch_summary()
                self.assertEqual(
                    page.batch_tree.set("zero", "selected"),
                    "✓",
                )
                self.assertEqual(
                    page.batch_tree.set("five", "selected"),
                    "",
                )
                with patch(
                    "weimi_manager.ui.upload_page.messagebox.askyesno",
                    return_value=True,
                ):
                    page._delete_selected_batch()
                self.assertEqual(
                    [row["batch_id"] for row in page._batch],
                    ["five"],
                )
                saved = json.loads(db.get_setting("upload_batch", "[]"))
                self.assertEqual(
                    [row["batch_id"] for row in saved],
                    ["five"],
                )
            finally:
                root.destroy()

    def test_parse_json_package_and_apply_template(self) -> None:
        payload = {
            "products": [
                {
                    "title": "测试商品",
                    "price": "9.90",
                    "stock": 12,
                    "payload": {
                        "title": "测试商品",
                        "cats": [{"cat_id": "10000111"}],
                        "head_imgs": ["https://example.test/a.jpg"],
                        "skus": [{"sale_price": 990, "stock_num": 12}],
                    },
                }
            ]
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "products.json"
            path.write_text(
                json.dumps(payload, ensure_ascii=False),
                encoding="utf-8",
            )
            rows = UploadPage._parse_package(None, path)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["title"], "测试商品")
        self.assertEqual(rows[0]["price"], 990)
        page = object.__new__(UploadPage)
        result = page._upload_payload(
            rows[0],
            {
                "title_prefix": "【新品】",
                "title_suffix": " 包邮",
                "price_multiplier": 1.1,
                "price_offset": 100,
                "stock": 30,
            },
        )
        self.assertEqual(result["title"], "【新品】测试商品 包邮")
        self.assertEqual(result["skus"][0]["sale_price"], 1189)
        self.assertEqual(result["skus"][0]["stock_num"], 30)

    def test_parse_tsv_package(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "products.tsv"
            path.write_text(
                "title\tprice\tstock\tplatform\n"
                "测试商品\t19.90\t8\t淘宝\n",
                encoding="utf-8",
            )
            rows = UploadPage._parse_package(None, path)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["price"], 1990)
        self.assertEqual(rows[0]["platform"], "淘宝")

    def test_parse_taobao_source_package(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "658035244243.txt"
            path.write_text(
                json.dumps(
                    {
                        "productId": "658035244243",
                        "productName": "黑色魔术裤",
                        "priceLow": "39.00",
                        "itemImgs": ["//img.alicdn.com/example.jpg"],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            rows = UploadPage._parse_package(None, path)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["title"], "黑色魔术裤")
        self.assertEqual(rows[0]["source_product_id"], "658035244243")
        self.assertEqual(rows[0]["price"], 3900)
        self.assertEqual(rows[0]["platform"], "淘宝")

    def test_parse_taobao_sku_quantity_as_stock(self) -> None:
        payload = {
            "productId": "REPRO-TB-1",
            "productName": "淘宝库存解析复现",
            "productUrl": "https://item.taobao.com/item.htm?id=REPRO-TB-1",
            "price": "39.90",
            "skus": [
                {"skuId": "S1", "price": "3990", "quantity": 21},
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "taobao-sku-stock.txt"
            path.write_text(
                json.dumps(payload, ensure_ascii=False),
                encoding="utf-8",
            )
            rows = UploadPage._parse_package(None, path)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["price"], 3990)
        self.assertEqual(rows[0]["stock"], 21)
        self.assertTrue(rows[0]["stock_known"])

    def test_parse_taobao_nested_sku_list_stock_is_summed(self) -> None:
        payload = {
            "uniqueId": "TAOBAO-SKU-LIST",
            "title": "淘宝多规格商品",
            "skuList": {
                "skuInfo": [
                    {"skuId": "RED", "price": 1999, "stockNum": 3},
                    {"skuId": "BLUE", "price": 2099, "amountOnSale": "7"},
                ]
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "taobao-sku-list.txt"
            path.write_text(
                json.dumps(payload, ensure_ascii=False),
                encoding="utf-8",
            )
            rows = UploadPage._parse_package(None, path)

        self.assertEqual(rows[0]["stock"], 10)
        self.assertTrue(rows[0]["stock_known"])

    def test_parse_taobao_sku_base_and_sku_core_stock(self) -> None:
        payload = {
            "productId": "TAOBAO-SKU-CORE",
            "productName": "淘宝新版规格数据",
            "priceLow": "12.50",
            "skuBase": {
                "skus": [
                    {"skuId": "A"},
                    {"skuId": "B"},
                ]
            },
            "skuCore": {
                "sku2info": {
                    "A": {"quantity": 4},
                    "B": {"quantity": 6},
                }
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "taobao-sku-core.txt"
            path.write_text(
                json.dumps(payload, ensure_ascii=False),
                encoding="utf-8",
            )
            rows = UploadPage._parse_package(None, path)

        self.assertEqual(rows[0]["stock"], 10)
        self.assertTrue(rows[0]["stock_known"])

    def test_missing_stock_stays_unknown_until_template_is_applied(self) -> None:
        payload = {
            "productId": "TAOBAO-NO-STOCK",
            "productName": "库存字段缺失商品",
            "price": "18.80",
            "skus": [{"skuId": "ONLY", "price": "1880"}],
            "itemImgs": ["https://example.test/no-stock.jpg"],
            "categoryId": "50007068",
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "taobao-no-stock.txt"
            path.write_text(
                json.dumps(payload, ensure_ascii=False),
                encoding="utf-8",
            )
            rows = UploadPage._parse_package(None, path)

        self.assertIsNone(rows[0]["stock"])
        self.assertFalse(rows[0]["stock_known"])
        page = object.__new__(UploadPage)
        result = page._upload_payload(
            rows[0],
            {"stock": 30, "price_multiplier": 1, "price_offset": 0},
        )
        self.assertEqual(result["skus"][0]["stock_num"], 30)

    def test_taobao_missing_category_requests_automatic_official_match(
        self,
    ) -> None:
        payload = {
            "productId": "TAOBAO-SHOE-CATEGORY",
            "productName": "秋冬加绒保暖棉鞋女鞋",
            "price": "39.90",
            "itemImgs": ["https://example.test/shoe.jpg"],
            "skus": [
                {"skuId": "S1", "price": "3990", "quantity": 21}
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "taobao-shoe-category.txt"
            path.write_text(
                json.dumps(payload, ensure_ascii=False),
                encoding="utf-8",
            )
            rows = UploadPage._parse_package(None, path)
        page = object.__new__(UploadPage)

        result = page._upload_payload(
            rows[0],
            {"stock": 30, "price_multiplier": 1, "price_offset": 0},
        )

        self.assertTrue(result["_auto_category_required"])
        self.assertEqual(result["title"], "秋冬加绒保暖棉鞋女鞋")

    def test_existing_cats_v2_satisfies_category_requirement(self) -> None:
        page = object.__new__(UploadPage)

        result = page._upload_payload(
            {
                "title": "已有微信类目商品",
                "price": 3990,
                "stock": 10,
                "payload": {
                    "title": "已有微信类目商品",
                    "head_imgs": ["https://example.test/item.jpg"],
                    "cats_v2": [
                        {"cat_id": "10000173"},
                        {"cat_id": "10000175"},
                        {"cat_id": "10000673"},
                        {"cat_id": "547711"},
                    ],
                    "skus": [
                        {"sale_price": 3990, "stock_num": 10}
                    ],
                },
            },
            {"stock": 10, "price_multiplier": 1, "price_offset": 0},
        )

        self.assertNotIn("_auto_category_required", result)
        self.assertEqual(
            result["cats_v2"][-1]["cat_id"],
            "547711",
        )

    def test_taobao_missing_category_matches_official_category_tree(
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
        store = SimpleNamespace(id=7, name="测试店铺")
        payload = page._upload_payload(
            {
                "title": "秋冬加绒保暖棉鞋女鞋",
                "price": 3990,
                "stock": 21,
                "payload": {
                    "title": "秋冬加绒保暖棉鞋女鞋",
                    "head_imgs": ["https://example.test/shoe.jpg"],
                    "skus": [
                        {"sale_price": 3990, "stock_num": 21}
                    ],
                },
            },
            {"stock": 21, "price_multiplier": 1, "price_offset": 0},
        )
        payload.pop("_auto_category_required", None)
        with patch(
            "weimi_manager.ui.upload_page.ShopPrivateClient",
            return_value=SimpleNamespace(
                category_catalog=lambda: catalog
            ),
        ):
            result = page._auto_match_category(
                store,
                payload,
                {},
            )

        self.assertEqual(
            result["cats_v2"][-1]["cat_id"],
            "547711",
        )
        self.assertEqual(
            result["category_path"],
            "鞋靴 / 女鞋 / 棉鞋 / 保暖棉鞋",
        )

    def test_explicit_all_zero_sku_stock_remains_known_zero(self) -> None:
        payload = {
            "productId": "TAOBAO-ZERO",
            "productName": "真实零库存商品",
            "price": "9.90",
            "skus": [
                {"skuId": "A", "quantity": 0},
                {"skuId": "B", "quantity": 0},
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "taobao-zero.txt"
            path.write_text(
                json.dumps(payload, ensure_ascii=False),
                encoding="utf-8",
            )
            rows = UploadPage._parse_package(None, path)

        self.assertEqual(rows[0]["stock"], 0)
        self.assertTrue(rows[0]["stock_known"])

    def test_auto_category_applies_template_deposit_limit_as_hard_cap(
        self,
    ) -> None:
        page = object.__new__(UploadPage)
        page.db = object()
        page._category_catalog_cache = {}
        catalog = [
            {
                "category_id": "HIGH",
                "name": "保暖棉鞋",
                "path": "鞋靴 / 女鞋 / 棉鞋 / 保暖棉鞋",
                "id_path": ["100", "200", "HIGH"],
            },
            {
                "category_id": "LOW",
                "name": "加厚棉鞋",
                "path": "鞋靴 / 女鞋 / 棉鞋 / 加厚棉鞋",
                "id_path": ["100", "200", "LOW"],
            },
        ]

        class Client:
            def category_catalog(self):
                return catalog

            def category_application_checks(self, ids):
                return {
                    category_id: {
                        "state": "not_applied",
                        "label": "未申请",
                        "reason": "",
                        "eligible": True,
                        "deposit_amount": (
                            50000 if category_id == "HIGH" else 10000
                        ),
                        "deposit_balance": 0,
                        "deposit_shortfall": (
                            50000 if category_id == "HIGH" else 10000
                        ),
                    }
                    for category_id in ids
                }

        store = SimpleNamespace(id=7, name="保证金上限店铺")
        with patch(
            "weimi_manager.ui.upload_page.ShopPrivateClient",
            return_value=Client(),
        ):
            result = page._auto_match_category(
                store,
                {
                    "title": "女士冬季保暖加厚棉鞋",
                    "category_hint": "保暖棉鞋",
                },
                {"deposit_limit": 200},
            )

        self.assertEqual(result["category_apply_ids"], ["LOW"])
        self.assertEqual(
            result["_category_selected"]["deposit_amount"],
            10000,
        )

    def test_auto_category_sorts_compliant_candidates_by_deposit_then_score(
        self,
    ) -> None:
        page = object.__new__(UploadPage)
        page.db = object()
        page._category_catalog_cache = {}
        catalog = [
            {
                "category_id": "1000",
                "name": "女士运动鞋",
                "path": "鞋靴 / 女鞋 / 女士运动鞋",
                "id_path": ["10", "20", "1000"],
            },
            {
                "category_id": "2000",
                "name": "女士运动鞋",
                "path": "鞋靴 / 女鞋 / 运动鞋 / 女士运动鞋",
                "id_path": ["10", "20", "2000"],
            },
            {
                "category_id": "20000",
                "name": "女士运动鞋",
                "path": "鞋靴 / 女鞋 / 运动户外 / 女士运动鞋",
                "id_path": ["10", "20", "20000"],
            },
            {
                "category_id": "100",
                "name": "手机壳",
                "path": "数码 / 手机配件 / 手机壳",
                "id_path": ["90", "100"],
            },
            {
                "category_id": "700",
                "name": "女士休闲鞋",
                "path": "鞋靴 / 女鞋 / 女士休闲鞋",
                "id_path": ["10", "20", "700"],
            },
        ]

        class Client:
            qualification_missing = False

            def category_catalog(self):
                return catalog

            def category_application_checks(self, ids):
                return {
                    category_id: {
                        "state": (
                            "qualification_required"
                            if self.qualification_missing
                            and category_id == "1000"
                            else "not_applied"
                        ),
                        "label": (
                            "缺资质"
                            if self.qualification_missing
                            and category_id == "1000"
                            else "未申请"
                        ),
                        "reason": "",
                        "eligible": category_id != "1000"
                        or not self.qualification_missing,
                        "deposit_amount": {
                            "1000": 1000,
                            "2000": 2000,
                            "20000": 20000,
                            "100": 100,
                            "700": 700,
                        }[category_id],
                        "deposit_balance": 0,
                        "deposit_shortfall": 0,
                    }
                    for category_id in ids
                }

        client = Client()
        store = SimpleNamespace(id=7, name="候选排序店铺")
        with patch(
            "weimi_manager.ui.upload_page.ShopPrivateClient",
            return_value=client,
        ):
            selected = page._auto_match_category(
                store,
                {
                    "title": "女士运动鞋",
                    "category_hint": "女鞋 / 女士运动鞋",
                },
                {"deposit_limit": 300},
            )
            client.qualification_missing = True
            page._category_catalog_cache = {}
            fallback = page._auto_match_category(
                store,
                {
                    "title": "女士运动鞋",
                    "category_hint": "女鞋 / 女士运动鞋",
                },
                {"deposit_limit": 300},
            )

        self.assertEqual(selected["category_apply_ids"], ["1000"])
        candidates = {
            item["category_id"]: item
            for item in selected["_category_candidates"]
        }
        self.assertTrue(candidates["1000"]["selected"])
        self.assertNotIn("100", candidates)
        self.assertFalse(candidates["700"]["selected"])
        self.assertIn(
            "语义未命中",
            candidates["700"]["unselected_reason"],
        )
        self.assertNotIn("100", selected["category_apply_ids"])
        self.assertIn(
            "保证金高于",
            candidates["2000"]["unselected_reason"],
        )
        self.assertEqual(fallback["category_apply_ids"], ["2000"])

    def test_auto_category_over_limit_is_kept_as_upload_candidate(self) -> None:
        page = object.__new__(UploadPage)
        page.db = object()
        page._category_catalog_cache = {}
        catalog = [
            {
                "category_id": "HIGH",
                "name": "保暖棉鞋",
                "path": "鞋靴 / 女鞋 / 棉鞋 / 保暖棉鞋",
                "id_path": ["100", "200", "HIGH"],
            }
        ]

        class Client:
            def category_catalog(self):
                return catalog

            def category_application_checks(self, ids):
                return {
                    "HIGH": {
                        "state": "deposit_required",
                        "label": "保证金不足",
                        "reason": "需补缴 30000 分",
                        "eligible": False,
                        "deposit_amount": 30000,
                        "deposit_balance": 0,
                        "deposit_shortfall": 30000,
                    }
                }

        store = SimpleNamespace(id=7, name="保证金不足店铺")
        with patch(
            "weimi_manager.ui.upload_page.ShopPrivateClient",
            return_value=Client(),
        ):
            selected = page._auto_match_category(
                store,
                {
                    "title": "女士冬季保暖棉鞋",
                    "category_hint": "保暖棉鞋",
                },
                {"deposit_limit": 200},
            )

        self.assertEqual(selected["category_apply_ids"], ["HIGH"])
        self.assertEqual(
            selected["auto_category"]["deposit_amount"],
            30000,
        )

    def test_fallback_does_not_override_credible_auto_match_when_disabled(
        self,
    ) -> None:
        page = object.__new__(UploadPage)
        page.db = object()
        page._category_catalog_cache = {}
        catalog = [
            {
                "category_id": "AUTO",
                "name": "保暖棉鞋",
                "path": "鞋靴 / 女鞋 / 棉鞋 / 保暖棉鞋",
                "id_path": ["100", "200", "AUTO"],
            },
            {
                "category_id": "FALLBACK",
                "name": "手机壳",
                "path": "数码 / 手机配件 / 手机壳",
                "id_path": ["900", "FALLBACK"],
            },
        ]

        class Client:
            def category_catalog(self):
                return catalog

            def category_application_checks(self, ids):
                return {
                    category_id: {
                        "state": "not_applied",
                        "label": "未申请",
                        "reason": "",
                        "eligible": True,
                        "deposit_amount": (
                            50000 if category_id == "300" else 10000
                        ),
                        "deposit_balance": 10000,
                        "deposit_shortfall": (
                            40000 if category_id == "300" else 0
                        ),
                    }
                    for category_id in ids
                }

        with patch(
            "weimi_manager.ui.upload_page.ShopPrivateClient",
            return_value=Client(),
        ):
            result = page._auto_match_category(
                SimpleNamespace(id=7, name="自动匹配店铺"),
                {
                    "title": "女士冬季保暖棉鞋",
                    "category_hint": "保暖棉鞋",
                },
                {
                    "fallback_category": "FALLBACK",
                    "force_fallback_category": False,
                    "deposit_limit": 200,
                },
            )

        self.assertEqual(result["category_apply_ids"], ["AUTO"])
        self.assertEqual(
            result["_category_selected"]["selection_source"],
            "auto",
        )

    def test_force_fallback_never_overrides_lower_deposit_auto_match(
        self,
    ) -> None:
        page = object.__new__(UploadPage)
        page.db = object()
        page._category_catalog_cache = {}
        catalog = [
            {
                "category_id": "300",
                "name": "保暖棉鞋",
                "path": "鞋靴 / 女鞋 / 棉鞋 / 保暖棉鞋",
                "id_path": ["100", "200", "300"],
            },
            {
                "category_id": "400",
                "name": "加厚棉鞋",
                "path": "鞋靴 / 女鞋 / 棉鞋 / 加厚棉鞋",
                "id_path": ["100", "200", "400"],
            },
            {
                "category_id": "500",
                "name": "手机壳",
                "path": "数码 / 手机配件 / 手机壳",
                "id_path": ["900", "500"],
            },
        ]

        class Client:
            def category_catalog(self):
                return catalog

            def category_application_checks(self, ids):
                return {
                    category_id: {
                        "state": "not_applied",
                        "label": "未申请",
                        "reason": "",
                        "eligible": True,
                        "deposit_amount": (
                            50000 if category_id == "300" else 10000
                        ),
                        "deposit_balance": 10000,
                        "deposit_shortfall": (
                            40000 if category_id == "300" else 0
                        ),
                    }
                    for category_id in ids
                }

        store = SimpleNamespace(id=7, name="强制兜底店铺")
        payload = {
            "title": "女士冬季保暖加厚棉鞋",
            "category_hint": "保暖棉鞋",
        }
        with patch(
            "weimi_manager.ui.upload_page.ShopPrivateClient",
            return_value=Client(),
        ):
            valid = page._auto_match_category(
                store,
                dict(payload),
                {
                    "fallback_category": '["100", "200", "400"]',
                    "force_fallback_category": True,
                    "deposit_limit": 200,
                },
            )

        self.assertEqual(valid["category_apply_ids"], ["400"])
        self.assertEqual(
            valid["_category_selected"]["selection_source"],
            "auto",
        )

    def test_force_fallback_does_not_override_semantic_leaf_over_deposit_cap(
        self,
    ) -> None:
        page = object.__new__(UploadPage)
        page.db = object()
        page._category_catalog_cache = {}
        catalog = [
            {
                "category_id": "300",
                "name": "保暖棉鞋",
                "path": "鞋靴 / 女鞋 / 棉鞋 / 保暖棉鞋",
                "id_path": ["100", "200", "300"],
            },
            {
                "category_id": "500",
                "name": "手机壳",
                "path": "数码 / 手机配件 / 手机壳",
                "id_path": ["900", "500"],
            },
        ]

        class Client:
            def category_catalog(self):
                return catalog

            def category_application_checks(self, ids):
                return {
                    category_id: {
                        "state": "not_applied",
                        "label": "未申请",
                        "reason": "",
                        "eligible": True,
                        "deposit_amount": (
                            50000 if category_id == "300" else 10000
                        ),
                        "deposit_balance": 10000,
                        "deposit_shortfall": 0,
                    }
                    for category_id in ids
                }

        with patch(
            "weimi_manager.ui.upload_page.ShopPrivateClient",
            return_value=Client(),
        ):
            selected = page._auto_match_category(
                SimpleNamespace(id=7, name="强制兜底店铺"),
                {
                    "title": "女士冬季保暖加厚棉鞋",
                    "category_hint": "保暖棉鞋",
                },
                {
                    "fallback_category": "500",
                    "force_fallback_category": True,
                    "deposit_limit": 200,
                },
            )

        self.assertEqual(selected["category_apply_ids"], ["300"])
        self.assertEqual(
            selected["_category_selected"]["selection_source"],
            "auto",
        )

    def test_old_taobao_batch_rows_are_renormalized_from_raw_skus(self) -> None:
        page = object.__new__(UploadPage)
        raw = {
            "productId": "OLD-TAOBAO-ROW",
            "productName": "旧批次淘宝商品",
            "skus": [{"skuId": "S1", "quantity": 21}],
        }
        page._batch = [
            {
                "batch_id": "old-row",
                "source_product_id": "OLD-TAOBAO-ROW",
                "title": "旧批次淘宝商品",
                "price": 3990,
                "stock": 0,
                "platform": "淘宝",
                "payload": raw,
                "raw": raw,
            }
        ]

        page._normalize_batch_rows()

        self.assertEqual(page._batch[0]["stock"], 21)
        self.assertTrue(page._batch[0]["stock_known"])

    def test_taobao_nested_and_unknown_stock_can_create_tasks(self) -> None:
        class FakeWindow:
            def __init__(self, db) -> None:
                self.db = db

            def set_status(self, *_args, **_kwargs) -> None:
                pass

        package_payload = {
            "products": [
                {
                    "productId": "TAOBAO-NESTED-21",
                    "productName": "淘宝嵌套库存商品",
                    "price": "39.90",
                    "itemImgs": ["https://example.test/nested.jpg"],
                    "categoryId": "50007068",
                    "skus": [
                        {"skuId": "S1", "price": "3990", "quantity": 21}
                    ],
                },
                {
                    "productId": "TAOBAO-UNKNOWN",
                    "productName": "淘宝未知库存商品",
                    "price": "29.90",
                    "itemImgs": ["https://example.test/unknown.jpg"],
                    "categoryId": "50007068",
                    "skus": [{"skuId": "S2", "price": "2990"}],
                },
            ]
        }
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "taobao-stock-task.db")
            package = Path(tmp) / "taobao-stock-task.json"
            package.write_text(
                json.dumps(package_payload, ensure_ascii=False),
                encoding="utf-8",
            )
            try:
                root = tk.Tk()
            except tk.TclError as exc:
                self.skipTest(str(exc))
            root.withdraw()
            apply_theme(root)
            try:
                page = UploadPage(root, FakeWindow(db))
                page._import_files([package])
                self.assertEqual(
                    [row["stock"] for row in page._batch],
                    [21, None],
                )
                page._templates = [
                    {
                        "name": "淘宝默认库存模板",
                        "price_multiplier": 1,
                        "price_offset": 0,
                        "stock": 30,
                    }
                ]
                page.template_var.set("淘宝默认库存模板")
                store = Store(
                    id=1,
                    name="淘宝测试店铺",
                    app_id="wx-taobao-test",
                    group_name="",
                    status="正常",
                    last_sync_at=None,
                    created_at="",
                )
                with patch.object(
                    page,
                    "_choose_stores",
                    return_value=[store],
                ):
                    page._create_tasks()
                self.assertEqual(len(page._tasks), 1)
                self.assertEqual(
                    {
                        item["title"]
                        for item in page._tasks[0]["items"]
                    },
                    {
                        "淘宝嵌套库存商品",
                        "淘宝未知库存商品",
                    },
                )
            finally:
                root.destroy()

    def test_parse_wechat_source_package_price_and_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "[10000024298905].txt"
            path.write_text(
                json.dumps(
                    {
                        "uniqueId": "10000024298905",
                        "title": "防晒帽",
                        "skuList": {
                            "skuInfo": [
                                {"price": 2299},
                                {"price": 1999},
                            ]
                        },
                        "totalStockNum": "12",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            rows = UploadPage._parse_package(None, path)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["title"], "防晒帽")
        self.assertEqual(rows[0]["source_product_id"], "10000024298905")
        self.assertEqual(rows[0]["price"], 1999)
        self.assertEqual(rows[0]["stock"], 12)
        self.assertEqual(rows[0]["platform"], "微信小店")

    def test_missing_source_ids_use_stable_distinct_fingerprints(self) -> None:
        payload = {
            "products": [
                {
                    "title": "无 ID 商品甲",
                    "platform": "淘宝",
                    "price": "12.00",
                    "specs": {"颜色": ["黑色"]},
                },
                {
                    "title": "无 ID 商品乙",
                    "platform": "淘宝",
                    "price": "15.00",
                    "specs": {"颜色": ["白色"]},
                },
            ]
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "同一数据包.json"
            path.write_text(
                json.dumps(payload, ensure_ascii=False),
                encoding="utf-8",
            )
            first = UploadPage._parse_package(None, path)
            second = UploadPage._parse_package(None, path)

        first_ids = [row["source_product_id"] for row in first]
        second_ids = [row["source_product_id"] for row in second]
        self.assertEqual(len(set(first_ids)), 2)
        self.assertNotIn("同一数据包", first_ids)
        self.assertEqual(first_ids, second_ids)
        self.assertTrue(
            all(source_id.startswith("source-fp-") for source_id in first_ids)
        )

    def test_exactly_duplicated_missing_ids_are_stable_and_distinct(self) -> None:
        product = {
            "title": "完全相同的无 ID 商品",
            "platform": "微信小店",
            "price": "9.90",
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "duplicate.json"
            path.write_text(
                json.dumps(
                    {"products": [product, product]},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            first = UploadPage._parse_package(None, path)
            second = UploadPage._parse_package(None, path)

        first_ids = [row["source_product_id"] for row in first]
        second_ids = [row["source_product_id"] for row in second]
        self.assertEqual(len(set(first_ids)), 2)
        self.assertEqual(first_ids, second_ids)
        self.assertTrue(first_ids[1].endswith("-2"))

    def test_same_missing_id_product_across_files_gets_same_fingerprint(
        self,
    ) -> None:
        product = {
            "title": "跨文件相同商品",
            "platform": "淘宝",
            "price": "18.80",
            "specs": {"颜色": ["米白"]},
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first_path = root / "第一批.json"
            second_path = root / "第二批.json"
            text = json.dumps(product, ensure_ascii=False)
            first_path.write_text(text, encoding="utf-8")
            second_path.write_text(text, encoding="utf-8")
            first = UploadPage._parse_package(None, first_path)
            second = UploadPage._parse_package(None, second_path)

        self.assertEqual(
            first[0]["source_product_id"],
            second[0]["source_product_id"],
        )

    def test_raw_taobao_package_builds_wechat_upload_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "taobao.txt"
            path.write_text(
                json.dumps(
                    {
                        "productId": "TB-1",
                        "productName": "高腰休闲裤",
                        "priceLow": "20.00",
                        "categoryId": "162201",
                        "categoryName": "女装/女士精品/裤子/休闲裤",
                        "itemImgs": [
                            "//img.alicdn.com/example-1.jpg",
                            "//img.alicdn.com/example-2.jpg",
                            "//img.alicdn.com/example-3.jpg",
                        ],
                        "specs": {
                            "颜色分类": {
                                "1": ["黑色", ""],
                            },
                            "尺码": {
                                "2": ["M", ""],
                            },
                        },
                        "skus": [
                            {
                                "price": "2000",
                                "quantity": "12",
                                "id": "1;2",
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            rows = UploadPage._parse_package(None, path)
        result = object.__new__(UploadPage)._upload_payload(
            rows[0],
            {"price_multiplier": 1.5, "price_offset": 0, "stock": 1000},
        )
        self.assertEqual(result["title"], "高腰休闲裤")
        self.assertEqual(
            [item["cat_id"] for item in result["cats"]],
            ["6033", "6057", "547675"],
        )
        self.assertEqual(result["head_imgs"][0], "https://img.alicdn.com/example-1.jpg")
        self.assertEqual(result["listing"], 1)
        self.assertEqual(result["skus"][0]["sale_price"], 3000)
        self.assertEqual(result["skus"][0]["stock_num"], 1000)
        self.assertEqual(
            {item["attr_key"] for item in result["skus"][0]["sku_attrs"]},
            {"颜色", "规格"},
        )

    def test_normalized_wechat_row_gets_default_sku_attribute(self) -> None:
        normalized = _normalize_source_payload(
            {
                "platform": "微信小店",
                "source_product_id": "WX-NORMALIZED",
                "out_product_id": "WX-NORMALIZED",
                "title": "已标准化微信商品",
                "head_imgs": ["https://example.test/main.jpg"],
                "skus": [
                    {
                        "out_sku_id": "WX-SKU-1",
                        "sale_price": 1200,
                        "stock_num": 6,
                    }
                ],
            }
        )

        self.assertEqual(normalized["source_product_id"], "WX-NORMALIZED")
        self.assertEqual(normalized["skus"][0]["out_sku_id"], "WX-SKU-1")
        self.assertEqual(
            normalized["skus"][0]["sku_attrs"],
            [{"attr_key": "规格", "attr_value": "默认规格"}],
        )

    def test_template_controls_immediate_listing_and_draft(self) -> None:
        page = object.__new__(UploadPage)
        item = {
            "title": "发布方式测试",
            "price": 100,
            "stock": 5,
            "payload": {
                "title": "发布方式测试",
                "cats": [{"cat_id": "10000111"}],
                "head_imgs": ["https://example.test/a.jpg"],
                "skus": [{"sale_price": 100, "stock_num": 5}],
            },
        }
        immediate = page._upload_payload(
            item,
            {"name": "立即上架", "price_multiplier": 1},
        )
        draft = page._upload_payload(
            item,
            {
                "name": "草稿",
                "price_multiplier": 1,
                "save_as_draft": True,
            },
        )
        delayed = page._upload_payload(
            item,
            {
                "name": "延迟",
                "price_multiplier": 1,
                "listing_mode": "delayed",
            },
        )
        self.assertEqual(immediate["listing"], 1)
        self.assertEqual(draft["listing"], 0)
        self.assertEqual(delayed["listing"], 0)

    def test_template_store_options_override_source_defaults(self) -> None:
        page = object.__new__(UploadPage)
        item = {
            "title": "店铺配置测试",
            "price": 100,
            "stock": 5,
            "payload": {
                "title": "店铺配置测试",
                "cats_v2": [{"cat_id": "10000111"}],
                "head_imgs": ["https://example.test/a.jpg"],
                "skus": [{"sale_price": 100, "stock_num": 5}],
                "express_info": {"template_id": "old-freight"},
                "after_sale_info": {
                    "after_sale_address_id": "old-address",
                },
                "brand_id": "old-brand",
            },
        }

        result = page._upload_payload(
            item,
            {
                "name": "店铺配置",
                "freight_template_id": "new-freight",
                "brand_id": "new-brand",
                "after_sale_address_id": "new-address",
            },
        )

        self.assertEqual(
            result["express_info"]["template_id"],
            "new-freight",
        )
        self.assertEqual(result["brand_id"], "new-brand")
        self.assertEqual(
            result["after_sale_info"]["after_sale_address_id"],
            "new-address",
        )

    def test_cloud_violation_type_filter_does_not_subtract_list_and_set(
        self,
    ) -> None:
        class FakeDb:
            @staticmethod
            def violation_blacklist_ids():
                return set()

            @staticmethod
            def get_setting(key, default=""):
                if key == "violation_library":
                    return json.dumps(
                        [
                            {
                                "product_id": "SOURCE-BAD",
                                "title": "明确违规商品",
                                "reason": "明确违规",
                            }
                        ],
                        ensure_ascii=False,
                    )
                return default

        page = object.__new__(UploadPage)
        page.db = FakeDb()
        for platform in ("1688", "淘宝", "微信小店"):
            with self.subTest(platform=platform):
                item = {
                    "source_product_id": "SOURCE-BAD",
                    "title": "明确违规商品",
                    "price": 100,
                    "stock": 5,
                    "payload": {
                        "title": "明确违规商品",
                        "platform": platform,
                        "cats": [{"cat_id": "10000111"}],
                        "head_imgs": ["https://example.test/a.jpg"],
                        "skus": [
                            {"sale_price": 100, "stock_num": 5}
                        ],
                    },
                }

                with self.assertRaises(TemplateRuleSkip):
                    page._upload_payload(
                        item,
                        {
                            "name": "云违规过滤",
                            "price_multiplier": 1,
                            "cloud_violation": True,
                            "cloud_violation_types": "明确违规",
                        },
                    )

    def test_cloud_violation_filter_blocks_exact_counterfeit_title(
        self,
    ) -> None:
        class FakeDb:
            @staticmethod
            def violation_blacklist_ids():
                return set()

            @staticmethod
            def get_setting(key, default=""):
                if key == "upload_violation_titles":
                    return json.dumps(
                        [
                            {
                                "title": "毛绒花束捧花｜520生日礼物",
                                "normalized": "毛绒花束捧花｜520生日礼物",
                                "reason": "出售假冒商品",
                            }
                        ],
                        ensure_ascii=False,
                    )
                return default

        page = object.__new__(UploadPage)
        page.db = FakeDb()
        item = {
            "source_product_id": "SOURCE-NEW",
            "title": "毛绒花束捧花｜520生日礼物",
            "price": 100,
            "stock": 5,
            "payload": {
                "title": "毛绒花束捧花｜520生日礼物",
                "platform": "淘宝",
                "cats": [{"cat_id": "10000111"}],
                "head_imgs": ["https://example.test/a.jpg"],
                "skus": [{"sale_price": 100, "stock_num": 5}],
            },
        }

        with self.assertRaisesRegex(
            TemplateRuleSkip,
            "云违规标题过滤命中：出售假冒商品",
        ):
            page._upload_payload(
                item,
                {
                    "name": "云违规标题过滤",
                    "price_multiplier": 1,
                    "cloud_violation": False,
                },
            )

    def test_cloud_violation_filter_uses_final_template_title(self) -> None:
        class FakeDb:
            @staticmethod
            def violation_blacklist_ids():
                return set()

            @staticmethod
            def get_setting(key, default=""):
                if key == "upload_violation_titles":
                    return json.dumps(
                        [
                            {
                                "title": "新品·新款售假标题 热卖",
                                "normalized": "新品·新款售假标题 热卖",
                                "reason": "出售假冒商品",
                            }
                        ],
                        ensure_ascii=False,
                    )
                return default

        page = object.__new__(UploadPage)
        page.db = FakeDb()
        with self.assertRaisesRegex(
            TemplateRuleSkip,
            "云违规标题过滤命中：出售假冒商品",
        ):
            page._upload_payload(
                {
                    "source_product_id": "SOURCE-FINAL-TITLE",
                    "title": "【促销】旧款售假标题",
                    "price": 100,
                    "payload": {
                        "title": "【促销】旧款售假标题",
                        "platform": "淘宝",
                        "cats": [{"cat_id": "10000111"}],
                        "head_imgs": ["https://example.test/a.jpg"],
                        "skus": [{"sale_price": 100, "stock_num": 5}],
                    },
                },
                {
                    "strip_title_brackets": True,
                    "title_replace_rules": "旧款=新款",
                    "title_prefix": "新品·",
                    "title_suffix": " 热卖",
                    "cloud_violation": True,
                },
            )

    def test_cloud_violation_blocks_original_title_after_rewrite(self) -> None:
        class FakeDb:
            @staticmethod
            def violation_blacklist_ids():
                return set()

            @staticmethod
            def get_setting(key, default=""):
                if key == "upload_violation_titles":
                    return json.dumps(
                        [
                            {
                                "title": "真实售假标题",
                                "normalized": "真实售假标题",
                                "reason": "出售假冒商品",
                            }
                        ],
                        ensure_ascii=False,
                    )
                return default

        page = object.__new__(UploadPage)
        page.db = FakeDb()
        with self.assertRaisesRegex(
            TemplateRuleSkip,
            "云违规标题过滤命中：出售假冒商品",
        ):
            page._upload_payload(
                {
                    "source_product_id": "SOURCE-REWRITTEN",
                    "title": "真实售假标题",
                    "price": 100,
                    "payload": {
                        "title": "真实售假标题",
                        "platform": "淘宝",
                        "cats": [{"cat_id": "10000111"}],
                        "head_imgs": ["https://example.test/a.jpg"],
                        "skus": [{"sale_price": 100, "stock_num": 5}],
                    },
                },
                {
                    "title_replace_rules": "真实售假标题=完全干净标题",
                    "cloud_violation": False,
                },
            )

    def test_duplicate_filter_defaults_to_store_and_can_be_disabled(
        self,
    ) -> None:
        self.assertEqual(_normalize_duplicate_filter_mode({}), "store")
        self.assertEqual(
            _normalize_duplicate_filter_mode(
                {"duplicate_filter_mode": "computer"}
            ),
            "computer",
        )
        self.assertEqual(
            _normalize_duplicate_filter_mode(
                {"duplicate_filter_mode": "off"}
            ),
            "off",
        )
        self.assertEqual(
            _normalize_duplicate_filter_mode(
                {
                    "duplicate_filter_mode": "off",
                    "skip_duplicate": False,
                }
            ),
            "off",
        )

    def test_duplicate_filter_scopes_are_isolated(self) -> None:
        by_store = {1: {"SOURCE-A"}, 2: {"SOURCE-B"}}
        by_computer = {"SOURCE-A", "SOURCE-B"}
        self.assertTrue(
            _is_duplicate_upload(
                "store",
                "SOURCE-A",
                1,
                by_store,
                by_computer,
            )
        )
        self.assertFalse(
            _is_duplicate_upload(
                "store",
                "SOURCE-A",
                2,
                by_store,
                by_computer,
            )
        )
        self.assertTrue(
            _is_duplicate_upload(
                "computer",
                "SOURCE-A",
                2,
                by_store,
                by_computer,
            )
        )
        self.assertFalse(
            _is_duplicate_upload(
                "off",
                "SOURCE-A",
                1,
                by_store,
                by_computer,
            )
        )

    def test_upload_payload_normalizes_string_categories(self) -> None:
        page = object.__new__(UploadPage)
        result = page._upload_payload(
            {
                "title": "字符串类目测试",
                "price": 100,
                "stock": 1,
                "payload": {
                    "title": "字符串类目测试",
                    "cats": ["6033", "6093", "6098"],
                    "cats_v2": ["10000111", "10000114", "6098"],
                    "head_imgs": [
                        "https://example.test/1.jpg",
                        "https://example.test/2.jpg",
                        "https://example.test/3.jpg",
                    ],
                    "skus": [{"sale_price": 100, "stock_num": 1}],
                },
            },
            {"price_multiplier": 1, "stock": 1},
        )
        self.assertEqual(
            result["cats"],
            [
                {"cat_id": "6033"},
                {"cat_id": "6093"},
                {"cat_id": "6098"},
            ],
        )
        self.assertEqual(
            result["cats_v2"],
            [
                {"cat_id": "10000111"},
                {"cat_id": "10000114"},
                {"cat_id": "6098"},
            ],
        )

    def test_upload_tasks_run_products_concurrently(self) -> None:
        store = type(
            "StoreValue",
            (),
            {"id": 7, "name": "并发测试店"},
        )()
        state = {
            "active": 0,
            "max_active": 0,
            "lock": threading.Lock(),
        }

        class FakeService:
            @staticmethod
            def client_for(_store):
                return object()

            @staticmethod
            def product_write(
                _store,
                _action,
                _payload,
                client=None,
                listing_mode="",
            ):
                assert client is not None
                assert listing_mode == "immediate"
                with state["lock"]:
                    state["active"] += 1
                    state["max_active"] = max(
                        state["max_active"],
                        state["active"],
                    )
                try:
                    time.sleep(0.04)
                finally:
                    with state["lock"]:
                        state["active"] -= 1
                return {
                    "errcode": 0,
                    "product_id": f"P-{uuid.uuid4().hex[:6]}",
                }

        class FakeWindow:
            service = FakeService()
            refreshed_store_ids: set[int] = set()

            def set_status(self, *_args, **_kwargs) -> None:
                pass

            def store_data_changed(self) -> None:
                pass

            @staticmethod
            def _post(*_args, **_kwargs) -> None:
                pass

            def run_task(
                self,
                work,
                done,
                _error,
                progress=None,
                log_title="",
            ) -> bool:
                done(work(progress))
                return True

        class FakeTree:
            @staticmethod
            def selection():
                return ("0",)

        class FakeStorePage:
            @staticmethod
            def refresh_after_upload(store_ids):
                FakeWindow.refreshed_store_ids.update(store_ids)

        items = [
            {
                "title": f"商品{index}",
                "payload": {"title": f"商品{index}"},
                "source_product_id": f"SOURCE-{index}",
                "status": "pending",
                "message": "",
                "product_id": "",
            }
            for index in range(12)
        ]
        task = {
            "id": "task-concurrent",
            "store_id": 7,
            "store_name": "并发测试店",
            "template": {
                "name": "并发模板",
                "skip_duplicate": True,
                "listing_mode": "immediate",
            },
            "status": "pending",
            "items": items,
            "message": "等待开始",
        }
        page = object.__new__(UploadPage)
        page.window = FakeWindow()
        page.window.store_page = FakeStorePage()
        page.db = type(
            "FakeDb",
            (),
            {
                "get_store": staticmethod(lambda _store_id: store),
                "record_upload_history": staticmethod(
                    lambda *_args, **_kwargs: None
                ),
                "uploaded_source_ids": staticmethod(
                    lambda _store_id=None: set()
                ),
            },
        )()
        page._tasks = [task]
        page._task_rows = {"0": task}
        page.task_tree = FakeTree()
        page._running = False
        page._paused = False
        page._task_lock = threading.RLock()
        page._save_tasks = lambda: None
        page._render_tasks = lambda: None
        page._progress = lambda *_args: None
        page._upload_payload = lambda item, _template: dict(
            item["payload"]
        )
        with (
            patch.dict(os.environ, {"WEIMI_UPLOAD_WORKERS": "4"}),
            patch(
                "weimi_manager.ui.upload_page.messagebox.askyesno",
                return_value=True,
            ),
            patch(
                "weimi_manager.ui.upload_page.messagebox.showwarning"
            ) as warning,
        ):
            page._start_tasks()
        if warning.called:
            self.fail(
                "unexpected warning: "
                + repr(
                    [str(value) for value in warning.call_args.args]
                )
            )
        self.assertGreaterEqual(state["max_active"], 2)
        self.assertTrue(
            all(item["status"] == "success" for item in items),
            [(item["status"], item.get("message")) for item in items],
        )
        self.assertEqual(task["status"], "completed")
        self.assertEqual(FakeWindow.refreshed_store_ids, {7})

    def test_upload_task_auto_applies_missing_category_before_retry(
        self,
    ) -> None:
        store = type(
            "StoreValue",
            (),
            {"id": 9, "name": "类目自动开通店"},
        )()
        product_attempts = []
        category_calls = []

        class FakeService:
            @staticmethod
            def client_for(_store):
                return object()

            @staticmethod
            def product_write(
                _store,
                _action,
                _payload,
                client=None,
                listing_mode="",
            ):
                product_attempts.append(listing_mode)
                if len(product_attempts) == 1:
                    raise WechatApiError(
                        "商品无该类目权限",
                        code=6600145,
                    )
                return {"errcode": 0, "product_id": "P-CATEGORY"}

        class FakeCategoryClient:
            def __init__(self, _db, _store) -> None:
                self.applied = False

            def category_relation_statuses(self, category_ids):
                if not self.applied:
                    return {}
                return {
                    str(category_id): "已生效"
                    for category_id in category_ids
                }

            def batch_add_categories(self, category_ids):
                category_calls.append(tuple(category_ids))
                self.applied = True
                return {"success": True}

        class FakeWindow:
            service = FakeService()
            diagnostics = None

            def set_status(self, *_args, **_kwargs) -> None:
                pass

            def store_data_changed(self) -> None:
                pass

            def run_task(
                self,
                work,
                done,
                _error,
                progress=None,
                log_title="",
            ) -> bool:
                done(work(progress))
                return True

        class FakeTree:
            @staticmethod
            def selection():
                return ("0",)

        class FakeStorePage:
            @staticmethod
            def refresh_after_upload(_store_ids):
                pass

        item = {
            "item_id": "category-item",
            "title": "类目自动开通商品",
            "source_product_id": "SOURCE-CATEGORY",
            "status": "pending",
            "payload": {
                "title": "类目自动开通商品",
                "cats": [{"cat_id": "101"}],
            },
        }
        task = {
            "id": "task-category",
            "store_id": 9,
            "store_name": "类目自动开通店",
            "template": {
                "name": "自动类目模板",
                "duplicate_filter_mode": "off",
            },
            "status": "pending",
            "items": [item],
        }
        page = object.__new__(UploadPage)
        page.window = FakeWindow()
        page.window.store_page = FakeStorePage()
        page.db = type(
            "FakeDb",
            (),
            {
                "get_store": staticmethod(lambda _store_id: store),
                "record_upload_history": staticmethod(
                    lambda *_args, **_kwargs: None
                ),
                "uploaded_source_ids": staticmethod(
                    lambda _store_id=None: set()
                ),
            },
        )()
        page._tasks = [task]
        page._task_rows = {"0": task}
        page.task_tree = FakeTree()
        page._running = False
        page._paused = False
        page._task_lock = threading.RLock()
        page._save_tasks = lambda: None
        page._render_tasks = lambda: None
        page._progress = lambda *_args: None
        page._upload_payload = lambda item_row, _template: dict(
            item_row["payload"]
        )
        with (
            patch(
                "weimi_manager.ui.upload_page.ShopPrivateClient",
                FakeCategoryClient,
            ),
            patch(
                "weimi_manager.ui.upload_page.messagebox.askyesno",
                return_value=True,
            ),
        ):
            page._start_tasks()

        self.assertEqual(category_calls, [("101",)])
        self.assertEqual(product_attempts, ["immediate", "immediate"])
        self.assertEqual(item["status"], "success")
        self.assertEqual(item["product_id"], "P-CATEGORY")

    def test_rejected_scheduler_does_not_leave_upload_running(self) -> None:
        class FakeTree:
            @staticmethod
            def selection():
                return ()

        class FakeWindow:
            def __init__(self) -> None:
                self.messages = []

            def set_status(self, message, **_kwargs) -> None:
                self.messages.append(message)

            def run_task(self, *_args, **_kwargs) -> bool:
                self.set_status("已有任务正在执行")
                return False

        task = {
            "id": "task-rejected",
            "store_id": 7,
            "store_name": "拒绝测试店",
            "template": {"name": "测试模板"},
            "status": "pending",
            "items": [
                {
                    "item_id": "item-rejected",
                    "title": "拒绝测试商品",
                    "source_product_id": "SOURCE-REJECTED",
                    "status": "pending",
                    "payload": {"title": "拒绝测试商品"},
                }
            ],
        }
        page = object.__new__(UploadPage)
        page.window = FakeWindow()
        page._tasks = [task]
        page._task_rows = {"0": task}
        page.task_tree = FakeTree()
        page._running = False
        page._paused = False
        page._task_lock = threading.RLock()
        page._save_tasks = Mock()
        page._render_tasks = Mock()
        page._progress = Mock()

        with (
            patch(
                "weimi_manager.ui.upload_page.messagebox.askyesno",
                return_value=True,
            ),
            patch(
                "weimi_manager.ui.upload_page.messagebox.showwarning"
            ),
        ):
            page._start_tasks()

        self.assertFalse(page._running)
        self.assertEqual(task["status"], "pending")
        self.assertEqual(task["items"][0]["status"], "pending")
        page._save_tasks.assert_not_called()
        self.assertIn("已有任务正在执行", page.window.messages)

    def test_refresh_does_not_replace_running_task_truth_source(self) -> None:
        original_task = {
            "id": "task-running",
            "store_id": 7,
            "store_name": "运行中店铺",
            "status": "running",
            "items": [
                {
                    "item_id": "item-success",
                    "title": "已完成商品",
                    "source_product_id": "SOURCE-SUCCESS",
                    "status": "success",
                    "product_id": "P_TEST_001",
                }
            ],
        }
        persisted_task = {
            "id": "task-running",
            "store_id": 7,
            "store_name": "运行中店铺",
            "status": "running",
            "items": [
                {
                    "item_id": "item-success",
                    "title": "已完成商品",
                    "source_product_id": "SOURCE-SUCCESS",
                    "status": "pending",
                    "product_id": "",
                }
            ],
        }

        class FakeDb:
            @staticmethod
            def get_setting(key, default):
                if key == "upload_tasks":
                    return json.dumps([persisted_task], ensure_ascii=False)
                return default

        page = object.__new__(UploadPage)
        page.db = FakeDb()
        page._tasks = [original_task]
        page._templates = []
        page._batch = []
        page._task_lock = threading.RLock()
        page._running = True
        page._run_requested = False
        page._render_templates = Mock()
        page._render_batch = Mock()
        page._render_tasks = Mock()
        page._render_category_stores = Mock()

        for _ in range(30):
            page.refresh()

        self.assertIs(page._tasks[0], original_task)
        self.assertEqual(
            page._tasks[0]["items"][0]["product_id"],
            "P_TEST_001",
        )

    def test_running_upload_cannot_clear_or_delete_tasks(self) -> None:
        task = {
            "id": "task-active",
            "status": "running",
            "items": [
                {
                    "item_id": "item-active",
                    "status": "submitting",
                }
            ],
        }

        class FakeTree:
            @staticmethod
            def selection():
                return ("0",)

        class FakeWindow:
            def __init__(self) -> None:
                self.messages = []

            def set_status(self, message, **_kwargs) -> None:
                self.messages.append(message)

        page = object.__new__(UploadPage)
        page.window = FakeWindow()
        page._tasks = [task]
        page._task_rows = {"0": task}
        page.task_tree = FakeTree()
        page._running = True
        page._run_requested = False
        page._paused = False
        page._save_tasks = Mock()
        page._render_tasks = Mock()

        with (
            patch(
                "weimi_manager.ui.upload_page.messagebox.askyesno",
                return_value=True,
            ),
            patch(
                "weimi_manager.ui.upload_page.messagebox.showwarning"
            ) as warning,
        ):
            page._clear_all_tasks()
            page._delete_tasks()

        self.assertEqual(page._tasks, [task])
        page._save_tasks.assert_not_called()
        self.assertGreaterEqual(warning.call_count, 2)
        self.assertFalse(
            any("已清空" in message for message in page.window.messages)
        )

    def test_pause_reports_in_flight_wait_not_stopped(self) -> None:
        page = object.__new__(UploadPage)
        page.window = Mock()
        page._paused = False

        page._pause_tasks()

        self.assertTrue(page._paused)
        page.window.set_status.assert_called_once()
        self.assertIn("当前商品处理结束后停止", page.window.set_status.call_args.args[0])

    def test_interrupted_upload_marks_unknown_results_for_verification(
        self,
    ) -> None:
        tasks = [
            {
                "id": "task-interrupted",
                "status": "running",
                "items": [
                    {
                        "title": "提交中商品",
                        "status": "submitting",
                    },
                    {
                        "title": "排队商品",
                        "status": "queued",
                    },
                    {
                        "item_id": "item-success",
                        "title": "已成功商品",
                        "status": "success",
                        "product_id": "P-SUCCESS",
                    },
                ],
            }
        ]

        changed = _recover_upload_tasks(tasks)

        self.assertTrue(changed)
        items = tasks[0]["items"]
        self.assertTrue(all(item.get("item_id") for item in items))
        self.assertEqual(items[0]["status"], "verification_pending")
        self.assertEqual(items[1]["status"], "paused")
        self.assertEqual(items[2]["status"], "success")
        self.assertEqual(items[2]["product_id"], "P-SUCCESS")
        self.assertEqual(tasks[0]["status"], "needs_verification")
        self.assertIn("待核实", tasks[0]["message"])

    def test_remote_success_with_failed_local_commit_is_verification_pending(
        self,
    ) -> None:
        store = type(
            "StoreValue",
            (),
            {"id": 9, "name": "落盘测试店"},
        )()

        class FakeService:
            calls = 0

            @staticmethod
            def client_for(_store):
                return object()

            @classmethod
            def product_write(cls, *_args, **_kwargs):
                cls.calls += 1
                return {
                    "errcode": 0,
                    "product_id": "P_REMOTE_OK",
                }

        class FakeDb:
            def __init__(self) -> None:
                self.snapshots = []
                self.history_calls = 0

            @staticmethod
            def get_store(_store_id):
                return store

            @staticmethod
            def uploaded_source_ids(_store_id):
                return set()

            def set_setting(self, key, value) -> None:
                if key == "upload_tasks":
                    self.snapshots.append(json.loads(value))

            def commit_upload_task_result(self, *_args, **_kwargs) -> None:
                raise OSError("simulated local disk failure")

            def record_upload_history(self, *_args, **_kwargs) -> None:
                self.history_calls += 1

        class FakeWindow:
            service = FakeService()

            def __init__(self) -> None:
                self.store_page = Mock()

            @staticmethod
            def set_status(*_args, **_kwargs) -> None:
                pass

            @staticmethod
            def store_data_changed() -> None:
                pass

            @staticmethod
            def _post(*_args, **_kwargs) -> None:
                pass

            @staticmethod
            def run_task(work, done, _error, progress=None, **_kwargs) -> bool:
                done(work(progress))
                return True

        class FakeTree:
            @staticmethod
            def selection():
                return ("0",)

        item = {
            "item_id": "item-local-failure",
            "title": "远端成功本地失败",
            "source_product_id": "SOURCE-LOCAL-FAILURE",
            "status": "pending",
            "payload": {"title": "远端成功本地失败"},
        }
        task = {
            "id": "task-local-failure",
            "store_id": 9,
            "store_name": "落盘测试店",
            "template": {"name": "测试模板"},
            "status": "pending",
            "items": [item],
        }
        page = object.__new__(UploadPage)
        page.window = FakeWindow()
        page.db = FakeDb()
        page._tasks = [task]
        page._task_rows = {"0": task}
        page.task_tree = FakeTree()
        page._running = False
        page._run_requested = False
        page._paused = False
        page._task_lock = threading.RLock()
        page._render_tasks = Mock()
        page._progress = Mock()
        page._upload_payload = lambda _item, _template: {"title": "测试"}

        with (
            patch(
                "weimi_manager.ui.upload_page.messagebox.askyesno",
                return_value=True,
            ),
            patch(
                "weimi_manager.ui.upload_page.messagebox.showwarning"
            ),
        ):
            page._start_tasks()

        self.assertEqual(FakeService.calls, 1)
        self.assertEqual(item["product_id"], "P_REMOTE_OK")
        self.assertEqual(item["status"], "verification_pending")
        self.assertEqual(task["status"], "needs_verification")
        self.assertEqual(page.db.history_calls, 0)
        self.assertTrue(
            any(
                snapshot[0]["items"][0]["status"] == "submitting"
                for snapshot in page.db.snapshots
            )
        )
        with (
            patch(
                "weimi_manager.ui.upload_page.messagebox.askyesno",
                return_value=True,
            ),
            patch(
                "weimi_manager.ui.upload_page.messagebox.showwarning"
            ),
        ):
            page._start_tasks()
        self.assertEqual(FakeService.calls, 1)

    def test_upload_snapshot_and_history_commit_in_one_database_transaction(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "upload-atomic.db")
            store_id = db.add_store("原子写入店", "wx-atomic", "secret")
            tasks = [
                {
                    "id": "task-atomic",
                    "status": "completed",
                    "items": [
                        {
                            "item_id": "item-atomic",
                            "status": "success",
                            "product_id": "P-ATOMIC",
                        }
                    ],
                }
            ]

            db.commit_upload_task_result(
                json.dumps(tasks, ensure_ascii=False),
                store_id,
                "SOURCE-ATOMIC",
                "P-ATOMIC",
                "原子写入商品",
            )

            self.assertEqual(
                json.loads(db.get_setting("upload_tasks", "[]")),
                tasks,
            )
            self.assertIn("SOURCE-ATOMIC", db.uploaded_source_ids(store_id))

    def test_prepare_for_close_persists_unknown_inflight_result(self) -> None:
        task = {
            "id": "task-closing",
            "status": "running",
            "items": [
                {
                    "item_id": "item-closing",
                    "status": "submitting",
                }
            ],
        }
        page = object.__new__(UploadPage)
        page._tasks = [task]
        page._task_lock = threading.RLock()
        page._paused = False
        page._save_tasks = Mock()

        page.prepare_for_close()

        self.assertTrue(page._paused)
        self.assertEqual(
            task["items"][0]["status"],
            "verification_pending",
        )
        page._save_tasks.assert_called_once_with()

    def test_network_error_after_submission_is_not_auto_retried(self) -> None:
        store = type(
            "StoreValue",
            (),
            {"id": 11, "name": "网络异常店"},
        )()

        class FakeService:
            calls = 0

            @staticmethod
            def client_for(_store):
                return object()

            @classmethod
            def product_write(cls, *_args, **_kwargs):
                cls.calls += 1
                raise TimeoutError("模拟远端超时")

        class FakeDb:
            snapshots = []

            @staticmethod
            def get_store(_store_id):
                return store

            @staticmethod
            def uploaded_source_ids(_store_id):
                return set()

            def set_setting(self, key, value) -> None:
                if key == "upload_tasks":
                    self.snapshots.append(json.loads(value))

        class FakeWindow:
            service = FakeService()

            def __init__(self) -> None:
                self.store_page = Mock()

            @staticmethod
            def set_status(*_args, **_kwargs) -> None:
                pass

            @staticmethod
            def store_data_changed() -> None:
                pass

            @staticmethod
            def _post(*_args, **_kwargs) -> None:
                pass

            @staticmethod
            def run_task(work, done, _error, progress=None, **_kwargs) -> bool:
                done(work(progress))
                return True

        class FakeTree:
            @staticmethod
            def selection():
                return ("0",)

        item = {
            "item_id": "item-timeout",
            "title": "超时商品",
            "source_product_id": "SOURCE-TIMEOUT",
            "status": "pending",
            "payload": {"title": "超时商品"},
        }
        task = {
            "id": "task-timeout",
            "store_id": 11,
            "store_name": "网络异常店",
            "template": {"name": "测试模板"},
            "status": "pending",
            "items": [item],
        }
        page = object.__new__(UploadPage)
        page.window = FakeWindow()
        page.db = FakeDb()
        page._tasks = [task]
        page._task_rows = {"0": task}
        page.task_tree = FakeTree()
        page._running = False
        page._run_requested = False
        page._paused = False
        page._task_lock = threading.RLock()
        page._render_tasks = Mock()
        page._progress = Mock()
        page._upload_payload = lambda _item, _template: {"title": "测试"}

        with (
            patch(
                "weimi_manager.ui.upload_page.messagebox.askyesno",
                return_value=True,
            ),
            patch(
                "weimi_manager.ui.upload_page.messagebox.showwarning"
            ),
        ):
            page._start_tasks()
            page._start_tasks()

        self.assertEqual(FakeService.calls, 1)
        self.assertEqual(item["status"], "verification_pending")
        self.assertEqual(task["status"], "needs_verification")
        self.assertIn("结果未知", item["message"])

    def test_multi_store_upload_keeps_results_isolated(self) -> None:
        stores = {
            21: type("StoreValue", (), {"id": 21, "name": "店铺甲"})(),
            22: type("StoreValue", (), {"id": 22, "name": "店铺乙"})(),
        }
        state = {
            "calls": [],
            "active": 0,
            "max_active": 0,
            "lock": threading.Lock(),
        }

        class FakeService:
            @staticmethod
            def client_for(_store):
                return object()

            @staticmethod
            def product_write(store, _action, payload, **_kwargs):
                with state["lock"]:
                    state["calls"].append((store.id, payload["title"]))
                    state["active"] += 1
                    state["max_active"] = max(
                        state["max_active"],
                        state["active"],
                    )
                try:
                    time.sleep(0.03)
                finally:
                    with state["lock"]:
                        state["active"] -= 1
                return {
                    "errcode": 0,
                    "product_id": f"P-{store.id}-{payload['title']}",
                }

        class FakeDb:
            @staticmethod
            def get_store(store_id):
                return stores.get(store_id)

            @staticmethod
            def uploaded_source_ids(_store_id):
                return set()

            @staticmethod
            def set_setting(*_args, **_kwargs):
                return

            @staticmethod
            def commit_upload_task_result(*_args, **_kwargs):
                return

            @staticmethod
            def record_upload_history(*_args, **_kwargs):
                return

        class FakeWindow:
            service = FakeService()

            def __init__(self) -> None:
                self.store_page = Mock()

            @staticmethod
            def set_status(*_args, **_kwargs) -> None:
                return

            @staticmethod
            def store_data_changed() -> None:
                return

            @staticmethod
            def _post(*_args, **_kwargs) -> None:
                return

            @staticmethod
            def run_task(work, done, _error, progress=None, **_kwargs) -> bool:
                done(work(progress))
                return True

        class FakeTree:
            @staticmethod
            def selection():
                return ("0", "1")

        tasks = []
        for task_index, store_id in enumerate((21, 22)):
            tasks.append(
                {
                    "id": f"task-multi-{store_id}",
                    "store_id": store_id,
                    "store_name": stores[store_id].name,
                    "template": {"name": "多店测试"},
                    "status": "pending",
                    "items": [
                        {
                            "item_id": f"item-{store_id}-{item_index}",
                            "title": f"{stores[store_id].name}商品{item_index}",
                            "source_product_id": (
                                f"SOURCE-{store_id}-{item_index}"
                            ),
                            "status": "pending",
                            "payload": {
                                "title": (
                                    f"{stores[store_id].name}商品{item_index}"
                                )
                            },
                        }
                        for item_index in range(2)
                    ],
                }
            )

        page = object.__new__(UploadPage)
        page.window = FakeWindow()
        page.db = FakeDb()
        page._tasks = tasks
        page._task_rows = {"0": tasks[0], "1": tasks[1]}
        page.task_tree = FakeTree()
        page._running = False
        page._run_requested = False
        page._paused = False
        page._task_lock = threading.RLock()
        page._render_tasks = Mock()
        page._progress = Mock()
        page._upload_payload = lambda item, _template: dict(item["payload"])

        with (
            patch.dict(os.environ, {"WEIMI_UPLOAD_WORKERS": "2"}),
            patch(
                "weimi_manager.ui.upload_page.messagebox.askyesno",
                return_value=True,
            ),
            patch(
                "weimi_manager.ui.upload_page.messagebox.showwarning"
            ),
        ):
            page._start_tasks()

        self.assertGreaterEqual(state["max_active"], 2)
        self.assertEqual({store_id for store_id, _ in state["calls"]}, {21, 22})
        self.assertTrue(
            all(
                item["status"] == "success"
                for task in tasks
                for item in task["items"]
            )
        )
        self.assertEqual(
            {
                int(item["product_id"].split("-")[1])
                for task in tasks
                for item in task["items"]
            },
            {21, 22},
        )

    def test_taobao_missing_head_images_uses_sku_and_detail_images(
        self,
    ) -> None:
        result = _normalize_source_payload(
            {
                "productId": "TB-IMAGE-FALLBACK",
                "productName": "主图不足但详情图完整",
                "categoryId": "162201",
                "itemImgs": ["//img.alicdn.com/main-1.jpg"],
                "desc": (
                    '<p><img src="//img.alicdn.com/detail-1.jpg"></p>'
                    '<p><img src="//img.alicdn.com/detail-2.jpg"></p>'
                ),
                "specs": {
                    "颜色分类": {
                        "1": ["黑色", ""],
                    },
                },
                "skus": [
                    {
                        "price": "2000",
                        "quantity": "12",
                        "id": "1",
                        "skuImageUrl": "//img.alicdn.com/sku-black.jpg",
                    }
                ],
            }
        )
        self.assertEqual(
            result["head_imgs"],
            [
                "https://img.alicdn.com/main-1.jpg",
                "https://img.alicdn.com/sku-black.jpg",
                "https://img.alicdn.com/detail-1.jpg",
            ],
        )
        self.assertEqual(
            result["desc_info"]["imgs"][0],
            "https://img.alicdn.com/detail-1.jpg",
        )

    def test_taobao_detail_images_fill_missing_heads_without_sku_images(
        self,
    ) -> None:
        result = _normalize_source_payload(
            {
                "productId": "TB-DETAIL-FALLBACK",
                "productName": "详情图补主图",
                "categoryId": "162201",
                "itemImgs": ["//img.alicdn.com/main-1.jpg"],
                "desc": (
                    '<img src="//img.alicdn.com/detail-1.jpg">'
                    '<img src="//img.alicdn.com/detail-2.jpg">'
                ),
                "skus": [
                    {"price": "2000", "quantity": "12", "id": ""}
                ],
            }
        )
        self.assertEqual(len(result["head_imgs"]), 3)
        self.assertEqual(
            result["head_imgs"][1:],
            [
                "https://img.alicdn.com/detail-1.jpg",
                "https://img.alicdn.com/detail-2.jpg",
            ],
        )

    def test_raw_wechat_package_builds_wechat_upload_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "wechat.txt"
            path.write_text(
                json.dumps(
                    {
                        "title": "测试防晒帽",
                        "headImg": [
                            "https://wst.example.test/hat-1.jpg",
                            "https://wst.example.test/hat-2.jpg",
                        ],
                        "category": [
                            {"catId": "6033"},
                            {"catId": "6093"},
                            {"catId": "549163"},
                        ],
                        "detail": {
                            "detailImg": [
                                "https://wst.example.test/detail-1.jpg"
                            ],
                            "desc": "详情",
                        },
                        "productSaleAttrs": [
                            {
                                "key": "颜色",
                                "items": [
                                    {
                                        "value": "米色",
                                        "skuIds": ["WX-SKU-1"],
                                    }
                                ],
                            }
                        ],
                        "skuList": {
                            "skuInfo": [
                                {
                                    "skuId": "WX-SKU-1",
                                    "price": 2000,
                                }
                            ]
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            rows = UploadPage._parse_package(None, path)
        result = object.__new__(UploadPage)._upload_payload(
            rows[0],
            {"price_multiplier": 1.5, "price_offset": 0, "stock": 1000},
        )
        self.assertEqual(result["title"], "测试防晒帽")
        self.assertEqual(
            [item["cat_id"] for item in result["cats"]],
            ["6033", "6093", "549163"],
        )
        self.assertEqual(result["head_imgs"][0], "https://wst.example.test/hat-1.jpg")
        self.assertEqual(
            result["desc_info"]["imgs"],
            ["https://wst.example.test/detail-1.jpg"],
        )
        self.assertEqual(result["skus"][0]["sale_price"], 3000)
        self.assertEqual(result["skus"][0]["stock_num"], 1000)
        self.assertEqual(
            result["skus"][0]["sku_attrs"][0]["attr_value"],
            "米色",
        )

    def test_package_files_recursively_finds_supported_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "淘宝数据").mkdir()
            (root / "微信数据").mkdir()
            taobao = root / "淘宝数据" / "a.txt"
            wechat = root / "微信数据" / "b.json"
            ignored = root / "微信数据" / "image.png"
            taobao.write_text("{}", encoding="utf-8")
            wechat.write_text("{}", encoding="utf-8")
            ignored.write_bytes(b"not-an-image")
            files = UploadPage._package_files(root)
        self.assertCountEqual(files, [taobao, wechat])

    def test_template_price_rules_title_and_attributes(self) -> None:
        page = object.__new__(UploadPage)
        item = {
            "title": "【促销】旧款棉柔巾",
            "price": 1000,
            "stock": 12,
            "payload": {
                "title": "【促销】旧款棉柔巾",
                "cats": [{"cat_id": "10000111"}],
                "head_imgs": ["https://example.test/a.jpg"],
                "skus": [{"sale_price": 1000, "stock_num": 12}],
            },
            "source_product_id": "source-1",
        }
        result = page._upload_payload(
            item,
            {
                "strip_title_brackets": True,
                "title_replace_rules": "旧款 = 新款",
                "title_prefix": "新品·",
                "title_suffix": " 热卖",
                "fixed_price_enabled": True,
                "fixed_price": 1999,
                "final_price_decimals": 2,
                "stock": 30,
                "enable_custom_attributes": True,
                "attribute_rules": "保质期=1年\n适用人群=家庭",
                "delivery_type": "现货",
                "delivery_time": "48小时内发货",
            },
        )
        self.assertEqual(result["title"], "新品·新款棉柔巾 热卖")
        self.assertEqual(result["skus"][0]["sale_price"], 1999)
        self.assertEqual(
            result["attrs"],
            [
                {"attr_key": "保质期", "attr_value": "1年"},
                {"attr_key": "适用人群", "attr_value": "家庭"},
            ],
        )

    def test_template_filter_skips_product(self) -> None:
        page = object.__new__(UploadPage)
        item = {
            "title": "买一送一测试商品",
            "price": 1000,
            "payload": {
                "title": "买一送一测试商品",
                "cats": [{"cat_id": "10000111"}],
                "head_imgs": ["https://example.test/a.jpg"],
                "skus": [{"sale_price": 1000, "stock_num": 12}],
            },
        }
        with self.assertRaises(TemplateRuleSkip):
            page._upload_payload(
                item,
                {"title_rules": "买一送一"},
            )

    def test_multi_store_category_scan_uses_one_reference_store(self) -> None:
        class FakeWindow:
            def __init__(self, db) -> None:
                self.db = db
                self.log_titles = []

            def set_status(self, *_args, **_kwargs) -> None:
                pass

            def run_task(
                self,
                work,
                done,
                _error,
                progress=None,
                log_title="",
            ) -> None:
                self.log_titles.append(log_title)
                done(work(progress))

        class FakeCategoryClient:
            scan_count = 0
            batch_calls = []

            def __init__(self, *_args, **_kwargs) -> None:
                self.store_id = _args[1].id

            def category_candidates(self, _budget):
                type(self).scan_count += 1
                return [
                    {
                        "category_id": "101",
                        "name": "测试类目",
                        "path": "一级 / 测试类目",
                        "required_deposit": 100000,
                        "amount_to_pay": 0,
                        "deposit_balance": 200000,
                        "status": "可申请",
                    }
                ]

            def batch_add_categories(self, category_ids):
                type(self).batch_calls.append(
                    (self.store_id, tuple(category_ids))
                )
                return {"success": True}

            def category_relation_statuses(self, category_ids):
                return {
                    str(category_id): "审核中"
                    for category_id in category_ids
                }

        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "ui.db")
            first = db.add_store("店铺一", "wx-first", "secret")
            second = db.add_store("店铺二", "wx-second", "secret")
            db.update_web_status(first, "已登录", "ok")
            db.update_web_status(second, "已登录", "ok")
            try:
                root = tk.Tk()
            except tk.TclError as exc:
                self.skipTest(str(exc))
            root.withdraw()
            apply_theme(root)
            page = UploadPage(root, FakeWindow(db))
            page.category_mode_var.set("多店申请")
            page._render_category_stores()
            page.category_store_tree.selection_set(
                (str(first), str(second))
            )
            page.category_budget_var.set("1000")
            with patch(
                "weimi_manager.ui.upload_page.ShopPrivateClient",
                FakeCategoryClient,
            ):
                page._scan_category_candidates()
                root.update()
            self.assertEqual(FakeCategoryClient.scan_count, 1)
            self.assertEqual(len(page._category_candidates), 1)
            row = next(iter(page._category_candidates.values()))
            self.assertEqual(
                set(row["target_store_ids"]),
                {first, second},
            )
            with (
                patch(
                    "weimi_manager.ui.upload_page.ShopPrivateClient",
                    FakeCategoryClient,
                ),
                patch(
                    "weimi_manager.ui.upload_page.messagebox.askyesno",
                    return_value=True,
                ),
            ):
                page._submit_category_candidates()
                root.update()
            self.assertEqual(
                sorted(
                    (store_id, categories)
                    for store_id, categories in FakeCategoryClient.batch_calls
                ),
                sorted(
                    [
                        (first, ("101",)),
                        (second, ("101",)),
                    ]
                ),
            )
            self.assertEqual(
                page.category_result_tree.set("0", "status"),
                "审核中",
            )
            self.assertEqual(
                page.window.log_titles,
                ["扫描可申请类目", "提交类目审核"],
            )
            root.destroy()


if __name__ == "__main__":
    unittest.main()
