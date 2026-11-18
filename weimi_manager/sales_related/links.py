from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_URL_PATTERN = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class ImportResult:
    accepted: tuple[str, ...] = ()
    duplicates: tuple[str, ...] = ()
    invalid: tuple[tuple[str, str], ...] = ()
    duplicate_entries: tuple[str, ...] = field(default_factory=tuple)


def normalize_share_link(value: str) -> str:
    text = str(value or "").strip().rstrip("，。；;,)）]】")
    if not text:
        raise ValueError("链接不能为空")
    parsed = urlsplit(text)
    if parsed.scheme.casefold() not in {"http", "https"}:
        raise ValueError("仅支持 http 或 https 商品链接")
    hostname = str(parsed.hostname or "").strip().casefold()
    if not hostname:
        raise ValueError("链接缺少有效域名")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("链接端口不正确") from exc
    netloc = hostname
    if port and not (
        parsed.scheme.casefold() == "http"
        and port == 80
        or parsed.scheme.casefold() == "https"
        and port == 443
    ):
        netloc = f"{hostname}:{port}"
    query = urlencode(sorted(parse_qsl(parsed.query, keep_blank_values=True)))
    path = parsed.path or "/"
    return urlunsplit(
        (
            parsed.scheme.casefold(),
            netloc,
            path,
            query,
            "",
        )
    )


def parse_link_import(
    text: str,
    *,
    existing_links: set[str] | None = None,
    allow_duplicates: bool = False,
) -> ImportResult:
    """Parse pasted TXT or simple CSV rows without imposing a batch limit."""
    existing = {
        normalize_share_link(value)
        for value in (existing_links or set())
        if str(value or "").strip()
    }
    accepted: list[str] = []
    duplicate_entries: list[str] = []
    duplicates: list[str] = []
    invalid: list[tuple[str, str]] = []
    seen_in_batch: set[str] = set()

    for line_number, raw_line in enumerate(str(text or "").splitlines(), 1):
        line = raw_line.strip()
        if not line:
            continue
        match = _URL_PATTERN.search(line)
        if match is None:
            invalid.append((str(line_number), "未找到 http/https 链接"))
            continue
        try:
            link = normalize_share_link(match.group(0))
        except ValueError as exc:
            invalid.append((str(line_number), str(exc)))
            continue
        duplicate = link in existing or link in seen_in_batch
        if duplicate:
            duplicates.append(link)
            if not allow_duplicates:
                continue
            duplicate_entries.append(link)
        seen_in_batch.add(link)
        accepted.append(link)
    return ImportResult(
        accepted=tuple(accepted),
        duplicates=tuple(dict.fromkeys(duplicates)),
        invalid=tuple(invalid),
        duplicate_entries=tuple(duplicate_entries),
    )
