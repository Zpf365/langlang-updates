from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from unittest import mock

import pytest

from weimi_manager.db import Database
from weimi_manager.service import SyncService
from weimi_manager.shop_private import ShopPrivateClient, ShopPrivateError
from weimi_manager.ui.upload_page import UploadPage
from weimi_manager.upload_violations import (
    build_cloud_counterfeit_rows,
    filter_active_violation_records,
    find_upload_violation_title,
    load_upload_violation_title_records,
    mandatory_upload_violation_issue,
    merge_cloud_counterfeit_titles,
)
from weimi_manager.webui.controller import NextUiController


class FakeClient:
    def __init__(self) -> None:
        self.pushed: list[dict] = []
        self.remote: list[dict] = []

    def upsert_organization_counterfeit_violations(self, _token, rows):
        self.pushed.extend(rows)
        by_key = {
            str(row["dedupe_key"]): dict(row)
            for row in self.remote
        }
        for row in rows:
            by_key[str(row["dedupe_key"])] = dict(row)
        self.remote = list(by_key.values())
        return {"saved": len(rows)}

    def pull_organization_counterfeit_violations(self, _token):
        return [dict(row) for row in self.remote]


class FakeService:
    def sync_violation_library(self, _store):
        return SimpleNamespace(count=1)


def _controller(tmp_path: Path, client: FakeClient) -> NextUiController:
    database = Database(tmp_path / "cloud-vio.db")
    store_id = database.add_store("本地全量店铺", "wx-vio", "secret")
    database.update_web_status(store_id, "已登录", "")
    context = SimpleNamespace(
        tokens=SimpleNamespace(access_token="token"),
        member_role="owner",
        permissions={},
    )
    return NextUiController(
        database,
        product_service=FakeService(),
        auth_client=client,
        auth_context=context,
    )


def test_cloud_index_whitelist_filters_non_counterfeit_and_sensitive_fields() -> None:
    records = [
        {
            "record_id": "counterfeit",
            "title": "  假冒商品 Test  ",
            "reason": "平台同步：出售假冒商品",
            "product_id": "p1",
            "store_id": 1,
            "store_app_id": "wx-one",
            "store_name": "不得上云店铺",
            "raw_json": {"buyer": "张三", "phone": "13800138000"},
            "buyer_name": "张三",
            "phone": "13800138000",
            "address": "秘密地址",
            "cookie": "secret-cookie",
        },
        {
            "record_id": "other",
            "title": "普通违规商品",
            "reason": "虚假宣传",
            "product_id": "p2",
            "store_app_id": "wx-one",
        },
        {
            "record_id": "reason-fallback",
            "title": "出售假冒商品",
            "reason": "出售假冒商品",
            "product_id": "p3",
            "store_app_id": "wx-one",
        },
    ]
    rows = build_cloud_counterfeit_rows(records, store_app_id="wx-one")

    assert len(rows) == 1
    assert rows[0]["title"] == "假冒商品 Test"
    assert rows[0]["reason_category"] == "出售假冒商品"
    assert set(rows[0]) == {
        "dedupe_key",
        "normalized_title",
        "title",
        "reason_category",
        "product_id",
        "store_app_id",
    }
    serialized = json.dumps(rows, ensure_ascii=False)
    for forbidden in (
        "不得上云店铺",
        "张三",
        "13800138000",
        "秘密地址",
        "secret-cookie",
        "raw_json",
    ):
        assert forbidden not in serialized


def test_two_stores_use_independent_dedupe_keys_and_merge_idempotently() -> None:
    first = build_cloud_counterfeit_rows(
        [{"title": "同标题", "reason": "售假", "product_id": "p1"}],
        store_app_id="wx-a",
    )
    second = build_cloud_counterfeit_rows(
        [{"title": "同标题", "reason": "售假", "product_id": "p2"}],
        store_app_id="wx-b",
    )
    assert first[0]["dedupe_key"] != second[0]["dedupe_key"]

    merged = merge_cloud_counterfeit_titles(
        [],
        [*first, *second, *first],
    )
    assert len(merged) == 1
    assert merged[0]["normalized"] == "同标题"
    assert merged[0]["source"] == "cloud_counterfeit"


