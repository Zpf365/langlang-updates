from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Callable

from .db import order_status_text, product_status_text
from .models import Order, OrderItem, Product, Store


ProgressCallback = Callable[[str, int, int], None]


class MockShopClient:
    def __init__(self, store: Store):
        self.store = store

    def test_connection(self) -> str:
        return "演示店铺连接成功"

    def fetch_orders(
        self,
        start_time: int,
        end_time: int,
        progress: ProgressCallback | None = None,
    ) -> list[Order]:
        now = int(time.time())
        base = max(start_time, now - 86400 * 2)
        samples = [
            (f"DEMO{datetime.now():%m%d}001", 20, "张三", "13800000001", 6990, 1, "示例商品 A"),
            (f"DEMO{datetime.now():%m%d}002", 30, "李四", "13800000002", 12990, 2, "示例商品 B"),
            (f"DEMO{datetime.now():%m%d}003", 100, "王五", "13800000003", 3990, 1, "示例商品 C"),
        ]
        orders: list[Order] = []
        for index, (order_id, status, buyer, phone, amount, count, title) in enumerate(samples, 1):
            if progress:
                progress(f"正在载入演示订单 {index}/{len(samples)}", index, len(samples))
            item = OrderItem(
                product_id=f"P{index:03d}",
                sku_id=f"S{index:03d}",
                title=title,
                sku_count=count,
                sale_price=amount // max(1, count),
            )
            order_time = min(end_time, max(start_time, base + index * 1800))
            orders.append(
                Order(
                    store_id=self.store.id,
                    store_name=self.store.name,
                    order_id=order_id,
                    status=status,
                    status_text=order_status_text(status),
                    create_time=order_time,
                    update_time=order_time,
                    pay_time=order_time if status != 10 else 0,
                    total_amount=amount,
                    product_amount=amount,
                    freight_amount=0,
                    discount_amount=0,
                    buyer_name=buyer,
                    phone=phone,
                    address="湖北省 武汉市 青山区 演示地址",
                    waybill_id=f"SFDEMO{index:06d}" if status in {21, 30, 100} else "",
                    carrier="顺丰速运" if status in {21, 30, 100} else "",
                    merchant_notes="这是本地演示数据，不连接微信接口。",
                    items=[item],
                    raw={"demo": True, "order_id": order_id},
                )
            )
        return orders

    def fetch_products(
        self,
        progress: ProgressCallback | None = None,
    ) -> list[Product]:
        products = [
            ("P001", "示例商品 A", 5, 1990, 26, "SKU-A"),
            ("P002", "示例商品 B", 11, 3990, 8, "SKU-B"),
            ("P003", "示例商品 C", 5, 990, 105, "SKU-C"),
            ("P004", "演示回收站商品", 6, 5990, 0, "SKU-D"),
        ]
        result: list[Product] = []
        for index, (pid, title, status, price, stock, code) in enumerate(products, 1):
            if progress:
                progress(f"正在载入演示商品 {index}/{len(products)}", index, len(products))
            result.append(
                Product(
                    store_id=self.store.id,
                    store_name=self.store.name,
                    product_id=pid,
                    title=title,
                    status=status,
                    status_text=product_status_text(status),
                    edit_status=4 if status == 5 else 0,
                    min_price=price,
                    sku_count=1,
                    stock_total=stock,
                    spu_code=code,
                    out_product_id=f"OUT-{pid}",
                    head_img="",
                    raw={
                        "demo": True,
                        "product_id": pid,
                        "skus": [
                            {
                                "sku_id": code,
                                "sku_code": code,
                                "sale_price": price,
                                "stock_num": stock,
                            }
                        ],
                    },
                )
            )
        return result

    def decode_sensitive_info(self, order_id: str) -> dict:
        return {
            "errcode": 0,
            "address_info": {
                "user_name": "演示买家",
                "tel_number": "13800000000",
                "province_name": "湖北省",
                "city_name": "武汉市",
                "county_name": "青山区",
                "detail_info": "演示地址",
            },
            "virtual_number_info": {"virtual_number": "17000000000", "extension": "123"},
        }

    def fetch_store_info(self) -> dict:
        return {
            "nickname": "澜琅演示店铺",
            "headimg_url": "",
            "subject_type": "企业",
            "status": "open_finished",
            "username": "demo-store",
            "is_local_life": 0,
            "open_timestamp": int(time.time()) - 86400 * 120,
        }

    def fetch_after_sale_ids(
        self,
        start_time: int,
        end_time: int,
        time_field: str = "create",
    ) -> list[str]:
        return ["DEMO-AS-001", "DEMO-AS-002"]

    def fetch_order_ids_by_update(self, start_time: int, end_time: int) -> list[str]:
        return []

    def fetch_after_sale(self, after_sale_id: str) -> dict:
        now = int(time.time())
        return {
            "after_sale_order_id": after_sale_id,
            "order_id": "DEMOORDER001",
            "status": "MERCHANT_PROCESSING",
            "type": "REFUND",
            "reason_text": "不需要了",
            "refund_info": {"amount": 1990},
            "create_time": now - 3600,
            "update_time": now - 600,
        }

    def fetch_shop_overview(self, day: str) -> dict[str, str]:
        return {
            "pay_gmv": "29980",
            "pay_uv": "12",
            "pay_order_cnt": "14",
            "pay_refund_gmv": "1990",
            "product_click_uv": "86",
            "live_pay_gmv": "0",
            "feed_pay_gmv": "1200",
        }

