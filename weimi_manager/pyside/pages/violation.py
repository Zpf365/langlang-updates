from __future__ import annotations

import json
from datetime import datetime

from PySide6.QtWidgets import QComboBox, QHBoxLayout, QInputDialog, QLabel, QPushButton

from ..widgets import DataTable, PrimaryButton
from .common import TaskPage


class ViolationPage(TaskPage):
    def __init__(self, workspace) -> None:
        super().__init__(
            workspace,
            "违规商品库",
            "平台违规、本机黑名单和商品处理",
            stale_seconds=20,
        )
        controls = QHBoxLayout()
        self.store_combo = QComboBox()
        self.sync_button = PrimaryButton("抓取违规")
        self.add_button = QPushButton("手工添加")
        self.remove_button = QPushButton("移出黑名单")
        self.process_button = QPushButton("处理商品")
        self.refresh_button = QPushButton("刷新")
        for widget in (
            self.store_combo,
            self.sync_button,
            self.add_button,
            self.remove_button,
            self.process_button,
            self.refresh_button,
        ):
            controls.addWidget(widget)
        controls.addStretch(1)
        self.root_layout.addLayout(controls)
        self.table = DataTable(
            [
                ("store", "店铺"),
                ("product_id", "商品ID"),
                ("title", "商品名称"),
                ("reason", "违规原因"),
                ("source", "来源"),
                ("blacklist", "黑名单"),
                ("time", "时间"),
            ]
        )
        self.table.setSelectionMode(DataTable.SelectionMode.ExtendedSelection)
        self.root_layout.addWidget(self.table, 1)
        self.summary = QLabel()
        self.summary.setObjectName("页面说明")
        self.root_layout.addWidget(self.summary)
        self._stores = list(workspace.db.list_stores())
        self._reload_stores()
        self.sync_button.clicked.connect(self._sync)
        self.add_button.clicked.connect(self._add)
        self.remove_button.clicked.connect(self._remove)
        self.process_button.clicked.connect(self._process)
        self.refresh_button.clicked.connect(self.refresh)
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

    def _store(self):
        store_id = self.store_combo.currentData()
        return next((store for store in self._stores if store.id == store_id), None)

    def refresh(self) -> None:
        self._reload_stores()
        try:
            records = json.loads(self.workspace.db.get_setting("violation_library", "[]"))
        except ValueError:
            records = []
        records = [row for row in records if isinstance(row, dict)]
        blocked = self.workspace.db.violation_blacklist_ids()
        store = self._store()
        if store is not None:
            records = [row for row in records if int(row.get("store_id") or 0) in {0, store.id}]
        self.table.set_rows(
            [
                {
                    **row,
                    "store": row.get("store_name") or "",
                    "blacklist": "是" if str(row.get("product_id")) in blocked else "",
                    "time": str(row.get("synced_at") or row.get("time") or "")[:19],
                }
                for row in records
            ]
        )
        self.summary.setText(f"违规记录 {len(records)} 条 · 黑名单 {len(blocked)} 个商品")
        super().refresh()

    def _sync(self) -> None:
        store = self._store()
        if store is None:
            return
        self.submit(
            f"violation:sync:{store.id}",
            lambda: self.workspace.service.sync_violation_library(store),
            lambda result: self._after(getattr(result, "message", "违规抓取完成")),
            self.sync_button,
        )

    def _after(self, message: str) -> None:
        self.refresh()
        self.notify.emit("操作完成", message)

    def _add(self) -> None:
        product_id, accepted = QInputDialog.getText(self, "加入黑名单", "商品 ID：")
        if not accepted or not product_id.strip():
            return
        reason, accepted = QInputDialog.getText(self, "加入黑名单", "原因：")
        if not accepted:
            return
        try:
            rows = json.loads(self.workspace.db.get_setting("violation_blacklist", "[]"))
        except ValueError:
            rows = []
        rows = [row for row in rows if isinstance(row, dict)]
        rows.append(
            {
                "product_id": product_id.strip(),
                "title": product_id.strip(),
                "reason": reason.strip() or "本机记录",
                "added": datetime.now().strftime("%Y-%m-%d %H:%M"),
            }
        )
        self.workspace.db.set_setting(
            "violation_blacklist",
            json.dumps(rows, ensure_ascii=False),
        )
        self.refresh()

    def _remove(self) -> None:
        rows = [
            self.table.model_data.row_at(index.row())
            for index in self.table.selectionModel().selectedRows()
        ]
        ids = {str(row["product_id"]) for row in rows if row}
        try:
            blacklist = json.loads(self.workspace.db.get_setting("violation_blacklist", "[]"))
        except ValueError:
            blacklist = []
        remaining = [
            row
            for row in blacklist
            if isinstance(row, dict) and str(row.get("product_id")) not in ids
        ]
        self.workspace.db.set_setting(
            "violation_blacklist",
            json.dumps(remaining, ensure_ascii=False),
        )
        self.refresh()

    def _process(self) -> None:
        store = self._store()
        rows = [
            self.table.model_data.row_at(index.row())
            for index in self.table.selectionModel().selectedRows()
        ]
        rows = [row for row in rows if row]
        if store is None or not rows:
            return
        choice, accepted = QInputDialog.getItem(
            self,
            "处理违规商品",
            "处理方式：",
            ("移入回收站", "下架", "彻底删除"),
            0,
            False,
        )
        if not accepted:
            return
        way = {"移入回收站": "recycle", "下架": "offshelf", "彻底删除": "real"}[choice]
        ids = [str(row["product_id"]) for row in rows]
        self.submit(
            f"violation:process:{store.id}:{way}",
            lambda: self.workspace.service.private_batch_product_action(
                store,
                ids,
                way,
            ),
            lambda _result: self._after(f"已处理 {len(ids)} 个商品"),
            self.process_button,
        )
