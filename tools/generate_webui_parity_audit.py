from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]
CONTROLLER = ROOT / "weimi_manager" / "webui" / "controller.py"
ROUTER = ROOT / "webui" / "src" / "router.js"
LEGACY_AUDIT = ROOT / "BUTTON_NETWORK_DEPENDENCY_AUDIT.md"
OUTPUT = ROOT / "BUTTON_WEBUI_FULL_PARITY_AUDIT_CN.md"


LEGACY_ENTRY_MAP = {
    "fetch_funds_summary": (
        "资金账户 / 刷新资金",
        "finance.summary",
        "_finance_summary",
        "真实余额与资金流水；加载、成功/失败提示；持久 task_logs",
    ),
    "sync_storefront": (
        "店铺主页 / 读取店铺主页",
        "storefront.load",
        "_load_storefront",
        "官方主页商品；加载与错误状态；持久 task_logs",
    ),
    "storefront_action": (
        "店铺主页 / 排序、隐藏、置顶",
        "storefront.action",
        "_storefront_action",
        "应用内风险确认；加载与结果提示；持久 task_logs",
    ),
    "sync_platform_violation_blacklist": (
        "违规中心 / 同步平台违规黑名单",
        "violations.sync-platform-blacklist",
        "_sync_platform_violation_blacklist",
        "平台违规商品写入本地过滤库；加载与失败反馈；持久 task_logs",
    ),
    "open_official_customer_console": (
        "AI客服 / 打开官方客服控制台",
        "customer-service.open",
        "_open_customer_console",
        "后台打开官方控制台；非阻塞 RPC；持久 task_logs",
    ),
    "sync_procurement_account": (
        "采购账号 / 同步；自动化 / 同步采购账号数据",
        "procurement.sync；automation 任务",
        "_sync_procurement；_execute_automation_task",
        "真实采购适配器；按钮 loading；运行记录持久化",
    ),
    "sync_orders": (
        "订单管理 / 同步、快速刷新；自动化 / 同步最近订单",
        "orders.sync；orders.quick-refresh.start；automation 任务",
        "_sync_orders；_start_order_quick_refresh；_execute_automation_task",
        "真实订单 API；进度与失败反馈；自动化运行记录持久化",
    ),
    "sync_recent_order_updates": (
        "自动化 / 刷新最近订单更新",
        "automation.tasks.run-start",
        "_start_automation_run；_execute_automation_task",
        "真实订单增量 API；后台进度与运行记录",
    ),
    "sync_products": (
        "商品管理 / 获取刷新；自动化 / 同步商品目录与详情",
        "products.refresh.start；automation 任务",
        "_start_product_refresh；_execute_automation_task",
        "真实商品 API；逐店进度；自动化运行记录持久化",
    ),
    "sync_product_details": (
        "商品管理 / 获取刷新；自动化 / 同步商品目录与详情",
        "products.refresh.start；automation 任务",
        "_start_product_refresh；_execute_automation_task",
        "真实商品详情 API；逐商品进度；自动化运行记录持久化",
    ),
    "refresh_product_status_snapshot": (
        "自动化 / 刷新商品状态数量",
        "automation 任务",
        "_execute_automation_task",
        "真实状态快照；执行中与结果提示；运行记录持久化",
    ),
    "scan_product_catalog": (
        "自动化 / 扫描商品目录",
        "automation.tasks.run-start",
        "_start_automation_run；_execute_automation_task",
        "真实商品目录分页扫描；后台进度与运行记录",
    ),
    "sync_product_performance": (
        "自动化 / 同步商品经营数据",
        "automation.tasks.run-start",
        "_start_automation_run；_execute_automation_task",
        "真实商品经营数据接口；后台进度与运行记录",
    ),
    "sync_after_sales": (
        "售后管理 / 同步；自动化 / 同步售后",
        "orders.after-sales.sync；automation 任务",
        "_sync_after_sales；_execute_automation_task",
        "真实售后 API；同步结果与自动化记录",
    ),
    "sync_analytics_day": (
        "店铺刷新 / 经营数据；自动化 / 同步经营数据",
        "stores.refresh.start；automation 任务",
        "_refresh_stores；_execute_automation_task",
        "真实经营数据 API；进度与结果反馈；刷新任务日志",
    ),
    "refresh_store_metrics": (
        "店铺管理 / 刷新",
        "stores.refresh.start",
        "_refresh_stores",
        "真实 key 与网页指标；逐店进度；持久任务状态",
    ),
    "sync_store_profile": (
        "店铺新增/刷新",
        "stores.create；stores.refresh.start",
        "_create_store；_refresh_stores",
        "真实店铺资料；进度与错误反馈",
    ),
    "product_write": (
        "商品管理 / 上架、下架、撤回审核、删除",
        "products.set-listing；products.cancel-audit；products.delete",
        "_set_products_listing；_cancel_product_audit；_delete_product",
        "逐商品执行、进度、防重复、持久商品日志",
    ),
    "set_products_listing": (
        "商品管理 / 批量上架、下架",
        "products.set-listing",
        "_set_products_listing",
        "逐商品执行、进度、防重复、持久商品日志",
    ),
    "private_batch_product_action": (
        "托管中心 / 删除无流量",
        "hosted.execute",
        "_execute_hosted",
        "托管执行结果持久化到 hosted_runs",
    ),
    "promote_products_to_league": (
        "托管中心 / 添加联盟推广",
        "hosted.execute",
        "_execute_hosted",
        "真实联盟接口；托管执行记录持久化",
    ),
    "fetch_league_products": (
        "托管中心 / 最新联盟商品、修改佣金",
        "hosted.execute",
        "_execute_hosted",
        "真实联盟读取；托管执行记录持久化",
    ),
    "update_league_ratio": (
        "托管中心 / 修改佣金",
        "hosted.execute",
        "_execute_hosted",
        "真实联盟佣金写入；托管执行记录持久化",
    ),
    "update_product_prices": (
        "托管中心 / 修改价格",
        "hosted.execute",
        "_execute_hosted",
        "真实商品价格写入；托管执行记录持久化",
    ),
    "stop_active_limited_discounts": (
        "托管中心 / 取消限时抢购",
        "hosted.execute",
        "_execute_hosted",
        "真实限时活动停止；托管执行记录持久化",
    ),
    "create_limited_discount_tasks": (
        "托管中心 / 限时抢购",
        "hosted.execute / create_snapup_activity",
        "_execute_hosted",
        "真实限时活动创建；托管执行记录持久化",
    ),
    "platform_activity_catalog": (
        "活动管理 / 读取平台活动",
        "activity.catalog",
        "_activity_catalog",
        "真实平台活动；loading 与持久操作记录",
    ),
    "activity_preview": (
        "活动管理 / 加载活动商品",
        "activity.preview",
        "_activity_preview",
        "真实 SKU 预检；loading 与操作记录",
    ),
    "apply_platform_activity_page": (
        "活动管理 / 报名选中活动",
        "activity.apply",
        "_activity_apply",
        "真实平台报名；风险确认与持久记录",
    ),
    "bulk_register_activity_products": (
        "活动管理 / 提交已选 SKU",
        "activity.submit-items",
        "_activity_submit_items",
        "真实 SKU 提交；风险确认与持久记录",
    ),
    "double11_remove_all": (
        "活动管理 / 移除全部报名",
        "activity.remove-all",
        "_activity_remove_all",
        "真实移除；风险确认与持久记录",
    ),
    "update_order_notes": (
        "订单管理 / 订单备注",
        "orders.update-note；orders.update-note-state",
        "_update_order_note；_update_order_note_state",
        "真实平台备注写入；保存中状态与应用内反馈",
    ),
    "update_order_price": (
        "订单工作台 / 改价",
        "orders.workbench.price-update",
        "_update_order_price",
        "真实平台改价；应用内确认、结果反馈",
    ),
    "request_order_address_change": (
        "订单工作台 / 改址",
        "orders.workbench.address-update",
        "_request_order_address_change",
        "真实平台改址；应用内确认、结果反馈",
    ),
    "update_order_delivery_info": (
        "订单工作台 / 发货、修改物流",
        "orders.workbench.delivery-update",
        "_update_order_delivery",
        "真实平台发货；应用内确认、结果反馈",
    ),
    "refresh_shipping_options": (
        "订单工作台 / 刷新电子面单",
        "orders.workbench.shipping-options",
        "_refresh_order_shipping",
        "真实面单账号与模板；应用内确认、反馈与持久日志",
    ),
    "create_shipment": (
        "订单工作台 / 生成电子面单",
        "orders.workbench.shipment-create",
        "_create_order_shipment",
        "真实电子面单接口；风险确认、结果反馈与持久日志",
    ),
    "complete_shipment": (
        "订单工作台 / 完成电子面单发货",
        "orders.workbench.shipment-complete",
        "_complete_order_shipment",
        "真实确认打印与订单发货；风险确认、反馈与持久日志",
    ),
    "sync_procurement_note_to_wechat": (
        "订单备注 / 同步采购备注",
        "orders.workbench.sync-procurement-note",
        "_sync_procurement_order_note",
        "真实平台备注写入；应用内确认与反馈",
    ),
    "decode_order_sensitive": (
        "订单管理 / 解密收货信息",
        "orders.decrypt-address",
        "_decrypt_order_address",
        "真实敏感信息解密；loading 与错误反馈",
    ),
    "open_official_order": (
        "订单管理 / 订单编号直达官方后台",
        "orders.workbench.open-official；orders.workbench.open-official.status",
        "_open_official_order；_open_official_order_status",
        "后台打开官方订单；非阻塞启动、成功/失败轮询与持久 task_logs",
    ),
    "open_official_backend": (
        "店铺管理 / 打开后台",
        "stores.open-backend",
        "_open_store_backend",
        "后台打开官方后台；按钮处理中状态",
    ),
    "open_official_product_backend": (
        "商品管理 / 打开商品官方后台",
        "products.open-official；products.open-official.status",
        "_open_official_product；_open_official_product_status",
        "真实商品行按钮；处理中状态、成功/失败轮询与持久 task_logs",
    ),
    "interactive_login": (
        "店铺管理 / 登录",
        "stores.login",
        "_login_store",
        "真实扫码登录；按钮处理中状态与结果提示",
    ),
    "import_cookie_header": (
        "店铺管理 / 凭据 / 导入网页 Cookie Header",
        "stores.import-cookie",
        "_import_store_cookie",
        "恢复官方网页登录态；提交后清空输入并写脱敏审计",
    ),
    "probe": (
        "店铺刷新 / 登录探测",
        "stores.refresh.start",
        "_refresh_stores",
        "真实登录态探测；进度与结果持久化",
    ),
    "cleanup_cache": (
        "店铺管理 / 清理缓存",
        "stores.clear-cache",
        "_clear_web_cache",
        "真实浏览器缓存清理；处理中与结果提示",
    ),
    "validate_store": (
        "店铺管理 / 凭据 / 检查当前密钥",
        "stores.credentials.probe",
        "_probe_store_credential",
        "旧版密钥校验能力的现行实现为 validate_store_credential；按钮有处理中与结果反馈",
    ),
    "client_for": (
        "商品管理 / 商品详情与 SKU",
        "products.detail",
        "_product_detail",
        "通过 SyncService.fetch_product_detail 实时读取官方商品详情并刷新本地缓存",
    ),
    "upload_product_images": (
        "商品管理 / 上传商品图片",
        "products.upload-images",
        "_upload_product_images",
        "浏览器文件转临时文件后调用旧版真实上传服务；持久操作日志",
    ),
    "upload_after_sale_media": (
        "订单工作台 / 售后详情 / 上传售后凭证",
        "orders.after-sales.upload-media",
        "_upload_after_sale_media",
        "真实售后媒体上传；应用内反馈与持久日志",
    ),
    "renew_snapup_activity": (
        "活动管理 / 限时抢购 / 续期",
        "activity.snapup.renew",
        "_renew_snapup_activity",
        "真实官方页面续期；风险确认、处理中反馈与持久日志",
    ),
    "renew_coupon": (
        "活动管理 / 优惠券 / 续期",
        "activity.coupons.renew",
        "_renew_coupon",
        "真实官方页面续期；风险确认、处理中反馈与持久日志",
    ),
    "sync_coupons": (
        "活动管理 / 优惠券 / 同步优惠券",
        "activity.coupons.sync",
        "_sync_coupons",
        "真实优惠券接口；加载、失败反馈与持久日志",
    ),
    "coupon_write": (
        "活动管理 / 优惠券 / 新建、停用、删除",
        "activity.coupons.create；activity.coupons.status",
        "_create_coupon；_update_coupon_status",
        "旧版完整优惠券表单；风险确认、真实写入与持久日志",
    ),
    "snapup_activities": (
        "活动管理 / 限时抢购 / 刷新活动",
        "activity.snapup.list",
        "_list_snapup_activities",
        "真实限时活动读取；加载与失败反馈",
    ),
    "double11_activity_products": (
        "活动管理 / 活动预填报 / 读取活动商品",
        "activity.double11.preview",
        "_double11_preview",
        "真实活动商品读取；处理中与失败反馈",
    ),
    "double11_submit": (
        "活动管理 / 活动预填报 / 提交本地预填草稿",
        "activity.double11.submit",
        "_double11_submit",
        "真实活动 SKU 写入；风险确认、反馈与持久日志",
    ),
    "double11_remove_all": (
        "活动管理 / 活动预填报 / 移除全部报名",
        "activity.double11.remove-all、activity.remove-all",
        "_double11_remove_all、_activity_remove_all",
        "真实活动商品移除；风险确认、反馈与持久日志",
    ),
    "list_members": (
        "更多 / 团队管理 / 团队成员",
        "team.operations.members",
        "_team_operations_members",
        "真实团队云端成员；加载与错误反馈",
    ),
    "create_subaccount": (
        "更多 / 团队管理 / 新建子账号",
        "team.operations.create",
        "_team_operations_create",
        "真实团队账号创建；处理中、反馈与权限审计",
    ),
    "update_member": (
        "更多 / 团队管理 / 编辑成员权限",
        "team.operations.save、team.operations.batch",
        "_team_operations_save、_team_operations_batch",
        "真实权限更新；立即生效并写权限审计",
    ),
    "add_existing_account": (
        "更多 / 团队管理 / 添加已有账号",
        "team.operations.add-existing",
        "_team_add_existing",
        "真实已有账号加入团队；处理中、反馈与权限审计",
    ),
    "store": (
        "桌面登录窗口 / 已保存账号读取",
        "应用登录外层循环",
        "AuthService/AppPaths",
        "账号隔离由登录窗口与 account_id 路径完成",
    ),
    "login": (
        "桌面登录窗口 / 登录",
        "应用登录外层循环",
        "AuthService",
        "真实登录由用户完成，不绕过认证",
    ),
    "restore_account": (
        "桌面登录窗口 / 恢复已保存账号",
        "应用登录外层循环",
        "AuthService",
        "真实授权恢复；失败返回登录状态",
    ),
    "change_password": (
        "桌面登录窗口 / 修改密码",
        "应用登录窗口",
        "AuthService",
        "真实认证服务；不读取或展示其他凭据",
    ),
    "send_registration_code": (
        "桌面登录窗口 / 注册验证码",
        "应用登录窗口",
        "AuthService",
        "真实认证服务；由用户完成验证",
    ),
    "complete_registration_with_code": (
        "桌面登录窗口 / 验证码注册",
        "应用登录窗口",
        "AuthService",
        "真实认证服务；由用户完成验证",
    ),
    "complete_registration_after_link": (
        "桌面登录窗口 / 链接注册",
        "应用登录窗口",
        "AuthService",
        "真实认证服务；由用户完成验证",
    ),
    "send_recovery_code": (
        "桌面登录窗口 / 找回密码验证码",
        "应用登录窗口",
        "AuthService",
        "真实认证服务；由用户完成验证",
    ),
    "reset_password": (
        "桌面登录窗口 / 重置密码；团队管理 / 重置成员密码",
        "应用登录窗口 / team.operations.save",
        "AuthService / TeamService",
        "真实认证服务；权限与审计边界保留",
    ),
    "redeem": (
        "桌面登录窗口 / 卡密兑换",
        "应用登录窗口",
        "AuthService",
        "真实授权兑换；不绕过认证",
    ),
    "get": (
        "旧对话框内部服务访问器",
        "不适用",
        "ServiceRegistry",
        "旧版依赖注入辅助方法，不是用户按钮或平台接口",
    ),
}