def test_counterfeit_title_without_product_id_is_still_shared() -> None:
    rows = build_cloud_counterfeit_rows(
        [
            {
                "title": "真实售假无商品ID",
                "reason": "出售假冒商品",
                "product_id": "",
            }
        ],
        store_app_id="wx-no-product-id",
    )

    assert len(rows) == 1
    assert rows[0]["title"] == "真实售假无商品ID"
    assert rows[0]["product_id"] == ""


def test_cloud_title_index_drops_non_counterfeit_reason() -> None:
    merged = merge_cloud_counterfeit_titles(
        [],
        [
            {
                "title": "普通违规标题",
                "normalized": "普通违规标题",
                "reason": "虚假宣传",
            },
            {
                "title": "真实售假标题",
                "normalized": "真实售假标题",
                "reason": "售假",
            },
        ],
    )

    assert [row["title"] for row in merged] == ["真实售假标题"]


def test_loaded_title_index_keeps_cloud_but_filters_stale_local(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "title-index.db")
    real_id = database.add_store("真实店铺", "wx-real", "secret")
    database.update_web_status(real_id, "已登录", "")
    stale_id = database.add_store("离线店铺", "wx-stale", "secret")
    database.update_web_status(stale_id, "未登录", "")
    database.set_setting(
        "violation_library",
        json.dumps(
            [
                {
                    "title": "真实本地售假",
                    "reason": "出售假冒商品",
                    "store_id": real_id,
                    "store_app_id": "wx-real",
                },
                {
                    "title": "离线本地售假",
                    "reason": "出售假冒商品",
                    "store_id": stale_id,
                    "store_app_id": "wx-stale",
                },
            ],
            ensure_ascii=False,
        ),
    )
    database.set_setting(
        "cloud_counterfeit_violation_titles",
        json.dumps(
            [
                {
                    "title": "组织云端售假",
                    "normalized": "组织云端售假",
                    "reason": "售假",
                    "source": "cloud_counterfeit",
                }
            ],
            ensure_ascii=False,
        ),
    )

    titles = [
        row["title"]
        for row in load_upload_violation_title_records(database)
    ]

    assert titles == ["真实本地售假", "组织云端售假"]


def test_controller_pushes_only_counterfeit_and_pulls_cross_store_title(
    tmp_path: Path,
) -> None:
    client = FakeClient()
    client.remote = [
        {
            "dedupe_key": "wx-other:p3:跨店云标题",
            "normalized_title": "跨店云标题",
            "title": "跨店云标题",
            "reason_category": "售假",
            "product_id": "p3",
            "store_app_id": "wx-other",
        }
    ]
    controller = _controller(tmp_path, client)
    store = controller.db.list_stores()[0]
    records = [
        {
            "record_id": "bad",
            "title": "跨店售假标题",
            "reason": "出售假冒商品",
            "product_id": "p1",
            "store_id": store.id,
            "store_app_id": store.app_id,
            "store_name": store.name,
        },
        {
            "record_id": "clean",
            "title": "普通宣传违规",
            "reason": "虚假宣传",
            "product_id": "p2",
            "store_id": store.id,
            "store_app_id": store.app_id,
            "store_name": store.name,
        },
    ]
    controller.db.set_setting(
        "violation_library",
        json.dumps(records, ensure_ascii=False),
    )

    result = controller._refresh_violations({})

    assert result["cloud_counterfeit"]["saved"] == 1
    assert len(client.pushed) == 1
    assert client.pushed[0]["title"] == "跨店售假标题"
    assert "普通宣传违规" not in json.dumps(client.pushed, ensure_ascii=False)
    cloud_rows = json.loads(
        controller.db.get_setting(
            "cloud_counterfeit_violation_titles",
            "[]",
        )
    )
    assert cloud_rows[0]["source"] == "cloud_counterfeit"
    match = find_upload_violation_title(
        "  跨店售假标题 ",
        controller._upload_violation_title_rows(),
    )
    assert match is not None
    cloud_match = find_upload_violation_title(
        "跨店云标题",
        controller._upload_violation_title_rows(),
    )
    assert cloud_match is not None
    assert cloud_match["source"] == "cloud_counterfeit"
    assert find_upload_violation_title(
        "完全干净的商品",
        controller._upload_violation_title_rows(),
    ) is None


