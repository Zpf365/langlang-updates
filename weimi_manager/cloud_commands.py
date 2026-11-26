from __future__ import annotations

import inspect
import uuid
from collections.abc import Callable
from threading import RLock
from typing import Any, TypeVar

from .auth_client import AuthError, SupabaseAuthClient
from .models import Store

T = TypeVar("T")

COMMANDS: dict[str, tuple[str, bool]] = {
    "settings.manage": ("stores_view", False),
    "stores.view": ("stores_view", False),
    "stores.edit": ("stores_edit", True),
    "stores.organize": ("stores_edit", False),
    "stores.delete": ("stores_delete", True),
    "stores.login": ("stores_login", True),
    "stores.export": ("stores_export", True),
    "products.view": ("products_view", True),
    "products.edit": ("products_edit", True),
    "products.upload": ("products_upload", True),
    "products.delete": ("products_edit", True),
    "products.listing": ("products_edit", True),
    "products.stock": ("products_edit", True),
    "orders.view": ("orders_view", True),
    "orders.edit": ("orders_edit", True),
    "orders.after_sales": ("after_sales", True),
    "operations.view": ("operations_view", False),
    "operations.export": ("operations_export", False),
    "operations.task_manage": ("operations_task_manage", False),
    "operations.rule_manage": ("operations_rule_manage", False),
    "operations.open_backend": ("operations_open_backend", False),
    "procurement.manage": ("procurement", False),
    "activities.manage": ("activities", True),
    "finance.view": ("orders_view", True),
    "team.manage": ("team_manage", False),
    "automation.manage": ("products_edit", False),
}

LOCAL_ONLY_SETTINGS = {
    "account_store_sync_error",
    "auto_refresh",
    "auto_refresh_batch_size",
    "auto_refresh_cursor",
    "automatic_backup_at",
    "automatic_backup_date",
    "last_update_check",
    "skipped_update_version",
    "tester_update_source",
    "upload_batch",
    "upload_last_source",
    "upload_tasks",
    "update_auto_check",
    "update_source",
    "violation_daily_attempt_at",
    "violation_daily_last_success",
    "violation_blacklist",
    "violation_library",
    "upload_violation_titles",
    "cloud_counterfeit_violation_titles",
}
LOCAL_ONLY_SETTING_PREFIXES = (
    "organization_synced_appids:",
    "pending_cloud_store_deletions:",
    "analytics_product_backfill_at_",
    "orders_day_backfill_",
    "product_quota_",
    "store_metrics_",
)


def is_local_only_setting(key: str) -> bool:
    normalized = str(key or "").strip()
    return normalized in LOCAL_ONLY_SETTINGS or normalized.startswith(
        LOCAL_ONLY_SETTING_PREFIXES
    )


