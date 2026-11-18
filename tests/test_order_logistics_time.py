from __future__ import annotations

import unittest

from weimi_manager.ui.pages import OrdersPage


class OrderLogisticsTimeTests(unittest.TestCase):
    def test_shipped_order_does_not_keep_shipping_deadline(self) -> None:
        now = 1_800_000_000
        text, overdue = OrdersPage._order_logistics_display(
            {
                "status": 30,
                "pay_time": now - 10 * 86400,
                "create_time": now - 10 * 86400,
                "update_time": now - 3600,
            },
            {},
            "shipped",
            now_ts=now,
        )

        self.assertFalse(overdue)
        self.assertNotIn("已超时", text)

    def test_completed_order_does_not_keep_shipping_deadline(self) -> None:
        now = 1_800_000_000
        text, overdue = OrdersPage._order_logistics_display(
            {
                "status": 100,
                "pay_time": now - 20 * 86400,
                "create_time": now - 20 * 86400,
                "update_time": now - 120,
            },
            {},
            "completed",
            now_ts=now,
        )

        self.assertFalse(overdue)
        self.assertNotIn("已超时", text)

    def test_pending_shipment_uses_payment_time_for_deadline(self) -> None:
        now = 1_800_000_000
        text, overdue = OrdersPage._order_logistics_display(
            {
                "status": 20,
                "pay_time": now - 3 * 86400,
                "create_time": now - 5 * 86400,
                "update_time": now,
            },
            {},
            "purchased_pending_shipment",
            now_ts=now,
        )

        self.assertTrue(overdue)
        self.assertTrue(text.startswith("已超时"))

    def test_unshipped_order_within_two_days_shows_remaining_time(self) -> None:
        now = 1_800_000_000
        text, overdue = OrdersPage._order_logistics_display(
            {
                "status": 20,
                "pay_time": now - 3600,
                "create_time": now - 7200,
                "update_time": now,
            },
            {},
            "pending_purchase",
            now_ts=now,
        )

        self.assertFalse(overdue)
        self.assertTrue(text.startswith("剩余"))


if __name__ == "__main__":
    unittest.main()
