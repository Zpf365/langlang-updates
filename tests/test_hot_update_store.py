from __future__ import annotations

import base64
import hashlib
import json
import stat
import zipfile
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from unittest import mock

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from weimi_manager.hot_update_store import (
    HotUpdateStore,
    InstallLockBusy,
    UpdateInstallLock,
)
from weimi_manager.update_protocol import (
    UpdateProtocolError,
    parse_signed_channel,
    sign_manifest,
)


def _keypair() -> tuple[Ed25519PrivateKey, dict[str, str]]:
    private_key = Ed25519PrivateKey.generate()
    public_bytes = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return private_key, {
        "release-test": base64.b64encode(public_bytes).decode("ascii"),
    }


def _file_hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _archive(
    path: Path,
    files: dict[str, bytes],
    *,
    symlink_path: str = "",
) -> str:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            info = zipfile.ZipInfo(name)
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            if name == symlink_path:
                info.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(info, content)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return digest


def _signed_channel(
    private_key: Ed25519PrivateKey,
    *,
    sequence: int,
    files: dict[str, bytes],
    archive_sha256: str,
    patch_id: str = "webui-20261127-01",
    kind: str = "webui",
    rollout: int = 100,
    paused: bool = False,
    revoked_patch_ids: list[str] | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": 1,
        "kind": "channel",
        "channel": "stable",
        "sequence": sequence,
        "product_version": "1.9.02",
        "paused": paused,
        "revoked_patch_ids": revoked_patch_ids or [],
        "patches": [
            {
                "kind": kind,
                "patch_id": patch_id,
                "sequence": sequence,
                "url": "https://example.invalid/patch.zip",
                "archive_sha256": archive_sha256,
                "app_min": "1.9.01.1",
                "app_max": "1.9.99",
                "backend_contract": 1,
                "rollout": rollout,
                "files": [
                    {
                        "path": name,
                        "sha256": _file_hash(content),
                    }
                    for name, content in files.items()
                ],
            }
        ],
    }
    payload["signature"] = sign_manifest(payload, private_key, "release-test")
    return payload


def _store(
    tmp_path: Path,
    public_keys: dict[str, str],
    validators: dict[str, object] | None = None,
) -> HotUpdateStore:
    return HotUpdateStore(
        tmp_path / "updates",
        public_keys=public_keys,
        current_version="1.9.01.1",
        backend_contract=1,
        install_id="install-test-01",
        rule_validators=validators or {},
    )


def test_webui_patch_activates_atomically_and_keeps_previous(
    tmp_path: Path,
) -> None:
    private_key, public_keys = _keypair()
    store = _store(tmp_path, public_keys)
    fallback = tmp_path / "embedded"
    fallback.mkdir()
    (fallback / "index.html").write_text("embedded", encoding="utf-8")

    first_files = {
        "index.html": b"<html>first</html>",
        "assets/app.js": b"console.log('first')",
    }
    first_zip = tmp_path / "first.zip"
    first_sha = _archive(first_zip, first_files)
    first_channel = parse_signed_channel(
        _signed_channel(
            private_key,
            sequence=1,
            files=first_files,
            archive_sha256=first_sha,
        ),
        public_keys,
        current_version="1.9.01.1",
        backend_contract=1,
    )
    store.install_patch(first_channel.patches[0], first_zip)
    first_root = store.active_root("webui", fallback)
    assert (first_root / "index.html").read_text(encoding="utf-8") == ("<html>first</html>")

    second_files = {
        "index.html": b"<html>second</html>",
        "assets/app.js": b"console.log('second')",
    }
    second_zip = tmp_path / "second.zip"
    second_sha = _archive(second_zip, second_files)
    second_channel = parse_signed_channel(
        _signed_channel(
            private_key,
            sequence=2,
            files=second_files,
            archive_sha256=second_sha,
            patch_id="webui-20261127-02",
        ),
        public_keys,
        current_version="1.9.01.1",
        backend_contract=1,
    )
    store.install_patch(second_channel.patches[0], second_zip)

    assert (store.active_root("webui", fallback) / "index.html").read_text(
        encoding="utf-8"
    ) == "<html>second</html>"
    assert store.active_patch_id("webui") == "webui-20261127-02"
    assert store.rollback("webui") is True
    assert store.active_patch_id("webui") == "webui-20261127-01"
    assert (store.active_root("webui", fallback) / "index.html").read_text(
        encoding="utf-8"
    ) == "<html>first</html>"


