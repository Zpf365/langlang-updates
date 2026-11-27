from __future__ import annotations

import base64
import hashlib
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from tools.build_hot_update_package import (
    RuleSource,
    build_update_package,
)
from weimi_manager.update_protocol import parse_signed_channel

ROOT = Path(__file__).resolve().parent.parent


def _private_key(path: Path) -> tuple[Ed25519PrivateKey, str]:
    private_key = Ed25519PrivateKey.generate()
    path.write_bytes(
        private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    public_key = base64.b64encode(
        private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
    ).decode("ascii")
    return private_key, public_key


def _webui(path: Path) -> Path:
    (path / "assets").mkdir(parents=True, exist_ok=True)
    (path / "index.html").write_text("<html>release</html>", encoding="utf-8")
    (path / "assets" / "app.js").write_text("console.log('release')", encoding="utf-8")
    return path


def _build(
    tmp_path: Path,
    *,
    output_name: str = "first",
    rules: tuple[RuleSource, ...] = (),
):
    private_path = tmp_path / "outside" / "release-private.pem"
    private_path.parent.mkdir(parents=True, exist_ok=True)
    private_key, public_key = _private_key(private_path)
    webui = _webui(tmp_path / "webui")
    result = build_update_package(
        webui,
        tmp_path / output_name,
        private_key_path=private_path,
        key_id="release-test",
        product_version="1.9.02",
        channel_sequence=3,
        webui_patch_id="webui-20261127-03",
        webui_sequence=3,
        webui_url="https://example.invalid/webui-20261127-03.zip",
        app_min="1.9.02",
        app_max="1.9.02.99",
        backend_contract=1,
        rollout=25,
        rules=rules,
        rules_patch_id="rules-20261127-03" if rules else "",
        rules_sequence=3 if rules else 0,
        rules_url=(
            "https://example.invalid/rules-20261127-03.zip"
            if rules
            else ""
        ),
        repo_root=tmp_path / "repository",
    )
    return private_key, public_key, result


def test_packager_is_deterministic_and_signs_complete_file_manifest(
    tmp_path: Path,
) -> None:
    _, public_key, first = _build(tmp_path, output_name="first")
    (tmp_path / "webui" / "index.html").touch()
    (tmp_path / "webui" / "assets" / "app.js").touch()
    _, _, second = _build(tmp_path, output_name="second")

    first_zip = first.webui_archive.read_bytes()
    second_zip = second.webui_archive.read_bytes()
    assert first_zip == second_zip
    assert hashlib.sha256(first_zip).hexdigest() == first.webui_archive_sha256
    assert first.webui_files == second.webui_files
    assert [item.path for item in first.webui_files] == [
        "assets/app.js",
        "index.html",
    ]
    assert [item.path for item in first.webui_files] == sorted(
        item.path for item in first.webui_files
    )

    payload = json.loads(first.channel_manifest.read_text(encoding="utf-8"))
    channel = parse_signed_channel(
        payload,
        {"release-test": public_key},
        current_version="1.9.02",
        backend_contract=1,
    )
    assert channel.sequence == 3
    assert channel.patches[0].patch_id == "webui-20261127-03"
    assert channel.patches[0].rollout == 25
    assert channel.patches[0].archive_sha256 == first.webui_archive_sha256
    assert {
        item.path: item.sha256 for item in channel.patches[0].files
    } == {
        item.path: item.sha256 for item in first.webui_files
    }

    with zipfile.ZipFile(first.webui_archive) as archive:
        infos = archive.infolist()
    assert [info.filename for info in infos] == [
        "assets/app.js",
        "index.html",
    ]
    assert all(info.date_time == (1980, 1, 1, 0, 0, 0) for info in infos)


def test_packager_builds_validated_controlled_rules_archive(
    tmp_path: Path,
) -> None:
    rules_path = tmp_path / "rules" / "display_thresholds.json"
    rules_path.parent.mkdir()
    rules_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "thresholds": {"low_stock": 10},
            }
        ),
        encoding="utf-8",
    )

    _, public_key, result = _build(
        tmp_path,
        rules=(
            RuleSource(
                name="display_thresholds",
                path=rules_path,
            ),
        ),
    )

    assert result.rules_archive is not None
    payload = json.loads(result.channel_manifest.read_text(encoding="utf-8"))
    channel = parse_signed_channel(
        payload,
        {"release-test": public_key},
        current_version="1.9.02",
        backend_contract=1,
    )
    rules_patch = next(item for item in channel.patches if item.kind == "rules")
    assert rules_patch.rules[0].name == "display_thresholds"
    assert rules_patch.rules[0].path == "display_thresholds.json"


def test_packager_rejects_invalid_rules_before_writing_channel(
    tmp_path: Path,
) -> None:
    rules_path = tmp_path / "rules" / "display_thresholds.json"
    rules_path.parent.mkdir()
    rules_path.write_text(
        '{"schema_version":1,"thresholds":{"low_stock":-1}}',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="非负"):
        _build(
            tmp_path,
            rules=(
                RuleSource(
                    name="display_thresholds",
                    path=rules_path,
                ),
            ),
        )

    assert not (tmp_path / "first" / "hot_update_channel.json").exists()


def test_packager_rejects_disallowed_webui_file(
    tmp_path: Path,
) -> None:
    (tmp_path / "webui").mkdir()
    (tmp_path / "webui" / "index.html").write_text("ok", encoding="utf-8")
    (tmp_path / "webui" / "backend.py").write_text("print('no')", encoding="utf-8")

    with pytest.raises(ValueError, match="文件类型"):
        _build(tmp_path)


def test_packager_rejects_private_key_inside_repository(
    tmp_path: Path,
) -> None:
    private_path = tmp_path / "repository" / "release-private.pem"
    private_path.parent.mkdir()
    _private_key(private_path)
    webui = _webui(tmp_path / "webui")

    with pytest.raises(ValueError, match="仓库外"):
        build_update_package(
            webui,
            tmp_path / "output",
            private_key_path=private_path,
            key_id="release-test",
            product_version="1.9.02",
            channel_sequence=1,
            webui_patch_id="webui-20261127-01",
            webui_sequence=1,
            webui_url="https://example.invalid/webui.zip",
            app_min="1.9.02",
            app_max="1.9.99",
            backend_contract=1,
            repo_root=tmp_path / "repository",
        )


def test_packager_rejects_symlinked_webui_file(
    tmp_path: Path,
) -> None:
    webui = _webui(tmp_path / "webui")
    try:
        os.symlink(
            webui / "index.html",
            webui / "linked.html",
        )
    except OSError as exc:
        pytest.skip(f"当前环境不能创建文件符号链接：{exc}")

    with pytest.raises(ValueError, match="符号链接"):
        _build(tmp_path)


@pytest.mark.parametrize(
    "script",
    [
        "tools/build_hot_update_package.py",
        "tools/verify_hot_update_package.py",
    ],
)
def test_release_tools_run_directly_from_repository_root(
    script: str,
) -> None:
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    result = subprocess.run(
        [sys.executable, script, "--help"],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "usage:" in result.stdout