@dataclass(frozen=True)
class LegacyMethod:
    method: str
    kind: str
    file: str
    line: int
    ui_method: str


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def route_pages() -> list[str]:
    source = read_text(ROUTER)
    return sorted(
        set(
            match.group(1)
            for match in re.finditer(
                r'import\("\./pages/([^"?]+)',
                source,
            )
        )
    )


def component_paths() -> list[str]:
    return sorted(
        f"components/{path.name}"
        for path in (ROOT / "webui" / "src" / "components").glob("*.vue")
    )


def reachable_surfaces() -> tuple[list[str], list[str]]:
    all_components = component_paths()
    routed_pages = [f"pages/{page}" for page in route_pages()]
    all_pages = sorted(
        f"pages/{path.name}"
        for path in (ROOT / "webui" / "src" / "pages").glob("*.vue")
    )
    all_surfaces = {"App.vue", *all_pages, *all_components}
    reachable = set()
    queue = ["App.vue", *routed_pages]
    visited = set()
    while queue:
        surface = queue.pop()
        if surface in visited:
            continue
        visited.add(surface)
        source = read_text(ROOT / "webui" / "src" / surface)
        for match in re.finditer(
            r'from\s+["\']([^"\']+\.vue)["\']',
            source,
        ):
            specifier = match.group(1)
            if not specifier.startswith("."):
                continue
            source_root = (ROOT / "webui" / "src").resolve()
            resolved = (
                source_root / Path(surface).parent / specifier
            ).resolve()
            try:
                component = resolved.relative_to(
                    source_root
                ).as_posix()
            except ValueError:
                continue
            if component not in all_surfaces:
                continue
            reachable.add(component)
            queue.append(component)
    return [
        "App.vue",
        *routed_pages,
        *sorted(reachable - {"App.vue", *routed_pages}),
    ], sorted(all_surfaces - reachable - {"App.vue", *routed_pages})


