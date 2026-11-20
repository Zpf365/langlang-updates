"""Build the LangLang 1.8.53 sync-scope delivery ZIP."""

# ruff: noqa: E402, I001

from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weimi_manager import __version__
from weimi_manager.dongxiao.infrastructure.migrations import _migration_v10_sql


VERSION = __version__
RELEASE_TOKEN = VERSION.replace(".", "_")
DELIVERY_ROOT = ROOT / "deliveries" / VERSION
OUTPUT = DELIVERY_ROOT / f"LangLang_{VERSION}_Delivery.zip"
WINDOWS_PROGRAM = ROOT / "release" / f"LangLang_{VERSION}_Windows_x64"
SOURCE_ZIP = ROOT / "release" / f"LangLang_{VERSION}_Source.zip"
ARTIFACTS = ROOT / "artifacts" / f"release_{RELEASE_TOKEN}"
REPORT_NAMES = {
    "1.8.53": "DONGXIAO_1_8_53_SYNC_SCOPE_CN.md",
    "1.8.54": "DONGXIAO_1_8_54_REAL_WRITE_CN.md",
    "1.8.55": "DONGXIAO_1_8_55_REFRESH_SELECTION_CN.md",
    "1.8.56": "DONGXIAO_1_8_56_SKU_FALLBACK_CN.md",
}
if VERSION not in REPORT_NAMES:
    raise RuntimeError(f"Unsupported delivery version: {VERSION}")
DOCUMENTS = (
    f"RELEASE_NOTES_{VERSION}.md",
    REPORT_NAMES[VERSION],
    f"DONGXIAO_{RELEASE_TOKEN}_TEST_REPORT_CN.md",
    f"DONGXIAO_{RELEASE_TOKEN}_DATABASE_MIGRATION_CN.md",
    "FEATURE_REGRESSION_CHECKLIST_CN.md",
)
ARTIFACT_FILES = (
    "pytest-full-final.log",
    "pyinstaller-build.log",
    "generate-release.log",
    "make-final-delivery.log",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_info() -> str:
    commands = (
        ("BRANCH", ["git", "rev-parse", "--abbrev-ref", "HEAD"]),
        ("COMMIT", ["git", "rev-parse", "HEAD"]),
        ("STATUS", ["git", "status", "--short", "--branch"]),
        ("LOG", ["git", "log", "--oneline", "-6"]),
    )
    lines = [f"LANG_LANG_VERSION={VERSION}", ""]
    for label, command in commands:
        result = subprocess.run(
            command,
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=True,
        )
        lines.extend((f"[{label}]", result.stdout.strip(), ""))
    return "\n".join(lines)


def main() -> int:
    for required in (
        WINDOWS_PROGRAM / "LangLang.exe",
        SOURCE_ZIP,
    ):
        if not required.exists():
            raise FileNotFoundError(required)
    DELIVERY_ROOT.mkdir(parents=True, exist_ok=True)
    if OUTPUT.exists():
        raise FileExistsError(OUTPUT)
    with tempfile.TemporaryDirectory(
        prefix=f"langlang-{VERSION}-delivery-",
        dir=ROOT / "artifacts",
    ) as temporary:
        stage = Path(temporary) / f"LangLang_{VERSION}_Delivery"
        stage.mkdir()
        with zipfile.ZipFile(SOURCE_ZIP) as archive:
            archive.extractall(stage / "01_完整源码")
        shutil.copytree(
            WINDOWS_PROGRAM,
            stage / "02_Windows程序" / "LangLang",
        )
        docs = stage / "03_文档"
        docs.mkdir()
        for name in DOCUMENTS:
            source = ROOT / name
            if not source.is_file():
                raise FileNotFoundError(source)
            shutil.copy2(source, docs / name)
        logs = stage / "04_测试与日志"
        logs.mkdir()
        for name in ARTIFACT_FILES:
            source = ARTIFACTS / name
            if source.is_file():
                shutil.copy2(source, logs / name)
        migration = stage / "05_数据库迁移"
        migration.mkdir()
        (migration / "v10_buyer_switch.sql").write_text(
            _migration_v10_sql().strip() + "\n",
            encoding="utf-8",
        )
        git_dir = stage / "06_Git信息"
        git_dir.mkdir()
        (git_dir / "GIT_INFO.txt").write_text(
            git_info(),
            encoding="utf-8",
        )
        delivery_focus = {
            "1.8.54": "真实平台写操作恢复",
            "1.8.55": "店铺刷新选择范围",
            "1.8.56": "SKU 起购兼容修复",
        }.get(VERSION, "店铺刷新范围修复")
        (stage / "README_交付说明_CN.md").write_text(
            f"""# 澜琅 {VERSION} {delivery_focus}

启动：双击 `02_Windows程序/LangLang/LangLang.exe`。

本版本修复 SKU 起购数量缺失导致的预检阻断。
真实平台写操作保持永久开启，资金操作仍保留人工确认和金额核验。

本包不包含真实数据库或凭据，未部署远程发布，不覆盖已有交付包。
""",
            encoding="utf-8",
        )
        checksum_lines = []
        for path in sorted(stage.rglob("*")):
            if path.is_file() and path.name != "SHA256SUMS.txt":
                checksum_lines.append(
                    f"{sha256(path)}  {path.relative_to(stage).as_posix()}"
                )
        (stage / "SHA256SUMS.txt").write_text(
            "\n".join(checksum_lines) + "\n",
            encoding="utf-8",
        )
        with zipfile.ZipFile(
            OUTPUT,
            "w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=9,
        ) as archive:
            for path in sorted(stage.rglob("*")):
                if path.is_file():
                    archive.write(
                        path,
                        (stage.name / path.relative_to(stage)).as_posix(),
                    )
    print(f"OUTPUT={OUTPUT}")
    print(f"SHA256={sha256(OUTPUT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
