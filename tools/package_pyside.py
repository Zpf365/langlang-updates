from __future__ import annotations

import argparse
import hashlib
import json
import sys
import zipfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from weimi_manager import __version__  # noqa: E402

EXCLUDED_PARTS = {
    ".git",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    "artifacts",
    "build",
    "data",
    "dist",
    "release",
}
EXCLUDED_NAMES = {
    ".env",
    ".env.local",
    "auth_config.json",
    "tester_accounts.json",
    "tester_channel.json",
    "universal_login.json",
    "legacy_account_claim.txt",
}
PUBLIC_CLIENT_CONFIG = Path("_internal/assets/auth_config.json")
PUBLIC_UNIVERSAL_LOGIN = Path("_internal/assets/universal_login.json")
PUBLIC_TESTER_ACCOUNTS = Path("_internal/assets/tester_accounts.json")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def make_program_zip(
    program: Path,
    target: Path,
    *,
    include_client_config: bool = True,
    bundle_manifest: dict[str, object] | None = None,
) -> None:
    manifest_paths = {
        Path("update_manifest.json"),
        Path("_internal/update_manifest.json"),
    }
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(program.rglob("*")):
            if path.is_file():
                relative = path.relative_to(program)
                if (
                    bundle_manifest is not None
                    and relative in manifest_paths
                ):
                    continue
                if not include_client_config and relative == PUBLIC_CLIENT_CONFIG:
                    continue
                archive.write(path, Path("LangLang") / relative)
        if bundle_manifest is not None:
            payload = json.dumps(
                bundle_manifest,
                ensure_ascii=False,
                indent=2,
            )
            for relative in sorted(manifest_paths):
                archive.writestr(
                    (Path("LangLang") / relative).as_posix(),
                    payload,
                )


def iter_source_files() -> list[Path]:
    include_roots = ("weimi_manager", "tests", "tools", "assets", "supabase")
    files: set[Path] = set()
    for dirname in include_roots:
        base = ROOT / dirname
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            relative = path.relative_to(ROOT)
            if not path.is_file():
                continue
            if any(part in EXCLUDED_PARTS for part in relative.parts):
                continue
            if path.name in EXCLUDED_NAMES or path.suffix == ".pyc":
                continue
            files.add(relative)
    for name in (
        "pyside_launcher.py",
        "launcher.py",
        "LangLang.spec",
        "pyproject.toml",
        "requirements_pyside.txt",
        "run.bat",
        "run_legacy_tk.bat",
        "run_pyside.bat",
        "README.md",
        "ROADMAP.md",
        "FUNCTIONAL_PARITY.md",
        "update_manifest.json",
    ):
        path = ROOT / name
        if path.is_file():
            files.add(path.relative_to(ROOT))
    files.update(path.relative_to(ROOT) for path in ROOT.glob("*.md") if path.is_file())
    return sorted(files, key=lambda item: item.as_posix().casefold())


def make_source_zip(target: Path) -> None:
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for relative in iter_source_files():
            archive.write(ROOT / relative, relative.as_posix())


def unsafe_program_files(program: Path) -> list[Path]:
    unsafe: list[Path] = []
    for path in program.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(program)
        if (
            (
                path.name in EXCLUDED_NAMES
                and relative not in {
                    PUBLIC_CLIENT_CONFIG,
                    PUBLIC_UNIVERSAL_LOGIN,
                    PUBLIC_TESTER_ACCOUNTS,
                }
            )
            or path.suffix.casefold() in {".db", ".sqlite", ".sqlite3"}
            or (
                path.name.casefold().startswith("icu")
                and "browser_runtime" not in relative.parts
            )
        ):
            unsafe.append(path)
    return unsafe


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--program", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "release")
    parser.add_argument(
        "--without-client-config",
        action="store_true",
        help="Create an update asset that preserves the installed 1.6.x client config.",
    )
    parser.add_argument(
        "--share-only",
        action="store_true",
        help="Create a share package without a stable update download URL.",
    )
    args = parser.parse_args()
    if (
        not args.without_client_config
        and not (ROOT / "assets" / "auth_config.json").is_file()
    ):
        raise RuntimeError(
            "缺少 assets/auth_config.json，不能生成完整安装包。"
            "请先放入公开 Supabase 客户端配置，"
            "或显式使用 --without-client-config 生成更新资产。"
        )
    program = args.program.resolve()
    if not (program / "LangLang.exe").is_file():
        raise FileNotFoundError(program / "LangLang.exe")
    unsafe = unsafe_program_files(program)
    if unsafe:
        raise RuntimeError("发行程序包含不允许的文件：" + ", ".join(map(str, unsafe)))
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    package = output / f"LangLang-{__version__}-win64.zip"
    source = output / f"LangLang_{__version__}_Source.zip"
    published_at = datetime.now(UTC).isoformat(timespec="seconds")
    notes = (ROOT / f"RELEASE_NOTES_{__version__}.md").read_text(
        encoding="utf-8"
    ).strip()
    bundle_manifest = {
        "version": __version__,
        "download_url": "",
        "sha256": "",
        "mandatory": False,
        "published_at": published_at,
        "notes": notes,
    }
    make_program_zip(
        program,
        package,
        include_client_config=not args.without_client_config,
        bundle_manifest=bundle_manifest,
    )
    digest = sha256(package)
    manifest = {
        "version": __version__,
        "download_url": (
            ""
            if args.share_only
            else (
                "https://github.com/Zpf365/langlang-updates/releases/download/"
                f"v{__version__}/{package.name}"
            )
        ),
        "sha256": digest,
        "mandatory": False,
        "published_at": published_at,
        "notes": notes,
    }
    (output / "update_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (ROOT / "update_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    make_source_zip(source)
    sums = (
        f"{digest}  {package.name}\n"
        f"{sha256(source)}  {source.name}\n"
        f"{sha256(program / 'LangLang.exe')}  LangLang/LangLang.exe\n"
    )
    (output / f"SHA256SUMS_{__version__.replace('.', '_')}.txt").write_text(
        sums,
        encoding="utf-8",
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
