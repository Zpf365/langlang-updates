from __future__ import annotations

import csv
import io
import uuid
from datetime import UTC, datetime

from ..domain.enums import ProductPoolStatus
from ..domain.models import ProductPool
from ..domain.rules import (
    StoreIsolationError,
    csv_restore_text,
    csv_safe_text,
)
from ..infrastructure.repository import (
    DongxiaoRepository,
    EntityNotFound,
)


class ProductPoolService:
    def __init__(self, repository: DongxiaoRepository) -> None:
        self.repository = repository

    def create_pool(
        self,
        store,
        *,
        name: str,
        group_name: str = "",
        idempotency_key: str,
    ) -> ProductPool:
        now = datetime.now(UTC)
        pool, _created = self.repository.create_pool(
            ProductPool(
                pool_id=uuid.uuid4().hex,
                store_id=int(store.id),
                store_app_id=str(store.app_id),
                name=name,
                group_name=group_name,
                status=ProductPoolStatus.ACTIVE,
                idempotency_key=idempotency_key,
                created_at=now,
                updated_at=now,
            )
        )
        return pool

    def list_pools(self, store) -> list[ProductPool]:
        return self.repository.list_pools(int(store.id))

    def _require_pool(self, store, pool_id: str) -> ProductPool:
        pool = self.repository.get_pool(
            pool_id,
            store_id=int(store.id),
        )
        if pool is None or pool.store_app_id != str(store.app_id):
            raise StoreIsolationError("商品池不属于当前店铺")
        return pool

    def rename(
        self,
        store,
        pool_id: str,
        *,
        name: str,
        group_name: str = "",
    ) -> ProductPool:
        self._require_pool(store, pool_id)
        return self.repository.rename_pool(
            pool_id,
            store_id=int(store.id),
            store_app_id=str(store.app_id),
            name=name,
            group_name=group_name,
        )

    def add_products(
        self,
        store,
        pool_id: str,
        products: list[tuple[str, str]],
    ) -> dict[str, int]:
        self._require_pool(store, pool_id)
        return self.repository.add_pool_products(
            pool_id,
            store_id=int(store.id),
            store_app_id=str(store.app_id),
            products=products,
        )

    def details(self, store, pool_id: str) -> list[dict[str, object]]:
        self._require_pool(store, pool_id)
        return self.repository.list_pool_items(
            pool_id,
            store_id=int(store.id),
            store_app_id=str(store.app_id),
        )

    def remove(self, store, pool_id: str, item_id: str) -> None:
        self._require_pool(store, pool_id)
        self.repository.remove_pool_item(
            pool_id,
            item_id,
            store_id=int(store.id),
            store_app_id=str(store.app_id),
        )

    def reorder(
        self,
        store,
        pool_id: str,
        item_ids: list[str],
    ) -> None:
        self._require_pool(store, pool_id)
        self.repository.reorder_pool_items(
            pool_id,
            item_ids,
            store_id=int(store.id),
            store_app_id=str(store.app_id),
        )

    def copy(
        self,
        store,
        source_pool_id: str,
        *,
        name: str,
        group_name: str = "",
    ) -> ProductPool:
        self._require_pool(store, source_pool_id)
        return self.repository.copy_pool(
            source_pool_id,
            new_pool_id=uuid.uuid4().hex,
            store_id=int(store.id),
            store_app_id=str(store.app_id),
            new_name=name,
            group_name=group_name,
        )

    def stats(self, store, pool_id: str) -> dict[str, int]:
        self._require_pool(store, pool_id)
        return self.repository.pool_stats(
            pool_id,
            store_id=int(store.id),
            store_app_id=str(store.app_id),
        )

    def history(self, store, pool_id: str) -> list[dict[str, object]]:
        self._require_pool(store, pool_id)
        return self.repository.list_pool_events(
            pool_id,
            store_id=int(store.id),
        )

    def export_csv(self, store, pool_id: str) -> str:
        rows = self.details(store, pool_id)
        buffer = io.StringIO()
        writer = csv.writer(buffer, lineterminator="\n")
        writer.writerow(
            (
                "product_id",
                "sku_id",
                "position",
                "title",
                "store_name",
                "state",
                "min_price_cents",
                "stock_total",
                "presence_state",
            )
        )
        for row in rows:
            writer.writerow(
                (
                    csv_safe_text(row["product_id"]),
                    csv_safe_text(row["sku_id"]),
                    row["position"],
                    csv_safe_text(row.get("title", "")),
                    csv_safe_text(row.get("store_name", "")),
                    csv_safe_text(row.get("state", "")),
                    row.get("min_price_cents", ""),
                    row.get("stock_total", ""),
                    csv_safe_text(row.get("presence_state", "")),
                )
            )
        return buffer.getvalue()

    def import_csv(
        self,
        store,
        pool_id: str,
        content: str,
    ) -> dict[str, int]:
        self._require_pool(store, pool_id)
        if not isinstance(content, str):
            raise TypeError("商品池导入内容必须是字符串")
        reader = csv.DictReader(io.StringIO(content.lstrip("\ufeff")))
        if not reader.fieldnames or not {
            "product_id",
            "sku_id",
        }.issubset(reader.fieldnames):
            raise ValueError("CSV 缺少 product_id 或 sku_id 列")
        entries: list[tuple[str, str]] = []
        invalid = 0
        for row in reader:
            product_id = csv_restore_text(
                row.get("product_id", "")
            ).strip()
            sku_id = csv_restore_text(row.get("sku_id", "")).strip()
            if not product_id:
                invalid += 1
                continue
            entries.append((product_id, sku_id))
        result = self.add_products(store, pool_id, entries)
        return {
            "added": result["added"],
            "skipped": result["skipped"],
            "invalid": invalid,
        }

    def ensure_exists(self, store, pool_id: str) -> None:
        try:
            self._require_pool(store, pool_id)
        except EntityNotFound as exc:
            raise StoreIsolationError("商品池不存在或不属于当前店铺") from exc
