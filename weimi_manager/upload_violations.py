from __future__ import annotations

import json
import re
import unicodedata
from typing import Any, Iterable


COUNTERFEIT_VIOLATION_MARKERS = (
    "出售假冒商品",
    "假冒商品",
    "售假",
)
DEMO_STORE_APP_IDS = {"demo-store"}


def normalize_upload_title(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = re.sub(r"[\u200b-\u200d\ufeff]", "", text)
    return re.sub(r"\s+", " ", text).strip().casefold()


def is_counterfeit_violation(record: dict[str, Any]) -> bool:
    reason = (
        f"{record.get('reason') or ''} "
        f"{record.get('reason_category') or ''}"
    )
    return any(marker in reason for marker in COUNTERFEIT_VIOLATION_MARKERS)


def _store_value(store: Any, key: str) -> Any:
    if isinstance(store, dict):
        return store.get(key)
    return getattr(store, key, None)


def is_real_logged_in_store(store: Any) -> bool:
    """Return true only for a non-demo store with a live official session."""
    app_id = str(_store_value(store, "app_id") or "").strip()
    if not app_id or app_id.casefold() in DEMO_STORE_APP_IDS:
        return False
    status = (
        f"{_store_value(store, 'web_status') or ''} "
        f"{_store_value(store, 'status') or ''}"
    )
    return "已登录" in status


def real_logged_in_stores(stores: Iterable[Any]) -> list[Any]:
    return [store for store in stores if is_real_logged_in_store(store)]


def filter_active_violation_records(
    records: Iterable[Any],
    stores: Iterable[Any],
) -> list[dict[str, Any]]:
    """Keep records that belong to a currently logged-in real store."""
    active_store_ids: set[int] = set()
    active_app_ids: set[str] = set()
    for store in real_logged_in_stores(stores):
        app_id = str(_store_value(store, "app_id") or "").strip()
        try:
            store_id = int(_store_value(store, "id") or 0)
        except (TypeError, ValueError):
            store_id = 0
        if store_id > 0:
            active_store_ids.add(store_id)
        active_app_ids.add(app_id.casefold())

    filtered: list[dict[str, Any]] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        app_id = str(record.get("store_app_id") or "").strip()
        if app_id.casefold() in DEMO_STORE_APP_IDS:
            continue
        try:
            store_id = int(record.get("store_id") or 0)
        except (TypeError, ValueError):
            store_id = 0
        if (
            store_id in active_store_ids
            or (app_id and app_id.casefold() in active_app_ids)
        ):
            filtered.append(dict(record))
    return filtered


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
        if normalized == normalize_upload_title(record.get("reason")):
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
        product_id = str(record.get("product_id") or "").strip()
        if (
            not title
            or not normalized
            or normalized == normalize_upload_title(
                record.get("reason")
            )
        ):
            continue
        reason = str(record.get("reason") or "")
        reason_category = next(
            marker
            for marker in COUNTERFEIT_VIOLATION_MARKERS
            if marker in reason
        )
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
            if source == "cloud" and not is_counterfeit_violation(row):
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


def load_upload_violation_title_records(db: Any) -> list[dict[str, Any]]:
    """Load the current local and organization counterfeit title index."""

    def read_rows(key: str) -> list[dict[str, Any]]:
        get_setting = getattr(db, "get_setting", None)
        if not callable(get_setting):
            return []
        try:
            value = json.loads(get_setting(key, "[]"))
        except (TypeError, ValueError, json.JSONDecodeError):
            return []
        if not isinstance(value, list):
            return []
        return [dict(row) for row in value if isinstance(row, dict)]

    list_stores = getattr(db, "list_stores", None)
    stores = list_stores() if callable(list_stores) else None
    records = read_rows("violation_library")
    local_rows = read_rows("upload_violation_titles")
    if stores is not None:
        records = filter_active_violation_records(records, stores)
        local_rows = filter_active_violation_records(local_rows, stores)

    local_by_title: dict[str, dict[str, Any]] = {}
    for row in [*local_rows, *build_upload_violation_titles(records)]:
        title = str(row.get("title") or "").strip()
        normalized = normalize_upload_title(
            row.get("normalized") or title
        )
        if not normalized:
            continue
        local_by_title[normalized] = {
            **row,
            "title": title,
            "normalized": normalized,
            "source": str(row.get("source") or "local"),
        }
    return merge_cloud_counterfeit_titles(
        local_by_title.values(),
        read_rows("cloud_counterfeit_violation_titles"),
    )


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


def mandatory_upload_violation_issue(
    title: Any,
    template: dict[str, Any],
    title_records: Iterable[Any],
) -> str:
    """Return a blocking issue for real counterfeit titles regardless of toggles."""
    original = str(title or "").strip()
    candidate_title = apply_template_title_rules(original, template)
    for value in dict.fromkeys((candidate_title, original)):
        matched = find_upload_violation_title(value, title_records)
        if matched is None:
            continue
        reason = str(matched.get("reason") or "出售假冒商品")
        return f"云违规标题过滤命中：{reason}"
    return ""
