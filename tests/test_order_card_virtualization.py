from __future__ import annotations

import tkinter as tk
import unittest

from weimi_manager.ui.virtual_order_card_list import OrderCardList


def _row(index: int) -> dict:
    return {
        "iid": str(index),
        "row": {
            "store_id": 1,
            "order_id": f"ORDER-{index:04d}",
            "store_name": "测试店铺",
            "total_amount": 1990,
            "buyer_name": "测试买家",
            "phone": "13800000000",
            "address": "测试地址",
            "carrier": "测试物流",
            "waybill_id": f"WAYBILL-{index:04d}",
            "merchant_notes": "",
            "raw_json": "{}",
        },
        "created_text": "2026-11-15 10:00",
        "status_text": "待发货",
        "product_title": "测试商品",
        "product_lines": [],
        "buyer_name": "测试买家",
        "buyer_detail": "测试地址",
        "notes": [],
    }


class OrderCardVirtualizationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        try:
            cls.root = tk.Tk()
        except tk.TclError as exc:
            raise unittest.SkipTest(str(exc)) from exc
        cls.root.geometry("1100x650")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.root.destroy()

    def setUp(self) -> None:
        self.actions: list[tuple[str, dict]] = []
        self.cards = OrderCardList(
            self.root,
            lambda action, item: self.actions.append((action, item)),
        )
        self.cards.pack(fill="both", expand=True)

    def tearDown(self) -> None:
        self.cards.destroy()

    def test_only_visible_cards_are_created_for_a_large_order_list(self) -> None:
        self.cards.set_rows([_row(index) for index in range(100)])
        self.root.update()

        rendered = self.cards.rendered_iids()
        self.assertLessEqual(len(rendered), 12)
        self.assertNotIn("99", rendered)

    def test_mouse_wheel_scrolls_when_pointer_is_over_a_card(self) -> None:
        self.cards.set_rows([_row(index) for index in range(100)])
        self.root.update()
        before = self.cards.canvas.yview()[0]

        self.cards.canvas.event_generate(
            "<MouseWheel>",
            x=100,
            y=100,
            delta=-120,
        )
        self.cards._flush_scroll()
        self.root.update()

        self.assertGreater(self.cards.canvas.yview()[0], before)

    def test_dragging_scrollbar_to_bottom_recycles_card_widgets(self) -> None:
        self.cards.set_rows([_row(index) for index in range(100)])
        self.root.update()

        self.cards._on_scrollbar("moveto", "1.0")
        self.cards._flush_scroll()
        self.root.update()

        rendered = self.cards.rendered_iids()
        self.assertLessEqual(len(rendered), 12)
        self.assertIn("99", rendered)
        self.assertNotIn("0", rendered)

    def test_order_cards_do_not_embed_child_windows(self) -> None:
        self.cards.set_rows([_row(index) for index in range(100)])
        self.root.update()

        item_types = {
            self.cards.canvas.type(item)
            for item in self.cards.canvas.find_all()
        }
        self.assertNotIn("window", item_types)

    def test_resize_reflows_rendered_cards_to_new_width(self) -> None:
        self.root.geometry("1000x650")
        self.cards.set_rows([_row(index) for index in range(10)])
        self.root.update()
        before = max(
            bbox[2]
            for item in self.cards.canvas.find_all()
            if (bbox := self.cards.canvas.bbox(item)) is not None
        )

        self.root.geometry("1500x650")
        self.root.update()

        after = max(
            bbox[2]
            for item in self.cards.canvas.find_all()
            if (bbox := self.cards.canvas.bbox(item)) is not None
        )
        self.assertGreater(after, before + 300)

    def test_product_image_is_vertically_centered_in_body_cell(self) -> None:
        self.cards.set_rows([_row(0)])
        self.root.update()
        image_box = next(
            item
            for item in self.cards.canvas.find_all()
            if self.cards.canvas.type(item) == "rectangle"
            and self.cards.canvas.itemcget(item, "fill") == "#eef2f7"
        )
        bbox = self.cards.canvas.bbox(image_box)
        assert bbox is not None
        center_y = (bbox[1] + bbox[3]) / 2
        expected_body_center = (42 + 184 - 8) / 2
        self.assertAlmostEqual(center_y, expected_body_center, delta=1)

    def test_canvas_action_button_still_dispatches_business_action(self) -> None:
        self.cards.set_rows([_row(0)])
        self.root.update()
        button_item = next(
            item
            for item in self.cards.canvas.find_all()
            if self.cards.canvas.type(item) == "text"
            and self.cards.canvas.itemcget(item, "text") == "补录采购订单"
        )
        bbox = self.cards.canvas.bbox(button_item)
        assert bbox is not None
        x = (bbox[0] + bbox[2]) // 2
        y = (bbox[1] + bbox[3]) // 2

        self.cards.canvas.event_generate("<Button-1>", x=x, y=y)
        self.root.update()

        self.assertEqual(self.actions[0][0], "manual_purchase")
        self.assertEqual(self.actions[0][1]["row"]["order_id"], "ORDER-0000")


if __name__ == "__main__":
    unittest.main()
