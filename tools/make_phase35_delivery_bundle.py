from __future__ import annotations

import hashlib
import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
OUTPUT = (
    PROJECT.parent
    / "第3.5阶段交付"
    / "LangLang_1.8.47_Dongxiao_Phase3_5_Delivery_20261118.zip"
)
ROOT_NAME = "LangLang_1.8.47_Dongxiao_Phase3_5_Delivery_20261118"

EXCLUDED_DIRS = {
    ".git",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    ".venv311",
    "__pycache__",
    "browser_runtime",
    "build",
    "data",
    "dist",
    "node_modules",
    "release",
}
EXCLUDED_FILES = {
    ".env",
    "SOURCE_SHA256_MANIFEST.txt",
    "assets/auth_config.json",
    "assets/tester_accounts.json",
    "assets/universal_login.json",
}
EXCLUDED_SUFFIXES = {
    ".db",
    ".db-shm",
    ".db-wal",
    ".key",
    ".log",
    ".part",
    ".pem",
    ".pfx",
    ".pyc",
    ".sqlite",
    ".sqlite3",
    ".tmp",
}

PERFORMANCE = {
    "tested_at": "2026-11-18T12:24:34+00:00",
    "timezone": "UTC",
    "database": "temporary isolated SQLite",
    "catalog_results": {
        "1000": {
            "write_ms": 552.81,
            "pagination_query_ms": 50.93,
            "multi_condition_filter_ms": 6.43,
            "sku_rule_10000_calls_ms": 302.47,
            "sku_rule_per_call_us": 30.25,
            "csv_export_ms": 40.48,
            "peak_python_bytes": 3351459,
            "peak_rss_delta_bytes": 8114176,
            "errors": 0,
        },
        "10000": {
            "write_ms": 5949.89,
            "pagination_query_ms": 70.90,
            "multi_condition_filter_ms": 21.31,
            "sku_rule_10000_calls_ms": 318.72,
            "sku_rule_per_call_us": 31.87,
            "csv_export_ms": 282.83,
            "peak_python_bytes": 24939745,
            "peak_rss_delta_bytes": 60813312,
            "errors": 0,
        },
    },
    "ui_responsiveness": {
        "products": 2000,
        "sync_ms": 1658.53,
        "event_loop_ticks": 107,
        "max_gap_ms": 19.63,
        "p95_gap_ms": 16.44,
        "errors": 0,
    },
}

TEST_RESULTS = """澜琅 1.8.47 动销中心第 3.5 阶段测试结果
测试日期：2026-11-18

动销专项：
63 passed, 466 deselected, 1 warning, 18 subtests passed

全量测试：
522 passed, 7 skipped, 1 warning, 18 subtests passed
耗时：72.19 秒

静态检查：
Ruff：通过
git diff --check：通过

测试环境：
- Python 3.12.13
- pytest 9.2.0
- Windows
- 临时隔离 SQLite，不接触真实用户数据库

跳过说明：
- 当前测试 Python 环境缺少部分 Tk 资源。
- 测试账号安全凭据不可获得的既有条件。

动销入口、权限、产品目录、迁移回滚、同步一致性、SKU 规则、
CSV 安全和数据库隔离测试均已实际执行。
"""

README = """澜琅 1.8.47 动销中心第 3.5 阶段交付包

交付日期：2026-11-18
版本基线：LangLang 1.8.47
开发分支：feature/dongxiao-center-phase3-5
阶段提交：cddf708

包内文件：
01_验收报告_CN.md
02_接口审计_CN.md
03_功能回归清单_CN.md
04_测试与性能/测试结果_20261118.txt
04_测试与性能/性能实测_20261118.json
05_完整源码/
DELIVERY_SHA256_MANIFEST.txt

完整源码入口：
- launcher.py
- weimi_manager/
- tests/
- pyproject.toml

本阶段交付内容：
- 真实微信小店商品接口专项审查。
- 商品和 SKU 当前状态、历史快照及目录缺失隔离。
- 分页失败、详情失败、并发导入和跨店铺同步保护。
- 官方销量、SKU 状态、限购和未验证起购规则处理。
- CSV Excel 公式注入防护及可逆重新导入。
- 实时、本地缓存、可能失效和 Mock 数据来源区分。
- 微信小店分享文本人工关联授权商品。
- v3 独立数据库迁移、重复执行及失败回滚测试。

验收结果：
- 全量测试：522 passed, 7 skipped, 18 subtests passed。
- 动销专项：63 passed。
- Ruff：通过。
- git diff --check：通过。

重要边界：
- 未编译或部署 EXE。
- 未修改正式部署目录 D:\\WG\\LangLang。
- 未修改 Android 客户端、Supabase migration 或旧 RPC。
- 未删除旧动销模块和历史数据。
- 未执行真实购物车、下单、支付或退款。
- 未使用真实店铺执行官方接口验收；该事项仍需正式授权环境验证。

完整源码包不包含：
- 数据库和密钥。
- 构建产物。
- 浏览器二进制运行时。
- Python 虚拟环境。

恢复说明：
解压 05_完整源码 后使用 Python 3.11-3.13 安装项目依赖。
数据库 v3 迁移前的自动备份名称为：
<数据库文件名>.pre-dongxiao-v3.bak
"""


