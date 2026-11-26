from __future__ import annotations

import html
import json
import re
import time
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from .browser_runtime import browser_launch_options
from .db import Database
from .web_session import normalize_cookies


class Source1688Error(RuntimeError):
    pass


class Source1688LoginRequired(Source1688Error):
    pass


@dataclass(frozen=True, slots=True)
class Offer1688Link:
    offer_id: str
    url: str


URL_PATTERN = re.compile(r"https?://[^\s,，;；]+", re.IGNORECASE)
OFFER_PATH_PATTERN = re.compile(r"/offer/(\d{6,})\.html", re.IGNORECASE)
OFFER_ID_FIELDS = (
    "offerId",
    "offer_id",
    "offerID",
    "id",
    "productId",
    "itemId",
)
TITLE_FIELDS = (
    "subject",
    "title",
    "offerTitle",
    "productName",
    "name",
)
CATEGORY_FIELDS = (
    "categoryPath",
    "category_path",
    "categoryName",
    "category_name",
    "leafCategoryName",
    "leaf_category_name",
)
MAIN_IMAGE_FIELDS = (
    "imageList",
    "images",
    "offerImgList",
    "mainImages",
    "main_imgs",
    "headImgs",
    "head_imgs",
    "imageUrl",
    "image_url",
    "picUrl",
    "pic_url",
)
DETAIL_IMAGE_FIELDS = (
    "detailImages",
    "detail_images",
    "detailImgs",
    "detail_imgs",
    "descImgs",
    "desc_imgs",
    "detailImageList",
    "detailImageUrls",
)
SKU_CONTAINER_FIELDS = (
    "skus",
    "skuInfos",
    "skuInfoList",
    "skuInfoMap",
    "skuMap",
    "skuList",
    "skuItems",
)
IMAGE_HOST_HINTS = (
    "alicdn.com",
    "1688.com",
    "taobao.com",
    "tbcdn.cn",
)
IMAGE_SUFFIXES = (
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".bmp",
)
SKIP_LOGIN_HOSTS = (
    "login.taobao.com",
    "login.1688.com",
    "havanalogin.taobao.com",
)
OFFICIAL_REQUEST_HOSTS = (
    "1688.com",
    "alicdn.com",
)


def is_1688_official_url(url: str) -> bool:
    """Allow only 1688 pages and Alibaba's static asset CDN."""
    parsed = urlparse(str(url or ""))
    scheme = parsed.scheme.casefold()
    if scheme in {"about", "blob", "data"}:
        return True
    if scheme not in {"http", "https"}:
        return False
    host = (parsed.hostname or "").casefold().rstrip(".")
    return any(
        host == allowed or host.endswith("." + allowed)
        for allowed in OFFICIAL_REQUEST_HOSTS
    )


def _offer_id(url: str) -> str:
    parsed = urlparse(url)
    match = OFFER_PATH_PATTERN.search(parsed.path)
    if match:
        return match.group(1)
    query = parse_qs(parsed.query)
    for key in ("offerId", "offer_id", "id"):
        values = query.get(key) or []
        if values and str(values[0]).isdigit():
            return str(values[0])
    return ""


def parse_1688_links(text: str, *, max_links: int = 50) -> list[Offer1688Link]:
    links: list[Offer1688Link] = []
    seen: set[str] = set()
    for raw_url in URL_PATTERN.findall(str(text or "")):
        url = html.unescape(raw_url).rstrip("。．.，,；;）)]}")
        parsed = urlparse(url)
        host = parsed.netloc.casefold()
        if not (
            host == "1688.com"
            or host.endswith(".1688.com")
        ):
            continue
        identifier = _offer_id(url)
        if not identifier or identifier in seen:
            continue
        seen.add(identifier)
        links.append(
            Offer1688Link(
                offer_id=identifier,
                url=(
                    f"https://detail.1688.com/offer/{identifier}.html"
                    "?sk=consign&amug_biz=distribution"
                ),
            )
        )
        if len(links) >= max(1, int(max_links)):
            break
    return links


def _walk_json(value: Any, depth: int = 0):
    if depth > 16:
        return
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_json(child, depth + 1)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_json(child, depth + 1)
    elif isinstance(value, str) and value[:1] in {"{", "["} and len(value) < 4_000_000:
        try:
            nested = json.loads(value)
        except (TypeError, ValueError):
            return
        yield from _walk_json(nested, depth + 1)


