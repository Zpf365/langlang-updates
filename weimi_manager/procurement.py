from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable

import requests

from .browser_runtime import browser_launch_options
from .db import Database
from .web_session import normalize_cookies

PLATFORM_LOGIN_URLS = {
    "1688": "https://login.1688.com/member/signin.htm",
    "taobao": "https://login.taobao.com/member/login.jhtml",
    "pinduoduo": "https://mobile.yangkeduo.com/login.html",
    "jd": "https://passport.jd.com/new/login.aspx",
}

PLATFORM_HOME_URLS = {
    "1688": "https://work.1688.com/",
    "taobao": "https://i.taobao.com/my_taobao.htm",
    "pinduoduo": "https://mobile.yangkeduo.com/personal.html",
    "jd": "https://home.jd.com/",
}

PLATFORM_ORDER_URLS = {
    "1688": "https://trade.1688.com/order/buyer_order_list.htm",
    "taobao": "https://buyertrade.taobao.com/trade/itemlist/list_bought_items.htm",
    "pinduoduo": "https://mobile.yangkeduo.com/orders.html",
    "jd": "https://order.jd.com/center/list.action",
}

MTOP_HOSTS = {
    "1688": "https://h5api.m.1688.com",
    "taobao": "https://h5api.m.taobao.com",
}

PLATFORM_COOKIE_DOMAINS = {
    "1688": ".1688.com",
    "taobao": ".taobao.com",
    "pinduoduo": ".yangkeduo.com",
    "jd": ".jd.com",
}


def _default_cookie_domain(platform: str) -> str:
    return PLATFORM_COOKIE_DOMAINS.get(platform, "")

MTOP_APIS = {
    "1688": (
        "mtop.1688.MtopOrderService.queryOrder",
        "mtop.1688.DeliveryOrderService.queryDeliveryOrderPackList",
        "mtop.cbu.logistics.cbutradedeliveryorderservice.queryreceivepackagelist",
        "mtop.cbu.logistics.cbutradedeliveryorderservice.queryreceivepackagestatistic",
    ),
    "taobao": (
        "mtop.taobao.trade.fullinfo.get",
        "mtop.taobao.logistics.address.search",
    ),
}

PROCUREMENT_AMOUNT_FIELDS: dict[str, tuple[tuple[str, str], ...]] = {
    "1688": (
        ("payAmount", "cent"),
        ("pay_amount", "cent"),
        ("amount", "cent"),
        ("totalAmount", "cent"),
    ),
    "taobao": (
        ("payAmount", "cent"),
        ("pay_amount", "cent"),
        ("actualFee", "yuan"),
        ("payment", "yuan"),
        ("amount", "yuan"),
        ("totalAmount", "yuan"),
    ),
}

PLATFORM_ORDER_ID_FIELDS: dict[str, tuple[str, ...]] = {
    "1688": (
        "tradeNo",
        "trade_no",
        "orderId",
        "order_id",
        "bizOrderId",
        "mainOrderId",
    ),
    "taobao": (
        "tradeNo",
        "trade_no",
        "orderId",
        "order_id",
        "tradeId",
        "trade_id",
        "tid",
        "bizOrderId",
    ),
}


class ProcurementError(RuntimeError):
    pass


def cookie_value(cookies: list[dict[str, Any]], name: str) -> str:
    for cookie in cookies:
        if str(cookie.get("name") or "") == name:
            return str(cookie.get("value") or "")
    return ""


def cookie_header(cookies: list[dict[str, Any]]) -> str:
    return "; ".join(
        f"{cookie.get('name')}={cookie.get('value')}"
        for cookie in cookies
        if cookie.get("name") and cookie.get("value") is not None
    )


def _first(data: dict[str, Any], keys: tuple[str, ...], default: Any = "") -> Any:
    for key in keys:
        value = data.get(key)
        if value is not None and value != "":
            return value
    return default


def _int(value: Any) -> int:
    try:
        return int(round(float(value or 0)))
    except (TypeError, ValueError):
        return 0


