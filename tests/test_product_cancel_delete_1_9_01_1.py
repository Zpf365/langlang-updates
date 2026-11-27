from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

from weimi_manager.db import Database
from weimi_manager.models import Product, SyncResult
from weimi_manager.service import SyncService
from weimi_manager.shop_private import (
    ShopPrivateClient,
    ShopPrivateError,
    ShopPrivateWriteUncertainError,
)
from weimi_manager.wechat_api import (
    WechatNetworkError,
    WechatTimeoutError,
)
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


def test_cached_recycle_product_missing_on_pre_read_never_calls_real_delete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = Database(tmp_path / "product-pre-read-missing.db")
    store_id = database.add_store("前置回读店铺", "wx-pre-read-missing", "secret")
    _seed_product(database, store_id, "cached-recycle", 6)
    calls: list[tuple[tuple[str, ...], str]] = []
    scans = 0

    class PrivateClient:
        def __init__(self, _database, _store):
            pass

        def batch_product_action(self, product_ids, way):
            calls.append((tuple(product_ids), way))
            return {"success": True}

        def scan_products(self, _tab_key, **_kwargs):
            nonlocal scans
            scans += 1
            return []

    monkeypatch.setattr(
        "weimi_manager.service.ShopPrivateClient",
        PrivateClient,
    )
    service = SyncService(database)
    service._require_platform_write = lambda *_args, **_kwargs: None

    result = service.batch_delete_products(
        database.get_store(store_id),
        ["cached-recycle"],
        hard_delete=True,
    )

    assert scans == 1
    assert calls == []
    assert result["counts"]["pending"] == 1
    assert result["items"][0]["status"] == "pending"
    assert database.get_product(store_id, "cached-recycle") is not None


def test_cached_recycle_product_pre_read_failure_never_calls_real_delete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = Database(tmp_path / "product-pre-read-failure.db")
    store_id = database.add_store("前置失败店铺", "wx-pre-read-failure", "secret")
    _seed_product(database, store_id, "cached-recycle-failure", 6)
    calls: list[tuple[tuple[str, ...], str]] = []

    class PrivateClient:
        def __init__(self, _database, _store):
            pass

        def batch_product_action(self, product_ids, way):
            calls.append((tuple(product_ids), way))
            return {"success": True}

        def scan_products(self, _tab_key, **_kwargs):
            raise ShopPrivateError("回收站读取失败")

    monkeypatch.setattr(
        "weimi_manager.service.ShopPrivateClient",
        PrivateClient,
    )
    service = SyncService(database)
    service._require_platform_write = lambda *_args, **_kwargs: None

    result = service.batch_delete_products(
        database.get_store(store_id),
        ["cached-recycle-failure"],
        hard_delete=True,
    )

    assert calls == []
    assert result["counts"]["pending"] == 1
    assert result["items"][0]["status"] == "pending"
    assert database.get_product(store_id, "cached-recycle-failure") is not None


def test_listing_transport_unknown_is_pending_but_explicit_error_is_failed(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "product-listing-classification.db")
    store_id = database.add_store("分类店铺", "wx-listing-classification", "secret")

    class Client:
        def set_product_listing(self, product_id, _listed):
            if product_id == "transport-unknown":
                raise WechatTimeoutError("response timeout after submit")
            raise RuntimeError("平台明确拒绝")

    service = SyncService(database)
    service._require_platform_write = lambda *_args, **_kwargs: None
    service.client_for = lambda _store: Client()

    result = service.set_products_listing(
        database.get_store(store_id),
        ["transport-unknown", "explicit-error"],
        True,
        workers=1,
    )

    assert result.success == 0
    assert result.failed == 1
    assert result.pending == 1
    assert result.verification_pending is True


def test_cancel_audit_transport_unknown_is_marked_verification_pending(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "product-audit-classification.db")
    store_id = database.add_store("审核分类店铺", "wx-audit-classification", "secret")

    class Client:
        def cancel_product_audit(self, _product_id):
            raise WechatNetworkError("connection reset after submit")

    service = SyncService(database)
    service._require_platform_write = lambda *_args, **_kwargs: None

    with pytest.raises(Exception) as raised:
        service.product_write(
            database.get_store(store_id),
            "cancel_audit",
            product_id="audit-product",
            client=Client(),
        )

    assert getattr(raised.value, "verification_pending", False) is True
    assert "待核实" in str(raised.value)


