from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DongxiaoSection:
    key: str
    label: str
    description: str


DONGXIAO_SECTIONS = (
    DongxiaoSection("overview", "总览与任务", "店铺、商品、批次与任务状态"),
    DongxiaoSection("stores", "店铺与授权", "复用澜琅现有店铺与授权状态"),
    DongxiaoSection("pools", "商品池管理", "商品导入、分组与池状态"),
    DongxiaoSection("products", "商品筛选", "目录、销量、价格与 SKU 条件"),
    DongxiaoSection("sku_rules", "SKU 规则", "库存、限购、价格与规格核验"),
    DongxiaoSection(
        "cart_batch",
        "批量加购",
        "桌面多账号批量加购、购物车回查与速度控制",
    ),
    DongxiaoSection(
        "pending_orders",
        "待付款订单",
        "购物车核验结果、待付款草案与人工审批",
    ),
    DongxiaoSection(
        "accounts",
        "多账号任务",
        "商家执行账号与独立买家微信账号管理",
    ),
    DongxiaoSection(
        "buyer_flow",
        "买家完整流程",
        "买家授权、商品预选、官方客户端交接和待付款订单核验",
    ),
    DongxiaoSection("tasks", "任务中心", "持久任务、进度与异常处理"),
    DongxiaoSection("orders", "订单与售后", "平台真实订单查询与核验"),
    DongxiaoSection("aftersales", "售后工单", "试算、审批、执行与回查"),
    DongxiaoSection("audit", "日志与诊断", "操作人、店铺、请求与结果记录"),
    DongxiaoSection(
        "real_ops",
        "真实操作管理",
        "按店铺开启真实写操作并进入现有业务页面",
    ),
    DongxiaoSection(
        "preflight",
        "实盘前检查",
        "数据库、授权、接口、调度器与资金安全验收",
    ),
)
