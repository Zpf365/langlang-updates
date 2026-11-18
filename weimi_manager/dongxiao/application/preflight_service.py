from __future__ import annotations

import re
import sqlite3
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Callable

from ..domain.operations import (
    PreflightCheck,
    PreflightReport,
    PreflightState,
)
from ..domain.rules import require_permission
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
        checks = [
            self._database_integrity_check(),
            self._migration_check(),
            self._backup_check(),
            self._restore_rehearsal_check(),
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
                message="尚未执行真实平台只读检查",
            ),
            PreflightCheck(
                code="order_api",
                label="订单接口",
                state=PreflightState.NOT_VERIFIED,
                message="尚未执行真实平台只读检查",
            ),
            PreflightCheck(
                code="aftersale_read_api",
                label="售后读取接口",
                state=PreflightState.NOT_VERIFIED,
                message="尚未执行真实平台只读检查",
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
        try:
            client = self.sync_service.client_for(store)
            token = client.get_access_token()
            if not token:
                raise RuntimeError("授权接口未返回有效 Token")
            checks.append(
                PreflightCheck(
                    code="store_authorization",
                    label="店铺授权",
                    state=PreflightState.PASSED,
                    message="稳定版 Token 获取成功；未记录 Token 内容",
                )
            )
        except Exception as exc:
            state = self._authorization_failure_state(exc)
            checks.append(
                PreflightCheck(
                    code="store_authorization",
                    label="店铺授权",
                    state=state,
                    message=self._safe_error("店铺授权检查失败", exc),
                )
            )

        if client is None:
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
                        message="店铺授权未通过，未发起真实平台只读请求",
                    )
                )
        else:
            checks.append(self._run_platform_call(
                code="product_api",
                label="商品接口",
                call=lambda: client.fetch_product_id_page(page_size=1),
            ))
            checks.append(self._run_platform_call(
                code="order_api",
                label="订单接口",
                call=lambda: client.fetch_order_ids(
                    start_time,
                    end_time,
                    None,
                ),
            ))
            checks.append(self._run_platform_call(
                code="aftersale_read_api",
                label="售后读取接口",
                call=lambda: client.fetch_after_sale_ids(
                    start_time,
                    end_time,
                    time_field="update",
                ),
            ))
        return PreflightReport(
            generated_at=now,
            checks=tuple(checks),
            store_app_id=str(getattr(store, "app_id", "") or ""),
        )

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

    def _backup_check(self) -> PreflightCheck:
        candidates = list(self.backup_dir.glob("*.db"))
        candidates.extend(self.database_path.parent.glob("*.bak"))
        candidates = [item for item in candidates if item.is_file()]
        if not candidates:
            return PreflightCheck(
                code="backup_available",
                label="数据库备份",
                state=PreflightState.FAILED,
                message="未发现可用的自动备份或迁移备份",
                detail=str(self.backup_dir),
            )
        latest = max(candidates, key=lambda item: item.stat().st_mtime)
        return PreflightCheck(
            code="backup_available",
            label="数据库备份",
            state=PreflightState.PASSED,
            message="已发现可用数据库备份",
            detail=f"{latest.name} · {latest.stat().st_size} 字节",
        )

    def _restore_rehearsal_check(self) -> PreflightCheck:
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                prefix="langlang-preflight-restore-",
                suffix=".db",
                delete=False,
                dir=str(self.database_path.parent),
            ) as handle:
                temporary = Path(handle.name)
            source = sqlite3.connect(self.database_path, timeout=15)
            destination = sqlite3.connect(temporary, timeout=15)
            try:
                source.backup(destination)
                result = destination.execute(
                    "PRAGMA integrity_check"
                ).fetchone()
            finally:
                destination.close()
                source.close()
            if result is None or str(result[0]) != "ok":
                raise RuntimeError("恢复副本完整性检查未通过")
            return PreflightCheck(
                code="restore_rehearsal",
                label="备份恢复演练",
                state=PreflightState.PASSED,
                message="隔离临时副本恢复及完整性检查成功",
            )
        except Exception as exc:
            return self._failed(
                "restore_rehearsal",
                "备份恢复演练",
                self._safe_error("恢复演练失败", exc),
            )
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass

    def _scheduler_check(self) -> PreflightCheck:
        scheduler = getattr(self.runtime, "scheduler", None)
        if scheduler is None:
            return self._failed(
                "scheduler",
                "任务调度器",
                "动销 Runtime 未初始化，不能创建后台任务",
            )
        try:
            healthy = bool(scheduler.is_healthy())
            active = int(scheduler.active_task_count())
        except Exception as exc:
            return self._failed(
                "scheduler",
                "任务调度器",
                self._safe_error("无法读取调度器健康状态", exc),
            )
        return PreflightCheck(
            code="scheduler",
            label="任务调度器",
            state=(
                PreflightState.PASSED
                if healthy
                else PreflightState.FAILED
            ),
            message=(
                f"调度线程运行中，活动任务 {active} 个"
                if healthy
                else "调度线程未运行，禁止创建无法执行的后台任务"
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
    ) -> PreflightCheck:
        try:
            if callable(call):
                call()
        except Exception as exc:
            return self._failed(
                code,
                label,
                self._safe_error("真实平台只读检查失败", exc),
            )
        return PreflightCheck(
            code=code,
            label=label,
            state=PreflightState.PASSED,
            message="真实平台只读请求成功",
        )

    @staticmethod
    def _authorization_failure_state(error: BaseException) -> PreflightState:
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
    ) -> PreflightCheck:
        return PreflightCheck(
            code=code,
            label=label,
            state=PreflightState.FAILED,
            message=message,
        )