class CloudCommandClient:
    """Small command seam for server-side authorization and audit."""

    def __init__(
        self,
        client: SupabaseAuthClient,
        access_token: str,
    ) -> None:
        self.client = client
        self.access_token = access_token

    def authorize(
        self,
        command: str,
        store_app_id: str = "",
    ) -> str:
        if command not in COMMANDS:
            raise AuthError(f"客户端不支持业务命令：{command}")
        request_id = uuid.uuid4().hex
        data = self.client.authorize_organization_command(
            self.access_token,
            request_id,
            command,
            store_app_id,
        )
        if not data.get("authorized"):
            raise AuthError("云端没有授权该业务操作", 403)
        if str(data.get("command") or command) != command:
            raise AuthError("云端返回了不匹配的业务命令", 502)
        if str(data.get("store_app_id") or "").strip() != store_app_id.strip():
            raise AuthError("云端返回了不匹配的店铺授权", 502)
        return request_id

    def execute(
        self,
        command: str,
        store_app_id: str,
        operation: Callable[[], T],
    ) -> T:
        return self.execute_many(
            command,
            [store_app_id] if store_app_id else [],
            operation,
        )

    def execute_many(
        self,
        command: str,
        store_app_ids: list[str],
        operation: Callable[[], T],
    ) -> T:
        definition = COMMANDS.get(command)
        if definition is None:
            raise AuthError(f"客户端不支持业务命令：{command}")
        normalized_ids = sorted(
            {str(item).strip() for item in store_app_ids if str(item).strip()}
        )
        if definition[1] and not normalized_ids:
            raise AuthError("该业务操作必须指定授权店铺", 403)
        request_ids: list[str] = []
        try:
            for app_id in normalized_ids or [""]:
                request_ids.append(self.authorize(command, app_id))
        except Exception as exc:
            for request_id in request_ids:
                try:
                    self.client.complete_organization_command(
                        self.access_token,
                        request_id,
                        False,
                        error=f"批量授权失败: {type(exc).__name__}: {exc}",
                    )
                except Exception:
                    pass
            raise
        try:
            result = operation()
        except Exception as exc:
            for request_id in request_ids:
                try:
                    self.client.complete_organization_command(
                        self.access_token,
                        request_id,
                        False,
                        error=f"{type(exc).__name__}: {exc}",
                    )
                except Exception:
                    pass
            raise
        detail: dict[str, Any]
        if isinstance(result, dict):
            detail = result
        elif isinstance(result, (str, int, float, bool)) or result is None:
            detail = {"value": result}
        else:
            detail = {"value": repr(result)[:1000]}
        for request_id in request_ids:
            try:
                self.client.complete_organization_command(
                    self.access_token,
                    request_id,
                    True,
                    result=detail,
                )
            except Exception:
                pass
        return result


SERVICE_COMMANDS = {
    "list_members": "team.manage",
    "create_subaccount": "team.manage",
    "add_existing_account": "team.manage",
    "reset_password": "team.manage",
    "update_member": "team.manage",
    "client_for": "products.view",
    "validate_store": "stores.edit",
    "sync_store_profile": "stores.edit",
    "sync_orders": "orders.view",
    "sync_order": "orders.view",
    "sync_recent_order_updates": "orders.view",
    "update_order_notes": "orders.edit",
    "update_order_price": "orders.edit",
    "request_order_address_change": "orders.edit",
    "update_order_delivery_info": "orders.edit",
    "aftersale_action": "orders.after_sales",
    "upload_after_sale_media": "orders.after_sales",
    "sync_after_sales": "orders.after_sales",
    "product_write": "products.edit",
    "upload_product_images": "products.edit",
    "sync_products": "products.view",
    "scan_product_catalog": "products.view",
    "sync_product_details": "products.view",
    "refresh_product_status_snapshot": "products.view",
    "refresh_store_metrics": "stores.view",
    "fetch_product_taglink": "products.view",
    "fetch_product_audit_quota": "products.view",
    "refresh_product_tab": "products.view",
    "sync_private_product_tab": "products.view",
    "sync_private_product_metrics": "products.view",
    "sync_product_performance": "products.view",
    "private_batch_product_action": "products.edit",
    "promote_products_to_league": "products.edit",
    "fetch_league_products": "products.view",
    "update_league_ratio": "products.edit",
    "update_product_prices": "products.edit",
    "create_snapup_activity": "activities.manage",
    "delete_snapup_activity": "activities.manage",
    "create_limited_discount_tasks": "products.edit",
    "stop_active_limited_discounts": "products.edit",
    "set_products_listing": "products.listing",
    "update_product_stock": "products.stock",
    "refresh_shipping_options": "orders.edit",
    "create_shipment": "orders.edit",
    "complete_shipment": "orders.edit",
    "decode_order_sensitive": "orders.view",
    "sync_analytics_day": "finance.view",
    "sync_analytics_range": "finance.view",
    "sync_coupons": "activities.manage",
    "coupon_write": "activities.manage",
    "sync_storefront": "stores.edit",
    "storefront_action": "stores.edit",
    "procurement_login": "procurement.manage",
    "sync_procurement_account": "procurement.manage",
    "procurement_browse": "procurement.manage",
    "match_procurement_orders": "procurement.manage",
    "sync_procurement_note_to_wechat": "orders.edit",
    "double11_activity_products": "activities.manage",
    "snapup_activities": "activities.manage",
    "platform_activity_catalog": "activities.manage",
    "apply_platform_activity": "activities.manage",
    "activity_preview": "activities.manage",
    "activity_submit_items": "activities.manage",
    "double11_remove_all": "activities.manage",
    "double11_submit": "activities.manage",
    "sync_platform_violation_blacklist": "products.view",
    "sync_violation_library": "products.view",
    "fetch_funds_summary": "finance.view",
}

