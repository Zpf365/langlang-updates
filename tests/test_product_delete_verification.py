from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from weimi_manager.db import Database
from weimi_manager.models import Product
from weimi_manager.service import (
    ProductDeleteUncertainError,
    SyncService,
)


def _seed_product(database: Database, store_id: int, product_id: str, status: int):
    store = database.get_store(store_id)
    database.upsert_product(
        Product(
            store_id,
            store.name,
            product_id,
            f"商品 {product_id}",
            status,
            "回收站" if status == 6 else "销售中",
            0,
            100,
            1,
            2,
            f"SPU-{product_id}",
            f"OUT-{product_id}",
            "",
            {},
        )
    )


class _PrivateClient:
    instances = []
    recycle_ids: dict[int, list[str]] = {}
    batch_error: Exception | None = None

    def __init__(self, _database, store):
        self.store = store
        self.calls = []
        self.__class__.instances.append(self)

    def batch_product_action(self, product_ids, way):
        self.calls.append(("batch", tuple(product_ids), way))
        if self.__class__.batch_error is not None:
            raise self.__class__.batch_error
        return {"success": True}

    def scan_products(self, tab_key, **_kwargs):
        self.calls.append(("scan", tab_key))
        assert tab_key == "recycle"
        return [
            SimpleNamespace(product_id=product_id)
            for product_id in self.__class__.recycle_ids.get(
                self.store.id,
                [],
            )
        ]


class _OfficialClient:
    def __init__(self, recycle_ids=None, delete_error=None):
        self.recycle_ids = list(recycle_ids or [])
        self.delete_error = delete_error
        self.delete_calls = []

    def delete_product(self, product_id):
        self.delete_calls.append(product_id)
        if self.delete_error is not None:
            raise self.delete_error
        return {"errcode": 0}

    def fetch_product_ids(self, status=None):
        assert status == 6
        return list(self.recycle_ids)


def _service(database: Database) -> SyncService:
    service = SyncService(database)
    service._require_platform_write = lambda *_args, **_kwargs: None
    return service


