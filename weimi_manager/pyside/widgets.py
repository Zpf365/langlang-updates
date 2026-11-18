from __future__ import annotations

from datetime import datetime
from typing import Any

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QTableView,
    QVBoxLayout,
    QWidget,
)


class PrimaryButton(QPushButton):
    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setProperty("primaryButton", True)
        self.setProperty("主要按钮", True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)


class MetricCard(QFrame):
    def __init__(
        self,
        title: str,
        value: str = "—",
        hint: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("指标卡片")
        self.setMinimumHeight(104)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 13, 16, 12)
        layout.setSpacing(5)
        self.title_label = QLabel(title)
        self.title_label.setStyleSheet("color:#718096;font-size:9pt;")
        self.value_label = QLabel(value)
        self.value_label.setStyleSheet("font-size:22pt;font-weight:700;")
        self.hint_label = QLabel(hint)
        self.hint_label.setStyleSheet("color:#8996A5;font-size:8pt;")
        layout.addWidget(self.title_label)
        layout.addWidget(self.value_label)
        layout.addWidget(self.hint_label)
        layout.addStretch(1)

    def set_value(self, value: str, hint: str | None = None) -> None:
        self.value_label.setText(value)
        if hint is not None:
            self.hint_label.setText(hint)


class SectionHeader(QWidget):
    def __init__(
        self,
        title: str,
        description: str = "",
        actions: list[QPushButton] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 8)
        layout.setSpacing(12)
        text_layout = QVBoxLayout()
        text_layout.setSpacing(4)
        title_label = QLabel(title)
        title_label.setStyleSheet("font-size:17pt;font-weight:700;")
        description_label = QLabel(description)
        description_label.setStyleSheet("color:#7A8797;font-size:9pt;")
        description_label.setWordWrap(True)
        text_layout.addWidget(title_label)
        if description:
            text_layout.addWidget(description_label)
        layout.addLayout(text_layout, 1)
        for button in actions or []:
            layout.addWidget(button)


class TableModel(QAbstractTableModel):
    """轻量只读表格模型，避免每次切页重建所有表格控件。"""

    def __init__(
        self,
        columns: list[tuple[str, str]],
        rows: list[dict[str, Any]] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.columns = list(columns)
        self.rows = list(rows or [])

    def rowCount(  # noqa: N802
        self,
        parent: QModelIndex = QModelIndex(),  # noqa: B008
    ) -> int:
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(  # noqa: N802
        self,
        parent: QModelIndex = QModelIndex(),  # noqa: B008
    ) -> int:
        return 0 if parent.isValid() else len(self.columns)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid() or not (0 <= index.row() < len(self.rows)):
            return None
        key, _label = self.columns[index.column()]
        value = self.rows[index.row()].get(key, "")
        if role == Qt.ItemDataRole.DisplayRole:
            if value is None:
                return ""
            if isinstance(value, bool):
                return "是" if value else "否"
            if isinstance(value, int) and (key.endswith("_amount") or key == "min_price"):
                return f"¥{value / 100:.2f}"
            if (
                isinstance(value, int)
                and key in {"create_time", "update_time", "pay_time"}
                and value > 0
            ):
                return datetime.fromtimestamp(value).strftime("%Y-%m-%d %H:%M")
            if isinstance(value, dict):
                return "已配置" if value else "未配置"
            if isinstance(value, list):
                return f"{len(value)} 项"
            return str(value)
        if role == Qt.ItemDataRole.UserRole:
            return self.rows[index.row()]
        if role == Qt.ItemDataRole.TextAlignmentRole and key in {
            "id",
            "status",
            "stock",
            "sku_count",
            "goods_count",
            "remaining_days",
        }:
            return int(Qt.AlignmentFlag.AlignCenter)
        return None

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> Any:
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal and 0 <= section < len(self.columns):
            return self.columns[section][1]
        if orientation == Qt.Orientation.Vertical:
            return section + 1
        return None

    def set_rows(self, rows: list[dict[str, Any]]) -> None:
        self.beginResetModel()
        self.rows = list(rows)
        self.endResetModel()

    def row_at(self, row: int) -> dict[str, Any] | None:
        return self.rows[row] if 0 <= row < len(self.rows) else None

    def update_row_by_key(self, key_field: str, key_value: Any, changes: dict[str, Any]) -> bool:
        for row_index, row in enumerate(self.rows):
            if row.get(key_field) == key_value:
                row.update(changes)
                if self.columnCount() > 0:
                    self.dataChanged.emit(
                        self.index(row_index, 0),
                        self.index(row_index, self.columnCount() - 1),
                        [Qt.ItemDataRole.DisplayRole],
                    )
                return True
        return False


class DataTable(QTableView):
    row_activated = Signal(object)

    def __init__(
        self,
        columns: list[tuple[str, str]],
        rows: list[dict[str, Any]] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.model_data = TableModel(columns, rows, self)
        self.setModel(self.model_data)
        self.setAlternatingRowColors(True)
        self.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QTableView.SelectionMode.SingleSelection)
        self.setSortingEnabled(False)
        self.setShowGrid(True)
        self.verticalHeader().setVisible(False)
        self.horizontalHeader().setStretchLastSection(True)
        self.horizontalHeader().setMinimumSectionSize(76)
        self.verticalHeader().setDefaultSectionSize(40)
        self.setWordWrap(False)
        self.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.doubleClicked.connect(self._emit_activated)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def _emit_activated(self, index: QModelIndex) -> None:
        value = self.model_data.row_at(index.row())
        if value is not None:
            self.row_activated.emit(value)

    def selected_row(self) -> dict[str, Any] | None:
        indexes = self.selectionModel().selectedRows()
        return self.model_data.row_at(indexes[0].row()) if indexes else None

    def set_rows(self, rows: list[dict[str, Any]]) -> None:
        self.model_data.set_rows(rows)

    def update_row_by_key(self, key_field: str, key_value: Any, changes: dict[str, Any]) -> bool:
        return self.model_data.update_row_by_key(key_field, key_value, changes)


class PageBase(QWidget):
    """缓存页面基类；进入页面只在过期时刷新，不销毁、不重建页面。"""

    notify = Signal(str, str)

    def __init__(
        self,
        title: str,
        description: str = "",
        parent: QWidget | None = None,
        stale_seconds: int = 30,
    ) -> None:
        super().__init__(parent)
        self.title = title
        self.description = description
        self.stale_seconds = max(0, stale_seconds)
        self._last_loaded_at = 0.0
        self._is_loading = False
        self.root_layout = QVBoxLayout(self)
        self.root_layout.setContentsMargins(24, 20, 24, 20)
        self.root_layout.setSpacing(14)

    def on_enter(self, force_refresh: bool = False) -> None:
        now = datetime.now().timestamp()
        stale = now - self._last_loaded_at >= self.stale_seconds
        if force_refresh or (stale and not self._is_loading):
            self.refresh()

    def refresh(self) -> None:
        self._last_loaded_at = datetime.now().timestamp()

    def set_loading(self, loading: bool) -> None:
        self._is_loading = bool(loading)

    def mark_stale(self) -> None:
        self._last_loaded_at = 0.0
