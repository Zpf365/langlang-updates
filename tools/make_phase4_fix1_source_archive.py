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
    / "第4阶段Fix1交付"
    / "LangLang_1.8.47_Dongxiao_Phase4_Fix1_Source.zip"
)
ROOT_NAME = "LangLang_1.8.47_Dongxiao_Phase4_Fix1_Source"

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

TEST_RESULTS = """澜琅 1.8.47 动销中心第 4 阶段 Fix1 全量测试
测试日期：2026-11-18

Fix1 专项：
112 passed, 466 deselected, 1 warning

全量：
571 passed, 7 skipped, 17 warnings, 18 subtests passed
耗时：120.52 秒

跳过：
- 2 个 Tk UI 测试：当前 uv Python 环境缺少 Tk/Tcl 资源。
- 5 个测试账号凭据测试：发行安全基线不包含测试凭据。

警告：
- 1 个 TesterAuthService 测试收集警告。
- 16 个 Tk 变量在主循环关闭后清理产生的测试环境警告。

Ruff：通过
git diff --check：通过
"""


def _git_output(*args: str) -> str:
    try:
        return subprocess.check_output(
            ["git", *args],
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
    branch = _git_output("branch", "--show-current")
    commit = _git_output("rev-parse", "HEAD")
    readme = f"""澜琅 1.8.47 动销中心第 4 阶段 Fix1 完整源码包

交付日期：2026-11-18
基线提交：bfcb2015397e4d8c80ec402058ae1f3c07eff85e
Fix1 分支：{branch}
Fix1 源码提交：{commit}

包内文件：
01_Fix1修复报告_CN.md
02_安全审计_CN.md
03_数据库迁移说明_CN.md
04_性能对比_20261118.json
05_全量测试结果.txt
06_功能回归清单_CN.md
07_完整源码/
FIX1_SHA256_MANIFEST.txt

本包只用于源码审查，不包含数据库、真实凭据、构建产物或浏览器运行时。

重要边界：
- 未修改 D:\\WG\\LangLang。
- 未编译、未部署、未发布。
- 未执行真实退款或真实店铺写操作。
- 未修改 Android、Supabase、旧 RPC。
- 官方当前售后接口不支持指定部分退款金额，Fix1 禁止本地部分金额执行。
"""
    entries: dict[str, bytes] = {
        "00_Fix1交付说明.md": readme.encode("utf-8"),
        "01_Fix1修复报告_CN.md": (
            PROJECT / "DONGXIAO_PHASE4_FIX1_REPORT_CN.md"
        ).read_bytes(),
        "02_安全审计_CN.md": (
            PROJECT / "DONGXIAO_PHASE4_FIX1_SAFETY_AUDIT_CN.md"
        ).read_bytes(),
        "03_数据库迁移说明_CN.md": (
            PROJECT / "DONGXIAO_PHASE4_FIX1_DATABASE_MIGRATION_CN.md"
        ).read_bytes(),
        "04_性能对比_20261118.json": (
            PROJECT / "DONGXIAO_PHASE4_FIX1_PERFORMANCE_20261118.json"
        ).read_bytes(),
        "05_全量测试结果.txt": TEST_RESULTS.encode("utf-8"),
        "06_功能回归清单_CN.md": (
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
        entries[f"07_完整源码/{relative}"] = path.read_bytes()

    manifest_lines = [
        f"Delivery generated: {datetime.now(UTC).isoformat(timespec='seconds')}",
        "Application version: 1.8.47",
        "Delivery: Dongxiao Phase 4 Fix1 Source",
        f"Branch: {branch}",
        f"Commit: {commit}",
        "",
    ]
    manifest_lines.extend(
        f"{hashlib.sha256(data).hexdigest()}  {name}"
        for name, data in sorted(entries.items())
    )
    entries["FIX1_SHA256_MANIFEST.txt"] = (
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
                "branch": branch,
                "commit": commit,
                "integrity": "ok",
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
