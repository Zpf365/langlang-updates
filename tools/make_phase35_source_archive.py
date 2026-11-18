from __future__ import annotations

import hashlib
import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
ARCHIVE = (
    PROJECT.parent
    / "第3.5阶段源码包"
    / "LangLang_1.8.47_Dongxiao_Phase3_5_Source_20261118.zip"
)
ROOT_NAME = "LangLang-1.8.47-Dongxiao-Phase3_5"

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
    files = sorted(
        path
        for path in PROJECT.rglob("*")
        if path.is_file() and included(path)
    )
    manifest_lines = [
        f"LangLang source snapshot: {datetime.now(UTC).isoformat(timespec='seconds')}",
        "Application version: 1.8.47",
        "Change: Dongxiao Phase 3.5 catalog acceptance and hardening",
        "",
    ]
    ARCHIVE.parent.mkdir(parents=True, exist_ok=True)
    ARCHIVE.unlink(missing_ok=True)
    with zipfile.ZipFile(
        ARCHIVE,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as bundle:
        for path in files:
            relative = path.relative_to(PROJECT)
            data = path.read_bytes()
            bundle.writestr(
                f"{ROOT_NAME}/{relative.as_posix()}",
                data,
            )
            manifest_lines.append(
                f"{hashlib.sha256(data).hexdigest()}  {relative.as_posix()}"
            )
        bundle.writestr(
            f"{ROOT_NAME}/SOURCE_SHA256_MANIFEST.txt",
            "\n".join(manifest_lines) + "\n",
        )

    with zipfile.ZipFile(ARCHIVE) as bundle:
        bad_entry = bundle.testzip()
        if bad_entry is not None:
            raise RuntimeError(f"ZIP integrity failure: {bad_entry}")
        names = bundle.namelist()
        forbidden = [
            name
            for name in names
            if "/.env" in name
            or "/assets/auth_config.json" in name
            or "/assets/tester_accounts.json" in name
            or "/assets/universal_login.json" in name
            or "/.git/" in name
            or "/build/" in name
            or "/dist/" in name
            or "/release/" in name
        ]
        if forbidden:
            raise RuntimeError(
                f"Forbidden archive entries: {forbidden[:5]}"
            )

    digest = hashlib.sha256(ARCHIVE.read_bytes()).hexdigest()
    print(
        json.dumps(
            {
                "archive": str(ARCHIVE),
                "size_bytes": ARCHIVE.stat().st_size,
                "file_count": len(files) + 1,
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
