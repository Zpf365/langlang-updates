from __future__ import annotations

import json
import re
import time
from pathlib import Path

import pytest

from weimi_manager.db import Database
from weimi_manager.models import Store
from weimi_manager.service import SyncService
from weimi_manager.ui.upload_page import (
    UploadPage,
    _apply_template_image_rules,
)
from weimi_manager.webui.controller import (
    UPLOAD_DEFAULT_TEMPLATE,
    NextUiController,
)
from weimi_manager.wechat_api import WechatShopClient


class _CategoryBrandTransport:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.brand_page = 0

    def post_json(
        self,
        url: str,
        payload: dict,
        timeout: int = 25,
    ) -> dict:
        self.calls.append((url, dict(payload)))
        if url.endswith("/cgi-bin/stable_token"):
            return {"access_token": "TOKEN", "expires_in": 7200}
        if "/shop/ec/category/all" in url:
            return {
                "errcode": 0,
                "errmsg": "ok",
                "cats_v2": [
                    {
                        "cat_and_qua": [
                            {
                                "cat": {
                                    "cat_id": "3",
                                    "name": "连衣裙",
                                    "f_cat_id": "2",
                                    "level": 3,
                                    "leaf": True,
                                }
                            },
                            {
                                "cat": {
                                    "cat_id": "2",
                                    "name": "女装",
                                    "f_cat_id": "1",
                                    "level": 2,
                                    "leaf": False,
                                }
                            },
                            {
                                "cat": {
                                    "cat_id": "1",
                                    "name": "服饰",
                                    "f_cat_id": "0",
                                    "level": 1,
                                    "leaf": False,
                                }
                            },
                        ]
                    },
                    {
                        "cat_and_qua": [
                            {
                                "cat": {
                                    "cat_id": "6",
                                    "name": "运动鞋",
                                    "f_cat_id": "5",
                                    "level": 3,
                                    "leaf": True,
                                }
                            },
                            {
                                "cat": {
                                    "cat_id": "5",
                                    "name": "运动户外",
                                    "f_cat_id": "4",
                                    "level": 2,
                                    "leaf": False,
                                }
                            },
                            {
                                "cat": {
                                    "cat_id": "4",
                                    "name": "鞋包",
                                    "f_cat_id": "0",
                                    "level": 1,
                                    "leaf": False,
                                }
                            },
                        ]
                    },
                ],
            }
        if "/shop/ec/category/get_category_relation_list" in url:
            assert payload["is_filter_status"] is True
            assert payload["status"] == 1
            return {
                "errcode": 0,
                "errmsg": "ok",
                "list": [
                    {"id": 3, "status": 1},
                    {"id": 6, "status": 1},
                ],
            }
        if "/channels/ec/brand/valid/list/get" in url:
            self.brand_page += 1
            if self.brand_page == 1:
                return {
                    "errcode": 0,
                    "errmsg": "ok",
                    "brands": [
                        {
                            "brand_id": 1001,
                            "ch_name": "店铺品牌甲",
                            "status": 3,
                        }
                    ],
                    "next_key": "NEXT",
                }
            return {
                "errcode": 0,
                "errmsg": "ok",
                "brands": [
                    {
                        "brand_id": 1002,
                        "ch_name": "即将过期品牌乙",
                        "status": 5,
                    }
                ],
                "next_key": "",
            }
        if "/shop/ec/category/getcategoryrule" in url:
            assert "rule_id=1" in url
            return {
                "errcode": 0,
                "errmsg": "ok",
                "deposit": 100000,
            }
        raise AssertionError(f"Unexpected URL: {url}")


def _official_client(transport: _CategoryBrandTransport) -> WechatShopClient:
    return WechatShopClient(
        "wx-category",
        "secret",
        transport=transport,
        sleeper=lambda _seconds: None,
        min_request_interval=0,
    )


def test_official_category_and_brand_read_contracts() -> None:
    transport = _CategoryBrandTransport()
    client = _official_client(transport)

    categories = client.fetch_all_categories()
    relations = client.fetch_effective_category_relations()
    brands = client.fetch_valid_brands()
    deposit = client.fetch_category_rule("3", rule_id=1)

    assert len(categories["cats_v2"]) == 2
    assert relations == ["3", "6"]
    assert brands == [
        {
            "brand_id": "1001",
            "ch_name": "店铺品牌甲",
            "status": 3,
            "expiring": False,
        },
        {
            "brand_id": "1002",
            "ch_name": "即将过期品牌乙",
            "status": 5,
            "expiring": True,
        },
    ]
    assert deposit["deposit"] == 100000
    category_calls = [
        payload
        for url, payload in transport.calls
        if "/shop/ec/category/get_category_relation_list" in url
    ]
    assert category_calls == [
        {"is_filter_status": True, "status": 1},
    ]


