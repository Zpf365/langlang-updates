from __future__ import annotations

import json
import tkinter as tk
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from weimi_manager.db import Database
from weimi_manager.ui.product_link_dialog import ProductPurchaseLinkDialog


class ProductLinkDialogLayoutTests(unittest.TestCase):
    def test_mapping_uses_unified_purchase_columns(self) -> None:
        try:
            root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(str(exc))
        root.withdraw()
        with TemporaryDirectory() as temp:
            db = Database(Path(temp) / "link-layout.db")
            product = {
                "product_id": "PRODUCT-1",
                "title": "测试商品",
                "store_name": "测试店铺",
                "raw_json": json.dumps(
                    {
                        "skus": [
                            {
                                "sku_id": "SKU-1",
                                "sale_price": 1990,
                                "sku_attrs": [
                                    {"attr_key": "颜色", "attr_value": "红色"}
                                ],
                            }
                        ]
                    }
                ),
            }
            window = SimpleNamespace(
                set_status=lambda *_args, **_kwargs: None,
                run_task=lambda *_args, **_kwargs: None,
            )
            dialog = ProductPurchaseLinkDialog(
                root,
                db=db,
                window=window,
                store_id=1,
                product=product,
            )
            try:
                root.update_idletasks()
                self.assertEqual(
                    tuple(dialog.store_tree["columns"]),
                    (
                        "sequence",
                        "attrs",
                        "store_price",
                        "purchase_link",
                        "purchase_price",
                        "purchase_image",
                        "purchase_attrs",
                    ),
                )
                self.assertEqual(
                    dialog.store_tree.heading("purchase_attrs")["text"],
                    "采购商品属性",
                )
            finally:
                dialog.destroy()
                root.destroy()


if __name__ == "__main__":
    unittest.main()