def test_webui_patch_rejects_symlink_without_replacing_active(
    tmp_path: Path,
) -> None:
    private_key, public_keys = _keypair()
    store = _store(tmp_path, public_keys)
    fallback = tmp_path / "embedded"
    fallback.mkdir()
    (fallback / "index.html").write_text("embedded", encoding="utf-8")

    files = {"index.html": b"<html>unsafe</html>"}
    archive_path = tmp_path / "unsafe.zip"
    digest = _archive(
        archive_path,
        files,
        symlink_path="index.html",
    )
    channel = parse_signed_channel(
        _signed_channel(
            private_key,
            sequence=1,
            files=files,
            archive_sha256=digest,
        ),
        public_keys,
        current_version="1.9.01.1",
        backend_contract=1,
    )

    with pytest.raises(UpdateProtocolError, match="符号链接"):
        store.install_patch(channel.patches[0], archive_path)
    assert store.active_root("webui", fallback) == fallback


def test_install_recovers_orphan_patch_directory_without_state(
    tmp_path: Path,
) -> None:
    private_key, public_keys = _keypair()
    store = _store(tmp_path, public_keys)
    files = {"index.html": b"<html>recovered</html>"}
    archive_path = tmp_path / "recovered.zip"
    digest = _archive(archive_path, files)
    channel = parse_signed_channel(
        _signed_channel(
            private_key,
            sequence=1,
            files=files,
            archive_sha256=digest,
        ),
        public_keys,
        current_version="1.9.01.1",
        backend_contract=1,
    )
    orphan = store.root / "webui" / "webui-20261127-01"
    orphan.mkdir(parents=True)
    (orphan / "stale.txt").write_text("stale", encoding="utf-8")

    installed = store.install_patch(channel.patches[0], archive_path)

    assert installed == orphan
    assert (installed / "index.html").read_text(encoding="utf-8") == ("<html>recovered</html>")
    assert not (installed / "stale.txt").exists()


def test_failed_state_write_never_deletes_sentinel_or_cwd(
    tmp_path: Path,
) -> None:
    private_key, public_keys = _keypair()
    store = _store(tmp_path, public_keys)
    files = {"index.html": b"<html>state failure</html>"}
    archive_path = tmp_path / "state-failure.zip"
    digest = _archive(archive_path, files)
    channel = parse_signed_channel(
        _signed_channel(
            private_key,
            sequence=1,
            files=files,
            archive_sha256=digest,
        ),
        public_keys,
        current_version="1.9.01.1",
        backend_contract=1,
    )

    with (
        mock.patch.object(
            store,
            "_write_state",
            side_effect=OSError("disk full"),
        ),
        mock.patch("weimi_manager.hot_update_store.shutil.rmtree") as remove_tree,
    ):
        with pytest.raises(OSError, match="disk full"):
            store.install_patch(channel.patches[0], archive_path)

    for call in remove_tree.call_args_list:
        target = Path(call.args[0]).resolve()
        assert target != Path.cwd().resolve()
        assert target != store.root


def test_rules_patch_uses_business_validator_and_keeps_previous_on_failure(
    tmp_path: Path,
) -> None:
    private_key, public_keys = _keypair()

    def validate_thresholds(payload: object) -> None:
        if not isinstance(payload, dict):
            raise ValueError("rules must be an object")
        if not isinstance(payload.get("warning"), (int, float)):
            raise ValueError("warning threshold is required")

    store = _store(
        tmp_path,
        public_keys,
        validators={"display_thresholds": validate_thresholds},
    )
    valid_payload = {"schema_version": 1, "warning": 100}
    files = {
        "display_thresholds.json": json.dumps(valid_payload).encode(),
    }
    archive_path = tmp_path / "rules-ok.zip"
    digest = _archive(archive_path, files)
    payload = _signed_channel(
        private_key,
        sequence=1,
        files=files,
        archive_sha256=digest,
        patch_id="rules-20261127-01",
        kind="rules",
    )
    payload["patches"][0]["rules"] = [  # type: ignore[index]
        {
            "name": "display_thresholds",
            "path": "display_thresholds.json",
        }
    ]
    payload["signature"] = sign_manifest(payload, private_key, "release-test")
    channel = parse_signed_channel(
        payload,
        public_keys,
        current_version="1.9.01.1",
        backend_contract=1,
    )
    store.install_patch(channel.patches[0], archive_path)
    assert store.rule_payload("display_thresholds") == valid_payload

    invalid_files = {
        "display_thresholds.json": b'{"schema_version": 1}',
    }
    invalid_archive = tmp_path / "rules-bad.zip"
    invalid_digest = _archive(invalid_archive, invalid_files)
    invalid_payload = _signed_channel(
        private_key,
        sequence=2,
        files=invalid_files,
        archive_sha256=invalid_digest,
        patch_id="rules-20261127-02",
        kind="rules",
    )
    invalid_payload["patches"][0]["rules"] = [  # type: ignore[index]
        {
            "name": "display_thresholds",
            "path": "display_thresholds.json",
        }
    ]
    invalid_payload["signature"] = sign_manifest(
        invalid_payload,
        private_key,
        "release-test",
    )
    invalid_channel = parse_signed_channel(
        invalid_payload,
        public_keys,
        current_version="1.9.01.1",
        backend_contract=1,
    )

    with pytest.raises(ValueError, match="warning"):
        store.install_patch(
            invalid_channel.patches[0],
            invalid_archive,
        )
    assert store.rule_payload("display_thresholds") == valid_payload


