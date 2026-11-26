from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from .. import __version__
from ..db import Database


def _settings_list(db: Database, key: str) -> list[dict[str, Any]]:
    try:
        rows = json.loads(db.get_setting(key, "[]"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def delivery_ledger_payload(
    db: Database,
    *,
    account: dict[str, Any],
) -> dict[str, Any]:
    now = datetime.now()
    stores = list(db.list_stores())
    products = db.list_products()
    after_sales = db.list_after_sales()
    upload_batches = _settings_list(db, "upload_batch")
    upload_tasks = _settings_list(db, "upload_tasks")
    credential_states = db.store_credential_state_map()
    invalid_credentials = sum(
        str(row.get("state") or "") == "invalid"
        for row in credential_states.values()
    )
    pending_candidates = len(
        db.list_store_credential_candidates(states=("pending",))
    )
    logs = db.list_task_logs(limit=20)
    modules = [
        {
            "id": "orders",
            "title": "订单管理",
            "status": "completed",
            "summary": "A 版高密度订单列表和嵌入订单工作台已交付。",
            "evidence": [
                "订单列表、采购、售后、利润、消息、日志、设置均有真实 handler。",
                "订单模块已通过两轮验收并作为受保护基线。",
            ],
            "route": "/orders",
        },
        {
            "id": "operations",
            "title": "运营中心",
            "status": "completed",
            "summary": "五个运营工作区、行动任务、巡检规则和官方深链已交付。",
            "evidence": [
                "真实数据库指标和官方 T+1 数据",
                "运营任务、规则持久化到 SQLite",
            ],
            "route": "/operations",
        },
        {
            "id": "permissions",
            "title": "团队权限后台",
            "status": "completed",
            "summary": "运营权限菜单、路由和 RPC 三重校验已交付。",
            "evidence": ["owner/admin 默认权限", "撤权即时生效并记录审计"],
            "route": "/more/team",
        },
        {
            "id": "upload",
            "title": "三类上货 WebUI",
            "status": "partial",
            "summary": "1688、淘宝数据、微信小店数据的本地导入与任务工作台已交付。",
            "evidence": [
                "复用旧版数据包解析器和上传 Runner",
                "模板、建任务、暂停、取消、重试、日志和导出已接通",
            ],
            "boundary": "真实 1688 网络采集未实测，真实商品创建未执行。",
            "route": "/product-upload",
        },
        {
            "id": "batch_stores",
            "title": "店铺批量添加与密钥候选",
            "status": "partial",
            "summary": "Paste/CSV/XLSX 本地预检、AppID 去重、候选加密和刷新验证已交付。",
            "evidence": [
                "导入阶段零网络请求",
                "候选 claim、替换和失败恢复有专项测试",
            ],
            "boundary": "未使用真实店铺密钥执行候选替换。",
            "route": "/shops",
        },
        {
            "id": "account_switch",
            "title": "切换账号与退出登录",
            "status": "partial",
            "summary": "外层登录循环、按 account_id 隔离路径、HTTP session token 重建已交付。",
            "evidence": ["普通切换保留本机账号", "退出登录 forget_local"],
            "boundary": "未自动登录真实账户执行登录后可视切换冒烟。",
            "route": "/shops",
        },
        {
            "id": "hosted",
            "title": "托管中心",
            "status": "partial",
            "summary": "配置和执行链路复用现有托管 Runner，部分达人筛选控件尚无真实接口。",
            "evidence": ["配置和定时设置持久化", "执行结果写托管日志"],
            "boundary": "达人类目、粉丝量、GMV 等高级筛选仍需平台数据接口。",
            "route": "/hosted",
        },
        {
            "id": "activity",
            "title": "平台活动管理",
            "status": "partial",
            "summary": "旧版活动目录、报名、商品预检、SKU 提交和移除全部流程已放出到 WebUI。",
            "evidence": [
                "复用原 platform activity 服务与活动操作表",
                "报名、提交和移除均经过当前店铺写授权",
            ],
            "boundary": "尚未使用真实店铺执行报名、提交或移除写操作。",
            "route": "/activity",
        },
        {
            "id": "customer_service",
            "title": "AI 客服",
            "status": "not_implemented",
            "summary": "当前没有真实客服会话与消息数据源，页面只保留空态。",
            "evidence": [],
            "boundary": "客服首响和未回复轮次无真实数据源。",
            "route": "/customer-service",
        },
        {
            "id": "logistics",
            "title": "物流监控",
            "status": "partial",
            "summary": "已接入本地真实订单与售后状态的异常筛选。",
            "evidence": [
                "列表只保留已退回、售后和超过 48 小时未签收的停滞订单",
                "在途、待揽收、运输中仅做数量统计",
            ],
            "boundary": "快递轨迹事件明细接口尚未接入；停滞按平台最后订单/发货状态时间判断。",
            "route": "/logistics",
        },
        {
            "id": "transit",
            "title": "中转订单",
            "status": "not_implemented",
            "summary": "当前没有可用中转业务接口。",
            "evidence": [],
            "boundary": "申请开通、拦截、自动出单和费用对账均未接入。",
            "route": "/transit",
        },
        {
            "id": "supply_market",
            "title": "货源市场",
            "status": "not_implemented",
            "summary": "当前没有货源市场数据接口。",
            "evidence": [],
            "boundary": "选品、整店搬运和货源日志未接入。",
            "route": "/supply-market",
        },
    ]
    boundaries = [
        {
            "id": "1688_live",
            "title": "真实 1688 采集",
            "status": "unverified",
            "detail": "当前真实数据库没有 1688 采购账号，未执行真实网络采集。",
        },
        {
            "id": "product_create",
            "title": "真实商品创建",
            "status": "unverified",
            "detail": "未向真实店铺创建商品，未消耗提审额度。",
        },
        {
            "id": "secret_replace",
            "title": "真实失效密钥替换",
            "status": "unverified",
            "detail": "未使用真实店铺密钥执行候选替换。",
        },
        {
            "id": "real_account_switch",
            "title": "真实登录后切换账号",
            "status": "unverified",
            "detail": "未自动提交真实账户凭据；循环与隔离使用假账号和临时目录验证。",
        },
        {
            "id": "missing_sources",
            "title": "缺失经营数据源",
            "status": "blocked",
            "detail": "分时曝光、加购明细、客服首响和未回复轮次尚无真实数据源。",
        },
        {
            "id": "activity_live",
            "title": "真实平台活动写操作",
            "status": "unverified",
            "detail": "活动页面已接入旧版真实接口，但未对真实店铺执行报名或商品提交写操作。",
        },
    ]
    return {
        "version": __version__,
        "generated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "account": account,
        "counts": {
            "stores": len(stores),
            "orders": db.count_orders(),
            "products": len(products),
            "after_sales": len(after_sales),
            "upload_batches": len(upload_batches),
            "upload_tasks": len(upload_tasks),
            "credential_invalid": invalid_credentials,
            "credential_candidates_pending": pending_candidates,
        },
        "modules": modules,
        "boundaries": boundaries,
        "recent_audit": [
            {
                "id": row.get("id"),
                "scope": row.get("scope"),
                "operation": row.get("operation"),
                "status": row.get("status"),
                "message": row.get("message"),
                "started_at": row.get("started_at"),
                "finished_at": row.get("finished_at"),
            }
            for row in logs
        ],
        "status_definitions": {
            "completed": "已完成并通过验收",
            "partial": "核心链路已交付，但仍存在未验证边界",
            "unverified": "实现存在但缺少真实环境验证",
            "blocked": "缺少真实数据源或外部条件",
            "not_implemented": "尚未实现",
        },
    }
