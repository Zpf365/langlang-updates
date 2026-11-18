from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtWidgets import QApplication

from .theme import THEMES, UiPreferences, build_stylesheet


class ThemeManager:
    def __init__(self, application: QApplication, preferences_path: Path) -> None:
        self.application = application
        self.preferences_path = preferences_path
        self.preferences = self._load()
        self.apply(self.preferences.theme)

    @property
    def current_theme(self) -> str:
        return self.preferences.theme

    def _load(self) -> UiPreferences:
        try:
            payload = json.loads(self.preferences_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeDecodeError):
            return UiPreferences()
        theme = str(payload.get("theme") or "")
        width = payload.get("window_width", 1400)
        height = payload.get("window_height", 850)
        return UiPreferences(
            theme=theme if theme in THEMES else "cloud_blue",
            density="compact" if payload.get("density") == "compact" else "comfortable",
            window_width=width if isinstance(width, int) and 1180 <= width <= 3840 else 1400,
            window_height=height if isinstance(height, int) and 720 <= height <= 2160 else 850,
        )

    def apply(self, theme_code: str) -> None:
        palette = THEMES.get(theme_code, THEMES["cloud_blue"])
        self.preferences = UiPreferences(
            theme=palette.code,
            density=self.preferences.density,
            window_width=self.preferences.window_width,
            window_height=self.preferences.window_height,
        )
        self.application.setStyleSheet(build_stylesheet(palette))
        self._save()

    def update_window_size(self, width: int, height: int) -> None:
        self.preferences = UiPreferences(
            theme=self.preferences.theme,
            density=self.preferences.density,
            window_width=max(1180, min(3840, int(width))),
            window_height=max(720, min(2160, int(height))),
        )
        self._save()

    def _save(self) -> None:
        self.preferences_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.preferences_path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(
                {
                    "theme": self.preferences.theme,
                    "density": self.preferences.density,
                    "window_width": self.preferences.window_width,
                    "window_height": self.preferences.window_height,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        temporary.replace(self.preferences_path)
