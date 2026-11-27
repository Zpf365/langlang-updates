from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from weimi_manager.db import Database
from weimi_manager.pyside.pages.upload import UploadRunner
from weimi_manager.wechat_api import WechatApiError


class FakeProtector:
    @staticmethod
    def protect(value: str) -> bytes:
        return b"fake:" + value.encode("utf-8")

    @staticmethod
    def unprotect(value: bytes) -> str:
        return value[5:].decode("utf-8")


def test_upload_runner_marks_missing_product_id_for_remote_verification(
    tmp_path: Path,
) -> None:
    db = Database(tmp_path / "upload.db", protector=FakeProtector())
    store_id = db.add_store("Fixture", "wx-upload", "secret")
    store = db.get_store(store_id)

    class Service:
        calls = 0

        @classmethod
        def product_write(cls, *_args, **_kwargs):
            cls.calls += 1
            return {"errcode": 0}

    task = {
        "id": "task-1",
        "store_id": store_id,
        "store_name": store.name,
        "status": "pending",
        "template": {
            "name": "fixture",
            "duplicate_filter_mode": "off",
            "auto_open_category": False,
            "listing_mode": "immediate",
        },
        "items": [
            {
                "item_id": "item-1",
                "source_product_id": "source-1",
                "title": "Fixture product",
                "price": 100,
                "stock": 2,
                "status": "pending",
                "payload": {
                    "title": "Fixture product",
                    "head_imgs": [
                        "https://example.invalid/1.jpg",
                        "https://example.invalid/2.jpg",
                        "https://example.invalid/3.jpg",
                    ],
                    "skus": [{"sale_price": 100, "stock_num": 2}],
                    "cats": ["6033", "6093", "549163"],
                },
            }
        ],
    }
    workspace = SimpleNamespace(db=db, service=Service())
    errors = UploadRunner(workspace, [task]).run([task])
    assert errors == []
    assert Service.calls == 1
    assert task["items"][0]["status"] == "verification_pending"
    assert "核实" in task["items"][0]["message"]