def test_cloud_push_resolves_store_app_id_from_active_store(
    tmp_path: Path,
) -> None:
    client = FakeClient()
    controller = _controller(tmp_path, client)
    store = controller.db.list_stores()[0]
    controller.db.set_setting(
        "violation_library",
        json.dumps(
            [
                {
                    "record_id": "legacy-no-app-id",
                    "title": "旧记录真实售假标题",
                    "reason": "出售假冒商品",
                    "product_id": "legacy-product",
                    "store_id": store.id,
                }
            ],
            ensure_ascii=False,
        ),
    )

    result = controller._push_counterfeit_violation_index("token")

    assert result["saved"] == 1
    assert client.pushed[0]["store_app_id"] == store.app_id


def test_cloud_migration_uses_per_store_dedupe_and_revision() -> None:
    source = (
        Path(__file__).parents[1]
        / "supabase"
        / "migrations"
        / "202611240001_organization_counterfeit_violations.sql"
    ).read_text(encoding="utf-8")
    assert "primary key (organization_id, dedupe_key)" in source
    assert "organization_counterfeit_violations.revision + 1" in source
    assert "reason_category in ('出售假冒商品', '假冒商品', '售假')" in source
    assert "violation_library" not in source
    assert "raw_json" not in source
    assert "buyer" not in source
    assert "phone" not in source


def test_violation_library_excludes_stale_demo_and_logged_out_stores() -> None:
    stores = [
        SimpleNamespace(
            id=7,
            app_id="wx-real",
            name="真实登录店铺",
            web_status="已登录",
            status="正常",
        ),
        SimpleNamespace(
            id=8,
            app_id="wx-logged-out",
            name="离线旧店铺",
            web_status="未登录",
            status="正常",
        ),
        SimpleNamespace(
            id=9,
            app_id="demo-store",
            name="演示店铺",
            web_status="已登录",
            status="正常",
        ),
    ]
    records = [
        {
            "record_id": "real",
            "store_id": 7,
            "store_app_id": "wx-real",
        },
        {
            "record_id": "stale-id",
            "store_id": 99,
            "store_app_id": "wx-removed",
        },
        {
            "record_id": "logged-out",
            "store_id": 8,
            "store_app_id": "wx-logged-out",
        },
        {
            "record_id": "demo",
            "store_id": 9,
            "store_app_id": "demo-store",
        },
    ]

    assert [
        row["record_id"]
        for row in filter_active_violation_records(records, stores)
    ] == ["real"]


def test_counterfeit_title_blocks_even_when_template_switch_is_off() -> None:
    title_records = [
        {
            "title": "新品·真实售假标题 热卖",
            "normalized": "新品·真实售假标题 热卖",
            "reason": "出售假冒商品",
            "source": "cloud_counterfeit",
        }
    ]

    issue = mandatory_upload_violation_issue(
        "【促销】旧款售假标题",
        {
            "strip_title_brackets": True,
            "title_replace_rules": "旧款=真实",
            "title_prefix": "新品·",
            "title_suffix": " 热卖",
            "cloud_violation": False,
        },
        title_records,
    )

    assert issue == "云违规标题过滤命中：出售假冒商品"


def test_cloud_404_is_reported_as_failure_not_success(tmp_path: Path) -> None:
    class MissingCloudClient(FakeClient):
        def upsert_organization_counterfeit_violations(self, _token, _rows):
            raise RuntimeError("HTTP 404: organization counterfeit RPC not found")

    controller = _controller(tmp_path, MissingCloudClient())
    store = controller.db.list_stores()[0]
    controller.db.set_setting(
        "violation_library",
        json.dumps(
            [
                {
                    "record_id": "bad",
                    "title": "真实售假标题",
                    "reason": "出售假冒商品",
                    "product_id": "p1",
                    "store_id": store.id,
                    "store_app_id": store.app_id,
                    "store_name": store.name,
                }
            ],
            ensure_ascii=False,
        ),
    )

    result = controller._refresh_violations({})

    assert "404" in result["cloud_counterfeit"]["error"]
    assert result["cloud_counterfeit"]["saved"] == 0


