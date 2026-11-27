from __future__ import annotations

import base64
import json
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from tools.sign_update_manifest import sign_manifest_file
from weimi_manager.update_protocol import verify_manifest_signature


def test_signing_tool_signs_manifest_without_embedding_private_key(
    tmp_path: Path,
) -> None:
    private_key = Ed25519PrivateKey.generate()
    private_path = tmp_path / "release-private.pem"
    private_path.write_bytes(
        private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    manifest_path = tmp_path / "update_manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "version": "1.9.02",
                "download_url": "https://example.invalid/LangLang.zip",
                "sha256": "a" * 64,
            }
        ),
        encoding="utf-8",
    )
    public_key = base64.b64encode(
        private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
    ).decode("ascii")

    output = sign_manifest_file(
        manifest_path,
        private_path,
        "release-test",
    )
    payload = json.loads(output.read_text(encoding="utf-8"))

    assert (
        verify_manifest_signature(
            payload,
            {"release-test": public_key},
        )
        == "release-test"
    )
    serialized = output.read_text(encoding="utf-8")
    assert "PRIVATE KEY" not in serialized
