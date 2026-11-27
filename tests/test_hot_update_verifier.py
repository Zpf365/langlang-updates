from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_hot_update_packager import _build
from tools.verify_hot_update_package import verify_update_assets
from weimi_manager.update_protocol import UpdateProtocolError


def test_verifier_checks_signature_compatibility_and_archive_hashes(
    tmp_path: Path,
) -> None:
    _, _public_key, package = _build(tmp_path)
    trust_root = tmp_path / "trust"
    (trust_root / "assets").mkdir(parents=True)
    (trust_root / "assets" / "update_public_keys.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "enabled": True,
                "blocker": "",
                "channel_url": (
                    "https://api.github.com/repos/Zpf365/"
                    "langlang-updates/releases/latest"
                ),
                "keys": [
                    {
                        "key_id": "release-test",
                        "public_key": package.public_key,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    verified = verify_update_assets(
        package.channel_manifest,
        trust_root=trust_root,
        current_version="1.9.02",
        backend_contract=1,
        webui_archive=package.webui_archive,
    )

    assert verified == ("webui-20261127-03",)


def test_verifier_rejects_tampered_archive(
    tmp_path: Path,
) -> None:
    _, _public_key, package = _build(tmp_path)
    trust_root = tmp_path / "trust"
    (trust_root / "assets").mkdir(parents=True)
    (trust_root / "assets" / "update_public_keys.json").write_text(
        json.dumps(
            {
                "enabled": True,
                "keys": [
                    {
                        "key_id": "release-test",
                        "public_key": package.public_key,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    package.webui_archive.write_bytes(
        package.webui_archive.read_bytes() + b"tampered"
    )

    with pytest.raises(UpdateProtocolError, match="ZIP SHA256"):
        verify_update_assets(
            package.channel_manifest,
            trust_root=trust_root,
            current_version="1.9.02",
            backend_contract=1,
            webui_archive=package.webui_archive,
        )
