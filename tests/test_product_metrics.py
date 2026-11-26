from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch

from weimi_manager.db import Database
from weimi_manager.service import SyncService
from weimi_manager.shop_private import ShopPrivateClient


def test_private_product_metrics_map_real_30d_and_recommend_fields() -> None:
    client = object.__new__(ShopPrivateClient)
    client._post = lambda _path, _body: {
        "data": {
            "rowsMap": [
                {
                    "value": {
                        "scene_type_": "0",
                        "spu_id_": "product-1",
                        "shopxspu_growth_product_view_cnt_30d_2": "320",
                        "shopxspu_growth_pay_product_cnt_30d_2": "18",
                        "shopxspu_growth_rec_product_view_cnt_30d_2": "210",
                    }
                },
                {
                    "value": {
                        "scene_type_": "1",
                        "spu_id_": "product-1",
                        "shopxspu_growth_product_view_cnt_30d_2": "999999",
                        "shopxspu_growth_rec_product_view_cnt_30d_2": "999999",
                    }
                },
            ]
        }
    }

    metrics = client.product_metrics(["product-1"])

    assert metrics["product-1"] == {
        "views30": 320,
        "sold30": 18,
        "recommend_views30": 210,
    }


def test_private_product_metrics_keep_missing_recommend_as_none() -> None:
    client = object.__new__(ShopPrivateClient)
    client._post = lambda _path, _body: {
        "data": {
            "rowsMap": [
                {
                    "value": {
                        "scene_type_": "0",
                        "spu_id_": "product-2",
                        "shopxspu_growth_product_view_cnt_30d_2": "12",
                        "shopxspu_growth_pay_product_cnt_30d_2": "3",
                    }
                }
            ]
        }
    }

    metrics = client.product_metrics(["product-2"])

    assert metrics["product-2"]["views30"] == 12
    assert metrics["product-2"]["sold30"] == 3
    assert metrics["product-2"]["recommend_views30"] is None


def test_database_adds_product_recommend_column_without_losing_metrics(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "product-metrics.db")
    store_id = database.add_store("指标店铺", "wx-product-metrics", "secret")
    database.upsert_product_metrics(
        store_id,
        "product-1",
        views30=100,
        sold30=9,
        recommend_views30=80,
    )
    database.upsert_product_metrics(
        store_id,
        "product-2",
        views30=50,
        sold30=4,
    )
    database.upsert_product_metrics(
        store_id,
        "product-3",
        views30=None,
        sold30=None,
        recommend_views30=None,
    )

    metrics = database.get_product_metrics_map(store_id)

    assert metrics["product-1"]["views30"] == 100
    assert metrics["product-1"]["sold30"] == 9
    assert metrics["product-1"]["recommend_views30"] == 80
    assert metrics["product-2"]["recommend_views30"] is None
    assert metrics["product-3"]["views30"] is None
    assert metrics["product-3"]["sold30"] is None
    assert metrics["product-3"]["recommend_views30"] is None


def test_existing_product_metrics_table_gets_additive_recommend_column(
    tmp_path: Path,
) -> None:
    path = tmp_path / "legacy-product-metrics.db"
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            """
            CREATE TABLE product_metrics (
                store_id INTEGER NOT NULL,
                product_id TEXT NOT NULL,
                views30 INTEGER NOT NULL DEFAULT 0,
                sold30 INTEGER NOT NULL DEFAULT 0,
                click_cnt INTEGER NOT NULL DEFAULT 0,
                click_uv INTEGER NOT NULL DEFAULT 0,
                pay_cnt INTEGER NOT NULL DEFAULT 0,
                pay_uv INTEGER NOT NULL DEFAULT 0,
                pay_gmv INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (store_id, product_id)
            )
            """
        )
        conn.execute(
            """
            INSERT INTO product_metrics(
                store_id, product_id, views30, sold30, click_cnt, click_uv,
                pay_cnt, pay_uv, pay_gmv, updated_at
            ) VALUES (1, 'legacy-product', 70, 6, 0, 0, 0, 0, 0, 'before')
            """
        )
        conn.commit()
    finally:
        conn.close()

    database = Database(path)
    metrics = database.get_product_metrics_map(1)

    assert metrics["legacy-product"]["views30"] == 70
    assert metrics["legacy-product"]["sold30"] == 6
    assert metrics["legacy-product"]["recommend_views30"] is None


def test_private_metric_refresh_persists_recommend_views(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "private-refresh.db")
    store_id = database.add_store("刷新指标店铺", "wx-refresh-metrics", "secret")
    store = database.get_store(store_id)
    assert store is not None

    class PrivateClient:
        def product_metrics(self, _product_ids, stop=None):
            return {
                "product-1": {
                    "views30": 88,
                    "sold30": 7,
                    "recommend_views30": 61,
                }
            }

    with patch(
        "weimi_manager.service.ShopPrivateClient",
        return_value=PrivateClient(),
    ):
        result = SyncService(database).sync_private_product_metrics(
            store,
            ["product-1"],
        )

    assert result.success == 1
    metrics = database.get_product_metrics_map(store_id)["product-1"]
    assert metrics["views30"] == 88
    assert metrics["sold30"] == 7
    assert metrics["recommend_views30"] == 61
