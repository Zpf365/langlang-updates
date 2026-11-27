from __future__ import annotations

import base64
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from tools.init_update_signing_key import create_signing_key


def test_creates_ed25519_key_outside_repository_without_printing_private_material(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    private_path = tmp_path / "outside" / "release.pem"

    info = create_signing_key(
        private_path,
        key_id="langlang-release-test",
        repo_root=repository,
    )

    key = serialization.load_pem_private_key(
        private_path.read_bytes(),
        password=None,
    )
    assert isinstance(key, Ed25519PrivateKey)
    expected_public = base64.b64encode(
        key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
    ).decode("ascii")
    assert info.public_key == expected_public
    assert info.key_id == "langlang-release-test"
    assert base64.b64decode(info.fingerprint)
    assert str(private_path.resolve()) not in repr(info)
    assert "PRIVATE KEY" not in repr(info)


def test_refuses_to_overwrite_existing_signing_key(tmp_path: Path) -> None:
    private_path = tmp_path / "outside" / "release.pem"
    info = create_signing_key(
        private_path,
        key_id="langlang-release-test",
        repo_root=tmp_path / "repository",
    )
    original = private_path.read_bytes()

    with pytest.raises(FileExistsError):
        create_signing_key(
            private_path,
            key_id="langlang-release-test",
            repo_root=tmp_path / "repository",
        )

    assert private_path.read_bytes() == original
    assert info.key_id == "langlang-release-test"


def test_rejects_signing_key_path_inside_repository(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()

    with pytest.raises(ValueError, match="仓库外"):
        create_signing_key(
            repository / "release.pem",
            key_id="langlang-release-test",
            repo_root=repository,
        )
