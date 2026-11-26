from __future__ import annotations

from pathlib import Path

from weimi_manager.db import Database
from weimi_manager.models import Product
from weimi_manager.service import SyncService
from weimi_manager.webui.hosted_runner import run_hosted_store


def _product(store_id: int, product_id: str, status: int) -> Product:
    return Product(
        store_id=store_id,
        store_name="联盟佣金测试店",
        product_id=product_id,
        title=f"商品 {product_id}",
        status=status,
        status_text="上架" if status == 5 else "下架",
        edit_status=0,
        min_price=1000,
        sku_count=1,
        stock_total=10,
        spu_code="",
        out_product_id="",
        head_img="",
    )


def test_hosted_commission_processes_all_active_products_and_existing_plans(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "hosted-all-commission.db")
    store_id = database.add_store("联盟佣金测试店", "wx-hosted-all-commission", "secret")
    active_ids = [str(10_000 + index) for index in range(45)]
    for product_id in active_ids:
        database.upsert_product(_product(store_id, product_id, 5))
    for index in range(8):
        database.upsert_product(_product(store_id, str(20_000 + index), 11))
    database.set_setting(
        "violation_blacklist",
        f'[{{"product_id": "{active_ids[0]}"}}]',
    )

    class FakeLeagueService:
        def __init__(self) -> None:
            self.promoted_ids: list[str] = []
            self.promote_ratio = 0.0
            self.updated_ids: list[str] = []
            self.update_ratio = 0.0

        def promote_products_to_league(self, store, product_ids, ratio):
            self.promoted_ids = list(product_ids)
            self.promote_ratio = ratio
            return {
                "errcode": 0,
                "requested_count": len(product_ids),
                "failed_count": 0,
                "result_info_list": [
                    {"product_id": product_id, "errcode": 0}
                    for product_id in product_ids
                ],
            }

        def fetch_active_product_ids(self, store):
            return active_ids

        def fetch_league_products(self, store):
            return {
                "items": [
                    {"product_id": str(30_000 + index)}
                    for index in range(150)
                ]
            }

        def update_league_ratio(self, store, product_ids, ratio):
            self.updated_ids = list(product_ids)
            self.update_ratio = ratio
            return []

    service = FakeLeagueService()
    store = database.get_store(store_id)
    error = run_hosted_store(
        database,
        service,
        store,
        {
            "enabled": 1,
            "enable_league": 1,
            "enable_commission": 1,
            "league_commission_percent": 20,
            "product_commission_percent": 20,
        },
    )

    assert error == ""
    assert set(service.promoted_ids) == set(active_ids[1:])
    assert len(service.promoted_ids) == 44
    assert service.promote_ratio == 20
    assert len(service.updated_ids) == 150
    assert service.update_ratio == 20
    run = database.list_hosted_runs()[0]
    assert run["result"] == "success"
    assert "全店联盟佣金 44/44" in run["actions"]
    assert "修改佣金 150" in run["actions"]


def test_fetch_league_products_paginates_beyond_first_page(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "hosted-league-pages.db")
    store_id = database.add_store("联盟分页店铺", "wx-league-pages", "secret")
    store = database.get_store(store_id)

    class FakeClient:
        def __init__(self) -> None:
            self.pages: list[int] = []

        def fetch_league_items(self, *, page_size, page_index):
            self.pages.append(page_index)
            if page_index == 1:
                return {
                    "errcode": 0,
                    "items": [
                        {"product_id": str(index)}
                        for index in range(100)
                    ],
                    "total_num": 102,
                }
            if page_index == 2:
                return {
                    "errcode": 0,
                    "items": [
                        {"product_id": str(100 + index)}
                        for index in range(2)
                    ],
                    "total_num": 102,
                }
            return {"errcode": 0, "items": [], "total_num": 102}

    client = FakeClient()
    service = SyncService(database)
    service.client_for = lambda _store: client

    result = service.fetch_league_products(store)

    assert client.pages == [1, 2]
    assert len(result["items"]) == 102
    assert result["total_num"] == 102