class _CategoryService:
    def __init__(self) -> None:
        self.calls = 0
        self.unavailable_stores: set[int] = set()

    def client_for(self, _store: Store):
        return _FakeCategoryClient()

    def upload_category_options(
        self,
        store: Store,
        *,
        refresh: bool = False,
    ) -> dict:
        self.calls += 1
        root_id, parent_id, leaf_id = "1", "2", "3"
        root_name, parent_name, leaf_name = "服饰", "女装", "连衣裙"
        return {
            "version": "tree-v1",
            "cache_key": f"{store.id}:tree-v1",
            "roots": [
                {
                    "category_id": root_id,
                    "name": root_name,
                    "path": root_name,
                    "id_path": [root_id],
                    "leaf": False,
                    "children": [
                        {
                            "category_id": parent_id,
                            "name": parent_name,
                            "path": f"{root_name} / {parent_name}",
                            "id_path": [root_id, parent_id],
                            "leaf": False,
                            "children": [
                                {
                                    "category_id": leaf_id,
                                    "name": leaf_name,
                                    "path": (
                                        f"{root_name} / {parent_name} / "
                                        f"{leaf_name}"
                                    ),
                                    "id_path": [root_id, parent_id, leaf_id],
                                    "leaf": True,
                                    "available": store.id not in self.unavailable_stores,
                                    "children": [],
                                }
                            ],
                        }
                    ],
                }
            ],
            "leaf_count": 1,
            "source": "官方类目树 + 本店生效权限",
            "refreshed_at": "2026-11-26T00:00:00+00:00",
        }

    def validate_category_selection(
        self,
        store: Store,
        category_ids: list[str],
        category_id: str = "",
        client=None,
    ) -> dict:
        options = self.upload_category_options(store, refresh=False)
        if (
            store.id in self.unavailable_stores
            or category_id != "3"
            or [str(value) for value in category_ids] != ["1", "2", "3"]
        ):
            raise ValueError(
                f"{store.name} 指定类目已失效、被冻结或不存在，请修改模板"
            )
        return {
            "category_id": "3",
            "category_ids": ["1", "2", "3"],
            "path": "服饰 / 女装 / 连衣裙",
            "version": options["version"],
            "cache_key": options["cache_key"],
        }


class _FakeCategoryClient:
    def fetch_valid_brands(self):
        return [
            {
                "brand_id": "1001",
                "ch_name": "店铺品牌甲",
                "status": 3,
                "expiring": False,
            }
        ]


class _ServiceCategoryClient:
    def __init__(self) -> None:
        self.category_calls = 0
        self.relation_calls = 0

    def fetch_all_categories(self):
        self.category_calls += 1
        return {
            "errcode": 0,
            "cats_v2": [
                {
                    "cat_and_qua": [
                        {
                            "cat": {
                                "cat_id": "3",
                                "name": "连衣裙",
                                "f_cat_id": "2",
                                "level": 3,
                                "leaf": True,
                            }
                        },
                        {
                            "cat": {
                                "cat_id": "2",
                                "name": "女装",
                                "f_cat_id": "1",
                                "level": 2,
                                "leaf": False,
                            }
                        },
                        {
                            "cat": {
                                "cat_id": "1",
                                "name": "服饰",
                                "f_cat_id": "0",
                                "level": 1,
                                "leaf": False,
                            }
                        },
                    ]
                },
                {
                    "cat_and_qua": [
                        {
                            "cat": {
                                "cat_id": "9",
                                "name": "未开通叶子",
                                "f_cat_id": "8",
                                "level": 3,
                                "leaf": True,
                            }
                        },
                        {
                            "cat": {
                                "cat_id": "8",
                                "name": "未开通二级",
                                "f_cat_id": "7",
                                "level": 2,
                                "leaf": False,
                            }
                        },
                        {
                            "cat": {
                                "cat_id": "7",
                                "name": "未开通一级",
                                "f_cat_id": "0",
                                "level": 1,
                                "leaf": False,
                            }
                        },
                    ]
                }
            ],
        }

    def fetch_effective_category_relations(self):
        self.relation_calls += 1
        return ["3"]

    def fetch_valid_brands(self):
        return _FakeCategoryClient().fetch_valid_brands()

    def fetch_category_product_rule(
        self,
        category_id: str,
        brand_id: str | int = 2100000000,
        release_mode: int = 0,
    ):
        return {
            "errcode": 0,
            "info": {"cat_id": str(category_id), "name": "连衣裙"},
            "brand_info": {
                "brand_id": str(brand_id),
                "is_brand_authorized": str(brand_id) == "1001",
            },
            "is_directed_category": False,
            "restricted_brand_list": [],
            "product_qua_list": [],
        }

    def fetch_category_rule(self, category_id: str, *, rule_id: int):
        assert rule_id == 1
        return {"errcode": 0, "deposit": 100000}


