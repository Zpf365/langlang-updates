from __future__ import annotations

import tkinter as tk
import unittest
from decimal import Decimal

from weimi_manager.ui.order_action_dialogs import (
    ManualShipmentDialog,
    ProductTitleEditDialog,
    PurchaseInfoDialog,
)
from weimi_manager.ui.procurement_account_dialog import (
    ProcurementAccountDialog,
    ProcurementPasswordDialog,
)


class OrderActionDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        try:
            cls.root = tk.Tk()
        except tk.TclError as exc:
            raise unittest.SkipTest(str(exc)) from exc
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.root.destroy()

    def test_product_title_dialog_submits_title(self) -> None:
        values: list[str] = []
        dialog = ProductTitleEditDialog(
            self.root,
            "原商品标题",
            values.append,
        )
        try:
            dialog.text.delete("1.0", "end")
            dialog.text.insert("1.0", "新商品标题")
            dialog._save()
            self.assertEqual(values, ["新商品标题"])
        finally:
            if dialog.winfo_exists():
                dialog.destroy()

    def test_purchase_info_dialog_submits_all_fields(self) -> None:
        values: list[dict] = []
        dialog = PurchaseInfoDialog(
            self.root,
            order_id="ORDER-1",
            on_save=values.append,
            initial={"transfer_warehouse": "否"},
        )
        try:
            dialog.platform.set("1688")
            dialog.purchase_order_no.set("PURCHASE-1")
            dialog.purchase_amount.set("12.50")
            dialog.commission.set("5")
            dialog._save()
            self.assertEqual(values[0]["platform"], "1688")
            self.assertEqual(values[0]["purchase_order_no"], "PURCHASE-1")
            self.assertEqual(values[0]["purchase_amount"], Decimal("12.50"))
            self.assertEqual(values[0]["commission"], Decimal("5"))
            self.assertFalse(values[0]["transfer_warehouse"])
        finally:
            if dialog.winfo_exists():
                dialog.destroy()

    def test_manual_shipment_dialog_submits_store_package(self) -> None:
        values: list[list[dict[str, str]]] = []
        dialog = ManualShipmentDialog(
            self.root,
            order_id="ORDER-1",
            on_save=values.append,
        )
        try:
            _row, company, tracking = dialog.package_rows[0]
            company.set("中通快递")
            tracking.set("TRACK-1")
            dialog._save()
            self.assertEqual(
                values,
                [[{"delivery_id": "中通快递", "waybill_id": "TRACK-1"}]],
            )
        finally:
            if dialog.winfo_exists():
                dialog.destroy()

    def test_procurement_account_dialog_submits_account_and_note(self) -> None:
        values: list[dict[str, str]] = []
        dialog = ProcurementAccountDialog(
            self.root,
            platform_label="1688",
            on_save=values.append,
        )
        try:
            dialog.account_var.set("buyer-1")
            dialog.password_var.set("secret")
            dialog.remark_text.insert("1.0", "采购备注")
            dialog._save()
            self.assertEqual(values[0]["account"], "buyer-1")
            self.assertEqual(values[0]["password"], "secret")
            self.assertEqual(values[0]["remark"], "采购备注")
        finally:
            if dialog.winfo_exists():
                dialog.destroy()

    def test_procurement_password_dialog_submits_password(self) -> None:
        values: list[str] = []
        dialog = ProcurementPasswordDialog(
            self.root,
            title="设置账号密码",
            field_label="新密码",
            on_save=values.append,
        )
        try:
            dialog.value_var.set("new-secret")
            dialog._save()
            self.assertEqual(values, ["new-secret"])
        finally:
            if dialog.winfo_exists():
                dialog.destroy()


if __name__ == "__main__":
    unittest.main()
