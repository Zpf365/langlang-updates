from __future__ import annotations

import sqlite3
from pathlib import Path

from weimi_manager.db import Database


def test_existing_database_without_purchase_record_link_id_is_upgraded(
    tmp_path: Path,
) -> None:
    path = tmp_path / "legacy-link-id.db"
    Database(path)
    with sqlite3.connect(path) as connection:
        connection.execute("DROP INDEX idx_procurement_purchase_link")
        connection.execute(
            "ALTER TABLE procurement_purchase_records DROP COLUMN link_id"
        )
        columns = {
            str(row[1])
            for row in connection.execute(
                "PRAGMA table_info(procurement_purchase_records)"
            )
        }
        assert "link_id" not in columns

    upgraded = Database(path)
    with upgraded.connect() as connection:
        columns = {
            str(row["name"])
            for row in connection.execute(
                "PRAGMA table_info(procurement_purchase_records)"
            )
        }
        indexes = {
            str(row["name"])
            for row in connection.execute(
                "PRAGMA index_list(procurement_purchase_records)"
            )
        }
    assert "link_id" in columns
    assert "idx_procurement_purchase_link" in indexes
