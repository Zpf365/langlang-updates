from __future__ import annotations

import hashlib
import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path

project = Path(r"D:\GPT\langlang-1.7.0-pyside")
archive = Path(r"D:\GPT\LangLang-1.8.14-source-JWT-refresh-20261113.zip")
root_name = "LangLang-1.8.14-source"

excluded_dirs = {
    ".git",
    ".pytest_cache",
    ".ruff_cache",
    ".venv311",
    "__pycache__",
    "build",
    "data",
    "dist",
    "release",
}
excluded_files = {
    ".env",
    "now()",
    "assets/auth_config.json",
    "assets/tester_accounts.json",
    "assets/universal_login.json",
}
excluded_suffixes = {
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
    relative = path.relative_to(project)
    parts = relative.parts
    if any(part in excluded_dirs for part in parts):
        return False
    if len(parts) >= 2 and parts[0] == "supabase" and parts[1] == ".temp":
        return False
    if relative.as_posix() in excluded_files:
        return False
    if relative.name == ".env" or relative.name.startswith(".env."):
        return False
    return path.suffix.casefold() not in excluded_suffixes


files = sorted(
    path
    for path in project.rglob("*")
    if path.is_file() and included(path)
)

manifest_lines = [
    f"LangLang source snapshot: {datetime.now(UTC).isoformat(timespec='seconds')}",
    "Application version: 1.8.14",
    "Change included: automatic JWT refresh and retry",
    "",
]

archive.unlink(missing_ok=True)
with zipfile.ZipFile(
    archive,
    "w",
    compression=zipfile.ZIP_DEFLATED,
    compresslevel=9,
) as bundle:
    for path in files:
        relative = path.relative_to(project)
        data = path.read_bytes()
        bundle.writestr(f"{root_name}/{relative.as_posix()}", data)
        manifest_lines.append(
            f"{hashlib.sha256(data).hexdigest()}  {relative.as_posix()}"
        )
    bundle.writestr(
        f"{root_name}/SOURCE_SHA256_MANIFEST.txt",
        "\n".join(manifest_lines) + "\n",
    )

with zipfile.ZipFile(archive) as bundle:
    bad_entry = bundle.testzip()
    names = set(bundle.namelist())
    if bad_entry is not None:
        raise RuntimeError(f"ZIP integrity failure: {bad_entry}")
    forbidden = [
        name
        for name in names
        if "/.env" in name
        or "/assets/auth_config.json" in name
        or "/assets/tester_accounts.json" in name
        or "/assets/universal_login.json" in name
        or "/.venv311/" in name
        or "/build/" in name
        or "/dist/" in name
        or "/release/" in name
    ]
    if forbidden:
        raise RuntimeError(f"Forbidden archive entries: {forbidden[:5]}")

digest = hashlib.sha256(archive.read_bytes()).hexdigest()
print(
    json.dumps(
        {
            "archive": str(archive),
            "size_bytes": archive.stat().st_size,
            "file_count": len(files) + 1,
            "sha256": digest,
            "integrity": "ok",
        },
        ensure_ascii=False,
        indent=2,
    )
)