WEB_SESSION_COMMANDS = {
    "interactive_login": "stores.login",
    "import_cookie_header": "stores.login",
    "bulk_register_activity_products": "activities.manage",
    "apply_platform_activity_page": "activities.manage",
    "renew_snapup_activity": "activities.manage",
    "renew_coupon": "activities.manage",
    "open_official_customer_console": "orders.after_sales",
    "open_official_backend": "stores.view",
    "open_official_order": "orders.view",
    "open_official_product_backend": "products.view",
    "probe": "stores.view",
    "logout": "stores.login",
}

STORE_SNAPSHOT_MUTATIONS = {
    "add_store",
    "clear_all_web_logins",
    "clear_web_session",
    "clear_selected_web_sessions",
    "create_store_group",
    "delete_store",
    "ensure_web_login_store",
    "import_store_accounts",
    "rename_store_group",
    "delete_store_group",
    "set_store_groups",
    "set_web_session",
    "update_store",
    "update_web_status",
    "upsert_stores",
}

DATABASE_COMMANDS = {
    "set_setting": "settings.manage",
    "update_order_workbench": "orders.edit",
    "create_store_group": "stores.organize",
    "add_store": "stores.edit",
    "ensure_web_login_store": "stores.edit",
    "upsert_stores": "stores.edit",
    "update_store": "stores.edit",
    "set_store_groups": "stores.edit",
    "set_web_session": "stores.login",
    "update_web_status": "stores.edit",
    "clear_web_session": "stores.login",
    "clear_selected_web_sessions": "stores.login",
    "clear_all_web_logins": "stores.login",
    "delete_store": "stores.delete",
    "import_store_accounts": "stores.edit",
    "rename_store_group": "stores.organize",
    "delete_store_group": "stores.organize",
    "upsert_procurement_link": "procurement.manage",
    "add_procurement_account": "procurement.manage",
    "update_procurement_account": "procurement.manage",
    "delete_procurement_account": "procurement.manage",
    "update_procurement_order_local": "procurement.manage",
    "add_automation_task": "automation.manage",
    "update_automation_task": "automation.manage",
    "delete_automation_task": "automation.manage",
    "update_hosted_config": "automation.manage",
    "restore_from": "settings.manage",
}

LOCAL_DATABASE_MUTATIONS = {
    "apply_remote_order_workbench",
    "apply_remote_setting",
    "delete_order_workbench_row",
    "delete_setting",
    "finish_task_log",
    "log_automation_run",
    "log_hosted_run",
    "recover_interrupted_task_logs",
    "set_store_status",
    "start_task_log",
    "touch_store_sync",
}


def _command_store_ids(args: tuple[Any, ...]) -> list[str]:
    for value in args:
        if isinstance(value, Store):
            return [value.app_id] if value.app_id else []
    return []


def _store_id_app_ids(db: Any, args: tuple[Any, ...], kwargs: dict[str, Any]) -> list[str]:
    values: list[Any] = []
    if args:
        if isinstance(args[0], (list, tuple, set)):
            values.extend(args[0])
        else:
            values.append(args[0])
    values.extend(
        kwargs[key]
        for key in ("store_id", "store_ids")
        if key in kwargs
    )
    result: list[str] = []
    for value in values:
        if isinstance(value, Store):
            if value.app_id:
                result.append(value.app_id)
            continue
        if isinstance(value, (list, tuple, set)):
            result.extend(_store_id_app_ids(db, tuple(value), {}))
            continue
        try:
            store = db.get_store(int(value))
        except (KeyError, TypeError, ValueError):
            continue
        if store is not None and store.app_id:
            result.append(store.app_id)
    return sorted(set(result))