def test_private_product_batch_transport_failure_is_uncertain(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "private-transport-uncertain.db")
    store_id = database.add_store("私有传输店铺", "wx-private-uncertain", "secret")
    database.set_web_session(
        store_id,
        {
            "cookies": [
                {
                    "name": "sessionid",
                    "value": "test-session",
                    "domain": ".weixin.qq.com",
                    "path": "/",
                    "secure": True,
                }
            ]
        },
        "已登录",
        "测试会话",
    )
    client = ShopPrivateClient(
        database,
        database.get_store(store_id),
    )
    client._sleep = lambda _seconds: None

    def fail_post(*_args, **_kwargs):
        raise requests.ConnectionError("connection reset after submit")

    client.session.post = fail_post

    with pytest.raises(ShopPrivateWriteUncertainError) as raised:
        client.batch_product_action(["private-product"], "recycle")

    assert getattr(raised.value, "verification_pending", False) is True


def test_pending_batch_item_prioritizes_verification_pending_task(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "product-pending-task.db")
    store_id = database.add_store("待核实任务店铺", "wx-pending-task", "secret")

    class PendingService:
        def __init__(self) -> None:
            self.platform_write_authorizer = None

        def set_products_listing(self, _store, product_ids, _listed, **_kwargs):
            return SyncResult(
                "product_listing",
                len(product_ids),
                "请求已发出但结果未知",
                success=0,
                failed=0,
                pending=len(product_ids),
                errors=["请求已发出但结果未知"],
            )

    controller = NextUiController(database, product_service=PendingService())
    started = controller.dispatch(
        "products.batch.start",
        {
            "action": "listing",
            "items": [{"store_id": store_id, "product_id": "pending-product"}],
        },
    )
    final = _wait_for_status(
        controller,
        "products.batch.status",
        started["job_id"],
    )

    assert final["status"] == "verification_pending"
    assert final["items"][0]["status"] == "pending"


def test_restart_recovers_running_product_task_as_verification_pending(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "product-restart-task.db")
    job_id = "restart-batch"
    database.set_setting(
        "webui_product_task_jobs",
        json.dumps(
            {
                "tasks": {
                    job_id: {
                        "job_id": job_id,
                        "kind": "batch",
                        "action": "delete",
                        "label": "批量彻底删除",
                        "account_id": "",
                        "generation": 0,
                        "status": "running",
                        "store_id": 1,
                        "store_ids": [1],
                        "current": 1,
                        "total": 3,
                        "success": 9,
                        "failed": 8,
                        "pending": 0,
                        "cancelled_count": 7,
                        "counts": {
                            "success": 9,
                            "failed": 8,
                            "cancelled": 7,
                            "pending": 0,
                        },
                        "items": [
                            {
                                "store_id": 1,
                                "product_id": "done",
                                "status": "success",
                                "message": "完成",
                            },
                            {
                                "store_id": 1,
                                "product_id": "running",
                                "status": "running",
                                "message": "处理中",
                            },
                            {
                                "store_id": 1,
                                "product_id": "queued",
                                "status": "queued",
                                "message": "等待执行",
                            },
                        ],
                    }
                }
            },
            ensure_ascii=False,
        ),
    )
    controller = NextUiController(database)

    restored = controller.dispatch(
        "products.batch.status",
        {"job_id": job_id},
    )

    assert restored["status"] == "verification_pending"
    assert restored["counts"] == {
        "success": 1,
        "failed": 0,
        "cancelled": 0,
        "pending": 2,
    }
    assert restored["success"] == 1
    assert restored["failed"] == 0
    assert restored["pending"] == 2
    assert restored["cancelled_count"] == 0
    assert restored["current"] == 3
    assert restored["total"] == 3
    assert [row["status"] for row in restored["items"]] == [
        "success",
        "pending",
        "pending",
    ]


