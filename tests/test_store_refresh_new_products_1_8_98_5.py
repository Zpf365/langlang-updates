from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import weimi_manager.db as db_module
from weimi_manager.db import Database
from weimi_manager.webui.controller import NextUiController


class RefreshService:
    def __init__(self, database: Database) -> None:
        self.db = database

    def refresh_store_metrics(self, _store):
        return {"_credential_probe_ok": True}

    def scan_product_catalog(self, store):
        run_id = self.db.start_sync_run(store.id, "product_catalog")
        scan = self.db.begin_product_scan(
            store.id,
            f"scan-{store.id}",
            reset=True,
        )
        self.db.save_product_scan_page(
            store.id,
            str(scan["scan_id"]),
            ["product-new-1"],
            "",
        )
        self.db.complete_product_scan(
            store.id,
            str(scan["scan_id"]),
        )
        self.db.finish_sync_run(
            run_id,
            "success",
            1,
            "ok",
        )
        return SimpleNamespace(count=1)


def test_store_refresh_runs_product_catalog_scan_for_new_count(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        db_module,
        "_now",
        lambda: "2026-11-25T13:00:00+00:00",
        raising=True,
    )
    database = Database(tmp_path / "refresh-new-products.db")
    store_id = database.add_store("刷新店铺", "wx-refresh-new", "secret")
    store = database.get_store(store_id)
    service = RefreshService(database)
    controller = NextUiController(
        database,
        product_service=service,
        now_provider=lambda: datetime.fromisoformat(
            "2026-11-25T21:00:00+08:00"
        ),
    )
    monkeypatch.setattr(
        controller,
        "_recover_store_credential",
        lambda current, _service: (
            current,
            {"action": "none"},
        ),
    )

    active_store, credential = controller._refresh_store_with_credential(
        store,
        service,
    )

    assert active_store.id == store.id
    assert "catalog_error" not in credential
    new_products = database.product_new_today_map(
        {store.id},
        now=controller._now_provider(),
    )[store.id]
    assert new_products["count"] == 1
    assert new_products["scan_completed_today"] is True
