from __future__ import annotations

import time
from datetime import date, datetime, timedelta
from datetime import time as datetime_time
from typing import Any, Callable

import requests

from .db import Database
from .models import Product, Store
from .web_session import cookie_header, normalize_cookies

BASE_URL = "https://store.weixin.qq.com"
SCAN_PATH = (
    "/shop-faas/mmchannelstradeproductcore/cgi/goods/scanProductPreview"
)
METRIC_PATH = (
    "/shop-faas/mmecnodeproductlist/cgi/goods/getProductMetricList"
)
ACTIVITY_PRODUCTS_PATH = (
    "/shop-faas/mmeckolmarketingnode/activity/cgi/"
    "getPlatformInvestmentProductInfoList"
)
ACTIVITY_LIST_PATH = (
    "/shop-faas/mmeckolmarketingnode/activity/cgi/"
    "listShopPlatformActivity"
)
ACTIVITY_APPLY_PATH = (
    "/shop-faas/mmeckolmarketingnode/activity/cgi/"
    "applyShopPlatformActivity"
)
ACCOUNT_CLOSE_STATUS_PATH = (
    "/shop-faas/mmecnodebase/close/cgi/getEcAcctCloseStatus"
)
ACTIVITY_UPDATE_PATH = (
    "/shop-faas/mmeckolmarketingnode/activity/cgi/updateRequestProductList"
)
ACTIVITY_CHECK_PRICE_PATH = (
    "/shop-faas/mmeckolmarketingnode/activity/cgi/checkModifyActivityPrice"
)
ACTIVITY_SUBMIT_PRICE_PATH = (
    "/shop-faas/mmeckolmarketingnode/activity/cgi/submitModifyActivityPrice"
)
ACTIVITY_VALIDATE_PATH = (
    "/shop-faas/mmeckolmarketingnode/activity/cgi/"
    "checkPlatformInvestmentProductValid"
)
ACTIVITY_CAN_APPLY_PATH = (
    "/shop-faas/mmeckolmarketingnode/activity/cgi/"
    "getShopActivityCanApplyProduct"
)
SNAPUP_LIST_PATH = (
    "/shop-faas/market/cgi/snapupV2/bGetSnapUpTaskListV2"
)
SNAPUP_DETAIL_PATH = (
    "/shop-faas/market/cgi/snapupV2/bGetSnapUpTaskV2"
)
SNAPUP_PRODUCTS_PATH = (
    "/shop-faas/market/cgi/snapupV2/batchGetProducts"
)
SNAPUP_CREATE_PATH = (
    "/shop-faas/market/cgi/snapupV2/createSnapUpTaskV2"
)
SNAPUP_STOP_PATH = (
    "/shop-faas/market/cgi/snapupV2/stopSnapUpTaskV2"
)
SNAPUP_DELETE_PATH = (
    "/shop-faas/market/cgi/snapupV2/deleteSnapUpTaskV2"
)
SNAPUP_MAX_PRODUCTS = 200
PRODUCT_RECYCLE_PATH = (
    "/shop-faas/mmecnodeproductlist/cgi/goods/batchDel"
)
PRODUCT_REAL_DELETE_PATH = (
    "/shop-faas/mmecnodeproductlist/cgi/goods/batchRealDel"
)
PRODUCT_UNLIST_PATH = (
    "/shop-faas/mmecnodeproductlist/cgi/goods/BatchUnListing"
)
VIOLATION_LIST_PATH = (
    "/shop-faas/mmecnodeviolationsec/violation/cgi/get"
)
VIOLATION_PREWARN_PATH = (
    "/shop-faas/mmecnodeviolationsec/prewarn/cgi/getList"
)
REALTIME_STATS_PATH = (
    "/shop-faas/mmecnodelinercgi/cgi/liner_query/3/"
    "home-overview-shop-realtime?token=&lang=zh_CN"
)
PERIOD_STATS_PATH = (
    "/shop-faas/mmecnodelinercgi/cgi/liner_query/3/"
    "home-overview-shop-offline?token=&lang=zh_CN"
)
TODO_STATS_PATH = (
    "/shop-faas/mmchannelstradehome/home/todo?token=&lang=zh_CN"
)
SCORE_STATS_PATH = (
    "/shop-faas/statistic/cgi/search?token=&lang=zh_CN"
)
RECOMMEND_STATS_PATH = (
    "/shop-faas/mmchannelstradebase/cgi/shop/"
    "getShopRecommendProductsStat"
)
SEARCH_STATS_PATH = (
    "/shop-faas/mmchannelstradebase/cgi/shop/"
    "getShopSearchProductsStat"
)
PRODUCT_OVERVIEW_PATH = (
    "/shop-faas/mmecnodelinercgi/cgi/liner/query"
)
PRODUCT_COMPASS_LOGIN_PATH = (
    "/shop-faas/mmecnodecompasscommon/thirdParty/shop/"
    "loginCompassByShop"
)
ACCOUNT_BALANCE_PATH = (
    "/shop-faas/mmchannelstradefunds/cgi/generalAccount/"
    "getGeneralAccountBalance?token=&lang=zh_CN"
)
DEPOSIT_STATE_PATH = (
    "/shop-faas/mmchannelstradedeposit/home/cgi/"
    "getDepositMPayState?token=&lang=zh_CN"
)
FULL_CATEGORY_TREE_PATH = (
    "/shop-faas/mmchannelstradecategory/cgi/category/getFullCategoryTree"
)
CATEGORY_RELATION_PATH = (
    "/shop-faas/mmchannelstradecategory/cgi/category/"
    "getCategoryRelationList"
)
CATEGORY_DEPOSIT_PATH = (
    "/shop-faas/mmchannelstradedeposit/home/cgi/getCategoryDeposit"
)
CATEGORY_REQUIREMENT_PATH = (
    "/shop-faas/mmchannelstradecategory/cgi/category/"
    "checkNeedQualificationByCategoryIds"
)
CATEGORY_ADD_PATH = (
    "/shop-faas/mmchannelstradecategory/cgi/category/"
    "batchAddCategory"
)
CATEGORY_CHECK_BATCH_SIZE = 200
ORDER_DETAIL_PATH = (
    "/shop-faas/mmchannelstradeorder/detail/cgi/orderDetail"
)
ORDER_BATCH_NOTES_PATH = (
    "/shop-faas/mmchannelstradeorder/common/cgi/batchMerchantNotes"
)

TAB_STATUSES: dict[str, tuple[int, ...]] = {
    "sales": (5,),
    "audit": (2,),
    "failed": (3,),
    "draft": (0,),
    "offline": (11, 12, 13, 20),
    "recycle": (6,),
    "strategy": (7,),
}

PRODUCT_STATUS_NAMES = {
    0: "草稿箱",
    1: "草稿箱",
    2: "审核中",
    3: "审核失败",
    4: "审核成功",
    5: "销售中",
    6: "回收站",
    7: "上架策略生效中",
    10: "待删除",
    11: "已下架",
    12: "售罄下架",
    13: "平台下架",
    20: "封禁下架",
}

AUDIT_STATUS_NAMES = {
    1: "编辑中",
    2: "审核中",
    3: "审核失败",
    4: "审核成功",
}


class ShopPrivateError(RuntimeError):
    pass


class ShopPrivateWriteUncertainError(ShopPrivateError):
    """A write request was sent but its business result is unknown."""

    verification_pending = True


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _first(item: dict[str, Any], *keys: str, default: Any = "") -> Any:
    for key in keys:
        value = item.get(key)
        if value is not None and value != "":
            return value
    return default


_PRODUCT_IMAGE_KEYS = (
    "productImg",
    "productImage",
    "product_img",
    "product_img_url",
    "productImageUrl",
    "headImg",
    "head_img",
    "headImgUrl",
    "head_img_url",
    "imgUrl",
    "imageUrl",
    "coverUrl",
    "picUrl",
    "thumbnail",
    "image",
    "img",
)


def _image_url(value: Any) -> str:
    if isinstance(value, dict):
        value = _first(value, *_PRODUCT_IMAGE_KEYS)
    if isinstance(value, list):
        for item in value:
            candidate = _image_url(item)
            if candidate:
                return candidate
        return ""
    text = str(value or "").strip()
    if text.startswith("//"):
        return f"https:{text}"
    if text.startswith(("http://", "https://")):
        return text
    return ""


