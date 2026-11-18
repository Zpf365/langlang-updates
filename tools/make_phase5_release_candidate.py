from __future__ import annotations

import hashlib
import json
import subprocess
import zipfile
from datetime import UTC, datetime
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
BASELINE = "e95b61bcd97ea6806abc5a1fd5b4d3b38aa38655"
ROOT_NAME = "LangLang_1.8.47_Dongxiao_Phase5_Release_Candidate"
OUTPUT = (
    PROJECT.parent
    / "第5阶段交付"
    / f"{ROOT_NAME}.zip"
)
CANDIDATE = PROJECT / "artifacts" / "phase5" / "candidate_dist" / "LangLang"

EXCLUDED_DIRS = {
    ".git",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    ".venv311",
    "__pycache__",
    "artifacts",
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
    "assets/tester_channel.json",
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

REPORT_FILES = (
    "DONGXIAO_PHASE5_REPORT_CN.md",
    "DONGXIAO_PHASE5_FIX1_REVIEW_CN.md",
    "DONGXIAO_PHASE5_INTERFACE_AUDIT_CN.md",
    "DONGXIAO_PHASE5_DATABASE_MIGRATION_CN.md",
    "DONGXIAO_PHASE5_SAFETY_AUDIT_CN.md",
    "DONGXIAO_PHASE5_STRESS_REPORT_CN.md",
    "DONGXIAO_PHASE5_TEST_REPORT_CN.md",
    "DONGXIAO_PHASE5_PREFLIGHT_REPORT_CN.md",
    "FEATURE_REGRESSION_CHECKLIST_CN.md",
)

EVIDENCE_FILES = (
    "artifacts/phase5/pytest-full.log",
    "artifacts/phase5/stress-report.json",
    "artifacts/phase5/stress-output.log",
    "artifacts/phase5/phase4-baseline-rerun.json",
    "artifacts/phase5/pyinstaller-build.log",
    "artifacts/phase5/preflight-local-output.txt",
    "artifacts/phase5/candidate_automation_self_test.log",
    "artifacts/phase5/candidate_automation_self_test.json",
    "artifacts/phase5/candidate_build_check.log",
    "artifacts/phase5/candidate_smoke.log",
    "artifacts/phase5/browser_smoke.log",
    "artifacts/phase5/ruff.log",
    "artifacts/phase5/ruff-changed-files.log",
    "artifacts/phase5/candidate_build/LangLang/warn-LangLang.txt",
)


def _git(*args: str) -> str:
    return subprocess.check_output(
        ["git", *args],
        cwd=PROJECT,
        text=True,
        encoding="utf-8",
    ).strip()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _included_source(path: Path) -> bool:
    relative = path.relative_to(PROJECT)
    if any(part in EXCLUDED_DIRS for part in relative.parts):
        return False
    if relative.as_posix() in EXCLUDED_FILES:
        return False
    if path.name == ".env" or path.name.startswith(".env."):
        return False
    return path.suffix.casefold() not in EXCLUDED_SUFFIXES


def _git_report() -> tuple[str, str]:
    branch = _git("branch", "--show-current")
    commit = _git("rev-parse", "HEAD")
    status = _git("status", "--short") or "clean"
    log = _git("log", "--oneline", "--decorate", f"{BASELINE}..HEAD")
    diff = _git("diff", "--name-status", f"{BASELINE}..HEAD")
    show = _git("show", "--stat", "--oneline", "--decorate", "HEAD")
    version = f"""# 第 5 阶段 Git 与版本信息

- 基线提交：{BASELINE}
- 当前分支：{branch}
- Phase 5 提交：{commit}
- 工作树：{status}

## 相对 Fix1 的提交

```text
{log}
```

## 当前提交摘要

```text
{show}
```
"""
    modifications = f"""# 第 5 阶段修改文件清单

基线：{BASELINE}  
当前 HEAD：{commit}

```text
{diff}
```

注：交付 ZIP、测试日志、候选 EXE 和压力报告属于交付证据，不在 Git
源码提交内；它们均在最终 SHA256 清单中有独立校验值。
"""
    return version, modifications


def main() -> int:
    if not (CANDIDATE / "LangLang.exe").is_file():
        raise FileNotFoundError("候选 EXE 不存在，不能生成最终交付包")

    branch = _git("branch", "--show-current")
    commit = _git("rev-parse", "HEAD")
    if commit == BASELINE:
        raise RuntimeError("Phase 5 尚未提交，拒绝生成最终交付包")

    readme = f"""# 澜琅 1.8.47 动销中心第 5 阶段实盘候选交付

交付时间：{datetime.now(UTC).isoformat(timespec='seconds')}

- 基线：{BASELINE}
- Phase 5 分支：{branch}
- Phase 5 提交：{commit}
- 软件版本：1.8.47
- 数据库迁移：v6

目录：

- `01_完整源码/`：完整可审查源码
- `02_候选程序/`：隔离构建的 Windows 候选程序
- `03_测试与压力原始结果/`：实际执行输出
- 根目录：开发、复核、接口、数据库、安全、测试、压力、实盘前检查报告

边界：未部署、未发布、未覆盖 `D:\\WG\\LangLang`，未执行真实店铺写操作。
"""
    in_memory: dict[str, bytes] = {
        "00_交付说明.md": readme.encode("utf-8"),
    }
    for name in REPORT_FILES:
        path = PROJECT / name
        if not path.is_file():
            raise FileNotFoundError(path)
        in_memory[name] = path.read_bytes()
    git_report, modification_report = _git_report()
    in_memory["DONGXIAO_PHASE5_GIT_VERSION_CN.md"] = (
        git_report.encode("utf-8")
    )
    in_memory["DONGXIAO_PHASE5_MODIFICATION_LIST_CN.md"] = (
        modification_report.encode("utf-8")
    )

    file_entries: dict[str, Path] = {}
    for path in sorted(PROJECT.rglob("*")):
        if path.is_file() and _included_source(path):
            file_entries[
                f"01_完整源码/{path.relative_to(PROJECT).as_posix()}"
            ] = path
    for path in sorted(CANDIDATE.rglob("*")):
        if path.is_file():
            file_entries[
                f"02_候选程序/LangLang/{path.relative_to(CANDIDATE).as_posix()}"
            ] = path
    for relative_name in EVIDENCE_FILES:
        path = PROJECT / relative_name
        if path.is_file():
            file_entries[
                f"03_测试与压力原始结果/{path.name}"
            ] = path

    manifest_lines = [
        f"generated_at={datetime.now(UTC).isoformat(timespec='seconds')}",
        f"baseline={BASELINE}",
        f"head={commit}",
        f"branch={branch}",
        "",
    ]
    for name, path in sorted(file_entries.items()):
        manifest_lines.append(f"{_sha256(path)}  {name}")
    for name, payload in sorted(in_memory.items()):
        manifest_lines.append(
            f"{hashlib.sha256(payload).hexdigest()}  {name}"
        )
    manifest = ("\n".join(manifest_lines) + "\n").encode("utf-8")

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.unlink(missing_ok=True)
    with zipfile.ZipFile(
        OUTPUT,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=6,
    ) as archive:
        for name, payload in sorted(in_memory.items()):
            archive.writestr(f"{ROOT_NAME}/{name}", payload)
        for name, path in sorted(file_entries.items()):
            archive.write(path, f"{ROOT_NAME}/{name}")
        archive.writestr(
            f"{ROOT_NAME}/SHA256_MANIFEST.txt",
            manifest,
        )

    with zipfile.ZipFile(OUTPUT) as archive:
        bad = archive.testzip()
        if bad is not None:
            raise RuntimeError(f"ZIP 完整性检查失败：{bad}")
        names = archive.namelist()
        forbidden_names = {
            "tester_accounts.json",
            "tester_channel.json",
            "universal_login.json",
        }
        forbidden = [
            name
            for name in names
            if (
                Path(name).name in forbidden_names
                or Path(name).suffix.casefold()
                in {".db", ".sqlite", ".sqlite3", ".pyc"}
                or "/__pycache__/" in name
                or "/.git/" in name
            )
            or name.endswith("/01_完整源码/assets/auth_config.json")
        ]
        if forbidden:
            raise RuntimeError(
                "交付包含禁止文件：" + ", ".join(forbidden[:10])
            )

    result = {
        "archive": str(OUTPUT),
        "size_bytes": OUTPUT.stat().st_size,
        "entry_count": len(file_entries) + len(in_memory) + 1,
        "sha256": _sha256(OUTPUT),
        "branch": branch,
        "commit": commit,
        "candidate_exe_sha256": _sha256(CANDIDATE / "LangLang.exe"),
        "integrity": "ok",
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
