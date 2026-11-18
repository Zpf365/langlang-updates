from __future__ import annotations

import sqlite3
import tempfile
import time
from pathlib import Path

import pytest

from weimi_manager.db import Database
from weimi_manager.sales_related.models import SkuCandidate
from weimi_manager.sales_related.repository import SalesRelatedRepository
from weimi_manager.sales_related.state_machine import (
    SalesEvent,
)


def _create_task(repo: SalesRelatedRepository, store_id: int):
    return repo.create_task(
        source_url="https://example.test/product/1?from=share",
        canonical_url="https://example.test/product/1?from=share",
        store_id=store_id,
        sku_selection_mode="lowest_price",
        purchase_quantity=3,
        created_by="tester",
    )


def _task_to_awaiting_confirmation(
    repo: SalesRelatedRepository,
    task_id: str,
) -> None:
    for event in (
        SalesEvent.IDENTIFY_PRODUCT,
        SalesEvent.CONFIRM_SKU,
        SalesEvent.VERIFY_CHECKOUT,
        SalesEvent.REQUEST_USER_CONFIRMATION,
    ):
        repo.transition_task(task_id, event, actor="tester")


def test_migration_creates_schema_and_preserves_existing_store_data() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "sales.db"
        db = Database(path)
        store_id = db.add_store("迁移保留店", "wx-sales-migrate", "secret")
        with db.connect() as conn:
            for table in (
                "sales_audit_logs",
                "sales_aftersale_cases",
                "sales_buyer_whitelist",
                "sales_device_leases",
                "sales_devices",
                "sales_order_verifications",
                "sales_orders",
                "sales_task_sku_snapshots",
                "sales_tasks",
                "schema_migrations",
            ):
                conn.execute(f"DROP TABLE IF EXISTS {table}")

        upgraded = Database(path)
        backups = list(path.parent.glob("*.pre-sales-related-v1.bak"))
        assert upgraded.get_store(store_id) is not None
        assert len(backups) == 1
        with upgraded.connect() as conn:
            version = conn.execute(
                """
                SELECT version FROM schema_migrations
                 WHERE scope='sales_related'
                """
            ).fetchone()
            assert version["version"] == 1
            tables = {
                row["name"]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
        assert "sales_tasks" in tables
        assert "sales_orders" in tables

        Database(path)
        assert len(list(path.parent.glob("*.pre-sales-related-v1.bak"))) == 1


def test_task_order_submission_and_one_order_per_task_constraint() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "task.db")
        store_id = db.add_store("任务店", "wx-sales-task", "secret")
        repo = SalesRelatedRepository(db.path)
        task = _create_task(repo, store_id)
        repo.add_sku_snapshot(
            task["task_id"],
            SkuCandidate(
                sku_id="SKU-1",
                price_cents=1990,
                stock=10,
            ),
            product_id="PRODUCT-1",
        )
        _task_to_awaiting_confirmation(repo, task["task_id"])
        submission_id = repo.set_submission_id(task["task_id"], actor="tester")
        repo.transition_task(
            task["task_id"],
            SalesEvent.REQUEST_SUBMISSION,
            actor="tester",
        )
        repo.record_order_verification(
            task["task_id"],
            submission_id,
            source="merchant_order_api",
            outcome="created",
            trusted=True,
            order_id="ORDER-1",
        )
        repo.transition_task(
            task["task_id"],
            SalesEvent.SUBMISSION_CONFIRMED,
            actor="tester",
        )
        order = repo.create_verified_order(
            task_id=task["task_id"],
            submission_id=submission_id,
            order_id="ORDER-1",
            sku_id="SKU-1",
            quantity=3,
            store_id=store_id,
            store_app_id="wx-sales-task",
            product_amount_cents=5970,
            payable_cents=5970,
            actor="tester",
        )
        assert order["quantity"] == 3
        assert len(repo.list_orders()) == 1
        with pytest.raises(ValueError):
            repo.create_verified_order(
                task_id=task["task_id"],
                submission_id=submission_id,
                order_id="ORDER-2",
                sku_id="SKU-1",
                quantity=3,
            )


def test_order_cannot_be_written_without_trusted_verification() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "untrusted.db")
        store_id = db.add_store("未核验店", "wx-untrusted", "secret")
        repo = SalesRelatedRepository(db.path)
        task = _create_task(repo, store_id)
        _task_to_awaiting_confirmation(repo, task["task_id"])
        submission_id = repo.set_submission_id(task["task_id"])
        repo.transition_task(
            task["task_id"],
            SalesEvent.REQUEST_SUBMISSION,
        )
        repo.record_order_verification(
            task["task_id"],
            submission_id,
            source="android_claim",
            outcome="created",
            trusted=True,
            order_id="ORDER-UNTRUSTED",
        )
        with pytest.raises(ValueError, match="可信订单核验"):
            repo.create_verified_order(
                task_id=task["task_id"],
                submission_id=submission_id,
                order_id="ORDER-UNTRUSTED",
                sku_id="SKU-1",
                quantity=1,
            )


def test_audit_log_is_append_only() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "audit.db")
        repo = SalesRelatedRepository(db.path)
        with repo.connect() as conn:
            repo.append_audit(conn, "sample", actor="tester")
        with repo.connect() as conn:
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute("UPDATE sales_audit_logs SET actor='tamper'")


def test_device_lease_prevents_duplicate_workers() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "lease.db")
        store_id = db.add_store("设备店", "wx-sales-device", "secret")
        repo = SalesRelatedRepository(db.path)
        task = _create_task(repo, store_id)
        repo.upsert_device("device-1", "token-1", display_name="手机一")
        assert repo.authenticate_device("device-1", "token-1")
        assert not repo.authenticate_device("device-1", "wrong-token")
        lease = repo.claim_task_lease(
            task["task_id"],
            "device-1",
            "lease-1",
            lease_seconds=60,
        )
        assert lease["device_id"] == "device-1"
        heartbeat = repo.heartbeat_lease(
            task["task_id"],
            "device-1",
            "lease-1",
            lease_seconds=60,
        )
        assert heartbeat["attempt_no"] == 1
        with pytest.raises(ValueError):
            repo.claim_task_lease(
                task["task_id"],
                "device-1",
                "lease-2",
                lease_seconds=60,
            )
        with repo.connect() as conn:
            conn.execute(
                """
                UPDATE sales_device_leases
                   SET lease_expires_at='2000-01-01T00:00:00+00:00'
                 WHERE task_id=?
                """,
                (task["task_id"],),
            )
        time.sleep(0.01)
        renewed = repo.claim_task_lease(
            task["task_id"],
            "device-1",
            "lease-3",
            lease_seconds=60,
        )
        assert renewed["attempt_no"] == 2
        repo.release_lease(task["task_id"], "device-1", "lease-3")


def test_whitelist_is_unique_per_store_and_verified_identity() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "whitelist.db")
        store_id = db.add_store("白名单店", "wx-sales-whitelist", "secret")
        repo = SalesRelatedRepository(db.path)
        values = {
            "store_id": store_id,
            "store_app_id": "wx-sales-whitelist",
            "buyer_identifier_type": "openid",
            "buyer_identifier": "openid-1",
            "allowed_after_sale_types": ["refund"],
            "single_refund_limit_cents": 5000,
            "daily_refund_limit_cents": 10000,
            "monthly_refund_limit_cents": 30000,
            "valid_from": "2026-01-01T00:00:00+00:00",
            "created_by": "tester",
        }
        first = repo.upsert_whitelist(**values)
        second = repo.upsert_whitelist(
            **{
                **values,
                "single_refund_limit_cents": 6000,
            }
        )
        assert first == second
