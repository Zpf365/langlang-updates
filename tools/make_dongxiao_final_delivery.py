"""Build the single current LangLang Dongxiao delivery ZIP."""

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
    DONGXIAO_MIGRATION_VERSION,
    _migration_v10_sql,
)


VERSION = __version__
DELIVERY_ROOT = ROOT / "deliveries" / VERSION
OUTPUT = DELIVERY_ROOT / f"LangLang_{VERSION}_Dongxiao_Final.zip"
WINDOWS_PROGRAM = ROOT / "release" / f"LangLang_{VERSION}_Windows_x64"
SOURCE_ZIP = ROOT / "release" / f"LangLang_{VERSION}_Source.zip"
BASELINE_SOURCE = (
    ROOT
    / "artifacts"
    / "v1_8_52_baseline"
    / "LangLang_1.8.51_HEAD_403d705.zip"
)
DOCUMENTS = (
    "DONGXIAO_1_8_52_REPORT_CN.md",
    "DONGXIAO_1_8_52_TEST_REPORT_CN.md",
    "DONGXIAO_1_8_52_WECHAT_COMPATIBILITY_CN.md",
    "DONGXIAO_1_8_52_DATABASE_MIGRATION_CN.md",
    "DONGXIAO_1_8_52_USAGE_CN.md",
    "DONGXIAO_1_8_52_UNRESOLVED_CN.md",
    "FEATURE_REGRESSION_CHECKLIST_CN.md",
    f"RELEASE_NOTES_{VERSION}.md",
    f"HANDOFF_{VERSION}.md",
)
TEST_ARTIFACTS = (
    f"artifacts/release_{VERSION.replace('.', '_')}/pytest-full-final.log",
    f"artifacts/release_{VERSION.replace('.', '_')}/pytest-auth-skip-diagnostic.log",
    f"artifacts/release_{VERSION.replace('.', '_')}/pytest-skip-diagnostic.log",
    f"artifacts/release_{VERSION.replace('.', '_')}/pyinstaller-build.log",
    f"artifacts/release_{VERSION.replace('.', '_')}/generate-release.log",
    f"artifacts/release_{VERSION.replace('.', '_')}/make-final-delivery.log",
    f"artifacts/release_{VERSION.replace('.', '_')}/wechat-probe.json",
    f"artifacts/release_{VERSION.replace('.', '_')}/account-wizard-acceptance.log",
    f"artifacts/release_{VERSION.replace('.', '_')}/ui-acceptance/account-wizard-acceptance.json",
    f"artifacts/release_{VERSION.replace('.', '_')}/ui-acceptance/real-wechat-probe.json",
    f"artifacts/release_{VERSION.replace('.', '_')}/ui-acceptance/wizard-real-client.png",
    f"artifacts/release_{VERSION.replace('.', '_')}/ui-acceptance/wizard-account-a-bound.png",
    f"artifacts/release_{VERSION.replace('.', '_')}/ui-acceptance/wizard-account-b-bound.png",
    f"artifacts/release_{VERSION.replace('.', '_')}/ui-acceptance/wizard-step-a.png",
    f"artifacts/release_{VERSION.replace('.', '_')}/ui-acceptance/wizard-step-b.png",
    f"artifacts/release_{VERSION.replace('.', '_')}/ui-acceptance/wizard-after-restart.png",
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
    text = f"""# 澜琅 {VERSION} 动销中心最终交付

## 目录

- `00_基线备份/`：开工时 1.8.51 源码快照，不包含真实凭据。
- `01_完整源码/`：{VERSION} 完整源码。
- `02_Windows程序/LangLang/`：完整 Windows onedir 运行目录。
- `03_文档/`：开发、测试、兼容性、迁移和使用手册。
- `04_测试与日志/`：全量测试、构建和实际 Windows 微信探测日志。
- `05_数据库迁移/`：v10 迁移 SQL 和迁移说明。
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
        prefix=f"langlang-{VERSION}-delivery-",
        dir=ROOT / "artifacts",
    ) as temporary:
        stage = Path(temporary) / f"LangLang_{VERSION}_Dongxiao_Final"
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
        (migration / f"v{DONGXIAO_MIGRATION_VERSION}_buyer_switch.sql").write_text(
            _migration_v10_sql().strip() + "\n",
            encoding="utf-8",
        )
        shutil.copy2(
            ROOT / f"DONGXIAO_{VERSION.replace('.', '_')}_DATABASE_MIGRATION_CN.md",
            migration
            / f"DONGXIAO_{VERSION.replace('.', '_')}_DATABASE_MIGRATION_CN.md",
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