def _first_text(nodes: list[dict[str, Any]], keys: tuple[str, ...]) -> str:
    for node in nodes:
        for key in keys:
            value = node.get(key)
            if value is None or value == "":
                continue
            if isinstance(value, (dict, list)):
                continue
            text = re.sub(r"\s+", " ", str(value)).strip()
            if text:
                return text
    return ""


def _normalize_image_url(value: Any) -> str:
    url = html.unescape(str(value or "")).strip()
    if not url:
        return ""
    for marker in ("&quot;", "\\u002F", "\\/"):
        url = url.replace(marker, "/" if marker != "&quot;" else "")
    if url.startswith("//"):
        url = "https:" + url
    if not url.startswith(("http://", "https://")):
        return ""
    base = url.split("?", 1)[0].casefold()
    host = urlparse(url).netloc.casefold()
    if not any(token in host for token in IMAGE_HOST_HINTS):
        return ""
    if not (
        any(base.endswith(suffix) for suffix in IMAGE_SUFFIXES)
        or any(token in url.casefold() for token in ("/img/", "/image/", "imgextra"))
    ):
        return ""
    return url


def _urls_from_value(value: Any, max_items: int = 120) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    text_values: list[str] = []

    def collect(current: Any, depth: int = 0) -> None:
        if len(result) >= max_items or depth > 8:
            return
        if isinstance(current, str):
            text_values.append(current)
            return
        if isinstance(current, dict):
            for key in (
                "url",
                "src",
                "imgUrl",
                "imageUrl",
                "picUrl",
                "image",
                "img",
            ):
                if current.get(key):
                    collect(current[key], depth + 1)
            for child in current.values():
                if isinstance(child, (dict, list, tuple)):
                    collect(child, depth + 1)
        elif isinstance(current, (list, tuple, set)):
            for child in current:
                collect(child, depth + 1)

    collect(value)
    candidates: list[str] = []
    for text in text_values:
        candidates.extend(
            re.findall(r"(?:https?:)?//[^\s\"'<>]+", html.unescape(text))
        )
        if text.startswith(("http://", "https://")):
            candidates.append(text)
    for candidate in candidates:
        url = _normalize_image_url(candidate.rstrip("),];"))
        if url and url not in seen:
            seen.add(url)
            result.append(url)
        if len(result) >= max_items:
            break
    return result


def _price_cents(value: Any, field_name: str = "") -> int:
    if value is None or value == "":
        return 0
    try:
        if isinstance(value, bool):
            return 0
        if isinstance(value, int):
            return max(0, value)
        text = str(value).strip().replace(",", "")
        if not text:
            return 0
        yuan_value = (
            isinstance(value, float)
            or "¥" in text
            or "￥" in text
            or "." in text
            or "text" in field_name.casefold()
        )
        amount = Decimal(text.replace("¥", "").replace("￥", ""))
        if yuan_value:
            amount *= Decimal("100")
        return max(
            0,
            int(amount.quantize(Decimal("1"), rounding=ROUND_HALF_UP)),
        )
    except (InvalidOperation, TypeError, ValueError):
        return 0


def _stock(value: Any) -> int:
    try:
        return max(0, int(float(str(value).replace(",", ""))))
    except (TypeError, ValueError):
        return 0


