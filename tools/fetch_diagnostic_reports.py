"""Download, redact, classify and summarize tester diagnostics."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weimi_manager import __version__  # noqa: E402
from weimi_manager.auth_config import AuthConfig  # noqa: E402
from weimi_manager.auth_service import build_auth_service  # noqa: E402
from weimi_manager.config import AppPaths  # noqa: E402


SENSITIVE_KEY_PARTS = (
    "password",
    "secret",
    "token",
    "authorization",
    "cookie",
    "api_key",
    "apikey",
    "session_id",
    "account_id",
    "username",
    "email",
    "phone",
    "mobile",
    "address",
    "device_hash",
)
SENSITIVE_PATTERNS = (
    (re.compile(r"(?i)Bearer\s+\S+"), "Bearer [REDACTED]"),
    (re.compile(r"\beyJ[A-Za-z0-9._-]{20,}\b"), "[REDACTED_TOKEN]"),
    (re.compile(r"\bsb_[A-Za-z0-9_-]{16,}\b"), "[REDACTED_KEY]"),
    (re.compile(r"\bwx[0-9a-fA-F]{16}\b"), "[REDACTED_APPID]"),
    (re.compile(r"\b1[3-9]\d{9}\b"), "[REDACTED_PHONE]"),
    (
        re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b"),
        "[REDACTED_EMAIL]",
    ),
    (re.compile(r"\b[0-9a-fA-F]{32,}\b"), "[REDACTED_HEX]"),
)
HIGH_SEVERITIES = {"critical", "fatal", "high"}
HIGH_RISK_CATEGORIES = {
    "background_task",
    "crash",
    "data_loss",
    "database",
    "exception",
    "permission",
    "security",
    "thread",
    "tk_callback",
    "uncaught",
    "upload",
    "upload_unknown",
    "ui_stall",
    "webui",
}


def _supabase_url() -> str:
    value = os.environ.get("LANGLANG_SUPABASE_URL", "").strip()
    if value:
        return value.rstrip("/")
    try:
        payload = json.loads(
            (ROOT / "assets" / "auth_config.json").read_text(
                encoding="utf-8"
            )
        )
    except (OSError, ValueError):
        return ""
    return str(payload.get("supabase_url") or "").rstrip("/")


def redact_text(value: Any) -> str:
    text = str(value or "")
    for pattern, replacement in SENSITIVE_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def redact_value(value: Any, *, key: str = "") -> Any:
    normalized = key.casefold()
    if key and any(part in normalized for part in SENSITIVE_KEY_PARTS):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {
            str(item_key): redact_value(item, key=str(item_key))
            for item_key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


def _normalized_message(value: Any) -> str:
    text = redact_text(value).strip()
    text = re.sub(r"\b[0-9a-fA-F]{8,}\b", "<ID>", text)
    text = re.sub(r"\d+", "<N>", text)
    return text[:300]


def _data_candidates(override: Path | None) -> list[Path]:
    candidates: list[Path] = []
    if override is not None:
        candidates.append(override)
    env_data = os.environ.get("LANGLANG_DIAGNOSTIC_DATA_DIR", "").strip()
    if env_data:
        candidates.append(Path(env_data))
    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    if local_app_data:
        candidates.append(Path(local_app_data) / "LangLang" / "data")
    candidates.append(
        ROOT
        / "isolated"
        / "exe-realenv-20261123"
        / "LocalAppData"
        / "LangLang"
        / "data"
    )
    candidates.append(AppPaths.discover().data_dir)
    return candidates


def resolve_paths(data_dir: Path | None) -> AppPaths:
    for candidate in _data_candidates(data_dir):
        resolved = candidate.expanduser().resolve()
        auth_file = resolved.parent / "auth" / "saved_accounts.bin"
        if auth_file.is_file():
            base = AppPaths.discover()
            return AppPaths(
                project_dir=base.project_dir,
                resource_dir=base.resource_dir,
                data_dir=resolved,
                database=resolved / "weimi_manager.db",
            )
    raise FileNotFoundError(
        "未找到包含 auth/saved_accounts.bin 的用户数据目录"
    )


def saved_admin_token(paths: AppPaths, account_name: str) -> str:
    config = AuthConfig.load(paths)
    service = build_auth_service(
        config,
        paths.data_dir.parent / "auth",
    )
    if service is None:
        raise RuntimeError("AuthService 不可用")
    accounts = service.store.list_accounts()
    if not accounts:
        raise RuntimeError("没有可用于诊断取数的保存账户")
    saved = (
        service.store.find(account_name)
        if account_name
        else accounts[0]
    )
    if saved is None:
        raise RuntimeError(
            f"保存账户中找不到诊断管理员：{account_name}"
        )
    context = service.restore_account(saved)
    token = str(context.tokens.access_token or "").strip()
    if not token:
        raise RuntimeError("AuthService 未返回可用访问令牌")
    return token


def _version_tuple(value: str) -> tuple[int, ...]:
    parts = []
    for part in value.replace("-", ".").split("."):
        digits = "".join(char for char in part if char.isdigit())
        parts.append(int(digits or 0))
    return tuple(parts)


def _fixed_reason(report: dict[str, Any]) -> str:
    category = str(report.get("category") or "").casefold()
    message = redact_text(report.get("message"))
    lowered = message.casefold()
    if category == "tk_callback" and (
        "'popdown'" in lowered
        or "_refresh_cart_batches" in lowered
    ):
        return (
            "正式入口已切换 WebUI；旧 Tk 工作台和动销页面未进入正式包。"
        )
    if category == "uncaught" and "itemflag" in lowered:
        return "旧 Qt 控件标志写法已从当前正式 WebUI 路径移除。"
    if ".zip.part" in lowered and "另一个程序正在使用" in message:
        return (
            "更新器已改为 PID/UUID 独立临时文件，并有锁定文件回退测试。"
        )
    if "更新包长度超过服务器声明大小" in message:
        return "下载器已增加响应长度校验和独立重试测试。"
    if "teamservice.list_members() takes" in lowered:
        return "TeamService 调用签名已统一并由团队权限测试覆盖。"
    if "不支持业务命令：snapup_activities" in message:
        return "云命令映射已包含 snapup_activities，活动接口测试通过。"
    if "jwtexpired" in lowered or "jwt expired" in lowered:
        return "诊断/业务请求已接入会话刷新和重试路径。"
    if "unique constraint failed: stores.app_id" in lowered:
        return "店铺批量写入已改为按 AppID 更新或插入的去重路径。"
    return ""


def classify_report(
    report: dict[str, Any],
) -> tuple[str, str]:
    version = str(
        report.get("app_version")
        or report.get("version")
        or ""
    )
    if _version_tuple(version) == _version_tuple(__version__):
        return "still_present", "与当前版本一致，需人工复核"
    if version and _version_tuple(version) < _version_tuple(__version__):
        fixed = _fixed_reason(report)
        if fixed:
            return "fixed", fixed
        return "historical", "旧版本报告，当前版本未再次出现"
    return "unknown", "版本未知，需人工复核"


def _is_high_risk(report: dict[str, Any], status: str) -> bool:
    if status not in {"still_present", "unknown"}:
        return False
    severity = str(report.get("severity") or "").casefold()
    category = str(report.get("category") or "").casefold()
    if severity in HIGH_SEVERITIES:
        return True
    if category in HIGH_RISK_CATEGORIES:
        return True
    message = redact_text(report.get("message")).casefold()
    return any(
        marker in message
        for marker in (
            "traceback",
            "uncaught",
            "data loss",
            "corrupt",
            "权限",
            "崩溃",
            "数据丢失",
            "重复扣款",
        )
    )


def analyze_reports(
    reports: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], Counter[str], Counter[str]]:
    rows = []
    status_counts: Counter[str] = Counter()
    severity_counts: Counter[str] = Counter()
    for report in reports:
        status, resolution = classify_report(report)
        row = {
            "status": status,
            "severity": str(report.get("severity") or "unknown"),
            "category": str(report.get("category") or "unknown"),
            "message": _normalized_message(report.get("message")),
            "route": str(report.get("route") or ""),
            "page": str(report.get("page") or ""),
            "version": str(
                report.get("app_version")
                or report.get("version")
                or ""
            ),
            "resolution": resolution,
            "high_risk": _is_high_risk(report, status),
        }
        rows.append(row)
        status_counts[status] += 1
        severity_counts[row["severity"].casefold()] += 1
    return rows, status_counts, severity_counts


def write_review(
    reports: list[dict[str, Any]],
    output: Path,
) -> dict[str, Any]:
    rows, status_counts, severity_counts = analyze_reports(reports)
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(
        list
    )
    for row in rows:
        grouped[
            (row["status"], row["category"], row["message"])
        ].append(row)
    ordered = sorted(
        grouped.items(),
        key=lambda item: (
            {"still_present": 0, "unknown": 1, "historical": 2}.get(
                item[0][0],
                3,
            ),
            -len(item[1]),
        ),
    )
    high_risk = [row for row in rows if row["high_risk"]]
    lines = [
        f"# 澜琅 {__version__} 远端诊断审查",
        "",
        f"- 报告总数：{len(reports)}",
        f"- 当前版本仍存在：{status_counts['still_present']}",
        f"- 已修复：{status_counts['fixed']}",
        f"- 历史版本/环境或预期问题：{status_counts['historical']}",
        f"- 无法确定版本：{status_counts['unknown']}",
        f"- 高危门禁项：{len(high_risk)}",
        "",
        "## 严重度",
        "",
    ]
    for severity, count in severity_counts.most_common():
        lines.append(f"- {severity}: {count}")
    lines.extend(
        [
            "",
            "## 分组",
            "",
            "| 分类 | 结论 | 次数 | 版本 | 页面 | 信息 | 当前判断 |",
            "| --- | --- | ---: | --- | --- | --- | --- |",
        ]
    )
    for (status, category, message), items in ordered:
        versions = ", ".join(
            sorted({item["version"] for item in items if item["version"]})
        )
        routes = Counter(
            item["route"] or item["page"]
            for item in items
            if item["route"] or item["page"]
        )
        route = routes.most_common(1)[0][0] if routes else ""
        resolutions = Counter(
            item["resolution"] for item in items if item["resolution"]
        )
        resolution = resolutions.most_common(1)[0][0]
        lines.append(
            f"| {category} | {status} | {len(items)} | {versions} | "
            f"{route.replace('|', '/')} | "
            f"{message.replace('|', '/')} | "
            f"{resolution.replace('|', '/')} |"
        )
    lines.extend(["", "## 发布门禁", ""])
    if high_risk:
        lines.append(
            "存在当前版本高危诊断，禁止发布；"
            "修复并重新收集诊断后才可继续。"
        )
        for row in high_risk[:50]:
            lines.append(
                f"- [{row['category']}] {row['message']}"
            )
    else:
        lines.append("未发现当前版本高危诊断。")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {
        "report_count": len(reports),
        "status_counts": dict(status_counts),
        "severity_counts": dict(severity_counts),
        "high_risk": len(high_risk),
        "output": str(output),
    }


def _safe_raw_path(
    raw_output: Path | None,
    raw_dir: Path,
) -> Path:
    if raw_output is None:
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        raw_output = raw_dir / f"diagnostics-{stamp}.json"
    target = raw_output.resolve()
    forbidden = (ROOT / "release").resolve()
    try:
        target.relative_to(forbidden)
    except ValueError:
        pass
    else:
        raise ValueError("脱敏诊断 JSON 不允许写入 release 目录")
    return target


def _fetch(
    *,
    base_url: str,
    token: str,
    service_key: str,
    limit: int,
) -> list[dict[str, Any]]:
    if service_key:
        response = requests.get(
            f"{base_url}/rest/v1/diagnostic_reports",
            params={
                "select": "*",
                "order": "created_at.desc",
                "limit": limit,
            },
            headers={
                "apikey": service_key,
                "Authorization": f"Bearer {service_key}",
            },
            timeout=(5, 45),
        )
    else:
        response = requests.get(
            f"{base_url}/functions/v1/list-diagnostic-reports",
            params={"limit": limit},
            headers={"Authorization": f"Bearer {token}"},
            timeout=(5, 45),
        )
    if not response.ok:
        raise RuntimeError(
            f"诊断接口返回 HTTP {response.status_code}"
        )
    payload = response.json()
    reports = (
        payload.get("reports")
        if isinstance(payload, dict)
        else payload
    )
    if not isinstance(reports, list):
        raise RuntimeError("诊断接口返回格式不正确")
    return [item for item in reports if isinstance(item, dict)]


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "release" / "DIAGNOSTIC_REVIEW.md",
    )
    parser.add_argument(
        "--raw-dir",
        type=Path,
        default=ROOT / "artifacts" / "diagnostic-reports",
    )
    parser.add_argument("--raw-output", type=Path)
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--input-json", type=Path)
    parser.add_argument("--account", default="zpfly")
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument(
        "--no-gate",
        action="store_true",
        help="Generate the review without failing on current high-risk errors.",
    )
    args = parser.parse_args(argv)

    base_url = _supabase_url()
    if not base_url:
        raise RuntimeError("assets/auth_config.json 缺少 Supabase URL")
    if args.input_json:
        loaded = json.loads(
            args.input_json.read_text(encoding="utf-8-sig")
        )
        if not isinstance(loaded, list):
            raise RuntimeError("--input-json 必须是报告数组")
        reports = [
            item
            for item in loaded
            if isinstance(item, dict)
        ]
    else:
        paths = resolve_paths(args.data_dir)
        admin_token = os.environ.get(
            "LANGLANG_DIAGNOSTIC_ADMIN_TOKEN",
            "",
        ).strip()
        if not admin_token:
            admin_token = saved_admin_token(paths, args.account)
        service_key = (
            os.environ.get(
                "LANGLANG_DIAGNOSTIC_SERVICE_ROLE_KEY",
                "",
            ).strip()
            or os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
        )
        reports = _fetch(
            base_url=base_url,
            token=admin_token,
            service_key=service_key,
            limit=max(1, min(500, args.limit)),
        )
    redacted = [
        redact_value(report)
        for report in reports
    ]
    raw_path = _safe_raw_path(args.raw_output, args.raw_dir)
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_text(
        json.dumps(redacted, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    result = write_review(redacted, args.output)
    result["raw_output"] = str(raw_path)
    result["raw_sha256"] = _file_sha256(raw_path)
    result["review_sha256"] = _file_sha256(args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["high_risk"] and not args.no_gate:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