def _violation_head_image(*containers: Any) -> str:
    nested_keys = (
        "product",
        "productInfo",
        "product_info",
        "spu",
        "spuInfo",
        "goods",
        "goodsInfo",
        "warnObjectInfo",
        "objectInfo",
        "punishObject",
        "objects",
    )
    for container in containers:
        if not isinstance(container, dict):
            continue
        direct = _image_url(_first(container, *_PRODUCT_IMAGE_KEYS))
        if direct:
            return direct
        for key in nested_keys:
            nested = container.get(key)
            candidate = _image_url(nested)
            if candidate:
                return candidate
            if isinstance(nested, list):
                for item in nested:
                    candidate = _image_url(item)
                    if candidate:
                        return candidate
    return ""


def normalize_order_private_detail(result: dict[str, Any]) -> dict[str, Any]:
    """Normalize the official merchant-backend order detail response."""
    code = _as_int(result.get("code"), 0)
    if code != 0:
        raise ShopPrivateError(
            str(
                result.get("errmsg")
                or result.get("title")
                or f"订单详情接口返回 code={code}"
            )
        )
    common = result.get("commonInfo")
    common = common if isinstance(common, dict) else {}
    order_info = result.get("orderInfo")
    order_info = order_info if isinstance(order_info, dict) else {}
    accept_info = result.get("acceptInfo")
    accept_info = accept_info if isinstance(accept_info, dict) else {}
    flag_info = result.get("flagInfo")
    flag_info = flag_info if isinstance(flag_info, dict) else {}
    express_info = result.get("expressInfo")
    express_info = express_info if isinstance(express_info, dict) else {}
    express_data = result.get("expressData")
    express_data = express_data if isinstance(express_data, dict) else {}

    merchant_notes = str(
        flag_info.get("flagNotes")
        or order_info.get("merchantNotes")
        or accept_info.get("merchantNotes")
        or ""
    )
    flag_color = _as_int(flag_info.get("flagColor"), 0)

    package_rows: list[dict[str, Any]] = []
    package_meta = {
        str(item.get("waybillId") or ""): item
        for item in express_info.get("deliveryProductInfo") or []
        if isinstance(item, dict)
        and str(item.get("waybillId") or "").strip()
    }
    delivery_rows = express_data.get("deliveryList")
    if not isinstance(delivery_rows, list):
        delivery_rows = []
    for row in delivery_rows:
        if not isinstance(row, dict):
            continue
        traces = []
        for trace in row.get("traces") or []:
            if not isinstance(trace, dict):
                continue
            traces.append(
                {
                    "time": str(trace.get("acceptTime") or ""),
                    "description": str(
                        trace.get("acceptAddress")
                        or trace.get("content")
                        or ""
                    ),
                }
            )
        waybill_id = str(row.get("waybillId") or "")
        meta = package_meta.get(waybill_id, {})
        product_infos = meta.get("productInfos")
        if not isinstance(product_infos, list):
            product_infos = row.get("productInfos")
        package_rows.append(
            {
                "waybill_id": waybill_id,
                "delivery_id": str(
                    row.get("deliveryId")
                    or meta.get("deliveryId")
                    or ""
                ),
                "delivery_name": str(
                    row.get("deliveryName")
                    or row.get("deliveryId")
                    or ""
                ),
                "status": str(row.get("title") or ""),
                "content": str(row.get("content") or ""),
                "error": str(row.get("errMsg") or ""),
                "traces": traces,
                "product_infos": [
                    {
                        "product_id": str(product.get("productId") or ""),
                        "sku_id": str(product.get("skuId") or ""),
                        "quantity": _as_int(
                            product.get("productCnt"),
                            0,
                        ),
                    }
                    for product in (
                        product_infos
                        if isinstance(product_infos, list)
                        else []
                    )
                    if isinstance(product, dict)
                ],
            }
        )

    if not package_rows:
        for row in express_info.get("deliveryProductInfo") or []:
            if not isinstance(row, dict):
                continue
            package_rows.append(
                {
                    "waybill_id": str(row.get("waybillId") or ""),
                    "delivery_id": str(row.get("deliveryId") or ""),
                    "delivery_name": str(
                        row.get("deliveryId") or ""
                    ),
                    "status": str(common.get("statusStr") or ""),
                    "content": "",
                    "error": "",
                    "traces": [],
                    "product_infos": [
                        {
                            "product_id": str(
                                product.get("productId") or ""
                            ),
                            "sku_id": str(product.get("skuId") or ""),
                            "quantity": _as_int(
                                product.get("productCnt"),
                                0,
                            ),
                        }
                        for product in (
                            row.get("productInfos")
                            if isinstance(row.get("productInfos"), list)
                            else []
                        )
                        if isinstance(product, dict)
                    ],
                }
            )

    products = []
    product_block = result.get("orderProducts")
    product_block = (
        product_block if isinstance(product_block, dict) else {}
    )
    product_rows = product_block.get("orderProductInfo")
    if not isinstance(product_rows, list):
        product_rows = []
    for row in product_rows:
        if not isinstance(row, dict):
            continue
        products.append(
            {
                "product_id": str(row.get("productId") or ""),
                "sku_id": str(row.get("skuId") or ""),
                "title": str(row.get("title") or ""),
                "thumb_url": str(row.get("thumbImg") or ""),
                "price": _as_int(row.get("salePrice"), 0),
                "real_price": _as_int(row.get("realPrice"), 0),
                "quantity": _as_int(row.get("productCnt"), 0),
            }
        )

    return {
        "order_id": str(
            common.get("orderId") or order_info.get("orderId") or ""
        ),
        "status": _as_int(common.get("status"), 0),
        "status_text": str(common.get("statusStr") or ""),
        "update_time": _as_int(order_info.get("updateTime"), 0),
        "merchant_notes": merchant_notes,
        "merchant_notes_tag_color": flag_color,
        "packages": package_rows,
        "products": products,
        "source": "微信小店商家后台订单详情",
        "checked_at": datetime.now().isoformat(timespec="seconds"),
    }


