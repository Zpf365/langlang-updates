"""Create the Windows package, source package and stable update manifest."""

# ruff: noqa: E402, I001

from __future__ import annotations

import hashlib
import json
import shutil
import sys
import zipfile
from datetime import UTC, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weimi_manager import __version__


RELEASE = ROOT / "release"
PROGRAM_NAME = f"LangLang_{__version__}_Windows_x64"
ASSET_NAME = f"LangLang-{__version__}-win64.zip"
SOURCE_ROOTS = (
    "weimi_manager",
    "tests",
    "tools",
    "supabase",
    "assets",
)
SOURCE_ROOT_FILES = (
    "launcher.py",
    "LangLang.spec",
    "pyproject.toml",
    "uv.lock",
    "run.bat",
    "README.md",
    "ROADMAP.md",
    "FUNCTIONAL_PARITY.md",
    "BROWSER_RUNTIME_IMPLEMENTATION.md",
    "ORDER_LIST_VIRTUALIZATION_REPORT.md",
    "TUOKE_ACCOUNT_AUTH_RESEARCH_CN.md",
    "update_manifest.json",
)
SOURCE_EXCLUDED_NAMES = {
    ".env",
    ".env.local",
    "HANDOFF_1.6.62.md",
    "auth_config.json",
    "tester_accounts.json",
    "tester_channel.json",
}
SOURCE_EXCLUDED_DIRS = {
    ".git",
    ".pytest_cache",
    ".temp",
    ".venv",
    "__pycache__",
    "build",
    "data",
    "dist",
    "release",
}
PUBLIC_EXCLUDED_NAMES = {
    ".env",
    ".env.local",
    "tester_accounts.json",
    "tester_channel.json",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def make_zip(source: Path, target: Path) -> None:
    if target.exists():
        raise FileExistsError(target)
    with zipfile.ZipFile(
        target,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        for path in sorted(source.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(source.parent))


def source_files() -> list[Path]:
    files: set[Path] = set()
    for dirname in SOURCE_ROOTS:
        base = ROOT / dirname
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            relative = path.relative_to(ROOT)
            if not path.is_file():
                continue
            if any(part in SOURCE_EXCLUDED_DIRS for part in relative.parts[:-1]):
                continue
            if path.name in SOURCE_EXCLUDED_NAMES or path.suffix == ".pyc":
                continue
            files.add(relative)
    for filename in SOURCE_ROOT_FILES:
        path = ROOT / filename
        if path.is_file():
            files.add(path.relative_to(ROOT))
    files.update(
        path.relative_to(ROOT)
        for path in ROOT.glob("RELEASE_NOTES_*.md")
        if path.is_file()
    )
    for pattern in (
        "HANDOFF_*.md",
        "MODIFICATION_LIST_*.md",
        "TEST_REPORT_*.md",
        "BUILD_LOG_*.txt",
    ):
        files.update(
            path.relative_to(ROOT)
            for path in ROOT.glob(pattern)
            if path.is_file()
        )
    return sorted(files, key=lambda item: item.as_posix().casefold())


def make_source_zip(target: Path) -> None:
    if target.exists():
        raise FileExistsError(target)
    with zipfile.ZipFile(
        target,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        for relative in source_files():
            archive.write(ROOT / relative, relative.as_posix())


def verify_embedded_version(program: Path) -> None:
    candidates = (
        program / "update_manifest.json",
        program / "_internal" / "update_manifest.json",
    )
    manifest_path = next((path for path in candidates if path.is_file()), None)
    if manifest_path is None:
        raise FileNotFoundError("程序包缺少 update_manifest.json")
    payload = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    embedded = str(payload.get("version") or "")
    if embedded != __version__:
        raise RuntimeError(
            f"程序包版本为 {embedded or '未知'}，不是当前源码版本 {__version__}；"
            "请使用包含最新 update_manifest.json 的 dist 重新构建。"
        )


def verify_public_program(program: Path) -> None:
    unsafe = [
        path.relative_to(program).as_posix()
        for path in program.rglob("*")
        if path.is_file()
        and (
            path.name in PUBLIC_EXCLUDED_NAMES
            or path.suffix.casefold() in {".db", ".sqlite", ".sqlite3"}
        )
    ]
    if unsafe:
        raise RuntimeError(
            "发行程序包含不允许公开的测试账号、环境或数据库文件："
            + ", ".join(sorted(unsafe))
        )


def main() -> int:
    diagnostic_review = RELEASE / "DIAGNOSTIC_REVIEW.md"
    if not diagnostic_review.is_file():
        raise RuntimeError(
            "缺少 release/DIAGNOSTIC_REVIEW.md，"
            "请先运行 tools/release_preflight.py 审查旧版本诊断。"
        )
    source_program = ROOT / "dist" / "LangLang"
    if not (source_program / "LangLang.exe").is_file():
        raise FileNotFoundError(source_program / "LangLang.exe")
    verify_embedded_version(source_program)
    verify_public_program(source_program)
    RELEASE.mkdir(parents=True, exist_ok=True)
    program = RELEASE / PROGRAM_NAME
    if program.exists():
        raise FileExistsError(program)
    shutil.copytree(source_program, program)
    package = RELEASE / ASSET_NAME
    make_zip(program, package)
    digest = sha256(package)
    notes_file = ROOT / f"RELEASE_NOTES_{__version__}.md"
    notes = (
        notes_file.read_text(encoding="utf-8").strip()
        if notes_file.is_file()
        else f"澜琅 {__version__}"
    )
    manifest = {
        "version": __version__,
        "download_url": (
            "https://github.com/Zpf365/langlang-updates/releases/download/"
            f"v{__version__}/{ASSET_NAME}"
        ),
        "sha256": digest,
        "mandatory": False,
        "published_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "notes": notes,
    }
    manifest_path = RELEASE / "update_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (ROOT / "update_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    source_zip = RELEASE / f"LangLang_{__version__}_Source.zip"
    make_source_zip(source_zip)
    (RELEASE / f"SHA256SUMS_{__version__.replace('.', '_')}.txt").write_text(
        f"{digest}  {package.name}\n"
        f"{sha256(source_zip)}  {source_zip.name}\n"
        f"{sha256(program / 'LangLang.exe')}  {PROGRAM_NAME}/LangLang.exe\n",
        encoding="utf-8",
    )
    print(f"PACKAGE={package}")
    print(f"SOURCE={source_zip}")
    print(f"MANIFEST={manifest_path}")
    print(f"SHA256={digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
