from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from weimi_manager.db import Database
from weimi_manager.upload_violations import (
    build_cloud_counterfeit_rows,
    find_upload_violation_title,
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
    database.add_store("本地全量店铺", "wx-vio", "secret")
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