def _optional_stock(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return max(0, int(float(str(value).replace(",", ""))))
    except (TypeError, ValueError):
        return None


def _parse_attr_text(value: Any) -> list[dict[str, str]]:
    if isinstance(value, dict):
        output = []
        for key, item in value.items():
            if isinstance(item, (dict, list)):
                continue
            attr_key = str(key).strip()
            attr_value = str(item or "").strip()
            if attr_key and attr_value:
                output.append(
                    {"attr_key": attr_key, "attr_value": attr_value}
                )
        return output
    if isinstance(value, list):
        output = []
        for item in value:
            output.extend(_parse_attr_text(item))
        return output
    text = str(value or "").strip()
    if not text:
        return []
    output = []
    for part in re.split(r"[;,；，]", text):
        separator = ":" if ":" in part else "：" if "：" in part else ""
        if not separator:
            continue
        key, current = part.split(separator, 1)
        key = key.strip()
        current = current.strip()
        if key and current:
            output.append(
                {"attr_key": key, "attr_value": current}
            )
    return output


def _sku_items(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    price_keys = (
        "salePrice",
        "sale_price",
        "consignPrice",
        "consign_price",
        "discountPrice",
        "priceText",
        "price",
    )
    stock_keys = (
        "canBookCount",
        "amountOnSale",
        "stockNum",
        "stock_num",
        "quantity",
        "stock",
    )
    sku_id_keys = (
        "skuId",
        "sku_id",
        "specId",
        "spec_id",
        "id",
        "code",
    )
    attr_keys = (
        "specAttrs",
        "skuAttrs",
        "sku_attrs",
        "attributes",
        "spec",
        "specText",
    )
    spec_text_keys = (
        "specText",
        "spec_text",
        "skuName",
        "sku_name",
        "specName",
        "spec_name",
    )
    for node in nodes:
        containers = [
            node.get(key)
            for key in SKU_CONTAINER_FIELDS
            if node.get(key) not in (None, "")
        ]
        for container in containers:
            values = (
                list(container.values())
                if isinstance(container, dict)
                else list(container)
                if isinstance(container, list)
                else []
            )
            for item in values:
                if not isinstance(item, dict):
                    continue
                price_key = next(
                    (key for key in price_keys if item.get(key) not in (None, "")),
                    "",
                )
                if not price_key:
                    continue
                sale_price = _price_cents(item.get(price_key), price_key)
                if sale_price <= 0:
                    continue
                sku_id = str(
                    next(
                        (
                            item.get(key)
                            for key in sku_id_keys
                            if item.get(key) not in (None, "")
                        ),
                        "",
                    )
                )
                if not sku_id:
                    continue
                identity = sku_id
                if identity in seen:
                    continue
                attrs = []
                for key in attr_keys:
                    if key in item:
                        attrs = _parse_attr_text(item.get(key))
                        if attrs:
                            break
                if not attrs:
                    for key in spec_text_keys:
                        text = re.sub(
                            r"\s+",
                            " ",
                            str(item.get(key) or ""),
                        ).strip()
                        if text:
                            attrs = [{"attr_key": "规格", "attr_value": text}]
                            break
                if not attrs:
                    continue
                stock_value = next(
                    (
                        item.get(key)
                        for key in stock_keys
                        if item.get(key) not in (None, "")
                    ),
                    None,
                )
                stock_num = _optional_stock(stock_value)
                if stock_num is None:
                    continue
                seen.add(identity)
                output.append(
                    {
                        "out_sku_id": sku_id,
                        "sale_price": sale_price,
                        "stock_num": stock_num,
                        "sku_code": sku_id,
                        "sku_attrs": attrs,
                    }
                )
    return output


def _detail_images(
    nodes: list[dict[str, Any]],
    dom: dict[str, Any],
) -> list[str]:
    values: list[str] = []
    values.extend(_urls_from_value(dom.get("detail_images") or [], 120))
    for node in nodes:
        for key in DETAIL_IMAGE_FIELDS:
            if node.get(key):
                values.extend(_urls_from_value(node[key], 120))
        for key in ("detailHtml", "detail_html", "description", "desc"):
            value = node.get(key)
            if isinstance(value, str) and value:
                values.extend(_urls_from_value(value, 120))
    return list(dict.fromkeys(url for url in values if url))


def _category_text(nodes: list[dict[str, Any]], dom: dict[str, Any]) -> tuple[str, str]:
    direct = _first_text(nodes, CATEGORY_FIELDS)
    pieces = []
    for item in dom.get("category_paths") or []:
        text = re.sub(r"\s+", " ", str(item or "")).strip()
        if text:
            pieces.append(text)
    path = " / ".join(dict.fromkeys(pieces))
    hint = " ".join(value for value in (direct, path) if value).strip()
    return direct or path, hint


def normalize_1688_capture(
    capture: dict[str, Any],
    *,
    skip_detail_images: int = 2,
) -> dict[str, Any]:
    responses = capture.get("responses") or []
    dom = capture.get("dom") if isinstance(capture.get("dom"), dict) else {}
    nodes = list(_walk_json(responses))
    offer_id = str(capture.get("offer_id") or "")
    title = _first_text(nodes, TITLE_FIELDS)
    if not title:
        title = str(dom.get("product_title") or "").strip()
    if not title and not str(
        capture.get("target_source_id") or ""
    ).strip():
        title = str(dom.get("title") or "").strip()
    if not title:
        raise Source1688Error("未能读取 1688 商品标题")

    main_images = _urls_from_value(dom.get("main_images") or [], 30)
    for node in nodes:
        for key in MAIN_IMAGE_FIELDS:
            if node.get(key):
                main_images.extend(_urls_from_value(node[key], 30))
    all_dom_images = _urls_from_value(dom.get("images") or [], 80)
    main_images = list(dict.fromkeys(main_images + all_dom_images))[:9]

    raw_detail_images = list(
        dict.fromkeys(url for url in _detail_images(nodes, dom) if url)
    )
    skip_count = max(0, int(skip_detail_images))
    detail_images = raw_detail_images[:120]

    skus = _sku_items(nodes)
    if not skus:
        raise Source1688Error("未能读取 1688 商品真实 SKU、价格和库存")

    category_name, category_hint = _category_text(nodes, dom)
    source_url = str(capture.get("url") or "")
    return {
        "platform": "1688",
        "offer_id": offer_id,
        "source_url": source_url,
        "title": title,
        "head_imgs": main_images,
        "desc_info": {
            "imgs": detail_images or main_images[:1],
            "desc": "",
        },
        "skus": skus,
        "category_name": category_name,
        "category_hint": category_hint or title,
        "detail_skip_first": skip_count,
        "detail_images_before_skip": len(raw_detail_images),
        "detail_images_after_skip": max(
            0,
            len(raw_detail_images) - skip_count,
        ),
    }


def normalized_1688_row(payload: dict[str, Any]) -> dict[str, Any]:
    sku_prices = [
        int(item.get("sale_price") or 0)
        for item in payload.get("skus") or []
        if isinstance(item, dict) and int(item.get("sale_price") or 0) > 0
    ]
    stocks = [
        int(item.get("stock_num") or 0)
        for item in payload.get("skus") or []
        if isinstance(item, dict)
    ]
    return {
        "batch_id": f"link-{payload.get('offer_id') or int(time.time() * 1000)}",
        "source_product_id": str(payload.get("offer_id") or ""),
        "title": str(payload.get("title") or ""),
        "price": min(sku_prices) if sku_prices else 0,
        "stock": max(stocks) if stocks else 0,
        "platform": "1688",
        "payload": payload,
        "raw": {
            "offer_id": payload.get("offer_id"),
            "source_url": payload.get("source_url"),
            "category_name": payload.get("category_name"),
        },
        "source_file": "1688链接",
        "source_identity": "offer_id",
    }


CATEGORY_SYNONYMS = {
    "女": ("女装", "女士", "女性", "女款"),
    "男": ("男装", "男士", "男性", "男款"),
    "裤": ("裤子", "长裤", "短裤", "打底裤", "休闲裤"),
    "鞋": ("鞋靴", "运动鞋", "休闲鞋", "女鞋", "男鞋"),
    "包": ("箱包", "背包", "手提包", "单肩包"),
    "帽": ("帽子", "遮阳帽", "鸭舌帽"),
    "衣": ("上衣", "服装", "衣服"),
    "家居": ("家纺", "家居用品", "日用百货"),
    "美妆": ("化妆品", "护肤", "彩妆"),
}


def _category_terms(value: str) -> set[str]:
    compact = re.sub(r"[^\w\u4e00-\u9fff]+", " ", str(value or "").casefold())
    terms = {
        word
        for word in re.split(r"\s+", compact)
        if len(word) >= 2
    }
    for run in re.findall(r"[\u4e00-\u9fff]{2,}", compact):
        for size in (2, 3, 4):
            terms.update(
                run[index : index + size]
                for index in range(max(0, len(run) - size + 1))
            )
    for trigger, synonyms in CATEGORY_SYNONYMS.items():
        if trigger in str(value or ""):
            terms.update(synonyms)
    return terms


def rank_category_candidates(
    source_text: str,
    candidates: list[dict[str, Any]],
    *,
    limit: int = 20,
) -> list[dict[str, Any]]:
    source = re.sub(r"\s+", " ", str(source_text or "")).strip()
    source_terms = _category_terms(source)
    ranked: list[dict[str, Any]] = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        category_id = str(candidate.get("category_id") or "").strip()
        path = str(candidate.get("path") or candidate.get("name") or "").strip()
        if not category_id or not path:
            continue
        candidate_terms = _category_terms(path)
        common = source_terms & candidate_terms
        score = float(sum(min(4, len(term)) for term in common))
        for term in sorted(common, key=len, reverse=True)[:8]:
            if term in source and term in path:
                score += 1.5
        if path and path in source:
            score += 8.0
        name = str(candidate.get("name") or "").strip()
        if name and name in source:
            score += 6.0
        if score <= 0:
            continue
        ranked.append({**candidate, "match_score": round(score, 3)})
    ranked.sort(
        key=lambda item: (
            float(item.get("match_score") or 0),
            -len(str(item.get("path") or "")),
        ),
        reverse=True,
    )
    return ranked[: max(1, int(limit))]


PAGE_SNAPSHOT_SCRIPT = r"""
() => {
  const clean = (value) => String(value || '').replace(/\s+/g, ' ').trim();
  const imageRows = (nodes) => nodes.map((img) => ({
    src: img.currentSrc || img.src ||
      img.getAttribute('data-src') ||
      img.getAttribute('data-lazy-src') ||
      img.getAttribute('data-original') ||
      '',
    width: img.naturalWidth || img.width || 0,
    height: img.naturalHeight || img.height || 0,
    alt: clean(img.alt || ''),
  })).filter((item) => item.src);
  const allImages = imageRows([...document.images]);
  const mainNodes = [...document.querySelectorAll(
    '[class*="gallery"] img,[class*="Gallery"] img,' +
    '[class*="main-image"] img,[class*="mainImage"] img,' +
    '[class*="preview"] img,[class*="pic"] img'
  )];
  const detailNodes = [...document.querySelectorAll(
    '[class*="detail"] img,[id*="detail"] img,[class*="desc"] img,[id*="desc"] img'
  )];
  const categoryNodes = [...document.querySelectorAll(
    '[class*="breadcrumb"] a,[class*="crumb"] a,[class*="category"] a,[class*="category-path"]'
  )];
  const productTitle = (() => {
    for (const node of document.querySelectorAll('script[type="application/ld+json"]')) {
      try {
        const data = JSON.parse(node.textContent || '{}');
        const values = Array.isArray(data) ? data : [data];
        const product = values.find((item) => item && item['@type'] === 'Product');
        if (clean(product?.name)) return clean(product.name);
      } catch (_error) {
        // Ignore malformed structured data and use a target-title selector.
      }
    }
    const selectors = [
      '[data-spm*="title"]',
      '[data-testid*="title"]',
      '[class*="offer-title"]',
      '[class*="product-title"]',
      '[class*="ItemHeader--mainTitle"]',
      '.tb-main-title',
    ];
    for (const selector of selectors) {
      const node = document.querySelector(selector);
      const text = clean(node?.innerText || node?.textContent);
      if (text) return text;
    }
    return '';
  })();
  const categoryPaths = categoryNodes.map((node) => clean(node.innerText)).filter(Boolean);
  return {
    title: productTitle,
    product_title: productTitle,
    url: location.href,
    text: clean(document.body?.innerText || '').slice(0, 30000),
    images: allImages.slice(0, 120),
    main_images: imageRows(mainNodes).slice(0, 30),
    detail_images: imageRows(detailNodes).slice(0, 120),
    category_paths: [...new Set(categoryPaths)].slice(0, 20),
  };
}
"""


class Link1688Collector:
    def __init__(self, db: Database, data_dir: Path) -> None:
        self.db = db
        self.data_dir = Path(data_dir)

    @staticmethod
    def _looks_like_login(url: str, text: str) -> bool:
        host = urlparse(url).netloc.casefold()
        if any(host == item or host.endswith("." + item) for item in SKIP_LOGIN_HOSTS):
            return True
        markers = ("验证码", "安全验证", "punish", "login_jump", "请登录")
        return any(marker.casefold() in text.casefold() for marker in markers)

    def collect(
        self,
        account: dict[str, Any],
        links: list[Offer1688Link],
        *,
        skip_detail_images: int = 2,
        progress: Callable[[str, int, int], None] | None = None,
    ) -> list[dict[str, Any]]:
        if not links:
            return []
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise Source1688Error("缺少 Playwright 本地运行环境") from exc
        payload = self.db.get_procurement_session(int(account["id"]))
        if not payload:
            raise Source1688LoginRequired("请先在采购中心登录 1688 账号")
        profile = Path(
            account.get("profile_dir")
            or self.data_dir
            / "procurement_profiles"
            / f"account_{account['id']}"
        )
        profile.mkdir(parents=True, exist_ok=True)
        cookies = normalize_cookies(payload.get("cookies") or [])
        results: list[dict[str, Any]] = []
        playwright = sync_playwright().start()
        context = None
        try:
            context = playwright.chromium.launch_persistent_context(
                str(profile),
                **browser_launch_options(
                    headless=False,
                    args=[
                        "--start-maximized",
                        "--no-first-run",
                        "--disable-blink-features=AutomationControlled",
                    ],
                ),
            )
            if cookies:
                context.add_cookies(
                    [
                        {
                            "name": cookie["name"],
                            "value": cookie["value"],
                            "domain": cookie.get("domain") or ".1688.com",
                            "path": cookie.get("path") or "/",
                        }
                        for cookie in cookies
                    ]
                )
            page = context.pages[0] if context.pages else context.new_page()
            for index, link in enumerate(links, 1):
                if progress:
                    progress(
                        f"正在采集 1688 商品 {index}/{len(links)}",
                        index,
                        len(links),
                    )
                captured: list[dict[str, Any]] = []

                def on_response(response) -> None:
                    url = response.url.casefold()
                    if "1688.com" not in url and "alicdn.com" not in url:
                        return
                    if not any(
                        token in url
                        for token in (
                            "offer",
                            "mtop",
                            "detail",
                            "product",
                            "sku",
                            "h5api",
                        )
                    ):
                        return
                    content_type = response.headers.get("content-type", "").casefold()
                    if "json" not in content_type and "javascript" not in content_type:
                        return
                    length = int(response.headers.get("content-length") or 0)
                    if length > 8_000_000:
                        return
                    try:
                        text = response.text()
                        if len(text) > 8_000_000:
                            return
                        match = re.search(r"\((\{.*\}|\[.*\])\)\s*;?\s*$", text, re.S)
                        if match:
                            text = match.group(1)
                        data = json.loads(text)
                    except Exception:
                        return
                    captured.append({"url": response.url, "data": data})

                def route_official(route) -> None:
                    if is_1688_official_url(route.request.url):
                        route.continue_()
                    else:
                        route.abort()

                page.route("**/*", route_official)
                page.on("response", on_response)
                try:
                    page.goto(link.url, wait_until="domcontentloaded", timeout=60000)
                    page.wait_for_timeout(3500)
                    for _ in range(10):
                        page.mouse.wheel(0, 1300)
                        page.wait_for_timeout(550)
                    dom = page.evaluate(PAGE_SNAPSHOT_SCRIPT)
                    current_url = str(page.url or "")
                    if self._looks_like_login(
                        current_url,
                        str((dom or {}).get("text") or ""),
                    ):
                        raise Source1688LoginRequired(
                            "1688 登录已失效或触发安全验证，请到采购中心重新登录"
                        )
                    normalized = normalize_1688_capture(
                        {
                            "offer_id": link.offer_id,
                            "url": link.url,
                            "dom": dom,
                            "responses": captured,
                        },
                        skip_detail_images=skip_detail_images,
                    )
                    results.append(
                        {
                            "ok": True,
                            "offer_id": link.offer_id,
                            "source_url": link.url,
                            "data": normalized,
                        }
                    )
                except Source1688LoginRequired:
                    raise
                except Exception as exc:
                    results.append(
                        {
                            "ok": False,
                            "offer_id": link.offer_id,
                            "source_url": link.url,
                            "error": str(exc)[:500],
                        }
                    )
                finally:
                    try:
                        page.unroute("**/*", route_official)
                    except Exception:
                        pass
                    try:
                        page.remove_listener("response", on_response)
                    except Exception:
                        pass
        finally:
            if context is not None:
                try:
                    latest = normalize_cookies(context.cookies())
                    if latest:
                        payload["cookies"] = latest
                except Exception:
                    pass
                try:
                    context.close()
                except Exception:
                    pass
            if payload.get("cookies"):
                self.db.set_procurement_session(
                    int(account["id"]),
                    payload,
                    "已登录",
                    "",
                )
            playwright.stop()
        return results