def test_incomplete_cloud_response_is_reported_as_failure(
    tmp_path: Path,
) -> None:
    class IncompleteCloudClient(FakeClient):
        def upsert_organization_counterfeit_violations(self, _token, _rows):
            return {}

    controller = _controller(tmp_path, IncompleteCloudClient())
    store = controller.db.list_stores()[0]
    controller.db.set_setting(
        "violation_library",
        json.dumps(
            [
                {
                    "record_id": "bad",
                    "title": "真实售假标题",
                    "reason": "出售假冒商品",
                    "product_id": "p1",
                    "store_id": store.id,
                    "store_app_id": store.app_id,
                }
            ],
            ensure_ascii=False,
        ),
    )

    result = controller._push_counterfeit_violation_index("token")

    assert result["saved"] == 0
    assert "不完整" in result["error"]


def test_explicit_demo_or_logged_out_store_is_never_synced(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "cloud-vio.db")
    demo_id = database.add_store("演示店铺", "demo-store", "local")
    database.update_web_status(demo_id, "已登录", "")
    offline_id = database.add_store("离线真实店铺", "wx-offline", "secret")
    database.update_web_status(offline_id, "未登录", "登录失效")
    calls: list[int] = []

    class RecorderService(FakeService):
        def sync_violation_library(self, store):
            calls.append(store.id)
            return SimpleNamespace(count=0)

    controller = NextUiController(
        database,
        product_service=RecorderService(),
    )

    for store_id in (demo_id, offline_id):
        result = controller._refresh_violations({"store_id": store_id})
        assert result["succeeded"] == 0
        assert result["errors"]
        assert result["violations"] == []
    assert calls == []


def test_service_refuses_demo_violation_sync(tmp_path: Path) -> None:
    database = Database(tmp_path / "demo-service.db")
    demo_id = database.add_store(
        "演示店铺",
        "demo-store",
        "local",
    )
    database.update_web_status(demo_id, "已登录", "")
    service = SyncService(database)
    original = database.get_setting("violation_library", "[]")

    with pytest.raises(ValueError, match="真实店铺"):
        service.sync_violation_library(database.get_store(demo_id))

    assert database.get_setting("violation_library", "[]") == original


def test_stale_demo_records_do_not_reach_controller_or_cloud_index(
    tmp_path: Path,
) -> None:
    client = FakeClient()
    controller = _controller(tmp_path, client)
    real_store = controller.db.list_stores()[0]
    demo_id = controller.db.add_store("演示店铺", "demo-store", "local")
    controller.db.update_web_status(demo_id, "已登录", "")
    offline_id = controller.db.add_store(
        "离线旧店铺",
        "wx-old",
        "secret",
    )
    controller.db.update_web_status(offline_id, "未登录", "失效")
    controller.db.set_setting(
        "violation_library",
        json.dumps(
            [
                {
                    "record_id": "real",
                    "title": "真实售假标题",
                    "reason": "出售假冒商品",
                    "product_id": "p-real",
                    "store_id": real_store.id,
                    "store_app_id": real_store.app_id,
                },
                {
                    "record_id": "demo",
                    "title": "演示售假标题",
                    "reason": "出售假冒商品",
                    "store_id": demo_id,
                    "store_app_id": "demo-store",
                },
                {
                    "record_id": "offline",
                    "title": "离线旧售假标题",
                    "reason": "出售假冒商品",
                    "store_id": offline_id,
                    "store_app_id": "wx-old",
                },
            ],
            ensure_ascii=False,
        ),
    )

    rows = controller._violation_rows()
    titles = controller._upload_violation_title_rows()
    cloud = controller._push_counterfeit_violation_index("token")

    assert [row["record_id"] for row in rows] == ["real"]
    assert [row["title"] for row in titles] == ["真实售假标题"]
    assert [row["title"] for row in client.pushed] == ["真实售假标题"]
    assert cloud["saved"] == 1


def test_successful_official_refresh_auto_pushes_counterfeit_title(
    tmp_path: Path,
) -> None:
    client = FakeClient()
    database = Database(tmp_path / "auto-push.db")
    store_id = database.add_store("自动上云店铺", "wx-auto", "secret")
    database.update_web_status(store_id, "已登录", "")
    context = SimpleNamespace(
        tokens=SimpleNamespace(access_token="token"),
        member_role="owner",
        permissions={},
    )

    class OfficialClient:
        def __init__(self, _db, _store):
            pass

        @staticmethod
        def platform_violation_records():
            return [
                {
                    "record_id": "official-1",
                    "product_id": "p1",
                    "title": "官方真实售假标题",
                    "reason": "出售假冒商品",
                    "source": "违规记录",
                    "status": "待处理",
                }
            ]

    controller = NextUiController(
        database,
        product_service=SyncService(database),
        auth_client=client,
        auth_context=context,
    )
    with patch(
        "weimi_manager.service.ShopPrivateClient",
        OfficialClient,
    ):
        result = controller._refresh_violations(
            {"store_id": store_id}
        )

    assert result["succeeded"] == 1
    assert result["errors"] == []
    assert [row["title"] for row in client.pushed] == [
        "官方真实售假标题"
    ]
    assert client.remote[0]["reason_category"] == "出售假冒商品"


