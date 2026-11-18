from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .enums import ProductImportStatus

DEFAULT_ALLOWED_PRODUCT_HOSTS = frozenset(
    {
        "channels.weixin.qq.com",
        "weixin.qq.com",
    }
)
_PRODUCT_ID_PATTERN = re.compile(r"^[0-9]{6,64}$")
_CONTROL_PATTERN = re.compile(r"[\x00-\x1f\x7f]")


@dataclass(frozen=True, slots=True)
class ProductImportItem:
    line_number: int
    raw_text: str
    status: ProductImportStatus
    product_id: str | None = None
    canonical_url: str = ""
    dedupe_key: str = ""
    reason: str = ""


@dataclass(frozen=True, slots=True)
class ProductImportPreview:
    items: tuple[ProductImportItem, ...]

    @property
    def ready_count(self) -> int:
        return sum(
            item.status is ProductImportStatus.READY
            for item in self.items
        )

    @property
    def pending_count(self) -> int:
        return sum(
            item.status is ProductImportStatus.PENDING_RESOLUTION
            for item in self.items
        )

    @property
    def invalid_count(self) -> int:
        return sum(
            item.status is ProductImportStatus.INVALID
            for item in self.items
        )

    @property
    def duplicate_count(self) -> int:
        return sum(
            item.status is ProductImportStatus.DUPLICATE
            for item in self.items
        )


def parse_product_import(
    text: str,
    *,
    allowed_hosts: frozenset[str] = DEFAULT_ALLOWED_PRODUCT_HOSTS,
) -> ProductImportPreview:
    if not isinstance(text, str):
        raise TypeError("导入内容必须是字符串")
    items: list[ProductImportItem] = []
    seen: set[str] = set()
    for line_number, raw_line in enumerate(text.splitlines(), 1):
        raw_text = raw_line.strip()
        if not raw_text:
            continue
        item = _parse_line(
            line_number,
            raw_text,
            allowed_hosts={host.casefold() for host in allowed_hosts},
        )
        if item.dedupe_key and item.dedupe_key in seen:
            item = ProductImportItem(
                line_number=item.line_number,
                raw_text=item.raw_text,
                status=ProductImportStatus.DUPLICATE,
                product_id=item.product_id,
                canonical_url=item.canonical_url,
                dedupe_key=item.dedupe_key,
                reason="与前面的导入行重复",
            )
        elif item.dedupe_key:
            seen.add(item.dedupe_key)
        items.append(item)
    return ProductImportPreview(tuple(items))


def _parse_line(
    line_number: int,
    raw_text: str,
    *,
    allowed_hosts: set[str],
) -> ProductImportItem:
    if _CONTROL_PATTERN.search(raw_text):
        return _invalid(line_number, raw_text, "包含不可见控制字符")
    if raw_text.startswith("#微信小店://"):
        if len(raw_text) > 4096:
            return _invalid(line_number, raw_text, "分享文本过长")
        return ProductImportItem(
            line_number=line_number,
            raw_text=raw_text,
            status=ProductImportStatus.PENDING_RESOLUTION,
            dedupe_key=f"share:{raw_text}",
            reason="分享文本未包含可信商品 ID，需授权解析，不能编造",
        )
    if _PRODUCT_ID_PATTERN.fullmatch(raw_text):
        return ProductImportItem(
            line_number=line_number,
            raw_text=raw_text,
            status=ProductImportStatus.READY,
            product_id=raw_text,
            dedupe_key=f"product:{raw_text}",
        )
    if raw_text.casefold().startswith(("http://", "https://")):
        return _parse_url(line_number, raw_text, allowed_hosts)
    return _invalid(
        line_number,
        raw_text,
        "既不是商品 ID，也不是安全的微信小店链接或分享文本",
    )


def _parse_url(
    line_number: int,
    raw_text: str,
    allowed_hosts: set[str],
) -> ProductImportItem:
    if len(raw_text) > 2048:
        return _invalid(line_number, raw_text, "链接过长")
    try:
        parsed = urlsplit(raw_text)
        hostname = str(parsed.hostname or "").casefold().rstrip(".")
        port = parsed.port
    except ValueError:
        return _invalid(line_number, raw_text, "链接格式不正确")
    if parsed.scheme.casefold() not in {"http", "https"} or not hostname:
        return _invalid(line_number, raw_text, "仅支持有效 HTTP/HTTPS 链接")
    if parsed.username is not None or parsed.password is not None:
        return _invalid(line_number, raw_text, "链接不能包含用户名或密码")
    if not any(
        hostname == allowed or hostname.endswith(f".{allowed}")
        for allowed in allowed_hosts
    ):
        return _invalid(line_number, raw_text, "链接域名不在微信允许名单中")
    if port is not None and port not in {80, 443}:
        return _invalid(line_number, raw_text, "链接端口不受支持")
    host = hostname
    if port and not (
        (parsed.scheme.casefold() == "http" and port == 80)
        or (parsed.scheme.casefold() == "https" and port == 443)
    ):
        host = f"{hostname}:{port}"
    query_pairs = parse_qsl(parsed.query, keep_blank_values=True)
    canonical_url = urlunsplit(
        (
            parsed.scheme.casefold(),
            host,
            parsed.path or "/",
            urlencode(sorted(query_pairs)),
            "",
        )
    )
    product_id = next(
        (
            value.strip()
            for key, value in query_pairs
            if key.casefold() == "product_id"
            and _PRODUCT_ID_PATTERN.fullmatch(value.strip())
        ),
        None,
    )
    if product_id:
        return ProductImportItem(
            line_number=line_number,
            raw_text=raw_text,
            status=ProductImportStatus.READY,
            product_id=product_id,
            canonical_url=canonical_url,
            dedupe_key=f"product:{product_id}",
        )
    return ProductImportItem(
        line_number=line_number,
        raw_text=raw_text,
        status=ProductImportStatus.PENDING_RESOLUTION,
        canonical_url=canonical_url,
        dedupe_key=f"url:{canonical_url}",
        reason="链接未包含可信商品 ID，需授权解析，不能编造",
    )


def _invalid(
    line_number: int,
    raw_text: str,
    reason: str,
) -> ProductImportItem:
    return ProductImportItem(
        line_number=line_number,
        raw_text=raw_text,
        status=ProductImportStatus.INVALID,
        reason=reason,
    )
