from __future__ import annotations

import json

from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QLabel, QMessageBox, QPushButton

from ..widgets import DataTable, PrimaryButton
from .common import TaskPage


class HostedPage(TaskPage):
    def __init__(self, workspace) -> None:
        super().__init__(
            workspace,
            "托管中心",
            "多店运营配置、并行执行和每日定时",
            stale_seconds=20,
        )
        toolbar = QHBoxLayout()
        self.revoke = QCheckBox("撤销审核")
        self.delete_failed = QCheckBox("删除审核失败")
        self.list_products = QCheckBox("自动上架")
        self.prefill = QCheckBox("双11预填报")
        self.save_button = QPushButton("保存启用项")
        self.execute_button = PrimaryButton("立即执行")
        self.refresh_button = QPushButton("刷新")
        for widget in (
            self.revoke,
            self.delete_failed,
            self.list_products,
            self.prefill,
            self.save_button,
            self.execute_button,
            self.refresh_button,
        ):
            toolbar.addWidget(widget)
        toolbar.addStretch(1)
        self.root_layout.addLayout(toolbar)
        self.table = DataTable(
            [
                ("name", "店铺"),
                ("enabled", "托管"),
                ("revoke", "撤审"),
                ("delete", "删失败"),
                ("listing", "上架"),
                ("prefill", "预填报"),
                ("last_run", "最近执行"),
            ]
        )
        self.table.setSelectionMode(DataTable.SelectionMode.ExtendedSelection)
        self.root_layout.addWidget(self.table, 1)
        self.summary = QLabel()
        self.summary.setObjectName("页面说明")
        self.root_layout.addWidget(self.summary)
        self.save_button.clicked.connect(self._save)
        self.execute_button.clicked.connect(self._execute)
        self.refresh_button.clicked.connect(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        self._stores = list(self.workspace.db.list_stores())
        rows = []
        for store in self._stores:
            config = self.workspace.db.get_hosted_config(store.id) or {}
            rows.append(
                {
                    **config,
                    "id": store.id,
                    "name": store.name,
                    "enabled": "启用" if config.get("enabled") else "停用",
                    "revoke": "✓" if config.get("revoke_audit") else "",
                    "delete": "✓" if config.get("delete_audit_failed") else "",
                    "listing": "✓" if config.get("list_products") else "",
                    "prefill": "✓" if config.get("prefill_11") else "",
                    "last_run": self.workspace.db.get_setting("hosted_last_run_date", "—"),
                }
            )
        self.table.set_rows(rows)
        self.summary.setText(f"托管店铺 {len(rows)} 家")
        super().refresh()

    def _save(self) -> None:
        rows = [
            self.table.model_data.row_at(index.row())
            for index in self.table.selectionModel().selectedRows()
        ]
        ids = {int(row["id"]) for row in rows if row} or {store.id for store in self._stores}
        changes = {
            "revoke_audit": self.revoke.isChecked(),
            "delete_audit_failed": self.delete_failed.isChecked(),
            "list_products": self.list_products.isChecked(),
            "prefill_11": self.prefill.isChecked(),
            "enabled": any(
                (
                    self.revoke.isChecked(),
                    self.delete_failed.isChecked(),
                    self.list_products.isChecked(),
                    self.prefill.isChecked(),
                )
            ),
        }

        def work() -> int:
            for store_id in ids:
                self.workspace.db.update_hosted_config(
                    store_id,
                    **changes,
                )
            return len(ids)

        def done(count: int) -> None:
            self.refresh()
            self.notify.emit(
                "已保存",
                f"{count} 家店铺托管配置",
            )

        self.submit(
            "hosted:save-configs",
            work,
            done,
            self.save_button,
        )

    def _execute(self) -> None:
        rows = [
            self.table.model_data.row_at(index.row())
            for index in self.table.selectionModel().selectedRows()
        ]
        rows = [row for row in rows if row]
        if not rows:
            model = self.table.model_data
            rows = [
                row
                for row in (model.row_at(index) for index in range(model.rowCount()))
                if row and row.get("enabled") == "启用"
            ]
        if not rows:
            self.notify.emit("提示", "没有选中的托管店铺")
            return
        answer = QMessageBox.question(
            self,
            "确认托管执行",
            f"即将对 {len(rows)} 家店铺执行已启用的托管动作。是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.submit(
            "hosted:execute",
            lambda: self._execute_rows(rows),
            lambda errors: self._after(errors),
            self.execute_button,
        )

    def _execute_rows(self, rows: list[dict]) -> list[str]:
        errors = []
        for row in rows:
            store = next((item for item in self._stores if item.id == int(row["id"])), None)
            if store is None:
                continue
            config = self.workspace.db.get_hosted_config(store.id) or {}
            actions = []
            try:
                products = self.workspace.db.list_products(store_id=store.id)
                blacklist = self.workspace.db.violation_blacklist_ids()
                products = [
                    item for item in products if str(item.get("product_id")) not in blacklist
                ]
                if config.get("revoke_audit"):
                    ids = [
                        str(item["product_id"])
                        for item in products
                        if int(item.get("status") or 0) == 2
                    ][:500]
                    for product_id in ids:
                        self.workspace.service.product_write(
                            store,
                            "cancel_audit",
                            product_id=product_id,
                        )
                    actions.append(f"撤审 {len(ids)}")
                if config.get("delete_audit_failed"):
                    ids = [
                        str(item["product_id"])
                        for item in products
                        if int(item.get("status") or 0) == 3
                    ][:500]
                    for product_id in ids:
                        self.workspace.service.product_write(
                            store,
                            "delete",
                            product_id=product_id,
                        )
                    actions.append(f"删失败 {len(ids)}")
                if config.get("list_products"):
                    ids = [
                        str(item["product_id"])
                        for item in products
                        if int(item.get("status") or 0) == 11
                    ][:200]
                    if ids:
                        self.workspace.service.set_products_listing(store, ids, True)
                    actions.append(f"上架 {len(ids)}")
                if config.get("prefill_11"):
                    raw = self.workspace.db.get_setting(
                        f"double11_prefill_{store.id}",
                        "[]",
                    )
                    drafts = json.loads(raw)
                    if isinstance(drafts, list) and drafts:
                        self.workspace.service.double11_submit(store, drafts)
                        actions.append(f"预填报 {len(drafts)}")
                self.workspace.db.log_hosted_run(
                    store.id,
                    "、".join(actions) or "无动作",
                    "success",
                    "PySide6 手动执行",
                )
            except Exception as exc:
                errors.append(f"{store.name}: {exc}")
                self.workspace.db.log_hosted_run(
                    store.id,
                    "、".join(actions) or "执行失败",
                    "failed",
                    str(exc),
                )
        return errors

    def _after(self, errors: list[str]) -> None:
        self.refresh()
        self.notify.emit("托管执行完成", f"问题 {len(errors)} 条")
