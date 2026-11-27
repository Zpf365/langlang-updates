from __future__ import annotations

import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from weimi_manager.db import Database
from weimi_manager.models import Product, SyncResult
from weimi_manager.service import SyncService
from weimi_manager.webui.controller import NextUiController


def _wait_for_status(
    controller: NextUiController,
    command: str,
    job_id: str,
) -> dict:
    for _ in range(500):
        status = controller.dispatch(command, {"job_id": job_id})
        if status["status"] in {
            "completed",
            "partial",
            "failed",
            "verification_pending",
            "cancelled",
        }:
            return status
        time.sleep(0.01)
    raise AssertionError(f"任务 {job_id} 未进入终态")


def test_product_batch_cancel_stops_at_safe_boundary_and_survives_reopen(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "product-batch-cancel.db")
    store_id = database.add_store("取消店铺", "wx-product-batch-cancel", "secret")

    class BlockingListingService:
        def __init__(self) -> None:
            self.platform_write_authorizer = None
            self.calls: list[tuple[str, ...]] = []
            self.first_started = threading.Event()
            self.release_first = threading.Event()

        def set_products_listing(
            self,
            _store,
            product_ids,
            listed,
            *,
            progress=None,
            cancel_event=None,
            **_kwargs,
        ):
            ids = tuple(product_ids)
            self.calls.append(ids)
            if len(self.calls) == 1:
                self.first_started.set()
                assert self.release_first.wait(3)
            if progress:
                progress(f"正在批量{'上架' if listed else '下架'} 1/1", 1, 1)
            return SyncResult(
                "product_listing",
                len(ids),
                "已处理",
                success=len(ids),
            )

    service = BlockingListingService()
    controller = NextUiController(database, product_service=service)
    started = controller.dispatch(
        "products.batch.start",
        {
            "action": "delisting",
            "items": [
                {"store_id": store_id, "product_id": "product-1"},
                {"store_id": store_id, "product_id": "product-2"},
            ],
        },
    )
    assert service.first_started.wait(3)

    cancelling = controller.dispatch(
        "products.batch.cancel",
        {"job_id": started["job_id"]},
    )
    assert cancelling["status"] == "cancelling"
    service.release_first.set()

    final = _wait_for_status(
        controller,
        "products.batch.status",
        started["job_id"],
    )
    assert final["status"] == "cancelled"
    assert service.calls == [("product-1",)]
    assert final["counts"]["cancelled"] == 1
    assert {
        row["product_id"]: row["status"]
        for row in final["items"]
    } == {
        "product-1": "success",
        "product-2": "cancelled",
    }

    reopened = NextUiController(database, product_service=service)
    restored = reopened.dispatch(
        "products.batch.status",
        {"job_id": started["job_id"]},
    )
    assert restored["status"] == "cancelled"
    assert restored["counts"]["cancelled"] == 1
    listed = reopened.dispatch("products.tasks.list", {})["tasks"]
    assert listed[0]["job_id"] == started["job_id"]
    assert listed[0]["status"] == "cancelled"


def test_product_refresh_cancel_finishes_inflight_tab_and_skips_later_tabs(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "product-refresh-cancel.db")
    store_id = database.add_store("刷新取消店铺", "wx-refresh-cancel", "secret")

    class BlockingRefreshService:
        def __init__(self) -> None:
            self.platform_write_authorizer = None
            self.tabs: list[str] = []
            self.started = threading.Event()

        def refresh_product_tab(
            self,
            _store,
            tab_key,
            *,
            progress=None,
            cancel_event=None,
        ):
            self.tabs.append(tab_key)
            self.started.set()
            assert cancel_event is not None
            assert cancel_event.wait(3)
            return SyncResult(
                "product_query",
                0,
                "已取消商品查询",
                cancelled=True,
            )

    service = BlockingRefreshService()
    controller = NextUiController(database, product_service=service)
    started = controller.dispatch(
        "products.refresh.start",
        {
            "store_id": store_id,
            "tab_keys": ["sales", "recycle"],
        },
    )
    assert service.started.wait(3)

    cancelling = controller.dispatch(
        "products.refresh.cancel",
        {"job_id": started["job_id"]},
    )
    assert cancelling["status"] == "cancelling"

    final = _wait_for_status(
        controller,
        "products.refresh.status",
        started["job_id"],
    )
    assert final["status"] == "cancelled"
    assert service.tabs == ["sales"]

    reopened = NextUiController(database, product_service=service)
    restored = reopened.dispatch(
        "products.refresh.status",
        {"job_id": started["job_id"]},
    )
    assert restored["status"] == "cancelled"