def test_failed_official_refresh_does_not_push_stale_titles(
    tmp_path: Path,
) -> None:
    client = FakeClient()
    database = Database(tmp_path / "auto-push.db")
    store_id = database.add_store("读取失败店铺", "wx-fails", "secret")
    database.update_web_status(store_id, "已登录", "")
    database.set_setting(
        "violation_library",
        json.dumps(
            [
                {
                    "record_id": "old",
                    "title": "旧售假标题",
                    "reason": "出售假冒商品",
                    "store_id": store_id,
                    "store_app_id": "wx-fails",
                }
            ],
            ensure_ascii=False,
        ),
    )
    context = SimpleNamespace(
        tokens=SimpleNamespace(access_token="token"),
        member_role="owner",
        permissions={},
    )

    class FailingService:
        @staticmethod
        def sync_violation_library(_store):
            raise RuntimeError("官方违规接口读取失败")

    controller = NextUiController(
        database,
        product_service=FailingService(),
        auth_client=client,
        auth_context=context,
    )
    result = controller._refresh_violations(
        {"store_id": store_id}
    )

    assert result["succeeded"] == 0
    assert result["errors"]
    assert client.pushed == []
    assert (
        result["cloud_counterfeit"]["skipped"]
        == "no_successful_store_sync"
    )
    assert "未推送云端售假库" in result["cloud_counterfeit"]["error"]


def test_start_upload_job_marks_counterfeit_items_failed(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "cloud-vio.db")
    controller = NextUiController(
        database,
        product_service=FakeService(),
    )
    database.set_setting(
        "upload_tasks",
        json.dumps(
            [
                {
                    "id": "task-blocked",
                    "store_id": 1,
                    "status": "pending",
                    "template": {
                        "cloud_violation": False,
                    },
                    "items": [
                        {
                            "item_id": "item-blocked",
                            "source_product_id": "p1",
                            "title": "真实售假标题",
                            "status": "pending",
                            "payload": {},
                        }
                    ],
                }
            ],
            ensure_ascii=False,
        ),
    )
    database.set_setting(
        "cloud_counterfeit_violation_titles",
        json.dumps(
            [
                {
                    "title": "真实售假标题",
                    "normalized": "真实售假标题",
                    "reason": "出售假冒商品",
                    "source": "cloud_counterfeit",
                }
            ],
            ensure_ascii=False,
        ),
    )

    with pytest.raises(ValueError, match="命中售假违规库"):
        controller.dispatch(
            "upload.run.start",
            {"task_ids": ["task-blocked"]},
        )

    task = json.loads(
        database.get_setting("upload_tasks", "[]")
    )[0]
    assert task["items"][0]["status"] == "failed"
    assert "云违规标题过滤命中" in task["items"][0]["message"]


def test_service_product_write_cannot_bypass_counterfeit_title(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "cloud-vio.db")
    store_id = database.add_store("最终门禁店铺", "wx-final", "secret")
    database.update_web_status(store_id, "已登录", "")
    database.set_setting(
        "cloud_counterfeit_violation_titles",
        json.dumps(
            [
                {
                    "title": "真实售假标题",
                    "normalized": "真实售假标题",
                    "reason": "出售假冒商品",
                    "source": "cloud_counterfeit",
                }
            ],
            ensure_ascii=False,
        ),
    )
    store = database.list_stores()[0]
    service = SyncService(database)
    service.platform_write_authorizer = lambda _app_id, _action: None

    class Client:
        called = False

        def add_product(self, _payload):
            self.called = True
            return {"errcode": 0, "product_id": "should-not-exist"}

    client = Client()
    for action in ("add", "update"):
        with pytest.raises(ValueError, match="售假违规库"):
            service.product_write(
                store,
                action,
                {
                    "product_id": "p-service",
                    "title": "真实售假标题",
                    "head_imgs": ["https://example.test/a.jpg"],
                    "skus": [{"sale_price": 100, "stock_num": 1}],
                },
                client=client,
            )

    assert client.called is False