class _FailingCategoryClient(_ServiceCategoryClient):
    def fetch_all_categories(self):
        self.category_calls += 1
        raise RuntimeError("network unavailable")


def test_service_category_cache_is_store_and_version_scoped(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "webui.db")
    store_id = database.add_store("缓存店铺", "wx-cache", "secret")
    store = database.get_store(store_id)
    assert store
    client = _ServiceCategoryClient()
    service = SyncService(database)

    first = service.upload_category_options(store, client)
    second = service.upload_category_options(store, client)

    assert first["roots"][0]["path"] == "服饰"
    assert first["roots"][0]["children"][0]["path"] == "服饰 / 女装"
    leaf = first["roots"][0]["children"][0]["children"][0]
    assert leaf["id_path"] == ["1", "2", "3"]
    assert leaf["available"] is True
    assert leaf["path"] == "服饰 / 女装 / 连衣裙"
    inactive = first["roots"][1]["children"][0]["children"][0]
    assert inactive["category_id"] == "9"
    assert inactive["available"] is False
    assert first["leaf_count"] == 2
    assert first["available_leaf_count"] == 1
    assert first["cache_key"] == f"{store_id}:{first['version']}"
    assert second["cached"] is True
    assert client.category_calls == 1
    assert client.relation_calls == 1
    assert (store_id, first["version"]) in service._upload_category_tree_cache

    persisted = json.loads(
        database.get_setting(f"upload_category_cache_{store_id}")
    )
    assert persisted["version"] == first["version"]
    assert persisted["roots"][0]["path"] == "服饰"

    new_service = SyncService(database)
    reused = new_service.upload_category_options(
        store,
        _FailingCategoryClient(),
    )
    assert reused["version"] == first["version"]
    assert reused["cached"] is True
    assert reused["stale"] is False


def test_stale_category_cache_returns_immediately_and_refreshes_in_background(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "webui.db")
    store_id = database.add_store("后台刷新店", "wx-bg", "secret")
    store = database.get_store(store_id)
    assert store
    client = _ServiceCategoryClient()
    SyncService(database).upload_category_options(store, client)
    key = f"upload_category_cache_{store_id}"
    cached = json.loads(database.get_setting(key))
    cached["saved_at"] = time.time() - 86401
    cached["last_refresh_attempt_at"] = cached["saved_at"]
    database.set_setting(key, json.dumps(cached, ensure_ascii=False))
    before_calls = client.category_calls

    fresh_service = SyncService(database)
    returned = fresh_service.upload_category_options(store, client)
    assert returned["stale"] is True
    assert returned["refresh_pending"] is True

    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        refreshed = json.loads(database.get_setting(key))
        if refreshed.get("saved_at") > cached["saved_at"]:
            break
        time.sleep(0.02)
    else:
        raise AssertionError("background category refresh did not finish")
    assert client.category_calls > before_calls


def test_manual_category_refresh_failure_returns_old_cache(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "webui.db")
    store_id = database.add_store("失败回退店", "wx-fail", "secret")
    store = database.get_store(store_id)
    assert store
    SyncService(database).upload_category_options(
        store,
        _ServiceCategoryClient(),
    )
    key = f"upload_category_cache_{store_id}"
    cached = json.loads(database.get_setting(key))
    cached["saved_at"] = time.time() - 86401
    cached["last_refresh_attempt_at"] = cached["saved_at"]
    database.set_setting(key, json.dumps(cached, ensure_ascii=False))

    returned = SyncService(database).upload_category_options(
        store,
        _FailingCategoryClient(),
        refresh=True,
    )
    assert returned["roots"] == cached["roots"]
    assert returned["stale"] is True
    assert returned["manual_refresh_failed"] is True
    assert "network unavailable" in returned["refresh_error"]

    database.delete_store(store_id)
    assert database.get_setting(key, "") == ""