def test_old_template_false_still_auto_applies_category(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db = Database(
        tmp_path / "upload-category-false.db",
        protector=FakeProtector(),
    )
    store_id = db.add_store(
        "Category false",
        "wx-category-false",
        "secret",
    )
    store = db.get_store(store_id)
    product_calls = []
    category_calls = []

    class Service:
        @classmethod
        def product_write(cls, *_args, **_kwargs):
            from weimi_manager.wechat_api import WechatApiError

            product_calls.append("product")
            if len(product_calls) == 1:
                raise WechatApiError("商品无该类目权限", code=6600145)
            return {"errcode": 0, "product_id": "WX-CATEGORY-RECOVERED"}

    class CategoryClient:
        def __init__(self, _db, _store):
            self.applied = False

        def category_relation_statuses(self, category_ids):
            if not self.applied:
                return {}
            return {str(category_id): "已生效" for category_id in category_ids}

        def batch_add_categories(self, category_ids):
            category_calls.append(tuple(category_ids))
            self.applied = True
            return {"success": True}

    monkeypatch.setattr(
        "weimi_manager.pyside.pages.upload.ShopPrivateClient",
        CategoryClient,
    )
    task = {
        "id": "task-category-false",
        "store_id": store_id,
        "store_name": store.name,
        "status": "pending",
        "template": {
            "name": "legacy false",
            "duplicate_filter_mode": "off",
            "auto_open_category": False,
            "listing_mode": "immediate",
        },
        "items": [
            {
                "item_id": "item-category-false",
                "source_product_id": "SOURCE-CATEGORY-FALSE",
                "title": "Category false product",
                "price": 100,
                "stock": 2,
                "status": "pending",
                "payload": {
                    "title": "Category false product",
                    "head_imgs": [
                        "https://example.invalid/1.jpg",
                        "https://example.invalid/2.jpg",
                        "https://example.invalid/3.jpg",
                    ],
                    "skus": [{"sale_price": 100, "stock_num": 2}],
                    "cats_v2": [
                        {"cat_id": "1000"},
                        {"cat_id": "2000"},
                        {"cat_id": "3001"},
                    ],
                    "category_apply_ids": ["1000", "2000", "3001"],
                },
            }
        ],
    }
    workspace = SimpleNamespace(db=db, service=Service())
    progress_messages = []

    errors = UploadRunner(
        workspace,
        [task],
        progress=lambda message, _completed, _total: progress_messages.append(
            message
        ),
    ).run([task])

    assert errors == []
    assert product_calls == ["product", "product"]
    assert category_calls == [("3001",)]
    assert task["items"][0]["status"] == "success"
    assert task["items"][0]["product_id"] == "WX-CATEGORY-RECOVERED"
    assert "正在检查并申请目标类目" in progress_messages
    assert "正在提交类目开通申请" in progress_messages
    assert "类目已生效，正在自动重新上货" in progress_messages


@pytest.mark.parametrize(
    ("error_text", "application_label", "application_reason"),
    [
        (
            "目标类目已冻结，商品不可创建",
            "已冻结",
            "平台已冻结该叶子类目",
        ),
        (
            "目标类目已失效或不存在，商品不可创建",
            "已失效",
            "刷新后的官方类目树未找到该叶子",
        ),
        (
            "目标类目已禁用，商品不可创建",
            "已禁用",
            "平台已禁用该叶子类目",
        ),
    ],
)
def test_upload_runner_refreshes_unavailable_store_category_once_and_blocks_later_writes(
    tmp_path: Path,
    monkeypatch,
    error_text: str,
    application_label: str,
    application_reason: str,
) -> None:
    db = Database(
        tmp_path / "upload-category-refresh-freeze.db",
        protector=FakeProtector(),
    )
    store_id = db.add_store(
        "Frozen category",
        "wx-category-refresh-freeze",
        "secret",
    )
    store = db.get_store(store_id)
    product_calls = []
    refresh_calls = []

    class Service:
        @classmethod
        def product_write(cls, *_args, **_kwargs):
            product_calls.append("product")
            raise WechatApiError(
                error_text,
                code=6609999,
            )

        @classmethod
        def upload_category_options(
            cls,
            selected_store,
            *,
            refresh=False,
        ):
            refresh_calls.append((selected_store.id, refresh))
            return {
                "roots": [
                    {
                        "category_id": "1000",
                        "children": [
                            {
                                "category_id": "3001",
                                "application_state": "unavailable",
                                "application_label": application_label,
                                "application_reason": application_reason,
                                "application_eligible": False,
                            }
                        ],
                    }
                ],
            }

    class CategoryClient:
        def __init__(self, _db, _store):
            pass

        def category_application_checks(self, category_ids):
            return {
                str(category_id): {
                    "state": "effective",
                    "label": "已生效",
                    "reason": "",
                    "eligible": False,
                }
                for category_id in category_ids
            }

        def batch_add_categories(self, _category_ids):
            raise AssertionError(
                "a frozen category must not be submitted for application"
            )

    monkeypatch.setattr(
        "weimi_manager.pyside.pages.upload.ShopPrivateClient",
        CategoryClient,
    )

    def make_item(suffix):
        return {
            "item_id": f"item-{suffix}",
            "source_product_id": f"SOURCE-{suffix}",
            "title": f"Frozen category {suffix}",
            "price": 100,
            "stock": 2,
            "status": "pending",
            "payload": {
                "title": f"Frozen category {suffix}",
                "head_imgs": [
                    "https://example.invalid/1.jpg",
                    "https://example.invalid/2.jpg",
                    "https://example.invalid/3.jpg",
                ],
                "skus": [{"sale_price": 100, "stock_num": 2}],
                "cats_v2": [
                    {"cat_id": "1000"},
                    {"cat_id": "3001"},
                ],
            },
        }

    task = {
        "id": "task-category-refresh-freeze",
        "store_id": store_id,
        "store_name": store.name,
        "status": "pending",
        "template": {
            "name": "frozen category",
            "duplicate_filter_mode": "off",
            "listing_mode": "immediate",
        },
        "items": [
            make_item("first"),
            make_item("second"),
        ],
    }

    errors = UploadRunner(
        SimpleNamespace(db=db, service=Service()),
        [task],
    ).run([task])

    assert product_calls == ["product"]
    assert refresh_calls == [(store_id, True)]
    assert task["items"][0]["status"] == "failed"
    assert task["items"][1]["status"] == "failed"
    assert application_reason in task["items"][0]["message"]
    assert application_reason in task["items"][1]["message"]
    assert len(errors) == 2


def test_upload_runner_memoizes_failed_store_category_refresh(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db = Database(
        tmp_path / "upload-category-refresh-failure.db",
        protector=FakeProtector(),
    )
    store_id = db.add_store(
        "Category refresh failure",
        "wx-category-refresh-failure",
        "secret",
    )
    store = db.get_store(store_id)
    product_calls = []
    refresh_calls = []

    class Service:
        @classmethod
        def product_write(cls, *_args, **_kwargs):
            product_calls.append("product")
            raise WechatApiError("无该类目权限", code=6600145)

        @classmethod
        def upload_category_options(
            cls,
            selected_store,
            *,
            refresh=False,
        ):
            refresh_calls.append((selected_store.id, refresh))
            raise RuntimeError("类目网络读取失败")

    class CategoryClient:
        def __init__(self, _db, _store):
            pass

        def category_application_checks(self, category_ids):
            return {
                str(category_id): {
                    "state": "effective",
                    "label": "已生效",
                    "reason": "",
                    "eligible": False,
                }
                for category_id in category_ids
            }

    monkeypatch.setattr(
        "weimi_manager.pyside.pages.upload.ShopPrivateClient",
        CategoryClient,
    )

    def make_item(suffix):
        return {
            "item_id": f"item-{suffix}",
            "source_product_id": f"SOURCE-{suffix}",
            "title": f"Refresh failure {suffix}",
            "price": 100,
            "stock": 2,
            "status": "pending",
            "payload": {
                "title": f"Refresh failure {suffix}",
                "head_imgs": [
                    "https://example.invalid/1.jpg",
                    "https://example.invalid/2.jpg",
                    "https://example.invalid/3.jpg",
                ],
                "skus": [{"sale_price": 100, "stock_num": 2}],
                "cats_v2": [
                    {"cat_id": "1000"},
                    {"cat_id": "3001"},
                ],
            },
        }

    task = {
        "id": "task-category-refresh-failure",
        "store_id": store_id,
        "store_name": store.name,
        "status": "pending",
        "template": {
            "name": "category refresh failure",
            "duplicate_filter_mode": "off",
            "listing_mode": "immediate",
        },
        "items": [
            make_item("first"),
            make_item("second"),
        ],
    }

    UploadRunner(
        SimpleNamespace(db=db, service=Service()),
        [task],
    ).run([task])

    assert product_calls == ["product"]
    assert refresh_calls == [(store_id, True)]
    assert task["items"][0]["status"] == "waiting_category"
    assert task["items"][1]["status"] == "waiting_category"
    assert "刷新类目失败" in task["items"][0]["message"]
    assert "刷新类目失败" in task["items"][1]["message"]


def test_upload_runner_applies_eligible_refreshed_category_and_retries_write(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db = Database(
        tmp_path / "upload-category-refresh-apply.db",
        protector=FakeProtector(),
    )
    store_id = db.add_store(
        "Category apply",
        "wx-category-refresh-apply",
        "secret",
    )
    store = db.get_store(store_id)
    product_calls = []
    refresh_calls = []
    apply_calls = []

    class Service:
        @classmethod
        def product_write(cls, *_args, **_kwargs):
            product_calls.append("product")
            if len(product_calls) == 1:
                raise WechatApiError("无该类目权限", code=6600145)
            return {"product_id": "WX-CATEGORY-REFRESHED"}

        @classmethod
        def upload_category_options(
            cls,
            selected_store,
            *,
            refresh=False,
        ):
            refresh_calls.append((selected_store.id, refresh))
            return {
                "roots": [
                    {
                        "category_id": "1000",
                        "children": [
                            {
                                "category_id": "3001",
                                "application_state": "not_applied",
                                "application_label": "未申请",
                                "application_reason": "",
                                "application_eligible": True,
                            }
                        ],
                    }
                ],
            }

    class CategoryClient:
        applied = False

        def __init__(self, _db, _store):
            pass

        def category_application_checks(self, category_ids):
            return {
                str(category_id): {
                    "state": (
                        "effective" if self.applied else "not_applied"
                    ),
                    "label": "已生效" if self.applied else "未申请",
                    "reason": "",
                    "eligible": not self.applied,
                }
                for category_id in category_ids
            }

        def batch_add_categories(self, category_ids):
            apply_calls.append(tuple(category_ids))
            self.applied = True
            return {"success": True}

    monkeypatch.setattr(
        "weimi_manager.pyside.pages.upload.ShopPrivateClient",
        CategoryClient,
    )
    task = {
        "id": "task-category-refresh-apply",
        "store_id": store_id,
        "store_name": store.name,
        "status": "pending",
        "template": {
            "name": "category apply",
            "duplicate_filter_mode": "off",
            "listing_mode": "immediate",
        },
        "items": [
            {
                "item_id": "item-category-refresh-apply",
                "source_product_id": "SOURCE-CATEGORY-REFRESH-APPLY",
                "title": "Category apply product",
                "price": 100,
                "stock": 2,
                "status": "pending",
                "payload": {
                    "title": "Category apply product",
                    "head_imgs": [
                        "https://example.invalid/1.jpg",
                        "https://example.invalid/2.jpg",
                        "https://example.invalid/3.jpg",
                    ],
                    "skus": [{"sale_price": 100, "stock_num": 2}],
                    "cats_v2": [
                        {"cat_id": "1000"},
                        {"cat_id": "3001"},
                    ],
                },
            }
        ],
    }

    errors = UploadRunner(
        SimpleNamespace(db=db, service=Service()),
        [task],
    ).run([task])

    assert errors == []
    assert product_calls == ["product", "product"]
    assert refresh_calls == [(store_id, True)]
    assert apply_calls == [("3001",)]
    assert task["items"][0]["status"] == "success"
    assert task["items"][0]["product_id"] == "WX-CATEGORY-REFRESHED"


@pytest.mark.parametrize(
    ("control_event", "expected_status"),
    [
        ("cancelled", "cancelled"),
        ("paused", "paused"),
    ],
)
@pytest.mark.parametrize(
    "control_at",
    [
        "refresh",
        "category_apply",
    ],
)
def test_upload_runner_stops_category_actions_after_cancel_or_pause(
    tmp_path: Path,
    monkeypatch,
    control_event: str,
    expected_status: str,
    control_at: str,
) -> None:
    db = Database(
        tmp_path / f"upload-category-{control_event}-{control_at}.db",
        protector=FakeProtector(),
    )
    store_id = db.add_store(
        "Category stopped",
        f"wx-category-{control_event}-{control_at}",
        "secret",
    )
    store = db.get_store(store_id)
    product_calls = []
    refresh_calls = []
    apply_calls = []
    runner_holder = {}

    def stop_runner() -> None:
        getattr(runner_holder["runner"], control_event).set()

    class Service:
        @classmethod
        def product_write(cls, *_args, **_kwargs):
            product_calls.append("product")
            if len(product_calls) == 1:
                raise WechatApiError("无该类目权限", code=6600145)
            return {"product_id": "SHOULD-NOT-RETRY"}

        @classmethod
        def upload_category_options(
            cls,
            selected_store,
            *,
            refresh=False,
        ):
            refresh_calls.append((selected_store.id, refresh))
            if control_at == "refresh":
                stop_runner()
            return {
                "roots": [
                    {
                        "category_id": "1000",
                        "children": [
                            {
                                "category_id": "3001",
                                "application_state": "not_applied",
                                "application_label": "未申请",
                                "application_reason": "",
                                "application_eligible": True,
                            }
                        ],
                    }
                ],
            }

    class CategoryClient:
        def __init__(self, _db, _store):
            pass

        def category_application_checks(self, category_ids):
            return {
                str(category_id): {
                    "state": "effective",
                    "label": "已生效",
                    "reason": "",
                    "eligible": False,
                }
                for category_id in category_ids
            }

        def batch_add_categories(self, category_ids):
            apply_calls.append(tuple(category_ids))
            if control_at == "category_apply":
                stop_runner()
            return {"success": True}

    monkeypatch.setattr(
        "weimi_manager.pyside.pages.upload.ShopPrivateClient",
        CategoryClient,
    )
    task = {
        "id": f"task-category-{control_event}-{control_at}",
        "store_id": store_id,
        "store_name": store.name,
        "status": "pending",
        "template": {
            "name": "category stopped",
            "duplicate_filter_mode": "off",
            "listing_mode": "immediate",
        },
        "items": [
            {
                "item_id": f"item-category-{control_event}-{control_at}",
                "source_product_id": "SOURCE-CATEGORY-STOPPED",
                "title": "Category stopped product",
                "price": 100,
                "stock": 2,
                "status": "pending",
                "payload": {
                    "title": "Category stopped product",
                    "head_imgs": [
                        "https://example.invalid/1.jpg",
                        "https://example.invalid/2.jpg",
                        "https://example.invalid/3.jpg",
                    ],
                    "skus": [{"sale_price": 100, "stock_num": 2}],
                    "cats_v2": [
                        {"cat_id": "1000"},
                        {"cat_id": "3001"},
                    ],
                },
            }
        ],
    }

    runner = UploadRunner(
        SimpleNamespace(db=db, service=Service()),
        [task],
    )
    runner_holder["runner"] = runner
    runner.run([task])

    assert product_calls == ["product"]
    assert refresh_calls == [(store_id, True)]
    if control_at == "refresh":
        assert apply_calls == []
    else:
        assert apply_calls == [("3001",)]
    assert task["items"][0]["status"] == expected_status


@pytest.mark.parametrize(
    "control_event",
    [
        "cancelled",
        "paused",
    ],
)
def test_upload_runner_preserves_unknown_write_after_cancel_or_pause(
    tmp_path: Path,
    control_event: str,
) -> None:
    db = Database(
        tmp_path / f"upload-unknown-{control_event}.db",
        protector=FakeProtector(),
    )
    store_id = db.add_store(
        "Unknown write",
        f"wx-unknown-{control_event}",
        "secret",
    )
    store = db.get_store(store_id)
    product_calls = []
    runner_holder = {}

    class Service:
        @classmethod
        def product_write(cls, *_args, **_kwargs):
            product_calls.append("product")
            getattr(runner_holder["runner"], control_event).set()
            raise TimeoutError("timed out")

    task = {
        "id": f"task-unknown-{control_event}",
        "store_id": store_id,
        "store_name": store.name,
        "status": "pending",
        "template": {
            "name": "unknown write",
            "duplicate_filter_mode": "off",
            "listing_mode": "immediate",
        },
        "items": [
            {
                "item_id": f"item-unknown-{control_event}",
                "source_product_id": "SOURCE-UNKNOWN-WRITE",
                "title": "Unknown write product",
                "price": 100,
                "stock": 2,
                "status": "pending",
                "payload": {
                    "title": "Unknown write product",
                    "head_imgs": [
                        "https://example.invalid/1.jpg",
                        "https://example.invalid/2.jpg",
                        "https://example.invalid/3.jpg",
                    ],
                    "skus": [{"sale_price": 100, "stock_num": 2}],
                    "cats_v2": [
                        {"cat_id": "1000"},
                        {"cat_id": "3001"},
                    ],
                },
            }
        ],
    }
    runner = UploadRunner(
        SimpleNamespace(db=db, service=Service()),
        [task],
    )
    runner_holder["runner"] = runner

    runner.run([task])

    assert product_calls == ["product"]
    assert task["items"][0]["status"] == "verification_pending"
    assert "结果未知" in task["items"][0]["message"]


def test_upload_runner_waits_later_products_after_shared_category_application(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db = Database(
        tmp_path / "upload-category-refresh-pending.db",
        protector=FakeProtector(),
    )
    store_id = db.add_store(
        "Category pending",
        "wx-category-refresh-pending",
        "secret",
    )
    store = db.get_store(store_id)
    product_calls = []
    refresh_calls = []
    apply_calls = []

    class Service:
        @classmethod
        def product_write(cls, *_args, **_kwargs):
            product_calls.append("product")
            raise WechatApiError("无该类目权限", code=6600145)

        @classmethod
        def upload_category_options(
            cls,
            selected_store,
            *,
            refresh=False,
        ):
            refresh_calls.append((selected_store.id, refresh))
            return {
                "roots": [
                    {
                        "category_id": "1000",
                        "children": [
                            {
                                "category_id": "3001",
                                "application_state": "not_applied",
                                "application_label": "未申请",
                                "application_reason": "",
                                "application_eligible": True,
                            }
                        ],
                    }
                ],
            }

    class CategoryClient:
        submitted = False

        def __init__(self, _db, _store):
            pass

        def category_application_checks(self, category_ids):
            return {
                str(category_id): {
                    "state": (
                        "pending" if self.submitted else "not_applied"
                    ),
                    "label": "审核中" if self.submitted else "未申请",
                    "reason": "",
                    "eligible": not self.submitted,
                }
                for category_id in category_ids
            }

        def batch_add_categories(self, category_ids):
            apply_calls.append(tuple(category_ids))
            self.submitted = True
            return {"success": True}

    monkeypatch.setattr(
        "weimi_manager.pyside.pages.upload.ShopPrivateClient",
        CategoryClient,
    )

    def make_item(suffix):
        return {
            "item_id": f"item-{suffix}",
            "source_product_id": f"SOURCE-{suffix}",
            "title": f"Category pending {suffix}",
            "price": 100,
            "stock": 2,
            "status": "pending",
            "payload": {
                "title": f"Category pending {suffix}",
                "head_imgs": [
                    "https://example.invalid/1.jpg",
                    "https://example.invalid/2.jpg",
                    "https://example.invalid/3.jpg",
                ],
                "skus": [{"sale_price": 100, "stock_num": 2}],
                "cats_v2": [
                    {"cat_id": "1000"},
                    {"cat_id": "3001"},
                ],
            },
        }

    task = {
        "id": "task-category-refresh-pending",
        "store_id": store_id,
        "store_name": store.name,
        "status": "pending",
        "template": {
            "name": "category pending",
            "duplicate_filter_mode": "off",
            "listing_mode": "immediate",
        },
        "items": [
            make_item("first"),
            make_item("second"),
        ],
    }

    UploadRunner(
        SimpleNamespace(db=db, service=Service()),
        [task],
    ).run([task])

    assert product_calls == ["product"]
    assert refresh_calls == [(store_id, True)]
    assert apply_calls == [("3001",)]
    assert task["items"][0]["status"] == "waiting_category"
    assert task["items"][1]["status"] == "waiting_category"
    assert task["items"][1]["category_state"] == "pending"


def test_upload_runner_retries_waiting_category_items_after_resume(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db = Database(tmp_path / "upload-category.db", protector=FakeProtector())
    store_id = db.add_store("Category", "wx-category-upload", "secret")
    store = db.get_store(store_id)
    events = []

    class Service:
        calls = 0

        @classmethod
        def product_write(cls, *_args, **_kwargs):
            cls.calls += 1
            events.append("product")
            return {"errcode": 0, "product_id": "WX-CATEGORY-1"}

    class CategoryClient:
        def __init__(self, _db, _store):
            pass

        def category_application_checks(self, category_ids):
            events.append("category")
            return {
                str(category_id): {
                    "state": "effective",
                    "label": "已生效",
                    "reason": "",
                    "eligible": False,
                }
                for category_id in category_ids
            }

        def batch_add_categories(self, _category_ids):
            raise AssertionError("already submitted category must not reapply")

    monkeypatch.setattr(
        "weimi_manager.pyside.pages.upload.ShopPrivateClient",
        CategoryClient,
    )
    task = {
        "id": "task-category",
        "store_id": store_id,
        "store_name": store.name,
        "status": "needs_category",
        "template": {
            "name": "category",
            "duplicate_filter_mode": "off",
            "auto_open_category": False,
            "listing_mode": "immediate",
        },
        "items": [
            {
                "item_id": "item-category",
                "source_product_id": "WX-CATEGORY-SOURCE",
                "title": "Category product",
                "price": 100,
                "stock": 2,
                "status": "waiting_category",
                "message": "Category approval pending",
                "category_state": "waiting",
                "category_ids": ["3001"],
                "category_apply_submitted": True,
                "category_application_at": "2026-11-22T10:00:00",
                "category_check_attempts": 3,
                "payload": {
                    "title": "Category product",
                    "head_imgs": [
                        "https://example.invalid/1.jpg",
                        "https://example.invalid/2.jpg",
                        "https://example.invalid/3.jpg",
                    ],
                    "skus": [{"sale_price": 100, "stock_num": 2}],
                    "cats": ["6033", "6093", "549163"],
                },
            }
        ],
    }
    workspace = SimpleNamespace(db=db, service=Service())

    errors = UploadRunner(workspace, [task]).run([task])

    assert errors == []
    assert Service.calls == 1
    assert events == ["category", "product"]
    assert task["items"][0]["status"] == "success"
    assert task["items"][0]["product_id"] == "WX-CATEGORY-1"


def test_upload_runner_can_execute_only_selected_items(
    tmp_path: Path,
) -> None:
    db = Database(tmp_path / "upload-selected.db", protector=FakeProtector())
    store_id = db.add_store("Selected", "wx-selected-upload", "secret")
    store = db.get_store(store_id)

    class Service:
        calls = 0

        @classmethod
        def product_write(cls, *_args, **_kwargs):
            cls.calls += 1
            return {"errcode": 0, "product_id": f"WX-SELECTED-{cls.calls}"}

    payload = {
        "title": "Selected product",
        "head_imgs": [
            "https://example.invalid/1.jpg",
            "https://example.invalid/2.jpg",
            "https://example.invalid/3.jpg",
        ],
        "skus": [{"sale_price": 100, "stock_num": 2}],
        "cats": ["6033", "6093", "549163"],
    }
    task = {
        "id": "task-selected",
        "store_id": store_id,
        "store_name": store.name,
        "status": "pending",
        "template": {
            "name": "selected",
            "duplicate_filter_mode": "off",
            "auto_open_category": False,
            "listing_mode": "immediate",
        },
        "items": [
            {
                "item_id": "item-skip",
                "source_product_id": "SOURCE-SKIP",
                "title": "Skip product",
                "price": 100,
                "stock": 2,
                "status": "pending",
                "payload": payload,
            },
            {
                "item_id": "item-run",
                "source_product_id": "SOURCE-RUN",
                "title": "Run product",
                "price": 100,
                "stock": 2,
                "status": "pending",
                "payload": payload,
            },
        ],
    }
    workspace = SimpleNamespace(db=db, service=Service())

    errors = UploadRunner(workspace, [task]).run(
        [task],
        selected_item_ids={"item-run"},
    )

    assert errors == []
    assert Service.calls == 1
    assert task["items"][0]["status"] == "pending"
    assert task["items"][1]["status"] == "success"


def test_upload_runner_removes_internal_category_fields_before_remote_write(
    tmp_path: Path,
) -> None:
    db = Database(tmp_path / "upload-clean-payload.db", protector=FakeProtector())
    store_id = db.add_store("Clean", "wx-clean", "secret")
    store = db.get_store(store_id)
    submitted = []

    class Service:
        @classmethod
        def product_write(cls, _store, _action, payload, **_kwargs):
            submitted.append(dict(payload))
            return {"product_id": "WX-CLEAN"}

    task = {
        "id": "task-clean",
        "store_id": store_id,
        "store_name": store.name,
        "template": {
            "duplicate_filter_mode": "off",
            "auto_open_category": False,
        },
        "items": [
            {
                "item_id": "item-clean",
                "source_product_id": "CLEAN-SOURCE",
                "title": "Clean payload",
                "status": "pending",
                "payload": {
                    "title": "Clean payload",
                    "platform": "1688",
                    "head_imgs": [
                        "https://example.invalid/1.jpg",
                        "https://example.invalid/2.jpg",
                        "https://example.invalid/3.jpg",
                    ],
                    "skus": [{"sale_price": 100, "stock_num": 2}],
                    "cats_v2": [
                        {"cat_id": "1000"},
                        {"cat_id": "2000"},
                        {"cat_id": "3001"},
                    ],
                    "category_apply_ids": ["1000", "2000", "3001"],
                    "category_name": "internal name",
                    "category_hint": "internal hint",
                    "auto_category": {"category_id": "3001"},
                },
            }
        ],
    }

    errors = UploadRunner(
        SimpleNamespace(db=db, service=Service()),
        [task],
    ).run([task])

    assert errors == []
    assert task["items"][0]["status"] == "success"
    assert submitted
    for key in (
        "category_apply_ids",
        "category_name",
        "category_hint",
        "auto_category",
        "_auto_category_required",
    ):
        assert key not in submitted[0]


def test_upload_runner_keeps_category_work_isolated_per_store(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db = Database(tmp_path / "upload-two-stores.db", protector=FakeProtector())
    first_store_id = db.add_store("First", "wx-first", "secret")
    second_store_id = db.add_store("Second", "wx-second", "secret")
    stores = {
        first_store_id: db.get_store(first_store_id),
        second_store_id: db.get_store(second_store_id),
    }
    product_calls = []
    category_calls = []

    class Service:
        attempts = {}

        @classmethod
        def product_write(cls, store, _action, _payload, **_kwargs):
            product_calls.append(store.id)
            cls.attempts[store.id] = cls.attempts.get(store.id, 0) + 1
            if cls.attempts[store.id] == 1:
                raise WechatApiError("商品无该类目权限", code=6600145)
            return {"product_id": f"WX-STORE-{store.id}"}

    class CategoryClient:
        def __init__(self, _db, store):
            self.store = store
            self.applied = False

        def category_application_checks(self, category_ids):
            category_calls.append((self.store.id, tuple(category_ids)))
            if self.applied:
                return {
                    category_id: {
                        "state": "effective",
                        "label": "已生效",
                        "reason": "",
                        "eligible": False,
                    }
                    for category_id in category_ids
                }
            return {
                category_id: {
                    "state": "not_applied",
                    "label": "未申请",
                    "reason": "",
                    "eligible": True,
                }
                for category_id in category_ids
            }

        def batch_add_categories(self, category_ids):
            category_calls.append(
                ("apply", self.store.id, tuple(category_ids))
            )
            self.applied = True
            return {"success": True}

    monkeypatch.setattr(
        "weimi_manager.pyside.pages.upload.ShopPrivateClient",
        CategoryClient,
    )

    def make_task(store_id, suffix, category_id):
        return {
            "id": f"task-{suffix}",
            "store_id": store_id,
            "store_name": stores[store_id].name,
            "template": {
                "duplicate_filter_mode": "off",
                "auto_open_category": False,
            },
            "items": [
                {
                    "item_id": f"item-{suffix}",
                    "source_product_id": f"SOURCE-{suffix}",
                    "title": f"Product {suffix}",
                    "status": "pending",
                    "payload": {
                        "title": f"Product {suffix}",
                        "head_imgs": [
                            "https://example.invalid/1.jpg",
                            "https://example.invalid/2.jpg",
                            "https://example.invalid/3.jpg",
                        ],
                        "skus": [{"sale_price": 100, "stock_num": 2}],
                        "cats_v2": [
                            {"cat_id": "1000"},
                            {"cat_id": category_id},
                        ],
                    },
                }
            ],
        }

    tasks = [
        make_task(first_store_id, "first", "3001"),
        make_task(second_store_id, "second", "4001"),
    ]
    errors = UploadRunner(
        SimpleNamespace(db=db, service=Service()),
        tasks,
    ).run(tasks)

    assert errors == []
    assert product_calls.count(first_store_id) == 2
    assert product_calls.count(second_store_id) == 2
    assert ("apply", first_store_id, ("3001",)) in category_calls
    assert ("apply", second_store_id, ("4001",)) in category_calls
    assert ("apply", first_store_id, ("4001",)) not in category_calls
    assert ("apply", second_store_id, ("3001",)) not in category_calls


def test_waiting_category_survives_restart_without_reapplying(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db = Database(tmp_path / "upload-restart.db", protector=FakeProtector())
    store_id = db.add_store("Restart", "wx-restart", "secret")
    store = db.get_store(store_id)
    product_attempts = 0
    apply_calls = []

    class Service:
        @classmethod
        def product_write(cls, _store, _action, _payload, **_kwargs):
            nonlocal product_attempts
            product_attempts += 1
            if product_attempts == 1:
                raise WechatApiError("商品无该类目权限", code=6600145)
            return {"product_id": "WX-RESTARTED"}

    class CategoryClient:
        mode = "pending_after_apply"

        def __init__(self, _db, _store):
            self.applied = False

        def category_application_checks(self, category_ids):
            if self.applied and self.mode == "pending_after_apply":
                state = "pending"
                label = "审核中"
                eligible = False
            elif self.mode == "effective":
                state = "effective"
                label = "已生效"
                eligible = False
            else:
                state = "not_applied"
                label = "未申请"
                eligible = True
            return {
                category_id: {
                    "state": state,
                    "label": label,
                    "reason": "",
                    "eligible": eligible,
                }
                for category_id in category_ids
            }

        def batch_add_categories(self, category_ids):
            apply_calls.append(tuple(category_ids))
            self.applied = True
            return {"success": True}

    monkeypatch.setattr(
        "weimi_manager.pyside.pages.upload.ShopPrivateClient",
        CategoryClient,
    )
    task = {
        "id": "task-restart",
        "store_id": store_id,
        "store_name": store.name,
        "template": {"duplicate_filter_mode": "off"},
        "items": [
            {
                "item_id": "item-restart",
                "source_product_id": "SOURCE-RESTART",
                "title": "Restart product",
                "status": "pending",
                "payload": {
                    "title": "Restart product",
                    "head_imgs": [
                        "https://example.invalid/1.jpg",
                        "https://example.invalid/2.jpg",
                        "https://example.invalid/3.jpg",
                    ],
                    "skus": [{"sale_price": 100, "stock_num": 2}],
                    "cats_v2": [{"cat_id": "2000"}, {"cat_id": "3001"}],
                },
            }
        ],
    }

    UploadRunner(
        SimpleNamespace(db=db, service=Service()),
        [task],
    ).run([task])

    assert task["items"][0]["status"] == "waiting_category"
    assert apply_calls == [("3001",)]
    attempt_id = task["items"][0]["submission_attempt_id"]
    persisted = json.loads(db.get_setting("upload_tasks", "[]"))

    CategoryClient.mode = "effective"
    second_errors = UploadRunner(
        SimpleNamespace(db=db, service=Service()),
        persisted,
    ).run(persisted)

    assert second_errors == []
    assert apply_calls == [("3001",)]
    assert persisted[0]["items"][0]["status"] == "success"
    assert persisted[0]["items"][0]["product_id"] == "WX-RESTARTED"
    assert persisted[0]["items"][0]["submission_attempt_id"] == attempt_id


def test_historical_category_recovery_never_writes_before_effective(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db = Database(tmp_path / "upload-history.db", protector=FakeProtector())
    pending_store_id = db.add_store("Pending", "wx-pending", "secret")
    effective_store_id = db.add_store("Effective", "wx-effective", "secret")
    stores = {
        pending_store_id: db.get_store(pending_store_id),
        effective_store_id: db.get_store(effective_store_id),
    }
    product_calls = []
    apply_calls = []

    class Service:
        @classmethod
        def product_write(cls, store, _action, _payload, **_kwargs):
            product_calls.append(store.id)
            return {"product_id": f"WX-HISTORY-{store.id}"}

    class CategoryClient:
        def __init__(self, _db, store):
            self.store = store

        def category_application_checks(self, category_ids):
            state = (
                "pending"
                if self.store.id == pending_store_id
                else "effective"
            )
            return {
                category_id: {
                    "state": state,
                    "label": "审核中" if state == "pending" else "已生效",
                    "reason": "",
                    "eligible": False,
                }
                for category_id in category_ids
            }

        def batch_add_categories(self, category_ids):
            apply_calls.append((self.store.id, tuple(category_ids)))
            return {"success": True}

    monkeypatch.setattr(
        "weimi_manager.pyside.pages.upload.ShopPrivateClient",
        CategoryClient,
    )

    def make_task(store_id, suffix):
        return {
            "id": f"task-{suffix}",
            "store_id": store_id,
            "store_name": stores[store_id].name,
            "status": "needs_category",
            "template": {"duplicate_filter_mode": "off"},
            "items": [
                {
                    "item_id": f"item-{suffix}",
                    "source_product_id": f"SOURCE-{suffix}",
                    "title": f"Historical {suffix}",
                    "status": "waiting_category",
                    "category_state": "waiting",
                    "category_auto_recovery": True,
                    "category_ids": ["3001"],
                    "category_apply_submitted": True,
                    "category_application_at": "2026-11-22T10:00:00",
                    "payload": {
                        "title": f"Historical {suffix}",
                        "head_imgs": [
                            "https://example.invalid/1.jpg",
                            "https://example.invalid/2.jpg",
                            "https://example.invalid/3.jpg",
                        ],
                        "skus": [{"sale_price": 100, "stock_num": 2}],
                        "cats_v2": [
                            {"cat_id": "2000"},
                            {"cat_id": "3001"},
                        ],
                    },
                }
            ],
        }

    tasks = [
        make_task(pending_store_id, "pending"),
        make_task(effective_store_id, "effective"),
    ]
    errors = UploadRunner(
        SimpleNamespace(db=db, service=Service()),
        tasks,
    ).run(tasks)

    assert errors == []
    assert apply_calls == []
    assert product_calls == [effective_store_id]
    assert tasks[0]["items"][0]["status"] == "waiting_category"
    assert tasks[1]["items"][0]["status"] == "success"
    assert tasks[1]["items"][0]["product_id"] == (
        f"WX-HISTORY-{effective_store_id}"
    )


def test_historical_category_without_leaf_checks_after_official_match(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db = Database(tmp_path / "upload-history-match.db", protector=FakeProtector())
    store_id = db.add_store("History match", "wx-history-match", "secret")
    store = db.get_store(store_id)
    calls = []

    class Service:
        @classmethod
        def product_write(cls, *_args, **_kwargs):
            calls.append("product")
            return {"product_id": "WX-HISTORY-MATCH-OK"}

    class CategoryClient:
        def __init__(self, _db, _store):
            self.state = "deposit_required"

        def category_application_checks(self, category_ids):
            calls.append(("category", tuple(category_ids)))
            return {
                category_id: {
                    "state": self.state,
                    "label": (
                        "保证金不足"
                        if self.state == "deposit_required"
                        else "已生效"
                    ),
                    "reason": (
                        "需补缴 200000 分"
                        if self.state == "deposit_required"
                        else ""
                    ),
                    "eligible": self.state == "not_applied",
                }
                for category_id in category_ids
            }

        def batch_add_categories(self, category_ids):
            calls.append(("apply", tuple(category_ids)))
            self.state = "effective"

    def match_category(_adapter, _store, payload, _template):
        calls.append("match")
        payload["cats_v2"] = [
            {"cat_id": "1000"},
            {"cat_id": "3001"},
        ]
        payload["category_apply_ids"] = ["3001"]
        payload["category_path"] = "鞋靴 / 女靴 / 女士切尔西靴"
        return payload

    monkeypatch.setattr(
        "weimi_manager.pyside.pages.upload.ShopPrivateClient",
        CategoryClient,
    )
    monkeypatch.setattr(
        "weimi_manager.pyside.pages.upload.LegacyUploadPage._auto_match_category",
        match_category,
    )
    task = {
        "id": "task-history-match",
        "store_id": store_id,
        "store_name": store.name,
        "template": {"duplicate_filter_mode": "off"},
        "items": [
            {
                "item_id": "item-history-match",
                "source_product_id": "SOURCE-HISTORY-MATCH",
                "title": "女式皮靴",
                "status": "waiting_category",
                "category_state": "waiting",
                "category_auto_recovery": True,
                "category_apply_submitted": False,
                "payload": {
                    "title": "女式皮靴",
                    "platform": "1688",
                    "category_name": "女式皮靴",
                    "category_hint": "女式皮靴",
                    "head_imgs": [
                        "https://example.invalid/1.jpg",
                        "https://example.invalid/2.jpg",
                        "https://example.invalid/3.jpg",
                    ],
                    "skus": [{"sale_price": 100, "stock_num": 2}],
                },
            }
        ],
    }

    errors = UploadRunner(
        SimpleNamespace(db=db, service=Service()),
        [task],
    ).run([task])

    assert errors == []
    assert calls == [
        "match",
        ("category", ("3001",)),
        ("apply", ("3001",)),
        ("category", ("3001",)),
        "product",
    ]
    assert task["items"][0]["status"] == "success"
    assert task["items"][0]["product_id"] == "WX-HISTORY-MATCH-OK"


def test_cancelled_waiting_category_task_does_not_touch_platform(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db = Database(tmp_path / "upload-cancel.db", protector=FakeProtector())
    store_id = db.add_store("Cancel", "wx-cancel", "secret")
    store = db.get_store(store_id)
    calls = []

    class Service:
        @classmethod
        def product_write(cls, *_args, **_kwargs):
            calls.append("product")
            return {"product_id": "SHOULD-NOT-RUN"}

    class CategoryClient:
        def __init__(self, *_args):
            pass

        def category_application_checks(self, _category_ids):
            calls.append("category")
            return {}

    monkeypatch.setattr(
        "weimi_manager.pyside.pages.upload.ShopPrivateClient",
        CategoryClient,
    )
    task = {
        "id": "task-cancel",
        "store_id": store_id,
        "store_name": store.name,
        "template": {"duplicate_filter_mode": "off"},
        "items": [
            {
                "item_id": "item-cancel",
                "source_product_id": "SOURCE-CANCEL",
                "title": "Cancelled product",
                "status": "waiting_category",
                "category_state": "waiting",
                "category_ids": ["3001"],
                "payload": {
                    "title": "Cancelled product",
                    "head_imgs": [
                        "https://example.invalid/1.jpg",
                        "https://example.invalid/2.jpg",
                        "https://example.invalid/3.jpg",
                    ],
                    "skus": [{"sale_price": 100, "stock_num": 2}],
                    "cats_v2": [{"cat_id": "3001"}],
                },
            }
        ],
    }
    runner = UploadRunner(
        SimpleNamespace(db=db, service=Service()),
        [task],
    )
    runner.cancelled.set()

    runner.run([task])

    assert calls == []
    assert task["items"][0]["status"] in {"cancelled", "waiting_category"}