def test_restart_recovers_running_refresh_as_verification_pending(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "product-restart-refresh.db")
    job_id = "restart-refresh"
    database.set_setting(
        "webui_product_task_jobs",
        json.dumps(
            {
                "tasks": {
                    job_id: {
                        "job_id": job_id,
                        "kind": "refresh",
                        "action": "refresh",
                        "account_id": "",
                        "generation": 0,
                        "status": "cancelling",
                        "store_id": 1,
                        "tab_keys": ["sales"],
                        "current": 1,
                        "total": 2,
                        "message": "正在取消",
                        "errors": [],
                    }
                }
            },
            ensure_ascii=False,
        ),
    )
    controller = NextUiController(database)

    restored = controller.dispatch(
        "products.refresh.status",
        {"job_id": job_id},
    )

    assert restored["status"] == "verification_pending"
    assert restored["current"] == 1
    assert restored["total"] == 2


def test_delete_first_blocks_async_and_manual_refresh_for_same_store(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "product-delete-first-lease.db")
    store_id = database.add_store("删除租约店铺", "wx-delete-lease", "secret")
    started_wait = threading.Event()
    release = threading.Event()

    class BlockingDeleteService:
        def __init__(self) -> None:
            self.platform_write_authorizer = None
            self.refresh_calls = 0

        def batch_delete_products(self, store, product_ids, **_kwargs):
            started_wait.set()
            assert release.wait(3)
            return {
                "cancelled": False,
                "items": [
                    {
                        "store_id": store.id,
                        "product_id": product_id,
                        "status": "success",
                        "message": "完成",
                    }
                    for product_id in product_ids
                ],
                "counts": {
                    "success": len(product_ids),
                    "failed": 0,
                    "cancelled": 0,
                    "pending": 0,
                },
            }

        def refresh_product_tab(self, *_args, **_kwargs):
            self.refresh_calls += 1
            raise AssertionError("同店刷新不应在删除租约期间执行")

    service = BlockingDeleteService()
    controller = NextUiController(database, product_service=service)
    delete_job = controller.dispatch(
        "products.batch.start",
        {
            "action": "delete",
            "items": [{"store_id": store_id, "product_id": "lease-product"}],
        },
    )
    assert started_wait.wait(3)
    try:
        with pytest.raises(ValueError, match="正在执行"):
            controller.dispatch(
                "products.refresh.start",
                {"store_id": store_id},
            )
        with pytest.raises(ValueError, match="正在执行"):
            controller.dispatch(
                "products.refresh",
                {"store_id": store_id},
            )
    finally:
        release.set()
    _wait_for_status(
        controller,
        "products.batch.status",
        delete_job["job_id"],
    )
    assert service.refresh_calls == 0


def test_async_refresh_first_blocks_delete_for_same_store(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "product-refresh-first-lease.db")
    store_id = database.add_store("刷新租约店铺", "wx-refresh-lease", "secret")
    started_wait = threading.Event()
    release = threading.Event()

    class BlockingRefreshService:
        def __init__(self) -> None:
            self.platform_write_authorizer = None
            self.delete_calls = 0

        def refresh_product_tab(self, _store, _tab, *, cancel_event=None, **_kwargs):
            started_wait.set()
            assert cancel_event is not None
            assert release.wait(3)
            return SyncResult("product_query", 0, "刷新结束", cancelled=True)

        def batch_delete_products(self, *_args, **_kwargs):
            self.delete_calls += 1
            raise AssertionError("刷新租约期间不应执行删除")

    service = BlockingRefreshService()
    controller = NextUiController(database, product_service=service)
    controller.dispatch(
        "products.refresh.start",
        {"store_id": store_id},
    )
    assert started_wait.wait(3)
    try:
        with pytest.raises(ValueError, match="正在执行"):
            controller.dispatch(
                "products.batch.start",
                {
                    "action": "delete",
                    "items": [{"store_id": store_id, "product_id": "lease-product"}],
                },
            )
    finally:
        release.set()
    assert service.delete_calls == 0


