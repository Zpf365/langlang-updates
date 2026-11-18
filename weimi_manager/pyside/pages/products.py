from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from ...exporter import export_xlsx
from ...models import Product, product_matches_tab
from ..widgets import DataTable, PrimaryButton
from .common import TaskPage

TABS = (
    ("sales", "销售中"),
    ("audit", "审核中"),
    ("failed", "审核失败"),
    ("draft", "草稿"),
    ("offline", "已下架"),
    ("recycle", "回收站"),
    ("strategy", "上架策略"),
)


class JsonEditorDialog(QDialog):
    def __init__(self, title: str, payload: dict[str, Any], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(820, 680)
        layout = QVBoxLayout(self)
        self.editor = QPlainTextEdit(json.dumps(payload, ensure_ascii=False, indent=2))
        layout.addWidget(self.editor, 1)
        actions = QHBoxLayout()
        actions.addStretch(1)
        cancel = QPushButton("取消")
        save = PrimaryButton("保存并提交")
        cancel.clicked.connect(self.reject)
        save.clicked.connect(self._accept)
        actions.addWidget(cancel)
        actions.addWidget(save)
        layout.addLayout(actions)

    def _accept(self) -> None:
        try:
            payload = json.loads(self.editor.toPlainText())
        except json.JSONDecodeError as exc:
            QMessageBox.warning(self, "JSON 格式错误", exc.msg)
            return
        if not isinstance(payload, dict):
            QMessageBox.warning(self, "JSON 格式错误", "顶层必须是对象。")
            return
        self.payload = payload
        self.accept()


class ProductsPage(TaskPage):
    def __init__(self, workspace) -> None:
        super().__init__(
            workspace,
            "商品管理",
            "多店商品、状态页签、实时查询和线上操作",
            stale_seconds=20,
        )
        self.active_tab_key = "sales"
        self.visible_rows: list[dict[str, Any]] = []
        self.tab_counts: dict[str, int] = {}
        self._stores = list(workspace.db.list_stores())

        tabs = QHBoxLayout()
        self.tab_group = QButtonGroup(self)
        self.tab_group.setExclusive(True)
        self.tab_buttons: dict[str, QPushButton] = {}
        for key, label in TABS:
            widget = QPushButton(label)
            widget.setCheckable(True)
            widget.clicked.connect(lambda _checked=False, tab=key: self._set_tab(tab))
            self.tab_group.addButton(widget)
            self.tab_buttons[key] = widget
            tabs.addWidget(widget)
        tabs.addStretch(1)
        self.root_layout.addLayout(tabs)

        filters = QHBoxLayout()
        self.store_combo = QComboBox()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("搜索商品名称、商品 ID、SPU 或外部 ID")
        self.search_input.setClearButtonEnabled(True)
        self.query_button = PrimaryButton("查询")
        self.reset_button = QPushButton("重置")
        self.quota_label = QLabel("提审额度 —")
        filters.addWidget(self.store_combo)
        filters.addWidget(self.search_input, 1)
        filters.addWidget(self.query_button)
        filters.addWidget(self.reset_button)
        filters.addWidget(self.quota_label)
        self.root_layout.addLayout(filters)
        self.query_button.clicked.connect(self._query_remote)
        self.reset_button.clicked.connect(self._reset)
        self.search_input.textChanged.connect(self._render)
        self.store_combo.currentIndexChanged.connect(lambda _index: self.refresh())

        actions = QHBoxLayout()
        self.add_button = PrimaryButton("上货发布")
        self.edit_button = QPushButton("编辑商品")
        self.detail_button = QPushButton("实时详情")
        self.token_button = QPushButton("获取口令")
        self.list_button = QPushButton("上架")
        self.unlist_button = QPushButton("下架")
        self.stock_button = QPushButton("修改库存")
        self.audit_button = QPushButton("撤回审核")
        self.delete_button = QPushButton("删除商品")
        self.delete_button.setObjectName("危险按钮")
        self.backend_button = QPushButton("打开后台")
        self.export_button = QPushButton("导出 XLSX")
        for widget in (
            self.add_button,
            self.edit_button,
            self.detail_button,
            self.token_button,
            self.list_button,
            self.unlist_button,
            self.stock_button,
            self.audit_button,
            self.delete_button,
            self.backend_button,
            self.export_button,
        ):
            actions.addWidget(widget)
        actions.addStretch(1)
        self.root_layout.addLayout(actions)
        self.add_button.clicked.connect(self._add_product)
        self.edit_button.clicked.connect(self._edit_product)
        self.detail_button.clicked.connect(self._show_detail)
        self.token_button.clicked.connect(self._get_token)
        self.list_button.clicked.connect(lambda: self._set_listing(True))
        self.unlist_button.clicked.connect(lambda: self._set_listing(False))
        self.stock_button.clicked.connect(self._update_stock)
        self.audit_button.clicked.connect(self._cancel_audit)
        self.delete_button.clicked.connect(self._delete_product)
        self.backend_button.clicked.connect(self._open_backend)
        self.export_button.clicked.connect(self._export)

        self.table = DataTable(
            [
                ("sequence", "序号"),
                ("store", "店铺"),
                ("product_id", "商品 ID"),
                ("title", "商品名称"),
                ("token", "口令"),
                ("price", "价格"),
                ("stock", "库存"),
                ("sold", "销量"),
                ("status", "状态"),
                ("listed", "上架时间"),
                ("audit", "审核结果"),
                ("operation", "操作结果"),
            ]
        )
        self.table.setSelectionMode(DataTable.SelectionMode.ExtendedSelection)
        self.table.row_activated.connect(lambda _row: self._show_detail())
        self.root_layout.addWidget(self.table, 1)
        self.summary_label = QLabel()
        self.summary_label.setObjectName("页面说明")
        self.root_layout.addWidget(self.summary_label)
        self._reload_stores()
        self.refresh()

    def _reload_stores(self) -> None:
        current = self.store_combo.currentData()
        self._stores = list(self.workspace.db.list_stores())
        self.store_combo.blockSignals(True)
        self.store_combo.clear()
        self.store_combo.addItem("全部店铺", None)
        for store in self._stores:
            self.store_combo.addItem(store.name, store.id)
        self.store_combo.setCurrentIndex(max(0, self.store_combo.findData(current)))
        self.store_combo.blockSignals(False)

    def refresh(self) -> None:
        self._reload_stores()
        store_id = self.store_combo.currentData()
        try:
            rows = self.workspace.db.list_products(
                store_id=store_id,
                search="",
            )
        except Exception as exc:
            self.notify.emit("商品加载失败", str(exc))
            rows = []
        self._all_rows = rows
        self.tab_counts = {
            key: sum(1 for row in rows if product_matches_tab(row, key)) for key, _label in TABS
        }
        for key, label in TABS:
            self.tab_buttons[key].setText(f"{label}  {self.tab_counts.get(key, 0)}")
        self._render()
        self._refresh_quota()
        super().refresh()

    def _set_tab(self, key: str) -> None:
        if key not in {item[0] for item in TABS}:
            return
        self.active_tab_key = key
        self.tab_buttons[key].setChecked(True)
        self._render()

    def _render(self) -> None:
        term = self.search_input.text().strip().casefold()
        filtered = []
        for row in getattr(self, "_all_rows", []):
            if not product_matches_tab(row, self.active_tab_key):
                continue
            searchable = " ".join(
                str(row.get(key) or "")
                for key in ("title", "product_id", "spu_code", "out_product_id")
            ).casefold()
            if term and term not in searchable:
                continue
            filtered.append(row)
        self.visible_rows = filtered
        display_rows = []
        for index, row in enumerate(filtered, 1):
            try:
                raw = json.loads(row.get("raw_json") or "{}")
            except (TypeError, ValueError):
                raw = {}
            meta = raw.get("_private_meta") if isinstance(raw, dict) else {}
            meta = meta if isinstance(meta, dict) else {}
            display_rows.append(
                {
                    **row,
                    "sequence": index,
                    "store": row.get("store_name") or "",
                    "token": str(meta.get("token") or "—"),
                    "price": f"¥{int(row.get('min_price') or 0) / 100:.2f}",
                    "sold": int(meta.get("total_sold_num") or 0),
                    "status": row.get("status_text") or "",
                    "listed": str(meta.get("listed_at") or row.get("synced_at") or "")[:16],
                    "audit": str(meta.get("audit_result") or ""),
                    "operation": str(meta.get("operation_result") or ""),
                }
            )
        self.table.set_rows(display_rows)
        self.summary_label.setText(
            f"当前页签 {self.tab_buttons[self.active_tab_key].text()} · "
            f"显示 {len(filtered)} 条 · 本地商品数据"
        )

    def _selected_row(self) -> dict[str, Any] | None:
        return self.table.selected_row()

    def _selected_rows(self) -> list[dict[str, Any]]:
        result = []
        for index in self.table.selectionModel().selectedRows():
            row = self.table.model_data.row_at(index.row())
            if row is not None:
                result.append(row)
        return result

    def _selected_store(self) -> Any | None:
        row = self._selected_row()
        if row:
            return next(
                (store for store in self._stores if store.id == int(row["store_id"])),
                None,
            )
        store_id = self.store_combo.currentData()
        return next((store for store in self._stores if store.id == store_id), None)

    def _query_remote(self) -> None:
        selected_id = self.store_combo.currentData()
        stores = [store for store in self._stores if selected_id is None or store.id == selected_id]
        if not stores:
            self.notify.emit("提示", "请先添加店铺")
            return
        tab_key = self.active_tab_key
        cancel_event = threading.Event()
        self._cancel_event = cancel_event

        def work() -> list[str]:
            errors = []
            for store in stores:
                if cancel_event.is_set():
                    break
                try:
                    self.workspace.service.refresh_product_tab(
                        store,
                        tab_key,
                        cancel_event=cancel_event,
                    )
                except Exception as exc:
                    errors.append(f"{store.name}: {exc}")
            return errors

        self.submit(
            f"products:query:{selected_id or 'all'}:{tab_key}",
            work,
            lambda errors: self._after_query(errors),
            self.query_button,
            cancelable=True,
        )

    def _after_query(self, errors: list[str]) -> None:
        self.refresh()
        if errors:
            QMessageBox.warning(
                self,
                "部分店铺商品读取失败",
                "\n".join(errors[:10]),
            )
        else:
            self.notify.emit("查询完成", "已读取当前页签商品")

    def _reset(self) -> None:
        self.search_input.clear()
        self.store_combo.setCurrentIndex(0)
        self._set_tab("sales")
        self._query_remote()

    def _refresh_quota(self) -> None:
        store = self._selected_store()
        if store is None:
            self.quota_label.setText("提审额度 —")
            return
        try:
            quota = json.loads(self.workspace.db.get_setting(f"product_quota_{store.id}", "{}"))
        except ValueError:
            quota = {}
        remaining = int(quota.get("remaining") or 0)
        total = int(quota.get("total") or 0)
        self.quota_label.setText(
            f"提审额度 {remaining}/{total}" if total or remaining else "提审额度 —"
        )

    def _payload(self, row: dict[str, Any]) -> dict[str, Any]:
        try:
            payload = json.loads(row.get("raw_json") or "{}")
        except (TypeError, ValueError):
            payload = {}
        return payload if isinstance(payload, dict) else {}

    def _show_detail(self) -> None:
        row = self._selected_row()
        store = self._selected_store()
        if not row or store is None:
            self.notify.emit("提示", "请先选择商品和店铺")
            return
        self.submit(
            f"products:detail:{store.id}:{row['product_id']}",
            lambda: self.workspace.service.client_for(store).fetch_product(
                store, str(row["product_id"])
            ),
            self._show_product_result,
            self.detail_button,
        )

    def _show_product_result(self, product: Product) -> None:
        JsonEditorDialog(
            f"商品详情 · {product.product_id}",
            product.raw,
            self,
        ).exec()

    def _add_product(self) -> None:
        store = self._selected_store()
        if store is None:
            self.notify.emit("提示", "新增商品前请选择具体店铺")
            return
        dialog = JsonEditorDialog("上货发布 · 微信商品 JSON", {}, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        answer = QMessageBox.question(
            self,
            "确认提交商品",
            f"即将向“{store.name}”提交新增商品请求。是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.submit(
            f"products:add:{store.id}",
            lambda: self.workspace.service.product_write(
                store,
                "add",
                dialog.payload,
            ),
            lambda _result: self._after_write("商品提交成功"),
            self.add_button,
        )

    def _edit_product(self) -> None:
        row = self._selected_row()
        store = self._selected_store()
        if not row or store is None:
            self.notify.emit("提示", "请先选择商品")
            return
        payload = self._payload(row)
        payload.setdefault("product_id", str(row["product_id"]))
        dialog = JsonEditorDialog("编辑商品 · 微信商品 JSON", payload, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        dialog.payload["product_id"] = str(row["product_id"])
        self.submit(
            f"products:update:{store.id}:{row['product_id']}",
            lambda: self.workspace.service.product_write(
                store,
                "update",
                dialog.payload,
            ),
            lambda _result: self._after_write("商品已提交编辑"),
            self.edit_button,
        )

    def _set_listing(self, listed: bool) -> None:
        store = self._selected_store()
        rows = self._selected_rows()
        if store is None or not rows:
            self.notify.emit("提示", "请先选择商品")
            return
        ids = list(dict.fromkeys(str(row["product_id"]) for row in rows))
        action = "上架" if listed else "下架"
        answer = QMessageBox.question(
            self,
            f"确认{action}",
            f"确定{action} {len(ids)} 个商品吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.submit(
            f"products:listing:{store.id}:{listed}:{','.join(ids)}",
            lambda: self.workspace.service.set_products_listing(store, ids, listed),
            lambda result: self._after_batch(result.message),
            self.list_button if listed else self.unlist_button,
        )

    def _update_stock(self) -> None:
        row = self._selected_row()
        store = self._selected_store()
        if not row or store is None:
            self.notify.emit("提示", "请先选择商品")
            return
        payload = self._payload(row)
        skus = [item for item in payload.get("skus", []) if isinstance(item, dict)]
        if not skus:
            QMessageBox.information(self, "无法修改库存", "请先读取实时商品详情。")
            return
        options = [
            f"SKU {item.get('sku_id') or index + 1} · 当前库存 {item.get('stock_num', 0)}"
            for index, item in enumerate(skus)
        ]
        selected, accepted = QInputDialog.getItem(
            self,
            "选择 SKU",
            "请选择要修改库存的 SKU：",
            options,
            0,
            False,
        )
        if not accepted:
            return
        sku = skus[options.index(selected)]
        current = int(sku.get("stock_num") or 0)
        amount, accepted = QInputDialog.getInt(
            self,
            "修改库存",
            "请输入目标库存：",
            current,
            0,
            10_000_000,
            1,
        )
        if not accepted:
            return
        sku_id = str(sku.get("sku_id") or "")
        self.submit(
            f"products:stock:{store.id}:{row['product_id']}:{sku_id}",
            lambda: self.workspace.service.update_product_stock(
                store,
                str(row["product_id"]),
                sku_id,
                3,
                amount,
            ),
            lambda _result: self._after_write("库存已提交，正在重新读取"),
            self.stock_button,
        )

    def _cancel_audit(self) -> None:
        self._write_single("cancel_audit", "撤回审核")

    def _delete_product(self) -> None:
        self._write_single("delete", "删除商品", destructive=True)

    def _write_single(
        self,
        action: str,
        label: str,
        *,
        destructive: bool = False,
    ) -> None:
        row = self._selected_row()
        store = self._selected_store()
        if not row or store is None:
            self.notify.emit("提示", "请先选择商品")
            return
        answer = QMessageBox.question(
            self,
            f"确认{label}",
            f"确定对商品“{row.get('title', '')}”执行{label}吗？"
            + ("\n该操作可能不可恢复。" if destructive else ""),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.submit(
            f"products:{action}:{store.id}:{row['product_id']}",
            lambda: self.workspace.service.product_write(
                store,
                action,
                product_id=str(row["product_id"]),
            ),
            lambda _result: self._after_write(f"{label}完成"),
            self.delete_button if action == "delete" else self.audit_button,
        )

    def _after_write(self, message: str) -> None:
        self._query_remote()
        self.notify.emit("操作完成", message)

    def _after_batch(self, message: str) -> None:
        self._query_remote()
        self.notify.emit("批量操作完成", message)

    def _get_token(self) -> None:
        row = self._selected_row()
        store = self._selected_store()
        if not row or store is None:
            return
        self.submit(
            f"products:taglink:{store.id}:{row['product_id']}",
            lambda: self.workspace.service.fetch_product_taglink(store, str(row["product_id"])),
            lambda token: self._after_token(str(token)),
            self.token_button,
        )

    def _after_token(self, token: str) -> None:
        from PySide6.QtWidgets import QApplication

        QApplication.clipboard().setText(token)
        self.notify.emit("口令已复制", token)

    def _open_backend(self) -> None:
        row = self._selected_row()
        store = self._selected_store()
        if not row or store is None:
            return
        self.submit(
            f"products:backend:{store.id}:{row['product_id']}",
            lambda: self.workspace.web_session.open_official_product_backend(
                store, str(row["product_id"])
            ),
            lambda _result: self._after_write("商品后台已关闭"),
            self.backend_button,
        )

    def _export(self) -> None:
        if not self.visible_rows:
            self.notify.emit("提示", "当前没有可导出的商品")
            return
        path, _filter = QFileDialog.getSaveFileName(
            self,
            "导出商品",
            f"商品_{datetime.now():%Y%m%d}.xlsx",
            "Excel 工作簿 (*.xlsx)",
        )
        if not path:
            return
        headers = ["店铺", "商品ID", "商品名称", "状态", "价格", "库存", "SPU", "外部ID"]
        rows = [
            [
                row.get("store_name", ""),
                row.get("product_id", ""),
                row.get("title", ""),
                row.get("status_text", ""),
                row.get("min_price", 0),
                row.get("stock_total", 0),
                row.get("spu_code", ""),
                row.get("out_product_id", ""),
            ]
            for row in self.visible_rows
        ]
        export_xlsx(path, "商品", headers, rows)
        self.notify.emit("导出完成", str(Path(path)))