def _mutation_store_ids(
    db: Any,
    name: str,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> list[str]:
    direct = _command_store_ids(args)
    if direct:
        return sorted(set(direct))
    if name in {"add_store", "ensure_web_login_store"}:
        app_id = str(args[1] if len(args) > 1 else kwargs.get("app_id", ""))
        return [app_id.strip()] if app_id.strip() else []
    if name == "upsert_stores":
        records = args[0] if args else kwargs.get("records", [])
        return sorted(
            {
                str(row[1]).strip()
                for row in records or []
                if isinstance(row, (list, tuple))
                and len(row) > 1
                and str(row[1]).strip()
            }
        )
    if name == "clear_all_web_logins":
        return sorted(
            {
                store.app_id
                for store in db.list_stores()
                if store.app_id
            }
        )
    if name == "import_store_accounts":
        snapshot = args[0] if args else kwargs.get("snapshot", {})
        return sorted(
            {
                str(row.get("app_id") or "").strip()
                for row in (snapshot.get("stores") or [])
                if isinstance(row, dict)
                and str(row.get("app_id") or "").strip()
            }
        ) if isinstance(snapshot, dict) else []
    if name == "set_store_groups":
        store_ids = args[0] if args else kwargs.get("store_ids", [])
        return _store_id_app_ids(db, (store_ids,), {})
    if name in {"rename_store_group", "delete_store_group"}:
        group_name = str(
            args[0] if args else kwargs.get("group_name", "")
        ).strip()
        return sorted(
            {
                store.app_id
                for store in db.list_stores()
                if store.group_name == group_name and store.app_id
            }
        )
    if name == "update_order_workbench":
        return _store_id_app_ids(db, args[:1], kwargs)
    if name in {
        "clear_web_session",
        "clear_selected_web_sessions",
        "delete_store",
        "set_store_status",
        "set_web_session",
        "update_store",
        "update_web_status",
        "update_hosted_config",
    }:
        return _store_id_app_ids(db, args[:1], kwargs)
    if name == "add_automation_task":
        return _store_id_app_ids(
            db,
            (),
            {"store_id": args[2] if len(args) > 2 else kwargs.get("store_id")},
        )
    if name == "update_automation_task":
        return _store_id_app_ids(
            db,
            (),
            {"store_id": kwargs.get("store_id")},
        )
    return []


class AuthorizedServiceAdapter:
    def __init__(
        self,
        service: Any,
        commands: CloudCommandClient,
    ) -> None:
        object.__setattr__(self, "_service", service)
        object.__setattr__(self, "_commands", commands)

    def __getattr__(self, name: str) -> Any:
        value = getattr(self._service, name)
        command = SERVICE_COMMANDS.get(name)
        if command is None or not callable(value):
            return value

        def authorized(*args: Any, **kwargs: Any) -> Any:
            return self._commands.execute_many(
                command,
                _command_store_ids(args),
                lambda: value(*args, **kwargs),
            )

        return authorized

    def __setattr__(self, name: str, value: Any) -> None:
        setattr(self._service, name, value)


class AuthorizedWebSessionAdapter:
    def __init__(
        self,
        session: Any,
        commands: CloudCommandClient,
    ) -> None:
        object.__setattr__(self, "_session", session)
        object.__setattr__(self, "_commands", commands)

    def __getattr__(self, name: str) -> Any:
        value = getattr(self._session, name)
        command = WEB_SESSION_COMMANDS.get(name)
        if command is None or not callable(value):
            return value

        def authorized(*args: Any, **kwargs: Any) -> Any:
            return self._commands.execute_many(
                command,
                _command_store_ids(args),
                lambda: value(*args, **kwargs),
            )

        return authorized

    def __setattr__(self, name: str, value: Any) -> None:
        setattr(self._session, name, value)


class AuthorizedDatabaseAdapter:
    _WORKBENCH_FIELDS = {
        "purchase_order_no",
        "pickup_code",
        "note",
        "profit",
        "cost",
        "is_marked",
        "is_marked_sent",
        "parsed_at",
    }

    def __init__(
        self,
        db: Any,
        commands: CloudCommandClient,
        store_sync: Any = None,
    ) -> None:
        object.__setattr__(self, "_db", db)
        object.__setattr__(self, "_commands", commands)
        object.__setattr__(self, "_store_sync", store_sync)
        object.__setattr__(self, "_store_sync_lock", RLock())
        object.__setattr__(self, "_store_login_batch_active", False)
        object.__setattr__(self, "_store_login_batch_changed", False)

    def begin_store_login_batch(self) -> None:
        """Defer full cloud vault synchronization during a scan-login batch."""
        store_sync = self._store_sync
        if store_sync is not None:
            pull = getattr(store_sync, "pull", None)
            if callable(pull):
                with self._store_sync_lock:
                    try:
                        parameters = inspect.signature(pull).parameters
                    except (TypeError, ValueError):
                        parameters = {}
                    pull(
                        **(
                            {
                                "include_workbench": False,
                                "include_settings": False,
                            }
                            if "include_workbench" in parameters
                            else {}
                        )
                    )
        object.__setattr__(self, "_store_login_batch_active", True)
        object.__setattr__(self, "_store_login_batch_changed", False)

    def end_store_login_batch(self) -> dict[str, Any]:
        """Upload one final vault snapshot after all scan-login stores."""
        active = self._store_login_batch_active
        changed = self._store_login_batch_changed
        object.__setattr__(self, "_store_login_batch_active", False)
        object.__setattr__(self, "_store_login_batch_changed", False)
        if not active or not changed:
            return {"stored": True}
        store_sync = self._store_sync
        if store_sync is None:
            return {"stored": True}
        with self._store_sync_lock:
            result = store_sync.push_current()
        if not result.get("stored", False):
            raise RuntimeError("云端未确认批量店铺登录状态保存")
        return result

    def set_store_sync(self, store_sync: Any) -> None:
        object.__setattr__(self, "_store_sync", store_sync)

    def delete_store_for_app_id(
        self,
        store_id: int,
        store_app_id: str,
    ) -> dict[str, Any]:
        app_id = str(store_app_id or "").strip()
        if not app_id:
            raise AuthError("删除店铺缺少 AppID", 403)

        def operation() -> dict[str, Any]:
            with self._store_sync_lock:
                store_sync = self._store_sync
                removed = 0
                if store_sync is not None:
                    result = store_sync.delete_now([app_id])
                    if not result.get("stored"):
                        raise RuntimeError("云端未确认店铺删除")
                    removed = int(result.get("removed") or 0)
                self._db.delete_store(int(store_id))
                return {"stored": True, "removed": removed}

        return self._commands.execute(
            "stores.delete",
            app_id,
            operation,
        )

    def _store_snapshot_mutation(
        self,
        name: str,
        operation: Callable[[], T],
        store_app_ids: list[str],
    ) -> T:
        store_sync = self._store_sync
        if store_sync is None:
            return operation()

        with self._store_sync_lock:
            # Pull before taking the rollback image so a successful local write
            # is compared with the latest server state, not an old local copy.
            pull = store_sync.pull
            try:
                parameters = inspect.signature(pull).parameters
            except (TypeError, ValueError):
                parameters = {}
            pull(
                **(
                    {
                        "include_workbench": False,
                        "include_settings": False,
                    }
                    if "include_workbench" in parameters
                    else {}
                )
            )
            previous = self._db.export_store_accounts()
            try:
                if name == "delete_store" and store_app_ids:
                    deleted = store_sync.delete_now(store_app_ids)
                    if not deleted.get("stored"):
                        raise RuntimeError("云端未确认店铺删除")
                result = operation()
                if name != "delete_store":
                    stored = store_sync.push_current()
                    if not stored.get("stored", False):
                        raise RuntimeError("云端未确认店铺数据保存")
                return result
            except Exception:
                restore = getattr(self._db, "restore_store_snapshot", None)
                if callable(restore):
                    restore(previous)
                raise

    def _authorized_mutation(
        self,
        name: str,
        method: Callable[..., T],
        *args: Any,
        **kwargs: Any,
    ) -> T:
        if (
            self._store_login_batch_active
            and name in {"ensure_web_login_store", "set_web_session"}
        ):
            result = method(*args, **kwargs)
            object.__setattr__(self, "_store_login_batch_changed", True)
            return result
        command = DATABASE_COMMANDS[name]
        store_app_ids = _mutation_store_ids(
            self._db,
            name,
            args,
            kwargs,
        )

        def operation() -> T:
            if name in STORE_SNAPSHOT_MUTATIONS:
                return self._store_snapshot_mutation(
                    name,
                    lambda: method(*args, **kwargs),
                    store_app_ids,
                )
            return method(*args, **kwargs)

        return self._commands.execute_many(
            command,
            store_app_ids,
            operation,
        )

    def _update_order_workbench(
        self,
        store_id: int,
        order_id: str,
        **changes: Any,
    ) -> dict[str, Any]:
        store = self._db.get_store(int(store_id))
        if store is None:
            raise KeyError("店铺不存在")

        def operation() -> dict[str, Any]:
            previous = self._db.get_order_workbench_row(
                int(store_id),
                str(order_id),
            )
            current = self._db.update_order_workbench(
                int(store_id),
                str(order_id),
                **changes,
            )
            payload = {
                key: current.get(key)
                for key in self._WORKBENCH_FIELDS
            }
            try:
                result = self._commands.client.upsert_organization_order_workbench(
                    self._commands.access_token,
                    [
                        {
                            "store_app_id": store.app_id,
                            "order_id": str(order_id),
                            "payload": payload,
                        }
                    ],
                )
                if int(result.get("saved") or 0) != 1:
                    raise RuntimeError("云端未确认订单工作台保存")
            except Exception:
                if previous is None:
                    self._db.delete_order_workbench_row(
                        int(store_id),
                        str(order_id),
                    )
                else:
                    self._db.update_order_workbench(
                        int(store_id),
                        str(order_id),
                        **{
                            key: previous.get(key)
                            for key in self._WORKBENCH_FIELDS
                        },
                    )
                raise
            return current

        return self._commands.execute(
            "orders.edit",
            store.app_id,
            operation,
        )

    def _set_setting(self, key: str, value: str) -> None:
        if is_local_only_setting(str(key)):
            self._db.set_setting(key, value)
            return
        missing = "\0__missing__"
        previous = self._db.get_setting(str(key), missing)

        def operation() -> None:
            self._db.set_setting(str(key), value)
            try:
                result = self._commands.client.upsert_organization_settings(
                    self._commands.access_token,
                    [{"key": str(key), "value": str(value)}],
                )
                if int(result.get("saved") or 0) != 1:
                    raise RuntimeError("云端未确认设置保存")
            except Exception:
                if previous == missing:
                    self._db.delete_setting(str(key))
                else:
                    self._db.set_setting(str(key), previous)
                raise

        self._commands.execute("settings.manage", "", operation)

    def __getattr__(self, name: str) -> Any:
        if name == "update_order_workbench":
            return self._update_order_workbench
        if name == "set_setting":
            return self._set_setting
        value = getattr(self._db, name)
        if name not in DATABASE_COMMANDS or not callable(value):
            return value

        def authorized(*args: Any, **kwargs: Any) -> Any:
            return self._authorized_mutation(
                name,
                value,
                *args,
                **kwargs,
            )

        return authorized

    def __setattr__(self, name: str, value: Any) -> None:
        setattr(self._db, name, value)


def wrap_database(
    db: Any,
    commands: CloudCommandClient | None,
    store_sync: Any = None,
) -> Any:
    return (
        AuthorizedDatabaseAdapter(db, commands, store_sync)
        if commands is not None
        else db
    )


def set_database_store_sync(db: Any, store_sync: Any) -> None:
    setter = getattr(db, "set_store_sync", None)
    if callable(setter):
        setter(store_sync)
