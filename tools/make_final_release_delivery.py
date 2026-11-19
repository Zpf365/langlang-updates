from __future__ import annotations

import hashlib
import json
import subprocess
import zipfile
from datetime import UTC, datetime
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
BASELINE = "4daa05eb878b3ef3cae1e4e44d2a8ba6ad730515"
ROOT_NAME = "LangLang_1.8.47_Dongxiao_Final_Release"
OUTPUT = (
    PROJECT.parent
    / "最终版交付"
    / f"{ROOT_NAME}.zip"
)
CANDIDATE = (
    PROJECT
    / "artifacts"
    / "final_release"
    / "candidate_dist"
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
    "DONGXIAO_FINAL_RELEASE_REPORT_CN.md",
    "DONGXIAO_FINAL_RELEASE_FEATURE_MATRIX_CN.md",
    "DONGXIAO_FINAL_RELEASE_INTERFACE_AUDIT_CN.md",
    "DONGXIAO_FINAL_RELEASE_DATABASE_MIGRATION_CN.md",
    "DONGXIAO_FINAL_RELEASE_SECURITY_AUDIT_CN.md",
    "DONGXIAO_FINAL_RELEASE_TEST_REPORT_CN.md",
    "DONGXIAO_FINAL_RELEASE_PERFORMANCE_CN.md",
    "DONGXIAO_FINAL_RELEASE_WINDOWS_SMOKE_CN.md",
    "DONGXIAO_FINAL_RELEASE_UNRESOLVED_CN.md",
    "FEATURE_REGRESSION_CHECKLIST_CN.md",
)

EVIDENCE_FILES = (
    "artifacts/final_release/pytest-full.log",
    "artifacts/final_release/performance.json",
    "artifacts/final_release/stress-60s-instrumented.json",
    "artifacts/final_release/pyinstaller-build.log",
    "artifacts/final_release/candidate-smoke.json",
    "artifacts/final_release/candidate-smoke-db.log",
    "artifacts/final_release/build/LangLang/warn-LangLang.txt",
    "artifacts/phase5_rc_fix1/stress-30min.json",
    "artifacts/phase5_rc_fix1/stress-30min.stdout.log",
    "artifacts/phase5_rc_fix1/stress-30min.stderr.log",
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


def _git_documents() -> tuple[bytes, bytes]:
    branch = _git("branch", "--show-current")
    commit = _git("rev-parse", "HEAD")
    status = _git("status", "--short") or "clean"
    log = _git("log", "--oneline", "--decorate", f"{BASELINE}..HEAD")
    diff = _git("diff", "--name-status", f"{BASELINE}..HEAD")
    version = f"""# Final Release Git 与版本信息

- 基线提交：{BASELINE}
- 当前分支：{branch}
- 最终提交：{commit}
- 工作树：{status}

## 提交历史

```text
{log}
```
"""
    modifications = f"""# Final Release 修改文件清单

基线：{BASELINE}

```text
{diff}
```
"""
    return version.encode("utf-8"), modifications.encode("utf-8")


def main() -> int:
    if not (CANDIDATE / "LangLang.exe").is_file():
        raise FileNotFoundError("最终候选 EXE 不存在")
    branch = _git("branch", "--show-current")
    commit = _git("rev-parse", "HEAD")
    if branch != "feature/dongxiao-final-release":
        raise RuntimeError(f"最终交付分支不正确：{branch}")
    if commit == BASELINE:
        raise RuntimeError("最终修改尚未提交")

    readme = f"""# 澜琅 1.8.47 动销中心 Final Release

交付时间：{datetime.now(UTC).isoformat(timespec='seconds')}

- 基线：{BASELINE}
- 分支：{branch}
- 最终提交：{commit}

目录：

- `01_完整源码/`
- `02_Windows发行程序/`
- `03_测试性能与冒烟证据/`
- 根目录中文报告、功能矩阵和 SHA256 清单

边界：未部署、未发布、未覆盖 `D:\\WG\\LangLang`；未执行真实退款、
售后同意/拒绝、真实支付或普通客户订单修改。
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
    memory_entries["DONGXIAO_FINAL_RELEASE_GIT_VERSION_CN.md"] = git_info
    memory_entries["DONGXIAO_FINAL_RELEASE_MODIFICATION_LIST_CN.md"] = (
        modifications
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
                "02_Windows发行程序/LangLang/"
                f"{path.relative_to(CANDIDATE).as_posix()}"
            ] = path
    for relative_name in EVIDENCE_FILES:
        path = PROJECT / relative_name
        if path.is_file():
            file_entries[
                "03_测试性能与冒烟证据/"
                f"{Path(relative_name).parent.name}-{path.name}"
            ] = path

    manifest_lines = [
        f"generated_at={datetime.now(UTC).isoformat(timespec='seconds')}",
        f"baseline={BASELINE}",
        f"head={commit}",
        f"branch={branch}",
        f"candidate_exe_sha256={_sha256(CANDIDATE / 'LangLang.exe')}",
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

    forbidden_names = {
        "tester_accounts.json",
        "tester_channel.json",
        "universal_login.json",
        ".env",
    }
    with zipfile.ZipFile(OUTPUT) as archive:
        bad = archive.testzip()
        if bad is not None:
            raise RuntimeError(f"ZIP 完整性检查失败：{bad}")
        names = archive.namelist()
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
            or name.endswith(
                "/01_完整源码/assets/auth_config.json"
            )
        ]
        if forbidden:
            raise RuntimeError(
                "交付包含禁止文件：" + ", ".join(forbidden[:10])
            )

    result = {
        "archive": str(OUTPUT),
        "size_bytes": OUTPUT.stat().st_size,
        "entry_count": len(file_entries) + len(memory_entries) + 1,
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