def included(path: Path) -> bool:
    relative = path.relative_to(PROJECT)
    if any(part in EXCLUDED_DIRS for part in relative.parts):
        return False
    if relative.as_posix() in EXCLUDED_FILES:
        return False
    if relative.name == ".env" or relative.name.startswith(".env."):
        return False
    return path.suffix.casefold() not in EXCLUDED_SUFFIXES


def main() -> int:
    entries: dict[str, bytes] = {
        "00_交付说明.md": README.encode("utf-8"),
        "01_验收报告_CN.md": (
            PROJECT / "DONGXIAO_PHASE3_5_REPORT_CN.md"
        ).read_bytes(),
        "02_接口审计_CN.md": (
            PROJECT / "DONGXIAO_PHASE3_5_INTERFACE_AUDIT_CN.md"
        ).read_bytes(),
        "03_功能回归清单_CN.md": (
            PROJECT / "FEATURE_REGRESSION_CHECKLIST_CN.md"
        ).read_bytes(),
        "04_测试与性能/测试结果_20261118.txt": TEST_RESULTS.encode(
            "utf-8"
        ),
        "04_测试与性能/性能实测_20261118.json": (
            json.dumps(
                PERFORMANCE,
                ensure_ascii=False,
                indent=2,
            )
            + "\n"
        ).encode("utf-8"),
    }
    source_files = sorted(
        path
        for path in PROJECT.rglob("*")
        if path.is_file() and included(path)
    )
    for path in source_files:
        relative = path.relative_to(PROJECT).as_posix()
        entries[f"05_完整源码/{relative}"] = path.read_bytes()

    manifest_lines = [
        f"Delivery generated: {datetime.now(UTC).isoformat(timespec='seconds')}",
        "Application version: 1.8.47",
        "Delivery: Dongxiao Phase 3.5",
        "",
    ]
    manifest_lines.extend(
        f"{hashlib.sha256(data).hexdigest()}  {name}"
        for name, data in sorted(entries.items())
    )
    entries["DELIVERY_SHA256_MANIFEST.txt"] = (
        "\n".join(manifest_lines) + "\n"
    ).encode("utf-8")

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.unlink(missing_ok=True)
    with zipfile.ZipFile(
        OUTPUT,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as bundle:
        for name, data in sorted(entries.items()):
            bundle.writestr(f"{ROOT_NAME}/{name}", data)

    with zipfile.ZipFile(OUTPUT) as bundle:
        bad_entry = bundle.testzip()
        if bad_entry is not None:
            raise RuntimeError(f"ZIP integrity failure: {bad_entry}")
        names = bundle.namelist()
        forbidden_markers = (
            "/.git/",
            "/build/",
            "/dist/",
            "/release/",
            "/browser_runtime/",
            "/.env",
            "/assets/auth_config.json",
            "/assets/tester_accounts.json",
            "/assets/universal_login.json",
        )
        forbidden = [
            name
            for name in names
            if any(marker in name for marker in forbidden_markers)
        ]
        if forbidden:
            raise RuntimeError(
                f"Forbidden archive entries: {forbidden[:5]}"
            )

    digest = hashlib.sha256(OUTPUT.read_bytes()).hexdigest()
    print(
        json.dumps(
            {
                "archive": str(OUTPUT),
                "size_bytes": OUTPUT.stat().st_size,
                "file_count": len(entries),
                "sha256": digest,
                "integrity": "ok",
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