def test_manual_refresh_first_blocks_delete_for_same_store(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "product-manual-refresh-lease.db")
    store_id = database.add_store("手动刷新租约店铺", "wx-manual-lease", "secret")
    started_wait = threading.Event()
    release = threading.Event()

    class BlockingManualRefreshService:
        def __init__(self) -> None:
            self.platform_write_authorizer = None

        def refresh_product_tab(self, _store, _tab, **_kwargs):
            started_wait.set()
            assert release.wait(3)
            return SyncResult("product_query", 0, "刷新结束", success=0)

        def batch_delete_products(self, *_args, **_kwargs):
            raise AssertionError("手动刷新租约期间不应执行删除")

    service = BlockingManualRefreshService()
    controller = NextUiController(database, product_service=service)
    errors: list[BaseException] = []

    def run_manual_refresh() -> None:
        try:
            controller.dispatch("products.refresh", {"store_id": store_id})
        except BaseException as exc:
            errors.append(exc)

    worker = threading.Thread(target=run_manual_refresh, daemon=True)
    worker.start()
    assert started_wait.wait(3)
    try:
        with pytest.raises(ValueError, match="正在执行"):
            controller.dispatch(
                "products.batch.start",
                {
                    "action": "delete",
                    "items": [{"store_id": store_id, "product_id": "lease-product"}],
                },
            )
    finally:
        release.set()
        worker.join(3)
    assert not errors


def test_single_delete_first_blocks_refresh_for_same_store(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "single-delete-first-lease.db")
    store_id = database.add_store(
        "单件删除租约店铺",
        "wx-single-delete-lease",
        "secret",
    )
    started_wait = threading.Event()
    release = threading.Event()

    class BlockingSingleDeleteService:
        def __init__(self) -> None:
            self.platform_write_authorizer = None
            self.refresh_calls = 0

        def product_write(
            self,
            _store,
            action,
            *,
            product_id="",
            **_kwargs,
        ):
            assert action == "delete"
            assert product_id == "single-delete-product"
            started_wait.set()
            assert release.wait(3)
            return {"errcode": 0, "delete_mode": "recycle"}

        def refresh_product_tab(self, *_args, **_kwargs):
            self.refresh_calls += 1
            return SyncResult("product_query", 0, "刷新不应执行", success=0)

    service = BlockingSingleDeleteService()
    controller = NextUiController(database, product_service=service)
    errors: list[BaseException] = []

    def run_delete() -> None:
        try:
            controller.dispatch(
                "products.delete",
                {
                    "store_id": store_id,
                    "product_id": "single-delete-product",
                },
            )
        except BaseException as exc:
            errors.append(exc)

    worker = threading.Thread(target=run_delete, daemon=True)
    worker.start()
    assert started_wait.wait(3)
    try:
        with pytest.raises(ValueError, match="正在执行"):
            controller.dispatch(
                "products.refresh.start",
                {"store_id": store_id},
            )
        with pytest.raises(ValueError, match="正在执行"):
            controller.dispatch(
                "products.refresh",
                {"store_id": store_id},
            )
    finally:
        release.set()
        worker.join(3)
    assert not errors
    assert service.refresh_calls == 0


def test_refresh_first_blocks_single_delete_before_service(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "single-delete-refresh-first.db")
    store_id = database.add_store(
        "单件删除刷新先店铺",
        "wx-single-refresh-first",
        "secret",
    )
    started_wait = threading.Event()
    release = threading.Event()

    class BlockingRefreshService:
        def __init__(self) -> None:
            self.platform_write_authorizer = None
            self.delete_calls = 0

        def refresh_product_tab(
            self,
            _store,
            _tab,
            *,
            cancel_event=None,
            **_kwargs,
        ):
            assert cancel_event is not None
            started_wait.set()
            assert release.wait(3)
            return SyncResult("product_query", 0, "刷新结束", success=0)

        def product_write(self, *_args, **_kwargs):
            self.delete_calls += 1
            return {"errcode": 0, "delete_mode": "recycle"}

    service = BlockingRefreshService()
    controller = NextUiController(database, product_service=service)
    controller.dispatch(
        "products.refresh.start",
        {"store_id": store_id},
    )
    assert started_wait.wait(3)
    try:
        with pytest.raises(ValueError, match="正在执行"):
            controller.dispatch(
                "products.delete",
                {
                    "store_id": store_id,
                    "product_id": "single-delete-product",
                },
            )
    finally:
        release.set()
    assert service.delete_calls == 0
