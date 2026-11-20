from __future__ import annotations

import hashlib
import os
import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from ..application.order_service import DongxiaoOrderService
from ..domain.enums import AuditResult, OrderStatus, ProductState
from ..domain.models import AuditEvent
from ..domain.queries import ProductFilter
from ..domain.rules import require_permission
from ..infrastructure.repository import DongxiaoRepository
from .adapters import (
    BuyerFlowAdapter,
    MockBuyerFlowAdapter,
    OfficialBuyerFlowAdapter,
)
from .domain import (
    BuyerAccount,
    BuyerAccountStatus,
    BuyerAdapterCapability,
    BuyerFlowItem,
    BuyerFlowMode,
    BuyerFlowRun,
    BuyerFlowStatus,
    BuyerIdentityStatus,
    BuyerItemStatus,
    BuyerOrderLink,
    BuyerOrderPreview,
    BuyerOrderStatus,
    BuyerOrderVerificationStatus,
    BuyerProvider,
    BuyerRunRevalidation,
    BuyerSelectionItem,
    BuyerSwitchOperation,
    BuyerSwitchState,
)
from .repository import BuyerFlowRepository
from .session_vault import BuyerSessionVault
from .wechat_desktop import (
    WechatDesktopClient,
    WechatDesktopProbe,
    WechatDesktopState,
)


class BuyerFlowServiceError(RuntimeError):
    pass


def _profile_text(value: object, *, field: str, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) > limit:
        raise ValueError(f"{field}不能超过 {limit} 个字符")
    if re.search(
        r"(?i)(password|passwd|cookie|authorization|access[_ -]?token|"
        r"refresh[_ -]?token|appsecret|secret\s*=|bearer\s+)",
        text,
    ):
        raise ValueError(f"{field}不能包含敏感凭据")
    return text


@dataclass(frozen=True, slots=True)
class BuyerLoginResult:
    account: BuyerAccount
    capability: BuyerAdapterCapability
    authorization_url: str = ""
    instructions: str = ""


