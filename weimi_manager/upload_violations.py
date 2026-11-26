from __future__ import annotations

import re
import unicodedata
from typing import Any, Iterable


COUNTERFEIT_VIOLATION_MARKERS = (
    "出售假冒商品",
    "假冒商品",
    "售假",
)


def normalize_upload_title(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = re.sub(r"[\u200b-\u200d\ufeff]", "", text)
    return re.sub(r"\s+", " ", text).strip().casefold()


def is_counterfeit_violation(record: dict[str, Any]) -> bool:
    reason = str(record.get("reason") or "")
    return any(marker in reason for marker in COUNTERFEIT_VIOLATION_MARKERS)


def build_upload_violation_titles(
    records: Iterable[Any],
) -> list[dict[str, Any]]:
    unique: dict[str, dict[str, Any]] = {}
    for record in records:
        if not isinstance(record, dict) or not is_counterfeit_violation(record):
            continue
        title = str(record.get("title") or "").strip()
        normalized = normalize_upload_title(title)
        if not normalized or normalized in unique:
            continue
        unique[normalized] = {
            "title": title,
            "normalized": normalized,
            "reason": str(record.get("reason") or ""),
            "record_id": str(record.get("record_id") or ""),
            "product_id": str(record.get("product_id") or ""),
            "store_id": int(record.get("store_id") or 0),
            "store_name": str(record.get("store_name") or ""),
            "head_img": str(record.get("head_img") or ""),
        }
    return sorted(unique.values(), key=lambda row: row["title"].casefold())


def build_cloud_counterfeit_rows(
    records: Iterable[Any],
    *,
    store_app_id: str,
) -> list[dict[str, Any]]:
    """Return only the whitelisted counterfeit fields safe for organization sharing."""
    app_id = str(store_app_id or "").strip()
    rows: dict[str, dict[str, Any]] = {}
    for record in records:
        if not isinstance(record, dict) or not is_counterfeit_violation(record):
            continue
        title = str(record.get("title") or "").strip()
        normalized = normalize_upload_title(title)
        if not title or not normalized:
            continue
        reason = str(record.get("reason") or "")
        reason_category = next(
            marker
            for marker in COUNTERFEIT_VIOLATION_MARKERS
            if marker in reason
        )
        product_id = str(record.get("product_id") or "")
        dedupe_key = f"{app_id}:{product_id}:{normalized}"
        rows[dedupe_key] = {
            "dedupe_key": dedupe_key,
            "normalized_title": normalized,
            "title": title,
            "reason_category": reason_category,
            "product_id": product_id,
            "store_app_id": app_id,
        }
    return sorted(rows.values(), key=lambda row: row["dedupe_key"])


def merge_cloud_counterfeit_titles(
    local_rows: Iterable[Any],
    cloud_rows: Iterable[Any],
) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for source, rows in (("local", local_rows), ("cloud", cloud_rows)):
        for row in rows:
            if not isinstance(row, dict):
                continue
            title = str(row.get("title") or "").strip()
            normalized = normalize_upload_title(
                row.get("normalized") or title
            )
            if not normalized or normalized in merged:
                continue
            merged[normalized] = {
                **dict(row),
                "title": title,
                "normalized": normalized,
                "source": str(
                    row.get("source")
                    or (
                        "cloud_counterfeit"
                        if source == "cloud"
                        else "local"
                    )
                ),
            }
    return sorted(merged.values(), key=lambda row: row["title"].casefold())


def apply_template_title_rules(
    title: Any,
    template: dict[str, Any],
) -> str:
    value = str(title or "").strip()
    if template.get("strip_title_brackets"):
        value = re.sub(r"^【[^】]*】\s*", "", value)
    for line in str(template.get("title_replace_rules") or "").splitlines():
        if "=" not in line:
            continue
        old, new = line.split("=", 1)
        if old.strip():
            value = value.replace(old.strip(), new.strip())
    prefix = str(template.get("title_prefix") or "")
    suffix = str(template.get("title_suffix") or "")
    return f"{prefix}{value}{suffix}".strip()


def find_upload_violation_title(
    title: Any,
    title_records: Iterable[Any],
) -> dict[str, Any] | None:
    normalized = normalize_upload_title(title)
    if not normalized:
        return None
    for record in title_records:
        if not isinstance(record, dict):
            continue
        candidate = normalize_upload_title(
            record.get("normalized")
            or record.get("title")
        )
        if candidate == normalized:
            return record
    return None
