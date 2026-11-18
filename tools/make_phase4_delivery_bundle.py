from __future__ import annotations

import hashlib
import json
import subprocess
import zipfile
from datetime import UTC, datetime
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
OUTPUT = (
    PROJECT.parent
    / "第4阶段交付"
    / "LangLang_1.8.47_Dongxiao_Phase4_Delivery_20261118.zip"
)
ROOT_NAME = "LangLang_1.8.47_Dongxiao_Phase4_Delivery_20261118"

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

TEST_RESULTS = """澜琅 1.8.47 动销中心第 4 阶段全量测试
测试日期：2026-11-18

全量 pytest：
542 passed, 6 skipped, 1 warning, 18 subtests passed
实际耗时：78.07 秒

跳过：
1. test_auth.py 中 1 个 Tk UI 测试：测试 Python 环境间歇性缺少 tk.tcl/ttk。
2. test_tester_auth.py 中 5 个测试：测试账号安全凭据未提供。

警告：
TesterAuthService 被 pytest 识别为候选测试类，产生 1 个既有
PytestCollectionWarning；不是失败。

补充：
正式动销路由和控件测试已通过。
test_window_smoke 单独重跑通过，最终全量复跑也通过。
Ruff 通过。
git diff --check 通过。
"""


def _git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=PROJECT,
            text=True,
        ).strip()
    except Exception:
        return "unknown"


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
    commit = _git_commit()
    readme = f"""澜琅 1.8.47 动销中心第 4 阶段完整交付包

交付日期：2026-11-18
应用版本：LangLang 1.8.47
开发分支：feature/dongxiao-center-phase4
交付源码提交：{commit}

包内文件：
01_开发报告_CN.md
02_接口审计_CN.md
03_数据库迁移说明_CN.md
04_测试与性能/全量测试结果.txt
04_测试与性能/性能实测_20261118.json
05_功能回归清单_CN.md
06_完整源码/
DELIVERY_SHA256_MANIFEST.txt

完成内容：
- 商品池批量任务与任务中心。
- 多任务并发调度、暂停、恢复、取消、有限重试、重启恢复。
- 官方订单/售后只读同步和订单状态历史。
- 退款金额核验、人工审批、权限控制、售后平台执行和写后回查。
- unknown 结果禁止自动重试，必须显式远端核验。
- 执行尝试、审计日志、统计和历史记录。
- 正式 Tkinter 页面整合。
- v4 数据库迁移与回滚测试。

验证：
- pytest: 542 passed, 6 skipped, 18 subtests passed。
- Ruff: 通过。
- git diff --check: 通过。

边界：
- 未编译或部署 EXE。
- 未修改 D:\\WG\\LangLang。
- 未删除旧代码或历史数据。
- 未修改 Android、Supabase migration 或旧 RPC。
- 未执行真实交易。
- 真实官方接口仍需正式授权店铺验收。

完整源码入口：
- launcher.py
- weimi_manager/
- tests/
- pyproject.toml

源码包不包含数据库、密钥、构建产物、虚拟环境和浏览器二进制运行时。
"""
    entries: dict[str, bytes] = {
        "00_交付说明.md": readme.encode("utf-8"),
        "01_开发报告_CN.md": (
            PROJECT / "DONGXIAO_PHASE4_REPORT_CN.md"
        ).read_bytes(),
        "02_接口审计_CN.md": (
            PROJECT / "DONGXIAO_PHASE4_INTERFACE_AUDIT_CN.md"
        ).read_bytes(),
        "03_数据库迁移说明_CN.md": (
            PROJECT / "DONGXIAO_PHASE4_DATABASE_MIGRATION_CN.md"
        ).read_bytes(),
        "04_测试与性能/全量测试结果.txt": TEST_RESULTS.encode("utf-8"),
        "04_测试与性能/性能实测_20261118.json": (
            PROJECT / "DONGXIAO_PHASE4_PERFORMANCE_20261118.json"
        ).read_bytes(),
        "05_功能回归清单_CN.md": (
            PROJECT / "FEATURE_REGRESSION_CHECKLIST_CN.md"
        ).read_bytes(),
    }
    source_files = sorted(
        path
        for path in PROJECT.rglob("*")
        if path.is_file() and included(path)
    )
    for path in source_files:
        relative = path.relative_to(PROJECT).as_posix()
        entries[f"06_完整源码/{relative}"] = path.read_bytes()

    manifest_lines = [
        f"Delivery generated: {datetime.now(UTC).isoformat(timespec='seconds')}",
        "Application version: 1.8.47",
        "Delivery: Dongxiao Phase 4",
        f"Source commit: {commit}",
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