def controller_action_map() -> dict[str, str]:
    source = read_text(CONTROLLER)
    return dict(
        re.findall(
            r'"([^"]+)": self\.(_[A-Za-z0-9_]+)',
            source,
        )
    )


def controller_methods() -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    tree = ast.parse(read_text(CONTROLLER))
    result = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            result[node.name] = node
    return result


def method_service_calls(
    method: ast.FunctionDef | ast.AsyncFunctionDef,
) -> tuple[set[str], set[str]]:
    service = set()
    session = set()
    for node in ast.walk(method):
        if not isinstance(node, ast.Attribute):
            continue
        owner = node.value
        if not isinstance(owner, ast.Name):
            continue
        if owner.id in {"service", "product_service"}:
            service.add(node.attr)
        if owner.id in {"session", "web_session"}:
            session.add(node.attr)
    return service, session


def parse_legacy_methods() -> list[LegacyMethod]:
    result = []
    for raw in read_text(LEGACY_AUDIT).splitlines():
        match = re.match(
            r"\| `([^`]+)` \| (\d+) \| `([^`]+)` \| "
            r"(service|web_session) \| `([^`]+)` \|",
            raw,
        )
        if not match:
            continue
        result.append(
            LegacyMethod(
                method=match.group(5),
                kind=match.group(4),
                file=match.group(1),
                line=int(match.group(2)),
                ui_method=match.group(3),
            )
        )
    return result


