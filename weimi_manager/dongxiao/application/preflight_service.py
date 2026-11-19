from __future__ import annotations

import hashlib
import re
import shutil
import sqlite3
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Callable

from ...wechat_api import WechatApiError, WechatShopClient
from ..domain.operations import (
    PreflightCheck,
    PreflightReport,
    PreflightState,
)
from ..domain.rules import require_permission
from ..infrastructure.migrations import (
    DONGXIAO_MIGRATION_VERSION,
    _verify_required_tables,
)
from ..infrastructure.repository import DongxiaoRepository

PLATFORM_READ_AUTHORIZATION = "AUTHORIZED_READ_ONLY_CHECK"


class DongxiaoPreflightService:
    def __init__(
        self,
        repository: DongxiaoRepository,
        *,
        runtime,
        sync_service,
        database_path: Path,
        backup_dir: Path,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.repository = repository
        self.runtime = runtime
        self.sync_service = sync_service
        self.database_path = Path(database_path)
        self.backup_dir = Path(backup_dir)
        self.clock = clock or (lambda: datetime.now(UTC))

    def run_local(
        self,
        *,
        actor_id: str,
        permission_checker,
    ) -> PreflightReport:
        del actor_id
        require_permission(
            permission_checker,
            "dongxiao_preflight_run",
        )
        backup, backup_error = self._find_latest_valid_backup()
        checks = [
            self._database_integrity_check(),
            self._migration_check(),
            self._backup_check(backup, backup_error),
            self._restore_rehearsal_check(backup),
            self._scheduler_check(),
            self._audit_check(),
            PreflightCheck(
                code="permissions",
                label="应用层权限",
                state=PreflightState.PASSED,
                message="当前账号具备动销实盘前检查权限",
            ),
            self._write_guard_check(),
            PreflightCheck(
                code="store_authorization",
                label="店铺授权",
                state=PreflightState.MANUAL_AUTHORIZATION_REQUIRED,
                message="真实店铺授权只能在用户手动确认后执行只读检查",
            ),
            PreflightCheck(
                code="product_api",
                label="商品接口",
                state=PreflightState.NOT_VERIFIED,
                message=(
                    "尚未执行官方 API 只读检查；"
                    "本地缓存和 Mock 不参与通过判定"
                ),
            ),
            PreflightCheck(
                code="order_api",
                label="订单接口",
                state=PreflightState.NOT_VERIFIED,
                message=(
                    "尚未执行官方 API 只读检查；"
                    "本地缓存和 Mock 不参与通过判定"
                ),
            ),
            PreflightCheck(
                code="aftersale_read_api",
                label="售后读取接口",
                state=PreflightState.NOT_VERIFIED,
                message=(
                    "尚未执行官方 API 只读检查；"
                    "本地缓存和 Mock 不参与通过判定"
                ),
            ),
        ]
        return PreflightReport(
            generated_at=self.clock(),
            checks=tuple(checks),
        )

    def run_platform_readonly(
        self,
        store,
        *,
        actor_id: str,
        permission_checker,
        authorization_text: str,
    ) -> PreflightReport:
        del actor_id
        require_permission(
            permission_checker,
            "dongxiao_preflight_platform_read",
        )
        if authorization_text != PLATFORM_READ_AUTHORIZATION:
            raise ValueError("未收到本次真实店铺只读检查的明确授权")
        invalidate = getattr(
            self.runtime,
            "invalidate_platform_readonly_verification",
            None,
        )
        if callable(invalidate):
            invalidate()
        local = self.run_local(
            actor_id="platform-readonly",
            permission_checker=permission_checker,
        )
        now = self.clock()
        end_time = int(now.timestamp())
        start_time = int((now - timedelta(days=1)).timestamp())
        checks = [
            item
            for item in local.checks
            if item.code
            not in {
                "store_authorization",
                "product_api",
                "order_api",
                "aftersale_read_api",
            }
        ]
        client = None
        token_verified = False
        mock_reason = ""
        if str(getattr(store, "app_id", "")).casefold() == "demo-store":
            mock_reason = "demo-store 只能使用 Mock 数据，禁止真实验收"
        else:
            try:
                candidate = self.sync_service.client_for(store)
                if type(candidate) is not WechatShopClient:
                    mock_reason = (
                        "当前客户端不是正式 WechatShopClient，"
                        "Mock/Fake 结果不允许真实验收通过"
                    )
                else:
                    client = candidate
            except Exception as exc:
                state = self._authorization_failure_state(exc)
                checks.append(
                    PreflightCheck(
                        code="store_authorization",
                        label="店铺授权",
                        state=state,
                        message=self._safe_error(
                            "店铺授权检查失败",
                            exc,
                        ),
                    )
                )

        if mock_reason:
            checks.append(
                PreflightCheck(
                    code="store_authorization",
                    label="店铺授权",
                    state=PreflightState.NOT_VERIFIED,
                    message=f"{mock_reason}；Mock 数据不参与通过判定",
                )
            )
        elif client is not None:
            try:
                token = client.get_access_token()
                if not token:
                    raise RuntimeError("授权接口未返回有效 Token")
                token_verified = True
                metadata = client.last_call_metadata()
                checks.append(
                    PreflightCheck(
                        code="store_authorization",
                        label="店铺授权",
                        state=PreflightState.PASSED,
                        message="正式 WechatShopClient 稳定版 Token 获取成功",
                        detail=self._request_detail(metadata),
                    )
                )
            except Exception as exc:
                state = self._authorization_failure_state(exc)
                metadata = client.last_call_metadata()
                checks.append(
                    PreflightCheck(
                        code="store_authorization",
                        label="店铺授权",
                        state=state,
                        message=self._safe_error(
                            "店铺授权检查失败",
                            exc,
                        ),
                        detail=self._request_detail(
                            metadata,
                            error=exc,
                        ),
                    )
                )

        if not token_verified:
            for code, label in (
                ("product_api", "商品接口"),
                ("order_api", "订单接口"),
                ("aftersale_read_api", "售后读取接口"),
            ):
                checks.append(
                    PreflightCheck(
                        code=code,
                        label=label,
                        state=PreflightState.NOT_VERIFIED,
                        message=(
                            "店铺授权失败，已停止后续真实平台请求"
                            if client is not None
                            else "未获得正式官方客户端，禁止发起真实验收"
                        ),
                    )
                )
        else:
            checks.append(self._run_platform_call(
                code="product_api",
                label="商品接口",
                call=lambda: client.fetch_product_id_page(page_size=1),
                client=client,
                validator=self._validate_product_page,
            ))
            checks.append(self._run_platform_call(
                code="order_api",
                label="订单接口",
                call=lambda: client.fetch_order_ids(
                    start_time,
                    end_time,
                    None,
                ),
                client=client,
                validator=self._validate_id_list,
            ))
            checks.append(self._run_platform_call(
                code="aftersale_read_api",
                label="售后读取接口",
                call=lambda: client.fetch_after_sale_ids(
                    start_time,
                    end_time,
                    time_field="update",
                ),
                client=client,
                validator=self._validate_id_list,
            ))
        report = PreflightReport(
            generated_at=self.clock(),
            checks=tuple(checks),
            store_app_id=str(getattr(store, "app_id", "") or ""),
        )
        remember = getattr(
            self.runtime,
            "remember_platform_readonly_report",
            None,
        )
        if callable(remember):
            remember(report)
        return report

    def format_report(self, report: PreflightReport) -> str:
        labels = {
            PreflightState.PASSED: "已通过",
            PreflightState.FAILED: "未通过",
            PreflightState.NOT_VERIFIED: "未验证",
            PreflightState.MANUAL_AUTHORIZATION_REQUIRED: "需要人工授权",
        }
        counts = report.counts
        lines = [
            "澜琅动销中心实盘前检查报告",
            f"生成时间：{report.generated_at.isoformat(timespec='seconds')}",
            f"店铺 AppID：{report.store_app_id or '未指定'}",
            (
                "汇总："
                f"已通过 {counts['passed']}，"
                f"未通过 {counts['failed']}，"
                f"未验证 {counts['not_verified']}，"
                f"需要人工授权 {counts['manual_authorization_required']}"
            ),
            f"是否可放行：{'是' if report.release_ready else '否'}",
            "",
        ]
        for item in report.checks:
            lines.append(
                f"[{labels[item.state]}] {item.label}：{item.message}"
            )
            if item.detail:
                lines.append(f"  详情：{item.detail}")
        return "\n".join(lines)

    def _database_integrity_check(self) -> PreflightCheck:
        try:
            result = self.repository.integrity_check()
        except Exception as exc:
            return self._failed(
                "database_integrity",
                "数据库完整性",
                self._safe_error("数据库无法执行完整性检查", exc),
            )
        return PreflightCheck(
            code="database_integrity",
            label="数据库完整性",
            state=(
                PreflightState.PASSED
                if result == "ok"
                else PreflightState.FAILED
            ),
            message=(
                "SQLite integrity_check 返回 ok"
                if result == "ok"
                else f"SQLite 完整性检查返回异常：{result[:200]}"
            ),
        )

    def _migration_check(self) -> PreflightCheck:
        try:
            summary = self.repository.schema_migration_summary()
        except Exception as exc:
            return self._failed(
                "migration_version",
                "迁移版本",
                self._safe_error("无法读取迁移版本", exc),
            )
        passed = summary["latest"] == 6
        return PreflightCheck(
            code="migration_version",
            label="迁移版本",
            state=(
                PreflightState.PASSED
                if passed
                else PreflightState.FAILED
            ),
            message=(
                "已应用 v6 release candidate 迁移"
                if passed
                else f"当前迁移版本为 v{summary['latest']}，预期 v6"
            ),
            detail=str(summary["latest_name"]),
        )

    def _backup_check(
        self,
        backup: Path | None,
        error: str,
    ) -> PreflightCheck:
        if backup is None:
            return PreflightCheck(
                code="backup_available",
                label="数据库备份",
                state=PreflightState.FAILED,
                message=error or "未发现可用的自动备份或迁移备份",
            )
        return PreflightCheck(
            code="backup_available",
            label="数据库备份",
            state=PreflightState.PASSED,
            message="已找到最近一次非空、可读且完整性通过的数据库备份",
            detail=f"{backup.name} · {backup.stat().st_size} 字节",
        )

    def _restore_rehearsal_check(
        self,
        backup: Path | None,
    ) -> PreflightCheck:
        temporary_dir: Path | None = None
        if backup is None:
            return self._failed(
                "restore_rehearsal",
                "备份恢复演练",
                "没有有效备份可用于恢复演练",
            )
        try:
            temporary_dir = Path(tempfile.mkdtemp(
                prefix="langlang-preflight-restore-",
            ))
            restored = temporary_dir / "recovered.db"
            shutil.copy2(backup, restored)
            if self._sha256(backup) != self._sha256(restored):
                raise RuntimeError("恢复副本与备份文件校验值不一致")
            inspection = self._inspect_backup_database(restored)
            if not inspection["valid"]:
                raise RuntimeError(str(inspection["reason"]))
            return PreflightCheck(
                code="restore_rehearsal",
                label="备份恢复演练",
                state=PreflightState.PASSED,
                message=(
                    "真实备份已复制到独立临时目录，"
                    "完整性、必要表和迁移版本检查通过"
                ),
                detail=(
                    f"{backup.name} → recovered.db；"
                    f"migration=v{inspection['migration_version']}"
                ),
            )
        except Exception as exc:
            return self._failed(
                "restore_rehearsal",
                "备份恢复演练",
                self._safe_error("恢复演练失败", exc),
            )
        finally:
            if temporary_dir is not None:
                try:
                    shutil.rmtree(temporary_dir)
                except OSError:
                    pass

    def _find_latest_valid_backup(self) -> tuple[Path | None, str]:
        candidates = {
            path.resolve()
            for directory, pattern in (
                (self.backup_dir, "*.db"),
                (self.backup_dir, "*.bak"),
                (self.database_path.parent, "*.bak"),
            )
            for path in directory.glob(pattern)
            if path.is_file()
        }
        current = self.database_path.resolve()
        candidates.discard(current)
        rejected: list[str] = []
        for candidate in sorted(
            candidates,
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        ):
            inspection = self._inspect_backup_database(candidate)
            if inspection["valid"]:
                return candidate, ""
            rejected.append(
                f"{candidate.name}: {inspection['reason']}"
            )
        if rejected:
            return None, "没有有效备份；" + "；".join(rejected[:5])
        return None, "未发现可用的自动备份或迁移备份"

    @staticmethod
    def _inspect_backup_database(path: Path) -> dict[str, object]:
        try:
            if not path.is_file():
                return {"valid": False, "reason": "备份文件不存在"}
            size = path.stat().st_size
            if size <= 0:
                return {"valid": False, "reason": "备份文件为空"}
            conn = sqlite3.connect(
                f"{path.resolve().as_uri()}?mode=ro",
                uri=True,
                timeout=15,
            )
            try:
                integrity = conn.execute(
                    "PRAGMA integrity_check"
                ).fetchone()
                if integrity is None or str(integrity[0]) != "ok":
                    return {
                        "valid": False,
                        "reason": "备份完整性检查失败",
                    }
                _verify_required_tables(conn)
                row = conn.execute(
                    """
                    SELECT MAX(version)
                    FROM dongxiao_schema_migrations
                    """
                ).fetchone()
                version = int(row[0] or 0) if row else 0
                if version != DONGXIAO_MIGRATION_VERSION:
                    return {
                        "valid": False,
                        "reason": (
                            f"备份迁移版本 v{version} 与预期 "
                            f"v{DONGXIAO_MIGRATION_VERSION} 不兼容"
                        ),
                        "migration_version": version,
                    }
                return {
                    "valid": True,
                    "reason": "",
                    "migration_version": version,
                    "size": size,
                }
            finally:
                conn.close()
        except (OSError, sqlite3.Error) as exc:
            return {
                "valid": False,
                "reason": f"备份不可读或损坏：{exc}",
            }

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _scheduler_check(self) -> PreflightCheck:
        if self.runtime is None:
            return self._failed(
                "scheduler",
                "任务调度器",
                "动销 Runtime 未初始化，不能创建后台任务",
            )
        try:
            health = dict(self.runtime.scheduler_health())
        except Exception as exc:
            return self._failed(
                "scheduler",
                "任务调度器",
                self._safe_error("无法读取调度器健康状态", exc),
            )
        healthy = bool(health.get("healthy"))
        active = int(health.get("active_tasks") or 0)
        runnable = bool(health.get("runnable_tasks"))
        idle = bool(health.get("idle"))
        return PreflightCheck(
            code="scheduler",
            label="任务调度器",
            state=(
                PreflightState.PASSED
                if healthy
                else PreflightState.FAILED
            ),
            message=(
                (
                    "调度器空闲且没有待执行任务，启动时将按需启动"
                    if idle
                    else f"调度线程运行中，活动任务 {active} 个"
                )
                if healthy
                else (
                    "存在活动或待执行任务，但调度线程未运行"
                    if active or runnable
                    else "调度线程未运行"
                )
            ),
        )

    def _audit_check(self) -> PreflightCheck:
        try:
            health = self.repository.audit_log_health()
        except Exception as exc:
            return self._failed(
                "audit_log",
                "审计日志",
                self._safe_error("审计日志检查失败", exc),
            )
        return PreflightCheck(
            code="audit_log",
            label="审计日志",
            state=(
                PreflightState.PASSED
                if health["append_only"]
                else PreflightState.FAILED
            ),
            message=(
                f"只追加保护已启用，当前 {health['event_count']} 条事件"
                if health["append_only"]
                else "审计日志缺少只追加保护触发器"
            ),
        )

    def _write_guard_check(self) -> PreflightCheck:
        try:
            enabled = self.repository.platform_writes_enabled()
        except Exception as exc:
            return self._failed(
                "platform_write_guard",
                "平台写操作总开关",
                self._safe_error("无法读取平台写操作总开关", exc),
            )
        return PreflightCheck(
            code="platform_write_guard",
            label="平台写操作总开关",
            state=(
                PreflightState.FAILED
                if enabled
                else PreflightState.PASSED
            ),
            message=(
                "总开关当前开启；实盘前默认状态必须关闭"
                if enabled
                else "总开关已关闭，所有新的平台写操作均被阻止"
            ),
        )

    def _run_platform_call(
        self,
        *,
        code: str,
        label: str,
        call,
        client,
        validator,
    ) -> PreflightCheck:
        try:
            if callable(call):
                value = call()
                validator(value)
            metadata = client.last_call_metadata()
        except Exception as exc:
            metadata = (
                client.last_call_metadata()
                if hasattr(client, "last_call_metadata")
                else {}
            )
            return self._failed(
                code,
                f"{label}（官方 API）",
                self._safe_error("真实平台只读检查失败", exc),
                detail=self._request_detail(metadata, error=exc),
            )
        metadata = client.last_call_metadata()
        is_empty = (
            not bool(getattr(value, "ids", ()))
            if code == "product_api"
            else not bool(value)
        )
        return PreflightCheck(
            code=code,
            label=f"{label}（官方 API）",
            state=PreflightState.PASSED,
            message=(
                "正式官方客户端返回有效空列表"
                if is_empty
                else "正式官方客户端请求成功且必要字段格式正确"
            ),
            detail=self._request_detail(metadata),
        )

    @staticmethod
    def _request_detail(
        metadata: dict[str, object],
        *,
        error: BaseException | None = None,
    ) -> str:
        request_id = str(metadata.get("request_id") or "")
        if not request_id and isinstance(error, WechatApiError):
            request_id = error.request_id
        path = str(metadata.get("path") or "")
        errcode = metadata.get("errcode")
        return (
            f"request_id={request_id or '—'}；"
            f"path={path or '—'}；"
            f"errcode={errcode if errcode is not None else '—'}"
        )

    @staticmethod
    def _validate_product_page(value) -> None:
        if not hasattr(value, "ids") or not hasattr(value, "has_more"):
            raise ValueError("商品列表返回结构不完整")
        if not isinstance(value.ids, list):
            raise ValueError("商品列表 ids 字段格式错误")

    @staticmethod
    def _validate_id_list(value) -> None:
        if not isinstance(value, list):
            raise ValueError("列表接口没有返回有效数组")
        if any(not str(item).strip() for item in value):
            raise ValueError("列表接口返回空 ID")

    @staticmethod
    def _authorization_failure_state(error: BaseException) -> PreflightState:
        if (
            isinstance(error, WechatApiError)
            and error.category in {"authentication", "permission"}
        ):
            return PreflightState.MANUAL_AUTHORIZATION_REQUIRED
        text = str(error).casefold()
        if any(
            marker in text
            for marker in (
                "appsecret",
                "未配置",
                "授权",
                "token",
                "access_token",
            )
        ):
            return PreflightState.MANUAL_AUTHORIZATION_REQUIRED
        return PreflightState.FAILED

    @staticmethod
    def _safe_error(prefix: str, error: BaseException) -> str:
        text = str(error)
        text = re.sub(
            r"(?i)\b(access_token|token|appsecret|secret|password|"
            r"cookie|authorization)\b\s*[:=]\s*[^&;\s,]+",
            r"\1=[REDACTED]",
            text,
        )
        text = re.sub(r"https?://[^\s]+", "[URL]", text)
        return f"{prefix}：{type(error).__name__}: {text[:300]}"

    @staticmethod
    def _failed(
        code: str,
        label: str,
        message: str,
        *,
        detail: str = "",
    ) -> PreflightCheck:
        return PreflightCheck(
            code=code,
            label=label,
            state=PreflightState.FAILED,
            message=message,
            detail=detail,
        )
