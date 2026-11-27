from __future__ import annotations

import base64
from copy import deepcopy
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from weimi_manager.update_protocol import (
    UpdateProtocolError,
    load_trust_config,
    parse_signed_channel,
    sign_manifest,
)

ROOT = Path(__file__).resolve().parent.parent


def _keypair() -> tuple[Ed25519PrivateKey, dict[str, str]]:
    private_key = Ed25519PrivateKey.generate()
    public_bytes = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return private_key, {
        "release-test": base64.b64encode(public_bytes).decode("ascii"),
    }


def _channel_payload(
    private_key: Ed25519PrivateKey,
    *,
    app_min: str = "1.9.01.1",
    app_max: str = "1.9.99",
    rollout: int = 100,
    paused: bool = False,
    revoked_patch_ids: list[str] | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": 1,
        "kind": "channel",
        "channel": "stable",
        "sequence": 7,
        "product_version": "1.9.02",
        "paused": paused,
        "revoked_patch_ids": revoked_patch_ids or [],
        "patches": [
            {
                "kind": "webui",
                "patch_id": "webui-20261127-01",
                "sequence": 3,
                "url": "https://example.invalid/webui.zip",
                "archive_sha256": "a" * 64,
                "app_min": app_min,
                "app_max": app_max,
                "backend_contract": 1,
                "rollout": rollout,
                "files": [
                    {
                        "path": "index.html",
                        "sha256": "b" * 64,
                    }
                ],
            }
        ],
    }
    payload["signature"] = sign_manifest(payload, private_key, "release-test")
    return payload


def test_signed_channel_accepts_compatible_rollout_patch() -> None:
    private_key, public_keys = _keypair()

    channel = parse_signed_channel(
        _channel_payload(private_key, rollout=25),
        public_keys,
        current_version="1.9.01.1",
        backend_contract=1,
    )

    assert channel.channel == "stable"
    assert channel.sequence == 7
    assert len(channel.patches) == 1
    assert channel.patches[0].patch_id == "webui-20261127-01"
    assert channel.patches[0].files[0].path == "index.html"


def test_checked_in_trust_config_is_closed_by_default_with_signed_channel_ready() -> None:
    trust = load_trust_config(ROOT)

    assert trust.enabled is False
    assert set(trust.public_keys) == {"langlang-release-2026-11"}
    assert len(base64.b64decode(trust.public_keys["langlang-release-2026-11"])) == 32
    assert trust.channel_url == (
        "https://api.github.com/repos/Zpf365/langlang-updates/"
        "releases/latest"
    )
    assert trust.blocker


def test_signed_channel_rejects_modified_descriptor() -> None:
    private_key, public_keys = _keypair()
    payload = _channel_payload(private_key)
    payload["patches"][0]["rollout"] = 0  # type: ignore[index]

    with pytest.raises(UpdateProtocolError, match="签名"):
        parse_signed_channel(
            payload,
            public_keys,
            current_version="1.9.01.1",
            backend_contract=1,
        )


def test_signed_channel_rejects_incompatible_app_range() -> None:
    private_key, public_keys = _keypair()

    with pytest.raises(UpdateProtocolError, match="兼容"):
        parse_signed_channel(
            _channel_payload(
                private_key,
                app_min="1.9.03",
                app_max="1.9.04",
            ),
            public_keys,
            current_version="1.9.01.1",
            backend_contract=1,
        )


@pytest.mark.parametrize(
    "path",
    [
        "../outside.html",
        "/absolute.html",
        "assets/app.py",
        "assets/runtime.pyd",
        "assets/update.exe",
        "assets/cache.db",
        "assets/name:stream.js",
    ],
)
def test_signed_channel_rejects_unsafe_patch_files(path: str) -> None:
    private_key, public_keys = _keypair()
    payload = deepcopy(_channel_payload(private_key))
    payload["patches"][0]["files"][0]["path"] = path  # type: ignore[index]
    payload["signature"] = sign_manifest(payload, private_key, "release-test")

    with pytest.raises(UpdateProtocolError, match="补丁文件"):
        parse_signed_channel(
            payload,
            public_keys,
            current_version="1.9.01.1",
            backend_contract=1,
        )
