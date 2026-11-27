from __future__ import annotations

import base64
import hashlib
import zipfile
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from weimi_manager.hot_update_store import HotUpdateStore
from weimi_manager.update_protocol import sign_manifest
from weimi_manager.update_runtime import HotUpdateRuntime


def _keypair() -> tuple[Ed25519PrivateKey, dict[str, str]]:
    private_key = Ed25519PrivateKey.generate()
    public_bytes = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return private_key, {
        "release-test": base64.b64encode(public_bytes).decode("ascii"),
    }


def _archive(path: Path, *, content: bytes) -> str:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("index.html", content)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _channel(
    private_key: Ed25519PrivateKey,
    *,
    archive_sha256: str,
    paused: bool = False,
    body_sha256: str,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": 1,
        "kind": "channel",
        "channel": "stable",
        "sequence": 1,
        "product_version": "1.9.02",
        "paused": paused,
        "revoked_patch_ids": [],
        "patches": [
            {
                "kind": "webui",
                "patch_id": "webui-20261127-01",
                "sequence": 1,
                "url": "https://example.invalid/webui.zip",
                "archive_sha256": archive_sha256,
                "app_min": "1.9.01.1",
                "app_max": "1.9.99",
                "backend_contract": 1,
                "rollout": 100,
                "files": [
                    {
                        "path": "index.html",
                        "sha256": body_sha256,
                    }
                ],
            }
        ],
    }
    payload["signature"] = sign_manifest(payload, private_key, "release-test")
    return payload


def _runtime(
    tmp_path: Path,
    public_keys: dict[str, str],
    channel: dict[str, object],
    archive_path: Path,
    busy: bool,
) -> tuple[HotUpdateRuntime, HotUpdateStore, list[str]]:
    store = HotUpdateStore(
        tmp_path / "updates",
        public_keys=public_keys,
        current_version="1.9.01.1",
        backend_contract=1,
        install_id="install-runtime-01",
        rule_validators={},
    )
    reloads: list[str] = []
    runtime = HotUpdateRuntime(
        store,
        channel_loader=lambda: channel,
        archive_loader=lambda _patch: archive_path,
        busy_provider=lambda: busy,
        reload_handler=lambda: reloads.append("reload"),
    )
    return runtime, store, reloads


def test_runtime_installs_webui_and_reloads_when_idle(
    tmp_path: Path,
) -> None:
    private_key, public_keys = _keypair()
    content = b"<html>patch</html>"
    archive_path = tmp_path / "patch.zip"
    archive_sha = _archive(archive_path, content=content)
    channel = _channel(
        private_key,
        archive_sha256=archive_sha,
        body_sha256=hashlib.sha256(content).hexdigest(),
    )
    runtime, store, reloads = _runtime(
        tmp_path,
        public_keys,
        channel,
        archive_path,
        False,
    )

    result = runtime.poll_once()

    assert result.installed_patch_ids == ("webui-20261127-01",)
    assert result.reload_requested is True
    assert store.active_patch_id("webui") == "webui-20261127-01"
    assert reloads == ["reload"]


def test_runtime_defers_reload_while_busy_but_keeps_patch_active(
    tmp_path: Path,
) -> None:
    private_key, public_keys = _keypair()
    content = b"<html>busy</html>"
    archive_path = tmp_path / "busy.zip"
    archive_sha = _archive(archive_path, content=content)
    channel = _channel(
        private_key,
        archive_sha256=archive_sha,
        body_sha256=hashlib.sha256(content).hexdigest(),
    )
    runtime, store, reloads = _runtime(
        tmp_path,
        public_keys,
        channel,
        archive_path,
        True,
    )

    result = runtime.poll_once()

    assert result.installed_patch_ids == ("webui-20261127-01",)
    assert result.reload_requested is False
    assert store.active_patch_id("webui") == "webui-20261127-01"
    assert reloads == []


def test_runtime_does_not_install_when_channel_is_paused(
    tmp_path: Path,
) -> None:
    private_key, public_keys = _keypair()
    content = b"<html>paused</html>"
    archive_path = tmp_path / "paused.zip"
    archive_sha = _archive(archive_path, content=content)
    channel = _channel(
        private_key,
        archive_sha256=archive_sha,
        body_sha256=hashlib.sha256(content).hexdigest(),
        paused=True,
    )
    runtime, store, reloads = _runtime(
        tmp_path,
        public_keys,
        channel,
        archive_path,
        False,
    )

    result = runtime.poll_once()

    assert result.installed_patch_ids == ()
    assert result.reload_requested is False
    assert store.active_patch_id("webui") == ""
    assert reloads == []


def test_runtime_rolls_back_webui_when_ready_probe_fails(
    tmp_path: Path,
) -> None:
    private_key, public_keys = _keypair()
    fallback = tmp_path / "embedded"
    fallback.mkdir()
    (fallback / "index.html").write_text("embedded", encoding="utf-8")
    content = b"<html>broken</html>"
    archive_path = tmp_path / "broken.zip"
    archive_sha = _archive(archive_path, content=content)
    channel = _channel(
        private_key,
        archive_sha256=archive_sha,
        body_sha256=hashlib.sha256(content).hexdigest(),
    )
    runtime, store, _reloads = _runtime(
        tmp_path,
        public_keys,
        channel,
        archive_path,
        False,
    )
    runtime.poll_once()

    runtime.mark_webui_ready(False)

    assert store.active_patch_id("webui") == ""
    assert store.active_root("webui", fallback) == fallback


def test_runtime_retries_same_channel_after_transient_archive_failure(
    tmp_path: Path,
) -> None:
    private_key, public_keys = _keypair()
    content = b"<html>retry</html>"
    archive_path = tmp_path / "retry.zip"
    archive_sha = _archive(archive_path, content=content)
    channel = _channel(
        private_key,
        archive_sha256=archive_sha,
        body_sha256=hashlib.sha256(content).hexdigest(),
    )
    store = HotUpdateStore(
        tmp_path / "updates",
        public_keys=public_keys,
        current_version="1.9.01.1",
        backend_contract=1,
        install_id="install-runtime-retry",
        rule_validators={},
    )
    attempts = 0

    def archive_loader(_patch):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("temporary network failure")
        return archive_path

    runtime = HotUpdateRuntime(
        store,
        channel_loader=lambda: channel,
        archive_loader=archive_loader,
        busy_provider=lambda: False,
        reload_handler=lambda: None,
    )
    with pytest.raises(OSError, match="temporary"):
        runtime.poll_once()

    result = runtime.poll_once()

    assert result.installed_patch_ids == ("webui-20261127-01",)
    assert store.active_patch_id("webui") == "webui-20261127-01"