def test_controller_exposes_per_store_category_tree_and_only_valid_brands(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "webui.db")
    store_id = database.add_store("类目店铺", "wx-category-rpc", "secret")
    service = _CategoryService()
    controller = NextUiController(database, product_service=service)

    result = controller.dispatch(
        "upload.categories",
        {"store_ids": [store_id]},
    )

    store_result = result["stores"][str(store_id)]
    assert store_result["version"] == "tree-v1"
    assert store_result["cache_key"] == f"{store_id}:tree-v1"
    assert store_result["roots"][0]["children"][0]["children"][0]["category_id"] == "3"
    assert store_result["leaf_count"] == 1

    options = controller.dispatch(
        "upload.store-options",
        {"store_ids": [store_id]},
    )["stores"][str(store_id)]
    assert options["brand_mode"] == "none"
    assert options["has_store_brands"] is True
    assert {"id": "1001", "label": "店铺品牌甲"} in options["brands"]


def test_template_category_snapshot_round_trip(tmp_path: Path) -> None:
    database = Database(tmp_path / "webui.db")
    store_id = database.add_store("模板类目店", "wx-template-cat", "secret")
    controller = NextUiController(
        database,
        product_service=_CategoryService(),
    )
    saved = controller.dispatch(
        "upload.templates.save",
        {
            "template": {
                "name": "指定类目模板",
                "price_multiplier": 1,
                "stock": 0,
                "listing_mode": "immediate",
                "duplicate_filter_mode": "off",
                "delivery_type": "现货",
                "delivery_time": "48小时内发货",
                "image_mode": "默认方式",
                "head_action": "删除违规图片",
                "detail_action": "删除违规图片",
                "category_mode": "specified",
                "specified_category_id": "3",
                "specified_category_ids": ["1", "2", "3"],
                "specified_category_path": "服饰 / 女装 / 连衣裙",
                "specified_category_version": "tree-v1",
                "specified_category_store_id": str(store_id),
            }
        },
    )["template"]
    copied = controller.dispatch(
        "upload.templates.copy",
        {"template_id": saved["id"]},
    )["template"]
    assert copied["category_mode"] == "specified"
    assert copied["specified_category_id"] == "3"
    assert copied["specified_category_ids"] == ["1", "2", "3"]
    assert copied["specified_category_path"] == "服饰 / 女装 / 连衣裙"
    assert copied["specified_category_version"] == "tree-v1"
    assert copied["specified_category_store_id"] == str(store_id)
    reopened = NextUiController(database).dispatch(
        "upload.templates.defaults",
        {},
    )
    template = next(
        row
        for row in NextUiController(database).dispatch(
            "upload.list",
            {},
        )["templates"]
        if row["id"] == saved["id"]
    )
    assert template["category_mode"] == "specified"
    assert template["specified_category_id"] == "3"
    assert template["specified_category_ids"] == ["1", "2", "3"]
    assert template["specified_category_path"] == "服饰 / 女装 / 连衣裙"
    assert template["specified_category_version"] == "tree-v1"
    assert template["specified_category_store_id"] == str(store_id)
    assert reopened["default_template"] is None


def test_unspecified_template_preserves_source_or_auto_category() -> None:
    page = object.__new__(UploadPage)
    page.db = None
    source_payload = _image_payload()
    template = {
        "category_mode": "auto",
        "price_multiplier": 1,
        "price_offset": 0,
        "stock": 0,
        "title_rules": "",
        "sku_rules": "",
        "image_rules": "",
    }
    preserved = page._upload_payload(
        {
            "title": "来源类目商品",
            "price": 1000,
            "payload": source_payload,
        },
        template,
    )
    assert preserved["cats_v2"] == [{"cat_id": "9"}]
    assert preserved["category_path"] == "来源类目"

    source_payload.pop("cats", None)
    source_payload.pop("cats_v2", None)
    source_payload.pop("category_path", None)
    automatic = page._upload_payload(
        {
            "title": "来源缺类目商品",
            "price": 1000,
            "payload": source_payload,
        },
        template,
    )
    assert automatic["_auto_category_required"] is True


