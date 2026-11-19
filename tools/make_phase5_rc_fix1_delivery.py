from __future__ import annotations

import hashlib
import json
import subprocess
import zipfile
from datetime import UTC, datetime
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
BASELINE = "534a587ffa1b9710cd334e5372e9ad6ed41c977d"
ROOT_NAME = "LangLang_1.8.47_Dongxiao_Phase5_RC_Fix1"
OUTPUT = (
    PROJECT.parent
    / "第5阶段RC_Fix1交付"
    / f"{ROOT_NAME}.zip"
)
CANDIDATE = (
    PROJECT
    / "artifacts"
    / "phase5_rc_fix1"
    / "candidate_dist_final"
    / "LangLang"
)

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
    "DONGXIAO_PHASE5_RC_FIX1_REPORT_CN.md",
    "DONGXIAO_PHASE5_RC_FIX1_BACKUP_RESTORE_CN.md",
    "DONGXIAO_PHASE5_RC_FIX1_INTERFACE_AUDIT_CN.md",
    "DONGXIAO_PHASE5_RC_FIX1_DATABASE_CN.md",
    "DONGXIAO_PHASE5_RC_FIX1_SAFETY_CN.md",
    "DONGXIAO_PHASE5_RC_FIX1_TEST_REPORT_CN.md",
    "DONGXIAO_PHASE5_RC_FIX1_STRESS_REPORT_CN.md",
    "DONGXIAO_PHASE5_RC_FIX1_CANDIDATE_EXE_CN.md",
    "FEATURE_REGRESSION_CHECKLIST_CN.md",
)

EVIDENCE_FILES = (
    "artifacts/phase5_rc_fix1/pytest-full.log",
    "artifacts/phase5_rc_fix1/stress-30min.json",
    "artifacts/phase5_rc_fix1/stress-30min.stdout.log",
    "artifacts/phase5_rc_fix1/stress-30min.stderr.log",
    "artifacts/phase5_rc_fix1/stress-smoke.json",
    "artifacts/phase5_rc_fix1/stress-smoke.log",
    "artifacts/phase5_rc_fix1/pyinstaller-build-final.log",
    "artifacts/phase5_rc_fix1/candidate_build_check.log",
    "artifacts/phase5_rc_fix1/candidate_automation_self_test.log",
    "artifacts/phase5_rc_fix1/candidate_automation_self_test.json",
    "artifacts/phase5_rc_fix1/candidate_smoke.log",
    "artifacts/phase5_rc_fix1/browser_smoke.log",
    "artifacts/phase5_rc_fix1/ruff-changed-files.log",
    "artifacts/phase5_rc_fix1/candidate_build_final/LangLang/warn-LangLang.txt",
    "artifacts/phase5_rc_fix1/post-stress-integrity.log",
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


def _git_documents() -> tuple[str, str]:
    branch = _git("branch", "--show-current")
    commit = _git("rev-parse", "HEAD")
    status = _git("status", "--short") or "clean"
    log = _git("log", "--oneline", "--decorate", f"{BASELINE}..HEAD")
    diff = _git("diff", "--name-status", f"{BASELINE}..HEAD")
    version = f"""# Phase 5 RC Fix1 Git 与版本信息

- 基线提交：{BASELINE}
- 当前分支：{branch}
- RC Fix1 提交：{commit}
- 工作树：{status}

## 提交历史

```text
{log}
```
"""
    modifications = f"""# Phase 5 RC Fix1 修改文件清单

基线：{BASELINE}

```text
{diff}
```
"""
    return version, modifications


def main() -> int:
    if not (CANDIDATE / "LangLang.exe").is_file():
        raise FileNotFoundError("RC Fix1 候选 EXE 不存在")
    branch = _git("branch", "--show-current")
    commit = _git("rev-parse", "HEAD")
    if commit == BASELINE:
        raise RuntimeError("RC Fix1 尚未提交，拒绝生成最终交付包")

    readme = f"""# 澜琅 1.8.47 动销中心 Phase5 RC Fix1

交付时间：{datetime.now(UTC).isoformat(timespec='seconds')}

- 基线：{BASELINE}
- 分支：{branch}
- RC Fix1 提交：{commit}

目录：

- `01_完整源码/`
- `02_最终候选程序/`
- `03_测试与压力原始结果/`
- 根目录中文报告与 SHA256 清单

边界：未部署、未发布、未覆盖 `D:\\WG\\LangLang`；未执行真实平台写操作。
"""
    memory_entries: dict[str, bytes] = {
        "00_交付说明.md": readme.encode("utf-8"),
    }
    for name in REPORT_FILES:
        path = PROJECT / name
        if not path.is_file():
            raise FileNotFoundError(path)
        memory_entries[name] = path.read_bytes()
    git_info, modifications = _git_documents()
    memory_entries["DONGXIAO_PHASE5_RC_FIX1_GIT_VERSION_CN.md"] = (
        git_info.encode("utf-8")
    )
    memory_entries["DONGXIAO_PHASE5_RC_FIX1_MODIFICATION_LIST_CN.md"] = (
        modifications.encode("utf-8")
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
                f"02_最终候选程序/LangLang/"
                f"{path.relative_to(CANDIDATE).as_posix()}"
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
    for name, payload in sorted(memory_entries.items()):
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
        for name, payload in sorted(memory_entries.items()):
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

    print(json.dumps({
        "archive": str(OUTPUT),
        "size_bytes": OUTPUT.stat().st_size,
        "entry_count": len(file_entries) + len(memory_entries) + 1,
        "sha256": _sha256(OUTPUT),
        "branch": branch,
        "commit": commit,
        "candidate_exe_sha256": _sha256(CANDIDATE / "LangLang.exe"),
        "integrity": "ok",
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
