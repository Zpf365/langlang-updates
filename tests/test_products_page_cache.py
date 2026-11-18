from __future__ import annotations

from types import SimpleNamespace

from weimi_manager.models import Store
from weimi_manager.ui.pages import ProductsPage


def _store(store_id: int, name: str, group: str):
    return Store(
        id=store_id,
        name=name,
        group_name=group,
        app_id=f"wx-{store_id}",
        status="",
        last_sync_at=None,
        created_at="2026-11-12T00:00:00+00:00",
    )


def test_products_refresh_uses_cache_without_network_query() -> None:
    page = object.__new__(ProductsPage)
    calls: list[str] = []
    page._reload_stores = lambda: calls.append("stores")
    page._load_cached_products = lambda: calls.append("cache")
    page._query_products = lambda: calls.append("network")

    ProductsPage.refresh(page)

    assert calls == ["stores", "cache"]


def test_store_selection_only_reloads_cache() -> None:
    page = object.__new__(ProductsPage)
    calls: list[str] = []
    page._query_generation = 0
    page._fresh_scope = (1, "sales")
    page._fresh_store_ids = {1}
    page._query = lambda: calls.append("local")
    page._load_cached_products = lambda: calls.append("cache")
    page._query_products = lambda: calls.append("network")

    ProductsPage._on_store_selected(page)

    assert calls == ["cache"]
    assert page._query_generation == 1
    assert page._fresh_scope is None
    assert page._fresh_store_ids == set()


def test_query_scope_honors_selected_store_and_group() -> None:
    page = object.__new__(ProductsPage)
    stores = [
        _store(1, "一组店铺", "一号组"),
        _store(2, "二组店铺", "二号组"),
        _store(3, "一号组第二家", "一号组"),
    ]
    page._all_stores = stores
    page.group_var = SimpleNamespace(get=lambda: "一号组")
    selected_store = {"value": "全部店铺"}
    page.store_var = SimpleNamespace(get=lambda: selected_store["value"])

    page._store_lookup = {"全部店铺": None}
    assert page._query_store_ids() == [1, 3]

    page._store_lookup = {"二组店铺 · #2": stores[1]}
    selected_store["value"] = "二组店铺 · #2"
    assert page._query_store_ids() == [2]

    page.group_var = SimpleNamespace(get=lambda: "全部分组")
    selected_store["value"] = "全部店铺"
    assert page._query_store_ids() == [1, 2, 3]