def test_upload_task_snapshot_keeps_store_specific_category_and_brand(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "webui.db")
    first_store = database.add_store("甲店", "wx-cat-a", "secret")
    second_store = database.add_store("乙店", "wx-cat-b", "secret")
    service = _CategoryService()
    controller = NextUiController(database, product_service=service)
    database.set_setting(
        "upload_templates",
        json.dumps(
            [
                {
                    **UPLOAD_DEFAULT_TEMPLATE,
                    "id": "tpl-category",
                    "name": "类目快照模板",
                    "category_mode": "specified",
                    "specified_category_id": "3",
                    "specified_category_ids": ["1", "2", "3"],
                    "specified_category_path": "服饰 / 女装 / 连衣裙",
                    "specified_category_version": "tree-v1",
                    "specified_category_store_id": str(first_store),
                }
            ],
            ensure_ascii=False,
        ),
    )
    database.set_setting(
        "upload_batch",
        json.dumps(
            [
                {
                    "batch_id": "batch-category",
                    "source_product_id": "SOURCE-CATEGORY",
                    "title": "类目快照商品",
                    "price": 1000,
                    "payload": {"title": "类目快照商品"},
                },
                {
                    "batch_id": "batch-category-b",
                    "source_product_id": "SOURCE-CATEGORY-B",
                    "title": "类目快照商品乙",
                    "price": 1000,
                    "payload": {"title": "类目快照商品乙"},
                }
            ],
            ensure_ascii=False,
        ),
    )

    created = controller.dispatch(
        "upload.tasks.create",
        {
            "batch_ids": ["batch-category", "batch-category-b"],
            "store_ids": [first_store, second_store],
            "template_id": "tpl-category",
            "assignments": {
                "batch-category": first_store,
                "batch-category-b": second_store,
            },
            "store_options": {
                str(first_store): {
                    "brand_mode": "store",
                    "brand_id": "1001",
                },
                str(second_store): {
                    "brand_mode": "none",
                    "brand_id": "2100000000",
                },
            },
        },
    )
    task = next(row for row in created["tasks"] if row["store_id"] == first_store)
    snapshot = task["template"]
    assert snapshot["specified_category_id"] == "3"
    assert snapshot["specified_category_ids"] == ["1", "2", "3"]
    assert snapshot["specified_category_path"] == "服饰 / 女装 / 连衣裙"
    assert snapshot["specified_category_version"] == "tree-v1"
    assert snapshot["brand_mode"] == "store"
    assert snapshot["brand_id"] == "1001"
    second_task = next(
        row for row in created["tasks"] if row["store_id"] == second_store
    )
    assert second_task["template"]["specified_category_ids"] == ["1", "2", "3"]
    assert second_task["template"]["brand_mode"] == "none"
    assert second_task["template"]["brand_id"] == "2100000000"

    reopened = NextUiController(database, product_service=service).dispatch(
        "upload.list",
        {},
    )
    reloaded = next(
        row for row in reopened["tasks"] if row["store_id"] == first_store
    )
    assert reloaded["template"]["specified_category_ids"] == ["1", "2", "3"]
    assert reloaded["template"]["specified_category_path"] == "服饰 / 女装 / 连衣裙"


def test_task_creation_blocks_only_store_with_unavailable_template_category(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "webui.db")
    first_store = database.add_store("可用店", "wx-cat-ok", "secret")
    second_store = database.add_store("不可用店", "wx-cat-no", "secret")
    service = _CategoryService()
    service.unavailable_stores.add(second_store)
    controller = NextUiController(database, product_service=service)
    database.set_setting(
        "upload_templates",
        json.dumps(
            [
                {
                    **UPLOAD_DEFAULT_TEMPLATE,
                    "id": "tpl-category-check",
                    "name": "逐店类目校验",
                    "category_mode": "specified",
                    "specified_category_id": "3",
                    "specified_category_ids": ["1", "2", "3"],
                    "specified_category_path": "服饰 / 女装 / 连衣裙",
                    "specified_category_version": "tree-v1",
                    "specified_category_store_id": str(first_store),
                }
            ],
            ensure_ascii=False,
        ),
    )
    database.set_setting(
        "upload_batch",
        json.dumps(
            [
                {
                    "batch_id": "batch-ok",
                    "source_product_id": "SOURCE-OK",
                    "title": "可用店商品",
                    "price": 1000,
                    "payload": {"title": "可用店商品"},
                },
                {
                    "batch_id": "batch-no",
                    "source_product_id": "SOURCE-NO",
                    "title": "不可用店商品",
                    "price": 1000,
                    "payload": {"title": "不可用店商品"},
                },
            ],
            ensure_ascii=False,
        ),
    )
    result = controller.dispatch(
        "upload.tasks.create",
        {
            "batch_ids": ["batch-ok", "batch-no"],
            "store_ids": [first_store, second_store],
            "template_id": "tpl-category-check",
            "assignments": {
                "batch-ok": first_store,
                "batch-no": second_store,
            },
        },
    )
    assert result["created"] == 1
    assert [task["store_id"] for task in result["tasks"]] == [first_store]
    assert result["blocked_stores"][0]["store_id"] == second_store
    assert "指定类目" in result["blocked_stores"][0]["error"]


