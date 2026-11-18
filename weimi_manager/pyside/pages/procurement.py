from __future__ import annotations

import json
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from ..widgets import DataTable, PrimaryButton
from .common import TaskPage
from .dialogs import PayloadDialog


class AccountDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("新增采购账号")
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.platform = QComboBox()
        self.platform.addItems(("淘宝", "1688"))
        self.label = QLineEdit()
        self.login_id = QLineEdit()
        form.addRow("平台：", self.platform)
        form.addRow("账号备注：", self.label)
        form.addRow("登录账号：", self.login_id)
        layout.addLayout(form)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values(self) -> tuple[str, str, str]:
        return (
            "taobao" if self.platform.currentIndex() == 0 else "1688",
            self.label.text().strip(),
            self.login_id.text().strip(),
        )


class ProcurementPage(TaskPage):
    def __init__(self, workspace) -> None:
        super().__init__(
            workspace,
            "采购中心",
            "淘宝、1688采购账号、订单、物流、取件码和本地备注",
            stale_seconds=20,
        )
        top = QHBoxLayout()
        self.add_button = PrimaryButton("新增账号")
        self.login_button = QPushButton("扫码登录")
        self.browse_button = QPushButton("打开采购网页")
        self.sync_button = QPushButton("同步账号")
        self.sync_all_button = QPushButton("同步全部")
        self.clear_session_button = QPushButton("清理会话")
        self.delete_account_button = QPushButton("删除账号")
        for widget in (
            self.add_button,
            self.login_button,
            self.browse_button,
            self.sync_button,
            self.sync_all_button,
            self.clear_session_button,
            self.delete_account_button,
        ):
            top.addWidget(widget)
        top.addStretch(1)
        self.root_layout.addLayout(top)
        self.add_button.clicked.connect(self._add_account)
        self.login_button.clicked.connect(self._login_account)
        self.browse_button.clicked.connect(self._browse_account)
        self.sync_button.clicked.connect(self._sync_account)
        self.sync_all_button.clicked.connect(self._sync_all)
        self.clear_session_button.clicked.connect(self._clear_session)
        self.delete_account_button.clicked.connect(self._delete_account)

        splitter = QSplitter(Qt.Orientation.Vertical)
        account_panel = QWidget()
        account_layout = QVBoxLayout(account_panel)
        account_layout.setContentsMargins(0, 0, 0, 0)
        account_layout.addWidget(QLabel("采购账号"))
        self.account_table = DataTable(
            [
                ("id", "序号"),
                ("platform", "平台"),
                ("label", "账号备注"),
                ("login_id", "登录账号"),
                ("status", "登录状态"),
                ("last_login", "最近登录"),
                ("last_sync", "最近同步"),
                ("last_error", "最近错误"),
            ]
        )
        self.account_table.setMaximumHeight(210)
        self.account_table.selectionModel().selectionChanged.connect(
            lambda _selected, _deselected: self._load_orders()
        )
        account_layout.addWidget(self.account_table)
        order_panel = QWidget()
        order_layout = QVBoxLayout(order_panel)
        order_layout.setContentsMargins(0, 0, 0, 0)
        order_tools = QHBoxLayout()
        self.store_combo = QComboBox()
        self.match_button = QPushButton("按运单号自动关联")
        self.manual_button = QPushButton("手工关联")
        self.note_button = QPushButton("编辑本地备注")
        self.sync_note_button = QPushButton("同步微信备注")
        self.raw_button = QPushButton("查看采购 JSON")
        for widget in (
            self.match_button,
            self.manual_button,
            self.note_button,
            self.sync_note_button,
            self.raw_button,
        ):
            order_tools.addWidget(widget)
        order_layout.addLayout(order_tools)
        self.order_table = DataTable(
            [
                ("platform", "平台"),
                ("trade_no", "采购订单号"),
                ("mail_no", "物流单号"),
                ("goods_title", "商品"),
                ("goods_count", "数量"),
                ("pay_amount", "实付金额"),
                ("status_text", "采购状态"),
                ("pickup_code", "取件码"),
            ]
        )
        order_layout.addWidget(self.order_table, 1)
        self.summary = QLabel()
        self.summary.setObjectName("页面说明")
        order_layout.addWidget(self.summary)
        splitter.addWidget(account_panel)
        splitter.addWidget(order_panel)
        splitter.setSizes([230, 430])
        self.root_layout.addWidget(splitter, 1)
        self.match_button.clicked.connect(self._auto_match)
        self.manual_button.clicked.connect(self._manual_link)
        self.note_button.clicked.connect(self._edit_note)
        self.sync_note_button.clicked.connect(self._sync_note)
        self.raw_button.clicked.connect(self._show_raw)
        self._load_store_choices()
        self.refresh()

    def _load_store_choices(self) -> None:
        current = self.store_combo.currentData()
        self.store_combo.blockSignals(True)
        self.store_combo.clear()
        self.store_combo.addItem("选择关联店铺", None)
        self._stores = list(self.workspace.db.list_stores())
        for store in self._stores:
            self.store_combo.addItem(store.name, store.id)
        self.store_combo.setCurrentIndex(max(0, self.store_combo.findData(current)))
        self.store_combo.blockSignals(False)

    def refresh(self) -> None:
        self._load_store_choices()
        accounts = self.workspace.db.list_procurement_accounts()
        selected = self.account_table.selected_row()
        selected_id = int(selected["id"]) if selected else None
        self.account_table.set_rows(
            [{**row, "platform": str(row.get("platform") or "")} for row in accounts]
        )
        if selected_id is not None:
            for index, row in enumerate(accounts):
                if int(row["id"]) == selected_id:
                    self.account_table.selectRow(index)
                    break
        self._load_orders()
        super().refresh()

    def _selected_account(self) -> dict[str, Any] | None:
        return self.account_table.selected_row()

    def _load_orders(self) -> None:
        account = self._selected_account()
        account_id = int(account["id"]) if account else None
        rows = (
            self.workspace.db.list_procurement_orders(account_id=account_id)
            if account_id is not None
            else []
        )
        self.order_table.set_rows(
            [
                {
                    **row,
                    "pay_amount": f"¥{int(row.get('pay_amount') or 0) / 100:.2f}",
                }
                for row in rows
            ]
        )
        self.summary.setText(f"采购订单 {len(rows)} 条")

    def _add_account(self) -> None:
        dialog = AccountDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        platform, label, login_id = dialog.values()
        if not label:
            QMessageBox.warning(self, "保存失败", "账号备注不能为空。")
            return
        self.workspace.db.add_procurement_account(platform, label, login_id)
        self.refresh()

    def _login_account(self) -> None:
        account = self._selected_account()
        if not account:
            return
        self.submit(
            f"procurement:login:{account['id']}",
            lambda: self.workspace.service.procurement_login(int(account["id"])),
            lambda _result: self._after("采购账号登录完成"),
            self.login_button,
            cancelable=True,
        )

    def _browse_account(self) -> None:
        account = self._selected_account()
        if not account:
            return
        self.submit(
            f"procurement:browse:{account['id']}",
            lambda: self.workspace.service.procurement_browse(int(account["id"])),
            lambda result: self._after(getattr(result, "message", "采购网页已关闭")),
            self.browse_button,
        )

    def _sync_account(self) -> None:
        account = self._selected_account()
        if not account:
            return
        self.submit(
            f"procurement:sync:{account['id']}",
            lambda: self.workspace.service.sync_procurement_account(int(account["id"])),
            lambda result: self._after(getattr(result, "message", "采购同步完成")),
            self.sync_button,
            cancelable=True,
        )

    def _sync_all(self) -> None:
        accounts = self.workspace.db.list_procurement_accounts()
        if not accounts:
            return

        def work():
            errors = []
            for account in accounts:
                try:
                    self.workspace.service.sync_procurement_account(int(account["id"]))
                except Exception as exc:
                    errors.append(f"{account['label']}: {exc}")
            return errors

        self.submit(
            "procurement:sync-all",
            work,
            lambda errors: self._after(f"采购同步完成，失败 {len(errors)} 个账号"),
            self.sync_all_button,
            cancelable=True,
        )

    def _after(self, message: str) -> None:
        self.refresh()
        self.notify.emit("操作完成", message)

    def _clear_session(self) -> None:
        account = self._selected_account()
        if not account:
            return
        self.workspace.db.update_procurement_account(
            int(account["id"]),
            status="未登录",
            last_error="登录态已清理",
        )
        self.workspace.db.set_procurement_session(int(account["id"]), {})
        self.refresh()

    def _delete_account(self) -> None:
        account = self._selected_account()
        if not account:
            return
        answer = QMessageBox.question(
            self,
            "删除采购账号",
            f"确定删除采购账号“{account['label']}”及其会话吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.workspace.db.delete_procurement_account(int(account["id"]))
            self.refresh()

    def _selected_store(self):
        store_id = self.store_combo.currentData()
        return next((store for store in self._stores if store.id == store_id), None)

    def _auto_match(self) -> None:
        store = self._selected_store()
        if store is None:
            self.notify.emit("提示", "请选择关联店铺")
            return
        self.submit(
            f"procurement:match:{store.id}",
            lambda: self.workspace.service.match_procurement_orders(store),
            lambda count: self._after(f"自动关联完成，匹配 {count} 条"),
            self.match_button,
        )

    def _manual_link(self) -> None:
        procurement = self.order_table.selected_row()
        store = self._selected_store()
        if not procurement or store is None:
            return
        order_id, accepted = QInputDialog.getText(
            self,
            "手工关联",
            "微信订单号：",
        )
        if not accepted or not order_id.strip():
            return
        if self.workspace.db.get_order(store.id, order_id.strip()) is None:
            QMessageBox.warning(self, "关联失败", "本地没有该订单。")
            return
        self.workspace.db.upsert_procurement_link(
            store.id,
            order_id.strip(),
            int(procurement["id"]),
            match_type="manual",
            confidence=100,
        )
        self._after("手工关联已保存")

    def _edit_note(self) -> None:
        procurement = self.order_table.selected_row()
        store = self._selected_store()
        if not procurement or store is None:
            return
        order_id, accepted = QInputDialog.getText(self, "采购关联", "微信订单号：")
        if not accepted or not order_id.strip():
            return
        links = self.workspace.db.list_procurement_links(store.id, order_id.strip())
        link = next(
            (row for row in links if int(row["procurement_order_id"]) == int(procurement["id"])),
            None,
        )
        if link is None:
            QMessageBox.warning(self, "无关联", "请先为该采购单建立微信订单关联。")
            return
        note, accepted = QInputDialog.getText(
            self,
            "本地采购备注",
            "备注：",
            text=str(link.get("local_note") or ""),
        )
        if not accepted:
            return
        self.workspace.db.upsert_procurement_link(
            store.id,
            order_id.strip(),
            int(procurement["id"]),
            match_type=str(link.get("match_type") or "manual"),
            confidence=int(link.get("confidence") or 100),
            local_note=note,
        )
        self._after("本地备注已保存")

    def _sync_note(self) -> None:
        store = self._selected_store()
        if store is None:
            return
        order_id, accepted = QInputDialog.getText(self, "同步微信备注", "微信订单号：")
        if not accepted or not order_id.strip():
            return
        self.submit(
            f"procurement:note:{store.id}:{order_id.strip()}",
            lambda: self.workspace.service.sync_procurement_note_to_wechat(
                store,
                order_id.strip(),
            ),
            lambda result: self._after(getattr(result, "message", "备注已同步")),
            self.sync_note_button,
        )

    def _show_raw(self) -> None:
        row = self.order_table.selected_row()
        if not row:
            return
        try:
            raw = json.loads(row.get("raw_json") or "{}")
        except ValueError:
            raw = {}
        PayloadDialog("采购订单 JSON", raw if isinstance(raw, dict) else {}, self).exec()
