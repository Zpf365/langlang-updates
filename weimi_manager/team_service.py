from __future__ import annotations

from typing import Any

from .auth_client import AuthError, SupabaseAuthClient

TEAM_PERMISSIONS: tuple[tuple[str, str], ...] = (
    ("stores_view", "查看店铺"),
    ("stores_edit", "编辑店铺"),
    ("stores_login", "扫码登录"),
    ("stores_delete", "删除店铺"),
    ("stores_export", "导入导出店铺密钥"),
    ("products_view", "查看商品"),
    ("products_edit", "编辑商品"),
    ("products_upload", "上传商品"),
    ("orders_view", "查看订单"),
    ("orders_edit", "处理订单"),
    ("after_sales", "售后处理"),
    ("operations_view", "查看运营中心"),
    ("operations_task_manage", "管理行动任务"),
    ("operations_export", "导出运营数据"),
    ("operations_rule_manage", "修改巡检规则"),
    ("operations_open_backend", "从运营中心打开官方后台"),
    ("procurement", "采购中心"),
    ("activities", "活动与优惠券"),
    ("dongxiao_cart_account_manage", "动销执行账号管理"),
    ("dongxiao_cart_task_preview", "批量加购预检"),
    ("dongxiao_cart_task_create", "创建批量加购任务"),
    ("dongxiao_cart_execute", "执行批量加购任务"),
    ("dongxiao_cart_order_approve", "审批待付款草案"),
    ("dongxiao_buyer_account_manage", "管理真实买家账号"),
    ("dongxiao_buyer_flow_prepare", "准备买家完整流程"),
    ("dongxiao_buyer_flow_execute", "执行或交接买家流程"),
    ("dongxiao_buyer_order_verify", "核验真实待付款订单"),
    ("team_manage", "管理子账号"),
)
DEFAULT_SUBACCOUNT_PERMISSIONS = {
    "stores_view": True,
    "stores_login": True,
    "products_view": True,
    "products_edit": True,
    "products_upload": True,
    "orders_view": True,
}


class TeamService:
    def __init__(
        self,
        client: SupabaseAuthClient,
        access_token: str,
    ) -> None:
        self.client = client
        self.access_token = access_token

    def list_members(self) -> list[dict[str, Any]]:
        return self.client.list_team_members(self.access_token)

    def create_subaccount(
        self,
        *,
        username: str,
        password: str,
        role: str,
        permissions: dict[str, bool],
        grants: list[str],
    ) -> dict[str, Any]:
        result = self.client.team_admin(
            self.access_token,
            {
                "action": "create_subaccount",
                "username": username.strip(),
                "password": password,
                "role": role,
                "permissions": permissions,
                "grants": grants,
            },
        )
        if result.get("error"):
            raise AuthError(str(result["error"]))
        return result

    def add_existing_account(
        self,
        username: str,
        role: str = "admin",
    ) -> dict[str, Any]:
        result = self.client.team_admin(
            self.access_token,
            {
                "action": "add_existing_account",
                "username": username.strip(),
                "role": role,
            },
        )
        if result.get("error"):
            raise AuthError(str(result["error"]))
        return result

    def reset_password(self, member_id: str, password: str) -> None:
        result = self.client.team_admin(
            self.access_token,
            {
                "action": "reset_password",
                "member_id": member_id,
                "password": password,
            },
        )
        if result.get("error"):
            raise AuthError(str(result["error"]))

    def update_member(
        self,
        member_id: str,
        *,
        role: str,
        permissions: dict[str, bool],
        grants: list[str],
        is_active: bool,
    ) -> None:
        result = self.client.team_admin(
            self.access_token,
            {
                "action": "update_member",
                "member_id": member_id,
                "role": role,
                "permissions": permissions,
                "grants": grants,
                "is_active": is_active,
            },
        )
        if result.get("error"):
            raise AuthError(str(result["error"]))