def test_recycle_product_with_app_secret_uses_private_real_delete(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database = Database(tmp_path / "delete-real.db")
    store_id = database.add_store("回收站店铺", "wx-real-delete", "secret")
    _seed_product(database, store_id, "recycle-1", 6)
    _PrivateClient.instances = []
    _PrivateClient.recycle_ids = {store_id: []}
    _PrivateClient.batch_error = None
    monkeypatch.setattr(
        "weimi_manager.service.ShopPrivateClient",
        _PrivateClient,
    )
    official = _OfficialClient(recycle_ids=["recycle-1"])

    result = _service(database).product_write(
        database.get_store(store_id),
        "delete",
        product_id="recycle-1",
        client=official,
    )

    assert official.delete_calls == []
    assert result["delete_mode"] == "real"
    assert result["remote_verified"] is True
    assert _PrivateClient.instances[0].calls[0] == (
        "batch",
        ("recycle-1",),
        "real",
    )
    assert database.get_product(store_id, "recycle-1") is None


def test_delete_success_with_product_still_in_recycle_is_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database = Database(tmp_path / "delete-still-there.db")
    store_id = database.add_store("仍在回收站", "wx-still-there", "secret")
    _seed_product(database, store_id, "recycle-still", 6)
    _PrivateClient.instances = []
    _PrivateClient.recycle_ids = {store_id: ["recycle-still"]}
    _PrivateClient.batch_error = None
    monkeypatch.setattr(
        "weimi_manager.service.ShopPrivateClient",
        _PrivateClient,
    )

    with pytest.raises(RuntimeError, match="仍存在"):
        _service(database).product_write(
            database.get_store(store_id),
            "delete",
            product_id="recycle-still",
        )

    assert database.get_product(store_id, "recycle-still") is not None


def test_non_recycle_product_uses_official_recycle_path(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database = Database(tmp_path / "delete-active.db")
    store_id = database.add_store("销售中店铺", "wx-active-delete", "secret")
    _seed_product(database, store_id, "active-1", 5)
    _PrivateClient.instances = []
    _PrivateClient.recycle_ids = {store_id: ["active-1"]}
    _PrivateClient.batch_error = None
    monkeypatch.setattr(
        "weimi_manager.service.ShopPrivateClient",
        _PrivateClient,
    )
    official = _OfficialClient(recycle_ids=["active-1"])

    result = _service(database).product_write(
        database.get_store(store_id),
        "delete",
        product_id="active-1",
        client=official,
    )

    assert official.delete_calls == ["active-1"]
    assert result["delete_mode"] == "recycle"
    assert not any(
        call[0] == "batch" and call[2] == "real"
        for call in _PrivateClient.instances[0].calls
    )


def test_official_success_without_remote_recycle_confirmation_is_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database = Database(tmp_path / "delete-unconfirmed.db")
    store_id = database.add_store("未确认店铺", "wx-delete-unconfirmed", "secret")
    _seed_product(database, store_id, "active-unconfirmed", 5)
    _PrivateClient.instances = []
    _PrivateClient.recycle_ids = {store_id: []}
    _PrivateClient.batch_error = None
    monkeypatch.setattr(
        "weimi_manager.service.ShopPrivateClient",
        _PrivateClient,
    )
    official = _OfficialClient(recycle_ids=[])

    with pytest.raises(RuntimeError, match="未找到"):
        _service(database).product_write(
            database.get_store(store_id),
            "delete",
            product_id="active-unconfirmed",
            client=official,
        )

    assert official.delete_calls == ["active-unconfirmed"]
    assert database.get_product(store_id, "active-unconfirmed") is not None


def test_delete_timeout_is_kept_pending_and_does_not_remove_cache(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database = Database(tmp_path / "delete-timeout.db")
    store_id = database.add_store("超时店铺", "wx-delete-timeout", "secret")
    _seed_product(database, store_id, "recycle-timeout", 6)
    _PrivateClient.instances = []
    _PrivateClient.recycle_ids = {store_id: ["recycle-timeout"]}
    _PrivateClient.batch_error = TimeoutError("network timeout")
    monkeypatch.setattr(
        "weimi_manager.service.ShopPrivateClient",
        _PrivateClient,
    )

    with pytest.raises(ProductDeleteUncertainError, match="未确认"):
        _service(database).product_write(
            database.get_store(store_id),
            "delete",
            product_id="recycle-timeout",
        )

    assert database.get_product(store_id, "recycle-timeout") is not None
    _PrivateClient.batch_error = None


def test_delete_uses_each_rows_store_scope(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database = Database(tmp_path / "delete-two-stores.db")
    first_id = database.add_store("店铺一", "wx-delete-one", "secret")
    second_id = database.add_store("店铺二", "wx-delete-two", "secret")
    _seed_product(database, first_id, "shared-id", 6)
    _seed_product(database, second_id, "shared-id", 6)
    _PrivateClient.instances = []
    _PrivateClient.recycle_ids = {first_id: [], second_id: ["shared-id"]}
    _PrivateClient.batch_error = None
    monkeypatch.setattr(
        "weimi_manager.service.ShopPrivateClient",
        _PrivateClient,
    )
    service = _service(database)

    service.product_write(
        database.get_store(first_id),
        "delete",
        product_id="shared-id",
    )
    with pytest.raises(RuntimeError, match="仍存在"):
        service.product_write(
            database.get_store(second_id),
            "delete",
            product_id="shared-id",
        )

    assert database.get_product(first_id, "shared-id") is None
    assert database.get_product(second_id, "shared-id") is not None
    assert {item.store.id for item in _PrivateClient.instances} == {
        first_id,
        second_id,
    }


def test_mixed_batch_routes_each_product_by_current_status(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database = Database(tmp_path / "delete-mixed.db")
    store_id = database.add_store("混合店铺", "wx-delete-mixed", "secret")
    _seed_product(database, store_id, "mixed-recycle", 6)
    _seed_product(database, store_id, "mixed-active", 5)
    recycle_ids = {"mixed-recycle"}
    private_calls = []

    class MixedPrivate:
        def __init__(self, _database, _store):
            pass

        def batch_product_action(self, product_ids, way):
            private_calls.append((tuple(product_ids), way))
            if way == "real":
                recycle_ids.difference_update(product_ids)
            return {"success": True}

        def scan_products(self, _tab_key, **_kwargs):
            return [
                SimpleNamespace(product_id=product_id)
                for product_id in recycle_ids
            ]

    class MixedOfficial:
        def delete_product(self, product_id):
            recycle_ids.add(product_id)
            return {"errcode": 0}

        def fetch_product_ids(self, _status=None):
            return list(recycle_ids)

    monkeypatch.setattr(
        "weimi_manager.service.ShopPrivateClient",
        MixedPrivate,
    )
    service = _service(database)
    official = MixedOfficial()

    service.product_write(
        database.get_store(store_id),
        "delete",
        product_id="mixed-recycle",
        client=official,
    )
    service.product_write(
        database.get_store(store_id),
        "delete",
        product_id="mixed-active",
        client=official,
    )

    assert private_calls == [(("mixed-recycle",), "real")]
    assert database.get_product(store_id, "mixed-recycle") is None
    assert database.get_product(store_id, "mixed-active") is None
    assert recycle_ids == {"mixed-active"}
