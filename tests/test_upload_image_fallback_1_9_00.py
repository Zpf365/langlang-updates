from __future__ import annotations

from pathlib import Path

from weimi_manager.db import Database
from weimi_manager.service import SyncService


def test_empty_processed_images_use_real_store_defaults(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database = Database(tmp_path / "upload-image-fallback.db")
    store_id = database.add_store(
        "图片回退店铺",
        "wx-upload-image-fallback",
        "secret",
    )
    store = database.get_store(store_id)
    assert store is not None
    service = SyncService(database)
    defaults = {
        "head_imgs": [
            "https://default-1.mmecimage.cn/image.jpg",
            "https://default-2.mmecimage.cn/image.jpg",
            "https://default-3.mmecimage.cn/image.jpg",
        ],
        "desc_info": {
            "imgs": ["https://detail-default.mmecimage.cn/image.jpg"],
        },
        "express_info": {"template_id": "freight-1"},
        "after_sale_info": {"after_sale_address_id": "address-1"},
        "deliver_method": 0,
    }
    monkeypatch.setattr(
        service,
        "_product_payload_defaults",
        lambda _store, _client: defaults,
    )
    monkeypatch.setattr(
        service,
        "_validate_store_delivery_config",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        service,
        "_apply_required_category_attributes",
        lambda *_args, **_kwargs: None,
    )

    prepared = service._prepare_product_payload(
        store,
        object(),
        {
            "head_imgs": [],
            "desc_info": {"imgs": []},
            "express_info": {"template_id": "freight-1"},
            "after_sale_info": {"after_sale_address_id": "address-1"},
            "skus": [
                {
                    "sale_price": 1000,
                    "stock_num": 1,
                    "sku_attrs": [
                        {"attr_key": "规格", "attr_value": "默认"}
                    ],
                }
            ],
        },
    )

    assert prepared["head_imgs"] == defaults["head_imgs"]
    assert prepared["desc_info"]["imgs"] == defaults["desc_info"]["imgs"]
