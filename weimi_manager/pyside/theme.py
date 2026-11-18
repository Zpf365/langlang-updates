# ruff: noqa: E501 - QSS declarations are intentionally kept on style lines.

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Final


@dataclass(frozen=True, slots=True)
class ThemePalette:
    code: str
    name: str
    background: str
    surface: str
    sidebar: str
    primary: str
    primary_hover: str
    primary_soft: str
    text: str
    text_muted: str
    border: str
    success: str
    warning: str
    danger: str
    selected: str
    input_background: str
    table_header: str
    table_stripe: str
    separator: str


THEMES: Final[dict[str, ThemePalette]] = {
    "cloud_blue": ThemePalette(
        "cloud_blue",
        "云瓷蓝白",
        "#F3F6FA",
        "#FFFFFF",
        "#17324F",
        "#2463A9",
        "#1B4F8A",
        "#E7EFF9",
        "#14253D",
        "#5D6B7E",
        "#D8E1EC",
        "#138A57",
        "#C77912",
        "#C43F3F",
        "#EAF2FC",
        "#FFFFFF",
        "#F1F5FA",
        "#F8FAFD",
        "#E7EDF5",
    ),
    "jade_mist": ThemePalette(
        "jade_mist",
        "雾青玉白",
        "#F3F7F5",
        "#FFFFFF",
        "#163D38",
        "#137A6F",
        "#0E635B",
        "#E2F1ED",
        "#142D2A",
        "#5D716E",
        "#D8E5E1",
        "#16804A",
        "#B96A12",
        "#C2413B",
        "#E8F5F1",
        "#FFFFFF",
        "#F0F6F3",
        "#F9FCFA",
        "#E2ECE8",
    ),
    "midnight_slate": ThemePalette(
        "midnight_slate",
        "曜石深蓝",
        "#101827",
        "#1B273A",
        "#172033",
        "#76A8FF",
        "#94BDFF",
        "#253C60",
        "#F2F6FE",
        "#AAB8CD",
        "#344259",
        "#59C896",
        "#E8B55E",
        "#F28585",
        "#273A57",
        "#202E45",
        "#253247",
        "#1D2A3F",
        "#2B3A52",
    ),
}


@dataclass(frozen=True, slots=True)
class UiPreferences:
    theme: str = "cloud_blue"
    density: str = "comfortable"
    window_width: int = 1400
    window_height: int = 850


def _validated_preferences(payload: object) -> UiPreferences:
    if not isinstance(payload, dict):
        return UiPreferences()
    theme = payload.get("theme", "cloud_blue")
    density = payload.get("density", "comfortable")
    width = payload.get("window_width", 1400)
    height = payload.get("window_height", 850)
    return UiPreferences(
        theme=theme if isinstance(theme, str) and theme in THEMES else "cloud_blue",
        density=density if density in {"comfortable", "compact"} else "comfortable",
        window_width=width if type(width) is int and 1180 <= width <= 3840 else 1400,
        window_height=height if type(height) is int and 720 <= height <= 2160 else 850,
    )


def load_preferences(path: Path) -> UiPreferences:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return UiPreferences()
    return _validated_preferences(payload)


def save_preferences(path: Path, preferences: UiPreferences) -> None:
    """使用同目录临时文件和原子替换，避免设置文件写一半损坏。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=".langlang-preferences-",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = handle.name
            json.dump(asdict(preferences), handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path and os.path.exists(temporary_path):
            os.unlink(temporary_path)


def build_stylesheet(palette: ThemePalette) -> str:
    """生成统一 Qt 样式；所有页面共享同一套设计令牌。"""
    return f"""
    QWidget {{
        color: {palette.text};
        font-family: 'Microsoft YaHei UI', 'Microsoft YaHei', 'Segoe UI';
        font-size: 10pt;
    }}
    QMainWindow, QDialog {{ background: {palette.background}; }}
    QFrame#侧边栏 {{ background: {palette.sidebar}; border: none; }}
    QFrame#顶部栏 {{ background: {palette.surface}; border-bottom: 1px solid {palette.border}; }}
    QFrame#内容卡片, QFrame#指标卡片 {{
        background: {palette.surface}; border: 1px solid {palette.border};
        border-radius: 7px;
    }}
    QLabel#页面标题 {{ font-size: 21pt; font-weight: 700; color: {palette.text}; }}
    QLabel#页面说明 {{ color: {palette.text_muted}; font-size: 9pt; }}
    QPushButton {{
        background: {palette.surface}; color: {palette.primary};
        border: 1px solid {palette.border}; border-radius: 4px;
        padding: 8px 14px; min-height: 18px;
    }}
    QPushButton:hover {{ background: {palette.primary_soft}; border-color: {palette.primary}; }}
    QPushButton:pressed {{ background: {palette.selected}; }}
    QPushButton:disabled {{ color: {palette.text_muted}; background: {palette.background}; }}
    QPushButton[主要按钮="true"] {{
        background: {palette.primary}; color: #FFFFFF; border-color: {palette.primary};
        font-weight: 600;
    }}
    QPushButton[主要按钮="true"]:hover {{ background: {palette.primary_hover}; }}
    QPushButton[导航按钮="true"] {{
        text-align: left; border: none; border-radius: 5px;
        color: {palette.text}; padding: 11px 14px; background: transparent;
    }}
    QFrame#侧边栏 QPushButton[导航按钮="true"] {{ color: #E7EEF8; }}
    QPushButton[导航按钮="true"][选中="true"] {{
        color: {palette.primary}; background: {palette.primary_soft};
        border-left: 3px solid {palette.primary}; font-weight: 700;
    }}
    QLineEdit, QComboBox, QDateEdit, QSpinBox, QDoubleSpinBox, QTextEdit {{
        background: {palette.input_background}; border: 1px solid {palette.border};
        border-radius: 3px; padding: 7px 9px; selection-background-color: {palette.primary};
    }}
    QLineEdit:focus, QComboBox:focus, QTextEdit:focus {{ border: 1px solid {palette.primary}; }}
    QTableView, QTreeView {{
        background: {palette.surface}; alternate-background-color: {palette.table_stripe};
        border: 1px solid {palette.border}; gridline-color: {palette.separator};
        selection-background-color: {palette.selected}; selection-color: {palette.text};
    }}
    QHeaderView::section {{
        background: {palette.table_header}; color: {palette.text_muted};
        border: none; border-bottom: 1px solid {palette.border};
        padding: 10px 8px; font-weight: 600;
    }}
    QTabWidget::pane {{ border: 1px solid {palette.border}; background: {palette.surface}; }}
    QTabBar::tab {{ background: {palette.surface}; padding: 9px 16px; border: 1px solid {palette.border}; }}
    QTabBar::tab:selected {{ color: {palette.primary}; border-bottom: 2px solid {palette.primary}; font-weight: 700; }}
    QProgressBar {{ border: 1px solid {palette.border}; border-radius: 4px; text-align: center; }}
    QProgressBar::chunk {{ background: {palette.primary}; border-radius: 3px; }}
    QStatusBar {{ background: {palette.surface}; color: {palette.text_muted}; border-top: 1px solid {palette.border}; }}
    QScrollBar:vertical {{ width: 10px; background: {palette.background}; margin: 0; }}
    QScrollBar::handle:vertical {{ background: {palette.border}; min-height: 28px; border-radius: 4px; }}
    QScrollBar::handle:vertical:hover {{ background: {palette.text_muted}; }}
    """
