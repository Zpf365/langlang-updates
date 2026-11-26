"""Build compact, product-facing update summaries from release notes."""

from __future__ import annotations

import re
from typing import Any, Iterable


def _plain_text(value: Any) -> str:
    text = str(value or "")
    text = re.sub(r"!\[[^\]]*]\([^)]*\)", "", text)
    text = re.sub(r"\[([^\]]+)]\([^)]*\)", r"\1", text)
    text = text.replace("`", "").replace("**", "").replace("__", "")
    text = re.sub(r"^[#>*+\-\s]+", "", text)
    return re.sub(r"\s+", " ", text).strip(" ：:；;")


def _is_noise(text: str) -> bool:
    lowered = text.casefold()
    return any(
        marker in lowered
        for marker in (
            "sha256",
            "pyinstaller",
            "候选状态",
            "未发布",
            "测试通过",
            "passed",
            "skipped",
            "代码路径已验证",
        )
    ) or len(text) > 180


def _normalize_groups(
    value: Any,
    *,
    max_points: int,
) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    if all(isinstance(item, str) for item in value):
        points = [
            text
            for item in value
            if (text := _plain_text(item)) and not _is_noise(text)
        ][:max_points]
        return [{"title": "主要更新", "items": points}] if points else []
    groups: list[dict[str, Any]] = []
    remaining = max_points
    for item in value:
        if remaining <= 0 or not isinstance(item, dict):
            continue
        title = _plain_text(item.get("title")) or "主要更新"
        raw_items = item.get("items")
        if not isinstance(raw_items, list):
            raw_items = []
        points = [
            text
            for raw in raw_items
            if (text := _plain_text(raw)) and not _is_noise(text)
        ][:remaining]
        if points:
            groups.append({"title": title, "items": points})
            remaining -= len(points)
    return groups


def build_update_summary(
    notes: str,
    *,
    max_points: int = 6,
) -> list[dict[str, Any]]:
    """Return at most ``max_points`` high-level release-note points."""
    sections: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    in_code = False
    for raw_line in str(notes or "").replace("\r\n", "\n").splitlines():
        line = raw_line.strip()
        if line.startswith("```"):
            in_code = not in_code
            continue
        if in_code or not line:
            continue
        heading = re.match(r"^#{2,3}\s+(.+)$", line)
        if heading:
            title = _plain_text(heading.group(1))
            if title and not any(
                marker in title for marker in ("边界", "候选", "测试")
            ):
                current = {"title": title, "items": []}
                sections.append(current)
            else:
                current = None
            continue
        bullet = re.match(r"^[-*+]\s+(.+)$", line)
        if bullet and current is not None:
            text = _plain_text(bullet.group(1))
            if text and not _is_noise(text):
                current["items"].append(text)

    if not sections:
        points = []
        for line in str(notes or "").splitlines():
            text = _plain_text(line)
            if text and not text.startswith("#") and not _is_noise(text):
                points.append(text)
            if len(points) >= max_points:
                break
        return [{"title": "主要更新", "items": points}] if points else []

    groups: list[dict[str, Any]] = []
    remaining = max_points
    item_index = 0
    while remaining > 0:
        added = False
        for section in sections:
            items = section.get("items") or []
            if item_index >= len(items):
                continue
            group = next(
                (item for item in groups if item["title"] == section["title"]),
                None,
            )
            if group is None:
                group = {"title": section["title"], "items": []}
                groups.append(group)
            group["items"].append(items[item_index])
            remaining -= 1
            added = True
            if remaining <= 0:
                break
        if not added:
            break
        item_index += 1
    return groups


def normalize_update_summary(
    value: Any,
    *,
    max_points: int = 6,
) -> list[dict[str, Any]]:
    return _normalize_groups(value, max_points=max_points)


def summary_point_count(groups: Iterable[dict[str, Any]]) -> int:
    return sum(
        len(group.get("items") or [])
        for group in groups
        if isinstance(group, dict)
    )


def release_notes_url_from_download(download_url: str) -> str:
    match = re.match(
        r"^(https://github\.com/[^/]+/[^/]+/releases/download/)"
        r"([^/]+)/[^/?#]+",
        str(download_url or "").strip(),
        re.IGNORECASE,
    )
    if not match:
        return ""
    return f"{match.group(1).replace('/download/', '/tag/')}{match.group(2)}"