def test_official_violation_read_failure_is_not_silently_empty() -> None:
    client = object.__new__(ShopPrivateClient)
    client._get = mock.Mock(
        side_effect=ShopPrivateError("商家后台请求失败（HTTP 503）")
    )
    client.prewarning_records = mock.Mock(return_value=[])

    with pytest.raises(ShopPrivateError, match="HTTP 503"):
        client.platform_violation_records()


def test_official_violation_missing_list_is_not_treated_as_empty() -> None:
    client = object.__new__(ShopPrivateClient)
    client._get = mock.Mock(return_value={"data": {}})
    client.prewarning_records = mock.Mock(return_value=[])

    with pytest.raises(ShopPrivateError, match="有效列表"):
        client.platform_violation_records()


def test_official_read_failure_preserves_last_successful_records(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = Database(tmp_path / "preserve-violations.db")
    store_id = database.add_store(
        "保留上次成功店铺",
        "wx-preserve",
        "secret",
    )
    database.update_web_status(store_id, "已登录", "")
    database.set_setting(
        "violation_library",
        json.dumps(
            [
                {
                    "record_id": "last-good",
                    "store_id": store_id,
                    "store_app_id": "wx-preserve",
                    "title": "上次成功违规商品",
                    "reason": "出售假冒商品",
                }
            ],
            ensure_ascii=False,
        ),
    )
    database.set_setting(
        "upload_violation_titles",
        json.dumps(
            [{"title": "上次成功违规商品", "reason": "出售假冒商品"}],
            ensure_ascii=False,
        ),
    )
    original_records = database.get_setting("violation_library", "[]")
    original_titles = database.get_setting(
        "upload_violation_titles",
        "[]",
    )

    class FailingClient:
        def __init__(self, _database, _store):
            pass

        @staticmethod
        def platform_violation_records():
            raise ShopPrivateError("商家后台请求失败（HTTP 503）")

    monkeypatch.setattr(
        "weimi_manager.service.ShopPrivateClient",
        FailingClient,
    )
    with pytest.raises(ShopPrivateError, match="HTTP 503"):
        SyncService(database).sync_violation_library(
            database.get_store(store_id)
        )

    assert database.get_setting("violation_library", "[]") == original_records
    assert (
        database.get_setting("upload_violation_titles", "[]")
        == original_titles
    )


def test_desktop_upload_task_creation_blocks_counterfeit_title(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = Database(tmp_path / "desktop-upload.db")
    store_id = database.add_store(
        "真实店铺",
        "wx-desktop-upload",
        "secret",
    )
    database.update_web_status(store_id, "已登录", "")
    database.set_setting(
        "cloud_counterfeit_violation_titles",
        json.dumps(
            [
                {
                    "title": "真实售假标题",
                    "normalized": "真实售假标题",
                    "reason": "出售假冒商品",
                    "source": "cloud_counterfeit",
                }
            ],
            ensure_ascii=False,
        ),
    )
    page = object.__new__(UploadPage)
    page.db = database
    page._batch = [
        {
            "batch_id": "counterfeit-batch",
            "title": "真实售假标题",
            "price": 100,
            "stock": 5,
            "source_product_id": "source-1",
            "payload": {},
        }
    ]
    page._selected_batch_ids = {"counterfeit-batch"}
    page._tasks = []
    page._sync_batch_selection = mock.Mock()
    page._chosen_template = mock.Mock(
        return_value={
            "name": "明确关闭云开关",
            "cloud_violation": False,
        }
    )
    page._choose_stores = mock.Mock(
        return_value=[database.get_store(store_id)]
    )
    page._save_tasks = mock.Mock()
    page._render_tasks = mock.Mock()
    page._show_view = mock.Mock()
    page.assignment_var = mock.Mock()
    page.assignment_var.get.return_value = "平均分配"
    page.window = SimpleNamespace(set_status=mock.Mock())
    warning = mock.Mock()
    monkeypatch.setattr(
        "weimi_manager.ui.upload_page.messagebox.showwarning",
        warning,
    )

    page._create_tasks()

    assert page._tasks == []
    page._save_tasks.assert_not_called()
    assert "售假" in warning.call_args.args[1]