class ShopPrivateClient:
    """Merchant-backend adapter using the user's existing web login.

    These are page-backend (shop-faas) endpoints, not public OpenAPI endpoints.
    They are isolated here so official API paths can remain the primary choice.
    """

    def __init__(
        self,
        db: Database,
        store: Store,
        use_system_proxy: bool = False,
        sleeper: Callable[[float], None] = time.sleep,
    ):
        self.store = store
        payload = db.get_web_cookies(store.id)
        cookies = normalize_cookies((payload or {}).get("cookies") or [])
        if not cookies:
            raise ShopPrivateError("店铺未登录网页后台")
        cookie_map = {
            str(cookie["name"]): str(cookie["value"])
            for cookie in cookies
        }
        self.session = requests.Session()
        self.session.trust_env = use_system_proxy
        self._headers = {
            "Cookie": cookie_header(cookies),
            "accept": "application/json, text/plain, */*",
            "accept-language": "zh-CN,zh;q=0.9",
            "biz_magic": cookie_map.get("biz_magic", ""),
            "potter-scene": "weixinShop",
            "origin": BASE_URL,
            "referer": f"{BASE_URL}/shop/home",
            "user-agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/132.0.0.0 Safari/537.36"
            ),
            "content-type": "application/json",
        }
        self._sleep = sleeper
        self._last_request = 0.0
        self._min_interval = 0.18
        self._product_compass_headers: dict[str, str] | None = None

    def _post(
        self,
        path: str,
        body: dict[str, Any],
        timeout: int = 25,
        headers: dict[str, str] | None = None,
        *,
        write_attempt: bool = False,
    ) -> dict[str, Any]:
        delay = self._min_interval - (time.monotonic() - self._last_request)
        if delay > 0:
            self._sleep(delay)
        try:
            response = self.session.post(
                BASE_URL + path,
                params={"token": "", "lang": "zh_CN"},
                headers={**self._headers, **(headers or {})},
                json=body,
                timeout=timeout,
            )
        except requests.RequestException as exc:
            message = f"商家后台网络异常：{exc}"
            if write_attempt:
                raise ShopPrivateWriteUncertainError(
                    "写入请求已发出但连接结果未知，待核实：" + message
                ) from exc
            raise ShopPrivateError(message) from exc
        finally:
            self._last_request = time.monotonic()
        if response.status_code in {401, 403, 429}:
            raise ShopPrivateError(
                f"商家后台拒绝请求（HTTP {response.status_code}），请重新扫码"
            )
        if response.status_code >= 400:
            raise ShopPrivateError(
                f"商家后台请求失败（HTTP {response.status_code}）"
            )
        lowered = response.text[:100000].lower()
        if "扫码进入我的小店" in response.text or "/login" in lowered:
            raise ShopPrivateError("网页登录已失效，请重新扫码")
        try:
            result = response.json()
        except ValueError as exc:
            if write_attempt:
                raise ShopPrivateWriteUncertainError(
                    "写入请求已返回无法解析的响应，待核实"
                ) from exc
            raise ShopPrivateError("商家后台返回了非 JSON 页面") from exc
        if not isinstance(result, dict):
            if write_attempt:
                raise ShopPrivateWriteUncertainError(
                    "写入请求响应格式异常，结果待核实"
                )
            raise ShopPrivateError("商家后台返回格式异常")
        code = result.get("code")
        if code not in (None, 0, "0") or result.get("errcode") not in (None, 0, "0"):
            message = (
                result.get("errmsg")
                or result.get("errMsg")
                or result.get("message")
                or result.get("error")
                or f"商家后台接口返回 code={code}"
            )
            raise ShopPrivateError(str(message))
        data = result.get("data")
        check = data if isinstance(data, dict) else result
        if (
            check.get("success") is False
            or _as_int(check.get("ret"), 0) != 0
            or _as_int(check.get("errcode"), 0) != 0
        ):
            message = (
                check.get("errMsg")
                or check.get("errmsg")
                or check.get("message")
                or "商家后台接口返回失败"
            )
            raise ShopPrivateError(str(message))
        return result

    def _product_compass_auth_headers(self) -> dict[str, str]:
        if self._product_compass_headers is not None:
            return self._product_compass_headers
        response = self.session.get(
            BASE_URL + PRODUCT_COMPASS_LOGIN_PATH,
            headers={
                **self._headers,
                "accept": "text/html,application/xhtml+xml",
                "referer": f"{BASE_URL}/shop/statistics/product",
            },
            timeout=25,
            allow_redirects=True,
        )
        if response.status_code in {401, 403, 429}:
            raise ShopPrivateError(
                f"商品数据登录已失效（HTTP {response.status_code}），请重新扫码"
            )
        if response.status_code >= 400:
            raise ShopPrivateError(
                f"商品数据登录失败（HTTP {response.status_code}）"
            )
        session_cookies = normalize_cookies(
            [
                {
                    "name": cookie.name,
                    "value": cookie.value,
                    "domain": cookie.domain,
                    "path": cookie.path,
                    "secure": cookie.secure,
                }
                for cookie in self.session.cookies
            ]
        )
        cookie_names = {cookie["name"] for cookie in session_cookies}
        if "user_magic" not in cookie_names:
            raise ShopPrivateError(
                "商品数据登录失败：商家后台未返回 user_magic"
            )
        self._product_compass_headers = {
            "Cookie": cookie_header(session_cookies),
            "x-use-new-account": "1",
            "x-use-wx-ticket": "1",
            "referer": f"{BASE_URL}/compass/embed/channels-shop-product?source=4",
        }
        return self._product_compass_headers

    def _get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        timeout: int = 25,
    ) -> dict[str, Any]:
        request_params = {"token": "", "lang": "zh_CN"}
        if params:
            request_params.update(params)
        try:
            response = self.session.get(
                BASE_URL + path,
                params=request_params,
                headers=self._headers,
                timeout=timeout,
            )
        except requests.RequestException as exc:
            raise ShopPrivateError(f"商家后台网络异常：{exc}") from exc
        if response.status_code in {401, 403, 429}:
            raise ShopPrivateError(
                f"商家后台拒绝请求（HTTP {response.status_code}），请重新扫码"
            )
        if response.status_code >= 400:
            raise ShopPrivateError(
                f"商家后台请求失败（HTTP {response.status_code}）"
            )
        try:
            result = response.json()
        except ValueError as exc:
            raise ShopPrivateError("商家后台返回了非 JSON 页面") from exc
        return result

    @staticmethod
    def _data(result: dict[str, Any]) -> dict[str, Any]:
        data = result.get("data")
        return data if isinstance(data, dict) else result

    def scan_page(
        self,
        tab_key: str,
        page: int = 1,
        page_size: int = 50,
    ) -> dict[str, Any]:
        if tab_key not in TAB_STATUSES:
            raise ValueError(f"未知商品页签：{tab_key}")
        body = {
            "pageSize": max(1, int(page_size)),
            "productStatus": [-1],
            "productSource": "[1,16,32]",
            "searchSource": 1,
            "useNew": True,
            "pageNum": max(1, int(page)),
            "status": list(TAB_STATUSES[tab_key]),
        }
        return self._data(self._post(SCAN_PATH, body))

    def status_counts(self) -> dict[str, int]:
        counts = {}
        for key in TAB_STATUSES:
            data = self.scan_page(key, page=1, page_size=1)
            counts[key] = _as_int(data.get("totalNum"))
        return counts

    def online_product_count(self) -> dict[str, Any]:
        """Return the current official in-sale count without using caches."""
        data = self.scan_page("sales", page=1, page_size=1)
        count = _optional_int(data.get("totalNum"))
        if count is None or count < 0:
            raise ShopPrivateError("商品接口未返回有效的出售中商品总数")
        return {
            "count": count,
            "checked_at": datetime.now().isoformat(timespec="seconds"),
            "source": (
                "微信小店商家后台商品列表 status=5 "
                "（productStatus sales / totalNum）"
            ),
        }

    def audit_success_today(self, max_pages: int = 5) -> int:
        start = int(
            datetime.combine(date.today(), datetime_time.min).timestamp()
        )
        count = 0
        newest = 0.0
        for page in range(1, max(1, int(max_pages)) + 1):
            data = self.scan_page("sales", page=page, page_size=50)
            rows = data.get("productList") or []
            if isinstance(rows, dict):
                rows = rows.get("list") or rows.get("items") or []
            if not rows:
                break
            page_count = 0
            for row in rows:
                if not isinstance(row, dict):
                    continue
                audit_info = row.get("auditResultInfo")
                audit_info = (
                    audit_info if isinstance(audit_info, dict) else {}
                )
                timestamp = _as_int(
                    _first(
                        audit_info,
                        "auditTimestamp",
                        "auditPassTime",
                        "auditTime",
                        default=_first(
                            row,
                            "auditTimestamp",
                            "auditPassTime",
                            "auditTime",
                            default=0,
                        ),
                    )
                )
                if timestamp >= start:
                    page_count += 1
                newest = max(newest, float(timestamp))
            count += page_count
            if len(rows) < 50:
                break
            if page > 1 and page_count == 0 and newest < start:
                break
        return count

    def scan_products(
        self,
        tab_key: str,
        max_pages: int | None = 10,
        page_size: int = 50,
        stop: Callable[[], bool] | None = None,
        on_progress: Callable[[str, int, int], None] | None = None,
    ) -> list[Product]:
        first = self.scan_page(tab_key, page=1, page_size=page_size)
        if max_pages is None and ("totalNum" not in first or "productList" not in first):
            raise ShopPrivateError("商品接口缺少总数或列表，无法确认最新商品")
        total = _as_int(first.get("totalNum"), -1)
        if total < 0:
            raise ShopPrivateError("商品接口总数格式异常")
        total_pages = max(1, (total + page_size - 1) // page_size)
        if max_pages is not None:
            total_pages = min(max(1, max_pages), total_pages)
        products: list[Product] = []
        for page in range(1, total_pages + 1):
            if stop and stop():
                break
            data = (
                first
                if page == 1
                else self.scan_page(tab_key, page=page, page_size=page_size)
            )
            rows = data.get("productList")
            if rows is None and max_pages is not None:
                rows = []
            if isinstance(rows, dict):
                rows = rows.get("list") or rows.get("items") or []
            if not isinstance(rows, list):
                raise ShopPrivateError("商品接口列表格式异常")
            if max_pages is None and _as_int(data.get("totalNum"), -1) != total:
                raise ShopPrivateError("查询期间商品数量发生变化，请重新查询")
            for item in rows:
                if isinstance(item, dict):
                    products.append(self._parse_product(item))
            if on_progress:
                on_progress(tab_key, page, total_pages)
            if len(rows) < page_size:
                break
        if max_pages is None and not (stop and stop()):
            if len({product.product_id for product in products if product.product_id}) != total:
                raise ShopPrivateError("商品分页结果不完整，请重新查询")
        return products

    @staticmethod
    def _parse_product(item: dict[str, Any]) -> Product:
        product_id = str(
            _first(
                item,
                "spuId",
                "spu_id",
                "productId",
                "product_id",
                default="",
            )
        )
        if not product_id and isinstance(item.get("productKey"), dict):
            product_id = str(item["productKey"].get("productId") or "")
        title = str(
            _first(item, "title", "productTitle", "name", "productName")
        ).strip()
        status = _as_int(item.get("status"), 0)
        edit_status = _as_int(item.get("editStatus"), 0)
        price = _first(item, "price", "lowSalePrice", default=0)
        stock = _first(
            item,
            "totalStockNum",
            "stockNum",
            "spuTotalStockNum",
            default=0,
        )
        if not stock and isinstance(item.get("spuTotalStock"), dict):
            stock = item["spuTotalStock"].get("stockNum") or 0
        sold = _first(item, "soldNum", "totalSoldNum", default=0)
        listing_time = _as_int(item.get("listingTime"), 0)
        audit_info = item.get("auditResultInfo")
        audit_time = (
            _as_int(audit_info.get("auditTimestamp"), 0)
            if isinstance(audit_info, dict)
            else 0
        )
        raw = {
            **item,
            "_private_source": "shop-faas",
            "_private_audit": AUDIT_STATUS_NAMES.get(edit_status, str(edit_status)),
            "_private_listing_time": listing_time,
            "_private_audit_time": audit_time,
            "total_sold_num": _as_int(sold),
        }
        return Product(
            store_id=0,
            store_name="",
            product_id=product_id,
            title=title,
            status=status,
            status_text=PRODUCT_STATUS_NAMES.get(status, str(status)),
            edit_status=edit_status,
            min_price=_as_int(price),
            sku_count=len(item.get("skuList") or []),
            stock_total=_as_int(stock),
            spu_code=str(item.get("spuCode") or item.get("spu_code") or ""),
            out_product_id=str(
                item.get("outProductId") or item.get("out_product_id") or ""
            ),
            head_img=str(item.get("headImg") or item.get("head_img") or ""),
            raw=raw,
        )

    def product_metrics(
        self,
        product_ids: list[str],
        page_size: int = 200,
        stop: Callable[[], bool] | None = None,
    ) -> dict[str, dict[str, int | None]]:
        remaining = list(dict.fromkeys(str(value) for value in product_ids if value))
        output: dict[str, dict[str, int | None]] = {}
        while remaining:
            if stop and stop():
                break
            batch = remaining[:page_size]
            remaining = remaining[page_size:]
            body = {
                "spuIdList": batch,
                "offset": 0,
                "limit": len(batch),
                "sortMetricCode": "shopxspu_growth_pay_product_cnt_30d_2",
                "sortRule": "desc",
            }
            data = self._data(self._post(METRIC_PATH, body))
            rows = data.get("rowsMap") or []
            if isinstance(rows, dict):
                rows = list(rows.values())
            for row in rows:
                if not isinstance(row, dict):
                    continue
                value = row.get("value")
                if not isinstance(value, dict):
                    continue
                if str(value.get("scene_type_", "0")) != "0":
                    continue
                product_id = str(value.get("spu_id_") or "")
                if not product_id:
                    continue
                metrics = output.setdefault(
                    product_id,
                    {
                        "views30": None,
                        "sold30": None,
                        "recommend_views30": None,
                    },
                )
                metric_fields = (
                    (
                        "views30",
                        "shopxspu_growth_product_view_cnt_30d_2",
                    ),
                    (
                        "sold30",
                        "shopxspu_growth_pay_product_cnt_30d_2",
                    ),
                    (
                        "recommend_views30",
                        "shopxspu_growth_rec_product_view_cnt_30d_2",
                    ),
                )
                for metric_key, source_key in metric_fields:
                    parsed = _optional_int(value.get(source_key))
                    if parsed is None:
                        continue
                    current = metrics.get(metric_key)
                    metrics[metric_key] = (
                        parsed
                        if current is None
                        else max(int(current), parsed)
                    )
        return output

    def activity_products(
        self,
        activity_id: int = 1323,
        page: int = 1,
        page_size: int = 100,
    ) -> dict[str, Any]:
        return self._data(
            self._post(
                ACTIVITY_PRODUCTS_PATH,
                {
                    "platformActivityId": int(activity_id),
                    "pageNum": int(page),
                    "pageSize": int(page_size),
                },
            )
        )

    def activity_submitted_products(
        self,
        activity_id: int = 1323,
        page_size: int = 100,
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        first = self.activity_products(
            activity_id,
            page=1,
            page_size=page_size,
        )
        try:
            total = int(first.get("totalNum") or 0)
        except (TypeError, ValueError):
            total = 0
        max_pages = max(1, (total + page_size - 1) // page_size)
        for page in range(1, max_pages + 1):
            data = (
                first
                if page == 1
                else self.activity_products(
                    activity_id,
                    page=page,
                    page_size=page_size,
                )
            )
            page_rows = data.get("productList") or []
            if not isinstance(page_rows, list) or not page_rows:
                break
            added = 0
            for row in page_rows:
                if not isinstance(row, dict):
                    continue
                key = (
                    str(row.get("productId") or ""),
                    str(row.get("skuId") or ""),
                )
                if not all(key) or key in seen:
                    continue
                seen.add(key)
                rows.append(row)
                added += 1
            if added == 0 or len(page_rows) < page_size:
                break
        return rows

    def platform_activities(self) -> list[dict[str, Any]]:
        activities: list[dict[str, Any]] = []
        seen: set[str] = set()
        for page in range(1, 21):
            result = self._get(
                ACTIVITY_LIST_PATH,
                {"pageNum": page, "pageSize": 50},
            )
            rows = result.get("shopPlatformActivityList") or []
            if not isinstance(rows, list) or not rows:
                break
            added = 0
            for row in rows:
                if not isinstance(row, dict):
                    continue
                activity_id = str(
                    row.get("activityId")
                    or row.get("platformActivityId")
                    or ""
                )
                if not activity_id or activity_id in seen:
                    continue
                seen.add(activity_id)
                item = dict(row)
                item["activity_id"] = activity_id
                activities.append(item)
                added += 1
            if added == 0:
                break
        return activities

    def snapup_activities(
        self,
        page_size: int = 100,
    ) -> list[dict[str, Any]]:
        activities: list[dict[str, Any]] = []
        seen: set[str] = set()
        page = 1
        while page <= 100:
            result = self._get(
                SNAPUP_LIST_PATH,
                {"pageNum": page, "pageSize": page_size},
            )
            rows = result.get("snapupTasks") or []
            if not isinstance(rows, list) or not rows:
                break
            added = 0
            for row in rows:
                if not isinstance(row, dict):
                    continue
                activity_id = str(row.get("activityId") or "")
                if not activity_id or activity_id in seen:
                    continue
                seen.add(activity_id)
                extra = row.get("extra") or {}
                products = (
                    extra.get("snapupProducts") or []
                    if isinstance(extra, dict)
                    else []
                )
                item = dict(row)
                item["activity_id"] = activity_id
                item["product_count"] = (
                    len(products) if isinstance(products, list) else 0
                )
                activities.append(item)
                added += 1
            try:
                total = int(result.get("totalNum") or 0)
            except (TypeError, ValueError):
                total = 0
            if added == 0 or (total > 0 and page * page_size >= total):
                break
            page += 1
        return activities

    def snapup_activity(self, activity_id: str) -> dict[str, Any]:
        result = self._get(
            SNAPUP_DETAIL_PATH,
            {"activityId": str(activity_id)},
        )
        task = result.get("task")
        return dict(task) if isinstance(task, dict) else {}

    def fetch_snapup_products(
        self,
        product_ids: list[str],
        batch_size: int = 20,
    ) -> list[dict[str, Any]]:
        normalized = list(
            dict.fromkeys(str(product_id) for product_id in product_ids if product_id)
        )
        batch_size = max(1, min(50, int(batch_size)))
        products: list[dict[str, Any]] = []
        for index in range(0, len(normalized), batch_size):
            result = self._get(
                SNAPUP_PRODUCTS_PATH,
                {
                    "productIds": normalized[index : index + batch_size],
                    "needSku": True,
                    "needStock": True,
                },
            )
            rows = result.get("products") or []
            if isinstance(rows, list):
                products.extend(
                    dict(row) for row in rows if isinstance(row, dict)
                )
        return products

    def create_snapup_activity(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._post(
            SNAPUP_CREATE_PATH,
            {**payload, "uploadSource": 6},
        )

    def stop_snapup_activity(self, activity_id: str) -> dict[str, Any]:
        return self._post(
            SNAPUP_STOP_PATH,
            {"activityId": str(activity_id), "uploadSource": 6},
        )

    def delete_snapup_activity(self, activity_id: str) -> dict[str, Any]:
        return self._post(
            SNAPUP_DELETE_PATH,
            {"activityId": str(activity_id)},
        )

    def apply_platform_activity(
        self,
        activity_id: int,
    ) -> dict[str, Any]:
        return self._post(
            ACTIVITY_APPLY_PATH,
            {"platformActivityId": int(activity_id)},
        )

    def account_close_status(self) -> int | None:
        try:
            result = self._get(ACCOUNT_CLOSE_STATUS_PATH)
        except ShopPrivateError:
            return None
        value = result.get("ecAcctStatus")
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def activity_update_products(
        self,
        activity_id: int,
        items: list[dict[str, Any]],
        action_type: int,
        del_source: int = 0,
        add_source: int = 0,
    ) -> dict[str, Any]:
        return self._data(
            self._post(
                ACTIVITY_UPDATE_PATH,
                {
                    "platformActivityId": int(activity_id),
                    "productList": items,
                    "actionType": int(action_type),
                    "delSource": int(del_source),
                    "addSource": int(add_source),
                },
            )
        )

    def activity_check_prices(
        self,
        activity_id: int,
        items: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return self._data(
            self._post(
                ACTIVITY_CHECK_PRICE_PATH,
                {
                    "platformActivityId": int(activity_id),
                    "productList": items,
                },
            )
        )

    def activity_validate_products(
        self,
        activity_id: int,
        items: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return self._post(
            ACTIVITY_VALIDATE_PATH,
            {
                "skuList": items,
                "platformActivityId": str(int(activity_id)),
                "checkSource": 3,
                "applyType": 0,
            },
        )

    def activity_eligible_products(
        self,
        activity_id: int,
        page_size: int = 50,
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for page in range(1, 201):
            result = self._post(
                ACTIVITY_CAN_APPLY_PATH,
                {
                    "platformActivityId": str(int(activity_id)),
                    "page": page,
                    "pageSize": int(page_size),
                    "queryWord": "",
                },
            )
            data = result.get("data") or {}
            request_info = data.get("requestProductInfoList") or {}
            page_rows = request_info.get("resultList") or []
            if not isinstance(page_rows, list) or not page_rows:
                break
            added = 0
            for row in page_rows:
                if not isinstance(row, dict):
                    continue
                product = row.get("product") or {}
                key = (
                    str(product.get("productId") or ""),
                    str(product.get("skuId") or ""),
                )
                if not all(key) or key in seen:
                    continue
                seen.add(key)
                item = dict(row)
                item["productId"] = key[0]
                item["skuId"] = key[1]
                rows.append(item)
                added += 1
            if added == 0:
                break
            try:
                total_pages = int(data.get("totalPage") or 0)
            except (TypeError, ValueError):
                total_pages = 0
            if total_pages and page >= total_pages:
                break
        return rows

    def activity_submit_prices(
        self,
        activity_id: int,
        items: list[dict[str, Any]],
        apply_type: int = 1,
    ) -> dict[str, Any]:
        return self._data(
            self._post(
                ACTIVITY_SUBMIT_PRICE_PATH,
                {
                    "platformActivityId": int(activity_id),
                    "productList": items,
                    "applyType": int(apply_type),
                },
            )
        )

    def platform_violation_products(self) -> list[dict[str, Any]]:
        output: list[dict[str, Any]] = []
        for path, params in (
            (
                VIOLATION_LIST_PATH,
                {"page": 1, "pageSize": 100, "violationType": "finder"},
            ),
            (
                VIOLATION_PREWARN_PATH,
                {"pageNum": 1, "pageSize": 100},
            ),
        ):
            result = self._get(path, params)
            data = self._data(result)
            if not isinstance(data, dict):
                data = {}
            record = result.get("record")
            if not isinstance(record, dict):
                record = data.get("record") if isinstance(data, dict) else {}
            rows = next(
                (
                    data[key]
                    for key in (
                        "list",
                        "rows",
                        "records",
                        "pageProducts",
                    )
                    if key in data
                ),
                record.get("items")
                if isinstance(record, dict) and "items" in record
                else None,
            )
            if not isinstance(rows, list):
                raise ShopPrivateError(
                    "违规记录接口未返回有效列表"
                )
            for row in rows:
                if not isinstance(row, dict):
                    continue
                product_id = str(
                    _first(
                        row,
                        "productId",
                        "spuId",
                        "spu_id",
                        "product_id",
                    )
                )
                title = str(
                    _first(row, "title", "productTitle", "productName", "name")
                    or _first(
                        row.get("punishObject")
                        if isinstance(row.get("punishObject"), list)
                        and row.get("punishObject")
                        and isinstance(row.get("punishObject")[0], dict)
                        else {},
                        "productDesc",
                        "name",
                        "title",
                    )
                )
                reason = str(
                    _first(
                        row,
                        "punishReason",
                        "reason",
                        "preWarnExplain",
                        "rectificationMethod",
                        "suggest",
                    )
                )
                if product_id:
                    output.append(
                        {
                            "product_id": product_id,
                            "title": title,
                            "reason": reason or "平台违规/预警",
                            "head_img": _violation_head_image(row),
                            "raw": row,
                        }
                    )
        return output

    def platform_violation_records(self) -> list[dict[str, Any]]:
        output: list[dict[str, Any]] = []
        for source_name, path, params in (
            (
                "违规记录",
                VIOLATION_LIST_PATH,
                {"page": 1, "pageSize": 100, "violationType": "finder"},
            ),
        ):
            result = self._get(path, params)
            data = self._data(result)
            if not isinstance(data, dict):
                data = {}
            record = result.get("record")
            if not isinstance(record, dict):
                record = data.get("record") if isinstance(data, dict) else {}
            rows = next(
                (
                    data[key]
                    for key in (
                        "list",
                        "rows",
                        "records",
                        "pageProducts",
                    )
                    if key in data
                ),
                record.get("items")
                if isinstance(record, dict) and "items" in record
                else None,
            )
            if not isinstance(rows, list):
                raise ShopPrivateError(
                    "违规记录接口未返回有效列表"
                )
            for row in rows:
                if not isinstance(row, dict):
                    continue
                objects = (
                    row.get("punishObject")
                    or row.get("objects")
                    or []
                )
                first_object = (
                    objects[0]
                    if isinstance(objects, list)
                    and objects
                    and isinstance(objects[0], dict)
                    else {}
                )
                product_id = str(
                    _first(
                        row,
                        "productId",
                        "spuId",
                        "spu_id",
                        "product_id",
                        default=_first(
                            first_object,
                            "productId",
                            "spuId",
                            "id",
                        ),
                    )
                )
                title = str(
                    _first(
                        row,
                        "title",
                        "productTitle",
                        "productName",
                        "preWarnTitle",
                        "name",
                        default=_first(
                            first_object,
                            "productDesc",
                            "name",
                            "title",
                            "productName",
                        ),
                    )
                )
                violation_type = str(
                    _first(
                        row,
                        "punishReason",
                        "reason",
                        "preWarnTypeName",
                        "warnTypeName",
                        "typeName",
                        default="平台违规/预警",
                    )
                )
                status_value = _first(
                    row,
                    "statusName",
                    "status_name",
                    "punishStatusName",
                    "preWarnStatusName",
                    default=row.get("status", ""),
                )
                status = {
                    "5": "超时未申诉",
                    "14": "已撤销",
                    "1": "待处理",
                    "2": "申诉中",
                    "3": "申诉成功",
                    "4": "申诉驳回",
                }.get(str(status_value), str(status_value))
                fix = str(
                    _first(
                        row,
                        "rectificationMethod",
                        "recommendation",
                        "preWarnExplain",
                        "suggest",
                        "punishMethod",
                        default="",
                    )
                )
                output.append(
                    {
                        "record_id": str(
                            _first(
                                row,
                                "punishUuid",
                                "preWarnId",
                                "id",
                                default=f"{source_name}-{len(output)}",
                            )
                        ),
                        "product_id": product_id,
                        "title": title or violation_type,
                        "reason": violation_type,
                        "source": source_name,
                        "status": status,
                        "fix": fix,
                        "time": str(
                            _first(
                                row,
                                "punishTime",
                                "preWarnTime",
                                "createTime",
                                "create_time",
                                default="",
                            )
                        ),
                        "head_img": _violation_head_image(row, first_object),
                        "raw": row,
                    }
                )
        output.extend(self.prewarning_records())
        return output

    def prewarning_records(self) -> list[dict[str, Any]]:
        try:
            type_result = self._get(
                "/shop-faas/mmecnodeviolationsec/prewarn/cgi/"
                "getPreWarnTypeList"
            )
        except ShopPrivateError:
            type_result = {}
        type_map = {
            str(key): str(value)
            for key, value in type_result.items()
            if key not in {"code", "msg"}
        }
        try:
            result = self._post(
                VIOLATION_PREWARN_PATH,
                {
                    "offset": 0,
                    "rows": 200,
                    "count": 200,
                    "idx": 0,
                },
            )
        except ShopPrivateError:
            return []
        objects = result.get("preWarnObjects") or []
        if not isinstance(objects, list):
            return []
        status_names = {
            1: "待处理",
            2: "已违规",
            3: "预警解除",
            4: "预警结束",
            5: "审核中",
        }
        output = []
        for row in objects:
            if not isinstance(row, dict):
                continue
            warn_type = str(row.get("warnType") or "")
            tag = type_map.get(warn_type, "店铺预警")
            object_info = row.get("warnObjectInfo")
            object_info = (
                object_info if isinstance(object_info, dict) else {}
            )
            object_items = object_info.get("objectItems")
            if not isinstance(object_items, list) or not object_items:
                object_items = [{}]
            for item in object_items:
                if not isinstance(item, dict):
                    continue
                status_code = _as_int(row.get("preWarnStatus"))
                fix = str(
                    row.get("rectificationMethod")
                    or row.get("preWarnExplain")
                    or ""
                )
                output.append(
                    {
                        "record_id": str(
                            row.get("preWarnId")
                            or f"warn-{len(output)}"
                        ),
                        "product_id": str(item.get("ids") or ""),
                        "title": str(
                            item.get("name")
                            or tag
                            or "店铺预警"
                        ),
                        "reason": tag,
                        "source": "预警",
                        "status": status_names.get(
                            status_code, str(status_code)
                        ),
                        "fix": fix,
                        "time": str(row.get("preWarnTime") or ""),
                        "head_img": _violation_head_image(
                            row,
                            object_info,
                            item,
                        ),
                        "raw": row,
                    }
                )
        return output

    def order_detail(self, order_id: str) -> dict[str, Any]:
        """Read the official merchant-backend order detail snapshot."""
        return normalize_order_private_detail(
            self._post(ORDER_DETAIL_PATH, {"id": str(order_id)})
        )

    def update_order_notes_private(
        self,
        order_id: str,
        notes: str,
        color: int,
    ) -> dict[str, Any]:
        """Write merchant notes and flag through the official web backend."""
        result = self._post(
            ORDER_BATCH_NOTES_PATH,
            {
                "items": [
                    {
                        "orderId": str(order_id),
                        "merchantNotes": str(notes),
                        "merchantNotesTagColor": int(color),
                    }
                ]
            },
        )
        code = _as_int(result.get("code"), 0)
        if code != 0:
            raise ShopPrivateError(
                str(
                    result.get("errmsg")
                    or result.get("errorMsg")
                    or f"订单备注保存接口返回 code={code}"
                )
            )
        return result

    def batch_product_action(
        self,
        product_ids: list[str],
        way: str,
    ) -> dict[str, Any]:
        paths = {
            "recycle": PRODUCT_RECYCLE_PATH,
            "real": PRODUCT_REAL_DELETE_PATH,
            "offshelf": PRODUCT_UNLIST_PATH,
        }
        if way not in paths:
            raise ValueError("不支持的商品批量处理方式")
        result = self._data(
            self._post(
                paths[way],
                {
                    "ids": [str(value) for value in product_ids],
                    "uploadSource": 6,
                },
                write_attempt=True,
            )
        )
        if result.get("success") is not True and not (
            "ret" in result and _as_int(result["ret"], -1) == 0
        ):
            raise ShopPrivateWriteUncertainError(
                "商品操作没有返回明确成功确认，写入结果待核实，"
                "请重新查询并核对微信后台"
            )
        return result

    def realtime_overview(self) -> dict[str, Any]:
        today = date.today()
        start = int(
            datetime.combine(today, datetime_time.min).timestamp() * 1000
        )
        result = self._post(
            REALTIME_STATS_PATH,
            {
                "time_query": {
                    "dateFormat": 1,
                    "dateType": 0,
                    "dateRange": 1,
                    "startMs": start,
                    "endMs": int(time.time() * 1000),
                }
            },
        )
        return dict(result.get("total") or {})

    def period_overview(
        self,
        days: int = 1,
        offset_days: int = 1,
    ) -> dict[str, Any]:
        span = max(1, int(days))
        last = date.today() - timedelta(
            days=max(1, int(offset_days))
        )
        first = last - timedelta(days=span - 1)
        start = int(
            datetime.combine(first, datetime_time.min).timestamp() * 1000
        )
        end = int(
            datetime.combine(last, datetime_time.max).timestamp() * 1000
        )
        result = self._post(
            PERIOD_STATS_PATH,
            {
                "time_query": {
                    "dateFormat": 2,
                    "dateType": 0,
                    "dateRange": span,
                    "startMs": start,
                    "endMs": end,
                }
            },
        )
        return dict(result.get("total") or {})

    def todo_overview(self) -> dict[str, int]:
        result = self._get(TODO_STATS_PATH)
        mapping = {
            "待发货": "wait_send",
            "物流异常": "express_err",
            "售后": "aftersale",
            "服务保障": "guarantee",
            "客诉工单": "complaint",
            "店铺预警": "warn",
            "店铺违规": "violation",
        }
        output = {}
        for item in result.get("list") or []:
            if not isinstance(item, dict):
                continue
            key = mapping.get(str(item.get("title") or "").strip())
            if key:
                output[key] = _as_int(item.get("num"))
        return output

    def score_overview(self) -> dict[str, Any]:
        result = self._post(
            SCORE_STATS_PATH,
            {
                "days": 14,
                "scoreTypeList": [11, 12, 13, 14, 1000],
            },
        )
        output: dict[str, Any] = {}
        daily_values: dict[str, list[dict[str, float | str]]] = {}
        for key, target in (
            ("dsrScore", "dsr"),
            ("productScore", "score_product"),
            ("deliverScore", "score_deliver"),
            ("serviceScore", "score_service"),
        ):
            block = result.get(key)
            if isinstance(block, dict):
                try:
                    output[target] = float(block.get("curValue"))
                except (TypeError, ValueError):
                    output[target] = None
                history: list[dict[str, float | str]] = []
                for row in block.get("valueList") or []:
                    if not isinstance(row, dict):
                        continue
                    raw_date = str(row.get("date") or "").strip()
                    if len(raw_date) == 8 and raw_date.isdigit():
                        raw_date = (
                            f"{raw_date[:4]}-"
                            f"{raw_date[4:6]}-"
                            f"{raw_date[6:]}"
                        )
                    try:
                        normalized_date = datetime.fromisoformat(
                            raw_date
                        ).date().isoformat()
                        value = float(row.get("value"))
                    except (TypeError, ValueError):
                        continue
                    history.append(
                        {
                            "date": normalized_date,
                            "value": value,
                        }
                    )
                if history:
                    daily_values[target] = sorted(
                        history,
                        key=lambda item: str(item["date"]),
                        reverse=True,
                    )
            else:
                output[target] = None
        output["_score_daily_values"] = daily_values
        return output

    def evaluation_overview(self) -> dict[str, float | None]:
        result = self._post(
            SCORE_STATS_PATH,
            {
                "days": 14,
                "scoreTypeList": [70, 64, 1000],
            },
        )
        good = result.get("proValidGoodEvalRatio")
        if not isinstance(good, dict) or not good:
            good = result.get("afterSaleNpsGoodRatio")
        quality = result.get("productQualityRatio_30d")

        def value(block: Any) -> float | None:
            if not isinstance(block, dict):
                return None
            try:
                return float(block.get("curValue"))
            except (TypeError, ValueError):
                return None

        return {
            "good_eval_ratio": value(good),
            "quality_refund_ratio": value(quality),
        }

    def account_balance(self) -> dict[str, int | None]:
        result = self._get(ACCOUNT_BALANCE_PATH)
        data = self._data(result)
        return {
            "cash": _optional_int(data.get("availableAmount")),
            "frozen": _optional_int(data.get("tempFrozenAmount")),
            "settle": _optional_int(data.get("pendingAmount")),
        }

    def deposit_balance(self) -> int | None:
        result = self._get(DEPOSIT_STATE_PATH)
        data = self._data(result)
        raw_value = data.get("depositBalance")
        if raw_value in (None, ""):
            raw_value = result.get("depositBalance")
        if raw_value in (None, ""):
            return None
        try:
            return int(float(raw_value))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _category_leaves(
        nodes: list[dict[str, Any]],
        parents: list[str] | None = None,
        parent_ids: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        output: list[dict[str, Any]] = []
        parents = parents or []
        parent_ids = parent_ids or []
        for node in nodes:
            if not isinstance(node, dict):
                continue
            path = [*parents, str(node.get("label") or "")]
            category_id = str(node.get("value") or "")
            id_path = [
                *parent_ids,
                *([category_id] if category_id else []),
            ]
            children = node.get("options")
            if isinstance(children, list) and children:
                output.extend(
                    ShopPrivateClient._category_leaves(
                        children,
                        path,
                        id_path,
                    )
                )
                continue
            if not node.get("leaf") or _as_int(node.get("state"), 0) != 0:
                continue
            output.append(
                {
                    "category_id": category_id,
                    "id_path": id_path,
                    "name": str(node.get("label") or ""),
                    "path": " / ".join(value for value in path if value),
                    "forbidden": bool(
                        node.get("isForbidForSpecialization")
                    ),
                }
            )
        return output

    def category_catalog(self) -> list[dict[str, Any]]:
        tree_result = self._get(FULL_CATEGORY_TREE_PATH)
        return self._category_leaves(
            list(tree_result.get("productCats") or [])
        )

    def category_candidates(
        self,
        max_deposit_cents: int,
    ) -> list[dict[str, Any]]:
        tree_result = self._get(FULL_CATEGORY_TREE_PATH)
        leaves = self._category_leaves(
            list(tree_result.get("productCats") or [])
        )
        if not leaves:
            return []
        existing: set[str] = set()
        for relation_status in (1, 2):
            relation_result = self._post(
                CATEGORY_RELATION_PATH,
                {"status": relation_status},
            )
            existing.update(
                str(item.get("id") or "")
                for item in relation_result.get("categoryRelationList") or []
                if isinstance(item, dict)
            )
        candidates = [
            item
            for item in leaves
            if item["category_id"]
            and item["category_id"] not in existing
            and not item["forbidden"]
        ]
        if not candidates:
            return []
        ids = [item["category_id"] for item in candidates]
        deposit_by_id: dict[str, dict[str, Any]] = {}
        requirement_by_id: dict[str, dict[str, Any]] = {}
        deposit_balance = 0
        real_deposit_balance = 0
        operation_required_deposit: int | None = None
        for start in range(0, len(ids), 200):
            chunk = ids[start : start + 200]
            deposit_result = self._post(
                CATEGORY_DEPOSIT_PATH,
                {
                    "categoryList": [
                        {"categoryId": category_id}
                        for category_id in chunk
                    ]
                },
            )
            deposit_balance = _as_int(
                deposit_result.get("depositBalance")
            )
            real_deposit_balance = _as_int(
                deposit_result.get("realDepositBalance"),
                deposit_balance,
            )
            operation_required_deposit = _as_int(
                deposit_result.get("operationRequiredDeposit"),
                None,
            )
            for item in deposit_result.get("categoryDepositList") or []:
                if not isinstance(item, dict):
                    continue
                category = item.get("category") or {}
                category_id = str(category.get("categoryId") or "")
                if category_id:
                    deposit_by_id[category_id] = item
            requirement_result = self._post(
                CATEGORY_REQUIREMENT_PATH,
                {"categoryIds": [int(value) for value in chunk]},
            )
            requirement_data = self._data(requirement_result)
            for category_id in requirement_data.get(
                "needQualificationIdList"
            ) or []:
                requirement_by_id[str(category_id)] = {"required": True}
        rows: list[dict[str, Any]] = []
        for item in candidates:
            category_id = item["category_id"]
            deposit = deposit_by_id.get(category_id)
            if not deposit:
                continue
            required = _as_int(deposit.get("requiredDeposit"))
            if required > max(0, int(max_deposit_cents)):
                continue
            rows.append(
                {
                    **item,
                    "required_deposit": required,
                    "amount_to_pay": _as_int(deposit.get("amountToPay")),
                    "deposit_balance": _as_int(
                        deposit.get("depositBalance"),
                        deposit_balance,
                    ),
                    "real_deposit_balance": real_deposit_balance,
                    "operation_required_deposit": (
                        operation_required_deposit
                    ),
                    "deposit_free_trial": bool(
                        deposit.get("isSupportDepositFreeTrial")
                    ),
                    "status": (
                        "需资质"
                        if requirement_by_id.get(category_id)
                        else "可申请"
                    ),
                }
            )
        return rows

    def batch_add_categories(
        self,
        category_ids: list[str],
    ) -> dict[str, Any]:
        ids = [int(value) for value in category_ids if str(value).isdigit()]
        if not ids:
            return {}
        return self._data(
            self._post(CATEGORY_ADD_PATH, {"categoryIdList": ids})
        )

    def category_application_checks(
        self,
        category_ids: list[str],
        business_type: str | None = None,
    ) -> dict[str, dict[str, Any]]:
        business_type = (
            business_type
            or getattr(self, "category_business_type", "")
            or "individual"
        )
        normalized_business_type = (
            business_type
            if business_type in {"individual", "enterprise"}
            else "individual"
        )
        requested = list(
            dict.fromkeys(
                str(category_id)
                for category_id in category_ids
                if str(category_id).strip()
            )
        )
        checks = {
            category_id: {
                "state": "unknown",
                "label": "状态未知",
                "reason": "",
                "eligible": False,
            }
            for category_id in requested
        }
        if not requested:
            return checks
        try:
            relation_rows: dict[str, tuple[int, dict[str, Any]]] = {}
            for relation_status in (1, 2):
                result = self._post(
                    CATEGORY_RELATION_PATH,
                    {"status": relation_status},
                )
                for item in result.get("categoryRelationList") or []:
                    if not isinstance(item, dict):
                        continue
                    category_id = str(
                        item.get("id")
                        or item.get("categoryId")
                        or (item.get("category") or {}).get("categoryId")
                        or ""
                    )
                    if category_id in checks:
                        relation_rows[category_id] = (
                            relation_status,
                            item,
                        )
            catalog = {
                str(item.get("category_id") or ""): item
                for item in self.category_catalog()
                if str(item.get("category_id") or "")
            }
            deposit_by_id: dict[str, dict[str, Any]] = {}
            deposit_balance = 0
            real_deposit_balance = 0
            operation_required_deposit: int | None = None
            qualification_ids: set[str] = set()
            for start in range(
                0,
                len(requested),
                CATEGORY_CHECK_BATCH_SIZE,
            ):
                chunk = requested[
                    start : start + CATEGORY_CHECK_BATCH_SIZE
                ]
                deposit_result = self._post(
                    CATEGORY_DEPOSIT_PATH,
                    {
                        "categoryList": [
                            {"categoryId": category_id}
                            for category_id in chunk
                        ],
                        "businessType": normalized_business_type,
                    },
                )
                for item in deposit_result.get(
                    "categoryDepositList"
                ) or []:
                    if not isinstance(item, dict):
                        continue
                    category_id = str(
                        (item.get("category") or {}).get(
                            "categoryId"
                        )
                        or ""
                    )
                    if category_id:
                        deposit_by_id[category_id] = item
                deposit_balance = _as_int(
                    deposit_result.get("depositBalance"),
                    deposit_balance,
                )
                real_deposit_balance = _as_int(
                    deposit_result.get("realDepositBalance"),
                    deposit_balance,
                )
                operation_required_deposit = _as_int(
                    deposit_result.get("operationRequiredDeposit"),
                    operation_required_deposit,
                )
                requirement_result = self._post(
                    CATEGORY_REQUIREMENT_PATH,
                    {
                        "categoryIds": [
                            int(value) for value in chunk
                        ]
                    },
                )
                requirement_data = self._data(requirement_result)
                qualification_ids.update(
                    str(value)
                    for value in requirement_data.get(
                        "needQualificationIdList"
                    ) or []
                )
        except Exception as exc:
            for check in checks.values():
                check["reason"] = str(exc)[:500]
            return checks
        for category_id, check in checks.items():
            relation = relation_rows.get(category_id)
            deposit = deposit_by_id.get(category_id) or {}
            required_deposit = _as_int(
                deposit.get("requiredDeposit")
            )
            amount_to_pay = _as_int(
                deposit.get("amountToPay")
            )
            category_deposit_balance = _as_int(
                deposit.get("depositBalance"),
                deposit_balance,
            )
            deposit_fields = {
                "deposit_amount": required_deposit,
                "required_deposit": required_deposit,
                "amount_to_pay": amount_to_pay,
                "deposit_balance": category_deposit_balance,
                "real_deposit_balance": real_deposit_balance,
                "operation_required_deposit": (
                    operation_required_deposit
                ),
                "deposit_free_trial": bool(
                    deposit.get("isSupportDepositFreeTrial")
                ),
                "business_type": (
                    normalized_business_type
                ),
            }
            deposit_fields["deposit_shortfall"] = max(
                0,
                (
                    operation_required_deposit
                    if operation_required_deposit is not None
                    else (
                        0
                        if deposit_fields["deposit_free_trial"]
                        else amount_to_pay
                    )
                )
                - category_deposit_balance,
            )
            if relation and relation[0] == 1:
                check.update(
                    state="effective",
                    label="已生效",
                    reason="",
                    **deposit_fields,
                )
                continue
            if relation and relation[0] == 2:
                reason = str(
                    relation[1].get("uneffectiveReason")
                    or relation[1].get("reason")
                    or ""
                ).strip()
                reason_text = reason.casefold()
                if not reason:
                    state = "pending"
                    label = "审核中"
                elif any(
                    word in reason_text
                    for word in ("拒绝", "驳回", "不通过", "未通过")
                ):
                    state = "rejected"
                    label = "平台已拒绝"
                elif any(
                    word in reason_text
                    for word in ("资质", "许可证", "证明")
                ):
                    state = "qualification_required"
                    label = "缺资质"
                elif any(
                    word in reason_text
                    for word in ("保证金", "押金", "补缴")
                ):
                    state = "deposit_required"
                    label = "保证金不足"
                else:
                    state = "unavailable"
                    label = "不可申请"
                check.update(
                    state=state,
                    label=label,
                    reason=reason[:500],
                    **deposit_fields,
                )
                continue
            leaf = catalog.get(category_id)
            if leaf is None or leaf.get("forbidden"):
                check.update(
                    state="unavailable",
                    label="不可申请",
                    reason=(
                        "平台目录中未找到该叶子类目"
                        if leaf is None
                        else "该类目被平台禁止申请"
                    ),
                    **deposit_fields,
                )
                continue
            if category_id in qualification_ids:
                check.update(
                    state="qualification_required",
                    label="缺资质",
                    reason="该叶子类目要求补充平台资质",
                    **deposit_fields,
                )
                continue
            if not deposit:
                check.update(
                    state="unknown",
                    label="状态未知",
                    reason="保证金接口未返回该类目信息",
                )
                continue
            if deposit_fields["deposit_shortfall"] > 0:
                check.update(
                    state="deposit_required",
                    label="保证金不足",
                    reason=(
                        "当前保证金余额 "
                        f"{category_deposit_balance} 分，"
                        "至少还需补缴 "
                        f"{deposit_fields['deposit_shortfall']} 分；"
                        "软件不会自动充值或支付"
                    ),
                    eligible=False,
                    **deposit_fields,
                )
                continue
            check.update(
                state="not_applied",
                label="未申请",
                reason=(
                    f"保证金门槛 {required_deposit} 分，"
                    "由平台申请响应确认是否需补缴"
                    if required_deposit > 0
                    else ""
                ),
                eligible=True,
                **deposit_fields,
            )
        return checks

    def category_relation_statuses(
        self,
        category_ids: list[str],
    ) -> dict[str, str]:
        requested = {
            str(category_id)
            for category_id in category_ids
            if str(category_id)
        }
        found: dict[str, str] = {}
        for relation_status, label in ((1, "已生效"), (2, "审核中")):
            result = self._post(
                CATEGORY_RELATION_PATH,
                {"status": relation_status},
            )
            for item in result.get("categoryRelationList") or []:
                if not isinstance(item, dict):
                    continue
                category_id = str(
                    item.get("id")
                    or item.get("categoryId")
                    or (item.get("category") or {}).get("categoryId")
                    or ""
                )
                if category_id in requested:
                    found[category_id] = label
        return {
            category_id: found.get(category_id, "未查到")
            for category_id in requested
        }

    def recommend_overview(
        self,
        yesterday: bool = False,
        *,
        days: int | None = None,
    ) -> dict[str, int | None]:
        if days is None:
            day = date.today() - timedelta(days=1 if yesterday else 0)
            start = datetime.combine(day, datetime_time.min)
            end = (
                datetime.combine(day, datetime_time(23, 59, 59))
                if yesterday
                else datetime.now()
            )
            time_query = {
                "dateFormat": 1,
                "dateType": 1,
                "startMs": int(start.timestamp() * 1000),
                "endMs": int(end.timestamp() * 1000),
            }
            is_today = not yesterday
            is_yesterday = yesterday
        else:
            span = max(1, min(int(days), 90))
            first_day = date.today() - timedelta(days=span - 1)
            start = datetime.combine(first_day, datetime_time.min)
            end = datetime.now()
            time_query = {
                "dateFormat": 2,
                "dateType": 0,
                "dateRange": span,
                "startMs": int(start.timestamp() * 1000),
                "endMs": int(end.timestamp() * 1000),
            }
            is_today = False
            is_yesterday = False
        result = self._post(
            RECOMMEND_STATS_PATH,
            {
                "isToday": is_today,
                "isYesterday": is_yesterday,
                "time_query": time_query,
            },
        )
        data = result.get("data")
        if isinstance(data, dict):
            data = data.get("data")
        total = (
            data.get("total")
            if isinstance(data, dict)
            and isinstance(data.get("total"), dict)
            else {}
        )
        exposure_count = _optional_int(total.get("product_view_cnt"))
        exposure_people = _optional_int(total.get("product_view_uv"))
        return {
            "exposure": exposure_people
            if exposure_people is not None
            else exposure_count,
            "exposure_count": exposure_count,
            "exposure_people": exposure_people,
            "click": _optional_int(total.get("product_click_uv")),
            "orders": _optional_int(total.get("pay_order_cnt")),
            "gmv": _optional_int(total.get("pay_gmv")),
        }

    def product_overview(
        self,
        *,
        days: int = 1,
        offset_days: int = 0,
        realtime: bool = False,
    ) -> dict[str, Any]:
        """Read the Compass product-conversion overview.

        This is distinct from ``getShopRecommendProductsStat``: Compass uses
        ``product-overview-shop-*`` and exposes product exposure, click and
        transaction metrics from the merchant's product-data page.
        """
        today = date.today()
        if realtime:
            first = today
            last = today
            end = datetime.now()
            date_range = 1
            date_format = 1
            overview_id = "product-overview-shop-realtime"
        else:
            span = max(1, int(days))
            last = today - timedelta(days=max(1, int(offset_days)))
            first = last - timedelta(days=span - 1)
            end = datetime.combine(last, datetime_time.max)
            date_range = span
            date_format = 2
            overview_id = "product-overview-shop-offline"
        start = datetime.combine(first, datetime_time.min)
        result = self._post(
            PRODUCT_OVERVIEW_PATH,
            {
                "time_query": {
                    "dateFormat": date_format,
                    "dateType": 0,
                    "dateRange": date_range,
                    "startMs": int(start.timestamp() * 1000),
                    "endMs": int(end.timestamp() * 1000),
                },
                "id": overview_id,
                "biz": "compass",
                "sale_type": "0",
            },
            headers=self._product_compass_auth_headers(),
        )
        total = result.get("total")
        return dict(total) if isinstance(total, dict) else {}

    def search_overview(self, days: int = 30) -> dict[str, int]:
        span = max(1, min(int(days), 90))
        last = date.today() - timedelta(days=1)
        first = last - timedelta(days=span - 1)
        start = int(time.mktime(first.timetuple()) * 1000)
        end = int(
            time.mktime(
                datetime.combine(last, datetime_time.max).timetuple()
            )
            * 1000
        )
        result = self._post(
            SEARCH_STATS_PATH,
            {
                "isToday": False,
                "isYesterday": False,
                "time_query": {
                    "dateFormat": 2,
                    "dateType": 0,
                    "dateRange": span,
                    "startMs": start,
                    "endMs": end,
                },
            },
        )
        data = result.get("data")
        if isinstance(data, dict):
            data = data.get("data")
        totals = (
            data.get("total")
            if isinstance(data, dict) and isinstance(data.get("total"), dict)
            else {}
        )
        yesterday = date.today() - timedelta(days=1)
        yday_key = yesterday.strftime("%Y%m%d")
        trend = data.get("trend") if isinstance(data, dict) else []
        yday_row = next(
            (
                row
                for row in (trend or [])
                if isinstance(row, dict)
                and str(row.get("datetime") or "") == yday_key
            ),
            {},
        )
        return {
            "visitors_30d": _as_int(totals.get("product_view_uv")),
            "product_click_30d": _as_int(totals.get("product_click_uv")),
            "search_pay_orders": _as_int(totals.get("pay_order_cnt")),
            "search_pay_gmv": _as_int(totals.get("pay_gmv")),
            "search_exposure_yesterday": _as_int(
                yday_row.get("product_view_cnt")
            ),
        }