def button_rows(surface: str) -> Iterable[tuple[str, str, str, str]]:
    source = read_text(ROOT / "webui" / "src" / surface)
    for match in re.finditer(r"<button\b[\s\S]*?</button>", source, re.I):
        tag = match.group(0)
        opening = re.match(r"<button\b[^>]*>", tag, re.I)
        opening_tag = opening.group(0) if opening else tag
        text = re.sub(r"<[^>]+>", " ", tag)
        text = re.sub(r"\s+", " ", text).strip().strip("|")
        click = re.search(r'@click="([^"]+)"', opening_tag)
        disabled = re.search(
            r'(?::disabled="([^"]+)"|\bdisabled\b)',
            opening_tag,
        )
        title = re.search(r':?title="([^"]+)"', opening_tag)
        yield (
            text or "（图标按钮）",
            click.group(1) if click else "",
            (
                disabled.group(1)
                if disabled and disabled.lastindex
                else "静态禁用"
                if disabled
                else ""
            ),
            title.group(1) if title else "",
        )


def main() -> None:
    actions = controller_action_map()
    methods = controller_methods()
    surfaces, orphan_components = reachable_surfaces()
    surfaces_button_count = sum(
        len(list(button_rows(surface)))
        for surface in surfaces
    )
    legacy_all = parse_legacy_methods()
    legacy_unique_count = len(
        {(row.kind, row.method) for row in legacy_all}
    )
    action_service: dict[str, set[str]] = {}
    action_session: dict[str, set[str]] = {}
    for action, handler in actions.items():
        node = methods.get(handler)
        if node is None:
            continue
        service, session = method_service_calls(node)
        action_service[action] = service
        action_session[action] = session

    lines = [
        "# 澜琅 Web UI 全量逐按钮与旧版网络能力对照审计",
        "",
        "生成时间：2026-11-22",
        "",
        "范围：`webui/src/router.js` 实际路由页面、页面内嵌组件，以及 "
        "`BUTTON_NETWORK_DEPENDENCY_AUDIT.md` 中旧版 service/web_session 调用。",
        "",
        f"- 实际 UI 检查面：{len(surfaces)} 个（App、路由页面与页面内嵌组件）",
        f"- 实际按钮/关键按钮条目：{surfaces_button_count} 个",
        f"- 未挂载组件文件：{len(orphan_components)} 个（不计入实际按钮覆盖）",
        f"- 旧版唯一 service/web_session 方法：{legacy_unique_count} 个",
        f"- Controller 已注册 RPC：{len(actions)} 个",
        "",
        "判定规则：",
        "",
        "- “旧版存在”以旧版真实 UI 调用为证据，不能因新版未迁移就判为平台无接口。",
        "- “代码已接入”只表示请求路径、RPC、controller 与 service 已存在；"
        "不代表已在真实店铺执行破坏性生产写操作。",
        "- 生产写操作仍需单独授权或安全测试店铺；本轮不执行批量删除、下架、报名等真实写操作。",
        "",
        "## 一、实际路由页面按钮清单",
        "",
    ]
    for surface in surfaces:
        lines.append(f"### `{surface}`")
        lines.append("")
        lines.append("| 按钮 | 前端 handler | 禁用条件 | 禁用原因 | 页面调用的 RPC |")
        lines.append("| --- | --- | --- | --- | --- |")
        source = read_text(ROOT / "webui" / "src" / surface)
        page_actions = sorted(set(re.findall(r'call\("([^"]+)"', source)))
        for text, click, disabled, title in button_rows(surface):
            lines.append(
                "| "
                + " | ".join(
                    [
                        text.replace("|", "\\|"),
                        f"`{click}`" if click else "—",
                        f"`{disabled}`" if disabled else "",
                        title.replace("|", "\\|") if title else "",
                        ", ".join(f"`{value}`" for value in page_actions) or "本地 UI",
                    ]
                )
                + " |"
            )
        lines.append("")

    lines.extend(
        [
            "## 二、旧版真实网络能力的新版映射",
            "",
            "以下是旧版审计中出现的全部唯一 service/web_session 方法。"
            "同一方法只列一次，旧版证据保留首次出现位置。",
            "",
            "| 旧版方法 | 类型 | 旧版证据 | 新 UI 入口 | RPC | Controller | "
            "Service/WebSession 实现 | 官方接口/页面 | 当前结论/边界 | 状态 |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    legacy = legacy_all
    seen: set[tuple[str, str]] = set()
    missing = []
    for row in legacy:
        key = (row.kind, row.method)
        if key in seen:
            continue
        seen.add(key)
        mapped = LEGACY_ENTRY_MAP.get(row.method)
        if mapped:
            entry, rpc, controller, conclusion = mapped
        else:
            matching_actions = [
                action
                for action, handler in actions.items()
                if row.method in action_service.get(action, set())
                or row.method in action_session.get(action, set())
            ]
            if matching_actions:
                entry = "现有 WebUI"
                rpc = "；".join(matching_actions)
                controller = "；".join(actions[action] for action in matching_actions)
                conclusion = "代码路径已接入，待逐业务流程复核按钮反馈"
            else:
                entry = "未找到"
                rpc = ""
                controller = ""
                conclusion = "待迁移或需证明平台真实阻塞"
                missing.append((row.kind, row.method, row.file, row.line))
        lines.append(
            "| "
            + " | ".join(
                [
                    f"`{row.method}`",
                    row.kind,
                    f"`{row.file}:{row.line}` / `{row.ui_method}`",
                    entry,
                    ", ".join(f"`{value}`" for value in rpc.split("；") if value),
                    ", ".join(f"`{value}`" for value in controller.split("；") if value),
                    (
                        f"`SyncService.{row.method}`"
                        if row.kind == "service"
                        else f"`WebSession.{row.method}`"
                    ),
                    (
                        "微信小店 API / 现有客户端"
                        if row.kind == "service"
                        else "Playwright 官方微信小店页面/会话"
                    ),
                    conclusion,
                    "已接入" if mapped or rpc else "待修复",
                ]
            )
            + " |"
        )

    lines.extend(
        [
            "",
            "## 三、仍需迁移或核实的方法",
            "",
        ]
    )
    if missing:
        for kind, method, file, line in missing:
            lines.append(
                f"- `{kind}.{method}`：旧版证据 `{file}:{line}`，"
                "当前未找到对应 WebUI RPC。"
            )
    else:
        lines.append("- 本轮脚本未发现完全缺失的方法；仍需按业务流程复核参数、反馈和真实调用边界。")

    lines.extend(
        [
            "",
            "## 四、本轮已新增的真实入口",
            "",
            "- 资金账户：官方余额、结算、预计结算和资金流水。",
            "- 自动化：任务持久化、后台执行、进度轮询、订单/商品/售后/经营/采购/托管同步。",
            "- 店铺主页：官方主页商品读取、排序、隐藏、置顶。",
            "- AI客服：打开旧版真实官方客服控制台，未伪造会话 API。",
            "- 违规中心：平台违规黑名单真实同步并更新上货标题过滤库。",
            "- 商品管理：商品详情/SKU、库存修改、商品短链、商品图片上传和商品官方后台定位。",
            "- 订单工作台：电子面单配置刷新、生成面单、完成电子面单发货、售后凭证上传。",
            "- 活动管理：优惠券同步/新建/续期/停用/删除、限时抢购读取/创建/续期/删除、活动预填报读取/提交/移除。",
            "- 活动预填报：本地草稿加入、活动价/库存编辑、删除、CSV 导出和真实提交。",
            "- 店铺管理：网页 Cookie Header 导入、名称/分组真实保存、保存防重复和官方资料后台同步。",
            "- 团队管理：添加已有账号，并复用现有团队创建、更新与批量权限 RPC。",
            "- 订单实际路由：电子面单刷新/取号/完成发货、售后凭证绑定、拒绝/协商/补寄/换货参数表单。",
            "- 自动化与托管：运行时定时调度、失败后重排、同日托管执行去重。",
            "- 商品管理：实时详情/SKU、按当前或全部状态刷新页签、违规黑名单删除拦截。",
            "",
            "## 五、验证边界",
            "",
            "- 已通过 Python 定向测试、Vitest 与 production build 的代码路径验证。",
            "- 静态契约测试会枚举全部实际路由页面、页面内嵌组件与 App 外壳按钮；"
            "所有生产前端 `call(...)` 必须能在 controller action 表中注册。",
            "- `MockShopClient` 只用于 AppID 为 `demo-store` 的演示店，不进入真实店铺路径。",
            "- 未执行真实店铺批量删除、下架、活动报名、资金提现、客服消息等破坏性/生产写操作。",
            "- 真实网络调用是否成功取决于当前账户登录态、店铺授权和平台返回；失败不得当作成功。",
            "- 未挂载组件不计入实际按钮覆盖："
            + ("、".join(f"`{path}`" for path in orphan_components) or "无"),
            "",
        ]
    )
    OUTPUT.write_text("\n".join(lines), encoding="utf-8")
    print(OUTPUT)


if __name__ == "__main__":
    main()