def _image_payload() -> dict:
    return {
        "title": "图片处理商品",
        "head_imgs": [f"h{index}" for index in range(1, 6)],
        "desc_info": {
            "imgs": [f"d{index}" for index in range(1, 9)],
            "desc": "详情",
        },
        "skus": [{"sale_price": 1000, "stock_num": 1}],
        "cats": [{"cat_id": "9"}],
        "cats_v2": [{"cat_id": "9"}],
        "category_path": "来源类目",
    }


def test_template_image_processing_order_idempotency_and_empty_fallback() -> None:
    payload = _image_payload()
    template = {
        "remove_head_index": 2,
        "trim_head": 1,
        "remove_detail_index": 2,
        "trim_detail": 2,
        "trim_detail_tail": 2,
        "detail_keep_first": 2,
    }
    processed = _apply_template_image_rules(payload, template)
    assert processed["head_imgs"] == ["h3", "h4", "h5"]
    assert processed["desc_info"]["imgs"] == ["d4", "d5"]

    repeated = _apply_template_image_rules(processed, template)
    assert repeated["head_imgs"] == processed["head_imgs"]
    assert repeated["desc_info"]["imgs"] == processed["desc_info"]["imgs"]

    emptied_detail = _apply_template_image_rules(
        {
            **_image_payload(),
            "desc_info": {"imgs": ["only"], "desc": ""},
        },
        {"trim_detail": 1},
    )
    assert emptied_detail["desc_info"]["imgs"] == []

    emptied_head = _apply_template_image_rules(
        {
            **_image_payload(),
            "head_imgs": ["only"],
        },
        {"remove_head_index": 1},
    )
    assert emptied_head["head_imgs"] == []

    boundary = _apply_template_image_rules(
        _image_payload(),
        {
            "remove_head_index": 99,
            "remove_detail_index": 99,
            "trim_detail_tail": 3,
            "detail_keep_first": 2,
        },
    )
    assert boundary["head_imgs"] == [f"h{index}" for index in range(1, 6)]
    assert boundary["desc_info"]["imgs"] == ["d1", "d2"]


def test_specified_category_validation_rejects_non_leaf_and_missing_brand(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "webui.db")
    store_id = database.add_store("校验店铺", "wx-validation", "secret")
    store = database.get_store(store_id)
    assert store
    client = _ServiceCategoryClient()
    service = SyncService(database)

    with pytest.raises(ValueError, match="叶子 ID"):
        service._validate_specified_product_category(
            store,
            client,
            {
                "_specified_category_id": "2",
                "cats_v2": [
                    {"cat_id": "1"},
                    {"cat_id": "2"},
                    {"cat_id": "3"},
                ],
                "brand_id": "2100000000",
                "_brand_mode": "none",
            },
        )

    directed = _ServiceCategoryClient()
    directed.fetch_category_product_rule = lambda category_id, brand_id=2100000000, release_mode=0: {
        "is_directed_category": True,
        "restricted_brand_list": [{"brand_id": "1001"}],
        "brand_info": {},
        "product_qua_list": [],
    }
    with pytest.raises(ValueError, match="品牌定向类目"):
        service._validate_specified_product_category(
            store,
            directed,
            {
                "_specified_category_id": "3",
                "cats_v2": [
                    {"cat_id": "1"},
                    {"cat_id": "2"},
                    {"cat_id": "3"},
                ],
                "brand_id": "2100000000",
                "_brand_mode": "none",
            },
        )

    options = service.upload_category_options(store, client)
    validated = service.validate_category_selection(
        store,
        ["1", "2", "3"],
        "3",
        client,
    )
    assert validated["path"] == "服饰 / 女装 / 连衣裙"

    attribute_client = _ServiceCategoryClient()
    attribute_client.fetch_category_product_rule = lambda category_id, brand_id=2100000000, release_mode=0: {
        "is_directed_category": False,
        "restricted_brand_list": [],
        "brand_info": {},
        "product_qua_list": [],
        "product_attr_list": [
            {
                "name": "材质",
                "required_rule": {"rule_type": 1},
            }
        ],
    }
    with pytest.raises(ValueError, match="缺少必填商品属性：材质"):
        SyncService(database)._validate_specified_product_category(
            store,
            attribute_client,
            {
                "_specified_category_id": "3",
                "cats_v2": [
                    {"cat_id": "1"},
                    {"cat_id": "2"},
                    {"cat_id": "3"},
                ],
                "brand_id": "2100000000",
                "_brand_mode": "none",
                "attrs": [],
            },
        )

    with pytest.raises(ValueError, match="不存在、未生效或已过期"):
        SyncService(database)._validate_specified_product_category(
            store,
            client,
            {
                "_specified_category_id": "3",
                "cats_v2": [
                    {"cat_id": "1"},
                    {"cat_id": "2"},
                    {"cat_id": "3"},
                ],
                "brand_id": "9999",
                "_brand_mode": "store",
                "attrs": [],
            },
        )

    with pytest.raises(ValueError, match="超过模板上限"):
        SyncService(database)._validate_specified_product_category(
            store,
            client,
            {
                "_specified_category_id": "3",
                "cats_v2": [
                    {"cat_id": "1"},
                    {"cat_id": "2"},
                    {"cat_id": "3"},
                ],
                "brand_id": "2100000000",
                "_brand_mode": "none",
                "_deposit_limit_cents": 1000,
                "attrs": [],
            },
        )