class DongxiaoBuyerFlowService:
    TARGET_STORE_NAME = "赤影云册精选"

    def __init__(
        self,
        repository: BuyerFlowRepository,
        catalog_repository: DongxiaoRepository,
        session_vault: BuyerSessionVault,
        *,
        order_service: DongxiaoOrderService | None = None,
        official_adapter: BuyerFlowAdapter | None = None,
        mock_adapter: BuyerFlowAdapter | None = None,
        wechat_client: WechatDesktopClient | None = None,
    ) -> None:
        self.repository = repository
        self.catalog_repository = catalog_repository
        self.session_vault = session_vault
        self.order_service = order_service
        self.official_adapter = official_adapter or OfficialBuyerFlowAdapter()
        self.mock_adapter = mock_adapter or MockBuyerFlowAdapter()
        self.wechat_client = wechat_client or WechatDesktopClient(
            screenshot_dir=repository.database_path.parent
            / "diagnostics"
            / "wechat-client"
        )

    def capability(self, *, mock: bool = False) -> BuyerAdapterCapability:
        return (self.mock_adapter if mock else self.official_adapter).capability()

    def list_accounts(self, *, permission_checker) -> list[BuyerAccount]:
        require_permission(
            permission_checker,
            "dongxiao_buyer_account_manage",
        )
        return self.repository.list_accounts()

    def current_account(self) -> BuyerAccount | None:
        return self.repository.current_account()

    def create_official_account(
        self,
        *,
        display_name: str,
        wechat_nickname: str = "",
        identity_label: str = "",
        remark: str = "",
        permission_checker,
        actor_id: str,
    ) -> BuyerLoginResult:
        require_permission(
            permission_checker,
            "dongxiao_buyer_account_manage",
        )
        capability = self.official_adapter.capability()
        display_name = _profile_text(
            display_name,
            field="买家显示名",
            limit=100,
        )
        if not display_name:
            raise ValueError("买家显示名不能为空")
        wechat_nickname = _profile_text(
            wechat_nickname,
            field="微信昵称",
            limit=100,
        )
        identity_label = _profile_text(
            identity_label,
            field="身份标识",
            limit=200,
        )
        remark = _profile_text(
            remark,
            field="账号备注",
            limit=500,
        )
        now = datetime.now(UTC)
        account_id = uuid.uuid4().hex
        reference = hashlib.sha256(
            f"official-unverified\0{account_id}\0{display_name}".encode("utf-8")
        ).hexdigest()
        account = BuyerAccount(
            buyer_account_id=account_id,
            display_name=str(display_name).strip(),
            account_ref_hash=reference,
            provider=BuyerProvider.MANUAL_HANDOFF,
            identity_status=BuyerIdentityStatus.USER_ASSERTED,
            status=(
                BuyerAccountStatus.PENDING_AUTHORIZATION
                if capability.state.value == "awaiting_authorization"
                else BuyerAccountStatus.DISCONNECTED
            ),
            authorization_at=None,
            last_verified_at=None,
            created_at=now,
            updated_at=now,
            status_reason=(
                capability.reason
                + " 当前账号只能用于用户本人操作的官方客户端人工交接，"
                "身份未经官方接口验证。"
            ),
            wechat_nickname=wechat_nickname,
            identity_label=identity_label,
            remark=remark,
        )
        saved, _created = self.repository.create_account(account)
        authorization = self.official_adapter.begin_authorization()
        self._audit(
            actor_id=actor_id,
            action="buyer_account_created",
            entity_type="buyer_account",
            entity_id=saved.buyer_account_id,
            result="success",
            detail={
                "provider": saved.provider.value,
                "identity_status": saved.identity_status.value,
                "official_capability": capability.state.value,
            },
        )
        return BuyerLoginResult(
            account=saved,
            capability=capability,
            authorization_url=authorization.official_url if authorization else "",
            instructions=(
                authorization.instructions
                if authorization
                else capability.reason
            ),
        )

    def create_mock_account(
        self,
        *,
        display_name: str,
        permission_checker,
        actor_id: str,
    ) -> BuyerAccount:
        require_permission(
            permission_checker,
            "dongxiao_buyer_account_manage",
        )
        proof = self.mock_adapter.verify_identity(
            authorization_id="mock-ui",
            user_confirmed_official_client=False,
        )
        now = datetime.now(UTC)
        account_id = uuid.uuid4().hex
        account = BuyerAccount(
            buyer_account_id=account_id,
            display_name=str(display_name).strip() or proof.display_name,
            account_ref_hash=hashlib.sha256(
                f"mock\0{account_id}\0{proof.official_account_id}".encode("utf-8")
            ).hexdigest(),
            provider=BuyerProvider.MOCK,
            identity_status=BuyerIdentityStatus.VERIFIED_MOCK,
            status=BuyerAccountStatus.MOCK_READY,
            authorization_at=now,
            last_verified_at=now,
            created_at=now,
            updated_at=now,
            status_reason="Mock 测试身份，不代表真实微信账号。",
        )
        saved, _created = self.repository.create_account(account)
        if self.repository.current_account() is None:
            saved = self.repository.set_current_account(saved.buyer_account_id)
        self._audit(
            actor_id=actor_id,
            action="buyer_mock_account_created",
            entity_type="buyer_account",
            entity_id=saved.buyer_account_id,
            result="info",
            detail={"source": "mock"},
        )
        return saved

    def register_current_official_account(
        self,
        *,
        display_name: str,
        wechat_nickname: str,
        identity_label: str,
        remark: str = "",
        permission_checker,
        actor_id: str,
    ) -> tuple[BuyerAccount, bool]:
        require_permission(
            permission_checker,
            "dongxiao_buyer_account_manage",
        )
        clean_display = _profile_text(
            display_name,
            field="买家显示名",
            limit=100,
        )
        nickname = _profile_text(
            wechat_nickname,
            field="微信昵称",
            limit=100,
        )
        label = _profile_text(
            identity_label,
            field="身份标识",
            limit=200,
        )
        clean_remark = _profile_text(
            remark,
            field="账号备注",
            limit=500,
        )
        if not clean_display or not nickname or not label:
            raise ValueError("显示名、微信昵称和身份标识必须填写")
        probe = self._require_running_client()
        session = self._client_session(probe)
        existing = next(
            (
                account
                for account in self.repository.list_accounts()
                if account.confirmed_client_session == session
            ),
            None,
        )
        if existing is not None:
            account = self.repository.update_account(
                existing.buyer_account_id,
                display_name=clean_display,
                wechat_nickname=nickname,
                identity_label=label,
                remark=clean_remark,
                switch_state=BuyerSwitchState.CONFIRMED,
                confirmed_client_session=session,
                last_client_check_at=probe.checked_at,
                identity_status=BuyerIdentityStatus.USER_ASSERTED,
                status=BuyerAccountStatus.ACTIVE,
                last_verified_at=datetime.now(UTC),
                status_reason=(
                    "当前微信会话已关联已有买家账号；"
                    "身份来源为用户人工确认。"
                ),
            )
            created = False
        else:
            login_result = self.create_official_account(
                display_name=clean_display,
                wechat_nickname=nickname,
                identity_label=label,
                remark=clean_remark,
                permission_checker=permission_checker,
                actor_id=actor_id,
            )
            account = login_result.account
            account = self.confirm_wechat_identity(
                account.buyer_account_id,
                wechat_nickname=nickname,
                identity_label=label,
                permission_checker=permission_checker,
                actor_id=actor_id,
            )
            created = True
        current = self.repository.current_account()
        if current is None:
            account = self.repository.set_current_account(
                account.buyer_account_id,
                actor_id=actor_id,
                reason="添加微信账号向导设为当前买家",
            )
        elif current.buyer_account_id != account.buyer_account_id:
            self.begin_account_switch(
                account.buyer_account_id,
                permission_checker=permission_checker,
                actor_id=actor_id,
            )
            account = self.complete_account_switch(
                account.buyer_account_id,
                wechat_nickname=nickname,
                identity_label=label,
                permission_checker=permission_checker,
                actor_id=actor_id,
            )
        self._audit(
            actor_id=actor_id,
            action="buyer_wizard_completed",
            entity_type="buyer_account",
            entity_id=account.buyer_account_id,
            result="success",
            detail={
                "created": created,
                "client_session": session,
                "identity_source": "user_confirmed_windows_wechat",
            },
        )
        return account, created

    def set_current_account(
        self,
        buyer_account_id: str,
        *,
        permission_checker,
        actor_id: str,
    ) -> BuyerAccount:
        require_permission(
            permission_checker,
            "dongxiao_buyer_account_manage",
        )
        account = self.repository.get_account(buyer_account_id)
        if account is None:
            raise BuyerFlowServiceError("买家账号不存在")
        if not (
            account.verified_identity
            or (
                account.provider is BuyerProvider.MANUAL_HANDOFF
                and account.identity_status is BuyerIdentityStatus.USER_ASSERTED
            )
        ):
            raise BuyerFlowServiceError(
                "买家身份未通过官方验证，不能设为当前账号。"
                "不能仅修改显示名称来确认身份。"
            )
        saved = self.repository.set_current_account(
            buyer_account_id,
            actor_id=actor_id,
            reason="用户主动切换当前买家账号",
        )
        self._audit(
            actor_id=actor_id,
            action="buyer_current_account_changed",
            entity_type="buyer_account",
            entity_id=saved.buyer_account_id,
            result="success",
            detail={"provider": saved.provider.value},
        )
        return saved

    def update_account_profile(
        self,
        buyer_account_id: str,
        *,
        display_name: str | None = None,
        wechat_nickname: str | None = None,
        identity_label: str | None = None,
        remark: str | None = None,
        permission_checker,
        actor_id: str,
    ) -> BuyerAccount:
        require_permission(
            permission_checker,
            "dongxiao_buyer_account_manage",
        )
        account = self.repository.get_account(buyer_account_id)
        if account is None:
            raise BuyerFlowServiceError("买家账号不存在")
        clean_display = (
            _profile_text(
                display_name,
                field="买家显示名",
                limit=100,
            )
            if display_name is not None
            else None
        )
        if clean_display == "":
            raise ValueError("买家显示名不能为空")
        saved = self.repository.update_account(
            buyer_account_id,
            display_name=clean_display,
            wechat_nickname=(
                _profile_text(
                    wechat_nickname,
                    field="微信昵称",
                    limit=100,
                )
                if wechat_nickname is not None
                else None
            ),
            identity_label=(
                _profile_text(
                    identity_label,
                    field="身份标识",
                    limit=200,
                )
                if identity_label is not None
                else None
            ),
            remark=(
                _profile_text(
                    remark,
                    field="账号备注",
                    limit=500,
                )
                if remark is not None
                else None
            ),
        )
        self._audit(
            actor_id=actor_id,
            action="buyer_account_profile_updated",
            entity_type="buyer_account",
            entity_id=saved.buyer_account_id,
            result="success",
            detail={
                "display_name": saved.display_name,
                "wechat_nickname": saved.wechat_nickname,
                "identity_label": saved.identity_label,
                "remark": saved.remark,
            },
        )
        return saved

    def list_account_switch_events(
        self,
        *,
        permission_checker,
        limit: int = 200,
    ):
        require_permission(
            permission_checker,
            "dongxiao_buyer_account_manage",
        )
        return self.repository.list_account_switch_events(limit=limit)

    def inspect_wechat_client(
        self,
        *,
        buyer_account_id: str = "",
        permission_checker,
        actor_id: str,
    ) -> WechatDesktopProbe:
        require_permission(
            permission_checker,
            "dongxiao_buyer_account_manage",
        )
        probe = self.wechat_client.probe()
        current = self.repository.current_account()
        if current is not None:
            session_changed = (
                bool(current.confirmed_client_session)
                and current.confirmed_client_session != probe.session_id
            )
            self.repository.update_account(
                current.buyer_account_id,
                switch_state=(
                    BuyerSwitchState.LOGIN_INVALID
                    if session_changed
                    else current.switch_state
                ),
                confirmed_client_session=(
                    "" if session_changed else current.confirmed_client_session
                ),
                last_client_check_at=probe.checked_at,
                status=(
                    BuyerAccountStatus.DISCONNECTED
                    if session_changed
                    else current.status
                ),
                status_reason=(
                    "微信客户端会话已变化，旧身份确认失效"
                    if session_changed
                    else current.status_reason
                ),
            )
        self.repository.record_client_event(
            buyer_account_id=buyer_account_id,
            client_state=probe.state.value,
            window_title=probe.window_title,
            process_id=probe.process_id or None,
            detail={
                "installed": probe.installed,
                "running": probe.running,
                "identity_confirmed": probe.identity_confirmed,
                "visible_identity": probe.visible_identity,
                "instructions": probe.instructions,
            },
            screenshot_path=probe.screenshot_path,
        )
        self._audit(
            actor_id=actor_id,
            action="buyer_wechat_client_checked",
            entity_type="buyer_account",
            entity_id=buyer_account_id or "unbound",
            result=(
                "success"
                if probe.state
                in {
                    WechatDesktopState.LOGGED_IN,
                    WechatDesktopState.IDENTITY_UNCERTAIN,
                }
                else "failed"
            ),
            detail={
                "client_state": probe.state.value,
                "running": probe.running,
                "identity_confirmed": probe.identity_confirmed,
            },
        )
        return probe

    @staticmethod
    def _client_session(probe: WechatDesktopProbe) -> str:
        return probe.session_id or (
            f"{probe.executable_name.casefold()}:"
            f"{probe.process_id}:{probe.hwnd}"
        )

    def _require_running_client(self) -> WechatDesktopProbe:
        probe = self.wechat_client.probe()
        if not probe.installed:
            raise BuyerFlowServiceError("未检测到 Windows 官方微信客户端")
        if not probe.running:
            raise BuyerFlowServiceError("Windows 官方微信未运行")
        if probe.state is WechatDesktopState.LOGIN_REQUIRED:
            raise BuyerFlowServiceError("Windows 官方微信尚未登录")
        if probe.state in {
            WechatDesktopState.ERROR,
            WechatDesktopState.UNSUPPORTED_VERSION,
        }:
            raise BuyerFlowServiceError(
                f"Windows 官方微信状态异常：{probe.error or probe.instructions}"
            )
        if not self._client_session(probe):
            raise BuyerFlowServiceError("无法取得当前微信运行会话")
        return probe

    def begin_account_switch(
        self,
        target_buyer_account_id: str,
        *,
        permission_checker,
        actor_id: str,
    ) -> BuyerSwitchOperation:
        require_permission(
            permission_checker,
            "dongxiao_buyer_account_manage",
        )
        target = self.repository.get_account(target_buyer_account_id)
        if target is None:
            raise BuyerFlowServiceError("目标买家账号不存在")
        probe = self._require_running_client()
        session = self._client_session(probe)
        source, target = self.repository.begin_account_switch(
            target_buyer_account_id,
            client_session=session,
            actor_id=actor_id,
        )
        self.repository.record_client_event(
            buyer_account_id=source.buyer_account_id,
            client_state="switch_started",
            window_title=probe.window_title,
            process_id=probe.process_id or None,
            detail={
                "target_buyer_account_id": target.buyer_account_id,
                "client_session": session,
            },
        )
        self._audit(
            actor_id=actor_id,
            action="buyer_account_switch_started",
            entity_type="buyer_account",
            entity_id=target.buyer_account_id,
            result="info",
            detail={
                "source_buyer_account_id": source.buyer_account_id,
                "client_session": session,
            },
        )
        return BuyerSwitchOperation(
            source_account_id=source.buyer_account_id,
            target_account_id=target.buyer_account_id,
            status=target.switch_state,
            client_session=session,
            instructions=(
                self.wechat_client.switch_account_instructions()
                + " 切换完成后返回澜琅确认目标身份。"
            ),
        )

    def complete_account_switch(
        self,
        target_buyer_account_id: str,
        *,
        wechat_nickname: str,
        identity_label: str,
        permission_checker,
        actor_id: str,
    ) -> BuyerAccount:
        require_permission(
            permission_checker,
            "dongxiao_buyer_account_manage",
        )
        nickname = _profile_text(
            wechat_nickname,
            field="微信昵称",
            limit=100,
        )
        label = _profile_text(
            identity_label,
            field="身份标识",
            limit=200,
        )
        if not nickname or not label:
            raise ValueError("微信昵称和身份标识必须由用户本人确认")
        probe = self._require_running_client()
        session = self._client_session(probe)
        saved = self.repository.complete_account_switch(
            target_buyer_account_id,
            client_session=session,
            actor_id=actor_id,
            wechat_nickname=nickname,
            identity_label=label,
        )
        self.repository.record_client_event(
            buyer_account_id=saved.buyer_account_id,
            client_state="identity_confirmed",
            window_title=probe.window_title,
            process_id=probe.process_id or None,
            detail={
                "wechat_nickname": nickname,
                "identity_label": label,
                "source": "user_confirmed_official_switch",
                "client_session": session,
            },
        )
        self._audit(
            actor_id=actor_id,
            action="buyer_account_switch_completed",
            entity_type="buyer_account",
            entity_id=saved.buyer_account_id,
            result="success",
            detail={
                "wechat_nickname": nickname,
                "identity_label": label,
                "client_session": session,
            },
        )
        return saved

    def fail_account_switch(
        self,
        target_buyer_account_id: str,
        *,
        reason: str,
        permission_checker,
        actor_id: str,
    ) -> BuyerAccount:
        require_permission(
            permission_checker,
            "dongxiao_buyer_account_manage",
        )
        target = self.repository.get_account(target_buyer_account_id)
        if target is None:
            raise BuyerFlowServiceError("目标买家账号不存在")
        saved = self.repository.update_account(
            target_buyer_account_id,
            switch_state=BuyerSwitchState.FAILED,
            confirmed_client_session="",
            status=BuyerAccountStatus.DISCONNECTED,
            status_reason=f"账号切换失败：{reason}"[:2000],
        )
        self._audit(
            actor_id=actor_id,
            action="buyer_account_switch_failed",
            entity_type="buyer_account",
            entity_id=target_buyer_account_id,
            result="failed",
            detail={"reason": str(reason)[:500]},
        )
        return saved

    def confirm_wechat_identity(
        self,
        buyer_account_id: str,
        *,
        wechat_nickname: str,
        identity_label: str,
        permission_checker,
        actor_id: str,
    ) -> BuyerAccount:
        require_permission(
            permission_checker,
            "dongxiao_buyer_account_manage",
        )
        account = self.repository.get_account(buyer_account_id)
        if account is None:
            raise BuyerFlowServiceError("买家账号不存在")
        nickname = _profile_text(
            wechat_nickname,
            field="微信昵称",
            limit=100,
        )
        label = _profile_text(
            identity_label,
            field="身份标识",
            limit=200,
        )
        if not nickname or not label:
            raise ValueError("微信昵称和身份标识必须由用户本人确认")
        probe = self._require_running_client()
        session = self._client_session(probe)
        now = datetime.now(UTC)
        saved = self.repository.update_account(
            buyer_account_id,
            wechat_nickname=nickname,
            identity_label=label,
            identity_status=BuyerIdentityStatus.USER_ASSERTED,
            status=BuyerAccountStatus.ACTIVE,
            last_verified_at=now,
            switch_state=BuyerSwitchState.CONFIRMED,
            confirmed_client_session=session,
            last_client_check_at=probe.checked_at,
            status_reason=(
                "用户本人在 Windows 官方微信客户端确认当前可见身份；"
                "该确认不等同于官方买家接口身份验证。"
            ),
        )
        self.repository.record_client_event(
            buyer_account_id=buyer_account_id,
            client_state="identity_confirmed",
            detail={
                "wechat_nickname": nickname,
                "identity_label": label,
                "source": "user_confirmed_windows_wechat",
                "client_session": session,
            },
        )
        self._audit(
            actor_id=actor_id,
            action="buyer_wechat_identity_confirmed",
            entity_type="buyer_account",
            entity_id=buyer_account_id,
            result="success",
            detail={
                "wechat_nickname": nickname,
                "identity_label": label,
            },
        )
        return saved

    def wechat_switch_instructions(self) -> str:
        return self.wechat_client.switch_account_instructions()

    def check_account(
        self,
        buyer_account_id: str,
        *,
        permission_checker,
        actor_id: str,
    ) -> BuyerAccount:
        require_permission(
            permission_checker,
            "dongxiao_buyer_account_manage",
        )
        account = self.repository.get_account(buyer_account_id)
        if account is None:
            raise BuyerFlowServiceError("买家账号不存在")
        now = datetime.now(UTC)
        if account.provider is BuyerProvider.MOCK:
            return self.repository.update_account(
                buyer_account_id,
                status=BuyerAccountStatus.MOCK_READY,
                last_verified_at=now,
                status_reason="Mock 测试身份可用。",
            )
        capability = self.official_adapter.capability()
        status = (
            BuyerAccountStatus.DISCONNECTED
            if account.provider is BuyerProvider.MANUAL_HANDOFF
            else (
                BuyerAccountStatus.PENDING_AUTHORIZATION
                if capability.official_login
                else BuyerAccountStatus.OFFICIAL_UNSUPPORTED
            )
        )
        saved = self.repository.update_account(
            buyer_account_id,
            status=status,
            status_reason=capability.reason,
            last_verified_at=(
                now if account.identity_status is BuyerIdentityStatus.VERIFIED_OFFICIAL else None
            ),
        )
        self._audit(
            actor_id=actor_id,
            action="buyer_account_checked",
            entity_type="buyer_account",
            entity_id=saved.buyer_account_id,
            result="failed" if status is BuyerAccountStatus.OFFICIAL_UNSUPPORTED else "success",
            detail={"capability": capability.state.value},
        )
        return saved

    def delete_account(
        self,
        buyer_account_id: str,
        *,
        permission_checker,
        actor_id: str,
    ) -> None:
        require_permission(
            permission_checker,
            "dongxiao_buyer_account_manage",
        )
        self.repository.delete_account(buyer_account_id)
        self.session_vault.remove(buyer_account_id)
        self._audit(
            actor_id=actor_id,
            action="buyer_account_deleted",
            entity_type="buyer_account",
            entity_id=buyer_account_id,
            result="success",
            detail={},
        )

    def find_target_store(self, stores: list[object], selected_id: int | None = None):
        if selected_id is not None:
            selected = next(
                (store for store in stores if int(getattr(store, "id", 0)) == selected_id),
                None,
            )
            if selected is not None:
                return selected
        return next(
            (
                store
                for store in stores
                if str(getattr(store, "name", "")).strip()
                == self.TARGET_STORE_NAME
            ),
            None,
        )

    def prepare_run(
        self,
        store,
        *,
        buyer_account_id: str,
        mode: BuyerFlowMode | str,
        requested_count: int,
        manual_product_ids: list[str],
        quantities: dict[str, int] | None,
        idempotency_key: str,
        permission_checker,
        actor_id: str,
    ) -> tuple[BuyerFlowRun, list[BuyerFlowItem]]:
        require_permission(
            permission_checker,
            "dongxiao_buyer_flow_prepare",
        )
        account = self.repository.get_account(buyer_account_id)
        if account is None:
            raise BuyerFlowServiceError("当前买家账号不存在")
        manual_handoff_account = (
            account.provider is BuyerProvider.MANUAL_HANDOFF
            and account.identity_status is BuyerIdentityStatus.USER_ASSERTED
        )
        if not account.verified_identity and not manual_handoff_account:
            raise BuyerFlowServiceError("当前买家身份未通过可验证授权")
        if manual_handoff_account:
            probe = self._require_running_client()
            session = self._client_session(probe)
            if (
                account.switch_state is not BuyerSwitchState.CONFIRMED
                or account.confirmed_client_session != session
            ):
                raise BuyerFlowServiceError(
                    "当前买家身份尚未人工确认，或微信客户端会话已变化；"
                    "请重新检查微信并人工确认身份"
                )
        if not 1 <= int(requested_count) <= 10:
            raise ValueError("商品数量必须在 1 到 10 之间")
        normalized_mode = BuyerFlowMode(mode)
        selection = self._select_products(
            store,
            mode=normalized_mode,
            requested_count=int(requested_count),
            product_ids=manual_product_ids,
            quantities=quantities or {},
        )
        if not selection:
            raise ValueError("没有满足上架、库存和 SKU 条件的真实商品")
        now = datetime.now(UTC)
        run_id = uuid.uuid4().hex
        items = [
            BuyerFlowItem(
                item_id=uuid.uuid4().hex,
                run_id=run_id,
                product_id=item.product_id,
                title=item.title,
                sku_id=item.sku_id,
                specs=item.specs,
                unit_price_cents=item.unit_price_cents,
                quantity=item.quantity,
                stock=item.stock,
                status=BuyerItemStatus.SELECTED,
            )
            for item in selection
        ]
        capability = self.capability(mock=account.provider is BuyerProvider.MOCK)
        if account.provider is BuyerProvider.MOCK:
            status = BuyerFlowStatus.PREPARED
            reason = "Mock 流程已准备；不代表真实购物车或订单。"
        elif manual_handoff_account:
            status = BuyerFlowStatus.WAITING_USER_HANDOFF
            reason = (
                "已生成真实商品预选清单。请在官方微信客户端由用户本人"
                "确认买家身份、SKU、价格、数量、地址并提交订单；"
                "澜琅不会自动操作或付款。"
            )
        elif capability.official_cart_write and capability.official_cart_read:
            status = BuyerFlowStatus.PREPARED
            reason = "官方买家购买流程已准备"
        else:
            status = BuyerFlowStatus.BLOCKED_OFFICIAL_UNSUPPORTED
            reason = capability.reason
        run = BuyerFlowRun(
            run_id=run_id,
            store_id=int(store.id),
            store_app_id=str(store.app_id),
            store_name=str(store.name),
            buyer_account_id=account.buyer_account_id,
            mode=normalized_mode,
            status=status,
            idempotency_key=str(idempotency_key),
            requested_count=int(requested_count),
            item_count=len(items),
            merchandise_total_cents=sum(item.subtotal_cents for item in items),
            created_at=now,
            updated_at=now,
            expires_at=now + timedelta(minutes=30),
            handoff_token=(
                secrets.token_urlsafe(18)
                if status is BuyerFlowStatus.BLOCKED_OFFICIAL_UNSUPPORTED
                else ""
            ),
            status_reason=reason,
            capability={
                "state": capability.state.value,
                "official_login": capability.official_login,
                "official_cart_write": capability.official_cart_write,
                "official_cart_read": capability.official_cart_read,
                "official_order_create": capability.official_order_create,
                "manual_handoff": capability.manual_handoff,
            },
        )
        saved, _created = self.repository.create_run(run, items)
        self._audit(
            store_id=int(store.id),
            store_app_id=str(store.app_id),
            actor_id=actor_id,
            action="buyer_flow_prepared",
            entity_type="buyer_flow_run",
            entity_id=saved.run_id,
            result=(
                "success"
                if saved.status is BuyerFlowStatus.PREPARED
                else "failed"
            ),
            detail={
                "buyer_account_id": account.buyer_account_id,
                "item_count": saved.item_count,
                "capability": capability.state.value,
            },
        )
        return saved, self.repository.list_items(run_id)

    def execute_mock_flow(
        self,
        run_id: str,
        *,
        permission_checker,
        actor_id: str,
    ) -> BuyerFlowRun:
        require_permission(
            permission_checker,
            "dongxiao_buyer_flow_execute",
        )
        run = self.repository.get_run(run_id)
        if run is None:
            raise BuyerFlowServiceError("买家流程不存在")
        account = self.repository.get_account(run.buyer_account_id)
        if account is None or account.provider is not BuyerProvider.MOCK:
            raise BuyerFlowServiceError("只有明确标记的 Mock 账号可执行模拟流程")
        for item in self.repository.list_items(run_id):
            proof = self.mock_adapter.add_to_cart(
                buyer_account_id=account.buyer_account_id,
                product_id=item.product_id,
                sku_id=item.sku_id,
                quantity=item.quantity,
                unit_price_cents=item.unit_price_cents,
                idempotency_key=f"mock-cart:{run.run_id}:{item.item_id}",
            )
            observed = self.mock_adapter.read_cart(
                buyer_account_id=account.buyer_account_id,
                product_id=item.product_id,
                sku_id=item.sku_id,
            )
            if (
                observed is None
                or observed.quantity < item.quantity
                or observed.unit_price_cents != item.unit_price_cents
                or not proof.server_state_token
            ):
                raise BuyerFlowServiceError("Mock 购物车服务端状态核验失败")
        order = self.mock_adapter.create_order(
            buyer_account_id=account.buyer_account_id,
            idempotency_key=f"mock-order:{run.run_id}",
        )
        now = datetime.now(UTC)
        link = BuyerOrderLink(
            link_id=uuid.uuid4().hex,
            run_id=run.run_id,
            store_id=run.store_id,
            store_app_id=run.store_app_id,
            buyer_account_id=account.buyer_account_id,
            platform_order_id=order.order_id,
            status=BuyerOrderVerificationStatus.PENDING_PAYMENT,
            total_amount_cents=order.total_amount_cents,
            item_count=order.item_count,
            created_at=now,
            verified_at=now,
            detail={"source": "mock", "status_token": order.status_token},
            order_status=BuyerOrderStatus.PENDING_PAYMENT,
        )
        self.repository.record_order_link(link)
        saved = self.repository.update_run(
            run.run_id,
            status=BuyerFlowStatus.ORDER_VERIFIED,
            order_id=order.order_id,
            verification_status=BuyerOrderVerificationStatus.PENDING_PAYMENT,
            status_reason="Mock 流程验证通过；不是真实订单。",
        )
        self._audit(
            store_id=run.store_id,
            store_app_id=run.store_app_id,
            actor_id=actor_id,
            action="buyer_mock_flow_executed",
            entity_type="buyer_flow_run",
            entity_id=run.run_id,
            result="info",
            detail={"mock_order_id": order.order_id},
        )
        return saved

    def start_manual_handoff(
        self,
        run_id: str,
        *,
        permission_checker,
        actor_id: str,
    ) -> BuyerFlowRun:
        require_permission(
            permission_checker,
            "dongxiao_buyer_flow_execute",
        )
        run = self.repository.get_run(run_id)
        if run is None:
            raise BuyerFlowServiceError("买家流程不存在")
        revalidation = self.revalidate_run(
            run.run_id,
            store=SimpleNamespace(
                id=run.store_id,
                app_id=run.store_app_id,
                name=run.store_name,
            ),
            permission_checker=permission_checker,
            actor_id=actor_id,
        )
        if revalidation.changed:
            raise BuyerFlowServiceError(
                "商品、SKU、价格或库存已变化，任务已暂停："
                + "；".join(revalidation.reasons)
            )
        probe = self.wechat_client.probe()
        account = self.repository.get_account(run.buyer_account_id)
        if account is None:
            raise BuyerFlowServiceError("买家账号不存在")
        if (
            account.provider is BuyerProvider.MANUAL_HANDOFF
            and (
                account.switch_state is not BuyerSwitchState.CONFIRMED
                or account.confirmed_client_session
                != self._client_session(probe)
            )
        ):
            raise BuyerFlowServiceError(
                "微信客户端会话或当前身份已变化，必须重新人工确认"
            )
        self.repository.record_client_event(
            buyer_account_id=run.buyer_account_id,
            client_state=probe.state.value,
            window_title=probe.window_title,
            process_id=probe.process_id or None,
            detail={
                "action": "manual_handoff",
                "installed": probe.installed,
                "running": probe.running,
                "identity_confirmed": probe.identity_confirmed,
                "instructions": probe.instructions,
            },
            screenshot_path=probe.screenshot_path,
        )
        if not probe.installed:
            raise BuyerFlowServiceError(
                "未检测到 Windows 官方微信客户端，无法开始客户端交接。"
            )
        if not probe.running:
            raise BuyerFlowServiceError(
                f"Windows 官方微信未运行；{probe.instructions}"
            )
        if probe.state is WechatDesktopState.LOGIN_REQUIRED:
            raise BuyerFlowServiceError(
                f"Windows 官方微信尚未登录；{probe.instructions}"
            )
        if probe.state in {
            WechatDesktopState.ERROR,
            WechatDesktopState.UNSUPPORTED_VERSION,
        }:
            raise BuyerFlowServiceError(
                f"Windows 官方微信状态异常：{probe.error or probe.instructions}"
            )
        capability = self.capability(mock=False)
        if not capability.manual_handoff:
            raise BuyerFlowServiceError("当前没有可用的人工交接路径")
        saved = self.repository.update_run(
            run_id,
            status=BuyerFlowStatus.WAITING_USER_HANDOFF,
            handoff_token=run.handoff_token or secrets.token_urlsafe(18),
            status_reason=(
                "请在本机官方微信/手机微信中人工完成商品核对、加购、"
                "地址和订单提交。澜琅不会自动点击或绕过验证。"
            ),
        )
        self._audit(
            store_id=run.store_id,
            store_app_id=run.store_app_id,
            actor_id=actor_id,
            action="buyer_manual_handoff_started",
            entity_type="buyer_flow_run",
            entity_id=run.run_id,
            result="info",
            detail={},
        )
        self.repository.record_task_event(
            run_id=run.run_id,
            buyer_account_id=run.buyer_account_id,
            event_type="manual_handoff_started",
            status=saved.status.value,
            detail={"client_state": probe.state.value},
        )
        return saved

    def revalidate_run(
        self,
        run_id: str,
        *,
        store,
        permission_checker,
        actor_id: str,
    ) -> BuyerRunRevalidation:
        require_permission(
            permission_checker,
            "dongxiao_buyer_flow_execute",
        )
        run = self.repository.get_run(run_id)
        if run is None:
            raise BuyerFlowServiceError("买家流程不存在")
        if (
            run.store_id != int(store.id)
            or run.store_app_id != str(store.app_id)
        ):
            raise BuyerFlowServiceError("买家流程不属于当前店铺")
        items = self.repository.list_items(run_id)
        snapshots = self.catalog_repository.catalog_snapshot_for_products(
            int(store.id),
            [item.product_id for item in items],
        )
        reasons: list[str] = []
        for item in items:
            snapshot = snapshots.get(item.product_id)
            if snapshot is None:
                reasons.append(f"商品 {item.product_id} 已失效或不在当前店铺")
                continue
            product, skus = snapshot
            if product.state is not ProductState.ACTIVE:
                reasons.append(f"商品 {item.product_id} 当前不是上架状态")
            if product.title != item.title:
                reasons.append(f"商品 {item.product_id} 标题已变化")
            sku = next(
                (candidate for candidate in skus if candidate.sku_id == item.sku_id),
                None,
            )
            if sku is None:
                reasons.append(
                    f"商品 {item.product_id} 的 SKU {item.sku_id} 已失效"
                )
                continue
            if not sku.available:
                reasons.append(f"SKU {item.sku_id} 当前不可售")
            if sku.price_cents != item.unit_price_cents:
                reasons.append(
                    f"SKU {item.sku_id} 价格由 "
                    f"{item.unit_price_cents} 分变为 {sku.price_cents} 分"
                )
            if sku.stock < item.quantity:
                reasons.append(
                    f"SKU {item.sku_id} 库存不足："
                    f"需要 {item.quantity}，当前 {sku.stock}"
                )
        checked_at = datetime.now(UTC)
        changed = bool(reasons)
        if changed:
            self.repository.update_run(
                run_id,
                status=BuyerFlowStatus.PAUSED,
                status_reason=(
                    "商品状态、SKU、价格或库存发生变化，"
                    "必须用户重新确认后继续："
                    + "；".join(reasons)
                )[:2000],
            )
            self.repository.record_task_event(
                run_id=run.run_id,
                buyer_account_id=run.buyer_account_id,
                event_type="revalidation_changed",
                status=BuyerFlowStatus.PAUSED.value,
                detail={"reasons": reasons},
            )
            self._audit(
                store_id=run.store_id,
                store_app_id=run.store_app_id,
                actor_id=actor_id,
                action="buyer_flow_revalidation_changed",
                entity_type="buyer_flow_run",
                entity_id=run.run_id,
                result="failed",
                detail={"reasons": reasons},
            )
        return BuyerRunRevalidation(
            run_id=run.run_id,
            changed=changed,
            reasons=tuple(reasons),
            checked_at=checked_at,
        )

    def mark_handoff_complete(
        self,
        run_id: str,
        *,
        permission_checker,
        actor_id: str,
    ) -> BuyerFlowRun:
        require_permission(
            permission_checker,
            "dongxiao_buyer_flow_execute",
        )
        run = self.repository.get_run(run_id)
        if run is None:
            raise BuyerFlowServiceError("买家流程不存在")
        if run.status not in {
            BuyerFlowStatus.WAITING_USER_HANDOFF,
            BuyerFlowStatus.PAUSED,
        }:
            raise BuyerFlowServiceError("当前流程不在人工交接阶段")
        saved = self.repository.update_run(
            run_id,
            status=BuyerFlowStatus.WAITING_ORDER_ID,
            status_reason="用户已完成官方客户端操作，等待输入并核验真实订单号。",
        )
        self._audit(
            store_id=run.store_id,
            store_app_id=run.store_app_id,
            actor_id=actor_id,
            action="buyer_handoff_user_confirmed",
            entity_type="buyer_flow_run",
            entity_id=run.run_id,
            result="success",
            detail={},
        )
        self.repository.record_task_event(
            run_id=run.run_id,
            buyer_account_id=run.buyer_account_id,
            event_type="handoff_user_confirmed",
            status=saved.status.value,
            detail={},
        )
        return saved

    def verify_merchant_order(
        self,
        run_id: str,
        *,
        order_id: str,
        store,
        permission_checker,
        actor_id: str,
    ) -> BuyerOrderLink:
        require_permission(
            permission_checker,
            "dongxiao_buyer_order_verify",
        )
        run = self.repository.get_run(run_id)
        if run is None:
            raise BuyerFlowServiceError("买家流程不存在")
        buyer_account = self.repository.get_account(run.buyer_account_id)
        if (
            buyer_account is None
            or buyer_account.provider is BuyerProvider.MOCK
        ):
            raise BuyerFlowServiceError(
                "Mock 买家流程不能与真实商家订单核验混合"
            )
        if run.store_id != int(store.id) or run.store_app_id != str(store.app_id):
            raise BuyerFlowServiceError("买家流程不属于当前店铺")
        if run.status is not BuyerFlowStatus.WAITING_ORDER_ID:
            raise BuyerFlowServiceError("请先完成官方客户端操作并确认交接")
        if self.order_service is None:
            raise BuyerFlowServiceError("当前未配置官方商家订单核验器")
        order = self.order_service.sync_order(store, str(order_id).strip())
        now = datetime.now(UTC)
        status = (
            BuyerOrderVerificationStatus.PENDING_PAYMENT
            if order.status is OrderStatus.PENDING_PAYMENT
            else BuyerOrderVerificationStatus.STATUS_MISMATCH
        )
        order_status = (
            BuyerOrderStatus.UNKNOWN
            if order.status is OrderStatus.UNKNOWN
            else BuyerOrderStatus(order.status.value)
        )
        link = BuyerOrderLink(
            link_id=uuid.uuid4().hex,
            run_id=run.run_id,
            store_id=run.store_id,
            store_app_id=run.store_app_id,
            buyer_account_id=run.buyer_account_id,
            platform_order_id=order.platform_order_id,
            status=status,
            total_amount_cents=order.total_amount_cents,
            item_count=order.item_count,
            created_at=now,
            verified_at=now,
            detail={
                "source": "official_merchant_order_api",
                "order_status": order.status.value,
            },
            order_status=order_status,
        )
        saved = self.repository.record_order_link(link)
        self.repository.update_run(
            run.run_id,
            status=(
                BuyerFlowStatus.ORDER_VERIFIED
                if order_status is not BuyerOrderStatus.UNKNOWN
                else BuyerFlowStatus.FAILED
            ),
            order_id=order.platform_order_id,
            verification_status=status,
            order_status=order_status,
            status_reason=(
                "官方商家订单接口确认订单处于待付款状态。"
                "买家账号与订单的本人对应关系仍需手机微信人工确认。"
                if status is BuyerOrderVerificationStatus.PENDING_PAYMENT
                else (
                    f"官方商家订单接口确认订单状态为 {order.status.value}。"
                    "买家账号与订单的本人对应关系仍需手机微信人工确认。"
                )
            ),
        )
        self._audit(
            store_id=run.store_id,
            store_app_id=run.store_app_id,
            actor_id=actor_id,
            action="buyer_order_verified",
            entity_type="buyer_order",
            entity_id=saved.platform_order_id,
            result=(
                "success"
                if status is BuyerOrderVerificationStatus.PENDING_PAYMENT
                else "failed"
            ),
            detail={"run_id": run.run_id, "status": status.value},
        )
        self.repository.record_task_event(
            run_id=run.run_id,
            buyer_account_id=run.buyer_account_id,
            event_type="merchant_order_verified",
            status=status.value,
            detail={
                "platform_order_id": saved.platform_order_id,
                "total_amount_cents": saved.total_amount_cents,
            },
        )
        return saved

    def preview(
        self,
        run_id: str,
        *,
        buyer_display_name: str = "",
    ) -> BuyerOrderPreview:
        run = self.repository.get_run(run_id)
        if run is None:
            raise BuyerFlowServiceError("买家流程不存在")
        items = tuple(self.repository.list_items(run_id))
        notes = [
            "运费、收货地址和最终应付金额只在官方结算页确认。",
            "商品价格和库存以官方结算页为准，变化时必须重新确认。",
            "本软件不会自动付款。",
        ]
        return BuyerOrderPreview(
            run_id=run.run_id,
            store_name=run.store_name,
            buyer_display_name=buyer_display_name,
            items=items,
            merchandise_total_cents=run.merchandise_total_cents,
            freight_cents=None,
            final_payable_cents=None,
            address_text="请在官方结算页选择并确认收货地址",
            notes=tuple(notes),
        )

    def set_run_status(
        self,
        run_id: str,
        action: str,
        *,
        permission_checker,
        actor_id: str,
    ) -> BuyerFlowRun:
        require_permission(
            permission_checker,
            "dongxiao_buyer_flow_execute",
        )
        run = self.repository.get_run(run_id)
        if run is None:
            raise BuyerFlowServiceError("买家流程不存在")
        if action == "resume":
            current = self.repository.current_account()
            if (
                current is None
                or current.buyer_account_id != run.buyer_account_id
            ):
                raise BuyerFlowServiceError(
                    "任务不属于当前买家账号，不能恢复执行"
                )
            if current.provider is not BuyerProvider.MOCK:
                probe = self._require_running_client()
                if (
                    current.switch_state is not BuyerSwitchState.CONFIRMED
                    or current.confirmed_client_session
                    != self._client_session(probe)
                ):
                    raise BuyerFlowServiceError(
                        "当前买家身份未确认或微信会话已变化，不能恢复任务"
                    )
        if action == "pause" and run.status in {
            BuyerFlowStatus.PREPARED,
            BuyerFlowStatus.WAITING_USER_HANDOFF,
            BuyerFlowStatus.WAITING_ORDER_ID,
        }:
            status = BuyerFlowStatus.PAUSED
        elif action == "resume" and run.status is BuyerFlowStatus.PAUSED:
            status = BuyerFlowStatus.WAITING_USER_HANDOFF
        elif action == "cancel" and run.status not in {
            BuyerFlowStatus.ORDER_VERIFIED,
            BuyerFlowStatus.CANCELLED,
        }:
            status = BuyerFlowStatus.CANCELLED
        else:
            raise BuyerFlowServiceError("当前流程状态不允许该操作")
        saved = self.repository.update_run(
            run_id,
            status=status,
            status_reason=f"用户操作：{action}",
        )
        self.repository.record_task_event(
            run_id=run.run_id,
            buyer_account_id=run.buyer_account_id,
            event_type=action,
            status=saved.status.value,
            detail={},
        )
        return saved

    def _select_products(
        self,
        store,
        *,
        mode: BuyerFlowMode,
        requested_count: int,
        product_ids: list[str],
        quantities: dict[str, int],
    ) -> list[BuyerSelectionItem]:
        candidates: list[str] = []
        if mode is BuyerFlowMode.MANUAL_SELECT:
            candidates = list(
                dict.fromkeys(
                    str(item).strip()
                    for item in product_ids
                    if str(item).strip()
                )
            )
        else:
            page_number = 1
            while len(candidates) < requested_count * 4 and page_number <= 100:
                page = self.catalog_repository.query_products(
                    int(store.id),
                    ProductFilter(
                        states=(ProductState.ACTIVE,),
                        page=page_number,
                        page_size=500,
                    ),
                )
                for product in page.items:
                    if product.stock_total > 0:
                        candidates.append(product.product_id)
                if page_number >= page.page_count:
                    break
                page_number += 1
            candidates = list(dict.fromkeys(candidates))
            if len(candidates) > requested_count:
                candidates = secrets.SystemRandom().sample(
                    candidates,
                    requested_count,
                )
        snapshots = self.catalog_repository.catalog_snapshot_for_products(
            int(store.id),
            candidates,
        )
        selected: list[BuyerSelectionItem] = []
        seen_products: set[str] = set()
        for product_id in candidates:
            if len(selected) >= requested_count:
                break
            if product_id in seen_products:
                continue
            snapshot = snapshots.get(product_id)
            if snapshot is None:
                continue
            product, skus = snapshot
            if product.state is not ProductState.ACTIVE:
                continue
            quantity = int(quantities.get(product_id, 1))
            if quantity <= 0:
                raise ValueError(f"商品 {product_id} 数量必须大于 0")
            available = [
                sku
                for sku in skus
                if sku.available
                and sku.stock >= quantity
                and sku.purchase_min <= quantity
                and (
                    sku.purchase_max is None
                    or quantity <= sku.purchase_max
                )
            ]
            if not available:
                continue
            sku = sorted(
                available,
                key=lambda item: (
                    item.price_cents,
                    item.sku_id,
                ),
            )[0]
            seen_products.add(product_id)
            selected.append(
                BuyerSelectionItem(
                    product_id=product_id,
                    title=product.title,
                    sku_id=sku.sku_id,
                    specs=sku.specs,
                    unit_price_cents=sku.price_cents,
                    quantity=quantity,
                    stock=sku.stock,
                )
            )
        return selected

    def open_official_wechat_client(self) -> bool:
        if os.name != "nt":
            raise BuyerFlowServiceError("官方微信交接仅支持 Windows")
        probe = self.wechat_client.probe()
        if probe.installed and probe.running:
            return True
        if probe.installed and probe.executable_path:
            try:
                os.startfile(probe.executable_path)  # type: ignore[attr-defined]
                return True
            except OSError as exc:
                raise BuyerFlowServiceError(
                    "启动 Windows 官方微信失败，请手动打开后再继续"
                ) from exc
        try:
            os.startfile("weixin://")  # type: ignore[attr-defined]
        except OSError as exc:
            raise BuyerFlowServiceError(
                "未找到系统注册的官方微信客户端，请手动打开后再继续"
            ) from exc
        return True

    def _audit(
        self,
        *,
        actor_id: str,
        action: str,
        entity_type: str,
        entity_id: str,
        result: str,
        detail: dict[str, object],
        store_id: int | None = None,
        store_app_id: str = "",
    ) -> None:
        self.catalog_repository.append_audit_event(
            AuditEvent(
                event_id=uuid.uuid4().hex,
                store_id=store_id,
                store_app_id=store_app_id,
                actor_id=str(actor_id),
                action=str(action),
                entity_type=str(entity_type),
                entity_id=str(entity_id),
                request_id="",
                result=AuditResult(str(result)),
                created_at=datetime.now(UTC),
                detail=dict(detail),
            )
        )
