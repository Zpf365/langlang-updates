from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from weimi_manager.db import Database
from weimi_manager.pyside.pages.upload import UploadRunner


class FakeProtector:
    @staticmethod
    def protect(value: str) -> bytes:
        return b"fake:" + value.encode("utf-8")

    @staticmethod
    def unprotect(value: bytes) -> str:
        return value[5:].decode("utf-8")


def test_upload_runner_marks_missing_product_id_for_remote_verification(
    tmp_path: Path,
) -> None:
    db = Database(tmp_path / "upload.db", protector=FakeProtector())
    store_id = db.add_store("Fixture", "wx-upload", "secret")
    store = db.get_store(store_id)

    class Service:
        calls = 0

        @classmethod
        def product_write(cls, *_args, **_kwargs):
            cls.calls += 1
            return {"errcode": 0}

    task = {
        "id": "task-1",
        "store_id": store_id,
        "store_name": store.name,
        "status": "pending",
        "template": {
            "name": "fixture",
            "duplicate_filter_mode": "off",
            "auto_open_category": False,
            "listing_mode": "immediate",
        },
        "items": [
            {
                "item_id": "item-1",
                "source_product_id": "source-1",
                "title": "Fixture product",
                "price": 100,
                "stock": 2,
                "status": "pending",
                "payload": {
                    "title": "Fixture product",
                    "head_imgs": [
                        "https://example.invalid/1.jpg",
                        "https://example.invalid/2.jpg",
                        "https://example.invalid/3.jpg",
                    ],
                    "skus": [{"sale_price": 100, "stock_num": 2}],
                    "cats": ["6033", "6093", "549163"],
                },
            }
        ],
    }
    workspace = SimpleNamespace(db=db, service=Service())
    errors = UploadRunner(workspace, [task]).run([task])
    assert errors == []
    assert Service.calls == 1
    assert task["items"][0]["status"] == "verification_pending"
    assert "核实" in task["items"][0]["message"]
