"""Create the Windows package, source package and stable update manifest."""

# ruff: noqa: E402, I001

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import zipfile
from datetime import UTC, datetime
from fnmatch import fnmatch
from pathlib import Path, PurePosixPath


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weimi_manager import __version__
from weimi_manager.update_notes import build_update_summary
from weimi_manager.updater import DEFAULT_REQUIRED_UPDATE_FILES


RELEASE = ROOT / "release"
PROGRAM_NAME = f"LangLang_{__version__}_Windows_x64"
ASSET_NAME = f"LangLang-{__version__}-win64.zip"
SOURCE_ROOTS = (
    "weimi_manager",
    "webui",
    "tests",
    "tools",
    "supabase",
    "assets",
    "buyer_miniprogram",
)
SOURCE_ROOT_FILES = (
    "launcher.py",
    "launch_webui.py",
    "LangLang.spec",
    "LangLangWebUI.spec",
    "pyproject.toml",
    "uv.lock",
    "run.bat",
    "README.md",
    "ROADMAP.md",
    "FUNCTIONAL_PARITY.md",
    "BROWSER_RUNTIME_IMPLEMENTATION.md",
    "ORDER_LIST_VIRTUALIZATION_REPORT.md",
    "TUOKE_ACCOUNT_AUTH_RESEARCH_CN.md",
    "FEATURE_REGRESSION_CHECKLIST_CN.md",
    "ANALYTICS_PRIVACY_AND_CONTROLS_CN.md",
    "AGENTS.md",
    "WEBUI_PARITY_MATRIX_CN.md",
    "BUTTON_WEBUI_FULL_PARITY_AUDIT_CN.md",
    "PACKAGE-INSTRUCTIONS.txt",
    "buyer_assistant.env.example",
    "buyer_assistant.example.json",
    "update_manifest.json",
)
SOURCE_EXCLUDED_NAMES = {
    ".env",
    ".env.local",
    "HANDOFF_1.6.62.md",
    "auth_config.json",
    "saved_accounts.bin",
    "tester_accounts.json",
    "tester_channel.json",
    "test-update-policy.json",
}
SOURCE_EXCLUDED_SUFFIXES = {
    ".bak",
    ".db",
    ".log",
    ".jsonl",
    ".pyc",
    ".sqlite",
    ".sqlite3",
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
    "LocalAppData",
    "logs",
    "node_modules",
    "release",
}
PROGRAM_EXCLUDED_DIRS = {
    "docs",
    "source",
    "tests",
}
PUBLIC_EXCLUDED_NAMES = {
    ".env",
    ".env.local",
    "tester_accounts.json",
    "tester_channel.json",
    "test-update-policy.json",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_release_checksums(
    package: Path,
    source_zip: Path | None,
    program: Path,
    manifest_path: Path,
) -> Path:
    sums_path = (
        RELEASE / f"SHA256SUMS_{__version__.replace('.', '_')}.txt"
    )
    content = f"{sha256(package)}  {package.name}\n"
    if source_zip is not None:
        content += f"{sha256(source_zip)}  {source_zip.name}\n"
    content += (
        f"{sha256(program / 'LangLang.exe')}  "
        f"{PROGRAM_NAME}/LangLang.exe\n"
        f"{sha256(manifest_path)}  {manifest_path.name}\n"
    )
    sums_path.write_text(content, encoding="utf-8")
    return sums_path


def build_bundle_manifest(
    *,
    download_url: str,
    published_at: str,
    notes: str,
) -> dict[str, object]:
    return {
        "version": __version__,
        "download_url": download_url,
        "sha256": "",
        "channel": "stable",
        "prerelease": False,
        "mandatory": False,
        "published_at": published_at,
        "notes": notes,
        "summary": build_update_summary(notes),
    }


def build_release_manifest(
    *,
    download_url: str,
    package_sha256: str,
    published_at: str,
    notes: str,
) -> dict[str, object]:
    return {
        "version": __version__,
        "download_url": download_url,
        "sha256": package_sha256,
        "channel": "stable",
        "prerelease": False,
        "mandatory": False,
        "published_at": published_at,
        "notes": notes,
        "summary": build_update_summary(notes),
        "required_files": list(DEFAULT_REQUIRED_UPDATE_FILES),
    }


def write_bundle_manifest(
    program: Path,
    manifest: dict[str, object],
) -> None:
    payload = json.dumps(manifest, ensure_ascii=False, indent=2)
    for relative in (
        Path("update_manifest.json"),
        Path("_internal") / "update_manifest.json",
    ):
        target = program / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(payload, encoding="utf-8")


def verify_release_package(
    package: Path,
    manifest: dict[str, object],
) -> None:
    digest = sha256(package)
    if digest != str(manifest.get("sha256") or "").casefold():
        raise RuntimeError("外置清单 SHA256 与最终 Windows ZIP 不一致")
    with zipfile.ZipFile(package) as archive:
        names = archive.namelist()
        manifest_suffix = "_internal/update_manifest.json"
        roots = {
            name[: -len(manifest_suffix) - 1]
            for name in names
            if name == manifest_suffix
            or name.endswith("/" + manifest_suffix)
        }
        if len(roots) != 1:
            raise RuntimeError("Windows ZIP 中找不到唯一的包内清单")
        root = next(iter(roots))
        prefix = f"{root}/" if root else ""
        bundle = json.loads(
            archive.read(prefix + manifest_suffix).decode("utf-8-sig")
        )
        if str(bundle.get("version") or "") != __version__:
            raise RuntimeError("包内清单版本与当前正式版本不一致")
        if str(bundle.get("sha256") or ""):
            raise RuntimeError("包内清单不得包含 ZIP 自身 SHA256")
        required = manifest.get("required_files")
        if not isinstance(required, list):
            raise RuntimeError("外置清单缺少 required_files")
        relative_names = {
            name[len(prefix) :]
            for name in names
            if prefix and name.startswith(prefix)
        } or set(names)
        for pattern in required:
            normalized = str(pattern).replace("\\", "/")
            path = PurePosixPath(normalized)
            if path.is_absolute() or ".." in path.parts:
                raise RuntimeError(
                    f"外置清单包含非法必需文件路径：{pattern}"
                )
        missing = [
            str(pattern)
            for pattern in required
            if not any(
                fnmatch(name, str(pattern))
                for name in relative_names
            )
        ]
        if missing:
            raise RuntimeError(
                "Windows ZIP 缺少必需文件：" + ", ".join(missing)
            )


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


def source_file_allowed(path: Path) -> bool:
    relative = path.relative_to(ROOT)
    return not (
        any(part in SOURCE_EXCLUDED_DIRS for part in relative.parts[:-1])
        or path.name in SOURCE_EXCLUDED_NAMES
        or path.suffix.casefold() in SOURCE_EXCLUDED_SUFFIXES
    )


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
            if not source_file_allowed(path):
                continue
            files.add(relative)
    for filename in SOURCE_ROOT_FILES:
        path = ROOT / filename
        if path.is_file() and source_file_allowed(path):
            files.add(path.relative_to(ROOT))
    files.update(
        path.relative_to(ROOT)
        for path in ROOT.glob("RELEASE_NOTES_*.md")
        if path.is_file() and source_file_allowed(path)
    )
    for pattern in (
        "HANDOFF_*.md",
        "DONGXIAO_*.md",
        "MODIFICATION_LIST_*.md",
        "TEST_REPORT_*.md",
        "BUILD_LOG_*.txt",
    ):
        files.update(
            path.relative_to(ROOT)
            for path in ROOT.glob(pattern)
            if path.is_file() and source_file_allowed(path)
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


def verify_webui_entry_source() -> None:
    launcher = (ROOT / "launcher.py").read_text(encoding="utf-8")
    if (
        "weimi_manager.webui.main import main" not in launcher
        or "weimi_manager.main import main" in launcher
        or "--legacy-ui" in launcher
    ):
        raise RuntimeError(
            "正式 launcher 不是纯 WebUI 入口；发现旧 Tk 入口或兼容分支"
        )
    for spec_name in ("LangLang.spec", "LangLangWebUI.spec"):
        spec = (ROOT / spec_name).read_text(encoding="utf-8")
        checks = (
            '"launcher.py"' in spec or "'launcher.py'" in spec,
            "webui" in spec and "dist" in spec,
            "PySide6.QtWebEngineCore" in spec,
            "PySide6.QtWebEngineWidgets" in spec,
            "PySide6.QtWebChannel" in spec,
            "PySide6.QtNetwork" in spec,
            "weimi_manager.main" in spec,
            "weimi_manager.ui.app" in spec,
        )
        if not all(checks):
            raise RuntimeError(
                f"{spec_name} 未固定为 WebUI 正式打包配置"
            )


def verify_webui_assets(program: Path) -> Path:
    frontend_candidates = (
        program / "_internal" / "webui" / "dist",
        program / "webui" / "dist",
    )
    frontend = next(
        (
            candidate
            for candidate in frontend_candidates
            if (candidate / "index.html").is_file()
        ),
        None,
    )
    if frontend is None:
        raise RuntimeError(
            "正式程序包缺少 _internal/webui/dist/index.html；"
            "必须使用 WebUI 正式 spec 重新构建"
        )
    assets = frontend / "assets"
    required = {
        "StoresPage": "StoresPage-*.js",
        "OrdersParityPage": "OrdersParityPage-*.js",
        "ProductsPage": "ProductsPage-*.js",
        "autoRefresh": "autoRefresh-*.js",
    }
    missing = [
        label
        for label, pattern in required.items()
        if not any(assets.glob(pattern))
    ]
    if "autoRefresh" in missing:
        # Vite may inline the shared module into the main entry chunk when
        # App.vue and lazy pages both import it.  Accept that build shape only
        # when a produced chunk contains the persisted refresh contract.
        for asset in assets.glob("*.js"):
            try:
                content = asset.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if (
                "langlang.auto-refresh" in content
                or "上一轮未结束，已顺延" in content
            ):
                missing.remove("autoRefresh")
                break
    if missing:
        raise RuntimeError(
            "正式程序包缺少 WebUI 构建资源：" + ", ".join(missing)
        )
    return frontend


def verify_webui_program(program: Path) -> None:
    verify_webui_assets(program)
    from PyInstaller.archive.readers import CArchiveReader

    archive = CArchiveReader(program / "LangLang.exe")
    pyz_name = next(
        (name for name in archive.toc if name.lower().endswith(".pyz")),
        "",
    )
    if not pyz_name:
        raise RuntimeError("正式程序包缺少 Python PYZ 归档")
    pyz = archive.open_embedded_archive(pyz_name)
    forbidden = {"weimi_manager.main", "weimi_manager.ui.app"}
    present = sorted(forbidden.intersection(pyz.toc))
    if present:
        raise RuntimeError(
            "正式 WebUI 包仍包含旧工作台模块：" + ", ".join(present)
        )
    required_big_screen = {
        "weimi_manager.big_screen",
        "weimi_manager.dashboard_metrics",
        "weimi_manager.ui.reference_big_screen",
        "weimi_manager.ui.skeletons",
    }
    missing_big_screen = sorted(required_big_screen.difference(pyz.toc))
    if missing_big_screen:
        raise RuntimeError(
            "正式包缺少数据大屏模块：" + ", ".join(missing_big_screen)
        )
    resources = (
        program / "_internal" / "_tcl_data",
        program / "_internal" / "_tk_data",
        program / "_internal" / "_tkinter.pyd",
        program / "_internal" / "tcl86t.dll",
        program / "_internal" / "tk86t.dll",
    )
    missing_resources = [
        str(path.relative_to(program))
        for path in resources
        if not path.exists()
    ]
    if missing_resources:
        raise RuntimeError(
            "正式包缺少 Tcl/Tk 数据大屏资源："
            + ", ".join(missing_resources)
        )


def verify_public_program(program: Path) -> None:
    unsafe = [
        path.relative_to(program).as_posix()
        for path in program.rglob("*")
        if path.is_file()
        and (
            path.name in PUBLIC_EXCLUDED_NAMES
            or path.suffix.casefold() == ".map"
            or (
                path.relative_to(program).parts
                and path.relative_to(program).parts[0].casefold()
                in PROGRAM_EXCLUDED_DIRS
            )
            or (
                len(path.relative_to(program).parts) == 1
                and path.suffix.casefold() == ".md"
            )
            or path.suffix.casefold()
            in {".db", ".jsonl", ".log", ".sqlite", ".sqlite3"}
            or (
                path.relative_to(program).parts
                and path.relative_to(program).parts[0].casefold()
                in {"accounts", "data", "localappdata", "logs"}
            )
        )
    ]
    if unsafe:
        raise RuntimeError(
            "发行程序包含不允许公开的测试账号、环境或数据库文件："
            + ", ".join(sorted(unsafe))
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source-only",
        action="store_true",
        help="Rebuild only the source archive from the verified current tree.",
    )
    parser.add_argument(
        "--include-internal-source",
        action="store_true",
        help="Also create an internal-only source archive with the release.",
    )
    args = parser.parse_args(argv)
    if args.source_only:
        manifest_path = ROOT / "update_manifest.json"
        manifest = json.loads(
            manifest_path.read_text(encoding="utf-8-sig")
        )
        if str(manifest.get("version") or "") != __version__:
            raise RuntimeError("根目录外置清单版本与当前源码不一致")
        source_zip = RELEASE / f"LangLang_{__version__}_Source.zip"
        make_source_zip(source_zip)
        package = RELEASE / ASSET_NAME
        program = RELEASE / PROGRAM_NAME
        write_release_checksums(
            package,
            source_zip,
            program,
            manifest_path,
        )
        print(f"SOURCE={source_zip}")
        print(f"SOURCE_SHA256={sha256(source_zip)}")
        return 0
    diagnostic_review = RELEASE / "DIAGNOSTIC_REVIEW.md"
    if not diagnostic_review.is_file():
        raise RuntimeError(
            "缺少 release/DIAGNOSTIC_REVIEW.md，"
            "请先运行 tools/release_preflight.py 审查旧版本诊断。"
        )
    source_program = ROOT / "dist" / "LangLang"
    if not (source_program / "LangLang.exe").is_file():
        raise FileNotFoundError(source_program / "LangLang.exe")
    verify_webui_entry_source()
    verify_embedded_version(source_program)
    verify_webui_program(source_program)
    verify_public_program(source_program)
    RELEASE.mkdir(parents=True, exist_ok=True)
    program = RELEASE / PROGRAM_NAME
    if program.exists():
        raise FileExistsError(program)
    shutil.copytree(source_program, program)
    notes_file = ROOT / f"RELEASE_NOTES_{__version__}.md"
    notes = (
        notes_file.read_text(encoding="utf-8").strip()
        if notes_file.is_file()
        else f"澜琅 {__version__}"
    )
    download_url = (
        "https://github.com/Zpf365/langlang-updates/releases/download/"
        f"v{__version__}/{ASSET_NAME}"
    )
    published_at = datetime.now(UTC).isoformat(timespec="seconds")
    bundle_manifest = build_bundle_manifest(
        download_url=download_url,
        published_at=published_at,
        notes=notes,
    )
    write_bundle_manifest(program, bundle_manifest)
    verify_embedded_version(program)
    package = RELEASE / ASSET_NAME
    make_zip(program, package)
    digest = sha256(package)
    manifest = build_release_manifest(
        download_url=download_url,
        package_sha256=digest,
        published_at=published_at,
        notes=notes,
    )
    manifest_path = RELEASE / "update_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (ROOT / "update_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    verify_release_package(package, manifest)
    source_zip = (
        RELEASE / f"LangLang_{__version__}_Source.zip"
        if args.include_internal_source
        else None
    )
    if source_zip is not None:
        make_source_zip(source_zip)
    write_release_checksums(
        package,
        source_zip,
        program,
        manifest_path,
    )
    print(f"PACKAGE={package}")
    if source_zip is not None:
        print(f"SOURCE={source_zip}")
    print(f"MANIFEST={manifest_path}")
    print(f"SHA256={digest}")
    print(f"MANIFEST_SHA256={sha256(manifest_path)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