def parse_procurement_amount_cents(value: Any, source_unit: str) -> int:
    """Parse a platform amount without binary floating-point rounding."""
    try:
        text = str(value).strip().replace(",", "").replace("¥", "").replace("￥", "")
        amount = Decimal(text)
        if source_unit == "yuan":
            amount *= Decimal("100")
        return int(amount.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    except (InvalidOperation, TypeError, ValueError):
        return 0


def _amount_cents(platform: str, data: dict[str, Any]) -> tuple[int, str, str]:
    fields = PROCUREMENT_AMOUNT_FIELDS.get(platform, ())
    for field, source_unit in fields:
        value = data.get(field)
        if value is None or value == "":
            continue
        return parse_procurement_amount_cents(value, source_unit), field, "cent"
    return 0, "", "cent"


def _walk_dicts(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_dicts(child)


def normalize_procurement_records(
    platform: str,
    payloads: list[Any],
) -> list[dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    for payload in payloads:
        for item in _walk_dicts(payload):
            trade_no = str(
                _first(
                    item,
                    PLATFORM_ORDER_ID_FIELDS.get(platform, ()),
                )
            )
            mail_no = str(
                _first(
                    item,
                    (
                        "mailNo",
                        "mail_no",
                        "waybillNo",
                        "waybill_no",
                        "billNo",
                        "trackingNumber",
                    ),
                )
            )
            pickup_code = str(
                _first(
                    item,
                    (
                        "sendingCode",
                        "sending_code",
                        "pickupCode",
                        "pickup_code",
                        "takeCode",
                        "code",
                    ),
                )
            )
            goods_title = str(
                _first(
                    item,
                    (
                        "itemName",
                        "item_name",
                        "goodsTitle",
                        "goods_title",
                        "productName",
                        "title",
                        "subject",
                    ),
                )
            )
            if not (trade_no or mail_no):
                continue
            external_id = trade_no or mail_no
            pay, pay_source, pay_unit = _amount_cents(platform, item)
            status = str(
                _first(item, ("status", "tradeStatus", "orderStatus", "logisticsStatus"))
            )
            records[external_id] = {
                "external_order_id": external_id,
                "trade_no": trade_no or external_id,
                "mail_no": mail_no,
                "pickup_code": pickup_code,
                "station_name": str(
                    _first(
                        item,
                        ("stationName", "station_name", "pickupStation", "siteName"),
                    )
                ),
                "station_phone": str(
                    _first(item, ("stationPhone", "station_phone", "phone", "mobile"))
                ),
                "status": status,
                "status_text": str(
                    _first(item, ("statusText", "status_text", "statusName"), default=status)
                ),
                "goods_title": goods_title,
                "goods_image": str(
                    _first(item, ("itemImg", "item_img", "goodsImage", "picUrl"))
                ),
                "goods_count": _int(
                    _first(item, ("itemNum", "item_num", "quantity", "buyAmount"))
                ),
                "pay_amount": pay,
                "pay_amount_source": pay_source,
                "pay_amount_unit": pay_unit,
                "seller": str(
                    _first(item, ("seller", "sellerName", "companyName", "supplier"))
                ),
                "buyer": str(
                    _first(item, ("buyer", "buyerName", "receiverName", "rcvName"))
                ),
                "fetched_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "raw": item,
                "platform": platform,
            }
    return list(records.values())


class ProcurementWebSession:
    def __init__(self, db: Database, data_dir: Path):
        self.db = db
        self.data_dir = data_dir
        self.use_system_proxy = False

    def login(self, account: dict[str, Any]) -> dict[str, Any]:
        try:
            from playwright.sync_api import Error as PlaywrightError
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise ProcurementError("缺少 Playwright 本地运行环境") from exc
        platform = str(account["platform"])
        login_url = PLATFORM_LOGIN_URLS[platform]
        profile = Path(
            account.get("profile_dir")
            or self.data_dir
            / "procurement_profiles"
            / f"account_{account['id']}"
        )
        profile.mkdir(parents=True, exist_ok=True)
        playwright = sync_playwright().start()
        context = None
        cookies: list[dict[str, Any]] = []
        current_url = login_url
        try:
            context = playwright.chromium.launch_persistent_context(
                str(profile),
                **browser_launch_options(
                    headless=False,
                    no_viewport=True,
                    args=["--start-maximized", "--no-first-run"],
                ),
            )
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(login_url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(900)
            login_name = str(
                account.get("login_id")
                or account.get("label")
                or ""
            ).strip()
            try:
                password = self.db.get_procurement_account_password(
                    int(account["id"])
                )
            except Exception:
                password = ""
            username_selectors = (
                "#fm-login-id",
                "input[name='account']",
                "input[name='loginId']",
                "input[name='username']",
                "#username",
                "#loginname",
            )
            password_selectors = (
                "#fm-login-password",
                "input[name='password']",
                "input[name='loginPassword']",
                "#password",
                "#nloginpwd",
            )
            for frame in context.pages[0].frames:
                if login_name:
                    for selector in username_selectors:
                        locator = frame.locator(selector).first
                        try:
                            if locator.count() and locator.is_visible():
                                locator.fill(login_name)
                                break
                        except Exception:
                            continue
                if password:
                    for selector in password_selectors:
                        locator = frame.locator(selector).first
                        try:
                            if locator.count() and locator.is_visible():
                                locator.fill(password)
                                break
                        except Exception:
                            continue
            while context.pages:
                time.sleep(1)
                try:
                    cookies = normalize_cookies(context.cookies())
                    for candidate in context.pages:
                        if not candidate.is_closed() and candidate.url.startswith("http"):
                            current_url = candidate.url
                except PlaywrightError:
                    break
        finally:
            if context is not None:
                try:
                    latest = normalize_cookies(context.cookies())
                    if latest:
                        cookies = latest
                except Exception:
                    pass
                try:
                    context.close()
                except Exception:
                    pass
            playwright.stop()
        if not cookies:
            self.db.update_procurement_account(
                int(account["id"]),
                status="未登录",
                last_error="没有捕获到登录 Cookie",
            )
            raise ProcurementError("没有捕获到登录 Cookie")
        markers = {
            "1688": {"_m_h5_tk", "cookie2", "__cn_logon__", "lgc", "unb"},
            "taobao": {"_m_h5_tk", "cookie2", "tracknick", "unb"},
            "pinduoduo": {"PDDAccessToken", "pdd_user_id", "api_uid"},
            "jd": {"pt_key", "pt_pin", "thor"},
        }.get(platform, set())
        names = {str(cookie.get("name") or "") for cookie in cookies}
        status = "已登录" if names.intersection(markers) else "待验证"
        payload = {
            "cookies": cookies,
            "source_url": current_url,
            "saved_at": int(time.time()),
            "platform": platform,
        }
        self.db.set_procurement_session(
            int(account["id"]),
            payload,
            status,
            "",
        )
        return payload

    def check_status(self, account: dict[str, Any]) -> str:
        payload = self.db.get_procurement_session(int(account["id"]))
        if not payload:
            status = "未登录"
            self.db.update_procurement_account(
                int(account["id"]),
                status=status,
                last_checked_at=datetime.now().isoformat(timespec="seconds"),
                last_error="本机没有采购账号登录会话",
            )
            return status
        platform = str(account["platform"])
        profile = Path(
            account.get("profile_dir")
            or self.data_dir
            / "procurement_profiles"
            / f"account_{account['id']}"
        )
        profile.mkdir(parents=True, exist_ok=True)
        cookies = normalize_cookies(payload.get("cookies") or [])
        status = "未登录"
        try:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as playwright:
                context = playwright.chromium.launch_persistent_context(
                    str(profile),
                    **browser_launch_options(
                        headless=True,
                        viewport={"width": 1360, "height": 900},
                        args=["--no-first-run"],
                    ),
                )
                try:
                    context.add_cookies(
                        [
                            {
                                "name": cookie["name"],
                                "value": cookie["value"],
                                "domain": cookie.get("domain")
                                or _default_cookie_domain(platform),
                                "path": cookie.get("path") or "/",
                            }
                            for cookie in cookies
                        ]
                    )
                    page = (
                        context.pages[0]
                        if context.pages
                        else context.new_page()
                    )
                    page.goto(
                        PLATFORM_HOME_URLS.get(
                            platform,
                            PLATFORM_ORDER_URLS[platform],
                        ),
                        wait_until="domcontentloaded",
                        timeout=45000,
                    )
                    page.wait_for_timeout(1200)
                    final_url = page.url.lower()
                    names = {
                        str(cookie.get("name") or "")
                        for cookie in context.cookies()
                    }
                    login_markers = {
                        "1688": {
                            "_m_h5_tk",
                            "cookie2",
                            "__cn_logon__",
                            "lgc",
                            "unb",
                        },
                        "taobao": {
                            "_m_h5_tk",
                            "cookie2",
                            "tracknick",
                            "unb",
                        },
                        "pinduoduo": {
                            "PDDAccessToken",
                            "pdd_user_id",
                            "api_uid",
                        },
                        "jd": {"pt_key", "pt_pin", "thor"},
                    }.get(platform, set())
                    logged_out = "login." in final_url or "passport." in final_url
                    status = (
                        "已登录"
                        if names.intersection(login_markers) and not logged_out
                        else "已掉线"
                    )
                finally:
                    context.close()
        except Exception as exc:
            status = "异常"
            detail = str(exc)[:500]
        else:
            detail = ""
        self.db.update_procurement_account(
            int(account["id"]),
            status=status,
            last_checked_at=datetime.now().isoformat(timespec="seconds"),
            last_error=detail,
        )
        return status

    def bind_alipay(self, account: dict[str, Any]) -> dict[str, Any]:
        profile_root = Path(
            account.get("profile_dir")
            or self.data_dir
            / "procurement_profiles"
            / f"account_{account['id']}"
        )
        profile = profile_root / "alipay_profile"
        profile.mkdir(parents=True, exist_ok=True)
        cookies: list[dict[str, Any]] = []
        try:
            from playwright.sync_api import Error as PlaywrightError
            from playwright.sync_api import sync_playwright

            with sync_playwright() as playwright:
                context = playwright.chromium.launch_persistent_context(
                    str(profile),
                    **browser_launch_options(
                        headless=False,
                        no_viewport=True,
                        args=["--start-maximized", "--no-first-run"],
                    ),
                )
                try:
                    page = (
                        context.pages[0]
                        if context.pages
                        else context.new_page()
                    )
                    page.goto(
                        "https://auth.alipay.com/login/index.htm",
                        wait_until="domcontentloaded",
                        timeout=60000,
                    )
                    while context.pages:
                        time.sleep(1)
                        try:
                            cookies = normalize_cookies(context.cookies())
                        except PlaywrightError:
                            break
                finally:
                    try:
                        latest = normalize_cookies(context.cookies())
                        if latest:
                            cookies = latest
                    except Exception:
                        pass
                    context.close()
        except Exception as exc:
            raise ProcurementError(f"支付宝授权窗口启动失败：{exc}") from exc
        if not cookies:
            raise ProcurementError("没有捕获到支付宝登录 Cookie")
        uid = ""
        for cookie in cookies:
            name = str(cookie.get("name") or "").lower()
            if "uid" in name or "user_id" in name:
                uid = str(cookie.get("value") or "")[:128]
                break
        self.db.set_procurement_alipay_session(
            int(account["id"]),
            {
                "cookies": cookies,
                "saved_at": int(time.time()),
                "platform": account.get("platform") or "",
            },
            uid,
        )
        return {
            "uid": uid,
            "cookie_count": len(cookies),
            "bound": True,
        }

    @staticmethod
    def payload_cookie_header(payload: dict[str, Any] | None) -> str:
        if not payload:
            return ""
        return cookie_header(normalize_cookies(payload.get("cookies") or []))

    def capture_page_responses(
        self,
        account: dict[str, Any],
        url: str | None = None,
        scroll_count: int = 8,
    ) -> list[dict[str, Any]]:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise ProcurementError("缺少 Playwright 本地运行环境") from exc
        platform = str(account["platform"])
        payload = self.db.get_procurement_session(int(account["id"]))
        if not payload:
            raise ProcurementError("采购账号尚未登录")
        profile = Path(
            account.get("profile_dir")
            or self.data_dir
            / "procurement_profiles"
            / f"account_{account['id']}"
        )
        profile.mkdir(parents=True, exist_ok=True)
        captured: list[dict[str, Any]] = []
        playwright = sync_playwright().start()
        context = None
        try:
            context = playwright.chromium.launch_persistent_context(
                str(profile),
                **browser_launch_options(
                    headless=True,
                    viewport={"width": 1360, "height": 900},
                    args=["--no-first-run"],
                ),
            )
            context.add_cookies(
                [
                    {
                        "name": cookie["name"],
                        "value": cookie["value"],
                            "domain": cookie.get("domain")
                            or _default_cookie_domain(platform),
                        "path": cookie.get("path") or "/",
                    }
                    for cookie in normalize_cookies(payload.get("cookies") or [])
                ]
            )
            page = context.pages[0] if context.pages else context.new_page()

            def on_response(response) -> None:
                response_url = response.url.lower()
                if not any(
                    token in response_url
                    for token in ("order", "trade", "logistics", "parcel", "delivery")
                ):
                    return
                content_type = str(response.headers.get("content-type") or "")
                if "json" not in content_type:
                    return
                try:
                    data = response.json()
                except Exception:
                    return
                captured.append({"url": response.url, "data": data})

            page.on("response", on_response)
            page.goto(
                url or PLATFORM_ORDER_URLS[platform],
                wait_until="domcontentloaded",
                timeout=60000,
            )
            page.wait_for_timeout(2500)
            for _index in range(max(0, scroll_count)):
                page.mouse.wheel(0, 1400)
                page.wait_for_timeout(700)
        finally:
            if context is not None:
                try:
                    context.close()
                except Exception:
                    pass
            playwright.stop()
        return captured

    def browse(
        self,
        account: dict[str, Any],
        url: str | None = None,
    ) -> list[dict[str, Any]]:
        try:
            from playwright.sync_api import Error as PlaywrightError
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise ProcurementError("缺少 Playwright 本地运行环境") from exc
        platform = str(account["platform"])
        payload = self.db.get_procurement_session(int(account["id"]))
        if not payload:
            raise ProcurementError("采购账号尚未登录")
        profile = Path(
            account.get("profile_dir")
            or self.data_dir
            / "procurement_profiles"
            / f"account_{account['id']}"
        )
        profile.mkdir(parents=True, exist_ok=True)
        captured: list[dict[str, Any]] = []
        cookies = normalize_cookies(payload.get("cookies") or [])
        playwright = sync_playwright().start()
        context = None
        try:
            context = playwright.chromium.launch_persistent_context(
                str(profile),
                **browser_launch_options(
                    headless=False,
                    no_viewport=True,
                    args=["--start-maximized", "--no-first-run"],
                ),
            )
            context.add_cookies(
                [
                    {
                        "name": cookie["name"],
                        "value": cookie["value"],
                            "domain": cookie.get("domain")
                            or _default_cookie_domain(platform),
                        "path": cookie.get("path") or "/",
                    }
                    for cookie in cookies
                ]
            )
            page = context.pages[0] if context.pages else context.new_page()

            def on_response(response) -> None:
                response_url = response.url.lower()
                if not any(
                    token in response_url
                    for token in ("order", "trade", "logistics", "parcel", "delivery")
                ):
                    return
                if "json" not in str(response.headers.get("content-type") or ""):
                    return
                try:
                    captured.append(
                        {"url": response.url, "data": response.json()}
                    )
                except Exception:
                    return

            page.on("response", on_response)
            page.goto(
                url or PLATFORM_ORDER_URLS[platform],
                wait_until="domcontentloaded",
                timeout=60000,
            )
            while context.pages:
                time.sleep(1)
                try:
                    latest = normalize_cookies(context.cookies())
                    if latest:
                        payload["cookies"] = latest
                except PlaywrightError:
                    break
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
        return captured


class MtopClient:
    def __init__(self, platform: str, cookies: list[dict[str, Any]]):
        self.platform = platform
        self.host = MTOP_HOSTS[platform]
        self.cookie_header = cookie_header(cookies)
        self.token = cookie_value(cookies, "_m_h5_tk").split("_", 1)[0]
        if not self.token:
            raise ProcurementError("登录 Cookie 中没有 MTOP 令牌")

    def call(self, api: str, data: dict[str, Any]) -> dict[str, Any]:
        timestamp = str(int(time.time() * 1000))
        data_text = json.dumps(
            data,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        app_key = "12574478"
        sign_text = f"{self.token}&{timestamp}&{app_key}&{data_text}"
        sign = hashlib.md5(sign_text.encode("utf-8")).hexdigest()
        params = {
            "jsv": "2.7.2",
            "appKey": app_key,
            "t": timestamp,
            "sign": sign,
            "api": api,
            "v": "1.0",
            "type": "jsonp",
            "dataType": "jsonp",
            "data": data_text,
        }
        try:
            response = requests.get(
                f"{self.host}/h5/{api}/1.0/",
                params=params,
                headers={
                    "Cookie": self.cookie_header,
                    "Referer": (
                        "https://trade.1688.com/"
                        if self.platform == "1688"
                        else "https://buyertrade.taobao.com/"
                    ),
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 Chrome/132.0.0.0 Safari/537.36"
                    ),
                },
                timeout=(5, 25),
                proxies={"http": None, "https": None},
            )
        except requests.RequestException as exc:
            raise ProcurementError(f"MTOP网络异常：{exc}") from exc
        text = response.text.strip()
        match = re.search(r"\((\{.*\})\)\s*;?\s*$", text, re.S)
        if match:
            text = match.group(1)
        try:
            result = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ProcurementError("MTOP返回内容无法解析") from exc
        if "TOKEN_EMPTY" in text or "TOKEN_EXPIRED" in text:
            raise ProcurementError("MTOP令牌已过期，请重新登录采购账号")
        return result


class ProcurementAdapter:
    def __init__(self, db: Database, data_dir: Path):
        self.db = db
        self.data_dir = data_dir
        self.web = ProcurementWebSession(db, data_dir)

    def sync(
        self,
        account: dict[str, Any],
        progress: Callable[[str, int, int], None] | None = None,
    ) -> int:
        platform = str(account["platform"])
        payloads: list[Any] = []
        session = self.db.get_procurement_session(int(account["id"]))
        if not session:
            raise ProcurementError("采购账号尚未登录")
        cookies = normalize_cookies(session.get("cookies") or [])
        if progress:
            progress("正在读取采购订单", 0, 2)
        try:
            client = MtopClient(platform, cookies)
            for api in MTOP_APIS[platform]:
                data = (
                    {"page": 1, "pageSize": 30}
                    if "Order" in api or "trade" in api
                    else {"pageNo": 1, "pageSize": 30}
                )
                try:
                    payloads.append(client.call(api, data))
                except ProcurementError:
                    continue
        except ProcurementError:
            pass
        if progress:
            progress("正在读取采购页面", 1, 2)
        try:
            captured = self.web.capture_page_responses(account)
            payloads.extend(item.get("data") for item in captured)
        except ProcurementError:
            if not payloads:
                raise
        records = normalize_procurement_records(platform, payloads)
        for record in records:
            self.db.upsert_procurement_order(
                int(account["id"]),
                platform,
                record,
            )
        self.db.update_procurement_account(
            int(account["id"]),
            status="已登录",
            last_sync=time.strftime("%Y-%m-%d %H:%M:%S"),
            last_error="",
        )
        if progress:
            progress("采购订单同步完成", 2, 2)
        return len(records)

    def match_orders(self, store_id: int) -> int:
        unmatched = self.db.list_procurement_orders()
        matched = 0
        for procurement in unmatched:
            mail_no = str(procurement.get("mail_no") or "")
            trade_no = str(procurement.get("trade_no") or "")
            candidates = []
            if mail_no:
                candidates = self.db.list_orders(store_id=store_id, search=mail_no)
            if not candidates and trade_no:
                candidates = self.db.list_orders(store_id=store_id, search=trade_no)
            exact = [
                order
                for order in candidates
                if mail_no and mail_no == str(order.get("waybill_id") or "")
            ]
            if len(exact) == 1:
                self.db.upsert_procurement_link(
                    store_id,
                    str(exact[0]["order_id"]),
                    int(procurement["id"]),
                    "mail_no",
                    100,
                )
                matched += 1
        return matched
