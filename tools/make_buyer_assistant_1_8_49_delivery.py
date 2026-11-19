from __future__ import annotations

import hashlib
import json
import subprocess
import zipfile
from datetime import UTC, datetime
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
BASELINE = "a3a4ecbe0a6a5bea3ee9e97cd49cc578e82f33b8"
BRANCH = "feature/dongxiao-1.8.49-buyer-assistant"
ROOT_NAME = "LangLang_1.8.49_Buyer_Assistant_Final"
OUTPUT = (
    PROJECT.parent
    / "1.8.49最终交付"
    / f"{ROOT_NAME}.zip"
)
CANDIDATE = (
    PROJECT
    / "artifacts"
    / "buyer_assistant_1_8_49"
    / "candidate_dist"
    / "LangLang"
)

EXCLUDED_DIRS = {
    ".git", ".pytest_cache", ".ruff_cache", ".venv", ".venv311",
    "__pycache__", "artifacts", "browser_runtime", "build", "data",
    "dist", "node_modules", "release",
}
EXCLUDED_FILES = {
    ".env", "SOURCE_SHA256_MANIFEST.txt", "assets/auth_config.json",
    "assets/tester_accounts.json", "assets/tester_channel.json",
    "assets/universal_login.json",
}
EXCLUDED_SUFFIXES = {
    ".db", ".db-shm", ".db-wal", ".key", ".log", ".part", ".pem",
    ".pfx", ".pyc", ".sqlite", ".sqlite3", ".tmp",
}
REPORT_FILES = (
    "DONGXIAO_1_8_49_REPORT_CN.md",
    "DONGXIAO_1_8_49_MINIPROGRAM_USAGE_CN.md",
    "DONGXIAO_1_8_49_WECHAT_CONFIG_CN.md",
    "DONGXIAO_1_8_49_CAPABILITY_CN.md",
    "DONGXIAO_1_8_49_SECURITY_CN.md",
    "DONGXIAO_1_8_49_TEST_REPORT_CN.md",
    "DONGXIAO_1_8_48_OFFICIAL_BUYER_FLOW_RESEARCH_CN.md",
    "FEATURE_REGRESSION_CHECKLIST_CN.md",
)
EVIDENCE_FILES = (
    "artifacts/buyer_assistant_1_8_49/pytest-group1-malloc.log",
    "artifacts/buyer_assistant_1_8_49/pytest-group2-malloc.log",
    "artifacts/buyer_assistant_1_8_49/pyinstaller-build.log",
    "artifacts/buyer_assistant_1_8_49/candidate-smoke.json",
    "artifacts/buyer_assistant_1_8_49/candidate-smoke-db.log",
    "artifacts/buyer_assistant_1_8_49/build/LangLang/warn-LangLang.txt",
)


def _git(*args: str) -> str:
    return subprocess.check_output(
        ["git", *args], cwd=PROJECT, text=True, encoding="utf-8"
    ).strip()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _included(path: Path) -> bool:
    relative = path.relative_to(PROJECT)
    if any(part in EXCLUDED_DIRS for part in relative.parts):
        return False
    if relative.as_posix() in EXCLUDED_FILES:
        return False
    if path.name == ".env" or path.name.startswith(".env."):
        return False
    return path.suffix.casefold() not in EXCLUDED_SUFFIXES


def main() -> int:
    if not (CANDIDATE / "LangLang.exe").is_file():
        raise FileNotFoundError("1.8.49 候选 EXE 不存在")
    branch = _git("branch", "--show-current")
    commit = _git("rev-parse", "HEAD")
    if branch != BRANCH or commit == BASELINE:
        raise RuntimeError("1.8.49 分支或提交不符合交付条件")
    status = _git("status", "--short") or "clean"
    git_log = _git("log", "--oneline", "--decorate", f"{BASELINE}..HEAD")
    diff = _git("diff", "--name-status", f"{BASELINE}..HEAD")

    memory_entries = {
        "00_交付说明.md": f"""# 澜琅 1.8.49 Buyer Assistant Final

- 基线：{BASELINE}
- 分支：{BRANCH}
- 提交：{commit}
- 工作树：{status}

包含完整 Windows 源码、微信小程序源码、Supabase 后端、候选运行目录、
配置说明、使用手册、测试证据和 SHA256。

未部署正式程序，未发布远程更新，未执行真实购买或付款。
""".encode("utf-8"),
        "DONGXIAO_1_8_49_GIT_VERSION_CN.md": f"""# Git

```text
{git_log}
```
""".encode("utf-8"),
        "DONGXIAO_1_8_49_MODIFICATION_LIST_CN.md": f"""# 修改文件

```text
{diff}
```
""".encode("utf-8"),
    }
    for name in REPORT_FILES:
        path = PROJECT / name
        if not path.is_file():
            raise FileNotFoundError(path)
        memory_entries[name] = path.read_bytes()

    entries: dict[str, Path] = {}
    for path in sorted(PROJECT.rglob("*")):
        if path.is_file() and _included(path):
            entries[f"01_完整源码/{path.relative_to(PROJECT).as_posix()}"] = path
    for path in sorted(CANDIDATE.rglob("*")):
        if path.is_file():
            entries[
                f"02_Windows候选程序/LangLang/{path.relative_to(CANDIDATE).as_posix()}"
            ] = path
    for relative_name in EVIDENCE_FILES:
        path = PROJECT / relative_name
        if path.is_file():
            entries[
                f"03_测试与冒烟证据/{Path(relative_name).parent.name}-{path.name}"
            ] = path

    manifest = [
        f"generated_at={datetime.now(UTC).isoformat(timespec='seconds')}",
        f"baseline={BASELINE}",
        f"head={commit}",
        f"branch={branch}",
        f"candidate_exe_sha256={_sha256(CANDIDATE / 'LangLang.exe')}",
        "",
    ]
    for name, path in sorted(entries.items()):
        manifest.append(f"{_sha256(path)}  {name}")
    for name, payload in sorted(memory_entries.items()):
        manifest.append(f"{hashlib.sha256(payload).hexdigest()}  {name}")
    manifest_bytes = ("\n".join(manifest) + "\n").encode("utf-8")

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.unlink(missing_ok=True)
    with zipfile.ZipFile(OUTPUT, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for name, payload in sorted(memory_entries.items()):
            archive.writestr(f"{ROOT_NAME}/{name}", payload)
        for name, path in sorted(entries.items()):
            archive.write(path, f"{ROOT_NAME}/{name}")
        archive.writestr(f"{ROOT_NAME}/SHA256_MANIFEST.txt", manifest_bytes)

    with zipfile.ZipFile(OUTPUT) as archive:
        assert archive.testzip() is None
        bad = [
            name for name in archive.namelist()
            if Path(name).name in {
                "tester_accounts.json", "universal_login.json",
                "tester_channel.json", ".env",
            }
            or Path(name).suffix.casefold() in {".db", ".sqlite", ".sqlite3", ".pyc"}
            or "/__pycache__/" in name or "/.git/" in name
        ]
        if bad:
            raise RuntimeError("禁止文件：" + ", ".join(bad[:10]))
    result = {
        "archive": str(OUTPUT),
        "size_bytes": OUTPUT.stat().st_size,
        "entry_count": len(entries) + len(memory_entries) + 1,
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
