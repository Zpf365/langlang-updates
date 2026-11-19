from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from .. import __version__
from ..account_store_sync import AccountStoreSync
from ..auth_client import SupabaseAuthClient
from ..auth_config import AuthConfig
from ..auth_service import AuthContext
from ..cloud_commands import (
    AuthorizedServiceAdapter,
    AuthorizedWebSessionAdapter,
    CloudCommandClient,
    wrap_database,
)
from ..config import AppPaths
from ..db import Database
from ..diagnostics import DiagnosticManager
from ..service import SyncService
from ..tester_channel import load_tester_channel
from ..web_session import WechatWebSession
from .task_manager import TaskManager


@dataclass(slots=True)
class Workspace:
    """One authenticated account's legacy business data behind the Qt shell."""

    paths: AppPaths
    auth_context: AuthContext
    db: Database
    service: SyncService
    web_session: WechatWebSession
    diagnostics: DiagnosticManager
    task_manager: TaskManager
    auth_client: SupabaseAuthClient | None = None
    account_store_sync: AccountStoreSync | None = None
    theme_manager: Any = None

    def close(self) -> None:
        try:
            self.web_session.cleanup_cache()
        finally:
            self.diagnostics.close()


def build_workspace(
    paths: AppPaths,
    auth_context: AuthContext,
    task_manager: TaskManager,
    *,
    auth_client: SupabaseAuthClient | None = None,
    theme_manager: Any = None,
) -> Workspace:
    """Build the legacy database and services without replacing their schema."""
    raw_db = Database(paths.database)
    try:
        raw_db.recover_interrupted_task_logs()
    except Exception:
        pass
    config = AuthConfig.load(paths)
    tester_channel = load_tester_channel(paths.resource_dir)
    if paths.data_dir.parent.parent.name.casefold() == "accounts":
        base_data_dir = paths.data_dir.parent.parent.parent / "data"
    else:
        base_data_dir = paths.data_dir
    diagnostics = DiagnosticManager(
        paths.data_dir,
        __version__,
        account_id=auth_context.account_id,
        username=auth_context.username,
        device_hash=auth_context.device_hash,
        upload_url=str(
            tester_channel.get("diagnostic_upload_url")
            or (config.function_url("submit-diagnostic-report") if config.configured else "")
        ),
        access_token=auth_context.tokens.access_token,
        access_token_provider=(
            lambda: str(auth_client.current_access_token or "")
            if auth_client is not None
            else ""
        ),
        access_token_refresher=(
            lambda failed: auth_client.refresh_current_session(
                failed
            ).access_token
            if auth_client is not None
            else ""
        ),
        tester_key=str(tester_channel.get("diagnostic_upload_key") or ""),
        crash_log_path=base_data_dir / "diagnostics" / "faulthandler.log",
    )
    diagnostics.install_exception_hooks()
    account_store_sync = None
    if auth_client is not None and auth_context.vault_key:
        account_store_sync = AccountStoreSync(
            db=raw_db,
            auth_client=auth_client,
            tokens=auth_context.tokens,
            vault_key=auth_context.vault_key,
            organization_id=auth_context.organization_id,
            member_role=auth_context.member_role,
            member_permissions=dict(auth_context.permissions),
        )
    commands = (
        CloudCommandClient(
            auth_client,
            auth_context.tokens.access_token,
        )
        if auth_client is not None
        and auth_context.tokens.access_token
        else None
    )
    db = wrap_database(raw_db, commands, account_store_sync)
    base_service = SyncService(db)
    base_web_session = WechatWebSession(db, paths.data_dir)
    service = (
        AuthorizedServiceAdapter(base_service, commands)
        if commands is not None
        else base_service
    )
    web_session = (
        AuthorizedWebSessionAdapter(base_web_session, commands)
        if commands is not None
        else base_web_session
    )
    return Workspace(
        paths=paths,
        auth_context=auth_context,
        db=db,
        service=service,
        web_session=web_session,
        diagnostics=diagnostics,
        task_manager=task_manager,
        auth_client=auth_client,
        account_store_sync=account_store_sync,
        theme_manager=theme_manager,
    )


def pull_account_stores(workspace: Workspace) -> dict[str, int]:
    """Pull the encrypted account store vault and record a task-log entry."""
    if workspace.account_store_sync is None:
        return {"created": 0, "updated": 0, "logins": 0}
    started = time.monotonic()
    log_id: Any = None
    try:
        log_id = workspace.db.start_task_log(
            scope="account_sync",
            operation="恢复账户店铺和登录状态",
            message="开始读取账户加密店铺保险箱",
        )
    except Exception:
        pass
    try:
        result = workspace.account_store_sync.pull()
    except Exception as exc:
        if log_id is not None:
            workspace.db.finish_task_log(
                log_id,
                status="failed",
                message=str(exc),
                detail=type(exc).__name__,
                duration_ms=int((time.monotonic() - started) * 1000),
            )
        raise
    if log_id is not None:
        workspace.db.finish_task_log(
            log_id,
            status="success",
            message=(f"账户店铺已同步：新增 {result['created']}，更新 {result['updated']}"),
            detail=(
                f"恢复登录态：{result['logins']}\n"
                f"新增店铺：{result['created']}\n"
                f"更新店铺：{result['updated']}"
            ),
            duration_ms=int((time.monotonic() - started) * 1000),
        )
    return result
