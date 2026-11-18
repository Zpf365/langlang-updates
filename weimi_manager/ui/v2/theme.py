"""澜琅三主题设计令牌与安全的本地界面偏好存储。

本模块与业务、网络、数据库完全解耦，方便旧项目直接复用。
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Final


@dataclass(frozen=True)
class Palette:
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


THEMES: Final[dict[str, Palette]] = {
    "cloud_blue": Palette(
        "cloud_blue", "云瓷蓝白", "#F3F6FA", "#FFFFFF", "#17324F",
        "#2463A9", "#1B4F8A", "#E7EFF9", "#14253D", "#5D6B7E",
        "#D8E1EC", "#138A57", "#C77912", "#C43F3F", "#EAF2FC",
        "#FFFFFF", "#F1F5FA", "#F8FAFD", "#E7EDF5",
    ),
    "jade_mist": Palette(
        "jade_mist", "雾青玉白", "#F3F7F5", "#FFFFFF", "#163D38",
        "#137A6F", "#0E635B", "#E2F1ED", "#142D2A", "#5D716E",
        "#D8E5E1", "#16804A", "#B96A12", "#C2413B", "#E8F5F1",
        "#FFFFFF", "#F0F6F3", "#F9FCFA", "#E2ECE8",
    ),
    "midnight_slate": Palette(
        "midnight_slate", "曜石深蓝", "#101827", "#1B273A", "#172033",
        "#76A8FF", "#94BDFF", "#253C60", "#F2F6FE", "#AAB8CD",
        "#344259", "#59C896", "#E8B55E", "#F28585", "#273A57",
        "#202E45", "#253247", "#1D2A3F", "#2B3A52",
    ),
}


@dataclass(frozen=True)
class Preferences:
    theme: str = "cloud_blue"
    density: str = "comfortable"
    window_width: int = 1400
    window_height: int = 850


def preferences_file() -> Path:
    """优先使用 Windows 本机配置目录，不存储账户凭据。"""
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "LangLang" / "ui_preferences.json"
    # Linux 仅作为界面参考运行，不影响 Windows 现有配置路径。
    return Path.home() / ".config" / "langlang" / "ui_preferences.json"


def parse_preferences(data: object) -> Preferences:
    """将外部 JSON 严格收敛为允许的配置，忽略多余字段。"""
    if not isinstance(data, dict):
        return Preferences()
    theme = data.get("theme", "cloud_blue")
    density = data.get("density", "comfortable")
    width = data.get("window_width", 1400)
    height = data.get("window_height", 850)
    return Preferences(
        theme=theme if isinstance(theme, str) and theme in THEMES else "cloud_blue",
        density=density if isinstance(density, str) and density in {"comfortable", "compact"} else "comfortable",
        window_width=width if type(width) is int and 1180 <= width <= 3840 else 1400,
        window_height=height if type(height) is int and 720 <= height <= 2160 else 850,
    )


def load_preferences(path: Path | None = None) -> Preferences:
    target = path or preferences_file()
    try:
        with target.open("r", encoding="utf-8") as file:
            return parse_preferences(json.load(file))
    except (OSError, ValueError, UnicodeError):
        # 异常配置不得阻止软件启动；保留原文件供诊断，不做破坏性覆盖。
        return Preferences()


def save_preferences(prefs: Preferences, path: Path | None = None) -> None:
    """原子写入。失败时不覆盖旧配置，临时文件确保清理。"""
    target = path or preferences_file()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=target.parent,
            prefix=".ui_preferences_", suffix=".tmp", delete=False,
        ) as file:
            temporary_name = file.name
            json.dump(asdict(prefs), file, ensure_ascii=False, indent=2)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary_name, target)
    finally:
        if temporary_name and os.path.exists(temporary_name):
            os.unlink(temporary_name)


def swatches(theme_code: str) -> tuple[str, str, str]:
    p = THEMES[theme_code]
    return p.primary, p.background, p.surface
