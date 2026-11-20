"""Build the single LangLang 1.8.50 Dongxiao delivery ZIP."""

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
from weimi_manager.dongxiao.infrastructure.migrations import (
    _migration_v9_sql,
)


DELIVERY_ROOT = ROOT / "deliveries" / "1.8.50"
OUTPUT = DELIVERY_ROOT / "LangLang_1.8.50_Dongxiao_Final.zip"
WINDOWS_PROGRAM = ROOT / "release" / "LangLang_1.8.50_Windows_x64"
SOURCE_ZIP = ROOT / "release" / "LangLang_1.8.50_Source.zip"
BASELINE_SOURCE = (
    ROOT
    / "artifacts"
    / "v1_8_50_baseline_check"
    / "LangLang_1.8.49_Source.zip"
)
DOCUMENTS = (
    "DONGXIAO_1_8_50_REPORT_CN.md",
    "DONGXIAO_1_8_50_TEST_REPORT_CN.md",
    "DONGXIAO_1_8_50_WECHAT_COMPATIBILITY_CN.md",
    "DONGXIAO_1_8_50_DATABASE_MIGRATION_CN.md",
    "DONGXIAO_1_8_50_USAGE_CN.md",
    "DONGXIAO_1_8_50_UNRESOLVED_CN.md",
    "FEATURE_REGRESSION_CHECKLIST_CN.md",
    "RELEASE_NOTES_1.8.50.md",
    "HANDOFF_1.8.50.md",
)
TEST_ARTIFACTS = (
    "artifacts/release_1_8_50/pytest-full-category-fix.log",
    "artifacts/release_1_8_50/pyinstaller-build-category-fix.log",
    "artifacts/release_1_8_50/generate-release-category-fix.log",
    "artifacts/release_1_8_50/make-final-delivery-category-fix.log",
    "artifacts/release_1_8_50/wechat-probe.json",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def copy_source_tree(target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(SOURCE_ZIP) as archive:
        for member in archive.infolist():
            destination = (target / member.filename).resolve()
            if not destination.is_relative_to(target.resolve()):
                raise ValueError(f"源码包包含非法路径：{member.filename}")
        archive.extractall(target)


def git_info() -> str:
    commands = (
        ("BRANCH", ["git", "rev-parse", "--abbrev-ref", "HEAD"]),
        ("COMMIT", ["git", "rev-parse", "HEAD"]),
        ("STATUS", ["git", "status", "--short", "--branch"]),
        ("LOG", ["git", "log", "--oneline", "-8"]),
    )
    lines = [f"LANG_LANG_VERSION={__version__}", ""]
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


def write_readme(stage: Path) -> None:
    text = f"""# 澜琅 {__version__} 动销中心最终交付

## 目录

- `00_基线备份/`：官方 1.8.49 源码包，不包含真实凭据。
- `01_完整源码/`：1.8.50 完整源码。
- `02_Windows程序/LangLang/`：完整 Windows onedir 运行目录。
- `03_文档/`：开发、测试、兼容性、迁移和使用手册。
- `04_测试与日志/`：全量测试、构建和实际 Windows 微信探测日志。
- `05_数据库迁移/`：v9 迁移 SQL 和迁移说明。
- `06_Git信息/`：分支、提交、状态和历史。

## 启动

双击 `02_Windows程序/LangLang/LangLang.exe`。首次运行后登录澜琅并进入动销中心。

## 安全边界

- 不包含真实账号、密码、Cookie、Token、AppSecret 或业务数据库。
- 不自动登录微信、不绕过验证码、不读取微信私有数据库。
- 不自动付款、退款或伪造订单成功。
- 真实微信扫码、账号切换、身份确认和订单本人归属需用户本人验收。
- 本包未部署到 `D:\\WG\\LangLang`。
"""
    (stage / "README_交付说明_CN.md").write_text(text, encoding="utf-8")


def main() -> int:
    for required in (
        WINDOWS_PROGRAM / "LangLang.exe",
        SOURCE_ZIP,
        BASELINE_SOURCE,
    ):
        if not required.exists():
            raise FileNotFoundError(required)
    DELIVERY_ROOT.mkdir(parents=True, exist_ok=True)
    if OUTPUT.exists():
        raise FileExistsError(OUTPUT)
    with tempfile.TemporaryDirectory(
        prefix="langlang-1.8.50-delivery-",
        dir=ROOT / "artifacts",
    ) as temporary:
        stage = Path(temporary) / "LangLang_1.8.50_Dongxiao_Final"
        stage.mkdir()
        baseline = stage / "00_基线备份"
        baseline.mkdir()
        shutil.copy2(
            BASELINE_SOURCE,
            baseline / BASELINE_SOURCE.name,
        )
        copy_source_tree(stage / "01_完整源码")
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
        tests = stage / "04_测试与日志"
        tests.mkdir()
        for relative in TEST_ARTIFACTS:
            source = ROOT / relative
            if source.is_file():
                shutil.copy2(source, tests / source.name)
        migration = stage / "05_数据库迁移"
        migration.mkdir()
        (migration / "20261119_v9_buyer_workspace.sql").write_text(
            _migration_v9_sql().strip() + "\n",
            encoding="utf-8",
        )
        shutil.copy2(
            ROOT / "DONGXIAO_1_8_50_DATABASE_MIGRATION_CN.md",
            migration / "DONGXIAO_1_8_50_DATABASE_MIGRATION_CN.md",
        )
        git_dir = stage / "06_Git信息"
        git_dir.mkdir()
        (git_dir / "GIT_INFO.txt").write_text(
            git_info(),
            encoding="utf-8",
        )
        write_readme(stage)
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