def test_upload_payload_overrides_source_category_with_specified_path() -> None:
    page = object.__new__(UploadPage)
    page.db = None
    processed = page._upload_payload(
        {
            "source_product_id": "SOURCE-1",
            "title": "指定类目商品",
            "price": 1000,
            "payload": _image_payload(),
        },
        {
            "category_mode": "specified",
            "specified_category_id": "3",
            "specified_category_ids": ["1", "2", "3"],
            "specified_category_path": "服饰 / 女装 / 连衣裙",
            "brand_mode": "store",
            "brand_id": "1001",
            "strip_title_brackets": False,
            "price_multiplier": 1,
            "price_offset": 0,
            "stock": 0,
            "title_rules": "",
            "sku_rules": "",
            "image_rules": "",
        },
    )
    assert processed["cats"] == []
    assert processed["cats_v2"] == [
        {"cat_id": "1"},
        {"cat_id": "2"},
        {"cat_id": "3"},
    ]
    assert processed["category_path"] == "服饰 / 女装 / 连衣裙"
    assert processed["brand_id"] == "1001"
    assert processed["_specified_category_id"] == "3"


def test_new_template_and_legacy_missing_fields_use_non_destructive_image_defaults(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "webui.db")
    controller = NextUiController(database)
    saved = controller.dispatch(
        "upload.templates.save",
        {
            "template": {
                "name": "图片默认值",
                "price_multiplier": 1,
                "stock": 0,
                "listing_mode": "immediate",
                "duplicate_filter_mode": "off",
                "delivery_type": "现货",
                "delivery_time": "48小时内发货",
                "image_mode": "默认方式",
                "head_action": "删除违规图片",
                "detail_action": "删除违规图片",
            }
        },
    )["template"]
    assert saved["remove_head_index"] == 0
    assert saved["remove_detail_index"] == 0
    assert saved["trim_detail"] == 0
    assert saved["trim_detail_tail"] == 0
    assert saved["detail_keep_first"] == 0

    edited = controller.dispatch(
        "upload.templates.save",
        {
            "template": {
                **saved,
                "remove_head_index": 3,
                "remove_detail_index": 4,
                "trim_detail": 0,
                "trim_detail_tail": 1,
                "detail_keep_first": 5,
            }
        },
    )["template"]
    assert edited["trim_detail"] == 0
    assert edited["trim_detail_tail"] == 1
    assert edited["detail_keep_first"] == 5


