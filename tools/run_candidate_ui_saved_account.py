"""Open the source candidate WebUI with the already-saved account session."""

from __future__ import annotations

import sys
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weimi_manager.auth_config import AuthConfig
from weimi_manager.auth_service import build_auth_service
from weimi_manager.config import AppPaths
from weimi_manager.db import Database
from weimi_manager.service import SyncService
from weimi_manager.team_service import TeamService
from weimi_manager.web_session import WechatWebSession
from weimi_manager.webui.controller import NextUiController
from weimi_manager.webui.desktop import run_desktop


def main() -> int:
    local_app_data = Path(
        os.environ.get("LOCALAPPDATA")
        or (Path.home() / "AppData" / "Local")
    )
    data_dir = local_app_data / "LangLang" / "data"
    paths = AppPaths(
        project_dir=ROOT,
        resource_dir=ROOT,
        data_dir=data_dir,
        database=data_dir / "weimi_manager.db",
    )
    auth_service = build_auth_service(
        AuthConfig.load(paths),
        paths.data_dir.parent / "auth",
    )
    if auth_service is None:
        raise RuntimeError("账号授权服务未配置")
    accounts = auth_service.store.list_accounts()
    saved = next(
        (
            account
            for account in accounts
            if str(account.username).casefold() == "zpfly"
        ),
        accounts[0] if accounts else None,
    )
    if saved is None:
        raise RuntimeError("没有可恢复的保存账户")
    context = auth_service.restore_account(saved)
    account_paths = paths.for_account(context.account_id)
    database = Database(account_paths.database)
    service = SyncService(database)
    controller = NextUiController(
        database,
        account_label=context.username,
        project_dir=ROOT,
        web_session=WechatWebSession(database, account_paths.data_dir),
        product_service=service,
        auth_context=context,
        auth_client=auth_service.client,
        auth_service=auth_service,
        team_service=TeamService(
            auth_service.client,
            context.tokens.access_token,
        ),
    )
    return run_desktop(
        controller,
        resource_dir=ROOT,
        title=f"澜琅候选 · 微信小店版 {context.username}",
    )


if __name__ == "__main__":
    raise SystemExit(main())