def _seed_product(
    database: Database,
    store_id: int,
    product_id: str,
    status: int = 5,
) -> None:
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
            1,
            "",
            "",
            "",
            {},
        )
    )


def test_batch_delete_uses_platform_chunks_and_one_verification_per_chunk(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = Database(tmp_path / "product-batch-delete.db")
    store_id = database.add_store("批删店铺", "wx-batch-delete", "secret")
    ids = [f"product-{index:02d}" for index in range(45)]
    for product_id in ids:
        _seed_product(database, store_id, product_id)

    private_calls: list[tuple[tuple[str, ...], str]] = []
    scans: list[str] = []
    recycled: set[str] = set()

    class PrivateClient:
        def __init__(self, _database, _store):
            pass

        def batch_product_action(self, product_ids, way):
            private_calls.append((tuple(product_ids), way))
            recycled.update(product_ids)
            return {"success": True}

        def scan_products(self, tab_key, **_kwargs):
            scans.append(tab_key)
            return [
                SimpleNamespace(product_id=product_id)
                for product_id in sorted(recycled)
            ]

    monkeypatch.setattr(
        "weimi_manager.service.ShopPrivateClient",
        PrivateClient,
    )
    service = SyncService(database)
    service._require_platform_write = lambda *_args, **_kwargs: None

    result = service.batch_delete_products(
        database.get_store(store_id),
        ids,
    )

    assert [len(product_ids) for product_ids, _way in private_calls] == [
        20,
        20,
        5,
    ]
    assert len(scans) == 3
    assert result["cancelled"] is False
    assert result["counts"]["success"] == 45
    assert {row["product_id"] for row in result["items"]} == set(ids)


def test_batch_delete_does_not_report_unknown_remote_result_as_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = Database(tmp_path / "product-batch-unknown.db")
    store_id = database.add_store("未知店铺", "wx-batch-unknown", "secret")
    ids = [f"unknown-{index}" for index in range(20)]
    for product_id in ids:
        _seed_product(database, store_id, product_id)

    class PrivateClient:
        def __init__(self, _database, _store):
            pass

        def batch_product_action(self, _product_ids, _way):
            raise TimeoutError("network timeout after submit")

        def scan_products(self, _tab_key, **_kwargs):
            return []

    monkeypatch.setattr(
        "weimi_manager.service.ShopPrivateClient",
        PrivateClient,
    )
    service = SyncService(database)
    service._require_platform_write = lambda *_args, **_kwargs: None

    result = service.batch_delete_products(
        database.get_store(store_id),
        ids,
    )

    assert result["counts"]["success"] == 0
    assert result["counts"]["pending"] == 20
    assert all(row["status"] == "pending" for row in result["items"])
    assert all(
        database.get_product(store_id, product_id) is not None
        for product_id in ids
    )


def test_batch_delete_cancel_finishes_current_chunk_then_stops_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = Database(tmp_path / "product-batch-delete-cancel.db")
    store_id = database.add_store("批删取消店铺", "wx-batch-delete-cancel", "secret")
    ids = [f"cancel-{index}" for index in range(45)]
    for product_id in ids:
        _seed_product(database, store_id, product_id)
    cancel_event = threading.Event()
    calls: list[tuple[str, ...]] = []
    recycled: set[str] = set()

    class PrivateClient:
        def __init__(self, _database, _store):
            pass

        def batch_product_action(self, product_ids, _way):
            calls.append(tuple(product_ids))
            recycled.update(product_ids)
            cancel_event.set()
            return {"success": True}

        def scan_products(self, _tab_key, **_kwargs):
            return [
                SimpleNamespace(product_id=product_id)
                for product_id in sorted(recycled)
            ]

    monkeypatch.setattr(
        "weimi_manager.service.ShopPrivateClient",
        PrivateClient,
    )
    service = SyncService(database)
    service._require_platform_write = lambda *_args, **_kwargs: None

    result = service.batch_delete_products(
        database.get_store(store_id),
        ids,
        cancel_event=cancel_event,
    )

    assert len(calls) == 1
    assert len(calls[0]) == 20
    assert result["cancelled"] is True
    assert result["counts"]["success"] == 20
    assert result["counts"]["cancelled"] == 25
    assert result["counts"]["pending"] == 0


def test_hard_delete_moves_to_recycle_then_permanently_deletes_in_one_task(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = Database(tmp_path / "product-hard-delete.db")
    store_id = database.add_store("两阶段店铺", "wx-hard-delete", "secret")
    _seed_product(database, store_id, "hard-product")
    calls: list[tuple[tuple[str, ...], str]] = []
    recycled: set[str] = set()
    scans = 0

    class PrivateClient:
        def __init__(self, _database, _store):
            pass

        def batch_product_action(self, product_ids, way):
            calls.append((tuple(product_ids), way))
            if way == "recycle":
                recycled.update(product_ids)
            elif way == "real":
                recycled.difference_update(product_ids)
            return {"success": True}

        def scan_products(self, _tab_key, **_kwargs):
            nonlocal scans
            scans += 1
            return [
                SimpleNamespace(product_id=product_id)
                for product_id in sorted(recycled)
            ]

    monkeypatch.setattr(
        "weimi_manager.service.ShopPrivateClient",
        PrivateClient,
    )
    service = SyncService(database)
    service._require_platform_write = lambda *_args, **_kwargs: None

    result = service.batch_delete_products(
        database.get_store(store_id),
        ["hard-product"],
        hard_delete=True,
    )

    assert calls == [
        (("hard-product",), "recycle"),
        (("hard-product",), "real"),
    ]
    assert scans == 2
    assert result["counts"]["success"] == 1
    assert database.get_product(store_id, "hard-product") is None


def test_hard_delete_cancel_after_recycle_does_not_start_real_delete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = Database(tmp_path / "product-hard-delete-cancel.db")
    store_id = database.add_store("两阶段取消店铺", "wx-hard-cancel", "secret")
    _seed_product(database, store_id, "hard-cancel-product")
    cancel_event = threading.Event()
    ways: list[str] = []
    recycled: set[str] = set()

    class PrivateClient:
        def __init__(self, _database, _store):
            pass

        def batch_product_action(self, product_ids, way):
            ways.append(way)
            recycled.update(product_ids)
            cancel_event.set()
            return {"success": True}

        def scan_products(self, _tab_key, **_kwargs):
            return [
                SimpleNamespace(product_id=product_id)
                for product_id in sorted(recycled)
            ]

    monkeypatch.setattr(
        "weimi_manager.service.ShopPrivateClient",
        PrivateClient,
    )
    service = SyncService(database)
    service._require_platform_write = lambda *_args, **_kwargs: None

    result = service.batch_delete_products(
        database.get_store(store_id),
        ["hard-cancel-product"],
        hard_delete=True,
        cancel_event=cancel_event,
    )

    assert ways == ["recycle"]
    assert result["cancelled"] is True
    assert result["counts"]["pending"] == 1
    assert result["items"][0]["status"] == "pending"
    assert "未彻底删除" in result["items"][0]["message"]


def test_batch_delete_without_private_capability_degrades_to_delisting(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "product-batch-fallback.db")
    store_id = database.add_store("降级店铺", "wx-batch-fallback", "secret")
    _seed_product(database, store_id, "active-fallback")
    service = SyncService(database)
    service._require_platform_write = lambda *_args, **_kwargs: None
    listing_calls: list[tuple[tuple[str, ...], bool]] = []

    def set_products_listing(
        _store,
        product_ids,
        listed,
        **_kwargs,
    ):
        listing_calls.append((tuple(product_ids), listed))
        database.mark_product_status(
            store_id,
            "active-fallback",
            11,
            "已下架",
        )
        return SyncResult(
            "product_delisting",
            1,
            "已安全下架",
            success=1,
        )

    service.set_products_listing = set_products_listing
    result = service.batch_delete_products(
        database.get_store(store_id),
        ["active-fallback"],
        use_private_batch=False,
    )

    assert listing_calls == [(("active-fallback",), False)]
    assert result["items"][0]["status"] == "failed"
    assert result["items"][0]["fallback_action"] == "商品下架"
    assert "商品未删除" in result["items"][0]["message"]
    assert database.get_product(store_id, "active-fallback") is not None


def test_controller_splits_batch_delete_by_store_before_platform_write(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "product-cross-store.db")
    first_id = database.add_store("店铺一", "wx-cross-one", "secret")
    second_id = database.add_store("店铺二", "wx-cross-two", "secret")
    _seed_product(database, first_id, "shared-product", 6)
    _seed_product(database, second_id, "shared-product", 6)

    class GroupingService:
        def __init__(self) -> None:
            self.platform_write_authorizer = None
            self.calls: list[tuple[int, tuple[str, ...]]] = []
            self.hard_modes: list[bool] = []

        def batch_delete_products(
            self,
            store,
            product_ids,
            *,
            cancel_event=None,
            progress=None,
            use_private_batch=True,
            hard_delete=False,
            **_kwargs,
        ):
            ids = tuple(product_ids)
            self.calls.append((store.id, ids))
            self.hard_modes.append(bool(hard_delete))
            if progress:
                progress(f"正在批量删除 1/{len(ids)}", 1, len(ids))
            return {
                "cancelled": False,
                "items": [
                    {
                        "store_id": store.id,
                        "product_id": product_id,
                        "status": "success",
                        "message": "平台已确认彻底删除",
                    }
                    for product_id in ids
                ],
                "counts": {
                    "success": len(ids),
                    "failed": 0,
                    "cancelled": 0,
                    "pending": 0,
                },
            }

    service = GroupingService()
    controller = NextUiController(database, product_service=service)
    started = controller.dispatch(
        "products.batch.start",
        {
            "action": "delete",
            "items": [
                {"store_id": first_id, "product_id": "shared-product"},
                {"store_id": second_id, "product_id": "shared-product"},
            ],
        },
    )
    final = _wait_for_status(
        controller,
        "products.batch.status",
        started["job_id"],
    )

    assert final["status"] == "completed"
    assert service.calls == [
        (first_id, ("shared-product",)),
        (second_id, ("shared-product",)),
    ]
    assert service.hard_modes == [True, True]

    recycle_started = controller.dispatch(
        "products.batch.start",
        {
            "action": "recycle",
            "items": [
                {"store_id": first_id, "product_id": "shared-product"},
                {"store_id": second_id, "product_id": "shared-product"},
            ],
        },
    )
    recycle_final = _wait_for_status(
        controller,
        "products.batch.status",
        recycle_started["job_id"],
    )
    assert recycle_final["status"] == "completed"
    assert service.hard_modes == [True, True, False, False]


def test_failed_store_does_not_prevent_next_store_batch_and_stays_pending(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "product-cross-store-failure.db")
    first_id = database.add_store("失败店铺", "wx-cross-fail-one", "secret")
    second_id = database.add_store("继续店铺", "wx-cross-fail-two", "secret")
    _seed_product(database, first_id, "first-product", 6)
    _seed_product(database, second_id, "second-product", 6)

    class PartialService:
        def __init__(self) -> None:
            self.platform_write_authorizer = None
            self.calls: list[int] = []

        def batch_delete_products(
            self,
            store,
            product_ids,
            **_kwargs,
        ):
            self.calls.append(store.id)
            if store.id == first_id:
                raise TimeoutError("first store timeout")
            ids = list(product_ids)
            return {
                "cancelled": False,
                "items": [
                    {
                        "store_id": store.id,
                        "product_id": product_id,
                        "status": "success",
                        "message": "平台已确认彻底删除",
                    }
                    for product_id in ids
                ],
                "counts": {
                    "success": len(ids),
                    "failed": 0,
                    "cancelled": 0,
                    "pending": 0,
                },
            }

    service = PartialService()
    controller = NextUiController(database, product_service=service)
    started = controller.dispatch(
        "products.batch.start",
        {
            "action": "delete",
            "items": [
                {"store_id": first_id, "product_id": "first-product"},
                {"store_id": second_id, "product_id": "second-product"},
            ],
        },
    )
    final = _wait_for_status(
        controller,
        "products.batch.status",
        started["job_id"],
    )

    assert service.calls == [first_id, second_id]
    assert final["status"] == "verification_pending"
    assert {
        row["product_id"]: row["status"]
        for row in final["items"]
    } == {
        "first-product": "pending",
        "second-product": "success",
    }