def test_preflight_reports_the_final_images_after_default_trimming(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "webui.db")
    controller = NextUiController(database)
    template = controller.dispatch(
        "upload.templates.save",
        {
            "template": {
                "name": "图片预检模板",
                "price_multiplier": 1,
                "stock": 0,
                "listing_mode": "immediate",
                "duplicate_filter_mode": "off",
                "delivery_type": "现货",
                "delivery_time": "48小时内发货",
                "image_mode": "默认方式",
                "head_action": "删除违规图片",
                "detail_action": "删除违规图片",
            }
        },
    )["template"]
    payload = _image_payload()
    payload["head_imgs"] = [f"h{index}" for index in range(1, 4)]
    payload["skus"][0]["sku_attrs"] = [
        {"attr_key": "规格", "attr_value": "默认"}
    ]
    payload["desc_info"]["imgs"] = [
        f"d{index}" for index in range(1, 9)
    ]
    database.set_setting(
        "upload_batch",
        json.dumps(
            [
                {
                    "batch_id": "image-preflight",
                    "source_product_id": "IMAGE-PREFLIGHT",
                    "title": "图片预检商品",
                    "price": 1000,
                    "stock": 1,
                    "platform": "微信小店",
                    "payload": payload,
                }
            ],
            ensure_ascii=False,
        ),
    )
    result = controller.dispatch(
        "upload.batch.preflight",
        {
            "batch_ids": ["image-preflight"],
            "template_id": template["id"],
        },
    )
    assert result["blocked"] == 0
    assert result["rows"][0]["mapping"]["head_image_count"] == 3
    assert result["rows"][0]["mapping"]["detail_image_count"] == 8

    store_id = database.add_store("预检店铺", "wx-image-preflight", "secret")
    created = controller.dispatch(
        "upload.tasks.create",
        {
            "batch_ids": ["image-preflight"],
            "store_ids": [store_id],
            "template_id": template["id"],
            "store_options": {
                str(store_id): {
                    "brand_mode": "none",
                    "brand_id": "2100000000",
                }
            },
        },
    )
    snapshot = created["tasks"][0]["items"][0]["image_snapshot"]
    assert snapshot["head_imgs"] == ["h1", "h2", "h3"]
    assert snapshot["detail_imgs"] == [
        "d1",
        "d2",
        "d3",
        "d4",
        "d5",
        "d6",
        "d7",
        "d8",
    ]
    page = object.__new__(UploadPage)
    page.db = database
    uploaded = page._upload_payload(
        created["tasks"][0]["items"][0],
        created["tasks"][0]["template"],
    )
    assert uploaded["head_imgs"] == snapshot["head_imgs"]
    assert uploaded["desc_info"]["imgs"] == snapshot["detail_imgs"]
def test_image_rules_pass_empty_lists_to_the_real_upload_fallback() -> None:
    resolved = _apply_template_image_rules(
        {
            "head_imgs": ["h1", "h2", "h3"],
            "desc_info": {"imgs": ["d1", "d2", "d3"]},
        },
        {
            "remove_head_index": 2,
            "trim_head": 2,
            "remove_detail_index": 0,
            "trim_detail": 2,
            "trim_detail_tail": 2,
            "detail_keep_first": 0,
        },
    )

    assert resolved["head_imgs"] == []
    assert resolved["desc_info"]["imgs"] == []
    assert resolved["_source_image_counts"] == {"head": 3, "detail": 3}




def test_explicit_image_trim_rules_do_not_block_task_creation(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "webui.db")
    controller = NextUiController(database)
    template = controller.dispatch(
        "upload.templates.save",
        {
            "template": {
                "name": "主动裁剪图片",
                "price_multiplier": 1,
                "stock": 0,
                "listing_mode": "immediate",
                "duplicate_filter_mode": "off",
                "delivery_type": "现货",
                "delivery_time": "48小时内发货",
                "image_mode": "默认方式",
                "head_action": "删除违规图片",
                "detail_action": "删除违规图片",
                "remove_head_index": 1,
                "trim_detail": 2,
                "trim_detail_tail": 2,
            }
        },
    )["template"]
    payload = _image_payload()
    payload["head_imgs"] = ["only-head"]
    payload["skus"][0]["sku_attrs"] = [
        {"attr_key": "规格", "attr_value": "默认"}
    ]
    payload["desc_info"]["imgs"] = [
        f"d{index}" for index in range(1, 4)
    ]
    database.set_setting(
        "upload_batch",
        json.dumps(
            [
                {
                    "batch_id": "image-user-rules",
                    "source_product_id": "IMAGE-USER-RULES",
                    "title": "用户主动裁剪图片商品",
                    "price": 1000,
                    "stock": 1,
                    "platform": "微信小店",
                    "payload": payload,
                }
            ],
            ensure_ascii=False,
        ),
    )

    preflight = controller.dispatch(
        "upload.batch.preflight",
        {
            "batch_ids": ["image-user-rules"],
            "template_id": template["id"],
        },
    )
    assert preflight["blocked"] == 0, preflight["rows"][0]["issues"]
    assert preflight["rows"][0]["mapping"]["detail_image_count"] == 0

    store_id = database.add_store("裁剪店铺", "wx-image-trim", "secret")
    created = controller.dispatch(
        "upload.tasks.create",
        {
            "batch_ids": ["image-user-rules"],
            "store_ids": [store_id],
            "template_id": template["id"],
            "store_options": {
                str(store_id): {
                    "brand_mode": "none",
                    "brand_id": "2100000000",
                }
            },
        },
    )
    assert created["created"] == 1
    snapshot = created["tasks"][0]["items"][0]["image_snapshot"]
    assert snapshot["head_imgs"] == []
    assert snapshot["detail_imgs"] == []