def test_revoked_active_patch_rolls_back_to_previous(tmp_path: Path) -> None:
    private_key, public_keys = _keypair()
    store = _store(tmp_path, public_keys)
    fallback = tmp_path / "embedded"
    fallback.mkdir()
    (fallback / "index.html").write_text("embedded", encoding="utf-8")

    for sequence, patch_id, content in (
        (1, "webui-01", b"first"),
        (2, "webui-02", b"second"),
    ):
        files = {"index.html": content}
        archive_path = tmp_path / f"{patch_id}.zip"
        digest = _archive(archive_path, files)
        channel = parse_signed_channel(
            _signed_channel(
                private_key,
                sequence=sequence,
                files=files,
                archive_sha256=digest,
                patch_id=patch_id,
            ),
            public_keys,
            current_version="1.9.01.1",
            backend_contract=1,
        )
        store.install_patch(channel.patches[0], archive_path)

    control = parse_signed_channel(
        _signed_channel(
            private_key,
            sequence=3,
            files={"index.html": b"unused"},
            archive_sha256="c" * 64,
            patch_id="webui-03",
            revoked_patch_ids=["webui-02"],
        ),
        public_keys,
        current_version="1.9.01.1",
        backend_contract=1,
    )
    store.apply_channel_control(control)

    assert store.active_patch_id("webui") == "webui-01"


def test_revoked_older_patch_is_removed_from_rollback_chain(
    tmp_path: Path,
) -> None:
    private_key, public_keys = _keypair()
    store = _store(tmp_path, public_keys)
    fallback = tmp_path / "embedded"
    fallback.mkdir()
    (fallback / "index.html").write_text("embedded", encoding="utf-8")

    for sequence, patch_id, content in (
        (1, "webui-01", b"first"),
        (2, "webui-02", b"second"),
    ):
        files = {"index.html": content}
        archive_path = tmp_path / f"{patch_id}.zip"
        digest = _archive(archive_path, files)
        channel = parse_signed_channel(
            _signed_channel(
                private_key,
                sequence=sequence,
                files=files,
                archive_sha256=digest,
                patch_id=patch_id,
            ),
            public_keys,
            current_version="1.9.01.1",
            backend_contract=1,
        )
        store.install_patch(channel.patches[0], archive_path)

    control = parse_signed_channel(
        _signed_channel(
            private_key,
            sequence=3,
            files={"index.html": b"unused"},
            archive_sha256="c" * 64,
            patch_id="webui-03",
            revoked_patch_ids=["webui-01"],
        ),
        public_keys,
        current_version="1.9.01.1",
        backend_contract=1,
    )
    store.apply_channel_control(control)

    assert store.rollback("webui") is True
    assert store.active_patch_id("webui") == ""
    assert store.active_root("webui", fallback) == fallback


def test_update_install_lock_rejects_concurrent_installer(
    tmp_path: Path,
) -> None:
    first = UpdateInstallLock(tmp_path / "updates")
    second = UpdateInstallLock(tmp_path / "updates")
    with first:
        with pytest.raises(InstallLockBusy):
            with second:
                pass


def test_channel_replay_does_not_reinstall_old_patch(tmp_path: Path) -> None:
    private_key, public_keys = _keypair()
    store = _store(tmp_path, public_keys)
    control = parse_signed_channel(
        _signed_channel(
            private_key,
            sequence=5,
            files={"index.html": b"five"},
            archive_sha256="d" * 64,
            patch_id="webui-05",
        ),
        public_keys,
        current_version="1.9.01.1",
        backend_contract=1,
    )
    assert store.apply_channel_control(control) is True

    replay = deepcopy(control)
    assert store.apply_channel_control(replay) is False

    older = replace(control, sequence=4)
    with pytest.raises(UpdateProtocolError, match="sequence"):
        store.apply_channel_control(older)
