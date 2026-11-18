from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DongxiaoSection:
    key: str
    label: str
    description: str


DONGXIAO_SECTIONS = (
    DongxiaoSection("overview", "总览", "店铺、商品、商品池和任务状态"),
    DongxiaoSection("stores", "店铺连接", "复用澜琅现有店铺与授权状态"),
    DongxiaoSection("pools", "商品池", "商品导入、分组与池状态"),
    DongxiaoSection("products", "商品筛选", "目录、销量、价格与 SKU 条件"),
    DongxiaoSection("tasks", "任务中心", "持久任务、进度与异常处理"),
    DongxiaoSection("orders", "真实订单", "平台真实订单查询与核验"),
    DongxiaoSection("aftersales", "售后工单", "试算、审批、执行与回查"),
    DongxiaoSection("audit", "日志审计", "操作人、店铺、请求与结果记录"),
)
