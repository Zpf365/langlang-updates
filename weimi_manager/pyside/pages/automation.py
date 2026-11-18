from __future__ import annotations

from datetime import date, datetime, timedelta

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from ..widgets import DataTable, PrimaryButton
from .common import TaskPage

TASK_TYPES = {
    "orders": "同步最近订单",
    "products": "同步商品目录与详情",
    "status": "刷新商品状态数量",
    "after_sales": "同步售后",
    "analytics": "同步经营数据",
}


class AutomationDialog(QDialog):
    def __init__(self, stores, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("新增自动化任务")
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.name = QLineEdit()
        self.task_type = QComboBox()
        for key, label in TASK_TYPES.items():
            self.task_type.addItem(label, key)
        self.store = QComboBox()
        self.store.addItem("全部店铺", None)
        for item in stores:
            self.store.addItem(item.name, item.id)
        self.interval = QSpinBox()
        self.interval.setRange(1, 10080)
        self.interval.setValue(60)
        form.addRow("任务名称：", self.name)
        form.addRow("任务类型：", self.task_type)
        form.addRow("店铺：", self.store)
        form.addRow("间隔分钟：", self.interval)
        layout.addLayout(form)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class AutomationPage(TaskPage):
    def __init__(self, workspace) -> None:
        super().__init__(
            workspace,
            "自动化",
            "本地定时同步、托管执行和运行日志",
            stale_seconds=0,
        )
        toolbar = QHBoxLayout()
        self.add_button = PrimaryButton("新增任务")
        self.run_button = QPushButton("立即执行")
        self.toggle_button = QPushButton("启用/停用")
        self.delete_button = QPushButton("删除")
        self.refresh_button = QPushButton("刷新")
        for widget in (
            self.add_button,
            self.run_button,
            self.toggle_button,
            self.delete_button,
            self.refresh_button,
        ):
            toolbar.addWidget(widget)
        toolbar.addStretch(1)
        self.stats = QLabel()
        toolbar.addWidget(self.stats)
        self.root_layout.addLayout(toolbar)
        self.table = DataTable(
            [
                ("id", "序号"),
                ("name", "任务名称"),
                ("type", "类型"),
                ("store", "店铺"),
                ("interval", "间隔"),
                ("enabled", "状态"),
                ("last_run", "最近执行"),
                ("next_run", "下次执行"),
                ("last_status", "结果"),
                ("last_message", "说明"),
            ]
        )
        self.root_layout.addWidget(self.table, 1)
        self.add_button.clicked.connect(self._add)
        self.run_button.clicked.connect(self._run_selected)
        self.toggle_button.clicked.connect(self._toggle)
        self.delete_button.clicked.connect(self._delete)
        self.refresh_button.clicked.connect(self.refresh)
        self._timer = QTimer(self)
        self._timer.setInterval(60000)
        self._timer.timeout.connect(self._tick)
        self._timer.start()
        self.refresh()

    def refresh(self) -> None:
        self._tasks = self.workspace.db.list_automation_tasks()
        self.table.set_rows(
            [
                {
                    **task,
                    "type": TASK_TYPES.get(str(task.get("task_type")), task.get("task_type", "")),
                    "store": task.get("store_name") or "全部店铺",
                    "interval": f"{task.get('interval_minutes', 0)} 分钟",
                    "enabled": "启用" if task.get("enabled") else "停用",
                }
                for task in self._tasks
            ]
        )
        enabled = sum(bool(task.get("enabled")) for task in self._tasks)
        self.stats.setText(f"{len(self._tasks)} 个任务 · {enabled} 个启用")
        super().refresh()

    def _selected(self):
        row = self.table.selected_row()
        if not row:
            return None
        return next(
            (task for task in self._tasks if int(task["id"]) == int(row["id"])),
            None,
        )

    def _add(self) -> None:
        stores = list(self.workspace.db.list_stores())
        dialog = AutomationDialog(stores, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        name = dialog.name.text().strip()
        if not name:
            QMessageBox.warning(self, "保存失败", "任务名称不能为空。")
            return
        self.workspace.db.add_automation_task(
            name,
            str(dialog.task_type.currentData()),
            dialog.store.currentData(),
            dialog.interval.value(),
        )
        self.refresh()

    def _toggle(self) -> None:
        task = self._selected()
        if not task:
            return
        self.workspace.db.update_automation_task(
            int(task["id"]),
            enabled=0 if task.get("enabled") else 1,
        )
        self.refresh()

    def _delete(self) -> None:
        task = self._selected()
        if not task:
            return
        answer = QMessageBox.question(
            self,
            "删除任务",
            f"确定删除自动化任务“{task['name']}”吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.workspace.db.delete_automation_task(int(task["id"]))
            self.refresh()

    def _run_selected(self) -> None:
        task = self._selected()
        if task:
            self._execute([task])

    def _execute(self, tasks: list[dict]) -> None:
        self.submit(
            "automation:execute:" + ",".join(str(task["id"]) for task in tasks),
            lambda: self._execute_work(tasks),
            lambda errors: self._after_execute(errors),
            self.run_button,
        )

    def _execute_work(self, tasks: list[dict]) -> list[str]:
        errors = []
        now = datetime.now()
        for task in tasks:
            task_type = str(task.get("task_type") or "")
            store_id = task.get("store_id")
            stores = (
                [self.workspace.db.get_store(int(store_id))]
                if store_id
                else self.workspace.db.list_stores()
            )
            stores = [store for store in stores if store is not None]
            try:
                for store in stores:
                    if task_type == "orders":
                        end = datetime.now()
                        start = end - timedelta(days=7)
                        self.workspace.service.sync_orders(
                            store,
                            int(start.timestamp()),
                            int(end.timestamp()),
                        )
                    elif task_type == "products":
                        self.workspace.service.sync_products(store, max_items=300)
                    elif task_type == "status":
                        self.workspace.service.refresh_product_status_snapshot(store)
                    elif task_type == "after_sales":
                        end = datetime.now()
                        start = end.replace(hour=0, minute=0, second=0)
                        self.workspace.service.sync_after_sales(
                            store,
                            int(start.timestamp()),
                            int(end.timestamp()),
                        )
                    elif task_type == "analytics":
                        self.workspace.service.sync_analytics_day(
                            store,
                            date.today().isoformat(),
                        )
                self.workspace.db.update_automation_task(
                    int(task["id"]),
                    last_run=now.strftime("%Y-%m-%d %H:%M:%S"),
                    next_run=(
                        now + timedelta(minutes=int(task.get("interval_minutes") or 60))
                    ).strftime("%Y-%m-%d %H:%M:%S"),
                    last_status="成功",
                    last_message=f"完成 {len(stores)} 家店铺",
                )
                self.workspace.db.log_automation_run(
                    int(task["id"]),
                    "success",
                    f"完成 {len(stores)} 家店铺",
                )
            except Exception as exc:
                message = f"{task.get('name')}: {exc}"
                errors.append(message)
                self.workspace.db.update_automation_task(
                    int(task["id"]),
                    last_status="失败",
                    last_message=str(exc)[:1000],
                )
                self.workspace.db.log_automation_run(
                    int(task["id"]),
                    "failed",
                    str(exc),
                )
        return errors

    def _after_execute(self, errors: list[str]) -> None:
        self.refresh()
        self.notify.emit("自动化执行完成", f"问题 {len(errors)} 条")

    def _tick(self) -> None:
        due = []
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for task in self.workspace.db.list_automation_tasks():
            if task.get("enabled") and str(task.get("next_run") or "") <= now:
                due.append(task)
        if due:
            self._execute(due)
