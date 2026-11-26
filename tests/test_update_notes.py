from __future__ import annotations

import tkinter as tk
from pathlib import Path
from types import SimpleNamespace

import pytest

from weimi_manager.update_notes import (
    build_update_summary,
    normalize_update_summary,
    release_notes_url_from_download,
    summary_point_count,
)
from weimi_manager.ui.update_notes_dialog import UpdateNotesDialog


LONG_NOTES = """
# 澜琅 1.8.84

## 订单与店铺
- 页签数量按店铺筛选。
- 批量登录只打开一次窗口。

## 销售与退款
- 官方销售扣除未领取礼物。
- 退款单量来自真实售后缓存。

## 店铺总览
- 默认经营总览无横向滚动。
- 详情行内展开。

## 数据大屏
- 恢复原版数据大屏。

## 边界
- 代码路径已验证，生产写未实测。

```
SHA256 technical
```
"""


def test_update_summary_is_compact_and_layered() -> None:
    groups = build_update_summary(LONG_NOTES)

    assert 4 <= summary_point_count(groups) <= 6
    assert len(groups) <= 6
    assert all(len(group["items"]) <= 2 for group in groups)
    text = " ".join(
        item
        for group in groups
        for item in group["items"]
    )
    assert "SHA256" not in text
    assert "生产写未实测" not in text


def test_manifest_summary_is_preferred_and_capped() -> None:
    groups = normalize_update_summary(
        [
            {
                "title": "主要修复",
                "items": ["更新弹窗改为宽屏", "底部按钮固定可见"],
            }
        ]
    )
    assert groups == [
        {
            "title": "主要修复",
            "items": ["更新弹窗改为宽屏", "底部按钮固定可见"],
        }
    ]
    assert summary_point_count(
        normalize_update_summary([f"要点 {index}" for index in range(10)])
    ) == 6


def test_release_notes_url_is_derived_from_asset_url() -> None:
    assert release_notes_url_from_download(
        "https://github.com/o/r/releases/download/v1.8.84/LangLang.zip"
    ) == "https://github.com/o/r/releases/tag/v1.8.84"
    assert release_notes_url_from_download("D:/updates/file.zip") == ""


def test_update_dialog_keeps_footer_visible_and_summary_scrollable() -> None:
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(str(exc))
    info = SimpleNamespace(
        current_version="1.8.83",
        latest_version="1.8.84",
        release_notes_url="https://github.com/o/r/releases/tag/v1.8.84",
        summary=build_update_summary(
            LONG_NOTES + "\n" * 80
        ),
    )
    dialog = UpdateNotesDialog(
        root,
        info,
        on_update=lambda: None,
    )
    try:
        dialog.deiconify()
        dialog.update_idletasks()
        dialog.update()
        height = dialog.winfo_height()
        width = dialog.winfo_width()
        screen_height = dialog.winfo_screenheight()
        assert width >= 680
        assert height <= max(420, int(screen_height * 0.70)) + 2
        buttons = {
            str(widget.cget("text")): widget
            for widget in dialog.winfo_children()[2].winfo_children()
            if isinstance(widget, tk.Button)
        }
        assert {"稍后", "立即更新", "查看完整更新说明"} <= set(buttons)
        assert all(button.winfo_ismapped() for button in buttons.values())
    finally:
        dialog.close()
        root.destroy()
