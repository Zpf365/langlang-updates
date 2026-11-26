from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from weimi_manager.db import Database
from weimi_manager.sales_related.models import SkuCandidate
from weimi_manager.sales_related.repository import SalesRelatedRepository
from weimi_manager.sales_related.service import SalesRelatedService


def test_service_imports_and_creates_independent_tasks() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "service.db")
        store_id = db.add_store("服务店", "wx-sales-service", "secret")
        service = SalesRelatedService(SalesRelatedRepository(db.path))
        parsed, tasks = service.create_tasks_from_text(
            "\n".join(
                f"https://example.test/item/{index}"
                for index in range(55)
            ),
            store_id=store_id,
            store_app_id="wx-sales-service",
            sku_selection_mode="lowest_price",
            purchase_quantity=3,
            created_by="tester",
        )
        assert len(parsed.accepted) == 55
        assert len(tasks) == 55
        assert all(task["store_app_id"] == "wx-sales-service" for task in tasks)
        assert all(task["purchase_quantity"] == 3 for task in tasks)


def test_service_rejects_operation_without_procurement_permission() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "permission.db")
        service = SalesRelatedService(
            SalesRelatedRepository(db.path),
            permission_checker=lambda permission: permission != "procurement",
        )
        with pytest.raises(PermissionError):
            service.import_links("https://example.test/item/1")


def test_service_prepares_sku_and_persists_pause_reason() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "selection.db")
        store_id = db.add_store("选品店", "wx-sales-selection", "secret")
        repository = SalesRelatedRepository(db.path)
        service = SalesRelatedService(repository)
        _parsed, tasks = service.create_tasks_from_text(
            "https://example.test/item/1",
            store_id=store_id,
            store_app_id="wx-sales-selection",
            sku_selection_mode="lowest_price",
            purchase_quantity=2,
            created_by="tester",
        )
        task = tasks[0]
        selection = service.prepare_task_sku(
            task["task_id"],
            [
                SkuCandidate("SKU-A", 1000, 1),
                SkuCandidate("SKU-B", 1200, 10),
            ],
            actor="tester",
        )
        assert selection.selected
        assert selection.selected.sku_id == "SKU-B"
        assert repository.get_task(task["task_id"])["status"] == "sku_confirmed"

        _parsed, paused_tasks = service.create_tasks_from_text(
            "https://example.test/item/2",
            store_id=store_id,
            store_app_id="wx-sales-selection",
            sku_selection_mode="lowest_price",
            purchase_quantity=5,
            created_by="tester",
        )
        paused = service.prepare_task_sku(
            paused_tasks[0]["task_id"],
            [SkuCandidate("SKU-A", 1000, 2)],
            actor="tester",
        )
        assert not paused.ok
        saved = repository.get_task(paused_tasks[0]["task_id"])
        assert "不拆单" in saved["status_reason"]
