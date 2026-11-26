"""Validated test-machine-only automatic update policy."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_TEST_INSTALL_DIR = Path(r"D:\WG\LangLang")
TEST_POLICY_RELATIVE = Path("_internal") / "test-update-policy.json"
TEST_POLICY_SCOPE = "langlang-test-machine"


@dataclass(frozen=True, slots=True)
class TestUpdatePolicy:
    install_path: Path
    channel: str
    allow_prerelease: bool
    policy_path: Path


def _canonical(path: Path) -> str:
    return os.path.normcase(str(path.resolve()))


def load_test_update_policy(
    target: Path,
    *,
    allowed_install_dir: Path = DEFAULT_TEST_INSTALL_DIR,
) -> TestUpdatePolicy | None:
    install_path = Path(target).resolve()
    if _canonical(install_path) != _canonical(allowed_install_dir):
        return None
    policy_path = (install_path / TEST_POLICY_RELATIVE).resolve()
    try:
        policy_path.relative_to(install_path)
    except ValueError:
        return None
    if not policy_path.is_file():
        return None
    try:
        payload: dict[str, Any] = json.loads(
            policy_path.read_text(encoding="utf-8-sig")
        )
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    declared_path = str(payload.get("install_path") or "")
    if (
        payload.get("scope") != TEST_POLICY_SCOPE
        or payload.get("enabled") is not True
        or str(payload.get("channel") or "").casefold() != "stable"
        or payload.get("allow_prerelease") is not False
        or not declared_path
        or _canonical(Path(declared_path)) != _canonical(install_path)
    ):
        return None
    return TestUpdatePolicy(
        install_path=install_path,
        channel="stable",
        allow_prerelease=False,
        policy_path=policy_path,
    )
