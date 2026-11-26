from __future__ import annotations

import base64
import csv
import hashlib
import io
import re
from pathlib import Path
from typing import Any


FIELD_ALIASES = {
    "name": {
        "store_name",
        "name",
        "店铺名称",
        "店铺名",
        "名称",
    },
    "app_id": {
        "app_id",
        "appid",
        "小程序id",
    },
    "secret": {
        "app_secret",
        "appsecret",
        "secret",
        "密钥",
        "密钥key",
    },
    "group_name": {
        "group_name",
        "group",
        "分组",
        "分组名",
    },
}
APP_ID_PATTERN = re.compile(r"^wx[A-Za-z0-9]{16}$")
CONTROL_PATTERN = re.compile(r"[\x00-\x1f\x7f]")


def _normalize_header(value: Any) -> str:
    return re.sub(
        r"[\s_\-]+",
        "",
        str(value or "").strip().casefold(),
    )


ALIAS_LOOKUP = {
    _normalize_header(alias): field
    for field, aliases in FIELD_ALIASES.items()
    for alias in aliases
}


def _decode_text(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("文件编码无法识别，请使用 UTF-8、UTF-8 BOM 或 GB18030")


def _delimiter(line: str) -> str:
    counts = {
        ",": line.count(","),
        "\t": line.count("\t"),
        "，": line.count("，"),
        ";": line.count(";"),
        "；": line.count("；"),
        "|": line.count("|"),
    }
    delimiter, count = max(counts.items(), key=lambda item: item[1])
    return delimiter if count else ","


def _header_mapping(values: list[Any]) -> dict[str, int]:
    mapping: dict[str, int] = {}
    for index, value in enumerate(values):
        field = ALIAS_LOOKUP.get(_normalize_header(value))
        if field and field not in mapping:
            mapping[field] = index
    return mapping


def _raw_row(
    *,
    source_kind: str,
    source_name: str,
    row_number: int,
    values: dict[str, Any],
) -> dict[str, Any]:
    name = str(values.get("name") or "").strip()
    app_id = str(values.get("app_id") or "").strip()
    secret = str(values.get("secret") or "").strip()
    group_name = str(values.get("group_name") or "").strip()
    errors: list[str] = []
    if not name:
        errors.append("店铺名称为空")
    elif len(name) > 120:
        errors.append("店铺名称超过 120 字符")
    if not app_id:
        errors.append("AppID 为空")
    elif not APP_ID_PATTERN.fullmatch(app_id):
        errors.append("AppID 格式无效，应为 wx 加 16 位字母或数字")
    if not secret:
        errors.append("AppSecret 为空")
    elif len(secret) < 16 or len(secret) > 128:
        errors.append("AppSecret 长度必须为 16 到 128 字符")
    elif not re.fullmatch(r"[A-Za-z0-9_-]+", secret):
        errors.append("AppSecret 含不允许的字符")
    if CONTROL_PATTERN.search(secret):
        errors.append("AppSecret 包含控制字符")
    if group_name and len(group_name) > 80:
        errors.append("分组名称超过 80 字符")
    fingerprint = hashlib.sha256(secret.encode("utf-8")).hexdigest() if secret else ""
    return {
        "row_number": int(row_number),
        "name": name,
        "app_id": app_id,
        "secret": secret,
        "group_name": group_name,
        "source_kind": source_kind,
        "source_name": source_name,
        "parse_errors": errors,
        "secret_masked": "••••••••" if secret else "",
        "secret_fingerprint_prefix": fingerprint[:10],
    }


def _parse_alternating_credentials(
    lines: list[str],
    *,
    source_kind: str,
    source_name: str,
) -> list[dict[str, Any]]:
    """Parse the common AppID-line/AppSecret-line clipboard format."""
    cleaned = [
        line.strip().rstrip("\\").strip()
        for line in lines
        if line.strip()
    ]
    if not cleaned or not APP_ID_PATTERN.fullmatch(cleaned[0]):
        return []

    rows: list[dict[str, Any]] = []
    index = 0
    while index < len(cleaned):
        app_id = cleaned[index]
        if not APP_ID_PATTERN.fullmatch(app_id):
            index += 1
            continue
        next_value = cleaned[index + 1] if index + 1 < len(cleaned) else ""
        if next_value and not APP_ID_PATTERN.fullmatch(next_value):
            secret = next_value
            index += 2
        else:
            secret = ""
            index += 1
        rows.append(
            _raw_row(
                source_kind=source_kind,
                source_name=source_name,
                row_number=index - (1 if secret else 0),
                values={
                    "name": app_id,
                    "app_id": app_id,
                    "secret": secret,
                    "group_name": "",
                },
            )
        )
    return rows


def _parse_delimited_text(
    text: str,
    *,
    source_kind: str,
    source_name: str,
) -> list[dict[str, Any]]:
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return []
    alternating_rows = _parse_alternating_credentials(
        lines,
        source_kind=source_kind,
        source_name=source_name,
    )
    if alternating_rows:
        return alternating_rows
    delimiter = _delimiter(lines[0])
    first_values = next(
        csv.reader([lines[0]], delimiter=delimiter)
    )
    mapping = _header_mapping(first_values)
    if "app_id" in mapping:
        rows: list[dict[str, Any]] = []
        reader = csv.reader(
            io.StringIO("\n".join(lines)),
            delimiter=delimiter,
        )
        next(reader, None)
        for row_index, row in enumerate(reader, 2):
            values = {
                field: (
                    row[column_index]
                    if column_index < len(row)
                    else ""
                )
                for field, column_index in mapping.items()
            }
            if not any(str(value or "").strip() for value in values.values()):
                continue
            rows.append(
                _raw_row(
                    source_kind=source_kind,
                    source_name=source_name,
                    row_number=row_index,
                    values=values,
                )
            )
        return rows

    from ..ui.store_page import parse_store_batch_line

    rows = []
    for row_index, line in enumerate(lines, 1):
        parsed = parse_store_batch_line(line)
        if parsed is None:
            continue
        name, app_id, secret = parsed
        rows.append(
            _raw_row(
                source_kind=source_kind,
                source_name=source_name,
                row_number=row_index,
                values={
                    "name": name,
                    "app_id": app_id,
                    "secret": secret,
                    "group_name": "",
                },
            )
        )
    return rows


def _parse_xlsx(
    data: bytes,
    *,
    source_name: str,
) -> list[dict[str, Any]]:
    try:
        from openpyxl import load_workbook
    except ImportError as exc:
        raise ValueError("当前运行环境缺少 XLSX 解析组件") from exc
    try:
        workbook = load_workbook(
            io.BytesIO(data),
            read_only=True,
            data_only=True,
        )
    except Exception as exc:
        raise ValueError("XLSX 文件无法读取") from exc
    try:
        for sheet in workbook.worksheets:
            rows = list(sheet.iter_rows(values_only=True))
            if not rows:
                continue
            header_index = -1
            mapping: dict[str, int] = {}
            for index, values in enumerate(rows[:20]):
                candidate = _header_mapping(list(values))
                if "app_id" in candidate:
                    header_index = index
                    mapping = candidate
                    break
            if header_index < 0:
                continue
            output = []
            for index, values in enumerate(
                rows[header_index + 1 :],
                header_index + 2,
            ):
                current = {
                    field: (
                        values[column]
                        if column < len(values)
                        else ""
                    )
                    for field, column in mapping.items()
                }
                if not any(
                    str(value or "").strip()
                    for value in current.values()
                ):
                    continue
                output.append(
                    _raw_row(
                        source_kind="xlsx",
                        source_name=source_name,
                        row_number=index,
                        values=current,
                    )
                )
            return output
        raise ValueError(
            "XLSX 中未找到包含 AppID 或 app_id 表头的数据表"
        )
    finally:
        workbook.close()


def parse_store_import_source(
    *,
    source_kind: str,
    source_name: str,
    text: str = "",
    content_base64: str = "",
) -> tuple[list[dict[str, Any]], str]:
    kind = str(source_kind or "").strip().casefold()
    safe_name = Path(str(source_name or "")).name[:240]
    if kind == "paste":
        payload = str(text or "").encode("utf-8")
        rows = _parse_delimited_text(
            str(text or ""),
            source_kind=kind,
            source_name=safe_name or "粘贴文本",
        )
    elif kind in {"csv", "paste_csv"}:
        try:
            payload = base64.b64decode(content_base64, validate=True)
        except Exception as exc:
            raise ValueError("CSV 文件内容无效") from exc
        rows = _parse_delimited_text(
            _decode_text(payload),
            source_kind="csv",
            source_name=safe_name or "店铺数据.csv",
        )
    elif kind == "xlsx":
        try:
            payload = base64.b64decode(content_base64, validate=True)
        except Exception as exc:
            raise ValueError("XLSX 文件内容无效") from exc
        rows = _parse_xlsx(
            payload,
            source_name=safe_name or "店铺数据.xlsx",
        )
    else:
        raise ValueError("仅支持粘贴文本、CSV 或 XLSX")
    return rows, hashlib.sha256(payload).hexdigest()
