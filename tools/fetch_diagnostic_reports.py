"""Download and group tester diagnostics before a release."""

from __future__ import annotations

import argparse
import json
import os
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import requests


ROOT = Path(__file__).resolve().parent.parent


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


def _normalized_message(value: Any) -> str:
    text = str(value or "").strip()
    text = re.sub(r"\b[0-9a-fA-F]{8,}\b", "<ID>", text)
    text = re.sub(r"\d+", "<N>", text)
    return text[:240]


def _write_review(reports: list[dict[str, Any]], output: Path) -> None:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for report in reports:
        grouped[
            (
                str(report.get("category") or "unknown"),
                _normalized_message(report.get("message")),
            )
        ].append(report)
    ordered = sorted(grouped.items(), key=lambda item: len(item[1]), reverse=True)
    lines = [
        "# 测试诊断问题汇总",
        "",
        f"- 报告总数：{len(reports)}",
        f"- 问题类型：{len(ordered)}",
        "",
        "| 次数 | 类型 | 版本 | 页面 | 信息 |",
        "| ---: | --- | --- | --- | --- |",
    ]
    for (category, message), rows in ordered:
        versions = ", ".join(
            sorted(
                {
                    str(row.get("app_version") or "")
                    for row in rows
                    if row.get("app_version")
                }
            )
        )
        routes = Counter(str(row.get("route") or "") for row in rows)
        route = routes.most_common(1)[0][0] if routes else ""
        lines.append(
            f"| {len(rows)} | {category} | {versions} | {route} | "
            f"{message.replace('|', '/')} |"
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "release" / "DIAGNOSTIC_REVIEW.md",
    )
    parser.add_argument("--limit", type=int, default=500)
    args = parser.parse_args()

    base_url = _supabase_url()
    service_key = (
        os.environ.get(
            "LANGLANG_DIAGNOSTIC_SERVICE_ROLE_KEY",
            "",
        ).strip()
        or os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    )
    admin_token = os.environ.get(
        "LANGLANG_DIAGNOSTIC_ADMIN_TOKEN",
        "",
    ).strip()
    if not base_url or not (service_key or admin_token):
        print(
            "需要 LANGLANG_SUPABASE_URL 和 "
            "LANGLANG_DIAGNOSTIC_ADMIN_TOKEN 或 "
            "LANGLANG_DIAGNOSTIC_SERVICE_ROLE_KEY 才能提取远端诊断。"
        )
        return 2
    limit = max(1, min(500, args.limit))
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
            timeout=(5, 30),
        )
    else:
        response = requests.get(
            f"{base_url}/functions/v1/list-diagnostic-reports",
            params={"limit": limit},
            headers={"Authorization": f"Bearer {admin_token}"},
            timeout=(5, 30),
        )
    response.raise_for_status()
    payload = response.json()
    reports = payload.get("reports") if isinstance(payload, dict) else payload
    if not isinstance(reports, list):
        raise RuntimeError("诊断接口返回格式不正确")
    _write_review(reports, args.output)
    raw_output = args.output.with_suffix(".json")
    raw_output.write_text(
        json.dumps(reports, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"已提取 {len(reports)} 条诊断：{args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
