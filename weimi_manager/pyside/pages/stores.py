from __future__ import annotations

import json
from typing import Any

from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from ...models import Store
from ...ui.store_page import StorePage as LegacyStorePage
from ..widgets import DataTable, PrimaryButton
from .common import TaskPage


class StoreDialog(QDialog):
    def __init__(self, parent=None, store: Store | None = None) -> None:
        super().__init__(parent)
        self.store = store
        self.setWindowTitle("编辑店铺" if store else "添加店铺")
        self.setMinimumWidth(460)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.name_input = QLineEdit(store.name if store else "")
        self.app_id_input = QLineEdit(store.app_id if store else "")
        self.secret_input = QLineEdit()
        self.secret_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.secret_input.setPlaceholderText("编辑时留空表示保持原密钥")
        self.group_input = QLineEdit(store.group_name if store else "")
        form.addRow("店铺名称：", self.name_input)
        form.addRow("微信 AppID：", self.app_id_input)
        form.addRow("AppSecret：", self.secret_input)
        form.addRow("店铺分组：", self.group_input)
        layout.addLayout(form)
        hint = QLabel("AppSecret 使用 Windows DPAPI 加密保存；可先仅配置 AppID 进行网页扫码登录。")
        hint.setWordWrap(True)
        hint.setObjectName("页面说明")
        layout.addWidget(hint)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values(self) -> dict[str, str]:
        return {
            "name": self.name_input.text().strip(),
            "app_id": self.app_id_input.text().strip(),
            "secret": self.secret_input.text(),
            "group_name": self.group_input.text().strip(),
        }


class BatchStoreDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("批量导入店铺")
        self.resize(720, 500)
        layout = QVBoxLayout(self)
        hint = QLabel(
            "一行一家：店铺名称 AppID AppSecret；AppSecret 可留空。重复 AppID 会更新原记录。"
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.editor = QPlainTextEdit()
        self.editor.setPlaceholderText("示例：\n店铺名称 wx1234567890abcdef 32位AppSecret")
        layout.addWidget(self.editor, 1)
        self.group_input = QLineEdit()
        self.group_input.setPlaceholderText("统一分组，可留空")
        layout.addWidget(self.group_input)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class StoresPage(TaskPage):
    FIELD_DEFINITIONS = LegacyStorePage.field_definitions

    def __init__(self, workspace) -> None:
        super().__init__(
            workspace,
            "店铺管理",
            "维护 AppID / AppSecret、登录态和经营数据",
            stale_seconds=10,
        )
        self._stores: list[Store] = []
        dimensions = {"selected": 38, "sequence": 54}
        widths = [
            *[
                (key, "选" if key == "selected" else "序号", width)
                for key, width in dimensions.items()
            ],
            *[(key, label, width) for key, label, width in self.FIELD_DEFINITIONS],
        ]
        columns = [(key, label) for key, label, _width in widths]
        self._columns = columns

        toolbar = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("搜索店铺名称、AppID、昵称或分组")
        self.search_input.setClearButtonEnabled(True)
        self.group_combo = QComboBox()
        self.group_combo.addItem("全部分组")
        self.refresh_button = QPushButton("刷新")
        self.refresh_button.clicked.connect(self.refresh)
        self.add_button = PrimaryButton("添加店铺")
        self.add_button.clicked.connect(self._add_store)
        self.batch_button = QPushButton("批量导入")
        self.batch_button.clicked.connect(self._batch_import)
        toolbar.addWidget(self.search_input, 1)
        toolbar.addWidget(self.group_combo)
        toolbar.addWidget(self.refresh_button)
        toolbar.addWidget(self.batch_button)
        toolbar.addWidget(self.add_button)
        self.root_layout.addLayout(toolbar)

        actions = QHBoxLayout()
        self.validate_button = QPushButton("验证接口")
        self.scan_button = PrimaryButton("扫码登录")
        self.probe_button = QPushButton("检测登录")
        self.backend_button = QPushButton("打开后台")
        self.edit_button = QPushButton("编辑")
        self.copy_button = QPushButton("复制导出")
        self.delete_button = QPushButton("删除")
        self.delete_button.setObjectName("危险按钮")
        for button in (
            self.validate_button,
            self.scan_button,
            self.probe_button,
            self.backend_button,
            self.edit_button,
            self.copy_button,
            self.delete_button,
        ):
            actions.addWidget(button)
        actions.addStretch(1)
        self.root_layout.addLayout(actions)
        self.validate_button.clicked.connect(self._validate_store)
        self.scan_button.clicked.connect(self._scan_login)
        self.probe_button.clicked.connect(self._probe_login)
        self.backend_button.clicked.connect(self._open_backend)
        self.edit_button.clicked.connect(self._edit_store)
        self.copy_button.clicked.connect(self._copy_export)
        self.delete_button.clicked.connect(self._delete_store)

        self.table = DataTable(columns)
        self.table.setSelectionMode(DataTable.SelectionMode.ExtendedSelection)
        for index, (_key, _label, width) in enumerate(widths):
            self.table.setColumnWidth(index, width)
        self.table.row_activated.connect(lambda _row: self._edit_store())
        self.root_layout.addWidget(self.table, 1)
        self.summary = QLabel("保存时只展示有效业务数据，AppSecret 与 Cookie 加密存储。")
        self.summary.setObjectName("页面说明")
        self.root_layout.addWidget(self.summary)
        self.search_input.textChanged.connect(self._render)
        self.group_combo.currentTextChanged.connect(self._render)
        self.refresh()

    def refresh(self) -> None:
        try:
            self._stores = list(self.workspace.db.list_stores())
        except Exception as exc:
            self.notify.emit("店铺加载失败", str(exc))
            super().refresh()
            return
        groups = sorted({store.group_name for store in self._stores if store.group_name})
        current = self.group_combo.currentText()
        self.group_combo.blockSignals(True)
        self.group_combo.clear()
        self.group_combo.addItem("全部分组")
        self.group_combo.addItems(groups)
        self.group_combo.setCurrentIndex(max(0, self.group_combo.findText(current)))
        self.group_combo.blockSignals(False)
        self._render()
        super().refresh()

    def _render(self) -> None:
        term = self.search_input.text().strip().casefold()
        group = self.group_combo.currentText()
        rows = []
        for index, store in enumerate(self._stores):
            text = " ".join(
                (
                    store.name,
                    store.app_id,
                    store.group_name,
                    store.web_status,
                )
            ).casefold()
            if term and term not in text:
                continue
            if group != "全部分组" and store.group_name != group:
                continue
            values = {
                "selected": "",
                "sequence": len(rows) + 1,
                "name": store.name,
                "group": store.group_name or "未分组",
                "app_id": store.app_id,
                "secret": "••••••" if store.id else "",
                "login": store.web_status,
                "_store_id": store.id,
            }
            metrics = self._metrics(store)
            for key, _label, _width in self.FIELD_DEFINITIONS:
                values.setdefault(key, metrics.get(key, "—"))
            rows.append(values)
        self.table.set_rows(rows)
        self.summary.setText(
            f"显示 {len(rows)} / {len(self._stores)} 家店铺 · 42 个数据字段可用（包含序号与选择列）"
        )

    def _metrics(self, store: Store) -> dict[str, Any]:
        try:
            metrics = json.loads(self.workspace.db.get_setting(f"store_metrics_{store.id}", "{}"))
        except Exception:
            metrics = {}
        summary = self.workspace.db.store_summary(store.id)
        values = {
            "in_sale": summary.get("in_sale", 0),
            "warehouse": summary.get("warehouse", 0),
            "product_total": summary.get("product_total", 0),
        }
        for key, value in metrics.items():
            if isinstance(value, int) and key in LegacyStorePage.money_fields:
                values[key] = f"¥{value / 100:.2f}"
            elif key in LegacyStorePage.percent_fields:
                values[key] = f"{float(value or 0):.2f}%"
            else:
                values[key] = str(value if value is not None else "—")
        values["login"] = LegacyStorePage._display_login_status(store)
        values["stats_at"] = str(metrics.get("updated_at") or "—")[:16]
        return values

    def _selected_store(self) -> Store | None:
        indexes = self.table.selectionModel().selectedRows()
        if not indexes:
            return None
        row = self.table.model_data.row_at(indexes[0].row())
        store_id = int(row.get("_store_id") or 0) if row else 0
        return next((store for store in self._stores if store.id == store_id), None)

    def _selected_stores(self) -> list[Store]:
        result = []
        for index in self.table.selectionModel().selectedRows():
            row = self.table.model_data.row_at(index.row())
            store_id = int(row.get("_store_id") or 0) if row else 0
            store = next((item for item in self._stores if item.id == store_id), None)
            if store is not None:
                result.append(store)
        return result

    def _add_store(self) -> None:
        dialog = StoreDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        values = dialog.values()
        if not values["name"] or not values["app_id"]:
            QMessageBox.warning(self, "保存失败", "店铺名称和 AppID 不能为空。")
            return
        self.submit(
            "store:create",
            lambda: self.workspace.db.add_store(
                values["name"],
                values["app_id"],
                values["secret"],
                values["group_name"],
            ),
            lambda _result: self._after_operation(
                "店铺已添加，密钥已加密保存"
            ),
        )

    def _edit_store(self) -> None:
        store = self._selected_store()
        if store is None:
            self.notify.emit("提示", "请先选择一家店铺")
            return
        dialog = StoreDialog(self, store)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        values = dialog.values()
        self.submit(
            f"store:edit:{store.id}",
            lambda: self.workspace.db.update_store(
                store.id,
                values["name"],
                values["group_name"],
                values["secret"] or None,
            ),
            lambda _result: self._after_operation("店铺资料已更新"),
        )

    def _delete_store(self) -> None:
        store = self._selected_store()
        if store is None:
            return
        answer = QMessageBox.question(
            self,
            "删除店铺",
            f"确定删除“{store.name}”吗？本地订单和商品缓存也会一并删除。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        delete_one = getattr(
            self.workspace.db,
            "delete_store_for_app_id",
            None,
        )
        def operation():
            if callable(delete_one):
                return delete_one(store.id, store.app_id)
            return self.workspace.db.delete_store(store.id)

        self.submit(
            f"store:delete:{store.app_id}",
            operation,
            lambda _result: self._after_operation("店铺已从云端和本机删除"),
        )

    def _copy_export(self) -> None:
        stores = self._selected_stores() or (
            [self._selected_store()] if self._selected_store() else []
        )
        if not stores:
            return
        lines = []
        for store in stores:
            _current, secret = self.workspace.db.get_store_secret(store.id)
            lines.append(f"{store.app_id} {secret}")
        QApplication.clipboard().setText("\n".join(lines))
        self.notify.emit("已复制", f"已复制 {len(stores)} 家店铺的 AppID / AppSecret")

    def _batch_import(self) -> None:
        dialog = BatchStoreDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        records = []
        errors = []
        for line_number, line in enumerate(dialog.editor.toPlainText().splitlines(), 1):
            if not line.strip():
                continue
            parsed = __import__(
                "weimi_manager.ui.store_page",
                fromlist=["parse_store_batch_line"],
            ).parse_store_batch_line(line)
            if not parsed or not parsed[1]:
                errors.append(f"第 {line_number} 行无法识别 AppID")
                continue
            records.append(parsed)
        if records:
            group_name = dialog.group_input.text().strip()

            def imported(result):
                self._after_operation(
                    f"批量导入完成：新增 {result[0]}，更新 {result[1]}，"
                    f"失败 {len(result[2]) + len(errors)}"
                )

            self.submit(
                "store:batch-import",
                lambda: self.workspace.db.upsert_stores(
                    records,
                    group_name,
                ),
                imported,
            )

    def _validate_store(self) -> None:
        store = self._selected_store()
        if store is None:
            return

        def work():
            self.workspace.service.validate_store(store)
            self.workspace.service.sync_store_profile(store)
            return self.workspace.db.get_store_profile(store.id) or {}

        self.submit(
            f"store:validate:{store.id}",
            work,
            lambda _result: self._after_operation("接口验证成功"),
            self.validate_button,
        )

    def _scan_login(self) -> None:
        stores = self._selected_stores()
        if not stores:
            self.notify.emit("提示", "请先选择要登录的店铺")
            return
        pending = {store.id for store in stores if store.web_status != "已登录"} or {
            store.id for store in stores
        }
        host = next(store for store in stores if store.id in pending)

        def work():
            return self.workspace.web_session.interactive_login(
                host,
                allowed_store_ids=pending,
            )

        self.submit(
            f"store:scan:{','.join(map(str, sorted(pending)))}",
            work,
            lambda result: self._after_operation(
                f"扫码完成，保存 {len(result.get('matched_store_ids') or [])} 家店铺"
            ),
            self.scan_button,
            cancelable=True,
        )

    def _probe_login(self) -> None:
        store = self._selected_store()
        if store is None:
            return
        self.submit(
            f"store:probe:{store.id}",
            lambda: self.workspace.web_session.probe(store),
            lambda result: self._after_operation(f"登录状态：{result[0]} · {result[1]}"),
            self.probe_button,
        )

    def _open_backend(self) -> None:
        store = self._selected_store()
        if store is None:
            return
        self.submit(
            f"store:backend:{store.id}",
            lambda: self.workspace.web_session.open_official_backend(store),
            lambda _result: self._after_operation("微信商家后台已关闭"),
            self.backend_button,
        )

    def _after_operation(self, message: str) -> None:
        self.refresh()
        self.notify.emit("操作完成", message)
